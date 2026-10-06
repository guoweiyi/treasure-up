import XCTest
import UIKit
@testable import TreasureUp

@MainActor
final class NativeOverlayPerformanceTests: XCTestCase {
    // 100,000 accepted cues, with eight occupied lanes per batch. This exercises
    // lookup deep into a large archive instead of benchmarking eight input rows.
    private static let largeArchive: [ScheduledDanmaku] = {
        let json = (0..<100_000).map { index in
            "{\"text\":\"离线弹幕 \(index)\",\"time\":\(index / 8 * 9),\"mode\":\(index % 8 < 6 ? 0 : 2)}"
        }.joined(separator: ",")
        let cues = try! JSONDecoder().decode([NativeDanmakuCue].self, from: Data("[\(json)]".utf8))
        return PlaybackTimeline.scheduleDanmaku(cues)
    }()

    func testLargeArchiveKeepsEightGlyphsAndPreparesEachOnlyOnceAcross120Frames() {
        let archive = Self.largeArchive
        XCTAssertEqual(archive.count, 100_000)
        let view = NativeDanmakuOverlayView(frame: CGRect(x: 0, y: 0, width: 640, height: 360))
        let start = 45_000.0
        var originalLayers: [ObjectIdentifier] = []
        var firstX = CGFloat.zero
        for frame in 0..<120 {
            render(view, archive: archive, time: start + Double(frame) / 30)
            XCTAssertEqual(view.cachedCueCount, 8)
            let layers = view.layer.sublayers ?? []
            if frame == 0 {
                originalLayers = layers.map(ObjectIdentifier.init)
                firstX = layers[0].position.x
            } else {
                XCTAssertEqual(layers.map(ObjectIdentifier.init), originalLayers)
            }
        }
        XCTAssertEqual(view.textPreparationCount, 8,
                       "120 frames × eight cues previously repeated 960 text resolutions/measurements")
        XCTAssertLessThan(view.layer.sublayers![0].position.x, firstX)
        XCTAssertTrue(view.layer.sublayers!.allSatisfy { $0.shouldRasterize && $0.animationKeys() == nil })
        XCTAssertTrue(view.accessibilityElementsHidden)
        XCTAssertFalse(view.isUserInteractionEnabled)

        render(view, archive: archive, time: start + 4.1)
        XCTAssertEqual(view.cachedCueCount, 6, "Expired fixed-position cues must release their backing stores")
        render(view, archive: archive, time: start + 8.1)
        XCTAssertEqual(view.cachedCueCount, 0)
        XCTAssertTrue(view.layer.sublayers?.isEmpty ?? true)
        render(view, archive: archive, time: start + 0.5)
        XCTAssertEqual(view.cachedCueCount, 8, "Seeking back restores cues without retaining the whole archive")
        XCTAssertEqual(view.textPreparationCount, 16)
        render(view, archive: archive, time: start + 9.5)
        XCTAssertEqual(view.cachedCueCount, 8, "A new batch must replace expired layers, never accumulate them")
        XCTAssertEqual(view.textPreparationCount, 24)
    }

    func testGlyphInvalidationCoversFontScaleResizeOpacityAndReducedMotion() {
        let view = NativeDanmakuOverlayView(frame: CGRect(x: 0, y: 0, width: 640, height: 360))
        view.traitOverrides.displayScale = 2
        view.updateTraitsIfNeeded()
        let archive = Self.largeArchive
        render(view, archive: archive, time: 0.5)
        XCTAssertEqual(view.textPreparationCount, 8)
        render(view, archive: archive, time: 0.5, opacity: 0.4, reduceMotion: true)
        let stationaryPositions = view.layer.sublayers!.map(\.position)
        render(view, archive: archive, time: 1.5, opacity: 0.4, reduceMotion: true)
        XCTAssertEqual(view.layer.sublayers!.map(\.position), stationaryPositions)
        XCTAssertEqual(view.textPreparationCount, 8, "Opacity composites the cached glyph; reduced motion only changes positions")
        XCTAssertTrue(view.layer.sublayers!.allSatisfy { abs($0.opacity - 0.4) < 0.001 })

        view.frame.size.width = 500
        render(view, archive: archive, time: 1.5, reduceMotion: true)
        XCTAssertNotEqual(view.layer.sublayers!.map(\.position), stationaryPositions)
        XCTAssertEqual(view.textPreparationCount, 8, "Width-only changes must reuse glyph measurements")
        render(view, archive: archive, time: 1.5, fontSize: 24)
        XCTAssertEqual(view.textPreparationCount, 16)
        view.frame.size.height = 180
        render(view, archive: archive, time: 1.5, fontSize: 24)
        XCTAssertEqual(view.textPreparationCount, 24, "Compact viewports change the effective lane font")
        view.traitOverrides.displayScale = 3
        view.updateTraitsIfNeeded()
        render(view, archive: archive, time: 1.5, fontSize: 24)
        XCTAssertEqual(view.textPreparationCount, 32)
        XCTAssertTrue(view.layer.sublayers!.allSatisfy { $0.contentsScale == 3 && $0.rasterizationScale == 3 })
        view.frame.size.height = 100
        render(view, archive: archive, time: 1.5)
        XCTAssertEqual(view.cachedCueCount, 0)
    }

    func testLargeSubtitleTrackMatchesCueBoundariesAndBackwardSeeks() {
        let cues = (0..<100_000).map { index in
            NativeSubtitleCue(start: Double(index) * 3, end: Double(index) * 3 + 2,
                              text: "字幕 \(index)")
        }
        for index in [0, 50_000, 99_999, 3] {
            let time = Double(index) * 3
            XCTAssertEqual(PlaybackTimeline.subtitleText(cues, at: time), "字幕 \(index)")
            XCTAssertEqual(PlaybackTimeline.subtitleText(cues, at: time + 1.999), "字幕 \(index)")
            XCTAssertEqual(PlaybackTimeline.subtitleText(cues, at: time + 2), "")
        }
    }

    func testNewTrackReusingCueIDReplacesItsCachedTextAndColor() throws {
        let view = NativeDanmakuOverlayView(frame: CGRect(x: 0, y: 0, width: 640, height: 360))
        let first = try JSONDecoder().decode([NativeDanmakuCue].self,
            from: Data("[{\"text\":\"原弹幕\",\"time\":0,\"color\":16777215}]".utf8))
        let replacement = try JSONDecoder().decode([NativeDanmakuCue].self,
            from: Data("[{\"text\":\"新弹幕\",\"time\":0,\"color\":16711680}]".utf8))
        render(view, archive: PlaybackTimeline.scheduleDanmaku(first), time: 1)
        let originalLayer = try XCTUnwrap(view.layer.sublayers?.first as? CATextLayer)
        render(view, archive: PlaybackTimeline.scheduleDanmaku(replacement), time: 1)
        let refreshedLayer = try XCTUnwrap(view.layer.sublayers?.first as? CATextLayer)
        XCTAssertTrue(originalLayer === refreshedLayer)
        XCTAssertEqual(view.cachedCueCount, 1)
        XCTAssertEqual(view.textPreparationCount, 2)
        let text = try XCTUnwrap(refreshedLayer.string as? NSAttributedString)
        XCTAssertEqual(text.string, "新弹幕")
        XCTAssertEqual(text.attribute(.foregroundColor, at: 0, effectiveRange: nil) as? UIColor, .red)
    }

    func testNativeClockReusesItsLinkPausesAndReleasesAfterLeavingTheWindow() {
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 1024, height: 768))
        let view = NativeTimedOverlayView(frame: CGRect(x: 0, y: 0, width: 640, height: 360))
        window.addSubview(view)
        let archive = Self.largeArchive
        view.configure(danmaku: archive, subtitles: [], time: 0.5, playing: true, timeProvider: { 0.5 })
        XCTAssertTrue(view.isAnimationRunning)
        XCTAssertEqual(view.animationFrameRate, 60)
        XCTAssertEqual(view.displayLinkCreationCount, 1)
        for _ in 0..<30 {
            view.configure(danmaku: archive, subtitles: [], time: 0.5, playing: false, timeProvider: { 0.5 })
            XCTAssertFalse(view.isAnimationRunning, "Paused/background overlays must stop animation work")
            view.configure(danmaku: archive, subtitles: [], time: 0.5, playing: true, timeProvider: { 0.5 })
        }
        XCTAssertEqual(view.displayLinkCreationCount, 1, "Transport toggles reuse the clock instead of rescheduling links")
        view.configure(danmaku: archive, subtitles: [], time: 0.5, reduceMotion: true,
                       playing: true, timeProvider: { 0.5 })
        XCTAssertEqual(view.animationFrameRate, 10)
        view.configure(danmaku: [], subtitles: [], time: 0.5, playing: true, timeProvider: { 0.5 })
        XCTAssertFalse(view.isAnimationRunning)
        XCTAssertEqual(view.danmakuView.cachedCueCount, 0)
        view.removeFromSuperview()
        XCTAssertFalse(view.isAnimationRunning)
        XCTAssertEqual(view.animationFrameRate, 0, "Offscreen overlays invalidate their run-loop registration")
    }

    func testNativeFramesKeepSubtitleLayoutAndDanmakuGlyphsCached() {
        let view = NativeTimedOverlayView(frame: CGRect(x: 0, y: 0, width: 1000, height: 560))
        let subtitles = [NativeSubtitleCue(start: 0, end: 2.5, text: "原生字幕\nNative subtitle"),
                         NativeSubtitleCue(start: 3, end: 4, text: "下一句")]
        view.configure(danmaku: Self.largeArchive, subtitles: subtitles, time: 0,
                       playing: false, timeProvider: { 0 })
        view.layoutIfNeeded()
        let hostTime = CACurrentMediaTime() + 1
        for frame in 0..<120 {
            view.renderFrame(time: Double(frame) / 60, hostTime: hostTime + Double(frame) / 60)
        }
        XCTAssertEqual(view.danmakuView.textPreparationCount, 8)
        XCTAssertEqual(view.subtitleAssignmentCount, 1, "A steady subtitle must not reassign text/layout at 60 Hz")
        XCTAssertEqual(view.displayedSubtitleText, "原生字幕\nNative subtitle")
        view.renderFrame(time: 2.5, hostTime: hostTime + 3)
        XCTAssertEqual(view.displayedSubtitleText, "")
        view.renderFrame(time: 3.5, hostTime: hostTime + 4)
        XCTAssertEqual(view.displayedSubtitleText, "下一句")
        view.configure(danmaku: [], subtitles: subtitles, time: 1,
                       playing: false, timeProvider: { 1 })
        XCTAssertEqual(view.displayedSubtitleText, "原生字幕\nNative subtitle", "A paused backward seek updates immediately")
        view.configure(danmaku: [], subtitles: [NativeSubtitleCue(start: 0, end: 2.5, text: "替换字幕")],
                       time: 1, playing: false, timeProvider: { 1 })
        XCTAssertEqual(view.displayedSubtitleText, "替换字幕", "Switching tracks with identical timestamps replaces text")
        view.stop()
        XCTAssertEqual(view.displayedSubtitleText, "")
        XCTAssertFalse(view.isAnimationRunning)
    }

    func testDisplayLinkTargetDoesNotRetainDetachedOverlay() {
        weak var releasedView: NativeTimedOverlayView?
        autoreleasepool {
            let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 1024, height: 768))
            let view = NativeTimedOverlayView(frame: window.bounds)
            releasedView = view
            window.addSubview(view)
            view.configure(danmaku: Self.largeArchive, subtitles: [], time: 0.5,
                           playing: true, timeProvider: { 0.5 })
            XCTAssertTrue(view.isAnimationRunning)
            view.removeFromSuperview()
            XCTAssertFalse(view.isAnimationRunning)
            XCTAssertEqual(view.animationFrameRate, 0, "Leaving the window removes the run-loop registration")
        }
        // UIKit may autorelease a recently detached view. The assertion belongs
        // outside that pool, while still rejecting any persistent link cycle.
        XCTAssertNil(releasedView)
    }

    func testSubtitleFramesAvoidFullscreenCutoutsAndTransportDuringResize() throws {
        let view = NativeTimedOverlayView(frame: CGRect(x: 0, y: 0, width: 852, height: 393))
        let cue = NativeSubtitleCue(start: 0, end: 10,
            text: "这是一段用于验证宽度约束与多行排版的离线字幕，需要在刘海和底部播放控件以内显示。")
        let landscape = UIEdgeInsets(top: 0, left: 59, bottom: 21, right: 20)
        view.configure(danmaku: [], subtitles: [cue], time: 1, controlsVisible: true,
                       safeAreaInsets: landscape, playing: false, timeProvider: { 1 })
        view.layoutIfNeeded()
        let label = try XCTUnwrap(view.subviews.flatMap(\.subviews).compactMap { $0 as? UILabel }.first)
        func assertInside(_ insets: UIEdgeInsets, controls: Bool, file: StaticString = #filePath, line: UInt = #line) {
            let actual = label.convert(label.bounds, to: view)
            let bottomReservation = controls ? max(3, insets.bottom) + 88 : insets.bottom
            XCTAssertFalse(actual.isEmpty, file: file, line: line)
            XCTAssertGreaterThanOrEqual(actual.minX, insets.left + 24, file: file, line: line)
            XCTAssertLessThanOrEqual(actual.maxX, view.bounds.width - insets.right - 24, file: file, line: line)
            XCTAssertGreaterThanOrEqual(actual.minY, insets.top, file: file, line: line)
            XCTAssertLessThanOrEqual(actual.maxY, view.bounds.height - bottomReservation - 8, file: file, line: line)
        }
        assertInside(landscape, controls: true)
        let assignments = view.subtitleAssignmentCount
        let portrait = UIEdgeInsets(top: 59, left: 0, bottom: 34, right: 0)
        view.frame.size = CGSize(width: 393, height: 852)
        view.configure(danmaku: [], subtitles: [cue], time: 1, controlsVisible: true,
                       safeAreaInsets: portrait, playing: false, timeProvider: { 1 })
        view.layoutIfNeeded()
        assertInside(portrait, controls: true)
        view.configure(danmaku: [], subtitles: [cue], time: 1, controlsVisible: false,
                       safeAreaInsets: portrait, playing: false, timeProvider: { 1 })
        view.layoutIfNeeded()
        assertInside(portrait, controls: false)
        XCTAssertEqual(view.subtitleAssignmentCount, assignments, "Resizing updates constraints without reassigning identical subtitle text")
    }

    func testInlineSubtitleDoesNotApplyAncestorSafeAreaTwiceAfterReturningFromFullscreen() throws {
        let inherited = UIEdgeInsets(top: 106, left: 24, bottom: 34, right: 24)
        let inline = NativeSubtitleLayout.safeAreaInsets(isExpanded: false, reported: inherited)
        XCTAssertEqual(inline, .zero)
        XCTAssertEqual(NativeSubtitleLayout.safeAreaInsets(isExpanded: true, reported: inherited), inherited)
        let view = NativeTimedOverlayView(frame: CGRect(x: 0, y: 0, width: 852, height: 393))
        let cues = [NativeSubtitleCue(start: 0, end: 10, text: "内联字幕")]
        view.configure(danmaku: [], subtitles: cues, time: 1, controlsVisible: true,
                       safeAreaInsets: UIEdgeInsets(top: 0, left: 59, bottom: 21, right: 59),
                       playing: false, timeProvider: { 1 })
        view.layoutIfNeeded()
        view.frame.size = CGSize(width: 393, height: 221)
        view.configure(danmaku: [], subtitles: cues, time: 1, controlsVisible: true,
                       safeAreaInsets: inline, playing: false, timeProvider: { 1 })
        view.layoutIfNeeded()
        let label = try XCTUnwrap(view.subviews.flatMap(\.subviews).compactMap { $0 as? UILabel }.first)
        let actual = label.convert(label.bounds, to: view)
        XCTAssertGreaterThan(actual.minY, 50)
        XCTAssertLessThanOrEqual(actual.maxY, 221 - 91 - 8)
        XCTAssertGreaterThan(actual.maxY, 90, "Ancestor bottom padding must not be applied a second time")
        XCTAssertGreaterThan(actual.width, 0)
    }

    func testCachedLargeArchiveFrameUpdatesPerformance() {
        let archive = Self.largeArchive
        let view = NativeDanmakuOverlayView(frame: CGRect(x: 0, y: 0, width: 640, height: 360))
        render(view, archive: archive, time: 45_000)
        let options = XCTMeasureOptions()
        options.iterationCount = 5
        // CPU lookup and layer-property submission only. This intentionally
        // makes no claim about GPU frame rate or real-device playback.
        measure(metrics: [XCTClockMetric()], options: options) {
            for frame in 0..<120 {
                render(view, archive: archive, time: 45_000 + Double(frame) / 30)
            }
        }
        XCTAssertEqual(view.textPreparationCount, 8)
    }

    private func render(_ view: NativeDanmakuOverlayView, archive: [ScheduledDanmaku], time: Double,
                        fontSize: Double = 18, opacity: Double = 0.85, reduceMotion: Bool = false) {
        view.update(rows: PlaybackTimeline.activeDanmaku(archive, at: time), time: time,
                    fontSize: fontSize, opacity: opacity, reduceMotion: reduceMotion)
    }
}
