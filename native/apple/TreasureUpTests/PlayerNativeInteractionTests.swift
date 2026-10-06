import XCTest
import UIKit
@testable import TreasureUp

@MainActor
final class PlayerNativeInteractionTests: XCTestCase {
    func testProgressClockCannotPullThumbBackAndReleaseCommitsOnlyOnce() {
        let slider = PlayerSeekSlider()
        slider.frame = CGRect(x: 0, y: 0, width: 418, height: 44)
        var previews: [Double] = []
        var commits: [Double] = []
        var editing: [Bool] = []
        func configuration(_ position: Double) -> PlayerSeekBar {
            PlayerSeekBar(value: position, duration: 100, bufferedTime: 65, mediaID: "part-a",
                onPreview: { previews.append($0) }, onEditingChanged: { editing.append($0) }, onCommit: { commits.append($0) })
        }
        slider.configure(configuration(10))
        XCTAssertTrue(slider.beginInteraction(at: 209))
        for tick in 0..<20 {
            slider.configure(configuration(10 + Double(tick) / 4))
            XCTAssertEqual(slider.value, 0.5, accuracy: 0.0001)
        }
        slider.updateInteraction(at: 329)
        XCTAssertEqual(previews.last!, 80, accuracy: 0.001)
        XCTAssertTrue(commits.isEmpty, "Dragging must never launch repeated media seeks")
        slider.finishInteraction()
        slider.finishInteraction()
        XCTAssertEqual(commits, [80])
        XCTAssertEqual(editing, [true, false])
        XCTAssertFalse(slider.isScrubbing)
    }

    func testCancelledScrubRestoresLatestClockWithoutSeeking() {
        let slider = PlayerSeekSlider()
        slider.frame = CGRect(x: 0, y: 0, width: 418, height: 44)
        var commits = 0
        var editing: [Bool] = []
        func configure(_ time: Double) {
            slider.configure(PlayerSeekBar(value: time, duration: 100, bufferedTime: 60, mediaID: "a",
                onPreview: { _ in }, onEditingChanged: { editing.append($0) }, onCommit: { _ in commits += 1 }))
        }
        configure(10)
        slider.beginInteraction(at: 350)
        configure(12)
        slider.cancelInteraction()
        slider.finishInteraction()
        XCTAssertEqual(slider.value, 0.12, accuracy: 0.001)
        XCTAssertEqual(commits, 0)
        XCTAssertEqual(editing, [true, false])
    }

    func testPartReplacementAndZeroDurationCannotReceiveOldScrubCommit() {
        let slider = PlayerSeekSlider()
        slider.frame = CGRect(x: 0, y: 0, width: 418, height: 44)
        var commits: [Double] = []
        func configure(_ id: String, duration: Double) {
            slider.configure(PlayerSeekBar(value: 0, duration: duration, bufferedTime: .infinity, mediaID: id,
                onPreview: { _ in }, onEditingChanged: { _ in }, onCommit: { commits.append($0) }))
        }
        configure("a", duration: 100)
        slider.beginInteraction(at: 350)
        configure("b", duration: 20)
        slider.finishInteraction()
        XCTAssertTrue(commits.isEmpty)
        XCTAssertEqual(slider.value, 0)
        configure("c", duration: 0)
        XCTAssertFalse(slider.beginInteraction(at: 20))
        slider.accessibilityIncrement()
        XCTAssertTrue(commits.isEmpty)
        slider.layoutIfNeeded()
        XCTAssertTrue(slider.layer.sublayers!.allSatisfy { $0.frame.width.isFinite && $0.frame.height.isFinite })
    }

    func testAccessibleProgressHasTenSecondStepsAndClampsBothEnds() {
        let slider = PlayerSeekSlider()
        var commits: [Double] = []
        slider.configure(PlayerSeekBar(value: 12, duration: 25, bufferedTime: 20, mediaID: "a",
            onPreview: { _ in }, onEditingChanged: { _ in }, onCommit: { commits.append($0) }))
        slider.accessibilityIncrement()
        slider.accessibilityIncrement()
        for _ in 0..<3 { slider.accessibilityDecrement() }
        XCTAssertEqual(commits, [22, 25, 15, 5, 0])
        XCTAssertEqual(slider.accessibilityValue, "00:00，共 00:25")
        XCTAssertEqual(slider.accessibilityTraits, .adjustable)
    }

    func testShowingControlsChangesOnlyHitRegionAndKeepsRecognizers() {
        let view = PlayerGestureView()
        view.frame = CGRect(x: 0, y: 0, width: 400, height: 225)
        func configuration(_ visible: Bool) -> PlayerGestureSurface {
            PlayerGestureSurface(activeBounds: visible ? CGRect(x: 0, y: 44, width: 400, height: 90) : view.bounds,
                controlsVisible: visible, isEnabled: true, onReveal: {}, onToggleControls: {},
                onSkip: { _ in }, onScrub: { _, _ in }, onHold: { _ in })
        }
        view.configure(configuration(false))
        let original = view.gestureRecognizers!.map(ObjectIdentifier.init)
        XCTAssertTrue(view.point(inside: CGPoint(x: 380, y: 205), with: nil))
        for _ in 0..<20 {
            view.configure(configuration(true))
            XCTAssertFalse(view.point(inside: CGPoint(x: 380, y: 205), with: nil), "Fullscreen and pause bands belong to buttons")
            XCTAssertFalse(view.point(inside: CGPoint(x: 380, y: 20), with: nil))
            XCTAssertTrue(view.point(inside: CGPoint(x: 300, y: 90), with: nil))
            view.configure(configuration(false))
        }
        XCTAssertEqual(view.gestureRecognizers!.map(ObjectIdentifier.init), original)
        XCTAssertTrue(view.gestureRecognizer(view.singleTap, shouldRecognizeSimultaneouslyWith: view.doubleTap))
        XCTAssertFalse(view.gestureRecognizer(view.pan, shouldRecognizeSimultaneouslyWith: view.hold))
    }

    func testUnknownDurationDuringScrubEndsEditingWithoutCommittingStaleTarget() async {
        for unavailableDuration in [0, Double.nan, .infinity] {
            let slider = PlayerSeekSlider()
            slider.frame = CGRect(x: 0, y: 0, width: 418, height: 44)
            var commits: [Double] = []
            var editing: [Bool] = []
            func configure(duration: Double) {
                slider.configure(PlayerSeekBar(value: 10, duration: duration, bufferedTime: 60, mediaID: "a",
                    onPreview: { _ in }, onEditingChanged: { editing.append($0) }, onCommit: { commits.append($0) }))
            }
            configure(duration: 100)
            XCTAssertTrue(slider.beginInteraction(at: 329))
            configure(duration: unavailableDuration)
            slider.finishInteraction()
            for _ in 0..<20 where editing.last != false { await Task.yield() }
            XCTAssertFalse(slider.isScrubbing)
            XCTAssertFalse(slider.isEnabled)
            XCTAssertTrue(commits.isEmpty, "The previous item's duration cannot be used for an invalid seek")
            XCTAssertEqual(editing, [true, false], "The parent must release its auto-hide and preview gates")
        }
    }

    func testItemReplacementCannotEndANewerScrubThroughDeferredCancellation() async {
        let slider = PlayerSeekSlider()
        slider.frame = CGRect(x: 0, y: 0, width: 418, height: 44)
        var editing: [Bool] = []
        var commits: [Double] = []
        func configure(_ id: String) {
            slider.configure(PlayerSeekBar(value: 10, duration: 100, bufferedTime: 60, mediaID: id,
                onPreview: { _ in }, onEditingChanged: { editing.append($0) }, onCommit: { commits.append($0) }))
        }
        configure("a")
        slider.beginInteraction(at: 329)
        configure("b")
        XCTAssertTrue(slider.beginInteraction(at: 209))
        for _ in 0..<10 { await Task.yield() }
        XCTAssertTrue(slider.isScrubbing)
        XCTAssertEqual(editing, [true, true], "An old deferred callback must not clear the new interaction")
        slider.finishInteraction()
        XCTAssertEqual(commits, [50])
        XCTAssertEqual(editing.last, false)
    }

    func testSidebarResizeCancelsProgressTouchWithoutChangingPlaybackPosition() {
        let slider = PlayerSeekSlider()
        slider.frame = CGRect(x: 0, y: 0, width: 418, height: 44)
        var commits: [Double] = []
        var editing: [Bool] = []
        slider.configure(PlayerSeekBar(value: 10, duration: 100, bufferedTime: 60, mediaID: "a",
            onPreview: { _ in }, onEditingChanged: { editing.append($0) }, onCommit: { commits.append($0) }))
        slider.layoutIfNeeded()
        slider.beginInteraction(at: 329)
        slider.frame.size.width = 300
        slider.setNeedsLayout()
        slider.layoutIfNeeded()
        slider.finishInteraction()
        XCTAssertTrue(commits.isEmpty)
        XCTAssertEqual(slider.value, 0.1, accuracy: 0.001)
        XCTAssertEqual(editing, [true, false])
    }

    func testUpdatedDurationClampsCommittedPositionAndRejectsInvalidInitialTouches() {
        let slider = PlayerSeekSlider()
        slider.frame = CGRect(x: 0, y: 0, width: 418, height: 44)
        var commits: [Double] = []
        var editing: [Bool] = []
        func configure(_ duration: Double) {
            slider.configure(PlayerSeekBar(value: 10, duration: duration, bufferedTime: 60, mediaID: "a",
                onPreview: { _ in }, onEditingChanged: { editing.append($0) }, onCommit: { commits.append($0) }))
        }
        configure(100)
        XCTAssertFalse(slider.beginInteraction(at: .nan))
        XCTAssertTrue(editing.isEmpty)
        XCTAssertTrue(slider.beginInteraction(at: 369))
        XCTAssertFalse(slider.beginInteraction(at: 100), "One physical touch has exactly one editing lifecycle")
        configure(60)
        slider.finishInteraction()
        XCTAssertEqual(commits, [60])
        XCTAssertEqual(slider.value, 1)
        XCTAssertEqual(editing, [true, false])
    }

    func testPictureDragKeepsOriginalCallbacksAndResizeCancelsInsteadOfSeeking() {
        let view = PlayerGestureView()
        view.frame = CGRect(x: 0, y: 0, width: 400, height: 225)
        var firstPhases: [PlayerGesturePhase] = []
        var secondPhases: [PlayerGesturePhase] = []
        func configuration(_ callback: @escaping (PlayerGesturePhase) -> Void) -> PlayerGestureSurface {
            PlayerGestureSurface(activeBounds: view.bounds, controlsVisible: false, isEnabled: true,
                onReveal: {}, onToggleControls: {}, onSkip: { _ in },
                onScrub: { _, phase in callback(phase) }, onHold: { _ in })
        }
        view.configure(configuration { firstPhases.append($0) })
        view.layoutIfNeeded()
        view.handlePan(translation: 5, phase: .began)
        view.configure(configuration { secondPhases.append($0) })
        view.handlePan(translation: 40, phase: .changed)
        view.frame.size.width = 300
        view.setNeedsLayout()
        view.layoutIfNeeded()
        view.handlePan(translation: 50, phase: .ended)
        XCTAssertEqual(firstPhases, [.began, .changed, .cancelled])
        XCTAssertTrue(secondPhases.isEmpty, "A changing view must not steal the old gesture's final callback")
        view.handlePan(translation: 5, phase: .began)
        view.handlePan(translation: 10, phase: .ended)
        XCTAssertEqual(secondPhases, [.began, .ended], "Fresh gestures use the new viewport configuration")
    }

    func testSidebarResizeRestoresTemporaryRateOnlyOnce() {
        let view = PlayerGestureView()
        view.frame = CGRect(x: 0, y: 0, width: 400, height: 225)
        var holding: [Bool] = []
        view.configure(PlayerGestureSurface(activeBounds: view.bounds, controlsVisible: false, isEnabled: true,
            onReveal: {}, onToggleControls: {}, onSkip: { _ in }, onScrub: { _, _ in },
            onHold: { holding.append($0) }))
        view.handleHold(active: true)
        view.frame.size.height = 300
        view.setNeedsLayout()
        view.layoutIfNeeded()
        view.handleHold(active: false)
        view.cancelInteractions()
        XCTAssertEqual(holding, [true, false])
    }

}
