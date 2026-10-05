import XCTest
@testable import TreasureUp

final class PlayerControlsTests: XCTestCase {
    func testOnlyClearlyHorizontalDragsBeginSeeking() {
        XCTAssertEqual(PlayerGestureMath.dragAxis(horizontal: 30, vertical: 5), .horizontal)
        XCTAssertEqual(PlayerGestureMath.dragAxis(horizontal: -30, vertical: -5), .horizontal)
        XCTAssertEqual(PlayerGestureMath.dragAxis(horizontal: 5, vertical: 30), .vertical)
        XCTAssertEqual(PlayerGestureMath.dragAxis(horizontal: 20, vertical: 20), .vertical)
        XCTAssertEqual(PlayerGestureMath.dragAxis(horizontal: .nan, vertical: 0), .vertical)
    }

    func testProgressClampsTransientPlaybackTimesToSliderRange() {
        // Duration and current time arrive independently during a part change.
        XCTAssertEqual(PlayerGestureMath.clampedTime(120, duration: 30), 30)
        XCTAssertEqual(PlayerGestureMath.clampedTime(-10, duration: 30), 0)
        XCTAssertEqual(PlayerGestureMath.clampedTime(12, duration: 30), 12)
        XCTAssertEqual(PlayerGestureMath.clampedTime(12, duration: 0), 0)
        XCTAssertEqual(PlayerGestureMath.clampedTime(.nan, duration: 30), 0)
        XCTAssertEqual(PlayerGestureMath.clampedTime(12, duration: .infinity), 0)
    }

    func testHorizontalScrubUsesDragStartAndClampsBothEnds() {
        XCTAssertEqual(PlayerGestureMath.scrubPosition(start: 60, translation: 100, width: 400, duration: 600), 90)
        XCTAssertEqual(PlayerGestureMath.scrubPosition(start: 60, translation: -100, width: 400, duration: 600), 30)
        XCTAssertEqual(PlayerGestureMath.scrubPosition(start: 10, translation: -400, width: 400, duration: 600), 0)
        XCTAssertEqual(PlayerGestureMath.scrubPosition(start: 590, translation: 400, width: 400, duration: 600), 600)
    }

    func testLongVideoScrubWindowStaysControllableAndShortVideoNeverOvershoots() {
        XCTAssertEqual(PlayerGestureMath.scrubPosition(start: 1_000, translation: 400, width: 400, duration: 7_200), 1_180)
        XCTAssertEqual(PlayerGestureMath.scrubPosition(start: 2, translation: 400, width: 400, duration: 8), 8)
        XCTAssertEqual(PlayerGestureMath.scrubPosition(start: 4, translation: -100, width: 400, duration: 8), 0)
    }

    func testGestureMathHandlesTransientZeroLayoutAndInvalidMediaTime() {
        XCTAssertEqual(PlayerGestureMath.scrubPosition(start: 30, translation: 50, width: 0, duration: 100), 0)
        XCTAssertEqual(PlayerGestureMath.scrubPosition(start: .nan, translation: 50, width: 300, duration: 100), 0)
        XCTAssertEqual(PlayerGestureMath.scrubPosition(start: 30, translation: .infinity, width: 300, duration: 100), 0)
        XCTAssertEqual(PlayerGestureMath.scrubPosition(start: 30, translation: 50, width: 300, duration: .infinity), 0)
        XCTAssertEqual(PlayerGestureMath.scrubPosition(start: 30, translation: 50, width: 300, duration: -10), 0)
    }

    func testPlaybackClockUsesHoursOnlyWhenNeededAndRejectsNonfiniteTime() {
        XCTAssertEqual(PlayerGestureMath.timeLabel(0), "00:00")
        XCTAssertEqual(PlayerGestureMath.timeLabel(65.9), "01:05")
        XCTAssertEqual(PlayerGestureMath.timeLabel(3_661), "1:01:01")
        XCTAssertEqual(PlayerGestureMath.timeLabel(-1), "00:00")
        XCTAssertEqual(PlayerGestureMath.timeLabel(.nan), "00:00")
        XCTAssertEqual(PlayerGestureMath.timeLabel(.infinity), "00:00")
    }
}
