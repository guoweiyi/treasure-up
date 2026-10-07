import Foundation
import Observation
import AVFoundation
import MediaPlayer
import UIKit

@MainActor @Observable
final class PlaybackCoordinator {
    let player: AVPlayer
    let fullscreenPresentation = FullscreenPresentationState()
    private(set) var currentVideo: ArchiveVideo?
    private(set) var currentPart: VideoPart?
    private(set) var currentVariant: MediaVariant?
    private(set) var session: PlaybackSession?
    private let playbackQueue = PlaybackQueue()
    var queue: [ArchiveVideo] { playbackQueue.items }
    var queueIndex: Int? { playbackQueue.currentIndex }
    var queueTitle: String { playbackQueue.title }
    var queueLoading: Bool { playbackQueue.isLoading }
    var queueError: String? { queueActionError ?? playbackQueue.errorMessage }
    private var queueActionError: String?
    private var diagnosticEvents: [String] = []
    var diagnosticsText: String {
        (["Treasure Up · 本地播放诊断", "系统：\(UIDevice.current.systemName) \(UIDevice.current.systemVersion) · \(UIDevice.current.model)",
          "仅含本次 App 进程最近 80 个事件；不包含媒体 URL、认证信息、用户名或密码。",
          "这些事件帮助定位播放生命周期，不代表已确认音爆原因。", ""] + diagnosticEvents).joined(separator: "\n")
    }
    private(set) var queueTransitioning = false
    private(set) var isLoading = false
    private(set) var isPlaying = false
    /// Requested transport state, updated synchronously even while AVPlayer is
    /// loading or buffering. Controls must not infer this from delayed KVO.
    private(set) var wantsPlayback = false
    private(set) var isBuffering = false
    private(set) var isSeeking = false
    private(set) var currentTime: Double = 0
    private(set) var duration: Double = 0
    private(set) var bufferedTime: Double = 0
    private(set) var outputRoute = "系统音频输出"
    private(set) var renderingStatus = "当前输出未报告空间渲染模式"
    private(set) var hdrEligible = AVPlayer.eligibleForHDRPlayback
    private(set) var audioOptions: [NativeMediaOption] = []
    private(set) var embeddedSubtitleOptions: [NativeMediaOption] = []
    private(set) var selectedAudio = "auto"
    private(set) var selectedSubtitle = "auto"
    private(set) var danmaku: [ScheduledDanmaku] = []
    private(set) var subtitleCues: [NativeSubtitleCue] = []
    private(set) var progressWarning: String?
    private(set) var ancillaryWarning: String?
    private(set) var deliveryDescription = "准备播放"
    var errorMessage: String?
    var statusMessage: String?
    var requestsPresentation = false
    var isPictureInPictureActive = false
    var autoAdvance = true { didSet { if oldValue != autoAdvance { saveQueuePreferences() } } }
    var queueMode = PlaybackQueueMode.pause { didSet { if oldValue != queueMode { saveQueuePreferences() } } }
    var danmakuEnabled = true { didSet { if oldValue != danmakuEnabled { defaults.set(danmakuEnabled, forKey: "treasure.native.playback.danmaku") } } }
    var danmakuOpacity = 0.85 { didSet { if oldValue != danmakuOpacity { defaults.set(danmakuOpacity, forKey: "treasure.native.playback.danmakuOpacity") } } }
    var danmakuFontSize = 18.0 { didSet { if oldValue != danmakuFontSize { defaults.set(danmakuFontSize, forKey: "treasure.native.playback.danmakuFontSize") } } }
    var loudnessBalance = false {
        didSet {
            guard oldValue != loudnessBalance else { return }
            defaults.set(loudnessBalance, forKey: "treasure.native.playback.loudnessBalance"); applyLoudness()
        }
    }
    var selectedRouteID = ""
    var preferredRate: Float = 1
    private(set) var temporaryRate: Float?
    var effectiveRate: Float { temporaryRate ?? preferredRate }
    var fitToFill = false { didSet { if oldValue != fitToFill { defaults.set(fitToFill, forKey: "treasure.native.playback.fitToFill") } } }
    var backgroundPlayback = true {
        didSet {
            guard oldValue != backgroundPlayback else { return }
            player.audiovisualBackgroundPlaybackPolicy = backgroundPlayback ? .continuesIfPossible : .pauses
            defaults.set(backgroundPlayback, forKey: "treasure.native.playback.background")
        }
    }

    @ObservationIgnored var presentation: NativePlaybackPresentation?
    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private let api: APIClient
    @ObservationIgnored private var generation = UUID()
    @ObservationIgnored private var activeItemGeneration: UUID?
    @ObservationIgnored private var queueLoadTask: Task<Void, Never>?
    @ObservationIgnored private var navigationGeneration = UUID()
    @ObservationIgnored private var queueIdentity = ""
    @ObservationIgnored private var restoringQueuePreferences = false
    @ObservationIgnored private var endGuard = PlaybackEndGuard()
    @ObservationIgnored private var pendingQueueRetry: (id: String, direction: Int, automatic: Bool)?
    @ObservationIgnored private var playbackIdentityRevision: Int?
    @ObservationIgnored private var saveProgressTask: Task<Void, Never>?
    @ObservationIgnored private var itemObservation: NSKeyValueObservation?
    @ObservationIgnored private var controlObservation: NSKeyValueObservation?
    @ObservationIgnored private var rateObservation: NSKeyValueObservation?
    @ObservationIgnored private var notifications: [NSObjectProtocol] = []
    @ObservationIgnored private var remoteTargets: [(MPRemoteCommand, Any)] = []
    @ObservationIgnored private var timeObserver: Any?
    @ObservationIgnored private var ancillaryTask: Task<Void, Never>?
    @ObservationIgnored private var progressTask: Task<Void, Never>?
    @ObservationIgnored private var renewalTask: Task<Void, Never>?
    @ObservationIgnored private var startupTimeoutTask: Task<Void, Never>?
    @ObservationIgnored private var stallTask: Task<Void, Never>?
    @ObservationIgnored private var audioGroup: AVMediaSelectionGroup?
    @ObservationIgnored private var subtitleGroup: AVMediaSelectionGroup?
    @ObservationIgnored private var startupPosition = PlaybackStartupPosition()
    @ObservationIgnored private var historyReadPending = false
    @ObservationIgnored private var requestedPartID: String?
    @ObservationIgnored private var seekTask: Task<Void, Never>?
    @ObservationIgnored private var inFlightSeek: PlaybackSeekAttempt?
    @ObservationIgnored private var playbackIntentRevision: UInt = 0
    @ObservationIgnored private var initialResumeApplied = false
    @ObservationIgnored private var lastProgressSave = Date.distantPast
    @ObservationIgnored private var settledPosition = 0.0
    @ObservationIgnored private var recovery = NativePlaybackRecovery()
    @ObservationIgnored private var selectedTransport = "auto"
    @ObservationIgnored private var wasPlayingBeforeInterruption = false
    @ObservationIgnored private var expectedTransportState: Bool?
    @ObservationIgnored private var playbackAnchor = Date()
    @ObservationIgnored private var anchorTime = 0.0
    @ObservationIgnored private var anchorRate: Float = 0
    @ObservationIgnored private var currentArtwork: MPMediaItemArtwork?
    @ObservationIgnored private var artworkTask: Task<Void, Never>?
    @ObservationIgnored private var lastNowPlayingUpdate = Date.distantPast
    @ObservationIgnored private var lastMediaRangeRead = Date.distantPast
    @ObservationIgnored private var nowPlayingPublication = PlaybackNowPlayingPublication()

    init(api: APIClient, defaults: UserDefaults = .standard, player: AVPlayer = AVPlayer()) {
        self.api = api
        self.defaults = defaults
        self.player = player
        danmakuEnabled = defaults.object(forKey: "treasure.native.playback.danmaku") as? Bool ?? api.defaultDanmaku
        danmakuOpacity = defaults.object(forKey: "treasure.native.playback.danmakuOpacity") as? Double ?? 0.85
        danmakuFontSize = defaults.object(forKey: "treasure.native.playback.danmakuFontSize") as? Double ?? 18
        loudnessBalance = defaults.bool(forKey: "treasure.native.playback.loudnessBalance")
        fitToFill = defaults.bool(forKey: "treasure.native.playback.fitToFill")
        backgroundPlayback = defaults.object(forKey: "treasure.native.playback.background") as? Bool ?? true
        preferredRate = min(2, max(0.5, defaults.object(forKey: "treasure.native.playback.rate") as? Float ?? 1))
        player.allowsExternalPlayback = true
        player.automaticallyWaitsToMinimizeStalling = true
        player.audiovisualBackgroundPlaybackPolicy = backgroundPlayback ? .continuesIfPossible : .pauses
        player.appliesMediaSelectionCriteriaAutomatically = true
        installObservers()
        installRemoteCommands()
        updateAudioRoute()
        loadQueuePreferences()
    }

    var playableParts: [VideoPart] { PlaybackQueue.playableParts(currentVideo?.parts ?? []) }
    var variants: [MediaVariant] { currentPart?.variants.filter { $0.kind != "hls" } ?? [] }
    var hasNext: Bool { hasAdjacentItem(direction: 1) || playbackQueue.hasUnloadedItems }
    var hasPrevious: Bool { hasAdjacentItem(direction: -1) }

    private func hasAdjacentItem(direction: Int) -> Bool {
        Self.hasAdjacentItem(parts: currentVideo?.parts ?? [], partID: currentPart?.id,
                             videos: queue, videoID: currentVideo?.id, direction: direction)
    }

    nonisolated static func hasAdjacentItem(parts: [VideoPart], partID: String?, videos: [ArchiveVideo], videoID: String?, direction: Int) -> Bool {
        let parts = PlaybackQueue.playableParts(parts)
        if let index = parts.firstIndex(where: { $0.id == partID }), parts.indices.contains(index + direction) { return true }
        // Identical to the manual queue target rule, without allocating an ID
        // array for the whole queue just to locate the current video.
        guard let id = videoID, let index = videos.firstIndex(where: { $0.id == id }) else { return false }
        return videos.indices.contains(index + direction)
    }
    var availableSubtitles: [SubtitleTrack] { session?.subtitles ?? [] }
    var sourceFeatures: [String] {
        guard let media = session?.media else { return [] }
        var result: [String] = []
        if media.dolbyVision == true { result.append("Dolby Vision") }
        else if media.hdr == true { result.append("HDR") }
        if media.dolbyAtmos == true { result.append("Dolby Atmos 原档") }
        else if let audio = media.audioCodec { result.append(audio.uppercased()) }
        if let channels = media.audioChannels, channels > 0 { result.append("\(channels) 声道") }
        return result
    }
    var compatibleAudioVariant: MediaVariant? {
        guard let currentVariant, currentVariant.kind == "archive" else { return nil }
        return variants.first { variant in
            let media = currentVideo?.mediaProperties[variant.id] ?? variant.metadata
            return variant.kind == "playback" && media?.sourceVariantId == currentVariant.id &&
                media?.compatibilityMode == "audio_only" && media?.videoStreamCopy == true &&
                media?.audioTranscoded == true && media?.audioCodec == "aac" &&
                media?.audioChannels == 2 && media?.dolbyAtmos == false
        }
    }

    func play(video: ArchiveVideo, part: VideoPart? = nil, variant: MediaVariant? = nil,
              autoplay: Bool = true, initialSeek: Double? = nil) async {
        guard !Task.isCancelled else { return }
        if let initialSeek, !initialSeek.isFinite || initialSeek < 0 { return }
        ensureQueueIdentity()
        if await resumeCurrentSelection(videoID: video.id, partID: part?.id, variantID: variant?.id,
                                        autoplay: autoplay, initialSeek: initialSeek) { return }
        navigationGeneration = UUID()
        queueTransitioning = false
        playbackQueue.select(video)
        await beginPlayback(video: video, part: part, variant: variant, resumeHistory: true,
                            autoplay: autoplay, initialSeek: initialSeek)
    }

    private func beginPlayback(video: ArchiveVideo, part: VideoPart? = nil, variant: MediaVariant? = nil,
                               resumeHistory: Bool, autoplay: Bool = true, initialSeek: Double? = nil) async {
        persistProgress()
        generation = UUID()
        activeItemGeneration = nil
        let key = generation
        cancelMediaTasks()
        player.pause()
        player.replaceCurrentItem(with: nil)
        temporaryRate = nil
        playbackIdentityRevision = api.sessionRevision
        currentVideo = video
        requestedPartID = part?.id
        currentPart = nil
        currentVariant = nil
        session = nil
        currentArtwork = nil
        errorMessage = nil
        statusMessage = nil
        progressWarning = nil
        ancillaryWarning = nil
        danmaku = []
        subtitleCues = []
        audioOptions = []
        embeddedSubtitleOptions = []
        selectedAudio = "auto"
        selectedSubtitle = "auto"
        currentTime = 0
        settledPosition = 0
        duration = 0
        bufferedTime = 0
        lastMediaRangeRead = .distantPast
        isBuffering = false
        if defaults.object(forKey: "treasure.native.playback.danmaku") == nil { danmakuEnabled = api.defaultDanmaku }
        selectedRouteID = ""
        selectedTransport = "auto"
        startupPosition = PlaybackStartupPosition(resumeHistory: resumeHistory)
        if let initialSeek { startupPosition.requestSeek(initialSeek) }
        historyReadPending = false
        recovery = NativePlaybackRecovery()
        initialResumeApplied = false
        wantsPlayback = autoplay
        expectedTransportState = autoplay
        isLoading = true
        recordDiagnostic("play-request")
        updateNowPlaying()
        do {
            let full: ArchiveVideo
            if video.parts.isEmpty { full = try await api.get("/videos/\(video.id)") }
            else { full = video }
            guard generation == key else { return }
            guard let selectedPart = part.flatMap({ chosen in full.parts.first(where: { $0.id == chosen.id && !$0.variants.isEmpty }) })
                    ?? PlaybackQueue.playableParts(full.parts).first else {
                throw NativePlaybackError.noMedia
            }
            currentVideo = full
            playbackQueue.select(full)
            currentPart = selectedPart
            currentVariant = variant.flatMap { chosen in selectedPart.variants.first { $0.id == chosen.id } }
                ?? selectedPart.variants.first(where: { $0.kind == "archive" })
                ?? selectedPart.variants.first(where: { $0.kind == "playback" })
            duration = selectedPart.duration
            currentTime = startupPosition.resolved(duration: duration)
            recordDiagnostic("source-selected")
            if resumeHistory && startupPosition.acceptsHistory && api.user != nil {
                historyReadPending = true
                progressTask = Task { [weak self] in
                    guard let self else { return }
                    defer { if self.generation == key { self.historyReadPending = false } }
                    do {
                        let saved: NativeWatchProgress = try await self.api.get("/progress/\(selectedPart.id)")
                        guard self.generation == key else { return }
                        self.startupPosition.acceptHistory(saved.position, duration: selectedPart.duration > 0 ? selectedPart.duration : saved.duration)
                    } catch { /* History is optional and has a bounded startup window. */ }
                }
            }
            try configureAudioSession()
            await createSession(key: key)
            guard generation == key, !Task.isCancelled else { return }
            loadArtwork(video: full, key: key)
        } catch {
            guard generation == key else { return }
            isLoading = false
            wantsPlayback = false
            expectedTransportState = nil
            errorMessage = error.localizedDescription
        }
    }

    func stop(clearQueue: Bool = false, saveProgress: Bool = true) {
        recordDiagnostic("stop")
        if saveProgress { persistProgress() }
        navigationGeneration = UUID()
        queueTransitioning = false
        wantsPlayback = false
        expectedTransportState = nil
        wasPlayingBeforeInterruption = false
        requestsPresentation = false
        queueLoadTask?.cancel()
        if clearQueue {
            queueLoadTask = nil
            playbackQueue.clear()
            queueActionError = nil
            pendingQueueRetry = nil
        }
        playbackIdentityRevision = nil
        generation = UUID()
        activeItemGeneration = nil
        cancelMediaTasks()
        player.pause()
        temporaryRate = nil
        itemObservation = nil
        player.replaceCurrentItem(with: nil)
        currentVideo = nil
        presentation?.stopPlaybackPresentation()
        requestedPartID = nil
        historyReadPending = false
        currentPart = nil
        currentVariant = nil
        session = nil
        currentTime = 0
        settledPosition = 0
        duration = 0
        isLoading = false
        isPlaying = false
        isBuffering = false
        danmaku = []
        subtitleCues = []
        currentArtwork = nil
        nowPlayingPublication = PlaybackNowPlayingPublication()
        MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    func resetForIdentityChange() {
        stop(clearQueue: true, saveProgress: false)
        diagnosticEvents.removeAll()
        recordDiagnostic("identity-reset")
        loadQueuePreferences()
    }

    func togglePlayback() { wantsPlayback ? pause() : resume() }

    func pause() {
        playbackIntentRevision &+= 1
        wantsPlayback = false
        expectedTransportState = false
        wasPlayingBeforeInterruption = false
        isBuffering = false
        player.pause()
        endTemporaryRate()
        recordDiagnostic("pause")
        persistProgress()
        updateNowPlaying()
    }

    func resume() {
        guard currentVideo != nil else { return }
        playbackIntentRevision &+= 1
        do {
            try configureAudioSession()
            wantsPlayback = true
            expectedTransportState = true
            // Startup owns the initial seek. An ordinary or replay seek owns
            // transport until the most recent requested position has settled.
            guard !isLoading else { return }
            if PlaybackStartupPosition.shouldReplay(position: currentTime, duration: duration), player.currentItem != nil {
                updateSeekIntent(to: 0)
                currentTime = 0
                endGuard.rearm(generation)
                recordDiagnostic("replay-request")
                beginPendingSeek()
            } else if seekTask == nil {
                player.playImmediately(atRate: effectiveRate)
                recordDiagnostic("resume-request")
            } else {
                isBuffering = true
            }
        } catch { wantsPlayback = false; expectedTransportState = nil; errorMessage = error.localizedDescription }
        updateNowPlaying()
    }

    func seek(to seconds: Double) {
        guard seconds.isFinite else { return }
        playbackIntentRevision &+= 1
        stallTask?.cancel()
        stallTask = nil
        let bounded = min(max(0, seconds), max(0, duration))
        if bounded < duration - 0.5 { endGuard.rearm(generation) }
        updateSeekIntent(to: bounded)
        currentTime = bounded
        if !isLoading { beginPendingSeek() }
        recordDiagnostic("seek-request")
        updateTimeAnchor()
        updateNowPlaying(forcePosition: true)
    }

    private func updateSeekIntent(to position: Double) {
        // Equal destinations at AVPlayer's requested time scale do not restart
        // a seek, but the first explicit gesture still closes history restore.
        if !startupPosition.hasExplicitSeek || !PlaybackSeekAttempt.sameDestination(startupPosition.position, position) {
            startupPosition.requestSeek(position)
        }
        guard var attempt = inFlightSeek, attempt.generation == generation,
              player.currentItem === attempt.item, !attempt.cancellationRequested,
              attempt.revision != startupPosition.revision,
              !PlaybackSeekAttempt.sameDestination(attempt.destination, position) else { return }
        // Mark before cancel: completion may be delivered immediately. Further
        // gestures only update the target until this attempt has unwound.
        attempt.cancellationRequested = true
        inFlightSeek = attempt
        attempt.item.cancelPendingSeeks()
    }

    /// At most one AVPlayer seek is active. New gestures only replace the
    /// destination; after completion the worker jumps straight to the latest.
    private func beginPendingSeek() {
        guard seekTask == nil, let item = player.currentItem else { return }
        let key = generation
        isSeeking = true
        isBuffering = wantsPlayback
        player.pause()
        seekTask = Task { [weak self, weak item] in
            guard let self, let item else { return }
            let destination = await self.settleLatestSeek(item: item, key: key)
            guard !Task.isCancelled, self.generation == key, self.player.currentItem === item else { return }
            self.seekTask = nil
            self.isSeeking = false
            self.applySettledPosition(destination)
            self.persistProgress()
            self.restoreTransportAfterSeek(item: item, key: key)
            self.updateTimeAnchor()
            self.updateNowPlaying(forcePosition: true)
        }
    }

    private func settleLatestSeek(item: AVPlayerItem, key: UUID) async -> Double? {
        while !Task.isCancelled, generation == key, player.currentItem === item {
            let revision = startupPosition.revision
            let destination = startupPosition.resolved(duration: duration)
            let attempt = PlaybackSeekAttempt(generation: key, item: item, revision: revision, destination: destination)
            inFlightSeek = attempt
            let finished = await player.seek(to: CMTime(seconds: destination, preferredTimescale: 600), toleranceBefore: .zero, toleranceAfter: .zero)
            if inFlightSeek?.id == attempt.id { inFlightSeek = nil }
            guard !Task.isCancelled, generation == key, player.currentItem === item else { return nil }
            if revision != startupPosition.revision { continue }
            // A canceled seek must not claim it reached its target. This also
            // keeps PiP/system seek interruptions from creating false progress.
            return finished ? destination : nil
        }
        return nil
    }

    private func applySettledPosition(_ destination: Double?) {
        if let destination {
            currentTime = destination
            if statusMessage == "进度跳转未完成，可以再次拖动进度条重试。" { statusMessage = nil }
        }
        else {
            let actual = player.currentTime().seconds
            currentTime = actual.isFinite ? min(duration, max(0, actual)) : 0
            statusMessage = "进度跳转未完成，可以再次拖动进度条重试。"
        }
        settledPosition = currentTime
    }

    private func restoreTransportAfterSeek(item: AVPlayerItem, key: UUID) {
        if PlaybackStartupPosition.shouldReplay(position: currentTime, duration: duration) {
            // Seeking to EOF may deliver its end notification while the seek is
            // still pending. Settle it here even if AVPlayer never repeats it.
            finishPlayback(item: item, key: key, advanceAutomatically: wantsPlayback)
        } else if wantsPlayback {
            expectedTransportState = true
            player.playImmediately(atRate: effectiveRate)
        }
        isBuffering = wantsPlayback && player.timeControlStatus == .waitingToPlayAtSpecifiedRate
    }

    private func finishPlayback(item: AVPlayerItem, key: UUID, advanceAutomatically: Bool) {
        guard generation == key, player.currentItem === item else { return }
        let intent = playbackIntentRevision
        wantsPlayback = false
        expectedTransportState = nil
        isPlaying = false
        isBuffering = false
        currentTime = duration
        settledPosition = duration
        // Consume paused seeks too: a delayed end notification must not turn a
        // user's paused scrub into autoplay. Resume or a backward seek rearms.
        let isFirstEnd = endGuard.consume(key)
        persistProgress()
        updateTimeAnchor()
        updateNowPlaying(forcePosition: true)
        guard isFirstEnd, advanceAutomatically else { return }
        recordDiagnostic("item-ended")
        Task { [weak self, weak item] in
            guard let self, let item, self.generation == key, self.player.currentItem === item,
                  self.playbackIntentRevision == intent else { return }
            await self.advance(direction: 1, automatic: true)
        }
    }

    func skip(_ seconds: Double) { seek(to: currentTime + seconds) }

    func setRate(_ rate: Float) {
        guard rate.isFinite else { return }
        let bounded = min(2, max(0.5, rate))
        guard bounded != preferredRate else { return }
        preferredRate = bounded
        player.defaultRate = preferredRate
        defaults.set(preferredRate, forKey: "treasure.native.playback.rate")
        if player.rate > 0 { player.rate = effectiveRate }
        updateTimeAnchor()
        updateNowPlaying()
    }

    func beginTemporaryRate(_ rate: Float = 2) {
        guard rate.isFinite, player.currentItem != nil else { return }
        temporaryRate = min(2, max(0.5, rate))
        if player.rate > 0 { player.rate = effectiveRate }
        updateTimeAnchor()
        updateNowPlaying()
    }

    func endTemporaryRate() {
        guard temporaryRate != nil else { return }
        temporaryRate = nil
        if player.rate > 0 { player.rate = preferredRate }
        updateTimeAnchor()
        updateNowPlaying()
    }

    func configureQueue(videos: [ArchiveVideo], currentVideoId: String?, title: String = "播放队列") {
        ensureQueueIdentity()
        cancelQueueNavigation()
        queueLoadTask?.cancel()
        queueLoadTask = nil
        playbackQueue.configure(videos: videos, currentVideoId: currentVideoId, title: title)
        updateNowPlaying()
    }

    /// The catalog supplies its exact search/filter/sort query. Loading starts at
    /// page 1, independent of the screen's currently loaded page or card count.
    func start(video: ArchiveVideo, part: VideoPart? = nil, context: PlaybackQueueContext? = nil,
               autoplay: Bool = true, initialSeek: Double? = nil) async {
        guard !Task.isCancelled else { return }
        ensureQueueIdentity()
        if let context {
            if playbackQueue.context != context || !queue.contains(where: { $0.id == video.id }) {
                queueLoadTask?.cancel()
                playbackQueue.configure(context: context, current: video)
                beginLoadingQueue()
            }
        } else if !queue.contains(where: { $0.id == video.id }) {
            configureQueue(videos: [video], currentVideoId: video.id)
        }
        if playbackQueue.hasUnloadedItems, queueLoadTask?.isCancelled == true { beginLoadingQueue() }
        await play(video: video, part: part, autoplay: autoplay, initialSeek: initialSeek)
    }

    func enqueue(video: ArchiveVideo) {
        ensureQueueIdentity()
        playbackQueue.enqueue(video)
        updateNowPlaying()
    }

    func removeFromQueue(id: String) {
        cancelQueueNavigation()
        // Removing the selected item stops that item; other entries remain
        // explicitly selectable, rather than silently playing something else.
        if currentVideo?.id == id { stop() }
        playbackQueue.remove(id: id)
        updateNowPlaying()
    }

    func moveQueueItems(fromOffsets offsets: IndexSet, toOffset destination: Int) {
        cancelQueueNavigation()
        playbackQueue.move(fromOffsets: offsets, toOffset: destination)
        updateNowPlaying()
    }

    func clearQueue() {
        stop(clearQueue: true)
        queueActionError = nil
        pendingQueueRetry = nil
    }

    func playQueueItem(id: String) async {
        guard !Task.isCancelled else { return }
        ensureQueueIdentity()
        guard queue.contains(where: { $0.id == id }) else { return }
        if await resumeCurrentSelection(videoID: id) { return }
        cancelQueueNavigation()
        await navigateQueue(direction: 1, startingAt: id, automatic: false)
    }

    /// Reopening the selected row means continue, including when its active part
    /// is not P1. Explicit queue transitions and loop playback bypass this path.
    private func resumeCurrentSelection(videoID: String, partID: String? = nil, variantID: String? = nil,
                                        autoplay: Bool = true, initialSeek: Double? = nil) async -> Bool {
        guard currentVideo?.id == videoID,
              partID == nil || partID == (currentPart?.id ?? requestedPartID),
              variantID == nil || variantID == currentVariant?.id else { return false }
        // A failed explicit seek starts a fresh generation carrying both intents;
        // retry() intentionally means play for the existing ordinary retry button.
        if errorMessage != nil && (initialSeek != nil || !autoplay) { return false }
        cancelQueueNavigation()
        if isLoading {
            wantsPlayback = autoplay; expectedTransportState = autoplay
            if !autoplay { player.pause() }
            if let initialSeek {
                let position = duration > 0 ? min(initialSeek, duration) : initialSeek
                updateSeekIntent(to: position)
                currentTime = position
            }
            updateNowPlaying()
            return true
        }
        if errorMessage != nil {
            if currentPart == nil {
                // Detail hydration previously failed; let play() retry it.
                return false
            }
            wantsPlayback = true
            await retry()
            return true
        }
        guard player.currentItem != nil else { return false }
        if let initialSeek {
            wantsPlayback = autoplay; expectedTransportState = autoplay
            if !autoplay { player.pause() }
            seek(to: initialSeek)
        } else if autoplay {
            if !wantsPlayback { resume() }
        } else { pause() }
        return true
    }

    func nextPart() async { await advance(direction: 1) }
    func previousPart() async { await advance(direction: -1) }
    func playNext() async { await nextPart() }

    func retryQueueLoading() async {
        guard !Task.isCancelled else { return }
        if playbackQueue.hasUnloadedItems {
            if queueLoadTask == nil || queueLoadTask?.isCancelled == true || !playbackQueue.isLoading { beginLoadingQueue() }
            await queueLoadTask?.value
        }
        guard !Task.isCancelled else { return }
        if let pending = pendingQueueRetry {
            await navigateQueue(direction: pending.direction, startingAt: pending.id, automatic: pending.automatic)
        }
    }

    private func beginLoadingQueue() {
        let revision = api.sessionRevision
        let previousLoad = queueLoadTask
        queueLoadTask = Task { [weak self] in
            // Let a canceled loader release its loading state before resuming
            // the same preserved queue; it must not suppress the replacement.
            if previousLoad?.isCancelled == true { await previousLoad?.value }
            guard let self, !Task.isCancelled else { return }
            await self.playbackQueue.loadAll { context, page in
                guard self.api.sessionRevision == revision else { throw APIError.staleSession }
                let response: Page<ArchiveVideo> = try await self.api.get(context.path, query: context.parameters(page: page))
                guard self.api.sessionRevision == revision else { throw APIError.staleSession }
                return response
            }
            self.updateNowPlaying()
        }
    }

    private func queueTarget(direction: Int, ended: Bool = false) -> PlaybackQueueTarget {
        PlaybackQueue.target(parts: currentVideo?.parts ?? [], partId: currentPart?.id,
                             videos: queue.map(\.id), videoId: currentVideo?.id,
                             direction: direction, ended: ended, mode: queueMode, autoAdvance: autoAdvance)
    }

    private func advance(direction: Int, automatic: Bool = false) async {
        guard !Task.isCancelled else { return }
        ensureQueueIdentity()
        guard !queueTransitioning else { return }
        let intent = playbackIntentRevision
        var target = queueTarget(direction: direction, ended: automatic)
        // A part transition never waits for unrelated catalog pages.
        if case .part = target { }
        else if case .replay = target { }
        else if playbackQueue.hasUnloadedItems && (!automatic || (autoAdvance && queueMode == .continuous)) {
            let key = generation
            let navigation = navigationGeneration
            queueTransitioning = true
            await retryQueueLoading()
            if navigationGeneration == navigation { queueTransitioning = false }
            guard generation == key, navigationGeneration == navigation,
                  !automatic || playbackIntentRevision == intent else { return }
            target = queueTarget(direction: direction, ended: automatic)
        }
        switch target {
        case .part(let id):
            guard let video = currentVideo, let part = playableParts.first(where: { $0.id == id }) else { return }
            cancelQueueNavigation()
            await beginPlayback(video: video, part: part, resumeHistory: !automatic)
        case .video(let id):
            await navigateQueue(direction: direction, startingAt: id, automatic: automatic)
        case .replay:
            // Explicitly restart: the periodic clock may have delivered one
            // final pre-EOF sample since the end notification was scheduled.
            seek(to: 0)
            resume()
        case .stop:
            if automatic { pause() }
            else if direction < 0 { seek(to: 0) }
        }
    }

    private func navigateQueue(direction: Int, startingAt id: String, automatic: Bool) async {
        guard !queueTransitioning else { return }
        let candidates = playbackQueue.candidates(direction: direction, startingAt: id)
        let navigation = UUID()
        navigationGeneration = navigation
        let identity = api.sessionRevision
        let intent = playbackIntentRevision
        queueTransitioning = true
        queueActionError = nil
        pendingQueueRetry = nil
        defer { if navigationGeneration == navigation { queueTransitioning = false } }
        var skipped = 0
        for summary in candidates {
            do {
                try Task.checkCancellation()
                let full: ArchiveVideo = summary.parts.isEmpty ? try await api.get("/videos/\(summary.id)") : summary
                guard navigationGeneration == navigation, api.sessionRevision == identity,
                      !automatic || playbackIntentRevision == intent else { return }
                let parts = PlaybackQueue.playableParts(full.parts)
                guard let part = direction < 0 ? parts.last : parts.first else { skipped += 1; continue }
                playbackQueue.select(full)
                await beginPlayback(video: full, part: part, resumeHistory: !automatic)
                if navigationGeneration == navigation, skipped > 0 { statusMessage = "已略过 \(skipped) 个暂无可播放归档的条目。" }
                return
            } catch {
                guard navigationGeneration == navigation, api.sessionRevision == identity, !Task.isCancelled else { return }
                if (error as? APIError)?.status == 404 { skipped += 1; continue }
                // Network/authorization failures are not evidence that a video
                // is unplayable: stop here and preserve the exact retry target.
                pendingQueueRetry = (summary.id, direction, automatic)
                queueActionError = "无法读取“\(summary.title)”：\(error.localizedDescription)"
                if automatic { pause() }
                return
            }
        }
        if skipped > 0 { queueActionError = "已检查后续 \(skipped) 个条目，暂时都没有可播放的归档。" }
        if automatic { pause() }
    }

    private func cancelQueueNavigation() {
        navigationGeneration = UUID()
        queueTransitioning = false
        pendingQueueRetry = nil
        queueActionError = nil
    }

    private func ensureQueueIdentity() {
        let expected = PlaybackQueuePreferences.key(server: api.baseURL, userId: api.user?.id)
        if expected != queueIdentity { resetForIdentityChange() }
    }

    private func loadQueuePreferences() {
        restoringQueuePreferences = true
        let preferences = PlaybackQueuePreferences.load(defaults: defaults, server: api.baseURL, userId: api.user?.id)
        queueMode = preferences.mode
        autoAdvance = preferences.autoAdvance
        queueIdentity = PlaybackQueuePreferences.key(server: api.baseURL, userId: api.user?.id)
        restoringQueuePreferences = false
    }

    private func saveQueuePreferences() {
        guard !restoringQueuePreferences,
              queueIdentity == PlaybackQueuePreferences.key(server: api.baseURL, userId: api.user?.id) else { return }
        PlaybackQueuePreferences(mode: queueMode, autoAdvance: autoAdvance)
            .save(defaults: defaults, server: api.baseURL, userId: api.user?.id)
    }

    func selectVariant(_ variant: MediaVariant) async {
        guard currentPart?.variants.contains(where: { $0.id == variant.id }) == true else { return }
        guard currentVariant?.id != variant.id || errorMessage != nil else { return }
        if errorMessage != nil { wantsPlayback = true }
        currentVariant = variant
        selectedTransport = "auto"
        recovery = NativePlaybackRecovery()
        await renew()
    }

    func selectRoute(_ id: String) async {
        guard selectedRouteID != id || errorMessage != nil else { return }
        if errorMessage != nil { wantsPlayback = true }
        selectedRouteID = id
        recovery.resetNetworkBudget()
        await renew()
    }

    func retry() async {
        guard currentVideo != nil else { return }
        wantsPlayback = true
        if currentPart == nil, let video = currentVideo {
            await play(video: video)
            return
        }
        recovery = NativePlaybackRecovery()
        await renew()
    }

    func renew() async {
        guard currentPart != nil else { return }
        recordDiagnostic("renew-request")
        let resumeAt = initialResumeApplied ? currentTime : startupPosition.position
        let shouldPlay = wantsPlayback
        generation = UUID()
        activeItemGeneration = nil
        let key = generation
        artworkTask?.cancel()
        artworkTask = nil
        seekTask?.cancel()
        seekTask = nil
        isSeeking = false
        inFlightSeek = nil
        player.currentItem?.cancelPendingSeeks()
        renewalTask?.cancel()
        startupTimeoutTask?.cancel()
        stallTask?.cancel()
        itemObservation = nil
        player.pause()
        startupPosition = PlaybackStartupPosition(resumeHistory: false, position: resumeAt)
        historyReadPending = false
        initialResumeApplied = false
        wantsPlayback = shouldPlay
        expectedTransportState = shouldPlay
        await createSession(key: key)
        if generation == key, !Task.isCancelled, currentArtwork == nil, let video = currentVideo {
            loadArtwork(video: video, key: key)
        }
    }

    func flushProgress() { persistProgress() }

    func presentationTime(at date: Date) -> Double {
        guard isPlaying, !isSeeking, !isLoading else { return currentTime }
        return min(duration, max(0, anchorTime + date.timeIntervalSince(playbackAnchor) * Double(anchorRate)))
    }

    func selectAudio(_ id: String) {
        selectedAudio = id
        guard let item = player.currentItem, let group = audioGroup else { return }
        if id == "auto" { item.selectMediaOptionAutomatically(in: group) }
        else if let index = Int(id), group.options.indices.contains(index) { item.select(group.options[index], in: group) }
        recordDiagnostic("audio-selection")
    }

    func selectSubtitle(_ id: String) async {
        selectedSubtitle = id
        subtitleCues = []
        if let item = player.currentItem, let group = subtitleGroup {
            if id == "auto" { item.selectMediaOptionAutomatically(in: group) }
            else if id.hasPrefix("embedded:"), let index = Int(id.dropFirst(9)), group.options.indices.contains(index) {
                item.select(group.options[index], in: group)
            } else { item.select(nil, in: group) }
        }
        guard id.hasPrefix("sidecar:"), let index = Int(id.dropFirst(8)), availableSubtitles.indices.contains(index) else { return }
        let key = generation
        let track = availableSubtitles[index]
        do {
            let data = try await api.data(track.url)
            guard generation == key, selectedSubtitle == id else { return }
            let cues = try await PlaybackSidecarDecoder.subtitles(data)
            guard generation == key, selectedSubtitle == id, !Task.isCancelled else { return }
            subtitleCues = cues
            if subtitleCues.isEmpty { ancillaryWarning = "这条字幕没有可显示的时间轴。" }
        } catch {
            guard generation == key, selectedSubtitle == id, !Task.isCancelled else { return }
            ancillaryWarning = "字幕加载失败：\(error.localizedDescription)"
        }
    }

    func variantLabel(_ variant: MediaVariant) -> String {
        let media = currentVideo?.mediaProperties[variant.id] ?? variant.metadata
        var result = variant.height.map { "\($0)p" } ?? "原始分辨率"
        if media?.dolbyVision == true { result += " · Dolby Vision" }
        else if media?.hdr == true { result += " · HDR" }
        if media?.dolbyAtmos == true { result += " · Atmos" }
        else if let codec = variant.audioCodec ?? media?.audioCodec { result += " · \(codec.uppercased())" }
        if media?.compatibilityMode == "audio_only" { result += " · 声音兼容版" }
        else { result += variant.kind == "archive" ? " · 原档" : " · 兼容版" }
        return result
    }

    private func createSession(key: UUID) async {
        guard let part = currentPart else { return }
        isLoading = true
        errorMessage = nil
        var body: [String: JSONValue] = ["part_id": .string(part.id), "protocol": .string(selectedTransport)]
        if let variant = currentVariant { body["variant_id"] = .string(variant.id) }
        if !selectedRouteID.isEmpty { body["route_id"] = .string(selectedRouteID) }
        do {
            let created: PlaybackSession = try await api.send("/playback-sessions", body: body)
            guard generation == key else { return }
            guard let url = api.resolveURL(created.url) else { throw NativePlaybackError.invalidURL }
            if let expected = currentVariant?.id,
               (created.sourceVariantId ?? created.variantId) != expected { throw NativePlaybackError.wrongSource }
            session = created
            applyLoudness()
            if let selected = part.variants.first(where: { $0.id == (created.sourceVariantId ?? created.variantId) }) {
                currentVariant = selected
            }
            recordDiagnostic("session-created")
            let route = created.routes.first(where: { $0.id == created.selectedRouteId })
            deliveryDescription = "\(created.protocol == "hls" ? "原生 HLS" : "原档直读") · \(route?.name ?? "自动节点")"
            let asset = AVURLAsset(url: url, options: [AVURLAssetHTTPCookiesKey: api.mediaCookies(for: url)])
            let item = AVPlayerItem(asset: asset)
            item.preferredForwardBufferDuration = 20
            // Preserve original video color metadata and compressed audio. No custom
            // audio mix, sample-buffer renderer, stereo downmix or forced SDR output.
            player.defaultRate = preferredRate
            itemObservation = item.observe(\.status, options: [.initial, .new]) { @Sendable [weak self, weak item] _, _ in
                Task { @MainActor in
                    guard let self, let item, self.generation == key else { return }
                    if item.status == .readyToPlay { await self.itemReady(item, key: key) }
                    else if item.status == .failed { await self.handleFailure(item.error, key: key) }
                }
            }
            activeItemGeneration = key
            player.replaceCurrentItem(with: item)
            startupTimeoutTask?.cancel()
            startupTimeoutTask = Task { [weak self] in
                do { try await Task.sleep(for: .seconds(30)) } catch { return }
                guard let self, self.generation == key, self.isLoading else { return }
                self.startupTimeoutTask = nil
                await self.handleFailure(NativePlaybackError.timeout, key: key)
            }
            scheduleRenewal(created, key: key)
            loadAncillary(created, key: key)
        } catch {
            guard generation == key else { return }
            isLoading = false
            wantsPlayback = false
            expectedTransportState = nil
            errorMessage = error.localizedDescription
        }
    }

    private func itemReady(_ item: AVPlayerItem, key: UUID) async {
        guard !initialResumeApplied, player.currentItem === item else { return }
        initialResumeApplied = true
        startupTimeoutTask?.cancel()
        recordDiagnostic("item-ready")
        let measured = item.duration.seconds
        if measured.isFinite, measured > 0 { duration = measured }
        // Media can become ready before the progress API responds. Allow a
        // short grace period; an explicit gesture immediately closes this window.
        let historyDeadline = ContinuousClock.now.advanced(by: .milliseconds(250))
        while historyReadPending && startupPosition.acceptsHistory && ContinuousClock.now < historyDeadline {
            do { try await Task.sleep(for: .milliseconds(10)) } catch { return }
            guard generation == key, player.currentItem === item else { return }
        }
        startupPosition.closeHistoryWindow()
        var resume: Double? = startupPosition.resolved(duration: duration)
        if (resume ?? 0) > 0 || startupPosition.hasExplicitSeek {
            isSeeking = true
            resume = await settleLatestSeek(item: item, key: key)
            guard !Task.isCancelled, generation == key, player.currentItem === item else { return }
            isSeeking = false
        }
        isLoading = false
        applySettledPosition(resume)
        if let resume, resume > 0 { recordDiagnostic("resume-seek-complete") }
        if startupPosition.hasExplicitSeek { persistProgress() }
        restoreTransportAfterSeek(item: item, key: key)
        if wantsPlayback { recordDiagnostic("playback-start-request") }
        updateNowPlaying()
        updateAudioRoute()
        let audible = try? await item.asset.loadMediaSelectionGroup(for: .audible)
        let legible = try? await item.asset.loadMediaSelectionGroup(for: .legible)
        guard generation == key, player.currentItem === item else { return }
        audioGroup = audible
        subtitleGroup = legible
        audioOptions = audible?.options.enumerated().map { NativeMediaOption(id: String($0.offset), title: $0.element.displayName) } ?? []
        embeddedSubtitleOptions = legible?.options.enumerated().map { NativeMediaOption(id: "embedded:\($0.offset)", title: $0.element.displayName) } ?? []
        selectAudio(selectedAudio)
        if selectedSubtitle != "auto" { await selectSubtitle(selectedSubtitle) }
    }

    private func handleFailure(_ error: Error?, key: UUID) async {
        guard generation == key else { return }
        let failure = error as NSError?
        recordDiagnostic("failure:\(Self.diagnosticValue(failure?.domain)):\(failure?.code ?? 0)")
        let network = NativePlaybackRecovery.isNetworkError(error, httpStatus: player.currentItem?.errorLog()?.events.last?.errorStatusCode)
            || (error as? NativePlaybackError) == .timeout
        let action = recovery.nextAction(isNetworkFailure: network, transport: session?.protocol,
                                         sourceID: session?.sourceVariantId, selectedSourceID: currentVariant?.id,
                                         availableSources: Set(variants.map(\.id)))
        switch action {
        case .useFile(let sourceID):
            selectedTransport = "file"
            currentVariant = variants.first { $0.id == sourceID }
            statusMessage = "分片解码失败，正在用同一原档直读；画质与音轨保持原样。"
            await renew()
        case .renewAddress:
            statusMessage = "连接中断，正在重新获取播放地址。"
            await renew()
        case .stop:
            pause()
            isLoading = false
            isBuffering = false
            errorMessage = "无法播放此归档：\(error?.localizedDescription ?? "系统解码器未能加载媒体")。可重试、切换节点或手动选择已保存的兼容版本。"
        }
    }

    private func loadAncillary(_ created: PlaybackSession, key: UUID) {
        ancillaryTask?.cancel()
        guard let path = created.danmakuUrl else { return }
        ancillaryTask = Task { [weak self] in
            guard let self else { return }
            do {
                let data = try await self.api.data(path)
                guard self.generation == key, !Task.isCancelled else { return }
                let scheduled = try await PlaybackSidecarDecoder.danmaku(data)
                guard self.generation == key, !Task.isCancelled else { return }
                self.danmaku = scheduled
            } catch {
                guard self.generation == key, !Task.isCancelled else { return }
                self.ancillaryWarning = "弹幕暂不可用；视频播放不受影响。"
            }
        }
    }

    private func scheduleRenewal(_ created: PlaybackSession, key: UUID) {
        renewalTask?.cancel()
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let expiry = created.expiresAt.flatMap { formatter.date(from: $0) ?? ISO8601DateFormatter().date(from: $0) }
        let wait = max(30, (expiry?.timeIntervalSinceNow ?? 14_400) - 300)
        renewalTask = Task { [weak self] in
            do { try await Task.sleep(for: .seconds(wait)) } catch { return }
            guard let self, self.generation == key else { return }
            self.renewalTask = nil
            await self.renew()
        }
    }

    private func persistProgress() {
        guard api.user != nil, playbackIdentityRevision == api.sessionRevision, let part = currentPart, duration.isFinite, duration > 0,
              settledPosition.isFinite, settledPosition >= 0 else { return }
        let position = min(duration, settledPosition)
        let total = duration
        let key = generation
        let identityRevision = api.sessionRevision
        let priorSave = saveProgressTask
        lastProgressSave = Date()
        saveProgressTask = Task { [weak self] in
            await priorSave?.value
            guard let self, self.api.sessionRevision == identityRevision else { return }
            do {
                try await self.api.mutate("/progress/\(part.id)", method: "PUT", body: ["position": .number(position), "duration": .number(total)])
                if self.generation == key { self.progressWarning = nil }
            } catch {
                if self.generation == key { self.progressWarning = "观看进度暂未同步，将在播放中重试。" }
            }
        }
    }

    private func applyLoudness() {
        guard loudnessBalance, let loudness = session?.loudness, loudness.status == "ready",
              !loudness.atmosBypass, session?.media?.dolbyAtmos != true else { player.volume = 1; return }
        player.volume = Float(min(1, max(0, loudness.gainLinear)))
    }

    private func configureAudioSession() throws {
        let audio = AVAudioSession.sharedInstance()
        // Reapplying a category/mode while an item is active can reconfigure the
        // output route. Only change configuration when the system differs.
        if audio.category != .playback || audio.mode != .moviePlayback || audio.routeSharingPolicy != .longFormVideo {
            try audio.setCategory(.playback, mode: .moviePlayback, policy: .longFormVideo)
            recordDiagnostic("audio-configuration-changed")
        }
        if !audio.supportsMultichannelContent {
            try audio.setSupportsMultichannelContent(true)
            recordDiagnostic("multichannel-enabled")
        }
        try audio.setActive(true)
        recordDiagnostic("audio-session-active")
    }

    private func updateAudioRoute() {
        let audio = AVAudioSession.sharedInstance()
        outputRoute = audio.currentRoute.outputs.map(\.portName).joined(separator: " · ")
        if outputRoute.isEmpty { outputRoute = "系统音频输出" }
        switch audio.renderingMode {
        case .dolbyAtmos: renderingStatus = "系统报告：Dolby Atmos 渲染"
        case .dolbyAudio: renderingStatus = "系统报告：Dolby Audio 渲染"
        case .spatialAudio: renderingStatus = "系统报告：空间音频渲染"
        case .surround: renderingStatus = "系统报告：环绕声渲染"
        case .monoStereo: renderingStatus = "系统报告：单声道 / 立体声渲染"
        case .notApplicable: renderingStatus = "当前输出未报告渲染模式"
        @unknown default: renderingStatus = "系统报告了新的音频渲染模式"
        }
        hdrEligible = AVPlayer.eligibleForHDRPlayback
    }

    private func installObservers() {
        timeObserver = player.addPeriodicTimeObserver(forInterval: CMTime(seconds: 0.25, preferredTimescale: 600), queue: .main) { @Sendable [weak self] _ in
            // AVPlayer delivers this observer on the explicitly supplied main
            // queue; avoid creating and scheduling another task on every tick.
            MainActor.assumeIsolated {
                guard let self, self.player.currentItem != nil else { return }
                let now = Date()
                let value = self.player.currentTime().seconds
                if value.isFinite, !self.isSeeking, !self.isLoading {
                    let position = max(0, value)
                    if self.currentTime != position { self.currentTime = position }
                    self.settledPosition = position
                }
                // Elapsed time remains responsive at 4 Hz. Buffer ranges and
                // duration need no more than one check per second between events.
                if now.timeIntervalSince(self.lastMediaRangeRead) >= 1, let item = self.player.currentItem {
                    self.lastMediaRangeRead = now
                    let total = item.duration.seconds
                    if total.isFinite, total > 0, self.duration != total { self.duration = total }
                    var buffered = 0.0
                    for value in item.loadedTimeRanges {
                        let end = CMTimeRangeGetEnd(value.timeRangeValue).seconds
                        if end.isFinite { buffered = max(buffered, end) }
                    }
                    if self.bufferedTime != buffered { self.bufferedTime = buffered }
                }
                self.updateTimeAnchor(at: now)
                if self.isPlaying, now.timeIntervalSince(self.lastProgressSave) >= 10 { self.persistProgress() }
                if self.player.rate > 0, self.currentTime < self.duration - 0.5 { self.endGuard.rearm(self.generation) }
                if now.timeIntervalSince(self.lastNowPlayingUpdate) >= 2 { self.updateNowPlaying(at: now) }
            }
        }
        controlObservation = player.observe(\.timeControlStatus, options: [.new]) { @Sendable [weak self] _, _ in
            Task { @MainActor in
                guard let self else { return }
                let wasPlaying = self.isPlaying
                let status = self.player.timeControlStatus
                self.isPlaying = self.currentVideo != nil && status == .playing
                self.isBuffering = self.currentVideo != nil && self.wantsPlayback && (self.isSeeking || status == .waitingToPlayAtSpecifiedRate) && !self.isLoading
                if self.currentVideo == nil {
                    self.wantsPlayback = false
                    self.expectedTransportState = nil
                } else if let expected = self.expectedTransportState {
                    // A delayed status from the previous command cannot reverse
                    // a newer tap. Waiting acknowledges a play request too.
                    if (expected && status != .paused) || (!expected && status == .paused) {
                        self.expectedTransportState = nil
                    }
                } else if self.isPlaying {
                    // Native PiP/system transport can change AVPlayer directly.
                    self.wantsPlayback = true
                } else if status == .paused, !self.isLoading, !self.isSeeking {
                    self.wantsPlayback = false
                }
                if wasPlaying && !self.isPlaying && status == .paused && !self.isLoading && !self.isSeeking { self.persistProgress() }
                if self.isPlaying != wasPlaying { self.recordDiagnostic(self.isPlaying ? "playing" : "not-playing") }
                self.updateTimeAnchor()
                self.updateNowPlaying()
            }
        }
        rateObservation = player.observe(\.rate, options: [.new]) { @Sendable [weak self] _, _ in
            Task { @MainActor in
                guard let self else { return }
                let rate = self.player.rate
                if rate.isFinite, rate > 0, self.temporaryRate == nil, self.preferredRate != rate {
                    self.preferredRate = rate
                    self.defaults.set(rate, forKey: "treasure.native.playback.rate")
                }
                self.updateTimeAnchor()
                self.updateNowPlaying()
            }
        }
        observe(AVPlayerItem.playbackStalledNotification) { coordinator, notification in
            guard let item = notification.item, item === coordinator.player.currentItem,
                  coordinator.activeItemGeneration == coordinator.generation else { return }
            coordinator.recordDiagnostic("playback-stalled")
            let key = coordinator.generation
            let checkpoint = PlaybackStallCheckpoint(intentRevision: coordinator.playbackIntentRevision, position: coordinator.currentTime)
            coordinator.stallTask?.cancel()
            coordinator.stallTask = Task { [weak coordinator] in
                do { try await Task.sleep(for: .seconds(12)) } catch { return }
                guard let coordinator, coordinator.generation == key,
                      checkpoint.shouldRecover(intentRevision: coordinator.playbackIntentRevision,
                                               position: coordinator.currentTime, wantsPlayback: coordinator.wantsPlayback,
                                               isSeeking: coordinator.isSeeking) else { return }
                coordinator.stallTask = nil
                await coordinator.handleFailure(NativePlaybackError.timeout, key: key)
            }
        }
        observe(AVPlayerItem.didPlayToEndTimeNotification) { coordinator, notification in
            guard let item = notification.item, item === coordinator.player.currentItem,
                  coordinator.activeItemGeneration == coordinator.generation,
                  !coordinator.isSeeking, !coordinator.isLoading,
                  PlaybackStartupPosition.shouldReplay(position: coordinator.player.currentTime().seconds,
                                                       duration: coordinator.duration) else { return }
            coordinator.finishPlayback(item: item, key: coordinator.generation, advanceAutomatically: true)
        }
        observe(AVPlayerItem.failedToPlayToEndTimeNotification) { coordinator, notification in
            guard let item = notification.item, item === coordinator.player.currentItem,
                  coordinator.activeItemGeneration == coordinator.generation else { return }
            let error = notification.error
            let key = coordinator.generation
            Task {
                guard coordinator.generation == key, coordinator.player.currentItem === item else { return }
                await coordinator.handleFailure(error, key: key)
            }
        }
        observe(AVAudioSession.interruptionNotification) { coordinator, notification in
            guard let raw = notification.interruptionType,
                  let type = AVAudioSession.InterruptionType(rawValue: raw) else { return }
            coordinator.recordDiagnostic("interruption:\(raw):options:\(notification.interruptionOptions ?? 0)")
            if type == .began {
                let shouldResume = coordinator.wantsPlayback
                coordinator.pause()
                // Remember playback intent even if an interruption arrives
                // before AVPlayer has progressed from buffering to playing.
                coordinator.wasPlayingBeforeInterruption = shouldResume
            } else {
                let options = notification.interruptionOptions.map(AVAudioSession.InterruptionOptions.init(rawValue:)) ?? []
                if coordinator.wasPlayingBeforeInterruption && options.contains(.shouldResume) { coordinator.resume() }
                coordinator.wasPlayingBeforeInterruption = false
            }
        }
        observe(AVAudioSession.routeChangeNotification) { coordinator, notification in
            let reason = notification.routeReason
            if reason == AVAudioSession.RouteChangeReason.oldDeviceUnavailable.rawValue { coordinator.pause() }
            coordinator.updateAudioRoute()
            coordinator.recordDiagnostic("route-change:\(reason ?? 0)")
        }
        observe(AVAudioSession.spatialPlaybackCapabilitiesChangedNotification) { coordinator, _ in coordinator.updateAudioRoute() }
        observe(AVAudioSession.renderingModeChangeNotification) { coordinator, _ in coordinator.updateAudioRoute() }
        observe(AVPlayer.eligibleForHDRPlaybackDidChangeNotification) { coordinator, _ in coordinator.updateAudioRoute() }
        observe(AVAudioSession.mediaServicesWereResetNotification) { coordinator, _ in
            coordinator.recordDiagnostic("media-services-reset")
            try? coordinator.configureAudioSession()
            let key = coordinator.generation
            Task {
                guard coordinator.generation == key else { return }
                await coordinator.renew()
            }
        }
        observe(UIApplication.didEnterBackgroundNotification) { coordinator, _ in coordinator.persistProgress() }
        observe(UIApplication.willEnterForegroundNotification) { coordinator, _ in
            coordinator.updateAudioRoute()
            guard let raw = coordinator.session?.expiresAt else { return }
            let parser = ISO8601DateFormatter()
            parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let expiry = parser.date(from: raw) ?? ISO8601DateFormatter().date(from: raw), expiry.timeIntervalSinceNow < 300 {
                let key = coordinator.generation
                Task {
                    guard coordinator.generation == key else { return }
                    await coordinator.renew()
                }
            }
        }
    }

    private func observe(_ name: Notification.Name, handler: @escaping @MainActor @Sendable (PlaybackCoordinator, NativePlaybackNotification) -> Void) {
        let token = NotificationCenter.default.addObserver(forName: name, object: nil, queue: .main) { @Sendable [weak self] notification in
            let snapshot = NativePlaybackNotification(notification)
            Task { @MainActor [weak self] in
                guard let self else { return }
                handler(self, snapshot)
            }
        }
        notifications.append(token)
    }

    private func installRemoteCommands() {
        let commands = MPRemoteCommandCenter.shared()
        func add(_ command: MPRemoteCommand, action: @escaping @MainActor @Sendable (PlaybackCoordinator, NativeRemoteCommand) -> Void) {
            let token = command.addTarget { @Sendable [weak self] event in
                let snapshot = NativeRemoteCommand(event)
                Task { @MainActor [weak self] in
                    guard let self else { return }
                    action(self, snapshot)
                }
                return .success
            }
            remoteTargets.append((command, token))
        }
        add(commands.playCommand) { coordinator, _ in coordinator.resume() }
        add(commands.pauseCommand) { coordinator, _ in coordinator.pause() }
        add(commands.togglePlayPauseCommand) { coordinator, _ in coordinator.togglePlayback() }
        commands.skipForwardCommand.preferredIntervals = [15]
        commands.skipBackwardCommand.preferredIntervals = [15]
        add(commands.skipForwardCommand) { coordinator, _ in coordinator.skip(15) }
        add(commands.skipBackwardCommand) { coordinator, _ in coordinator.skip(-15) }
        add(commands.changePlaybackPositionCommand) { coordinator, event in
            guard let position = event.position else { return }
            coordinator.seek(to: position)
        }
        add(commands.nextTrackCommand) { coordinator, _ in Task { await coordinator.nextPart() } }
        add(commands.previousTrackCommand) { coordinator, _ in Task { await coordinator.previousPart() } }
        commands.changePlaybackRateCommand.supportedPlaybackRates = [0.5, 0.75, 1, 1.25, 1.5, 2]
        add(commands.changePlaybackRateCommand) { coordinator, event in
            guard let rate = event.rate else { return }
            coordinator.setRate(rate)
        }
    }

    private func updateTimeAnchor(at date: Date = Date()) {
        anchorTime = currentTime
        anchorRate = player.rate
        playbackAnchor = date
    }

    private func updateNowPlaying(at date: Date = Date(), forcePosition: Bool = false) {
        lastNowPlayingUpdate = date
        guard let video = currentVideo else { return }
        let rate = isPlaying && !isSeeking && !isLoading && player.rate.isFinite ? player.rate : 0
        let state = PlaybackNowPlayingState(videoID: video.id, partID: currentPart?.id,
                                           title: video.title, artist: video.creators.map(\.name).joined(separator: "、"),
                                           album: currentPart?.title ?? "", duration: duration, rate: rate,
                                           preferredRate: preferredRate, artwork: currentArtwork.map(ObjectIdentifier.init),
                                           hasNext: hasNext, hasPrevious: hasPrevious)
        // The system advances elapsed time from rate. Repeated KVO or normal
        // clock ticks need no dictionary allocation or Now Playing publication.
        guard nowPlayingPublication.shouldPublish(state: state, position: currentTime, at: date, forcePosition: forcePosition) else { return }
        var info: [String: Any] = [
            MPMediaItemPropertyTitle: state.title,
            MPMediaItemPropertyArtist: state.artist,
            MPMediaItemPropertyAlbumTitle: state.album,
            MPMediaItemPropertyPlaybackDuration: state.duration,
            MPNowPlayingInfoPropertyElapsedPlaybackTime: currentTime,
            MPNowPlayingInfoPropertyPlaybackRate: state.rate,
            MPNowPlayingInfoPropertyDefaultPlaybackRate: state.preferredRate,
            MPNowPlayingInfoPropertyMediaType: MPNowPlayingInfoMediaType.video.rawValue,
            MPNowPlayingInfoPropertyExternalContentIdentifier: state.videoID,
            MPNowPlayingInfoPropertyIsLiveStream: false
        ]
        if let currentArtwork { info[MPMediaItemPropertyArtwork] = currentArtwork }
        MPNowPlayingInfoCenter.default().nowPlayingInfo = info
        let commands = MPRemoteCommandCenter.shared()
        if commands.nextTrackCommand.isEnabled != state.hasNext { commands.nextTrackCommand.isEnabled = state.hasNext }
        if commands.previousTrackCommand.isEnabled != state.hasPrevious { commands.previousTrackCommand.isEnabled = state.hasPrevious }
    }

    private func loadArtwork(video: ArchiveVideo, key: UUID) {
        artworkTask?.cancel()
        guard generation == key, let path = video.coverUrl else { return }
        artworkTask = Task { [weak self] in
            guard let self, !Task.isCancelled,
                  let data = try? await self.api.data(path), self.generation == key, !Task.isCancelled,
                  let image = UIImage(data: data) else { return }
            self.currentArtwork = MPMediaItemArtwork(boundsSize: image.size) { @Sendable _ in image }
            self.updateNowPlaying()
        }
    }

    private func cancelMediaTasks() {
        artworkTask?.cancel()
        artworkTask = nil
        seekTask?.cancel()
        seekTask = nil
        isSeeking = false
        inFlightSeek = nil
        player.currentItem?.cancelPendingSeeks()
        ancillaryTask?.cancel()
        progressTask?.cancel()
        renewalTask?.cancel()
        startupTimeoutTask?.cancel()
        stallTask?.cancel()
        itemObservation = nil
        audioGroup = nil
        subtitleGroup = nil
    }

    private func recordDiagnostic(_ event: String) {
        let audio = AVAudioSession.sharedInstance()
        let media = session?.media ?? currentVariant.flatMap { currentVideo?.mediaProperties[$0.id] ?? $0.metadata }
        let routeTypes = audio.currentRoute.outputs.map { Self.diagnosticValue($0.portType.rawValue) }.joined(separator: ",")
        let entry = [
            Date().ISO8601Format(.init(includingFractionalSeconds: true)), event,
            "video=\(Self.diagnosticValue(currentVideo?.id))", "part=\(Self.diagnosticValue(currentPart?.id))",
            "variant=\(Self.diagnosticValue(currentVariant?.id))",
            "audioCodec=\(Self.diagnosticValue(media?.audioCodec ?? currentVariant?.audioCodec))",
            "videoCodec=\(Self.diagnosticValue(media?.videoCodec ?? currentVariant?.videoCodec))",
            "sourceChannels=\(media?.audioChannels ?? 0)", "atmos=\(media?.dolbyAtmos.map(String.init) ?? "unknown")",
            "dolbyVision=\(media?.dolbyVision.map(String.init) ?? "unknown")", "hdr=\(media?.hdr.map(String.init) ?? "unknown")",
            "time=\(String(format: "%.3f", currentTime))", "rate=\(player.rate)", "preferredRate=\(preferredRate)",
            "volume=\(player.volume)", "wantsPlayback=\(wantsPlayback)",
            "outputHz=\(audio.sampleRate)", "outputChannels=\(audio.outputNumberOfChannels)", "routeTypes=\(routeTypes)",
            "category=\(Self.diagnosticValue(audio.category.rawValue))", "mode=\(Self.diagnosticValue(audio.mode.rawValue))",
            "routePolicy=\(audio.routeSharingPolicy.rawValue)", "multichannel=\(audio.supportsMultichannelContent)",
            "renderingMode=\(audio.renderingMode.rawValue)"
        ].joined(separator: " | ")
        diagnosticEvents.append(entry)
        if diagnosticEvents.count > 80 { diagnosticEvents.removeFirst(diagnosticEvents.count - 80) }
    }

    private static func diagnosticValue(_ value: String?) -> String {
        guard let value, !value.isEmpty else { return "none" }
        let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
        guard value.count <= 128, value.unicodeScalars.allSatisfy({ allowed.contains($0) }) else { return "redacted" }
        return value
    }
}

/// A stall belongs to the position and transport intent that created it. A
/// backward seek or pause/resume must not trip an older watchdog.
struct PlaybackStallCheckpoint {
    let intentRevision: UInt
    let position: Double

    func shouldRecover(intentRevision: UInt, position: Double, wantsPlayback: Bool, isSeeking: Bool) -> Bool {
        self.intentRevision == intentRevision && wantsPlayback && !isSeeking && position < self.position + 0.2
    }
}

/// Shared by startup restore, replay and ordinary scrubbing. Cancellation is
/// scoped to the actual AVPlayer operation, not to the task that owns it.
private struct PlaybackSeekAttempt {
    let id = UUID()
    let generation: UUID
    let item: AVPlayerItem
    let revision: Int
    let destination: Double
    var cancellationRequested = false

    static func sameDestination(_ lhs: Double, _ rhs: Double) -> Bool {
        CMTimeCompare(CMTime(seconds: lhs, preferredTimescale: 600),
                      CMTime(seconds: rhs, preferredTimescale: 600)) == 0
    }
}

struct NativeMediaOption: Identifiable {
    let id: String
    let title: String
}

private struct NativeWatchProgress: Decodable, Sendable {
    let position: Double
    let duration: Double
}

private enum NativePlaybackError: LocalizedError, Equatable {
    case noMedia, invalidURL, wrongSource, invalidSubtitles, timeout
    var errorDescription: String? {
        switch self {
        case .noMedia: "此视频尚无可播放的归档文件。"
        case .invalidURL: "服务器没有返回有效的播放地址。"
        case .wrongSource: "服务器返回的文件与选中的原档不一致。"
        case .invalidSubtitles: "字幕不是有效的 UTF-8 文件，或文件过大。"
        case .timeout: "媒体加载超时。"
        }
    }
}

/// Extract typed values before crossing the notification callback's isolation boundary.
private struct NativePlaybackNotification: Sendable {
    let item: AVPlayerItem?
    let error: (any Error)?
    let interruptionType: UInt?
    let interruptionOptions: UInt?
    let routeReason: UInt?

    init(_ notification: Notification) {
        item = notification.object as? AVPlayerItem
        error = notification.userInfo?[AVPlayerItemFailedToPlayToEndTimeErrorKey] as? Error
        interruptionType = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt
        interruptionOptions = notification.userInfo?[AVAudioSessionInterruptionOptionKey] as? UInt
        routeReason = notification.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt
    }
}

/// MediaPlayer is free to deliver remote commands off the main thread. Only the
/// scalar event payload crosses into MainActor; its Objective-C event stays local.
private struct NativeRemoteCommand: Sendable {
    let position: Double?
    let rate: Float?
    init(_ event: MPRemoteCommandEvent) {
        position = (event as? MPChangePlaybackPositionCommandEvent)?.positionTime
        rate = (event as? MPChangePlaybackRateCommandEvent)?.playbackRate
    }
}

/// Startup seek intent is separate from persisted watch history. This prevents
/// late API responses or an in-flight seek from undoing the user's gesture.
struct PlaybackStartupPosition {
    private(set) var position: Double
    private(set) var acceptsHistory: Bool
    private(set) var hasExplicitSeek = false
    private(set) var revision = 0
    private var isHistory: Bool

    init(resumeHistory: Bool = true, position: Double = 0) {
        self.position = position
        acceptsHistory = resumeHistory
        isHistory = resumeHistory
    }

    mutating func acceptHistory(_ position: Double, duration: Double) {
        guard acceptsHistory else { return }
        self.position = PlaybackTimeline.resumePosition(position, duration: duration)
    }

    mutating func requestSeek(_ position: Double) {
        guard position.isFinite else { return }
        self.position = max(0, position)
        hasExplicitSeek = true
        isHistory = false
        acceptsHistory = false
        revision += 1
    }

    mutating func closeHistoryWindow() { acceptsHistory = false }

    func resolved(duration: Double) -> Double {
        guard position.isFinite, duration.isFinite, duration > 0 else { return 0 }
        return isHistory ? PlaybackTimeline.resumePosition(position, duration: duration)
            : min(max(0, position), duration)
    }

    static func shouldReplay(position: Double, duration: Double) -> Bool {
        position.isFinite && duration.isFinite && duration > 0 && position >= duration - 0.1
    }
}

/// Decode and prepare large sidecars off the UI actor. Only immutable Data and
/// Sendable cue values cross the boundary; no player or session is captured.
enum PlaybackSidecarDecoder {
    nonisolated static func danmaku(_ data: Data) async throws -> [ScheduledDanmaku] {
        try Task.checkCancellation()
        let worker = Task.detached(priority: .userInitiated) {
            try Task.checkCancellation()
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            let rows: [NativeDanmakuCue]
            do { rows = try decoder.decode([NativeDanmakuCue].self, from: data) }
            catch { throw APIError.decoding }
            try Task.checkCancellation()
            let scheduled = PlaybackTimeline.scheduleDanmaku(rows)
            try Task.checkCancellation()
            return scheduled
        }
        return try await withTaskCancellationHandler {
            try await worker.value
        } onCancel: {
            worker.cancel()
        }
    }

    nonisolated static func subtitles(_ data: Data) async throws -> [NativeSubtitleCue] {
        try Task.checkCancellation()
        let worker = Task.detached(priority: .userInitiated) {
            try Task.checkCancellation()
            guard data.count <= 8_000_000, let text = String(data: data, encoding: .utf8) else {
                throw NativePlaybackError.invalidSubtitles
            }
            let cues = PlaybackTimeline.subtitles(text)
            try Task.checkCancellation()
            return cues
        }
        return try await withTaskCancellationHandler {
            try await worker.value
        } onCancel: {
            worker.cancel()
        }
    }
}

/// Lock-screen metadata changes are distinct from the system's advancing clock.
struct PlaybackNowPlayingState: Equatable {
    var videoID: String
    var partID: String?
    var title: String
    var artist: String
    var album: String
    var duration: Double
    var rate: Float
    var preferredRate: Float
    var artwork: ObjectIdentifier?
    var hasNext: Bool
    var hasPrevious: Bool
}

struct PlaybackNowPlayingPublication {
    private var previous: (state: PlaybackNowPlayingState, position: Double, date: Date)?

    mutating func shouldPublish(state: PlaybackNowPlayingState, position: Double, at date: Date, forcePosition: Bool = false) -> Bool {
        guard position.isFinite else { return false }
        if !forcePosition, let previous, previous.state == state {
            let elapsed = max(0, date.timeIntervalSince(previous.date))
            let expected = min(state.duration, previous.position + elapsed * Double(state.rate))
            if abs(position - expected) < 0.75 { return false }
        }
        previous = (state, position, date)
        return true
    }
}
