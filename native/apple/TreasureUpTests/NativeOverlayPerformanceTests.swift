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
