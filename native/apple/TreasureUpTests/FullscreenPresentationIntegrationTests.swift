import XCTest
import SwiftUI
import Observation
import AVFoundation
@testable import TreasureUp

@MainActor
final class FullscreenPresentationIntegrationTests: XCTestCase {
    func testTeardownBeforeQueuedPresentationStopsOnlyItsOwnInlinePlayback() async throws {
        let fixture = try await FullscreenTransitionFixture()
        defer { fixture.close() }
        await waitUntil("Inline fixture must attach") { fixture.playback.presentation?.controller.view.window === fixture.window }
        let anchor = try XCTUnwrap(findAnchor(in: fixture.split))
        // These calls deliberately share one main-actor turn: the queued
        // presentation has not created a fullscreen controller at teardown.
        anchor.update(isPresented: true, playback: fixture.playback) { fixture.state.isPresented = false }
        anchor.tearDown()
        XCTAssertNil(fixture.playback.currentVideo,
                     "The page's fullscreen flag otherwise makes its onDisappear skip this stop")
        XCTAssertNil(presentedFullscreen(from: fixture.split))
    }

    func testQueuedTeardownDoesNotStopSameVideoBorrowedByAnotherContainer() async throws {
        let fixture = try await FullscreenTransitionFixture()
        defer { fixture.close() }
        await waitUntil("Inline fixture must attach") { fixture.playback.presentation?.controller.view.window === fixture.window }
        let anchor = try XCTUnwrap(findAnchor(in: fixture.split))
        let presentation = try XCTUnwrap(fixture.playback.presentation)
        anchor.update(isPresented: true, playback: fixture.playback) { fixture.state.isPresented = false }
        let replacementOwner = NativePlayerContainerViewController(presentation: presentation)
        replacementOwner.loadViewIfNeeded()
        replacementOwner.view.frame = CGRect(x: 0, y: 0, width: 400, height: 225)
        replacementOwner.beginAppearanceTransition(true, animated: false)
        replacementOwner.endAppearanceTransition()
        defer { replacementOwner.detachPlayerIfOwned() }
        XCTAssertTrue(presentation.controller.parent === replacementOwner)
        anchor.tearDown()
        XCTAssertEqual(fixture.playback.currentVideo?.id, fixture.source.id,
                       "Even the same video is no longer this retired page's playback to stop")
        XCTAssertTrue(presentation.controller.parent === replacementOwner)
    }

    func testTeardownDuringExitStopsPlaybackAfterInlineHasReclaimedLayer() async throws {
        let fixture = try await FullscreenTransitionFixture()
        defer { fixture.close() }
        await waitUntil("Inline fixture must attach") { fixture.playback.presentation?.controller.view.window === fixture.window }
        let presentation = try XCTUnwrap(fixture.playback.presentation)
        let inline = try XCTUnwrap(presentation.controller.parent)
        let anchor = try XCTUnwrap(findAnchor(in: fixture.split))
        fixture.state.isPresented = true
        await waitUntil("Fullscreen entrance must finish") {
            guard let controller = self.presentedFullscreen(from: fixture.split) else { return false }
            return !controller.isBeingPresented && controller.view.window === fixture.window
        }
        let fullscreen = try XCTUnwrap(presentedFullscreen(from: fixture.split))
        fullscreen.rootView.close()
        await waitUntil("The inline layer must return while dismissal is still in flight") {
            fullscreen.isBeingDismissed && presentation.controller.parent === inline
        }
        XCTAssertTrue(fixture.state.isPresented, "The binding remains true until UIKit finishes dismissal")
        anchor.tearDown()
        XCTAssertNil(fixture.playback.currentVideo,
                     "Returning the layer early during dismissal must not bypass page-exit cleanup")
        await waitUntil("The in-flight dismissal must still complete after teardown") {
            fullscreen.presentingViewController == nil
        }
    }

    func testCancelDuringEntranceAndReopenDuringExitSerializeTheirTransitions() async throws {
        let fixture = try await FullscreenTransitionFixture()
        defer { fixture.close() }
        await waitUntil("Inline surface must attach before transition testing") {
            fixture.playback.presentation?.controller.view.window === fixture.window
        }
        let presentation = try XCTUnwrap(fixture.playback.presentation)
        let inline = try XCTUnwrap(presentation.controller.parent)
        fixture.state.isPresented = true
        await waitUntil("The entrance animation must actually be in flight") {
            self.presentedFullscreen(from: fixture.split)?.isBeingPresented == true
        }
        let entering = try XCTUnwrap(presentedFullscreen(from: fixture.split))
        entering.rootView.close()
        await waitUntil("Closing during entrance must finish both transitions and restore the inline surface") {
            !fixture.state.isPresented && entering.presentingViewController == nil &&
                presentation.controller.parent === inline
        }
        XCTAssertEqual(fixture.playback.currentVideo?.id, fixture.source.id)

        fixture.state.isPresented = true
        await waitUntil("A later request must still present after entrance cancellation") {
            guard let controller = self.presentedFullscreen(from: fixture.split) else { return false }
            return !controller.isBeingPresented && controller.view.window === fixture.window
        }
        let exiting = try XCTUnwrap(presentedFullscreen(from: fixture.split))
        fixture.state.isPresented = false
        await waitUntil("The exit animation must actually be in flight") { exiting.isBeingDismissed }
        fixture.state.isPresented = true
        await waitUntil("A new open request during dismissal must survive the old completion") {
            guard let controller = self.presentedFullscreen(from: fixture.split) else { return false }
            return controller !== exiting && !controller.isBeingPresented && controller.view.window === fixture.window
        }
        let reopened = try XCTUnwrap(presentedFullscreen(from: fixture.split))
        XCTAssertTrue(fixture.state.isPresented)
        exiting.rootView.close()
        XCTAssertTrue(fixture.state.isPresented, "A stale controller must not close the new presentation")
        XCTAssertTrue(presentedFullscreen(from: fixture.split) === reopened)
        reopened.rootView.close()
        await waitUntil("The final dismissal must return the same player controller") {
            !fixture.state.isPresented && presentation.controller.parent === inline
        }
        XCTAssertEqual(fixture.playback.currentVideo?.id, fixture.source.id)
    }

    func testUnrelatedModalRejectsFullscreenWithoutLeavingPhantomPresentation() async throws {
        let fixture = try await FullscreenTransitionFixture()
        defer { fixture.close() }
        await waitUntil("Fixture must be in its window") { fixture.playback.presentation?.controller.view.window === fixture.window }
        let alert = UIAlertController(title: "Offline modal", message: nil, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "Close", style: .cancel))
        fixture.split.present(alert, animated: false)
        await waitUntil("The unrelated modal must actually be presented") { alert.presentingViewController != nil }
        fixture.state.isPresented = true
        await waitUntil("Blocked fullscreen must clear its request instead of retaining a phantom controller") {
            !fixture.state.isPresented
        }
        XCTAssertNil(presentedFullscreen(from: fixture.split))
        XCTAssertNotNil(alert.presentingViewController, "Fullscreen must not replace the unrelated modal")
        XCTAssertEqual(fixture.playback.currentVideo?.id, fixture.source.id)
        alert.dismiss(animated: false)
        await waitUntil("The modal must dismiss before another user request") { alert.presentingViewController == nil }
        fixture.state.isPresented = true
        await waitUntil("A fresh request after the modal closes must succeed") {
            guard let controller = self.presentedFullscreen(from: fixture.split) else { return false }
            return !controller.isBeingPresented && controller.view.window === fixture.window
        }
        try XCTUnwrap(presentedFullscreen(from: fixture.split)).rootView.close()
        await waitUntil("Normal dismissal must reset the binding") { !fixture.state.isPresented }
    }

    func testChangedVideoUpdatesOrientationAndRetiredAnchorDoesNotStopReplacement() async throws {
        let fixture = try await FullscreenTransitionFixture()
        defer { fixture.close() }
        await waitUntil("Inline fixture must attach") { fixture.playback.presentation?.controller.view.window === fixture.window }
        fixture.state.isPresented = true
        await waitUntil("Fullscreen must finish presenting") {
            guard let controller = self.presentedFullscreen(from: fixture.split) else { return false }
            return !controller.isBeingPresented && controller.view.window === fixture.window
        }
        let fullscreen = try XCTUnwrap(presentedFullscreen(from: fixture.split))
        XCTAssertTrue(fullscreen.videoOrientation.isLandscape)
        let replacement = ArchiveVideo(id: "portrait-replacement", title: "Portrait replacement", playable: true,
            parts: [VideoPart(id: "portrait-part", position: 1, duration: 120,
                             variants: [MediaVariant(id: "portrait-source", width: 1080, height: 1920)])])
        await fixture.playback.start(video: replacement)
        fixture.playback.errorMessage = nil
        fixture.playback.player.replaceCurrentItem(with: AVPlayerItem(asset: AVMutableComposition()))
        await waitUntil("Changing the actual player item must refresh the fullscreen orientation") {
            fullscreen.videoOrientation == .portrait && fullscreen.supportedInterfaceOrientations == .portrait
        }
        let anchor = try XCTUnwrap(findAnchor(in: fixture.split))
        anchor.tearDown()
        await waitUntil("A retired anchor must dismiss its own fullscreen") { fullscreen.presentingViewController == nil }
        XCTAssertEqual(fixture.playback.currentVideo?.id, replacement.id,
                       "Dismantling the old video's presenter must not stop a replacement selected elsewhere")
    }

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

    private func findAnchor(in controller: UIViewController) -> PlayerPresentationAnchor? {
        if let anchor = controller as? PlayerPresentationAnchor { return anchor }
        for child in controller.children {
            if let anchor = findAnchor(in: child) { return anchor }
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

@MainActor
private final class FullscreenTransitionFixture {
    let source: ArchiveVideo
    let playback: PlaybackCoordinator
    let state = FullscreenFixtureState()
    let split = UISplitViewController(style: .doubleColumn)
    let window: UIWindow
    private let previousKeyWindow: UIWindow?
    private let defaults: UserDefaults
    private let suite: String

    init() async throws {
        guard let scene = UIApplication.shared.connectedScenes.compactMap({ $0 as? UIWindowScene })
            .first(where: { $0.activationState == .foregroundActive }) else {
            throw XCTSkip("A foreground simulator window scene is required")
        }
        suite = "TreasureFullscreenTransition.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suite)!
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [FullscreenFixtureURLProtocol.self]
        let api = APIClient(baseURL: URL(string: "https://fullscreen.example.test")!, defaults: defaults,
                            sessionConfiguration: configuration, persistSession: false)
        let coordinator = PlaybackCoordinator(api: api, defaults: defaults)
        playback = coordinator
        source = ArchiveVideo(id: "transition-fixture", title: "Transition fixture", playable: true,
            parts: [VideoPart(id: "transition-part", position: 1, duration: 120,
                             variants: [MediaVariant(id: "transition-source", width: 1920, height: 1080)])])
        previousKeyWindow = scene.windows.first(where: \.isKeyWindow)
        window = UIWindow(windowScene: scene)
        window.frame = scene.coordinateSpace.bounds
        let host = UIHostingController(rootView: FullscreenFixtureView(playback: coordinator, state: state,
                                                                       lifetime: VideoPageLifetime()))
        split.setViewController(UIViewController(), for: .primary)
        split.setViewController(UINavigationController(rootViewController: host), for: .secondary)
        split.preferredDisplayMode = .oneBesideSecondary
        window.rootViewController = split
        await coordinator.start(video: source)
        coordinator.errorMessage = nil
        coordinator.player.replaceCurrentItem(with: AVPlayerItem(asset: AVMutableComposition()))
        window.makeKeyAndVisible()
    }

    func close() {
        split.dismiss(animated: false)
        playback.stop(clearQueue: true, saveProgress: false)
        window.isHidden = true
        window.rootViewController = nil
        previousKeyWindow?.makeKey()
        defaults.removePersistentDomain(forName: suite)
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
