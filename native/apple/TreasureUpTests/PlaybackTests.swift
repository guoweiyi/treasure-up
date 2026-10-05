import XCTest
import AVFoundation
@testable import TreasureUp

final class PlaybackTests: XCTestCase {
    @MainActor
    func testPlayerKVOCallbacksCanArriveFromBackgroundThreads() async {
        let client = APIClient(baseURL: URL(string: "https://playback.example.test")!, persistSession: false)
        let coordinator = PlaybackCoordinator(api: client)
        let player = coordinator.player
        // AVFoundation can deliver KVO on its media/network queues. Emitting real
        // KVO notifications here catches an inherited MainActor entry thunk before
        // a callback gets the chance to enqueue its inner MainActor Task.
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            DispatchQueue.global().async {
                Self.emitPlaybackKVO(player)
                continuation.resume()
            }
        }
        await Task.yield()
        XCTAssertFalse(coordinator.isPlaying)
        XCTAssertEqual(coordinator.currentTime, 0)
        coordinator.stop()
    }

    private nonisolated static func emitPlaybackKVO(_ player: AVPlayer) {
        for key in ["rate", "timeControlStatus"] {
            player.willChangeValue(forKey: key)
            player.didChangeValue(forKey: key)
        }
    }

    func testResumeRejectsCompletedInvalidAndOutOfBoundsPositions() {
        XCTAssertEqual(PlaybackTimeline.resumePosition(42, duration: 90), 42)
        for position in [Double.nan, .infinity, -2, 0, 87, 90, 200] {
            XCTAssertEqual(PlaybackTimeline.resumePosition(position, duration: 90), 0)
        }
        XCTAssertEqual(PlaybackTimeline.resumePosition(1, duration: .nan), 0)
        XCTAssertEqual(PlaybackTimeline.resumePosition(1, duration: 2), 0)
    }

    func testWebVTTHandlesCueIDsSettingsEscapesAndCRLF() {
        let text = "WEBVTT\r\n\r\ncue-a\r\n00:00:01.000 --> 00:00:03.500 align:center\r\n<b>你好</b> &amp; world\r\n第二行\r\n\r\n00:04.000 --> 00:05.000\r\n下一句\r\n\r\n00:99.000 --> 00:02.000\r\nInvalid"
        let cues = PlaybackTimeline.subtitles(text)
        XCTAssertEqual(cues.count, 2)
        XCTAssertEqual(cues[0].start, 1)
        XCTAssertEqual(cues[0].end, 3.5)
        XCTAssertEqual(cues[0].text, "你好 & world\n第二行")
        XCTAssertEqual(PlaybackTimeline.subtitleText(cues, at: 0), "")
        XCTAssertEqual(PlaybackTimeline.subtitleText(cues, at: 1), "你好 & world\n第二行")
        XCTAssertEqual(PlaybackTimeline.subtitleText(cues, at: 3.5), "")
        XCTAssertEqual(PlaybackTimeline.subtitleText(cues, at: 4.5), "下一句")
        XCTAssertEqual(PlaybackTimeline.subtitleText(cues, at: .infinity), "")
    }

    func testSubtitleTimestampValidationAndOverlappingBilingualCues() {
        XCTAssertEqual(PlaybackTimeline.timestamp("01:02:03.500"), 3_723.5)
        XCTAssertEqual(PlaybackTimeline.timestamp("02:03,500"), 123.5)
        for invalid in ["00:60.000", "00:99:00", "-1:00:00", "nonsense", "NaN:00:00"] {
            XCTAssertNil(PlaybackTimeline.timestamp(invalid))
        }
        let cues = PlaybackTimeline.subtitles("00:01.000 --> 00:04.000\n中文\n\n00:02.000 --> 00:03.000\nEnglish")
        XCTAssertEqual(PlaybackTimeline.subtitleText(cues, at: 2.5), "中文\nEnglish")
        XCTAssertEqual(PlaybackTimeline.subtitleText(cues, at: 3.5), "中文")
    }

    func testDanmakuColorsDensityAndSeekWindow() throws {
        let rows = try JSONDecoder().decode([NativeDanmakuCue].self, from: Data("""
        [{"text":"红","time":1,"mode":0,"color":16711680},
         {"text":"白","time":1,"mode":0,"color":"#fff"},
         {"text":"绿","time":1,"mode":0,"color":"#00ff00"},
         {"text":"四","time":1,"mode":0,"color":-4},
         {"text":"五","time":1,"mode":0,"color":"invalid"},
         {"text":"六","time":1,"mode":0,"color":16777299},
         {"text":"过密不重叠","time":1,"mode":0},
         {"text":"较晚","time":12,"mode":0},
         {"text":"非法模式","time":1,"mode":9}]
        """.utf8))
        XCTAssertEqual(rows[0].color, 0xFF0000)
        XCTAssertEqual(rows[1].color, 0xFFFFFF)
        XCTAssertEqual(rows[2].color, 0x00FF00)
        XCTAssertEqual(rows[3].color, 0)
        XCTAssertEqual(rows[4].color, 0xFFFFFF)
        XCTAssertEqual(rows[5].color, 0xFFFFFF)
        let scheduled = PlaybackTimeline.scheduleDanmaku(rows)
        XCTAssertEqual(scheduled.count, 7)
        XCTAssertEqual(Set(scheduled.prefix(6).map(\.lane)).count, 6)
        XCTAssertEqual(PlaybackTimeline.activeDanmaku(scheduled, at: 5).count, 6)
        XCTAssertEqual(PlaybackTimeline.activeDanmaku(scheduled, at: 11).count, 0)
        XCTAssertEqual(PlaybackTimeline.activeDanmaku(scheduled, at: 13).first?.cue.text, "较晚")
        XCTAssertEqual(PlaybackTimeline.activeDanmaku(scheduled, at: 5).count, 6, "Seeking backwards should restore the appropriate cues")
    }

    func testTransportFallbackPreservesSourceAndIsBounded() {
        var recovery = NativePlaybackRecovery()
        XCTAssertEqual(recovery.nextAction(isNetworkFailure: false, transport: "hls", sourceID: "original", selectedSourceID: "original", availableSources: ["original", "aac-copy"]), .useFile(sourceID: "original"))
        XCTAssertEqual(recovery.nextAction(isNetworkFailure: false, transport: "hls", sourceID: "original", selectedSourceID: "original", availableSources: ["original"]), .stop)
        XCTAssertEqual(recovery.nextAction(isNetworkFailure: true, transport: "file", sourceID: "original", selectedSourceID: "original", availableSources: ["original"]), .renewAddress)
        XCTAssertEqual(recovery.nextAction(isNetworkFailure: true, transport: "file", sourceID: "original", selectedSourceID: "original", availableSources: ["original"]), .stop)
    }

    func testRecoveryNeverSwitchesOriginalsOrInventsAACFallback() {
        for scenario in [("hls", "other", "selected", Set(["other", "selected"])),
                         ("hls", "missing", "missing", Set(["aac-copy"])),
                         ("file", "original", "original", Set(["original"]))] {
            var recovery = NativePlaybackRecovery()
            XCTAssertEqual(recovery.nextAction(isNetworkFailure: false, transport: scenario.0, sourceID: scenario.1,
                                               selectedSourceID: scenario.2, availableSources: scenario.3), .stop)
        }
    }

    func testNetworkErrorsIncludeNestedAVFoundationFailuresAndExpiry() {
        let nested = NSError(domain: "AVFoundationErrorDomain", code: -11800,
                             userInfo: [NSUnderlyingErrorKey: URLError(.networkConnectionLost)])
        XCTAssertTrue(NativePlaybackRecovery.isNetworkError(nested))
        XCTAssertTrue(NativePlaybackRecovery.isNetworkError(nil, httpStatus: 403))
        XCTAssertTrue(NativePlaybackRecovery.isNetworkError(nil, httpStatus: 503))
        XCTAssertFalse(NativePlaybackRecovery.isNetworkError(NSError(domain: "AVFoundationErrorDomain", code: -11821)))
    }
}
