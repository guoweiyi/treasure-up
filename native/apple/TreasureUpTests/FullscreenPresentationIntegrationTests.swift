import XCTest
import SwiftUI
import Observation
import AVFoundation
@testable import TreasureUp

@MainActor
final class FullscreenPresentationIntegrationTests: XCTestCase {
    func testFullscreenPresentsAcrossSplitViewAndReturnsSharedVideoLayerRepeatedly() async throws {
        guard let scene = UIApplication.shared.connectedScenes.compactMap({ $0 as? UIWindowScene })
            .first(where: { $0.activationState == .foregroundActive }) else {
            throw XCTSkip("A foreground simulator window scene is required")
        }
        let suite = "TreasureFullscreenIntegration.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [FullscreenFixtureURLProtocol.self]
        let api = APIClient(baseURL: URL(string: "https://fullscreen.example.test")!, defaults: defaults,
                            sessionConfiguration: configuration, persistSession: false)
        let playback = PlaybackCoordinator(api: api, defaults: defaults)
        let source = ArchiveVideo(id: "fullscreen-fixture", title: "Fullscreen fixture", playable: true,
                                  parts: [VideoPart(id: "part", position: 1, duration: 120,
                                                    variants: [MediaVariant(id: "source", width: 1920, height: 1080)])])
        // Seed the real coordinator using its public API. The stub deliberately
        // avoids media/network dependencies; this test exercises UIKit hosting.
        await playback.start(video: source)
        playback.errorMessage = nil
        playback.player.replaceCurrentItem(with: AVPlayerItem(asset: AVMutableComposition()))

        let state = FullscreenFixtureState()
        let lifetime = VideoPageLifetime()
        let host = UIHostingController(rootView: FullscreenFixtureView(playback: playback, state: state, lifetime: lifetime))
        let split = UISplitViewController(style: .doubleColumn)
        split.setViewController(UIViewController(), for: .primary)
        split.setViewController(UINavigationController(rootViewController: host), for: .secondary)
        split.preferredDisplayMode = .oneBesideSecondary
        let previousKeyWindow = scene.windows.first(where: \.isKeyWindow)
        let window = UIWindow(windowScene: scene)
        window.frame = scene.coordinateSpace.bounds
        window.rootViewController = split
        window.makeKeyAndVisible()
        defer {
            split.dismiss(animated: false)
            playback.stop(clearQueue: true, saveProgress: false)
            window.isHidden = true
            window.rootViewController = nil
            previousKeyWindow?.makeKey()
        }

        await waitUntil("Inline video should attach to the fixture window") {
            playback.presentation?.controller.view.window === window
        }
        let presentation = try XCTUnwrap(playback.presentation)
        let originalContainer = try XCTUnwrap(presentation.controller.parent as? NativePlayerContainerViewController)
        let originalWindowSize = window.bounds.size
        XCTAssertLessThanOrEqual(originalContainer.view.bounds.width, 360)

        state.isPresented = true
        await waitUntil("The anchor must actually present a full-screen controller") {
            self.presentedFullscreen(from: split)?.viewIfLoaded?.window === window
        }
        let fullscreen = try XCTUnwrap(presentedFullscreen(from: split))
        await waitUntil("The shared video layer must leave the inline container") {
            presentation.controller.parent !== originalContainer && !fullscreen.isBeingPresented
        }
        await waitUntil("Fullscreen geometry must fill the app window, including the sidebar region") {
            abs(fullscreen.view.bounds.width - window.bounds.width) < 2 &&
            abs(fullscreen.view.bounds.height - window.bounds.height) < 2
        }
        XCTAssertNotNil(playback.currentVideo)
        XCTAssertTrue(lifetime.isCoveredByPresentation)
        XCTAssertTrue(presentation.videoView.clipsToBounds)
        if UIDevice.current.userInterfaceIdiom == .phone {
            XCTAssertGreaterThan(fullscreen.view.bounds.width, fullscreen.view.bounds.height,
                                 "A landscape video must rotate the phone's fullscreen viewport")
        }
        // iPadOS 18 multitasking keeps the scene orientation under system
        // control. The iPad assertion above intentionally checks full WINDOW
        // coverage. iPadOS 26+ additionally supports the controller's public
        // interface-orientation lock when the scene occupies the whole screen.

        fullscreen.rootView.close()
        await waitUntil("Dismissal should return to the original player without stopping playback") {
            !state.isPresented && presentation.controller.parent === originalContainer &&
            fullscreen.presentingViewController == nil
        }
        XCTAssertEqual(playback.currentVideo?.id, source.id)
        XCTAssertFalse(lifetime.isCoveredByPresentation)
        XCTAssertEqual(presentation.videoView.bounds.size, originalContainer.view.bounds.size)
        await waitUntil("Dismissing fullscreen should restore the original window orientation") {
            abs(window.bounds.width - originalWindowSize.width) < 2 &&
            abs(window.bounds.height - originalWindowSize.height) < 2
        }

        // A successful first transition alone would miss a stale presenter or
        // borrowed-layer owner that makes later fullscreen presses do nothing.
        state.isPresented = true
        await waitUntil("A second fullscreen request must present a new controller") {
            guard let reopened = self.presentedFullscreen(from: split) else { return false }
            return reopened !== fullscreen && !reopened.isBeingPresented && reopened.view.window === window
        }
        let reopened = try XCTUnwrap(presentedFullscreen(from: split))
        XCTAssertTrue(presentation.controller.parent !== originalContainer)
        reopened.rootView.close()
        await waitUntil("The second dismissal must restore the same inline render container") {
            !state.isPresented && presentation.controller.parent === originalContainer &&
            reopened.presentingViewController == nil
        }
        XCTAssertEqual(playback.currentVideo?.id, source.id)
    }

    private func presentedFullscreen(from controller: UIViewController) -> FullscreenPlayerController? {
        if let fullscreen = controller.presentedViewController as? FullscreenPlayerController { return fullscreen }
        if let presented = controller.presentedViewController, let found = presentedFullscreen(from: presented) { return found }
        for child in controller.children {
            if let found = presentedFullscreen(from: child) { return found }
        }
        return nil
    }

    private func waitUntil(_ message: String, condition: @MainActor () -> Bool) async {
        for _ in 0..<250 {
            if condition() { return }
            try? await Task.sleep(for: .milliseconds(20))
        }
        XCTAssertTrue(condition(), message)
    }
}

@MainActor @Observable
private final class FullscreenFixtureState {
    var isPresented = false
}

private struct FullscreenFixtureView: View {
    let playback: PlaybackCoordinator
    @Bindable var state: FullscreenFixtureState
    let lifetime: VideoPageLifetime

    var body: some View {
        VStack {
            InlineNativePlayer(coordinator: playback, onToggleExpanded: { state.isPresented = true })
                .frame(width: 360, height: 203).clipped()
            Text("Inline details remain below the video")
            Spacer()
        }
        .background {
            FullscreenPlayerPresenter(isPresented: $state.isPresented, playback: playback, lifetime: lifetime)
                .frame(width: 0, height: 0)
        }
    }
}

private final class FullscreenFixtureURLProtocol: URLProtocol, @unchecked Sendable {
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let response = HTTPURLResponse(url: request.url!, statusCode: 503, httpVersion: "HTTP/1.1",
                                       headerFields: ["Content-Type": "application/json"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data("{}".utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() { }
}
