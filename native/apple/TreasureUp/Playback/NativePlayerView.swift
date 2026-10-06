import SwiftUI
import AVKit
import Observation

/// The only video display layer is retained with playback. Containers transfer
/// its view, rather than creating competing AVPlayerLayers for the same player.
struct NativePlayerView: UIViewControllerRepresentable {
    let coordinator: PlaybackCoordinator
    var isExpanded = false

    func makeUIViewController(context: Context) -> NativePlayerContainerViewController {
        if coordinator.presentation == nil {
            coordinator.presentation = NativePlaybackPresentation(coordinator: coordinator)
        }
        let presentation = coordinator.presentation!
        presentation.configure(coordinator: coordinator)
        return NativePlayerContainerViewController(presentation: presentation, isExpanded: isExpanded)
    }

    func updateUIViewController(_ container: NativePlayerContainerViewController, context: Context) {
        container.isExpanded = isExpanded
        container.presentation.configure(coordinator: coordinator)
    }

    func sizeThatFits(_ proposal: ProposedViewSize, uiViewController: NativePlayerContainerViewController,
                     context: Context) -> CGSize? {
        // The detail column owns the viewport. Do not let the retained UIKit
        // controller's previous size influence SwiftUI after a sidebar resize.
        guard let width = proposal.width, let height = proposal.height,
              width.isFinite, height.isFinite else { return nil }
        return CGSize(width: max(0, width), height: max(0, height))
    }

    static func dismantleUIViewController(_ container: NativePlayerContainerViewController, coordinator: ()) {
        container.detachPlayerIfOwned()
    }
}

@MainActor
final class NativePlayerContainerViewController: UIViewController {
    let presentation: NativePlaybackPresentation
    var isExpanded: Bool {
        didSet { if oldValue != isExpanded { updateOverlaySafeArea() } }
    }
    private var playerConstraints: [NSLayoutConstraint] = []

    init(presentation: NativePlaybackPresentation, isExpanded: Bool = false) {
        self.presentation = presentation
        self.isExpanded = isExpanded
        super.init(nibName: nil, bundle: nil)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("Use init(presentation:)") }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        view.clipsToBounds = true
        view.layer.masksToBounds = true
    }

    override func viewWillAppear(_ animated: Bool) {
        super.viewWillAppear(animated)
        attachPlayerIfNeeded()
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        guard presentation.controller.parent === self else { return }
        // Split-view sidebar and fullscreen transitions can resize the host
        // without remounting this shared video layer. Match the current bounds
        // in the same layout pass, with no stale-frame layer animation.
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        let content = presentation.controller.view!
        if content.frame != view.bounds {
            content.frame = view.bounds
            content.setNeedsLayout()
        }
        content.layoutIfNeeded()
        CATransaction.commit()
        updateOverlaySafeArea()
    }

    override func viewSafeAreaInsetsDidChange() {
        super.viewSafeAreaInsetsDidChange()
        updateOverlaySafeArea()
    }

    private func updateOverlaySafeArea() {
        guard isViewLoaded, presentation.controller.parent === self else { return }
        presentation.setOverlaySafeAreaInsets(NativeSubtitleLayout.safeAreaInsets(isExpanded: isExpanded,
                                                                                 reported: view.safeAreaInsets))
    }

    private func attachPlayerIfNeeded() {
        let controller = presentation.controller
        guard controller.parent !== self else { return }
        if let previous = controller.parent as? NativePlayerContainerViewController {
            previous.detachPlayerIfOwned()
        } else if controller.parent != nil {
            controller.willMove(toParent: nil)
            controller.view.removeFromSuperview()
            controller.removeFromParent()
        }
        addChild(controller)
        controller.view.frame = view.bounds
        controller.view.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(controller.view)
        playerConstraints = [
            controller.view.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            controller.view.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            controller.view.topAnchor.constraint(equalTo: view.topAnchor),
            controller.view.bottomAnchor.constraint(equalTo: view.bottomAnchor)
        ]
        NSLayoutConstraint.activate(playerConstraints)
        controller.didMove(toParent: self)
        updateOverlaySafeArea()
    }

    func detachPlayerIfOwned() {
        let controller = presentation.controller
        guard controller.parent === self else { return }
        presentation.containerWillDisappear(self)
        controller.willMove(toParent: nil)
        NSLayoutConstraint.deactivate(playerConstraints)
        playerConstraints = []
        controller.view.removeFromSuperview()
        controller.removeFromParent()
    }

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        // NavigationStack may retain this container while another detail page
        // borrows the shared layer. Returning must reclaim it without reloading.
        attachPlayerIfNeeded()
        presentation.containerDidAppear(self)
    }

    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        presentation.containerWillDisappear(self)
    }
}

@MainActor
final class NativeVideoLayerView: UIView {
    override class var layerClass: AnyClass { AVPlayerLayer.self }
    var playerLayer: AVPlayerLayer { layer as! AVPlayerLayer }

    override init(frame: CGRect) {
        super.init(frame: frame)
        clipsToBounds = true
        layer.masksToBounds = true
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("Use init(frame:)") }
}

@MainActor @Observable
private final class NativeOverlayPresentationState {
    var controlsVisible = false
    var isVisible = false
    var safeAreaInsets = UIEdgeInsets.zero
}

/// Public AVKit custom-player PiP API. AVPlayerLayer preserves the native decoder,
/// color metadata and EDR path; no image filter or sample-buffer conversion occurs.
@MainActor @Observable
final class NativePlaybackPresentation: NSObject, @preconcurrency AVPictureInPictureControllerDelegate {
    @ObservationIgnored let controller = UIViewController()
    @ObservationIgnored let videoView = NativeVideoLayerView(frame: .zero)
    @ObservationIgnored private let overlay: UIHostingController<NativePlaybackOverlay>
    @ObservationIgnored private let overlayState: NativeOverlayPresentationState
    @ObservationIgnored private weak var coordinator: PlaybackCoordinator?
    @ObservationIgnored private var pipController: AVPictureInPictureController?
    @ObservationIgnored private var pipObservation: NSKeyValueObservation?
    @ObservationIgnored private var restoreCompletion: ((Bool) -> Void)?
    @ObservationIgnored private weak var visibleContainer: NativePlayerContainerViewController?
    private(set) var canStartPictureInPicture = false

    init(coordinator: PlaybackCoordinator) {
        self.coordinator = coordinator
        let state = NativeOverlayPresentationState()
        overlayState = state
        overlay = UIHostingController(rootView: NativePlaybackOverlay(coordinator: coordinator, presentation: state))
        super.init()
        controller.view.backgroundColor = .black
        controller.view.clipsToBounds = true
        controller.view.layer.masksToBounds = true
        videoView.backgroundColor = .black
        videoView.isUserInteractionEnabled = false
        videoView.playerLayer.player = coordinator.player
        videoView.playerLayer.videoGravity = .resizeAspect
        videoView.translatesAutoresizingMaskIntoConstraints = false
        controller.view.addSubview(videoView)
        NSLayoutConstraint.activate([
            videoView.leadingAnchor.constraint(equalTo: controller.view.leadingAnchor),
            videoView.trailingAnchor.constraint(equalTo: controller.view.trailingAnchor),
            videoView.topAnchor.constraint(equalTo: controller.view.topAnchor),
            videoView.bottomAnchor.constraint(equalTo: controller.view.bottomAnchor)
        ])
        controller.addChild(overlay)
        overlay.view.backgroundColor = .clear
        overlay.view.clipsToBounds = true
        overlay.view.layer.masksToBounds = true
        overlay.view.isUserInteractionEnabled = false
        overlay.view.accessibilityElementsHidden = true
        overlay.view.translatesAutoresizingMaskIntoConstraints = false
        overlay.view.setContentHuggingPriority(.defaultLow, for: .horizontal)
        overlay.view.setContentHuggingPriority(.defaultLow, for: .vertical)
        overlay.view.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        overlay.view.setContentCompressionResistancePriority(.defaultLow, for: .vertical)
        controller.view.addSubview(overlay.view)
        NSLayoutConstraint.activate([
            overlay.view.leadingAnchor.constraint(equalTo: controller.view.leadingAnchor),
            overlay.view.trailingAnchor.constraint(equalTo: controller.view.trailingAnchor),
            overlay.view.topAnchor.constraint(equalTo: controller.view.topAnchor),
            overlay.view.bottomAnchor.constraint(equalTo: controller.view.bottomAnchor)
        ])
        overlay.didMove(toParent: controller)
        if AVPictureInPictureController.isPictureInPictureSupported() {
            pipController = AVPictureInPictureController(playerLayer: videoView.playerLayer)
            pipController?.delegate = self
            // PiP is always an explicit action; leaving the video page must not
            // turn into an unexpected floating player.
            pipController?.canStartPictureInPictureAutomaticallyFromInline = false
            pipObservation = pipController?.observe(\.isPictureInPicturePossible, options: [.initial, .new]) { @Sendable [weak self] _, _ in
                Task { @MainActor in
                    guard let self else { return }
                    self.canStartPictureInPicture = self.pipController?.isPictureInPicturePossible == true
                }
            }
        }
    }

    func configure(coordinator: PlaybackCoordinator) {
        // AVPlayer owns the background playback policy. Keep its single render
        // layer connected for explicitly requested PiP and fullscreen transfers.
        if videoView.playerLayer.player !== coordinator.player { videoView.playerLayer.player = coordinator.player }
        let gravity: AVLayerVideoGravity = coordinator.fitToFill ? .resizeAspectFill : .resizeAspect
        if videoView.playerLayer.videoGravity != gravity { videoView.playerLayer.videoGravity = gravity }
    }

    func refreshBackgroundConfiguration() {
        // Background audio is managed by AVPlayer. It does not opt into PiP.
        if pipController?.canStartPictureInPictureAutomaticallyFromInline == true {
            pipController?.canStartPictureInPictureAutomaticallyFromInline = false
        }
    }

    func setControlsVisible(_ visible: Bool) {
        if overlayState.controlsVisible != visible { overlayState.controlsVisible = visible }
    }

    func setOverlaySafeAreaInsets(_ insets: UIEdgeInsets) {
        if overlayState.safeAreaInsets != insets { overlayState.safeAreaInsets = insets }
    }

    private func refreshOverlayVisibility() {
        guard let coordinator else { return }
        // The hosting controller is retained with playback even after leaving
        // the video page. Stop its animation clock while no overlay is visible.
        let visible = visibleContainer != nil && !coordinator.isPictureInPictureActive && coordinator.currentVideo != nil
        if overlayState.isVisible != visible { overlayState.isVisible = visible }
    }

    func togglePictureInPicture() {
        guard let pipController, coordinator?.currentVideo != nil else { return }
        if pipController.isPictureInPictureActive { pipController.stopPictureInPicture() }
        else if pipController.isPictureInPicturePossible { pipController.startPictureInPicture() }
    }

    func stopPlaybackPresentation() {
        let completion = restoreCompletion
        restoreCompletion = nil
        completion?(false)
        if pipController?.isPictureInPictureActive == true { pipController?.stopPictureInPicture() }
        coordinator?.isPictureInPictureActive = false
        coordinator?.requestsPresentation = false
        overlayState.isVisible = false
    }

    func containerDidAppear(_ container: NativePlayerContainerViewController) {
        guard controller.parent === container else { return }
        visibleContainer = container
        refreshOverlayVisibility()
        refreshBackgroundConfiguration()
        let completion = restoreCompletion
        restoreCompletion = nil
        completion?(true)
    }

    func containerWillDisappear(_ container: NativePlayerContainerViewController) {
        if visibleContainer === container { visibleContainer = nil }
        refreshOverlayVisibility()
        refreshBackgroundConfiguration()
    }

    func pictureInPictureControllerWillStartPictureInPicture(_ pictureInPictureController: AVPictureInPictureController) {
        guard coordinator?.currentVideo != nil else {
            pictureInPictureController.stopPictureInPicture()
            return
        }
        coordinator?.isPictureInPictureActive = true
        refreshOverlayVisibility()
    }
    func pictureInPictureControllerDidStopPictureInPicture(_ pictureInPictureController: AVPictureInPictureController) {
        coordinator?.isPictureInPictureActive = false
        refreshOverlayVisibility()
    }
    func pictureInPictureController(_ pictureInPictureController: AVPictureInPictureController, failedToStartPictureInPictureWithError error: Error) {
        coordinator?.isPictureInPictureActive = false
        refreshOverlayVisibility()
        coordinator?.statusMessage = appPrompt("画中画暂不可用：\(error.localizedDescription)")
    }
    func pictureInPictureController(_ pictureInPictureController: AVPictureInPictureController,
                                    restoreUserInterfaceForPictureInPictureStopWithCompletionHandler completionHandler: @escaping (Bool) -> Void) {
        guard coordinator?.currentVideo != nil else { completionHandler(false); return }
        if let visibleContainer, controller.parent === visibleContainer,
           visibleContainer.view.window != nil {
            completionHandler(true)
            return
        }
        restoreCompletion?(false)
        restoreCompletion = completionHandler
        coordinator?.requestsPresentation = true
    }
}

private struct NativePlaybackOverlay: View {
    let coordinator: PlaybackCoordinator
    let presentation: NativeOverlayPresentationState
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.scenePhase) private var scenePhase

    private var hasDanmaku: Bool { coordinator.danmakuEnabled && !coordinator.danmaku.isEmpty }

    var body: some View {
        if presentation.isVisible && (hasDanmaku || !coordinator.subtitleCues.isEmpty) {
            NativeTimedPlaybackOverlay(coordinator: coordinator, playbackTime: coordinator.currentTime,
                                       controlsVisible: presentation.controlsVisible,
                                       reduceMotion: reduceMotion, sceneActive: scenePhase == .active,
                                       safeAreaInsets: presentation.safeAreaInsets)
                .allowsHitTesting(false)
                .accessibilityHidden(true)
                .clipped()
        }
    }
}

private struct NativeTimedPlaybackOverlay: UIViewRepresentable {
    let coordinator: PlaybackCoordinator
    // Observe coarse progress for seeks and paused frames; animation never
    // publishes a SwiftUI state change or asks AVPlayer for its time per frame.
    let playbackTime: Double
    let controlsVisible: Bool
    let reduceMotion: Bool
    let sceneActive: Bool
    let safeAreaInsets: UIEdgeInsets

    func makeUIView(context: Context) -> NativeTimedOverlayView { NativeTimedOverlayView(frame: .zero) }

    func updateUIView(_ view: NativeTimedOverlayView, context: Context) {
        view.configure(danmaku: coordinator.danmakuEnabled ? coordinator.danmaku : [],
                       subtitles: coordinator.subtitleCues,
                       time: coordinator.isPlaying ? coordinator.presentationTime(at: Date()) : playbackTime,
                       fontSize: coordinator.danmakuFontSize, opacity: coordinator.danmakuOpacity,
                       controlsVisible: controlsVisible, reduceMotion: reduceMotion,
                       safeAreaInsets: safeAreaInsets,
                       playing: coordinator.isPlaying && !coordinator.isLoading && !coordinator.isSeeking && sceneActive,
                       timeProvider: { [weak coordinator] in coordinator?.presentationTime(at: Date()) ?? 0 })
    }

    static func dismantleUIView(_ view: NativeTimedOverlayView, coordinator: ()) { view.stop() }
}

/// Inline video is already placed inside the page's safe area. Only the
/// edge-to-edge fullscreen container contributes device cutout/home-bar insets.
enum NativeSubtitleLayout {
    static func safeAreaInsets(isExpanded: Bool, reported: UIEdgeInsets) -> UIEdgeInsets {
        guard isExpanded else { return .zero }
        func finite(_ value: CGFloat) -> CGFloat { value.isFinite ? max(0, value) : 0 }
        return UIEdgeInsets(top: finite(reported.top), left: finite(reported.left),
                            bottom: finite(reported.bottom), right: finite(reported.right))
    }
}

/// A single visible overlay owns the native animation clock. Moving a cached
/// glyph no longer rebuilds SwiftUI/UIViewRepresentable 30 times per second;
/// 60 Hz layer positions stay independent from the video's decoding/HDR path.
@MainActor
final class NativeTimedOverlayView: UIView {
    @MainActor private final class DisplayLinkTarget: NSObject {
        weak var owner: NativeTimedOverlayView?
        init(owner: NativeTimedOverlayView) { self.owner = owner }
        @objc func tick(_ link: CADisplayLink) { owner?.advance(link) }
    }

    let danmakuView = NativeDanmakuOverlayView(frame: .zero)
    private let subtitleBackground = UIView()
    private let subtitleLabel = UILabel()
    private var subtitleBottom: NSLayoutConstraint!
    private var subtitleTop: NSLayoutConstraint!
    private var subtitleLeading: NSLayoutConstraint!
    private var subtitleTrailing: NSLayoutConstraint!
    private var subtitleCenter: NSLayoutConstraint!
    private var viewportInsets = UIEdgeInsets.zero
    private var controlsVisible = false
    private var displayLink: CADisplayLink?
    private var danmaku: [ScheduledDanmaku] = []
    private var subtitles: [NativeSubtitleCue] = []
    private var timeProvider: (() -> Double)?
    private var playing = false
    private var fontSize = 18.0
    private var opacity = 0.85
    private var reduceMotion = false
    private var lastSubtitleHostTime: CFTimeInterval = -.infinity
    private(set) var subtitleAssignmentCount = 0
    private(set) var displayedSubtitleText = ""
    private(set) var displayLinkCreationCount = 0
    var isAnimationRunning: Bool { displayLink?.isPaused == false }
    var animationFrameRate: Int { displayLink?.preferredFramesPerSecond ?? 0 }

    override init(frame: CGRect) {
        super.init(frame: frame)
        backgroundColor = .clear
        isUserInteractionEnabled = false
        accessibilityElementsHidden = true
        clipsToBounds = true
        danmakuView.translatesAutoresizingMaskIntoConstraints = false
        addSubview(danmakuView)
        subtitleBackground.backgroundColor = UIColor.black.withAlphaComponent(0.72)
        subtitleBackground.layer.cornerRadius = 6
        subtitleBackground.clipsToBounds = true
        subtitleBackground.translatesAutoresizingMaskIntoConstraints = false
        subtitleBackground.isHidden = true
        addSubview(subtitleBackground)
        subtitleLabel.font = UIFontMetrics(forTextStyle: .callout)
            .scaledFont(for: UIFont.systemFont(ofSize: 16, weight: .semibold))
        subtitleLabel.adjustsFontForContentSizeCategory = true
        subtitleLabel.textColor = .white
        subtitleLabel.textAlignment = .center
        subtitleLabel.numberOfLines = 0
        subtitleLabel.translatesAutoresizingMaskIntoConstraints = false
        subtitleBackground.addSubview(subtitleLabel)
        subtitleBottom = subtitleBackground.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -22)
        subtitleTop = subtitleBackground.topAnchor.constraint(greaterThanOrEqualTo: topAnchor, constant: 12)
        // A transient zero-height viewport must not create required-constraint
        // conflicts while UIKit transfers the player between containers.
        subtitleTop.priority = UILayoutPriority(999)
        subtitleLeading = subtitleBackground.leadingAnchor.constraint(greaterThanOrEqualTo: leadingAnchor, constant: 24)
        subtitleTrailing = subtitleBackground.trailingAnchor.constraint(lessThanOrEqualTo: trailingAnchor, constant: -24)
        subtitleCenter = subtitleBackground.centerXAnchor.constraint(equalTo: centerXAnchor)
        NSLayoutConstraint.activate([
            danmakuView.leadingAnchor.constraint(equalTo: leadingAnchor),
            danmakuView.trailingAnchor.constraint(equalTo: trailingAnchor),
            danmakuView.topAnchor.constraint(equalTo: topAnchor),
            danmakuView.bottomAnchor.constraint(equalTo: bottomAnchor),
            subtitleCenter, subtitleLeading, subtitleTrailing, subtitleBottom, subtitleTop,
            subtitleLabel.leadingAnchor.constraint(equalTo: subtitleBackground.leadingAnchor, constant: 10),
            subtitleLabel.trailingAnchor.constraint(equalTo: subtitleBackground.trailingAnchor, constant: -10),
            subtitleLabel.topAnchor.constraint(equalTo: subtitleBackground.topAnchor, constant: 5),
            subtitleLabel.bottomAnchor.constraint(equalTo: subtitleBackground.bottomAnchor, constant: -5)
        ])
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("Use init(frame:)") }

    func configure(danmaku: [ScheduledDanmaku], subtitles: [NativeSubtitleCue], time: Double,
                   fontSize: Double = 18, opacity: Double = 0.85, controlsVisible: Bool = false,
                   reduceMotion: Bool = false, safeAreaInsets: UIEdgeInsets = .zero,
                   playing: Bool, timeProvider: @escaping () -> Double) {
        self.danmaku = danmaku
        self.subtitles = subtitles
        self.fontSize = fontSize
        self.opacity = opacity
        self.reduceMotion = reduceMotion
        self.playing = playing
        self.timeProvider = timeProvider
        self.controlsVisible = controlsVisible
        viewportInsets = safeAreaInsets
        updateSubtitleLayout()
        renderFrame(time: time, hostTime: CACurrentMediaTime(), forceSubtitle: true)
        refreshClock()
    }

    override func layoutSubviews() {
        updateSubtitleLayout()
        super.layoutSubviews()
    }

    private func updateSubtitleLayout() {
        let insets = NativeSubtitleLayout.safeAreaInsets(isExpanded: true, reported: viewportInsets)
        let bottom = controlsVisible ? max(3, insets.bottom) + 96 : insets.bottom + 22
        let top = insets.top + (controlsVisible ? 52 : 12)
        let leading = insets.left + 24
        let trailing = -(insets.right + 24)
        let center = (insets.left - insets.right) / 2
        if subtitleBottom.constant != -bottom { subtitleBottom.constant = -bottom }
        if subtitleTop.constant != top { subtitleTop.constant = top }
        if subtitleLeading.constant != leading { subtitleLeading.constant = leading }
        if subtitleTrailing.constant != trailing { subtitleTrailing.constant = trailing }
        if subtitleCenter.constant != center { subtitleCenter.constant = center }
    }

    override func didMoveToWindow() {
        super.didMoveToWindow()
        refreshClock()
    }

    private func refreshClock() {
        guard window != nil else {
            displayLink?.invalidate()
            displayLink = nil
            return
        }
        let running = playing && (!danmaku.isEmpty || !subtitles.isEmpty)
        if running && displayLink == nil {
            let link = CADisplayLink(target: DisplayLinkTarget(owner: self), selector: #selector(DisplayLinkTarget.tick(_:)))
            link.add(to: .main, forMode: .common)
            displayLink = link
            displayLinkCreationCount += 1
        }
        let frameRate = !danmaku.isEmpty && !reduceMotion ? 60 : 10
        if displayLink?.preferredFramesPerSecond != frameRate { displayLink?.preferredFramesPerSecond = frameRate }
        displayLink?.isPaused = !running
    }

    private func advance(_ link: CADisplayLink) {
        guard let timeProvider else { return }
        renderFrame(time: timeProvider(), hostTime: link.timestamp)
    }

    // Also used by deterministic offline regressions: presentation time and
    // display time are separate so 2× playback doesn't double subtitle work.
    func renderFrame(time: Double, hostTime: CFTimeInterval, forceSubtitle: Bool = false) {
        danmakuView.update(rows: PlaybackTimeline.activeDanmaku(danmaku, at: time), time: time,
                           fontSize: fontSize, opacity: opacity, reduceMotion: reduceMotion)
        guard forceSubtitle || hostTime - lastSubtitleHostTime >= 0.1 else { return }
        lastSubtitleHostTime = hostTime
        let text = PlaybackTimeline.subtitleText(subtitles, at: time)
        guard displayedSubtitleText != text else { return }
        displayedSubtitleText = text
        subtitleLabel.text = text
        subtitleBackground.isHidden = text.isEmpty
        subtitleAssignmentCount += 1
    }

    func stop() {
        playing = false
        displayLink?.invalidate()
        displayLink = nil
        timeProvider = nil
        danmaku = []
        subtitles = []
        renderFrame(time: 0, hostTime: CACurrentMediaTime(), forceSubtitle: true)
    }
}

/// Only the small visible cue set has layers. Text layout and its rasterized
/// shadow survive frame ticks; scrolling changes layer positions, never the
/// AVPlayerLayer or a snapshot of the video underneath it.
@MainActor
final class NativeDanmakuOverlayView: UIView {
    private struct GlyphStyle: Equatable {
        let text: String
        let color: UInt32
        let fontSize: CGFloat
        let scale: CGFloat
    }

    private final class Glyph {
        let layer = CATextLayer()
        var style: GlyphStyle
        var generation: UInt64 = 0

        init(style: GlyphStyle) { self.style = style }
    }

    private var glyphs: [Int: Glyph] = [:]
    private var generation: UInt64 = 0
    private var rows: ArraySlice<ScheduledDanmaku> = []
    private var time = 0.0
    private var fontSize = 18.0
    private var opacity = 0.85
    private var reduceMotion = false
    private var lastRenderedBounds: CGRect?
    private var lastRenderedScale: CGFloat?
    private(set) var textPreparationCount = 0
    var cachedCueCount: Int { glyphs.count }

    override init(frame: CGRect) {
        super.init(frame: frame)
        backgroundColor = .clear
        isUserInteractionEnabled = false
        accessibilityElementsHidden = true
        clipsToBounds = true
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("Use init(frame:)") }

    func update(rows: ArraySlice<ScheduledDanmaku>, time: Double, fontSize: Double,
                opacity: Double, reduceMotion: Bool) {
        self.rows = rows
        self.time = time
        self.fontSize = fontSize
        self.opacity = opacity
        self.reduceMotion = reduceMotion
        renderFrame()
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        // A clock tick may already have rendered these bounds. Ordinary parent
        // layout passes do not need another generation sweep/layer submission.
        if lastRenderedBounds != bounds || lastRenderedScale != traitCollection.displayScale { renderFrame() }
    }

    override func didMoveToWindow() {
        super.didMoveToWindow()
        renderFrame()
    }

    private func renderFrame() {
        lastRenderedBounds = bounds
        lastRenderedScale = traitCollection.displayScale
        generation &+= 1
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        defer { CATransaction.commit() }
        if bounds.width > 0, bounds.height > 120, time.isFinite {
            let laneHeight = min(29, max(17, (bounds.height - 100) / 8))
            let effectiveFont = min(fontSize.isFinite ? max(1, fontSize) : 18, laneHeight - 2)
            let scale = max(1, traitCollection.displayScale)
            let effectiveOpacity = Float(opacity.isFinite ? min(1, max(0, opacity)) : 0.85)
            for row in rows {
                let elapsed = time - row.cue.time
                guard row.duration.isFinite, row.duration > 0, elapsed >= 0, elapsed <= row.duration else { continue }
                let style = GlyphStyle(text: row.cue.text, color: row.cue.color,
                                       fontSize: effectiveFont, scale: scale)
                let glyph: Glyph
                if let existing = glyphs[row.id] {
                    glyph = existing
                    if glyph.style != style { prepare(glyph, style: style) }
                } else {
                    glyph = Glyph(style: style)
                    prepare(glyph, style: style)
                    layer.addSublayer(glyph.layer)
                    glyphs[row.id] = glyph
                }
                glyph.generation = generation
                if glyph.layer.opacity != effectiveOpacity { glyph.layer.opacity = effectiveOpacity }
                let width = glyph.layer.bounds.width
                let x: CGFloat
                let y: CGFloat
                if row.cue.mode == 0 && !reduceMotion {
                    x = bounds.width - (bounds.width + width) * (elapsed / row.duration)
                    y = 46 + CGFloat(row.lane) * laneHeight
                } else if row.cue.mode == 2 {
                    x = (bounds.width - width) / 2
                    y = bounds.height - 105 - CGFloat(row.lane) * laneHeight
                } else {
                    x = (bounds.width - width) / 2
                    y = 46 + CGFloat(row.lane) * laneHeight
                }
                let position = CGPoint(x: x, y: y)
                if glyph.layer.position != position { glyph.layer.position = position }
            }
        }
        // No all-track glyph cache: expired cues, seeks and hidden/zero-size
        // viewports release their backing stores immediately. The scheduler
        // admits at most eight simultaneously visible lanes.
        var expired: [Int] = []
        for (id, glyph) in glyphs where glyph.generation != generation {
            glyph.layer.removeFromSuperlayer()
            expired.append(id)
        }
        for id in expired { glyphs.removeValue(forKey: id) }
    }

    private func prepare(_ glyph: Glyph, style: GlyphStyle) {
        let font = UIFont.systemFont(ofSize: style.fontSize, weight: .medium)
        let color = UIColor(red: CGFloat((style.color >> 16) & 255) / 255,
                            green: CGFloat((style.color >> 8) & 255) / 255,
                            blue: CGFloat(style.color & 255) / 255, alpha: 1)
        let text = NSAttributedString(string: style.text, attributes: [.font: font, .foregroundColor: color])
        let measured = text.boundingRect(with: CGSize(width: CGFloat.greatestFiniteMagnitude,
                                                      height: CGFloat.greatestFiniteMagnitude),
                                         options: [.usesLineFragmentOrigin, .usesFontLeading], context: nil)
        glyph.style = style
        glyph.layer.anchorPoint = .zero
        glyph.layer.bounds = CGRect(x: 0, y: 0, width: ceil(measured.width), height: ceil(measured.height))
        glyph.layer.contentsScale = style.scale
        glyph.layer.string = text
        glyph.layer.isWrapped = false
        glyph.layer.truncationMode = .none
        glyph.layer.shadowColor = UIColor.black.cgColor
        glyph.layer.shadowOpacity = 0.95
        glyph.layer.shadowRadius = 1
        glyph.layer.shadowOffset = CGSize(width: 1, height: 1)
        glyph.layer.shouldRasterize = true
        glyph.layer.rasterizationScale = style.scale
        textPreparationCount += 1
    }
}

struct NativeAirPlayButton: UIViewRepresentable {
    var tint: UIColor = .label
    var onPresent: () -> Void = {}
    var onDismiss: () -> Void = {}

    func makeCoordinator() -> Delegate { Delegate(onPresent: onPresent, onDismiss: onDismiss) }

    func makeUIView(context: Context) -> AVRoutePickerView {
        let view = AVRoutePickerView()
        view.prioritizesVideoDevices = true
        view.tintColor = tint
        view.activeTintColor = .systemCyan
        view.delegate = context.coordinator
        return view
    }
    func updateUIView(_ uiView: AVRoutePickerView, context: Context) {
        uiView.tintColor = tint
        context.coordinator.onPresent = onPresent
        context.coordinator.onDismiss = onDismiss
    }

    @MainActor final class Delegate: NSObject, AVRoutePickerViewDelegate {
        var onPresent: () -> Void
        var onDismiss: () -> Void
        init(onPresent: @escaping () -> Void, onDismiss: @escaping () -> Void) {
            self.onPresent = onPresent
            self.onDismiss = onDismiss
        }
        nonisolated func routePickerViewWillBeginPresentingRoutes(_ routePickerView: AVRoutePickerView) {
            Task { @MainActor [weak self] in self?.onPresent() }
        }
        nonisolated func routePickerViewDidEndPresentingRoutes(_ routePickerView: AVRoutePickerView) {
            Task { @MainActor [weak self] in self?.onDismiss() }
        }
    }
}

struct PlaybackSettingsView: View {
    @Bindable var coordinator: PlaybackCoordinator
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section("画质与音轨") {
                    ForEach(coordinator.variants) { variant in
                        Button {
                            Task { await coordinator.selectVariant(variant) }
                        } label: {
                            HStack {
                                Text(coordinator.variantLabel(variant)).foregroundStyle(.primary)
                                Spacer()
                                if coordinator.currentVariant?.id == variant.id { Image(systemName: "checkmark").foregroundStyle(.tint) }
                            }.frame(minHeight: 30)
                        }
                    }
                    if let copy = coordinator.compatibleAudioVariant {
                        Button("使用 AAC 立体声兼容音轨") { Task { await coordinator.selectVariant(copy) } }
                        Text(appPrompt("兼容版保留原视频画面，音频转换为 AAC 立体声，不含 Dolby Atmos。")).font(.caption).foregroundStyle(.secondary)
                    }
                    if !coordinator.audioOptions.isEmpty {
                        Picker("内嵌音轨", selection: Binding(get: { coordinator.selectedAudio }, set: { coordinator.selectAudio($0) })) {
                            Text("跟随系统").tag("auto")
                            ForEach(coordinator.audioOptions) { Text($0.title).tag($0.id) }
                        }
                    }
                }
                Section("字幕") {
                    Picker("字幕轨道", selection: Binding(get: { coordinator.selectedSubtitle }, set: { value in Task { await coordinator.selectSubtitle(value) } })) {
                        Text("跟随系统").tag("auto")
                        Text("关闭").tag("off")
                        ForEach(coordinator.embeddedSubtitleOptions) { Text($0.title).tag($0.id) }
                        ForEach(Array(coordinator.availableSubtitles.enumerated()), id: \.offset) { index, track in
                            Text(track.label).tag("sidecar:\(index)")
                        }
                    }
                    Text(appPrompt("内嵌字幕由系统播放器显示。归档独立字幕与弹幕显示于 App 内及全屏；画中画和 AirPlay 画面不包含这两种叠层。"))
                        .font(.caption).foregroundStyle(.secondary)
                }
                Section("播放") {
                    Picker("速度", selection: Binding(get: { Double(coordinator.preferredRate) }, set: { coordinator.setRate(Float($0)) })) {
                        ForEach([0.5, 0.75, 1, 1.25, 1.5, 2.0], id: \.self) { Text("\($0.formatted())×").tag($0) }
                    }
                    Toggle("响度平衡", isOn: $coordinator.loudnessBalance)
                    Text(appPrompt("按归档的响度分析适度衰减过响内容；杜比全景声音轨自动跳过，保留系统原生音频路径。"))
                        .font(.caption).foregroundStyle(.secondary)
                    Toggle("填满画面", isOn: $coordinator.fitToFill)
                    Toggle("自动播放下一集", isOn: $coordinator.autoAdvance)
                    Toggle("允许后台继续播放", isOn: $coordinator.backgroundPlayback)
                    Picker("分发节点", selection: Binding(get: { coordinator.selectedRouteID }, set: { value in Task { await coordinator.selectRoute(value) } })) {
                        Text("自动选择").tag("")
                        ForEach(coordinator.session?.routes ?? []) { route in
                            Text(route.name + (route.status == "unavailable" ? " · 不可用" : ""))
                                .tag(route.id).disabled(route.status == "unavailable")
                        }
                    }
                }
                Section("弹幕") {
                    Toggle("显示弹幕", isOn: $coordinator.danmakuEnabled)
                    LabeledContent("透明度", value: "\(Int(coordinator.danmakuOpacity * 100))%")
                    Slider(value: $coordinator.danmakuOpacity, in: 0.2...1).accessibilityLabel("弹幕透明度")
                    LabeledContent("字号", value: "\(Int(coordinator.danmakuFontSize))")
                    Slider(value: $coordinator.danmakuFontSize, in: 14...26, step: 1).accessibilityLabel("弹幕字号")
                    Text(appPrompt("原生时间轴与播放进度同步，自动避让相邻弹幕；开启“减弱动态效果”后使用静态显示。"))
                        .font(.caption).foregroundStyle(.secondary)
                }
                Section("播放信息") {
                    LabeledContent("传输", value: coordinator.deliveryDescription)
                    LabeledContent("音频输出", value: coordinator.outputRoute)
                    LabeledContent("HDR 播放条件", value: coordinator.hdrEligible ? "系统报告当前具备条件" : "系统报告当前不具备条件")
                    Text(appPrompt(coordinator.renderingStatus)).font(.subheadline)
                    Text(appPrompt("原档的 HDR、Dolby Vision 和 Atmos 标记描述已保存的媒体。实际呈现由编码、设备、显示器、音频路由与系统设置决定；HDR 播放条件不代表已在输出 HDR。部分耳机和内置扬声器不会向 App 报告渲染模式。"))
                        .font(.caption).foregroundStyle(.secondary)
                    if coordinator.preferredRate != 1 {
                        Text(appPrompt("变速播放可能改变空间音频处理；检查杜比效果时请使用 1× 速度。"))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            .navigationTitle("播放设置")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() } } }
        }
    }
}
