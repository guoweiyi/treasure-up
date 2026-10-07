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

    func testDurationReloadCancelsDragAndBalancesEditingWithoutSeeking() {
        // A route/quality reload can report an unknown duration while keeping
        // the part ID unchanged. Releasing that touch must not seek the reload.
        for replacementDuration in [0, .nan, .infinity, 25] {
            let slider = PlayerSeekSlider()
            slider.frame = CGRect(x: 0, y: 0, width: 418, height: 44)
            var editing: [Bool] = []
            var commits: [Double] = []
            func configure(_ duration: Double) {
                slider.configure(PlayerSeekBar(value: 12, duration: duration, bufferedTime: 20, mediaID: "same-part",
                    onPreview: { _ in }, onEditingChanged: { editing.append($0) }, onCommit: { commits.append($0) }))
            }
            configure(100)
            XCTAssertTrue(slider.beginInteraction(at: 329))
            configure(replacementDuration)
            slider.finishInteraction()
            XCTAssertFalse(slider.isScrubbing)
            XCTAssertEqual(editing, [true, false])
            XCTAssertTrue(commits.isEmpty)
            XCTAssertTrue(slider.value.isFinite)
        }
    }

    func testMediaReplacementReturnsEditingToOriginalOwnerOnly() {
        let slider = PlayerSeekSlider()
        slider.frame = CGRect(x: 0, y: 0, width: 418, height: 44)
        var events: [String] = []
        func configure(_ media: String) {
            slider.configure(PlayerSeekBar(value: 10, duration: 100, bufferedTime: 20, mediaID: media,
                onPreview: { _ in }, onEditingChanged: { events.append("\(media):\($0)") },
                onCommit: { _ in events.append("\(media):commit") }))
        }
        configure("old")
        slider.beginInteraction(at: 209)
        configure("new")
        slider.finishInteraction()
        XCTAssertEqual(events, ["old:true", "old:false"])
        XCTAssertTrue(slider.beginInteraction(at: 209))
        slider.finishInteraction()
        XCTAssertEqual(events, ["old:true", "old:false", "new:true", "new:commit", "new:false"])
    }

    func testSidebarResizeCancelsThumbDragEvenBeforeLayoutPass() {
        for layoutBeforeRelease in [false, true] {
            let slider = PlayerSeekSlider()
            slider.frame = CGRect(x: 0, y: 0, width: 818, height: 44)
            var commits = 0
            var editing: [Bool] = []
            slider.configure(PlayerSeekBar(value: 12, duration: 100, bufferedTime: 60, mediaID: "a",
                onPreview: { _ in }, onEditingChanged: { editing.append($0) }, onCommit: { _ in commits += 1 }))
            XCTAssertTrue(slider.beginInteraction(at: 409))
            slider.frame.size.width = 418
            if layoutBeforeRelease { slider.layoutIfNeeded() }
            slider.finishInteraction()
            XCTAssertEqual(commits, 0, "The same finger position must not jump from 50% to the end after a resize")
            XCTAssertEqual(editing, [true, false])
            XCTAssertEqual(slider.value, 0.12, accuracy: 0.001)
        }
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
}
