import XCTest
import SwiftUI
import UIKit
import AVFoundation
@testable import TreasureUp

@MainActor
final class VideoDetailViewportTests: XCTestCase {
    func testSidebarAndWindowResizingKeepHostedPlayerAndDetailsInsideTheirColumns() async throws {
        let suiteName = "VideoDetailViewportTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suiteName))
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let api = APIClient(baseURL: URL(string: "https://viewport.example.test")!,
                            defaults: defaults, persistSession: false)
        let playback = PlaybackCoordinator(api: api, defaults: defaults)
        defer { playback.stop(clearQueue: true, saveProgress: false) }
        let probes = ViewportProbes()
        let host = UIHostingController(rootView: VideoDetailViewport {
            NativePlayerView(coordinator: playback)
                .background { ViewportProbe(kind: .player, probes: probes) }
        } details: {
            ViewportProbe(kind: .details, probes: probes)
        }.ignoresSafeArea())
        let fixture = try ViewportWindowFixture(host: host, size: CGSize(width: 1180, height: 700))
        defer { fixture.close() }
        await resize(host, to: CGSize(width: 1180, height: 700), probes: probes, playback: playback)
        let presentation = try XCTUnwrap(playback.presentation)
        let sharedLayer = presentation.videoView.playerLayer
        let sharedContainer = try XCTUnwrap(presentation.controller.parent as? NativePlayerContainerViewController)
        var originalPlayer: UIView?
        var originalDetails: UIView?

        // One hosting tree crosses the sidebar breakpoint in both directions.
        // Rebuilding a new hosting controller for every size would miss stale
        // UIKit bounds and accidental remounts during an actual sidebar toggle.
        for size in [CGSize(width: 1180, height: 700), CGSize(width: 930, height: 700),
                     CGSize(width: 800, height: 700), CGSize(width: 620, height: 700),
                     CGSize(width: 834, height: 1100), CGSize(width: 1024, height: 1200),
                     CGSize(width: 744, height: 520), CGSize(width: 930, height: 420),
                     CGSize(width: 1180, height: 700)] {
            await resize(host, to: size, probes: probes, playback: playback)
            let player = try XCTUnwrap(probes.player)
            let details = try XCTUnwrap(probes.details)
            if let originalPlayer { XCTAssertTrue(player === originalPlayer, "Sidebar resizing must not replace the player") }
            else { originalPlayer = player }
            if let originalDetails { XCTAssertTrue(details === originalDetails, "Resizing must preserve section state") }
            else { originalDetails = details }

            let playerFrame = player.convert(player.bounds, to: host.view)
            let detailsFrame = details.convert(details.bounds, to: host.view)
            let expected = VideoViewportGeometry(size: size)
            assertRect(playerFrame, equals: expected.playerFrame)
            assertRect(detailsFrame, equals: expected.detailsFrame)
            XCTAssertTrue(presentation.controller.parent === sharedContainer)
            XCTAssertTrue(presentation.videoView.playerLayer === sharedLayer)
            assertRect(sharedContainer.view.convert(sharedContainer.view.bounds, to: host.view), equals: playerFrame)
            assertRect(presentation.videoView.convert(presentation.videoView.bounds, to: host.view), equals: playerFrame)
            assertRect(sharedLayer.bounds, equals: CGRect(origin: .zero, size: playerFrame.size))
            XCTAssertFalse(playerFrame.insetBy(dx: 0.5, dy: 0.5).intersects(detailsFrame))
            XCTAssertTrue(host.view.bounds.insetBy(dx: -0.5, dy: -0.5).contains(playerFrame))
            XCTAssertTrue(host.view.bounds.insetBy(dx: -0.5, dy: -0.5).contains(detailsFrame))
            XCTAssertEqual(playerFrame.width, expected.playerColumn.width, accuracy: 0.5)
            let imageFrame = AVMakeRect(aspectRatio: CGSize(width: 16, height: 9), insideRect: playerFrame)
            XCTAssertTrue(playerFrame.insetBy(dx: -0.5, dy: -0.5).contains(imageFrame))
            XCTAssertEqual(imageFrame.width / imageFrame.height, 16.0 / 9.0, accuracy: 0.005)
        }
        XCTAssertEqual(probes.playerCreations, 1)
        XCTAssertEqual(probes.detailsCreations, 1)
    }

    func testPortraitMediaAlsoKeepsItsActualViewportSeparateFromDetails() async throws {
        let probes = ViewportProbes()
        let host = UIHostingController(rootView: VideoDetailViewport(aspectRatio: 9.0 / 16.0) {
            ViewportProbe(kind: .player, probes: probes)
        } details: {
            ViewportProbe(kind: .details, probes: probes)
        }.ignoresSafeArea())
        let fixture = try ViewportWindowFixture(host: host, size: CGSize(width: 930, height: 700))
        defer { fixture.close() }
        for size in [CGSize(width: 930, height: 700), CGSize(width: 620, height: 700),
                     CGSize(width: 393, height: 760), CGSize(width: 393, height: 650), CGSize(width: 320, height: 460)] {
            await resize(host, to: size, probes: probes, aspectRatio: 9.0 / 16.0)
            let player = try XCTUnwrap(probes.player)
            let details = try XCTUnwrap(probes.details)
            let playerFrame = player.convert(player.bounds, to: host.view)
            let detailsFrame = details.convert(details.bounds, to: host.view)
            let expected = VideoViewportGeometry(size: size, aspectRatio: 9.0 / 16.0)
            XCTAssertEqual(playerFrame.width, expected.playerColumn.width, accuracy: 0.5,
                           "Portrait media must not squeeze or clip the player controls")
            XCTAssertGreaterThanOrEqual(playerFrame.width, 320)
            let imageFrame = AVMakeRect(aspectRatio: CGSize(width: 9, height: 16), insideRect: playerFrame)
            XCTAssertTrue(playerFrame.insetBy(dx: -0.5, dy: -0.5).contains(imageFrame))
            XCTAssertEqual(imageFrame.width / imageFrame.height, 9.0 / 16.0, accuracy: 0.005)
            XCTAssertFalse(playerFrame.insetBy(dx: 0.5, dy: 0.5).intersects(detailsFrame))
            XCTAssertTrue(host.view.bounds.insetBy(dx: -0.5, dy: -0.5).contains(playerFrame))
            XCTAssertTrue(host.view.bounds.insetBy(dx: -0.5, dy: -0.5).contains(detailsFrame))
        }
    }

    func testGeometryBoundsInformationWidthAndRejectsInvalidTransientMeasurements() {
        for width: CGFloat in [820, 930, 1180, 1800] {
            let layout = VideoViewportGeometry(size: CGSize(width: width, height: 700))
            XCTAssertTrue(layout.isWide)
            XCTAssertGreaterThanOrEqual(layout.playerColumn.width, 480)
            XCTAssertTrue((320...420).contains(layout.detailsFrame.width))
            XCTAssertEqual(layout.playerColumn.maxX, layout.detailsFrame.minX, accuracy: 0.001)
        }
        for size in [CGSize.zero, CGSize(width: -1, height: 700), CGSize(width: CGFloat.nan, height: CGFloat.infinity)] {
            let layout = VideoViewportGeometry(size: size, aspectRatio: .nan)
            for frame in [layout.playerColumn, layout.playerFrame, layout.detailsFrame] {
                XCTAssertTrue(frame.minX.isFinite && frame.minY.isFinite && frame.width.isFinite && frame.height.isFinite)
                XCTAssertGreaterThanOrEqual(frame.width, 0)
                XCTAssertGreaterThanOrEqual(frame.height, 0)
            }
        }
        let fallback = VideoViewportGeometry(size: CGSize(width: 620, height: 700), aspectRatio: 0)
        let imageFrame = AVMakeRect(aspectRatio: CGSize(width: 16, height: 9), insideRect: fallback.playerFrame)
        XCTAssertEqual(imageFrame.width / imageFrame.height, 16.0 / 9.0, accuracy: 0.001)
        XCTAssertEqual(fallback.playerFrame.width, fallback.playerColumn.width, accuracy: 0.001)
        XCTAssertGreaterThanOrEqual(fallback.detailsFrame.height, 700 * 0.52)
    }

    func testPortraitIPadUsesFullWidthWhileLandscapeKeepsReadableColumns() {
        for size in [CGSize(width: 834, height: 1100), CGSize(width: 1024, height: 1200)] {
            let layout = VideoViewportGeometry(size: size)
            XCTAssertFalse(layout.isWide)
            XCTAssertEqual(layout.playerFrame.width, size.width)
            XCTAssertEqual(layout.detailsFrame.width, size.width)
            XCTAssertEqual(layout.playerFrame.maxY, layout.detailsFrame.minY)
            XCTAssertGreaterThanOrEqual(layout.detailsFrame.height, size.height * 0.52)
        }
        let landscape = VideoViewportGeometry(size: CGSize(width: 1180, height: 700))
        XCTAssertTrue(landscape.isWide)
        XCTAssertGreaterThanOrEqual(landscape.playerFrame.width, 500)
        XCTAssertGreaterThanOrEqual(landscape.detailsFrame.width, 320)
    }

    func testAccessibilityTextDoesNotSqueezeInformationIntoNarrowSidebar() {
        let narrow = VideoViewportGeometry(size: CGSize(width: 900, height: 650), minimumInformationWidth: 420)
        XCTAssertFalse(narrow.isWide)
        XCTAssertEqual(narrow.detailsFrame.width, 900)
        let wide = VideoViewportGeometry(size: CGSize(width: 1180, height: 700), minimumInformationWidth: 420)
        XCTAssertTrue(wide.isWide)
        XCTAssertEqual(wide.detailsFrame.width, 420)
        XCTAssertGreaterThanOrEqual(wide.playerColumn.width, 500)
        let invalid = VideoViewportGeometry(size: CGSize(width: 1180, height: 700), minimumInformationWidth: .nan)
        XCTAssertEqual(invalid, VideoViewportGeometry(size: CGSize(width: 1180, height: 700)))
    }

    func testKeyboardAvoidanceKeepsPortraitLayoutButStillShrinksDetails() throws {
        let keyboard = try XCTUnwrap(VideoViewportKeyboardGeometry(viewportHeight: 1100, windowHeight: 1194))
        let visible = CGSize(width: 834, height: 620)
        let layout = VideoViewportGeometry(size: visible,
            unobscuredHeight: keyboard.unobscuredHeight(viewportHeight: visible.height, windowHeight: 1194))
        XCTAssertFalse(layout.isWide, "Opening comment search must not switch a portrait iPad to two columns")
        XCTAssertEqual(layout.detailsFrame.maxY, visible.height)
        XCTAssertFalse(layout.playerFrame.intersects(layout.detailsFrame))
        XCTAssertEqual(layout.playerFrame.width, visible.width)
    }

    func testKeyboardClassificationTracksRotationSidebarAndStageManagerWindowHeight() throws {
        let keyboard = try XCTUnwrap(VideoViewportKeyboardGeometry(viewportHeight: 1100, windowHeight: 1194))
        // Rotating the same window changes its unobscured height, even while
        // the keyboard remains presented and the SwiftUI proposal stays short.
        let landscapeHeight = keyboard.unobscuredHeight(viewportHeight: 420, windowHeight: 834)
        XCTAssertEqual(landscapeHeight, 740)
        let landscape = VideoViewportGeometry(size: CGSize(width: 1100, height: 420), unobscuredHeight: landscapeHeight)
        XCTAssertTrue(landscape.isWide)
        let sidebar = VideoViewportGeometry(size: CGSize(width: 760, height: 420), unobscuredHeight: landscapeHeight)
        XCTAssertFalse(sidebar.isWide)
        let tallStageWindow = VideoViewportGeometry(size: CGSize(width: 930, height: 600),
            unobscuredHeight: keyboard.unobscuredHeight(viewportHeight: 600, windowHeight: 1100))
        XCTAssertFalse(tallStageWindow.isWide)
        let shortStageWindow = VideoViewportGeometry(size: CGSize(width: 930, height: 400),
            unobscuredHeight: keyboard.unobscuredHeight(viewportHeight: 400, windowHeight: 700))
        XCTAssertTrue(shortStageWindow.isWide)
        XCTAssertEqual(shortStageWindow.detailsFrame.height, 400)
    }

    func testKeyboardGeometryIgnoresInvalidMeasurementsAndDoesNotReduceAvailableHeight() throws {
        XCTAssertNil(VideoViewportKeyboardGeometry(viewportHeight: 0, windowHeight: 1000))
        XCTAssertNil(VideoViewportKeyboardGeometry(viewportHeight: .nan, windowHeight: 1000))
        let keyboard = try XCTUnwrap(VideoViewportKeyboardGeometry(viewportHeight: 700, windowHeight: 800))
        XCTAssertEqual(keyboard.unobscuredHeight(viewportHeight: 720, windowHeight: 800), 720)
        XCTAssertEqual(keyboard.unobscuredHeight(viewportHeight: 400, windowHeight: .nan), 400)
        let invalid = VideoViewportGeometry(size: CGSize(width: 930, height: 700), unobscuredHeight: .nan)
        XCTAssertEqual(invalid, VideoViewportGeometry(size: CGSize(width: 930, height: 700)))
    }

    func testVeryWideMediaKeepsSeparateControlRowsInShortWindows() {
        for size in [CGSize(width: 393, height: 250), CGSize(width: 930, height: 300)] {
            let layout = VideoViewportGeometry(size: size, aspectRatio: 4)
            XCTAssertGreaterThanOrEqual(layout.playerFrame.height, 44 * 3 + 3)
            XCTAssertEqual(layout.playerFrame.width, layout.playerColumn.width)
            XCTAssertTrue(CGRect(origin: .zero, size: size).contains(layout.playerFrame))
            XCTAssertFalse(layout.playerFrame.insetBy(dx: 0.5, dy: 0.5).intersects(layout.detailsFrame))
            let imageFrame = AVMakeRect(aspectRatio: CGSize(width: 4, height: 1), insideRect: layout.playerFrame)
            XCTAssertTrue(layout.playerFrame.insetBy(dx: -0.5, dy: -0.5).contains(imageFrame))
            XCTAssertEqual(imageFrame.width / imageFrame.height, 4, accuracy: 0.005)
        }
    }

    private func resize<Content: View>(_ host: UIHostingController<Content>, to size: CGSize,
                                      probes: ViewportProbes, aspectRatio: CGFloat = 16.0 / 9.0,
                                      playback: PlaybackCoordinator? = nil) async {
        host.view.frame = CGRect(origin: .zero, size: size)
        host.view.setNeedsLayout()
        let expected = VideoViewportGeometry(size: size, aspectRatio: aspectRatio)
        // GeometryReader and representable updates are scheduled by SwiftUI.
        // Wait for observed geometry, rather than assuming one run-loop yield
        // mounts the hosting tree or propagates its new proposal to AVPlayerLayer.
        for _ in 0..<250 {
            host.view.window?.layoutIfNeeded()
            host.view.layoutIfNeeded()
            if let player = probes.player, let details = probes.details,
               player.window === host.view.window, details.window === host.view.window,
               rect(player.convert(player.bounds, to: host.view), matches: expected.playerFrame),
               rect(details.convert(details.bounds, to: host.view), matches: expected.detailsFrame) {
                if let playback {
                    if let presentation = playback.presentation,
                       presentation.controller.viewIfLoaded?.window === host.view.window,
                       rect(presentation.videoView.playerLayer.bounds,
                            matches: CGRect(origin: .zero, size: expected.playerFrame.size)) {
                        return
                    }
                } else {
                    return
                }
            }
            try? await Task.sleep(for: .milliseconds(20))
        }
        XCTFail("Hosted viewport did not settle at \(size); player: \(String(describing: probes.player?.frame)), details: \(String(describing: probes.details?.frame))")
    }

    private func rect(_ actual: CGRect, matches expected: CGRect) -> Bool {
        abs(actual.minX - expected.minX) < 0.5 && abs(actual.minY - expected.minY) < 0.5 &&
        abs(actual.width - expected.width) < 0.5 && abs(actual.height - expected.height) < 0.5
    }

    private func assertRect(_ actual: CGRect, equals expected: CGRect, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertEqual(actual.minX, expected.minX, accuracy: 0.5, file: file, line: line)
        XCTAssertEqual(actual.minY, expected.minY, accuracy: 0.5, file: file, line: line)
        XCTAssertEqual(actual.width, expected.width, accuracy: 0.5, file: file, line: line)
        XCTAssertEqual(actual.height, expected.height, accuracy: 0.5, file: file, line: line)
    }
}

@MainActor
private final class ViewportWindowFixture {
    private let window: UIWindow
    private let previousKeyWindow: UIWindow?
    private let host: UIViewController

    init(host: UIViewController, size: CGSize) throws {
        guard let scene = UIApplication.shared.connectedScenes.compactMap({ $0 as? UIWindowScene })
            .first(where: { $0.activationState == .foregroundActive }) else {
            throw XCTSkip("A foreground simulator window scene is required")
        }
        self.host = host
        previousKeyWindow = scene.windows.first(where: \.isKeyWindow)
        window = UIWindow(windowScene: scene)
        window.frame = scene.coordinateSpace.bounds
        let container = UIViewController()
        window.rootViewController = container
        container.loadViewIfNeeded()
        container.addChild(host)
        host.loadViewIfNeeded()
        // The hosting controller must be a child. A window resizes its root
        // controller to screen bounds, which would erase our sidebar proposals.
        host.view.autoresizingMask = []
        host.view.frame = CGRect(origin: .zero, size: size)
        container.view.addSubview(host.view)
        host.didMove(toParent: container)
        window.makeKeyAndVisible()
    }

    func close() {
        window.isHidden = true
        host.willMove(toParent: nil)
        host.view.removeFromSuperview()
        host.removeFromParent()
        window.rootViewController = nil
        previousKeyWindow?.makeKey()
    }
}

@MainActor
private final class ViewportProbes {
    weak var player: UIView?
    weak var details: UIView?
    var playerCreations = 0
    var detailsCreations = 0
}

private struct ViewportProbe: UIViewRepresentable {
    enum Kind { case player, details }
    let kind: Kind
    let probes: ViewportProbes

    func makeUIView(context: Context) -> UIView {
        let view = UIView(frame: .zero)
        switch kind {
        case .player: probes.player = view; probes.playerCreations += 1
        case .details: probes.details = view; probes.detailsCreations += 1
        }
        return view
    }

    func updateUIView(_ uiView: UIView, context: Context) { }

    func sizeThatFits(_ proposal: ProposedViewSize, uiView: UIView, context: Context) -> CGSize? {
        guard let width = proposal.width, let height = proposal.height else { return nil }
        return CGSize(width: width, height: height)
    }
}
