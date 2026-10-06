import XCTest
import SwiftUI
@testable import TreasureUp

final class PlayerControlsTests: XCTestCase {
    func testInlineViewportDoesNotApplyAncestorNavigationInsetsTwice() {
        let inherited = EdgeInsets(top: 106, leading: 24, bottom: 34, trailing: 24)
        let insets = PlayerGestureMath.controlInsets(isExpanded: false, reported: inherited)
        let region = PlayerGestureMath.gestureBounds(size: CGSize(width: 393, height: 221), insets: insets,
                                                   controlsVisible: true, largeText: false)
        // The header stays in the first 44pt and the transport rows stay at the
        // bottom; the ancestor's 106pt navigation area must not collapse this gap.
        XCTAssertEqual(region.minY, 44)
        XCTAssertEqual(region.maxY, 130)
        XCTAssertTrue(region.contains(CGPoint(x: 196, y: 90)))
        XCTAssertFalse(region.contains(CGPoint(x: 196, y: 22)))
        XCTAssertFalse(region.contains(CGPoint(x: 196, y: 199)))
        XCTAssertEqual(insets.leading, 0)
        XCTAssertEqual(insets.trailing, 0)
    }

    func testFullscreenViewportStillReservesDeviceSafeAreas() {
        let reported = EdgeInsets(top: 24, leading: 59, bottom: 21, trailing: 59)
        let insets = PlayerGestureMath.controlInsets(isExpanded: true, reported: reported)
        let region = PlayerGestureMath.gestureBounds(size: CGSize(width: 852, height: 393), insets: insets,
                                                   controlsVisible: true, largeText: false)
        XCTAssertEqual(region.minY, 68)
        XCTAssertEqual(region.maxY, 284)
        XCTAssertEqual(insets.leading, 59)
        XCTAssertEqual(insets.trailing, 59)
        XCTAssertFalse(region.contains(CGPoint(x: 200, y: 45)))
        XCTAssertFalse(region.contains(CGPoint(x: 200, y: 350)))
    }

    func testVisibleTransportBandsNeverBelongToVideoGestures() {
        let size = CGSize(width: 390, height: 220)
        let region = PlayerGestureMath.gestureBounds(size: size, insets: EdgeInsets(), controlsVisible: true, largeText: false)
        XCTAssertTrue(region.contains(CGPoint(x: 195, y: 80)))
        for point in [CGPoint(x: 22, y: 22), CGPoint(x: 22, y: 195),
                      CGPoint(x: 368, y: 195), CGPoint(x: 195, y: 150)] {
            XCTAssertFalse(region.contains(point), "Header, pause, fullscreen and progress coordinates must be isolated")
        }
        let hidden = PlayerGestureMath.gestureBounds(size: size, insets: EdgeInsets(), controlsVisible: false, largeText: false)
        XCTAssertTrue(hidden.contains(CGPoint(x: 368, y: 195)), "A first tap anywhere reveals hidden controls")
    }

    func testGestureRegionRespectsSafeAreasAndCollapsesBeforeOverlappingLargeControls() {
        let insets = EdgeInsets(top: 24, leading: 0, bottom: 34, trailing: 0)
        let region = PlayerGestureMath.gestureBounds(size: CGSize(width: 900, height: 400), insets: insets,
                                                   controlsVisible: true, largeText: false)
        XCTAssertEqual(region.minY, 68)
        XCTAssertEqual(region.maxY, 278)
        let crowded = PlayerGestureMath.gestureBounds(size: CGSize(width: 320, height: 150), insets: insets,
                                                    controlsVisible: true, largeText: true)
        XCTAssertEqual(crowded.height, 0)
        XCTAssertEqual(PlayerGestureMath.gestureBounds(size: .zero, insets: insets, controlsVisible: true, largeText: false), .zero)
    }

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

    func testClockSnapshotCoalescesQuarterSecondTicksButImmediatelyReflectsSeek() {
        // These snapshots are the Equatable input to the actual clock label.
        // Twelve progress updates at normal speed need only three Text values.
        let snapshots = (0..<12).map {
            PlayerClockSnapshot(position: 60 + Double($0) / 4, duration: 600)
        }
        let labelUpdates = zip(snapshots, snapshots.dropFirst()).filter { $0 != $1 }.count + 1
        XCTAssertEqual(labelUpdates, 3)
        XCTAssertEqual(snapshots.first?.position, 60)
        XCTAssertEqual(snapshots.last?.position, 62)

        let seekPreview = PlayerClockSnapshot(position: 185.75, duration: 600)
        XCTAssertNotEqual(seekPreview, snapshots.last)
        XCTAssertEqual(seekPreview.position, 185)
        XCTAssertNotEqual(seekPreview, PlayerClockSnapshot(position: 185.75, duration: 601))
    }

    func testClockSnapshotClampsPartChangesAndInvalidMediaValues() {
        XCTAssertEqual(PlayerClockSnapshot(position: 80, duration: 30).position, 30)
        XCTAssertEqual(PlayerClockSnapshot(position: -1, duration: 30).position, 0)
        XCTAssertEqual(PlayerClockSnapshot(position: .nan, duration: 30).position, 0)
        XCTAssertEqual(PlayerClockSnapshot(position: .infinity, duration: 30).position, 0)
        XCTAssertEqual(PlayerClockSnapshot(position: 10, duration: .nan),
                       PlayerClockSnapshot(position: 0, duration: 0))
        XCTAssertEqual(PlayerClockSnapshot(position: 10, duration: .infinity).duration, 0)
        XCTAssertEqual(PlayerClockSnapshot(position: 1_000_000, duration: 1_000_000).position, 359_999)
    }
}
