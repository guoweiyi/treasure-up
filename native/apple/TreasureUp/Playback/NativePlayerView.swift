import SwiftUI
import AVKit
import Observation

/// The only video display layer is retained with playback. Containers transfer
/// its view, rather than creating competing AVPlayerLayers for the same player.
struct NativePlayerView: UIViewControllerRepresentable {
    let coordinator: PlaybackCoordinator

    func makeUIViewController(context: Context) -> NativePlayerContainerViewController {
        if coordinator.presentation == nil {
            coordinator.presentation = NativePlaybackPresentation(coordinator: coordinator)
        }
        let presentation = coordinator.presentation!
        presentation.configure(coordinator: coordinator)
        return NativePlayerContainerViewController(presentation: presentation)
    }

    func updateUIViewController(_ container: NativePlayerContainerViewController, context: Context) {
        container.presentation.configure(coordinator: coordinator)
    }

    static func dismantleUIViewController(_ container: NativePlayerContainerViewController, coordinator: ()) {
        container.detachPlayerIfOwned()
    }
}

@MainActor
final class NativePlayerContainerViewController: UIViewController {
    let presentation: NativePlaybackPresentation
    private var playerConstraints: [NSLayoutConstraint] = []

    init(presentation: NativePlaybackPresentation) {
        self.presentation = presentation
        super.init(nibName: nil, bundle: nil)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("Use init(presentation:)") }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        view.clipsToBounds = true
    }

    override func viewWillAppear(_ animated: Bool) {
        super.viewWillAppear(animated)
        attachPlayerIfNeeded()
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
}

/// Public AVKit custom-player PiP API. AVPlayerLayer preserves the native decoder,
/// color metadata and EDR path; no image filter or sample-buffer conversion occurs.
@MainActor @Observable
final class NativePlaybackPresentation: NSObject, @preconcurrency AVPictureInPictureControllerDelegate {
    @ObservationIgnored let controller = UIViewController()
    @ObservationIgnored let videoView = NativeVideoLayerView()
    @ObservationIgnored private let overlay: UIHostingController<NativePlaybackOverlay>
    @ObservationIgnored private weak var coordinator: PlaybackCoordinator?
    @ObservationIgnored private var pipController: AVPictureInPictureController?
    @ObservationIgnored private var pipObservation: NSKeyValueObservation?
    @ObservationIgnored private var restoreCompletion: ((Bool) -> Void)?
    @ObservationIgnored private weak var visibleContainer: NativePlayerContainerViewController?
    private(set) var canStartPictureInPicture = false

    init(coordinator: PlaybackCoordinator) {
        self.coordinator = coordinator
        overlay = UIHostingController(rootView: NativePlaybackOverlay(coordinator: coordinator))
        super.init()
        controller.view.backgroundColor = .black
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
        overlay.view.isUserInteractionEnabled = false
        overlay.view.accessibilityElementsHidden = true
        overlay.view.translatesAutoresizingMaskIntoConstraints = false
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
            // Enable automatic PiP only while this is the visible primary video.
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
        // iOS 15+ continuesIfPossible on the AVPlayer supplies background video
        // playback policy. Keep this layer connected so automatic PiP can start;
        // disconnecting it at didEnterBackground races AVKit's PiP transition.
        if videoView.playerLayer.player !== coordinator.player { videoView.playerLayer.player = coordinator.player }
        videoView.playerLayer.videoGravity = coordinator.fitToFill ? .resizeAspectFill : .resizeAspect
        refreshBackgroundConfiguration()
    }

    func refreshBackgroundConfiguration() {
        pipController?.canStartPictureInPictureAutomaticallyFromInline =
            coordinator?.backgroundPlayback == true && visibleContainer != nil
    }

    func setControlsVisible(_ visible: Bool) {
        guard let coordinator else { return }
        overlay.rootView = NativePlaybackOverlay(coordinator: coordinator, controlsVisible: visible)
    }

    func togglePictureInPicture() {
        guard let pipController else { return }
        if pipController.isPictureInPictureActive { pipController.stopPictureInPicture() }
        else if pipController.isPictureInPicturePossible { pipController.startPictureInPicture() }
    }

    func containerDidAppear(_ container: NativePlayerContainerViewController) {
        guard controller.parent === container else { return }
        visibleContainer = container
        refreshBackgroundConfiguration()
        let completion = restoreCompletion
        restoreCompletion = nil
        completion?(true)
    }

    func containerWillDisappear(_ container: NativePlayerContainerViewController) {
        if visibleContainer === container { visibleContainer = nil }
        refreshBackgroundConfiguration()
    }

    func pictureInPictureControllerWillStartPictureInPicture(_ pictureInPictureController: AVPictureInPictureController) {
        coordinator?.isPictureInPictureActive = true
    }
    func pictureInPictureControllerDidStopPictureInPicture(_ pictureInPictureController: AVPictureInPictureController) {
        coordinator?.isPictureInPictureActive = false
    }
    func pictureInPictureController(_ pictureInPictureController: AVPictureInPictureController, failedToStartPictureInPictureWithError error: Error) {
        coordinator?.isPictureInPictureActive = false
        coordinator?.statusMessage = "画中画暂不可用：\(error.localizedDescription)"
    }
    func pictureInPictureController(_ pictureInPictureController: AVPictureInPictureController,
                                    restoreUserInterfaceForPictureInPictureStopWithCompletionHandler completionHandler: @escaping (Bool) -> Void) {
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
    var controlsVisible = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        TimelineView(.animation(minimumInterval: reduceMotion ? 0.25 : 1.0 / 30,
                                paused: !coordinator.isPlaying || scenePhase != .active)) { context in
            let time = coordinator.presentationTime(at: context.date)
            ZStack(alignment: .bottom) {
                if coordinator.danmakuEnabled {
                    let activeRows = PlaybackTimeline.activeDanmaku(coordinator.danmaku, at: time)
                    let fontSize = coordinator.danmakuFontSize
                    let opacity = coordinator.danmakuOpacity
                    let reduceMotion = reduceMotion
                    Canvas { @Sendable canvas, size in
                        guard size.height > 120 else { return }
                        let laneHeight = min(29, max(17, (size.height - 100) / 8))
                        for row in activeRows {
                            let elapsed = time - row.cue.time
                            guard elapsed >= 0, elapsed <= row.duration else { continue }
                            let color = Color(red: Double((row.cue.color >> 16) & 255) / 255,
                                              green: Double((row.cue.color >> 8) & 255) / 255,
                                              blue: Double(row.cue.color & 255) / 255)
                            let text = Text(row.cue.text)
                                .font(.system(size: min(fontSize, laneHeight - 2), weight: .medium))
                                .foregroundStyle(color.opacity(opacity))
                            let resolved = canvas.resolve(text)
                            let textSize = resolved.measure(in: CGSize(width: .greatestFiniteMagnitude, height: laneHeight))
                            let x: CGFloat
                            let y: CGFloat
                            if row.cue.mode == 0 && !reduceMotion {
                                x = size.width - (size.width + textSize.width) * (elapsed / row.duration)
                                y = 46 + CGFloat(row.lane) * laneHeight
                            } else if row.cue.mode == 2 {
                                x = (size.width - textSize.width) / 2
                                y = size.height - 105 - CGFloat(row.lane) * laneHeight
                            } else {
                                x = (size.width - textSize.width) / 2
                                y = 46 + CGFloat(row.lane) * laneHeight
                            }
                            var cueCanvas = canvas
                            cueCanvas.addFilter(.shadow(color: .black.opacity(0.95), radius: 1, x: 1, y: 1))
                            cueCanvas.draw(resolved, at: CGPoint(x: x, y: y), anchor: .topLeading)
                        }
                    }
                }
                let text = PlaybackTimeline.subtitleText(coordinator.subtitleCues, at: time)
                if !text.isEmpty {
                    Text(text)
                        .font(.callout.weight(.semibold))
                        .foregroundStyle(.white)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 5)
                        .background(.black.opacity(0.72), in: .rect(cornerRadius: 6))
                        .padding(.horizontal, 24)
                        .padding(.bottom, controlsVisible ? 88 : 22)
                }
            }
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

struct NativeAirPlayButton: UIViewRepresentable {
    var tint: UIColor = .label
    func makeUIView(context: Context) -> AVRoutePickerView {
        let view = AVRoutePickerView()
        view.prioritizesVideoDevices = true
        view.tintColor = tint
        view.activeTintColor = .systemCyan
        return view
    }
    func updateUIView(_ uiView: AVRoutePickerView, context: Context) { uiView.tintColor = tint }
}

/// A compact recovery surface for old routes; the primary experience lives in
/// VideoDetailView and embeds InlineNativePlayer directly in that page.
struct PlayerScreen: View {
    let coordinator: PlaybackCoordinator
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        InlineNativePlayer(coordinator: coordinator, isExpanded: true, onToggleExpanded: { dismiss() })
            .background(.black).ignoresSafeArea()
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
                        Text("兼容版保留原视频画面，音频转换为 AAC 立体声，不含 Dolby Atmos。").font(.caption).foregroundStyle(.secondary)
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
                    Text("内嵌字幕由系统播放器显示。归档独立字幕与弹幕显示于 App 内及全屏；画中画和 AirPlay 画面不包含这两种叠层。")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Section("播放") {
                    Picker("速度", selection: Binding(get: { Double(coordinator.preferredRate) }, set: { coordinator.setRate(Float($0)) })) {
                        ForEach([0.5, 0.75, 1, 1.25, 1.5, 2.0], id: \.self) { Text("\($0.formatted())×").tag($0) }
                    }
                    Toggle("响度平衡", isOn: $coordinator.loudnessBalance)
                    Text("按归档的响度分析适度衰减过响内容；杜比全景声音轨自动跳过，保留系统原生音频路径。")
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
                    Text("原生时间轴与播放进度同步，自动避让相邻弹幕；开启“减弱动态效果”后使用静态显示。")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Section("播放信息") {
                    LabeledContent("传输", value: coordinator.deliveryDescription)
                    LabeledContent("音频输出", value: coordinator.outputRoute)
                    LabeledContent("HDR 播放条件", value: coordinator.hdrEligible ? "系统报告当前具备条件" : "系统报告当前不具备条件")
                    Text(coordinator.renderingStatus).font(.subheadline)
                    Text("原档的 HDR、Dolby Vision 和 Atmos 标记描述已保存的媒体。实际呈现由编码、设备、显示器、音频路由与系统设置决定；HDR 播放条件不代表已在输出 HDR。部分耳机和内置扬声器不会向 App 报告渲染模式。")
                        .font(.caption).foregroundStyle(.secondary)
                    if coordinator.preferredRate != 1 {
                        Text("变速播放可能改变空间音频处理；检查杜比效果时请使用 1× 速度。")
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
