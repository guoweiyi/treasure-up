import AVFoundation
import Observation

/// Playback telemetry is deliberately separate from transport state. Only the
/// small waiting badge observes it; a network update never invalidates the
/// player, its controls, comments or queue.
@MainActor @Observable
final class PlaybackTransferMonitor {
    private(set) var status: PlaybackTransferStatus = .measuring
    @ObservationIgnored private weak var item: AVPlayerItem?
    @ObservationIgnored private var metricsTask: Task<Void, Never>?
    @ObservationIgnored private var generation = UUID()
    @ObservationIgnored private var meter = PlaybackTransferMeter()
    @ObservationIgnored private var lastPoll = -Double.infinity
    @ObservationIgnored private var isLocal = false

    func start(item: AVPlayerItem) {
        stop()
        self.item = item
        isLocal = (item.asset as? AVURLAsset)?.url.isFileURL == true
        if isLocal { status = .local; return }
        let key = generation
        // Subscribe before installing the item. HLS exposes resource metrics on
        // iOS 18+, and newer systems also expose progressive download metrics.
        // Access-log deltas below cover items without resource events.
        let events = item.allMetrics()
        metricsTask = Task { [weak self] in
            do {
                for try await event in events {
                    guard !Task.isCancelled, let self, self.generation == key else { return }
                    self.receive(event)
                }
            } catch {
                // Telemetry must never interrupt playback or start a retry.
            }
        }
    }

    func stop() {
        generation = UUID()
        metricsTask?.cancel()
        metricsTask = nil
        item = nil
        meter = PlaybackTransferMeter()
        isLocal = false
        lastPoll = -Double.infinity
        status = .measuring
    }

    /// Called only while waiting feedback is on screen. Inline/fullscreen hosts
    /// may briefly coexist; this gate still permits at most one poll per second.
    func refresh(now: Date = Date(), uptime: TimeInterval = ProcessInfo.processInfo.systemUptime) {
        guard uptime - lastPoll >= 0.9 else { return }
        lastPoll = uptime
        guard !isLocal else { status = .local; return }
        if let events = item?.accessLog()?.events, let event = events.last {
            meter.recordAccess(.init(eventCount: events.count, start: event.playbackStartDate,
                                     bytes: event.numberOfBytesTransferred, duration: event.transferDuration), at: now)
        }
        let next = meter.status(at: now)
        if status != next { status = next }
    }

    private func receive(_ event: AVMetricEvent) {
        let resource: AVMetricMediaResourceRequestEvent?
        switch event {
        case let segment as AVMetricHLSMediaSegmentRequestEvent: resource = segment.mediaResourceRequestEvent
        case let playlist as AVMetricHLSPlaylistRequestEvent: resource = playlist.mediaResourceRequestEvent
        case let request as AVMetricMediaResourceRequestEvent: resource = request
        default: return
        }
        guard let resource, !resource.wasReadFromCache,
              let transactions = resource.networkTransactionMetrics?.transactionMetrics else { return }
        let now = Date()
        for transaction in transactions where transaction.resourceFetchType == .networkLoad {
            guard let start = transaction.responseStartDate, let end = transaction.responseEndDate else { continue }
            meter.recordNetwork(.init(bytes: transaction.countOfResponseBodyBytesReceived,
                                      start: start, end: end), at: now)
        }
    }

    deinit { metricsTask?.cancel() }
}

enum PlaybackTransferStatus: Equatable {
    case measuring, waiting, local, rate(Double)

    var label: String {
        switch self {
        case .measuring: appPrompt("正在测速")
        case .waiting: appPrompt("等待数据")
        case .local: appPrompt("本地媒体")
        case .rate(let bytes): Self.format(bytesPerSecond: bytes)
        }
    }

    static func format(bytesPerSecond value: Double) -> String {
        guard value.isFinite, value >= 0 else { return appPrompt("正在测速") }
        // Decimal byte units, not media bitrate (bits/s).
        if value >= 1_000_000_000 { return String(format: "%.1f GB/s", value / 1_000_000_000) }
        if value >= 1_000_000 { return String(format: "%.1f MB/s", value / 1_000_000) }
        if value >= 1_000 { return String(format: "%.1f KB/s", value / 1_000) }
        return String(format: "%.0f B/s", value)
    }
}

/// No URLs, cookies or identifiers leave AVFoundation. Keep only a bounded set
/// of recent byte/time samples, excluding cache reads and duplicate events.
struct PlaybackTransferMeter {
    struct NetworkSample: Equatable {
        let bytes: Int64
        let start: Date
        let end: Date
    }
    struct AccessSnapshot {
        let eventCount: Int
        let start: Date?
        let bytes: Int64
        let duration: TimeInterval
    }
    private var samples: [NetworkSample] = []
    private var previousAccess: (snapshot: AccessSnapshot, date: Date)?
    private var accessRate: (value: Double, date: Date)?
    private var hasMeasured = false
    private let freshness: TimeInterval = 5

    mutating func recordNetwork(_ sample: NetworkSample, at now: Date) {
        let elapsed = sample.end.timeIntervalSince(sample.start)
        // A long progressive request's lifetime average is not its current
        // speed. Access-log deltas can still measure that transfer as it runs.
        guard sample.bytes > 0, elapsed.isFinite, elapsed > 0, elapsed <= freshness,
              now.timeIntervalSince(sample.end) >= -1, now.timeIntervalSince(sample.end) <= freshness else { return }
        let cutoff = now.addingTimeInterval(-freshness)
        samples.removeAll { $0.end < cutoff }
        guard !samples.contains(sample) else { return }
        samples.append(sample)
        if samples.count > 64 { samples.removeFirst(samples.count - 64) }
        hasMeasured = true
    }

    mutating func recordAccess(_ snapshot: AccessSnapshot, at now: Date) {
        defer { previousAccess = (snapshot, now) }
        guard let previous = previousAccess,
              previous.snapshot.eventCount == snapshot.eventCount,
              previous.snapshot.start == snapshot.start,
              snapshot.bytes >= 0, previous.snapshot.bytes >= 0,
              snapshot.duration.isFinite, previous.snapshot.duration.isFinite,
              snapshot.duration >= 0, previous.snapshot.duration >= 0,
              snapshot.bytes >= previous.snapshot.bytes,
              snapshot.duration >= previous.snapshot.duration else {
            accessRate = nil
            return
        }
        // A newly shown badge must not treat hours of accumulated access logs
        // as current speed. Establish a fresh baseline after every polling gap.
        let wallTime = now.timeIntervalSince(previous.date)
        guard wallTime > 0, wallTime <= freshness else { accessRate = nil; return }
        let bytes = snapshot.bytes - previous.snapshot.bytes
        let elapsed = snapshot.duration - previous.snapshot.duration
        guard bytes > 0, elapsed > 0 else { return }
        let rate = Double(bytes) / elapsed
        guard rate.isFinite, rate > 0 else { return }
        accessRate = (rate, now)
        hasMeasured = true
    }

    func status(at now: Date) -> PlaybackTransferStatus {
        let recent = samples.filter { now.timeIntervalSince($0.end) >= -1 && now.timeIntervalSince($0.end) <= freshness }
            .sorted { $0.start < $1.start }
        // Audio/video requests may overlap. Sum received bytes but count each
        // instant only once, so parallel downloads don't halve the shown rate.
        var duration: TimeInterval = 0
        var end: Date?
        var bytes: Double = 0
        for sample in recent {
            let start = max(sample.start, end ?? sample.start)
            duration += max(0, sample.end.timeIntervalSince(start))
            end = max(end ?? sample.end, sample.end)
            bytes += Double(sample.bytes)
        }
        if duration > 0, (bytes / duration).isFinite { return .rate(bytes / duration) }
        if let accessRate, now.timeIntervalSince(accessRate.date) >= 0,
           now.timeIntervalSince(accessRate.date) <= freshness { return .rate(accessRate.value) }
        return hasMeasured ? .waiting : .measuring
    }
}
