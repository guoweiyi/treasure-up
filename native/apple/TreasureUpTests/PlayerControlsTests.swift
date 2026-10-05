import XCTest
@testable import TreasureUp

final class PlayerControlsTests: XCTestCase {
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
