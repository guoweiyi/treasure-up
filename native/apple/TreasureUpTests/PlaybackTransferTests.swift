import XCTest
import AVFoundation
@testable import TreasureUp

final class PlaybackTransferTests: XCTestCase {
    private let origin = Date(timeIntervalSince1970: 1_000)

    func testDisplayUsesBytesAndDecimalUnitsWithoutInventingUnknownRates() {
        XCTAssertEqual(PlaybackTransferStatus.format(bytesPerSecond: 1_200_000), "1.2 MB/s")
        XCTAssertEqual(PlaybackTransferStatus.format(bytesPerSecond: 120_000), "120.0 KB/s")
        XCTAssertEqual(PlaybackTransferStatus.format(bytesPerSecond: 0), "0 B/s")
        XCTAssertEqual(PlaybackTransferStatus.format(bytesPerSecond: 1_500_000_000), "1.5 GB/s")
        for invalid in [Double.nan, .infinity, -1] {
            XCTAssertEqual(PlaybackTransferStatus.format(bytesPerSecond: invalid), appPrompt("正在测速"))
        }
    }

    func testConcurrentAudioAndVideoUseCombinedBytesAndUnionOfTransferTime() {
        var meter = PlaybackTransferMeter()
        let video = PlaybackTransferMeter.NetworkSample(bytes: 2_000_000, start: origin, end: origin.addingTimeInterval(2))
        let audio = PlaybackTransferMeter.NetworkSample(bytes: 400_000, start: origin.addingTimeInterval(1), end: origin.addingTimeInterval(2))
        meter.recordNetwork(video, at: origin.addingTimeInterval(2))
        meter.recordNetwork(audio, at: origin.addingTimeInterval(2))
        meter.recordNetwork(video, at: origin.addingTimeInterval(2))
        XCTAssertEqual(meter.status(at: origin.addingTimeInterval(2)), .rate(1_200_000), "Wrapped HLS and resource events must not count twice")
        XCTAssertEqual(meter.status(at: origin.addingTimeInterval(8)), .waiting, "Expired samples cannot pretend a stalled connection is downloading")
    }

    func testDisjointTransfersExcludeIdleTimeAndRejectUnknownOrStaleSamples() {
        var meter = PlaybackTransferMeter()
        meter.recordNetwork(.init(bytes: 1_000_000, start: origin, end: origin.addingTimeInterval(1)), at: origin.addingTimeInterval(1))
        meter.recordNetwork(.init(bytes: 1_000_000, start: origin.addingTimeInterval(3), end: origin.addingTimeInterval(4)), at: origin.addingTimeInterval(4))
        meter.recordNetwork(.init(bytes: 900_000_000, start: origin, end: origin), at: origin)
        meter.recordNetwork(.init(bytes: -1, start: origin, end: origin.addingTimeInterval(1)), at: origin)
        meter.recordNetwork(.init(bytes: 900_000_000, start: origin.addingTimeInterval(-10), end: origin.addingTimeInterval(-9)), at: origin)
        XCTAssertEqual(meter.status(at: origin.addingTimeInterval(4)), .rate(1_000_000))
    }

    func testAccessLogsUseDeltasAndResetBaselineAfterNewEventOrLongPollingGap() {
        var meter = PlaybackTransferMeter()
        func snapshot(_ bytes: Int64, _ duration: Double, event: Int = 1) -> PlaybackTransferMeter.AccessSnapshot {
            .init(eventCount: event, start: origin, bytes: bytes, duration: duration)
        }
        meter.recordAccess(snapshot(10_000_000, 10), at: origin)
        XCTAssertEqual(meter.status(at: origin), .measuring, "A historical average is not live transfer speed")
        meter.recordAccess(snapshot(11_200_000, 11), at: origin.addingTimeInterval(1))
        XCTAssertEqual(meter.status(at: origin.addingTimeInterval(1)), .rate(1_200_000))
        meter.recordAccess(snapshot(11_200_000, 11), at: origin.addingTimeInterval(2))
        XCTAssertEqual(meter.status(at: origin.addingTimeInterval(7)), .waiting)
        meter.recordAccess(snapshot(90_000_000, 12), at: origin.addingTimeInterval(100))
        XCTAssertEqual(meter.status(at: origin.addingTimeInterval(100)), .waiting)
        meter.recordAccess(snapshot(1_000_000, 1, event: 2), at: origin.addingTimeInterval(101))
        XCTAssertEqual(meter.status(at: origin.addingTimeInterval(101)), .waiting)
        meter.recordAccess(snapshot(2_200_000, 2, event: 2), at: origin.addingTimeInterval(102))
        XCTAssertEqual(meter.status(at: origin.addingTimeInterval(102)), .rate(1_200_000))
    }

    func testInvalidAndResetAccessCountersNeverProduceSpikes() {
        var meter = PlaybackTransferMeter()
        func record(_ bytes: Int64, _ duration: Double, _ time: Double) {
            meter.recordAccess(.init(eventCount: 1, start: origin, bytes: bytes, duration: duration), at: origin.addingTimeInterval(time))
        }
        record(-1, -1, 0)
        record(100, 1, 1)
        XCTAssertEqual(meter.status(at: origin.addingTimeInterval(1)), .measuring)
        record(0, 0, 2)
        record(100, .nan, 3)
        record(100, 1, 4)
        record(1_200_100, 2, 5)
        XCTAssertEqual(meter.status(at: origin.addingTimeInterval(5)), .rate(1_200_000))
        record(10, 0, 6)
        XCTAssertEqual(meter.status(at: origin.addingTimeInterval(6)), .waiting)
    }

    func testCompletedLongFileRequestDoesNotReplaceRecentAccessLogRate() {
        var meter = PlaybackTransferMeter()
        meter.recordAccess(.init(eventCount: 1, start: origin, bytes: 100, duration: 1), at: origin)
        meter.recordAccess(.init(eventCount: 1, start: origin, bytes: 1_200_100, duration: 2), at: origin.addingTimeInterval(1))
        meter.recordNetwork(.init(bytes: 600_000_000, start: origin.addingTimeInterval(-59),
                                  end: origin.addingTimeInterval(1)), at: origin.addingTimeInterval(1))
        XCTAssertEqual(meter.status(at: origin.addingTimeInterval(1)), .rate(1_200_000))
    }

    @MainActor
    func testLocalItemsAndStopNeverCarryNetworkSpeedIntoNextPlayback() {
        let monitor = PlaybackTransferMonitor()
        let item = AVPlayerItem(url: URL(fileURLWithPath: "/offline-fixture.mp4"))
        monitor.start(item: item)
        XCTAssertEqual(monitor.status, .local)
        monitor.refresh(now: origin, uptime: 1)
        XCTAssertEqual(monitor.status, .local)
        monitor.stop()
        XCTAssertEqual(monitor.status, .measuring)
        monitor.refresh(now: origin, uptime: 2)
        XCTAssertEqual(monitor.status, .measuring)
    }
}
