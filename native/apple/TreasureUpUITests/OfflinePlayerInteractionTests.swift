import XCTest

/// Run directly with -only-testing:TreasureUpUITests/OfflinePlayerInteractionTests.
/// These tests opt into the DEBUG simulator fixture, never the live smoke path.
@MainActor
final class OfflinePlayerInteractionTests: XCTestCase {
    private var launchedApp: XCUIApplication?

    func testRepeatedTransportAndFullscreenUseActualPlayer() {
        defer { XCUIDevice.shared.orientation = .portrait }
        let app = launch()
        let isPhone = app.staticTexts["offline-device-kind"].label == "iPhone"
        if isPhone { XCTAssertGreaterThan(self.mainWindowFrame(in: app).height, self.mainWindowFrame(in: app).width) }
        setPlaying(true, in: app)
        control("player-play-pause", in: app).press(forDuration: 4)
        wait("Holding the pause button beyond auto-hide must still pause on release") {
            app.staticTexts["offline-player-state"].label == "paused"
        }
        for _ in 0..<4 {
            setPlaying(false, in: app)
            setPlaying(true, in: app)
        }
        setPlaying(false, in: app)
        for _ in 0..<2 {
            control("player-fullscreen", in: app).tap()
            let fullscreen = app.otherElements["fullscreen-player"].firstMatch
            wait("Fullscreen must cover the actual app window") {
                guard fullscreen.exists else { return false }
                let frame = fullscreen.frame
                let window = self.mainWindowFrame(in: app)
                return abs(frame.minX - window.minX) < 3 && abs(frame.minY - window.minY) < 3 &&
                    abs(frame.width - window.width) < 3 && abs(frame.height - window.height) < 3
            }
            if isPhone {
                wait("Entering landscape-video fullscreen must rotate the portrait phone window") {
                    self.mainWindowFrame(in: app).width > self.mainWindowFrame(in: app).height
                }
            }
            let transport = control("player-play-pause", in: app)
            XCTAssertTrue(fullscreen.frame.insetBy(dx: -1, dy: -1).contains(transport.frame))
            control("player-fullscreen", in: app).tap()
            wait("Fullscreen must dismiss") { !fullscreen.exists }
            if isPhone {
                wait("Leaving fullscreen must restore the portrait phone window") {
                    self.mainWindowFrame(in: app).height > self.mainWindowFrame(in: app).width
                }
            }
            // Actual AVPlayer/KVO state, rather than only the button's requested
            // intent, must still respond after the shared layer transfers back.
            setPlaying(true, in: app)
            setPlaying(false, in: app)
        }
    }

    func testIPadSidebarResizeKeepsTransportAndFullscreenHittable() throws {
        defer { XCUIDevice.shared.orientation = .portrait }
        let app = launch(landscapeWindow: true)
        guard app.staticTexts["offline-device-kind"].label == "iPad" else {
            throw XCTSkip("The sidebar scenario requires an iPad simulator")
        }
        setPlaying(false, in: app)
        let sidebar = app.staticTexts["offline-sidebar"]
        XCTAssertTrue(sidebar.waitForExistence(timeout: 5))
        XCTAssertTrue(sidebar.isHittable)
        // iPadOS 26 can report the whole window for SwiftUI's accessibility
        // group even while its actual player column is narrower. Measure the
        // native render surface, which is also the region receiving gestures.
        let sidebarWidth = pictureFrame(in: app).width
        let toggle = app.buttons["offline-sidebar-toggle"]
        toggle.tap()
        wait("Hiding the sidebar must enlarge the actual player viewport") {
            !sidebar.isHittable && self.pictureFrame(in: app).width > sidebarWidth + 30
        }
        let expandedWidth = pictureFrame(in: app).width
        setPlaying(true, in: app)
        setPlaying(false, in: app)
        toggle.tap()
        wait("Showing the sidebar must shrink the actual player viewport") {
            sidebar.isHittable && self.pictureFrame(in: app).width < expandedWidth - 30
        }
        let button = control("player-play-pause", in: app)
        XCTAssertTrue(pictureFrame(in: app).insetBy(dx: -1, dy: -1).contains(button.frame))
        setPlaying(true, in: app)
        setPlaying(false, in: app)
        control("player-fullscreen", in: app).tap()
        let fullscreen = app.otherElements["fullscreen-player"].firstMatch
        XCTAssertTrue(fullscreen.waitForExistence(timeout: 10))
        wait("Fullscreen must include the sidebar area") { fullscreen.frame.width >= self.mainWindowFrame(in: app).width - 3 }
        control("player-fullscreen", in: app).tap()
        wait("Exit fullscreen must restore inline controls") { !fullscreen.exists }
        setPlaying(true, in: app)
    }

    func testPortraitVideoRetainsFullSizeTopAndTransportTargets() {
        defer { XCUIDevice.shared.orientation = .portrait }
        let app = launch(portrait: true)
        setPlaying(false, in: app)
        XCTAssertEqual(app.staticTexts["offline-media-orientation"].label, "portrait")
        let player = app.otherElements["inline-player"].firstMatch
        let transport = control("player-play-pause", in: app)
        let more = control("更多播放选项", in: app)
        for button in [transport, more] {
            XCTAssertGreaterThanOrEqual(button.frame.width, 43)
            XCTAssertGreaterThanOrEqual(button.frame.height, 43)
            XCTAssertTrue(player.frame.insetBy(dx: -1, dy: -1).contains(button.frame),
                          "Portrait media must not crop the real 44pt control targets")
        }
        more.tap()
        XCTAssertTrue(app.descendants(matching: .any)["player-options-panel"].firstMatch.waitForExistence(timeout: 5))
        let close = app.buttons["player-panel-close"].firstMatch
        XCTAssertTrue(close.waitForExistence(timeout: 5))
        close.tap()
        setPlaying(true, in: app)
        setPlaying(false, in: app)
        control("player-fullscreen", in: app).tap()
        let fullscreen = app.otherElements["fullscreen-player"].firstMatch
        XCTAssertTrue(fullscreen.waitForExistence(timeout: 10))
        wait("Portrait fullscreen must stay portrait and cover the entire window") {
            let frame = fullscreen.frame
            let window = self.mainWindowFrame(in: app)
            return window.height > window.width &&
                abs(frame.minX - window.minX) < 3 && abs(frame.minY - window.minY) < 3 &&
                abs(frame.width - window.width) < 3 && abs(frame.height - window.height) < 3
        }
        control("player-fullscreen", in: app).tap()
        wait("Portrait fullscreen must dismiss") { !fullscreen.exists }
        setPlaying(true, in: app)
    }

    func testNativeScrubberAndHiddenDoubleTapKeepPausedPlaybackAtRequestedPosition() {
        defer { XCUIDevice.shared.orientation = .portrait }
        let app = launch()
        let progress = app.sliders["player-progress"]
        XCTAssertTrue(progress.waitForExistence(timeout: 5))
        progress.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        waitForSeek(near: 60, in: app)
        XCTAssertEqual(app.staticTexts["offline-player-state"].label, "paused")

        // Check single-tap reveal while paused, when the controls stay visible.
        // A slow CI accessibility snapshot must not race the normal 3.5-second
        // auto-hide timer, whose behavior is verified separately below.
        pictureCoordinate(in: app, x: 0.5).tap()
        wait("A single tap hides the controls") { !app.buttons["player-play-pause"].exists }
        pictureCoordinate(in: app, x: 0.5).tap()
        XCTAssertTrue(app.buttons["player-play-pause"].waitForExistence(timeout: 2))
        XCTAssertEqual(app.staticTexts["offline-player-state"].label, "paused")

        // Hide again, then double tap without first revealing the controls:
        // that same pair of touches must skip only once.
        pictureCoordinate(in: app, x: 0.5).tap()
        wait("Controls must be hidden before the double tap") { !app.buttons["player-play-pause"].exists }
        pictureCoordinate(in: app, x: 0.8).doubleTap()
        waitForSeek(near: 75, in: app)
        XCTAssertEqual(app.staticTexts["offline-player-state"].label, "paused")

        // A horizontal picture gesture previews locally and commits on release.
        let start = pictureCoordinate(in: app, x: 0.3)
        let end = pictureCoordinate(in: app, x: 0.7)
        start.press(forDuration: 0.05, thenDragTo: end)
        waitForSeek(near: 87, in: app)
        XCTAssertEqual(app.staticTexts["offline-player-state"].label, "paused")
    }

    func testInlineSpeedSelectionAndPanelDismissalRestoreAutoHide() {
        let app = launch()
        setPlaying(true, in: app)
        control("player-speed", in: app).tap()
        XCTAssertTrue(app.descendants(matching: .any)["player-options-panel"].firstMatch.waitForExistence(timeout: 5))
        let panelScreenshot = XCTAttachment(screenshot: app.screenshot())
        panelScreenshot.name = "Inline speed panel"
        panelScreenshot.lifetime = .keepAlways
        add(panelScreenshot)
        let rate = app.buttons["1.5 倍速"].firstMatch
        XCTAssertTrue(rate.waitForExistence(timeout: 5))
        rate.tap()
        wait("Speed selection must reach the player") { app.staticTexts["offline-player-rate"].label == "1.50" }
        control("更多播放选项", in: app).tap()
        let close = app.buttons["player-panel-close"].firstMatch
        XCTAssertTrue(close.waitForExistence(timeout: 5))
        close.tap()
        wait("Closing a panel must rearm normal auto-hide", timeout: 8) { !app.buttons["player-play-pause"].exists }
    }

    private func pictureFrame(in app: XCUIApplication) -> CGRect {
        // SwiftUI's focus/accessibility group can include the navigation safe
        // area. Its native rendering child reports the actual video viewport.
        app.otherElements["inline-player"].firstMatch.children(matching: .other).firstMatch.frame
    }

    private func pictureCoordinate(in app: XCUIApplication, x: CGFloat) -> XCUICoordinate {
        let frame = pictureFrame(in: app)
        return app.coordinate(withNormalizedOffset: .zero)
            .withOffset(CGVector(dx: frame.minX + frame.width * x, dy: frame.minY + frame.height * 0.42))
    }

    private func waitForSeek(near target: Double, in app: XCUIApplication) {
        wait("The real paused AVPlayer must settle near \(target)") {
            let actual = Double(app.staticTexts["offline-player-actual-time"].label) ?? -1000
            return app.staticTexts["offline-player-seek-state"].label == "settled" && abs(actual - target) < 3
        }
    }

    private func launch(portrait: Bool = false, landscapeWindow: Bool = false) -> XCUIApplication {
        continueAfterFailure = false
        XCUIDevice.shared.orientation = landscapeWindow ? .landscapeLeft : .portrait
        let app = XCUIApplication()
        app.launchArguments = ["--offline-player-ui"]
        if portrait { app.launchArguments.append("--offline-player-portrait") }
        app.launch()
        launchedApp = app
        let status = app.staticTexts["offline-player-ready"]
        let statusExists = status.waitForExistence(timeout: 10)
        if !statusExists { print(app.debugDescription) }
        XCTAssertTrue(statusExists, "DEBUG offline fixture must be wired into the app entry point")
        wait("Local AVPlayer item must become ready after a real start/pause cycle", timeout: 40) {
            status.label == "ready"
        }
        XCTAssertEqual(app.staticTexts["offline-player-state"].label, "paused")
        return app
    }

    private func mainWindowFrame(in app: XCUIApplication) -> CGRect {
        // Dismissing an iPad menu can leave a zero-sized transient AX window.
        // Compare against the actual app window, never that first-match shell.
        app.windows.allElementsBoundByIndex.map(\.frame)
            .max { $0.width * $0.height < $1.width * $1.height } ?? .zero
    }

    private func setPlaying(_ playing: Bool, in app: XCUIApplication) {
        let state = app.staticTexts["offline-player-state"]
        let expected = playing ? "playing" : "paused"
        if state.label != expected { control("player-play-pause", in: app).tap() }
        wait("Actual AVPlayer must become \(expected)") { state.label == expected }
        XCTAssertEqual(control("player-play-pause", in: app).label, playing ? "暂停" : "播放")
    }

    private func control(_ identifier: String, in app: XCUIApplication) -> XCUIElement {
        let matches = app.buttons.matching(identifier: identifier)
        if let visible = matches.allElementsBoundByIndex.first(where: { $0.isHittable }) { return visible }
        let fullscreen = app.otherElements["fullscreen-player"].firstMatch
        let player = fullscreen.exists ? fullscreen : app.otherElements["inline-player"].firstMatch
        XCTAssertTrue(player.waitForExistence(timeout: 5))
        player.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        wait("Player control \(identifier) must be hittable") {
            matches.allElementsBoundByIndex.contains(where: { $0.isHittable })
        }
        return matches.allElementsBoundByIndex.first(where: { $0.isHittable }) ?? matches.firstMatch
    }

    private func wait(_ message: String, timeout: TimeInterval = 10, condition: @escaping () -> Bool) {
        let expectation = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in condition() }, object: nil)
        let result = XCTWaiter.wait(for: [expectation], timeout: timeout)
        if result != .completed, let launchedApp { print(launchedApp.debugDescription) }
        XCTAssertEqual(result, .completed, message)
    }
}
