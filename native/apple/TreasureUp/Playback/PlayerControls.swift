import SwiftUI
import AVKit
import Observation

/// A single native video surface with controls that stay in the detail page.
/// The caller owns the viewport and the transition to a full-screen controller.
struct InlineNativePlayer: View {
    @Bindable var coordinator: PlaybackCoordinator
    var isExpanded = false
    let onToggleExpanded: () -> Void
    var onBack: (() -> Void)? = nil

    @Environment(\.accessibilityVoiceOverEnabled) private var voiceOver
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @Environment(\.scenePhase) private var scenePhase
    @State private var controlsVisible = true
    @State private var presentation: NativePlaybackPresentation?
    @State private var hideTask: Task<Void, Never>?
    @State private var noticeTask: Task<Void, Never>?
    @State private var scrubbing = PlayerScrubbingState()
    @State private var feedback: String?
    @State private var panel: PlayerInlinePanel?
    @GestureState private var holdingSpeed = false

    private var seekableDuration: Double { coordinator.duration.isFinite ? max(0, coordinator.duration) : 0 }

    var body: some View {
        GeometryReader { geometry in
            let controlInsets = PlayerGestureMath.controlInsets(isExpanded: isExpanded, reported: geometry.safeAreaInsets)
            let gestureBounds = PlayerGestureMath.gestureBounds(size: geometry.size, insets: controlInsets,
                controlsVisible: controlsVisible || voiceOver, largeText: dynamicTypeSize.isAccessibilitySize)
            ZStack {
                NativePlayerView(coordinator: coordinator)
                    .frame(width: geometry.size.width, height: geometry.size.height).clipped()
                    .allowsHitTesting(false)
                gestureSurface(width: geometry.size.width)
                    .frame(width: gestureBounds.width, height: gestureBounds.height)
                    .position(x: gestureBounds.midX, y: gestureBounds.midY)
                    .allowsHitTesting(panel == nil)
                if coordinator.isPictureInPictureActive {
                    VStack(spacing: 10) {
                        Image(systemName: "pip.fill").font(.largeTitle)
                        Text(appPrompt("正在画中画中播放")).font(.subheadline)
                    }.foregroundStyle(.white.opacity(0.8)).allowsHitTesting(false)
                }
                if controlsVisible || voiceOver {
                    controls(size: geometry.size, insets: controlInsets)
                        .allowsHitTesting(panel == nil)
                        .accessibilityHidden(panel != nil)
                        .transition(.opacity)
                }
                PlayerInteractionFeedback(coordinator: coordinator, scrubbing: scrubbing,
                    holdingSpeed: holdingSpeed, feedback: feedback, topInset: controlInsets.top)
                if coordinator.isLoading || coordinator.isBuffering {
                    ProgressView().tint(.white).controlSize(.large)
                        .padding(18).background(.black.opacity(0.35), in: .circle)
                        .allowsHitTesting(false)
                }
                if let error = coordinator.errorMessage {
                    VStack(spacing: 10) {
                        Label(appPrompt("播放遇到问题"), systemImage: "exclamationmark.triangle")
                            .font(.subheadline.bold())
                        Text(appPrompt(error)).font(.caption).lineLimit(isExpanded ? 4 : 2)
                        Button("重新尝试") { Task { await coordinator.retry() } }
                            .buttonStyle(.borderedProminent).tint(.white.opacity(0.25))
                    }.foregroundStyle(.white).multilineTextAlignment(.center)
                        .padding(16).frame(maxWidth: 380)
                        .background(.black.opacity(0.82), in: .rect(cornerRadius: 16))
                        .padding(20)
                }
                if let panel {
                    Color.black.opacity(0.3).contentShape(Rectangle())
                        .onTapGesture { closePanel() }
                        .accessibilityHidden(true)
                    inlinePanel(panel, size: geometry.size)
                        .accessibilityAddTraits(.isModal)
                }
            }
            .background(.black)
            .clipped()
            .onAppear {
                if coordinator.presentation == nil { coordinator.presentation = NativePlaybackPresentation(coordinator: coordinator) }
                presentation = coordinator.presentation
                coordinator.presentation?.setControlsVisible(controlsVisible)
                scheduleHide()
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("inline-player")
        .onChange(of: coordinator.wantsPlayback) { _, requested in
            if !requested { revealControls() }
            else { scheduleHide() }
        }
        .onChange(of: controlsVisible) { _, visible in presentation?.setControlsVisible(visible || voiceOver) }
        .onChange(of: voiceOver) { _, _ in revealControls() }
        .onChange(of: coordinator.backgroundPlayback) { _, _ in presentation?.refreshBackgroundConfiguration() }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { revealControls() }
            else {
                hideTask?.cancel()
                resetScrubbing()
                coordinator.endTemporaryRate()
            }
        }
        .onChange(of: holdingSpeed) { _, active in
            if active && scenePhase == .active {
                hideTask?.cancel()
                coordinator.beginTemporaryRate(2)
            } else {
                coordinator.endTemporaryRate()
                scheduleHide()
            }
        }
        .onChange(of: coordinator.currentPart?.id) { _, _ in
            resetScrubbing()
            coordinator.endTemporaryRate()
            revealControls()
        }
        .onChange(of: isExpanded) { _, _ in revealControls() }
        .onDisappear {
            hideTask?.cancel()
            noticeTask?.cancel()
            resetScrubbing()
            coordinator.endTemporaryRate()
            coordinator.flushProgress()
        }
        .accessibilityAction(named: Text("前进十五秒")) { coordinator.skip(15) }
        .accessibilityAction(named: Text("后退十五秒")) { coordinator.skip(-15) }
    }

    private func gestureSurface(width: CGFloat) -> some View {
        let supportsSkipping = controlsVisible || voiceOver
        return Color.clear
            .contentShape(Rectangle())
            .gesture(
                // When hidden, reveal immediately instead of waiting for the
                // double-tap timeout before the transport buttons can be hit.
                SpatialTapGesture(count: supportsSkipping ? 2 : 1)
                    .exclusively(before: SpatialTapGesture(count: 1))
                    .onEnded { result in
                        switch result {
                        case .first(let value):
                            guard supportsSkipping else {
                                revealControls()
                                return
                            }
                            let delta = value.location.x < width / 2 ? -15.0 : 15.0
                            coordinator.skip(delta)
                            showFeedback(delta < 0 ? "后退 15 秒" : "前进 15 秒")
                            scheduleHide()
                        case .second:
                            if controlsVisible && !voiceOver { setControlsVisible(false) }
                            else { revealControls() }
                        }
                    }
            )
            .simultaneousGesture(
                DragGesture(minimumDistance: 18)
                    .onChanged { value in
                        guard !holdingSpeed, seekableDuration > 0, scenePhase == .active else { return }
                        if scrubbing.dragAxis == nil {
                            scrubbing.dragAxis = PlayerGestureMath.dragAxis(horizontal: value.translation.width, vertical: value.translation.height)
                        }
                        guard scrubbing.dragAxis == .horizontal else { return }
                        if scrubbing.gestureStartTime == nil { scrubbing.gestureStartTime = coordinator.currentTime; hideTask?.cancel() }
                        scrubbing.seekPreview = PlayerGestureMath.scrubPosition(start: scrubbing.gestureStartTime ?? coordinator.currentTime,
                            translation: value.translation.width, width: width, duration: coordinator.duration)
                    }
                    .onEnded { _ in
                        if let preview = scrubbing.seekPreview, scrubbing.gestureStartTime != nil { coordinator.seek(to: preview) }
                        resetScrubbing()
                        scheduleHide()
                    }
            )
            .simultaneousGesture(
                LongPressGesture(minimumDuration: 0.35, maximumDistance: 22)
                    .sequenced(before: DragGesture(minimumDistance: 0))
                    .updating($holdingSpeed) { value, state, _ in
                        if case .second(true, _) = value, coordinator.isPlaying,
                           scenePhase == .active, scrubbing.dragAxis == nil { state = true }
                    }
            )
            .accessibilityHidden(true)
    }

    private func controls(size: CGSize, insets: EdgeInsets) -> some View {
        VStack(spacing: 0) {
            HStack(spacing: 4) {
                if isExpanded {
                    controlButton("收起播放器", symbol: "chevron.down", action: onToggleExpanded)
                        .accessibilityIdentifier("player-collapse")
                } else if let onBack {
                    controlButton("返回", symbol: "chevron.left", action: onBack)
                }
                if isExpanded {
                    Text(coordinator.currentVideo?.title ?? "正在播放")
                        .font(.subheadline.weight(.semibold)).lineLimit(1)
                }
                Spacer(minLength: 4)
                Button {
                    presentation?.togglePictureInPicture()
                    scheduleHide()
                } label: {
                    Image(uiImage: coordinator.isPictureInPictureActive
                          ? AVPictureInPictureController.pictureInPictureButtonStopImage
                          : AVPictureInPictureController.pictureInPictureButtonStartImage)
                        .renderingMode(.template).frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .disabled(presentation?.canStartPictureInPicture != true && !coordinator.isPictureInPictureActive)
                .opacity(presentation?.canStartPictureInPicture == true || coordinator.isPictureInPictureActive ? 1 : 0.4)
                .accessibilityLabel(coordinator.isPictureInPictureActive ? "退出画中画" : "画中画")
                NativeAirPlayButton(tint: .white).frame(width: 44, height: 44).accessibilityLabel("AirPlay 输出")
                moreMenu.frame(width: 44, height: 44)
            }
            .padding(.horizontal, max(8, max(insets.leading, insets.trailing)))
            .padding(.top, max(0, insets.top))
            .background(alignment: .top) {
                LinearGradient(colors: [.black.opacity(0.65), .clear], startPoint: .top, endPoint: .bottom)
                    .padding(.bottom, -20).allowsHitTesting(false)
            }
            Spacer(minLength: 0)
            VStack(spacing: 0) {
                PlayerProgressSlider(coordinator: coordinator, scrubbing: scrubbing,
                    onInteractionBegan: { hideTask?.cancel() }, onInteractionEnded: { scheduleHide() })
                HStack(spacing: 4) {
                    controlButton(coordinator.wantsPlayback ? "暂停" : "播放", symbol: coordinator.wantsPlayback ? "pause.fill" : "play.fill") {
                        coordinator.togglePlayback()
                        revealControls()
                    }
                    .accessibilityIdentifier("player-play-pause")
                    PlayerPlaybackClock(coordinator: coordinator, scrubbing: scrubbing, isExpanded: isExpanded)
                    Spacer(minLength: 2)
                    if isExpanded && size.width >= 500 && !dynamicTypeSize.isAccessibilitySize {
                        controlButton(coordinator.danmakuEnabled ? "关闭弹幕" : "显示弹幕", symbol: coordinator.danmakuEnabled ? "text.bubble.fill" : "text.bubble") {
                            coordinator.danmakuEnabled.toggle()
                            scheduleHide()
                        }
                    }
                    // The same choices remain in More when the overlay cannot
                    // fit them without shrinking the clock or transport targets.
                    if size.width >= (isExpanded ? 390 : 350) && !dynamicTypeSize.isAccessibilitySize { rateMenu }
                    if size.width >= (isExpanded ? 600 : 430) && dynamicTypeSize <= .xxxLarge { qualityMenu }
                    controlButton(isExpanded ? "退出全屏" : "展开播放器", symbol: isExpanded ? "arrow.down.right.and.arrow.up.left" : "arrow.up.left.and.arrow.down.right") {
                        coordinator.endTemporaryRate()
                        onToggleExpanded()
                    }.accessibilityIdentifier("player-fullscreen")
                }
            }
            .padding(.horizontal, max(isExpanded ? 18 : 10, max(insets.leading, insets.trailing)))
            .padding(.bottom, max(3, insets.bottom))
            .background {
                LinearGradient(colors: [.clear, .black.opacity(0.75)], startPoint: .top, endPoint: .bottom)
                    .padding(.top, -28).allowsHitTesting(false)
            }
        }
        .foregroundStyle(.white)
        .buttonStyle(PlayerOverlayButtonStyle { hideTask?.cancel() })
        .font(.system(size: 17, weight: .medium))
    }

    private var rateMenu: some View {
        Menu {
            rateChoices
        } label: {
            Text("\(Double(coordinator.preferredRate).formatted())×")
                .font(.subheadline.weight(.semibold)).frame(minWidth: 44, minHeight: 44)
                .contentShape(Rectangle())
        }.accessibilityLabel("播放速度")
            .simultaneousGesture(TapGesture().onEnded { hideTask?.cancel() })
    }

    private var rateChoices: some View {
        ForEach([0.5, 0.75, 1, 1.25, 1.5, 2.0], id: \.self) { rate in
            selectedMenuButton("\(rate.formatted())×", selected: abs(Double(coordinator.preferredRate) - rate) < 0.01) {
                coordinator.setRate(Float(rate)); revealControls()
            }
        }
    }

    private var qualityMenu: some View {
        Menu {
            qualityChoices
        } label: {
            Text(coordinator.currentVariant?.height.map { "\($0)p" } ?? "原画")
                .font(.caption.weight(.semibold)).frame(minWidth: 44, minHeight: 44)
                .contentShape(Rectangle())
        }.accessibilityLabel("分辨率与音轨")
            .simultaneousGesture(TapGesture().onEnded { hideTask?.cancel() })
    }

    private var qualityChoices: some View {
        ForEach(coordinator.variants) { variant in
            Button {
                Task { await coordinator.selectVariant(variant) }
                revealControls()
            } label: {
                if coordinator.currentVariant?.id == variant.id { Label(coordinator.variantLabel(variant), systemImage: "checkmark") }
                else { Text(coordinator.variantLabel(variant)) }
            }
        }
    }

    private var moreMenu: some View {
        Menu {
            Toggle("显示弹幕", isOn: $coordinator.danmakuEnabled)
            Menu("分辨率与音轨") { qualityChoices }
            Menu("速度") { rateChoices }
            Menu("字幕") {
                subtitleChoice("跟随系统", id: "auto")
                subtitleChoice("关闭", id: "off")
                ForEach(coordinator.embeddedSubtitleOptions) { option in
                    subtitleChoice(option.title, id: option.id)
                }
                ForEach(Array(coordinator.availableSubtitles.enumerated()), id: \.offset) { index, track in
                    subtitleChoice(track.label, id: "sidecar:\(index)")
                }
            }
            if !coordinator.audioOptions.isEmpty {
                Menu("内嵌音轨") {
                    selectedMenuButton("跟随系统", selected: coordinator.selectedAudio == "auto") {
                        coordinator.selectAudio("auto"); revealControls()
                    }
                    ForEach(coordinator.audioOptions) { option in
                        selectedMenuButton(option.title, selected: coordinator.selectedAudio == option.id) {
                            coordinator.selectAudio(option.id); revealControls()
                        }
                    }
                }
            }
            Toggle("填满画面", isOn: $coordinator.fitToFill)
            Toggle("响度平衡", isOn: $coordinator.loudnessBalance)
            Toggle("允许后台播放", isOn: $coordinator.backgroundPlayback)
            Menu("分发节点") {
                selectedMenuButton("自动选择", selected: coordinator.selectedRouteID.isEmpty) {
                    Task { await coordinator.selectRoute("") }; revealControls()
                }
                ForEach(coordinator.session?.routes ?? []) { route in
                    selectedMenuButton(route.name, selected: coordinator.selectedRouteID == route.id) {
                        Task { await coordinator.selectRoute(route.id) }; revealControls()
                    }
                        .disabled(route.status == "unavailable")
                }
            }
            Divider()
            Button("弹幕设置", systemImage: "text.bubble") { openPanel(.danmaku) }
            Button("播放信息", systemImage: "info.circle") { openPanel(.information) }
        } label: {
            Image(systemName: "ellipsis").frame(width: 44, height: 44)
                .contentShape(Rectangle())
        }.accessibilityLabel("更多播放选项")
            .simultaneousGesture(TapGesture().onEnded { hideTask?.cancel() })
    }

    private func subtitleChoice(_ title: String, id: String) -> some View {
        selectedMenuButton(title, selected: coordinator.selectedSubtitle == id) {
            Task { await coordinator.selectSubtitle(id) }
            revealControls()
        }
    }

    private func selectedMenuButton(_ title: String, selected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            if selected { Label(title, systemImage: "checkmark") }
            else { Text(title) }
        }
    }

    private func inlinePanel(_ panel: PlayerInlinePanel, size: CGSize) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(panel == .danmaku ? "弹幕设置" : "播放信息").font(.headline)
                Spacer()
                Button("关闭", systemImage: "xmark") { closePanel() }
                    .labelStyle(.iconOnly).frame(width: 44, height: 44)
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    if panel == .danmaku {
                        Toggle("显示弹幕", isOn: $coordinator.danmakuEnabled)
                        Text("透明度 \(Int(coordinator.danmakuOpacity * 100))%")
                        Slider(value: $coordinator.danmakuOpacity, in: 0.2...1)
                            .accessibilityLabel("弹幕透明度")
                        Text("字号 \(Int(coordinator.danmakuFontSize))")
                        Slider(value: $coordinator.danmakuFontSize, in: 14...26, step: 1)
                            .accessibilityLabel("弹幕字号")
                    } else {
                        Text(coordinator.sourceFeatures.joined(separator: " · ")).fontWeight(.medium)
                        Text(coordinator.deliveryDescription)
                        Text(coordinator.outputRoute)
                        Text(coordinator.renderingStatus)
                        Text(appPrompt("原档标签描述媒体格式，实际 HDR 与空间音频输出由设备和输出路线决定。"))
                        Text(appPrompt("独立字幕和弹幕仅显示在 App 画面中；PiP 与 AirPlay 只包含视频中的字幕。"))
                        if let message = coordinator.progressWarning ?? coordinator.ancillaryWarning ?? coordinator.statusMessage {
                            Text(appPrompt(message))
                        }
                        if let error = coordinator.errorMessage { Text(appPrompt(error)) }
                    }
                }.font(.footnote).frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(16)
        .frame(width: min(340, max(0, size.width - 28)), height: min(300, max(0, size.height - 24)))
        .background(.regularMaterial, in: .rect(cornerRadius: 18))
        .environment(\.colorScheme, .dark)
        .padding(12)
    }

    private func controlButton(_ title: String, symbol: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol).frame(width: 44, height: 44)
                .contentShape(Rectangle())
        }.accessibilityLabel(title)
    }

    private func openPanel(_ value: PlayerInlinePanel) {
        hideTask?.cancel()
        resetScrubbing()
        coordinator.endTemporaryRate()
        panel = value
    }
    private func closePanel() { panel = nil; revealControls() }
    private func resetScrubbing() { scrubbing.reset() }
    private func showFeedback(_ text: String) {
        feedback = text
        noticeTask?.cancel()
        noticeTask = Task { @MainActor in
            do { try await Task.sleep(for: .seconds(0.9)) } catch { return }
            feedback = nil
        }
    }
    private func setControlsVisible(_ value: Bool) {
        withAnimation(reduceMotion ? nil : .easeOut(duration: 0.18)) { controlsVisible = value }
    }
    private func revealControls() { setControlsVisible(true); scheduleHide() }
    private func scheduleHide() {
        hideTask?.cancel()
        guard scenePhase == .active, coordinator.wantsPlayback, coordinator.errorMessage == nil,
              !voiceOver, !holdingSpeed, !scrubbing.isSliderEditing, scrubbing.gestureStartTime == nil, panel == nil else { return }
        hideTask = Task { @MainActor in
            do { try await Task.sleep(for: .seconds(3.5)) } catch { return }
            guard scenePhase == .active, coordinator.wantsPlayback, coordinator.errorMessage == nil,
                  !voiceOver, !holdingSpeed, !scrubbing.isSliderEditing, panel == nil else { return }
            setControlsVisible(false)
        }
    }
}

private enum PlayerInlinePanel { case danmaku, information }

/// Preview changes belong to the progress/HUD views, not the video, controls or
/// open menus. Scrubbing still issues exactly one media seek when released.
@MainActor @Observable
private final class PlayerScrubbingState {
    var seekPreview: Double?
    var gestureStartTime: Double?
    var dragAxis: PlayerGestureMath.DragAxis?
    var isSliderEditing = false

    func reset() {
        seekPreview = nil
        gestureStartTime = nil
        dragAxis = nil
        isSliderEditing = false
    }
}

private struct PlayerProgressSlider: View {
    let coordinator: PlaybackCoordinator
    let scrubbing: PlayerScrubbingState
    let onInteractionBegan: () -> Void
    let onInteractionEnded: () -> Void
    @Environment(\.accessibilityVoiceOverEnabled) private var voiceOver

    private var duration: Double { coordinator.duration.isFinite ? max(0, coordinator.duration) : 0 }
    private var displayedTime: Double {
        PlayerGestureMath.clampedTime(scrubbing.seekPreview ?? coordinator.currentTime, duration: duration)
    }

    var body: some View {
        Slider(value: Binding(get: { displayedTime }, set: { value in
            if voiceOver { coordinator.seek(to: value); scrubbing.seekPreview = nil }
            else { scrubbing.seekPreview = value }
        }), in: 0...max(0.01, duration)) { editing in
            scrubbing.isSliderEditing = editing
            if editing {
                scrubbing.seekPreview = coordinator.currentTime
                onInteractionBegan()
            } else {
                if let preview = scrubbing.seekPreview { coordinator.seek(to: preview) }
                scrubbing.seekPreview = nil
                onInteractionEnded()
            }
        }
        .tint(.cyan)
        .disabled(duration <= 0)
        .accessibilityLabel("播放进度")
        .accessibilityValue("\(PlayerGestureMath.timeLabel(displayedTime))，共 \(PlayerGestureMath.timeLabel(duration))")
        .accessibilityIdentifier("player-progress")
        .frame(minHeight: 44)
    }
}

private struct PlayerPlaybackClock: View {
    let coordinator: PlaybackCoordinator
    let scrubbing: PlayerScrubbingState
    let isExpanded: Bool

    var body: some View {
        PlayerClockLabel(snapshot: PlayerClockSnapshot(position: scrubbing.seekPreview ?? coordinator.currentTime,
                                                       duration: coordinator.duration), isExpanded: isExpanded)
            .equatable()
    }
}

/// The small observer above receives progress ticks; string formatting and Text
/// construction below only change when the displayed whole-second value changes.
private struct PlayerClockLabel: View, Equatable {
    let snapshot: PlayerClockSnapshot
    let isExpanded: Bool

    var body: some View {
        Text("\(PlayerGestureMath.timeLabel(snapshot.position)) / \(PlayerGestureMath.timeLabel(snapshot.duration))")
            .font(.system(size: isExpanded ? 14 : 11, weight: .medium, design: .monospaced))
            .lineLimit(1).minimumScaleFactor(0.75)
            .accessibilityIdentifier("player-time")
    }
}

struct PlayerClockSnapshot: Equatable {
    let position: Double
    let duration: Double

    init(position: Double, duration: Double) {
        let duration = duration.isFinite ? min(359_999, max(0, duration)) : 0
        self.position = PlayerGestureMath.clampedTime(position, duration: duration).rounded(.down)
        self.duration = duration.rounded(.down)
    }
}

private struct PlayerInteractionFeedback: View {
    let coordinator: PlaybackCoordinator
    let scrubbing: PlayerScrubbingState
    let holdingSpeed: Bool
    let feedback: String?
    let topInset: CGFloat

    var body: some View {
        if holdingSpeed {
            pill("2× 快进中", symbol: "forward.fill")
                .frame(maxHeight: .infinity, alignment: .top)
                .padding(.top, max(12, topInset + 8))
                .allowsHitTesting(false)
        } else if let preview = scrubbing.seekPreview, !scrubbing.isSliderEditing {
            VStack(spacing: 8) {
                Image(systemName: preview >= (scrubbing.gestureStartTime ?? 0) ? "goforward" : "gobackward")
                    .font(.title2)
                Text(PlayerGestureMath.timeLabel(preview)).font(.title2.monospacedDigit().bold())
                Text(appPrompt("松手跳转 · 共 \(PlayerGestureMath.timeLabel(coordinator.duration))"))
                    .font(.caption)
            }.foregroundStyle(.white).padding(20)
                .background(.black.opacity(0.7), in: .rect(cornerRadius: 16))
                .allowsHitTesting(false)
        } else if let feedback {
            pill(feedback, symbol: "play.fill").allowsHitTesting(false)
        }
    }

    private func pill(_ text: String, symbol: String) -> some View {
        Label(appPrompt(text), systemImage: symbol).font(.subheadline.weight(.semibold))
            .foregroundStyle(.white).padding(.horizontal, 16).padding(.vertical, 10)
            .background(.black.opacity(0.65), in: .capsule)
    }
}

/// Pressing a transport control owns that touch until release. In particular,
/// the auto-hide timer must not remove a button while the finger is still down.
private struct PlayerOverlayButtonStyle: ButtonStyle {
    let onPress: () -> Void

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .contentShape(Rectangle())
            .opacity(configuration.isPressed ? 0.6 : 1)
            .onChange(of: configuration.isPressed) { _, pressed in
                if pressed { onPress() }
            }
    }
}

/// A full-width drag covers a bounded time window, so long recordings remain
/// controllable and very short clips never seek past their actual duration.
enum PlayerGestureMath {
    enum DragAxis: Equatable { case horizontal, vertical }

    /// The detail viewport already sits inside its navigation safe area. Its
    /// offset GeometryReader can still report the ancestor's obscured region;
    /// applying that inset again pushes the header into the transport controls.
    /// Only the edge-to-edge fullscreen host needs device safe-area padding.
    static func controlInsets(isExpanded: Bool, reported: EdgeInsets) -> EdgeInsets {
        isExpanded ? reported : EdgeInsets()
    }

    /// Visible controls own their full-width bands, including the transparent
    /// spaces between buttons. The video gestures never extend under a slider
    /// or transport target, regardless of native view hit-test ordering.
    static func gestureBounds(size: CGSize, insets: EdgeInsets, controlsVisible: Bool, largeText: Bool) -> CGRect {
        guard size.width.isFinite, size.height.isFinite, size.width > 0, size.height > 0 else { return .zero }
        guard controlsVisible else { return CGRect(origin: .zero, size: size) }
        let top = min(size.height, max(0, insets.top) + (largeText ? 88 : 44))
        let bottom = max(top, size.height - 88 - max(3, insets.bottom))
        return CGRect(x: 0, y: top, width: size.width, height: bottom - top)
    }

    /// Lock the initial direction, so a vertical page gesture cannot become an
    /// accidental seek when the finger drifts sideways later in the gesture.
    static func dragAxis(horizontal: Double, vertical: Double) -> DragAxis {
        guard horizontal.isFinite, vertical.isFinite else { return .vertical }
        return abs(horizontal) > abs(vertical) * 1.3 ? .horizontal : .vertical
    }

    static func clampedTime(_ time: Double, duration: Double) -> Double {
        guard time.isFinite, duration.isFinite, duration > 0 else { return 0 }
        return min(duration, max(0, time))
    }

    static func scrubPosition(start: Double, translation: Double, width: Double, duration: Double) -> Double {
        guard start.isFinite, translation.isFinite, width.isFinite, duration.isFinite, width > 0, duration > 0 else { return 0 }
        let window = min(180, max(30, duration * 0.2))
        return min(duration, max(0, start + translation / width * window))
    }
    static func timeLabel(_ seconds: Double) -> String {
        guard seconds.isFinite, seconds >= 0 else { return "00:00" }
        let value = Int(min(seconds, 359_999))
        if value >= 3_600 { return String(format: "%d:%02d:%02d", value / 3_600, value / 60 % 60, value % 60) }
        return String(format: "%02d:%02d", value / 60, value % 60)
    }
}
