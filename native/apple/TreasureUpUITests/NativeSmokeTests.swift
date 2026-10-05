import XCTest

/// Opt-in integration checks against the configured test server, never required
/// for offline unit-test runs. No login credentials are embedded in the bundle.
final class NativeSmokeTests: XCTestCase {
    @MainActor
    func testCatalogNavigationAndPlayerPresentation() throws {
        guard ProcessInfo.processInfo.environment["TREASURE_UI_SMOKE"] == "1" else {
            throw XCTSkip("Set TREASURE_UI_SMOKE=1 in the test scheme for live-server UI checks.")
        }
        let app = XCUIApplication()
        app.launch()
        XCTAssertTrue(app.navigationBars["资料库"].waitForExistence(timeout: 30))
        let first = app.scrollViews.buttons.matching(NSPredicate(format: "label CONTAINS %@", "红豆")).firstMatch
        XCTAssertTrue(first.waitForExistence(timeout: 20))
        first.tap()
        XCTAssertTrue(app.buttons["playVideo"].waitForExistence(timeout: 15))
        app.buttons["playVideo"].tap()
        XCTAssertTrue(app.sliders["player-progress"].waitForExistence(timeout: 30))
        XCTAssertTrue(app.segmentedControls["videoSections"].exists)
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "Native player"
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
