#if DEBUG
import SwiftUI
import AVFoundation
import Observation

/// Opt-in simulator fixture. API traffic is intercepted and video is generated
/// inside the app's temporary directory; no configured server or login is used.
struct OfflinePlayerUITestFixture: View {
    static var isEnabled: Bool {
        #if targetEnvironment(simulator)
        ProcessInfo.processInfo.arguments.contains("--offline-player-ui")
        #else
        false
        #endif
    }

    @State private var state = OfflinePlayerUIState()
    @State private var columns: NavigationSplitViewVisibility = .all
    @State private var expanded = false
    @State private var lifetime = VideoPageLifetime()

    var body: some View {
        Group {
            if UIDevice.current.userInterfaceIdiom == .pad {
                NavigationSplitView(columnVisibility: $columns) {
                    List {
                        Text("离线侧栏").accessibilityIdentifier("offline-sidebar")
                    }
                    .navigationTitle("离线测试")
                    .navigationSplitViewColumnWidth(min: 220, ideal: 250, max: 320)
                } detail: {
                    NavigationStack { playerPage }
                }
                .navigationSplitViewStyle(.balanced)
            } else {
                NavigationStack { playerPage }
            }
        }
        .environment(state.api)
        .environment(state.playback)
        .task { await state.prepare() }
    }

    private var playerPage: some View {
        VideoDetailViewport(aspectRatio: state.portrait ? 9.0 / 16.0 : 16.0 / 9.0) {
            InlineNativePlayer(coordinator: state.playback, onToggleExpanded: { expanded = true })
        } details: {
            VStack(alignment: .leading, spacing: 12) {
                Text("本地生成的视频").font(.headline)
                Text(state.ready ? "ready" : state.failure ?? "preparing")
                    .accessibilityIdentifier("offline-player-ready")
                Text(state.playback.isPlaying ? "playing" : "paused")
                    .accessibilityIdentifier("offline-player-state")
                Text(String(format: "%.2f", state.playback.currentTime))
                    .accessibilityIdentifier("offline-player-time")
                Text(state.playback.isSeeking ? "seeking" : "settled")
                    .accessibilityIdentifier("offline-player-seek-state")
                Text(String(format: "%.2f", state.playback.player.currentTime().seconds))
                    .accessibilityIdentifier("offline-player-actual-time")
                Text(String(format: "%.2f", state.playback.preferredRate))
                    .accessibilityIdentifier("offline-player-rate")
                Text(UIDevice.current.userInterfaceIdiom == .pad ? "iPad" : "iPhone")
                    .accessibilityIdentifier("offline-device-kind")
                Text(state.portrait ? "portrait" : "landscape")
                    .accessibilityIdentifier("offline-media-orientation")
                Spacer()
            }
            .padding()
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .accessibilityElement(children: .contain)
        }
        // An identifier on an ungrouped SwiftUI container can propagate to
        // descendants and replace the fixture status/control identifiers.
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("offline-detail-viewport")
        .navigationTitle("离线播放器回归")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if UIDevice.current.userInterfaceIdiom == .pad {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("切换侧栏") { columns = columns == .detailOnly ? .all : .detailOnly }
                        .accessibilityIdentifier("offline-sidebar-toggle")
                }
            }
        }
        .background {
            FullscreenPlayerPresenter(isPresented: $expanded, playback: state.playback, lifetime: lifetime)
                .frame(width: 0, height: 0)
        }
    }
}

@MainActor @Observable
private final class OfflinePlayerUIState {
    let portrait = ProcessInfo.processInfo.arguments.contains("--offline-player-portrait")
    let api: APIClient
    let playback: PlaybackCoordinator
    private(set) var ready = false
    private(set) var failure: String?
    private var started = false

    init() {
        let defaults = UserDefaults(suiteName: "TreasureOfflinePlayerUI.\(UUID().uuidString)")!
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [OfflinePlayerUIURLProtocol.self]
        let client = APIClient(baseURL: URL(string: "https://offline-player.invalid")!, defaults: defaults,
                               sessionConfiguration: configuration, persistSession: false)
        api = client
        playback = PlaybackCoordinator(api: client, defaults: defaults)
        playback.backgroundPlayback = false
        playback.autoAdvance = false
    }

    func prepare() async {
        guard !started else { return }
        started = true
        do {
            let url = try await Self.makeMovie(portrait: portrait)
            let video = ArchiveVideo(id: "offline-ui-video", title: "离线触控样本", playable: true,
                parts: [VideoPart(id: "offline-ui-part", position: 1, duration: 120,
                    variants: [MediaVariant(id: "offline-ui-source", width: portrait ? 90 : 160,
                                            height: portrait ? 160 : 90)])])
            // Establish normal page/queue state through the public coordinator.
            // The intentionally rejected session request never leaves URLProtocol.
            await playback.start(video: video)
            playback.errorMessage = nil
            let item = AVPlayerItem(url: url)
            playback.player.replaceCurrentItem(with: item)
            let deadline = ContinuousClock.now.advanced(by: .seconds(15))
            while item.status == .unknown && ContinuousClock.now < deadline {
                try await Task.sleep(for: .milliseconds(20))
            }
            guard item.status == .readyToPlay else { throw item.error ?? OfflinePlayerUIError.mediaNotReady }
            playback.resume()
            while !playback.isPlaying && ContinuousClock.now < deadline {
                try await Task.sleep(for: .milliseconds(20))
            }
            guard playback.isPlaying else { throw OfflinePlayerUIError.mediaNotReady }
            // Prove the local item actually plays, then wait paused for XCTest
            // to attach. Automation startup must not race the normal 3.5s
            // controls timeout before its first real transport interaction.
            playback.pause()
            while playback.isPlaying && ContinuousClock.now < deadline {
                try await Task.sleep(for: .milliseconds(20))
            }
            guard !playback.isPlaying else { throw OfflinePlayerUIError.mediaNotReady }
            ready = true
        } catch {
            failure = "fixture-error: \(error.localizedDescription)"
        }
    }

    /// Three local keyframes form a 120-second static video. The sparse sample
    /// timestamps keep the fixture tiny while testing an actual AVPlayer item.
    private static func makeMovie(portrait: Bool) async throws -> URL {
        let width = portrait ? 90 : 160
        let height = portrait ? 160 : 90
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("offline-player-\(UUID().uuidString).mov")
        let writer = try AVAssetWriter(outputURL: url, fileType: .mov)
        let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
            AVVideoCodecKey: AVVideoCodecType.h264,
            AVVideoWidthKey: width, AVVideoHeightKey: height,
            AVVideoCompressionPropertiesKey: [AVVideoMaxKeyFrameIntervalKey: 1]
        ])
        input.expectsMediaDataInRealTime = false
        let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input,
            sourcePixelBufferAttributes: [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
                                         kCVPixelBufferWidthKey as String: width,
                                         kCVPixelBufferHeightKey as String: height])
        guard writer.canAdd(input) else { throw OfflinePlayerUIError.mediaNotReady }
        writer.add(input)
        guard writer.startWriting() else { throw writer.error ?? OfflinePlayerUIError.mediaNotReady }
        writer.startSession(atSourceTime: .zero)
        var buffer: CVPixelBuffer?
        guard CVPixelBufferCreate(kCFAllocatorDefault, width, height, kCVPixelFormatType_32BGRA,
                                  nil, &buffer) == kCVReturnSuccess, let buffer else {
            throw OfflinePlayerUIError.mediaNotReady
        }
        CVPixelBufferLockBaseAddress(buffer, [])
        if let pixels = CVPixelBufferGetBaseAddress(buffer)?.assumingMemoryBound(to: UInt32.self) {
            pixels.initialize(repeating: 0xFF304060, count: CVPixelBufferGetBytesPerRow(buffer) * height / 4)
        }
        CVPixelBufferUnlockBaseAddress(buffer, [])
        for seconds in [0, 1, 119] {
            let deadline = ContinuousClock.now.advanced(by: .seconds(10))
            while !input.isReadyForMoreMediaData && writer.status == .writing && ContinuousClock.now < deadline {
                try await Task.sleep(for: .milliseconds(10))
            }
            guard input.isReadyForMoreMediaData,
                  adaptor.append(buffer, withPresentationTime: CMTime(seconds: Double(seconds), preferredTimescale: 600)) else {
                throw writer.error ?? OfflinePlayerUIError.mediaNotReady
            }
        }
        writer.endSession(atSourceTime: CMTime(seconds: 120, preferredTimescale: 600))
        input.markAsFinished()
        await writer.finishWriting()
        guard writer.status == .completed else { throw writer.error ?? OfflinePlayerUIError.mediaNotReady }
        return url
    }
}

private enum OfflinePlayerUIError: Error { case mediaNotReady }

private final class OfflinePlayerUIURLProtocol: URLProtocol, @unchecked Sendable {
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let response = HTTPURLResponse(url: request.url!, statusCode: 503, httpVersion: "HTTP/1.1",
                                       headerFields: ["Content-Type": "application/json"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data("{}".utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() { }
}
#endif
