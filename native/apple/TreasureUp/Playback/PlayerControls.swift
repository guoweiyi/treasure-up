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
    @State private var holdingSpeed = false
    @State private var controlPressed = false
    @State private var routePickerPresented = false

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
                gestureSurface(width: geometry.size.width, bounds: gestureBounds)
                    .frame(width: geometry.size.width, height: geometry.size.height)
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
                PlayerWaitingFeedback(isLoading: coordinator.isLoading, isBuffering: coordinator.isBuffering,
                                      wantsPlayback: coordinator.wantsPlayback, hasError: coordinator.errorMessage != nil)
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
                    inlinePanel(panel, size: geometry.size, insets: controlInsets)
                        .accessibilityAddTraits(.isModal)
                }
            }
            .background(.black)
            .clipped()
            .onAppear {
                if coordinator.presentation == nil { coordinator.presentation = NativePlaybackPresentation(coordinator: coordinator) }
                presentation = coordinator.presentation
                coordinator.presentation?.setControlsVisible(controlsVisible)
                controlPressed = false
                scheduleHide()
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("inline-player")
        .focusable()
        .focusEffectDisabled()
        .onKeyPress(keys: [.space, .leftArrow, .rightArrow, .escape]) { key in
            guard scenePhase == .active else { return .ignored }
            if key.key == .escape {
                if panel != nil { closePanel(); return .handled }
                if isExpanded { onToggleExpanded(); return .handled }
                return .ignored
            }
            guard panel == nil else { return .ignored }
            if key.key == .space { coordinator.togglePlayback(); revealControls() }
            else {
                let delta = key.key == .leftArrow ? -15.0 : 15.0
                coordinator.skip(delta)
                showFeedback(delta < 0 ? "后退 15 秒" : "前进 15 秒")
                revealControls()
            }
            return .handled
        }
        .onChange(of: coordinator.wantsPlayback) { _, requested in
            if !requested { revealControls() }
            else { scheduleHide() }
        }
        .onChange(of: controlsVisible) { _, visible in presentation?.setControlsVisible(visible || voiceOver) }
        .onChange(of: voiceOver) { _, _ in revealControls() }
        .onChange(of: coordinator.backgroundPlayback) { _, _ in presentation?.refreshBackgroundConfiguration() }
        .onChange(of: coordinator.isLoading || coordinator.isBuffering) { _, waiting in
            if waiting { revealControls() }
            else { scheduleHide() }
        }
        .onChange(of: coordinator.isSeeking) { _, seeking in
            if seeking { holdingSpeed = false; revealControls() }
            else { scheduleHide() }
        }
        .onChange(of: coordinator.errorMessage) { _, error in
            if error != nil { revealControls() }
            else { scheduleHide() }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { revealControls() }
            else {
                hideTask?.cancel()
                resetScrubbing()
                holdingSpeed = false
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
            holdingSpeed = false
            coordinator.endTemporaryRate()
            revealControls()
        }
        .onChange(of: isExpanded) { _, _ in revealControls() }
        .onDisappear {
            hideTask?.cancel()
            noticeTask?.cancel()
            feedback = nil
            controlPressed = false
            holdingSpeed = false
            resetScrubbing()
            coordinator.endTemporaryRate()
            coordinator.flushProgress()
        }
        .accessibilityAction(named: Text("前进十五秒")) { coordinator.skip(15) }
        .accessibilityAction(named: Text("后退十五秒")) { coordinator.skip(-15) }
    }

    private func gestureSurface(width: CGFloat, bounds: CGRect) -> some View {
        PlayerGestureSurface(activeBounds: bounds, controlsVisible: controlsVisible || voiceOver,
            isEnabled: panel == nil && scenePhase == .active && !controlPressed &&
                !scrubbing.isSliderEditing && !routePickerPresented,
            onReveal: { revealControls() },
            onToggleControls: {
                if controlsVisible && !voiceOver { setControlsVisible(false) }
                else { revealControls() }
            },
            onSkip: { delta in
                coordinator.skip(delta)
                showFeedback(delta < 0 ? "后退 15 秒" : "前进 15 秒")
                scheduleHide()
            },
            onScrub: { translation, phase in
                switch phase {
                case .began:
                    guard !holdingSpeed, seekableDuration > 0, scenePhase == .active else { return }
                    scrubbing.gestureStartTime = coordinator.currentTime
                    scrubbing.seekPreview = PlayerGestureMath.scrubPosition(start: coordinator.currentTime,
                        translation: translation, width: width, duration: coordinator.duration)
                    hideTask?.cancel()
                case .changed:
                    guard let start = scrubbing.gestureStartTime else { return }
                    scrubbing.seekPreview = PlayerGestureMath.scrubPosition(start: start,
                        translation: translation, width: width, duration: coordinator.duration)
                case .ended:
                    if let start = scrubbing.gestureStartTime {
                        coordinator.seek(to: PlayerGestureMath.scrubPosition(start: start,
                            translation: translation, width: width, duration: coordinator.duration))
                    }
                    resetScrubbing()
                    scheduleHide()
                case .cancelled:
                    resetScrubbing()
                    scheduleHide()
                }
            },
            onHold: { active in
                holdingSpeed = active && coordinator.isPlaying && !coordinator.isSeeking &&
                    scenePhase == .active && scrubbing.gestureStartTime == nil
            })
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
                NativeAirPlayButton(tint: .white, onPresent: {
                    routePickerPresented = true
                    hideTask?.cancel()
                }, onDismiss: {
                    routePickerPresented = false
                    revealControls()
                }).frame(width: 44, height: 44).accessibilityLabel("AirPlay 输出")
                controlButton("更多播放选项", symbol: "ellipsis") { openPanel(.settings) }
                    .accessibilityIdentifier("player-settings")
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
                    // Optional controls yield to the clock and 44pt transport
                    // buttons; their choices stay available in the same panel.
                    if size.width >= (isExpanded ? 390 : 350) && !dynamicTypeSize.isAccessibilitySize { rateButton }
                    if size.width >= (isExpanded ? 600 : 430) && dynamicTypeSize <= .xxxLarge { qualityButton }
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
        .buttonStyle(PlayerOverlayButtonStyle { pressed in
            controlPressed = pressed
            if pressed { hideTask?.cancel() }
            else { scheduleHide() }
        })
        .font(.system(size: 17, weight: .medium))
    }

    private var rateButton: some View {
        Button { openPanel(.speed) } label: {
            Text("\(Double(coordinator.preferredRate).formatted())×")
                .font(.subheadline.weight(.semibold)).frame(minWidth: 44, minHeight: 44)
                .contentShape(Rectangle())
        }.accessibilityLabel("播放速度")
            .accessibilityIdentifier("player-speed")
    }

    private var qualityButton: some View {
        Button { openPanel(.quality) } label: {
            Text(coordinator.currentVariant?.height.map { "\($0)p" } ?? "原画")
                .font(.caption.weight(.semibold)).frame(minWidth: 44, minHeight: 44)
                .contentShape(Rectangle())
        }.accessibilityLabel("分辨率与音轨")
    }

    private var speedChoices: some View {
        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8),
                                count: dynamicTypeSize.isAccessibilitySize ? 2 : 3), spacing: 8) {
            ForEach([0.5, 0.75, 1, 1.25, 1.5, 2.0], id: \.self) { rate in
                let selected = abs(Double(coordinator.preferredRate) - rate) < 0.01
                Button {
                    coordinator.setRate(Float(rate))
                    closePanel()
                    showFeedback("已切换为 \(rate.formatted())×")
                } label: {
                    Text("\(rate.formatted())×").font(.subheadline.weight(.semibold))
                        .frame(maxWidth: .infinity, minHeight: 44)
                        .background(selected ? Color.cyan.opacity(0.26) : Color.white.opacity(0.09), in: .rect(cornerRadius: 10))
                        .overlay(RoundedRectangle(cornerRadius: 10).stroke(selected ? .cyan : .clear, lineWidth: 1))
                        .contentShape(Rectangle())
                }
                .accessibilityLabel("\(rate.formatted()) 倍速")
                .accessibilityAddTraits(selected ? .isSelected : [])
            }
        }
    }

    private var qualityChoices: some View {
        VStack(spacing: 0) {
            ForEach(coordinator.variants) { variant in
                choiceRow(coordinator.variantLabel(variant), selected: coordinator.currentVariant?.id == variant.id) {
                    closePanel()
                    Task { await coordinator.selectVariant(variant) }
                }
            }
            if coordinator.variants.isEmpty {
                Text(appPrompt("暂无其他画质")).font(.subheadline).foregroundStyle(.secondary).padding(.vertical, 12)
            }
        }
    }

    private var subtitleChoices: some View {
        VStack(spacing: 0) {
            subtitleChoice("跟随系统", id: "auto")
            subtitleChoice("关闭", id: "off")
            ForEach(coordinator.embeddedSubtitleOptions) { option in subtitleChoice(option.title, id: option.id) }
            ForEach(Array(coordinator.availableSubtitles.enumerated()), id: \.offset) { index, track in
                subtitleChoice(track.label, id: "sidecar:\(index)")
            }
        }
    }

    private func subtitleChoice(_ title: String, id: String) -> some View {
        choiceRow(title, selected: coordinator.selectedSubtitle == id) {
            closePanel()
            Task { await coordinator.selectSubtitle(id) }
        }
    }

    private func choiceRow(_ title: String, selected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 12) {
                Text(title).multilineTextAlignment(.leading).fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                if selected { Image(systemName: "checkmark").foregroundStyle(.cyan) }
            }.font(.subheadline).padding(.vertical, 8).frame(maxWidth: .infinity, minHeight: 44)
                .contentShape(Rectangle())
        }.accessibilityAddTraits(selected ? .isSelected : [])
    }

    private func panelSection<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                .accessibilityAddTraits(.isHeader)
            content()
        }.frame(maxWidth: .infinity, alignment: .leading)
    }

    private var settingsContent: some View {
        LazyVStack(alignment: .leading, spacing: 20) {
            panelSection("播放速度") { speedChoices }
            panelSection("画质与音轨") { qualityChoices }
            panelSection("字幕") { subtitleChoices }
            if !coordinator.audioOptions.isEmpty {
                panelSection("声音") {
                    choiceRow("跟随系统", selected: coordinator.selectedAudio == "auto") {
                        coordinator.selectAudio("auto"); closePanel()
                    }
                    ForEach(coordinator.audioOptions) { option in
                        choiceRow(option.title, selected: coordinator.selectedAudio == option.id) {
                            coordinator.selectAudio(option.id); closePanel()
                        }
                    }
                }
            }
            panelSection("画面与播放") {
                Toggle("填满画面", isOn: $coordinator.fitToFill).frame(minHeight: 44)
                Toggle("响度平衡", isOn: $coordinator.loudnessBalance).frame(minHeight: 44)
                Toggle("允许后台播放", isOn: $coordinator.backgroundPlayback).frame(minHeight: 44)
            }
            panelSection("弹幕") {
                Toggle("显示弹幕", isOn: $coordinator.danmakuEnabled).frame(minHeight: 44)
                if coordinator.danmakuEnabled {
                    Text("透明度 \(Int(coordinator.danmakuOpacity * 100))%").font(.subheadline)
                    Slider(value: $coordinator.danmakuOpacity, in: 0.2...1).frame(minHeight: 44)
                        .accessibilityLabel("弹幕透明度")
                    Text("字号 \(Int(coordinator.danmakuFontSize))").font(.subheadline)
                    Slider(value: $coordinator.danmakuFontSize, in: 14...26, step: 1).frame(minHeight: 44)
                        .accessibilityLabel("弹幕字号")
                }
            }
            if !(coordinator.session?.routes.isEmpty ?? true) {
                panelSection("分发节点") {
                    choiceRow("自动选择", selected: coordinator.selectedRouteID.isEmpty) {
                        closePanel(); Task { await coordinator.selectRoute("") }
                    }
                    ForEach(coordinator.session?.routes ?? []) { route in
                        choiceRow(route.name, selected: coordinator.selectedRouteID == route.id) {
                            closePanel(); Task { await coordinator.selectRoute(route.id) }
                        }.disabled(route.status == "unavailable")
                    }
                }
            }
            panelSection("播放信息") {
                VStack(alignment: .leading, spacing: 8) {
                    Text(coordinator.sourceFeatures.joined(separator: " · ")).fontWeight(.medium)
                    Text(coordinator.deliveryDescription)
                    Text(coordinator.outputRoute)
                    Text(coordinator.renderingStatus)
                    if let message = coordinator.progressWarning ?? coordinator.ancillaryWarning ?? coordinator.statusMessage {
                        Text(appPrompt(message))
                    }
                }.font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
            }
        }
    }

    private func inlinePanel(_ panel: PlayerInlinePanel, size: CGSize, insets: EdgeInsets) -> some View {
        let geometry = PlayerPanelGeometry(size: size, insets: insets,
                                           preferredHeight: panel == .speed && !dynamicTypeSize.isAccessibilitySize ? 188 : 420)
        return VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Text(panel.title).font(.headline).lineLimit(1)
                    .dynamicTypeSize(...DynamicTypeSize.xxxLarge)
                    .accessibilityAddTraits(.isHeader)
                Spacer(minLength: 0)
                controlButton("关闭", symbol: "xmark") { closePanel() }
                    .accessibilityIdentifier("player-panel-close")
            }.frame(height: 44)
            ScrollView {
                Group {
                    switch panel {
                    case .speed: speedChoices
                    case .quality: qualityChoices
                    case .settings: settingsContent
                    }
                }.frame(maxWidth: .infinity, alignment: .leading)
            }.scrollBounceBehavior(.basedOnSize)
        }
        .padding(12)
        .frame(width: geometry.panelSize.width, height: geometry.panelSize.height)
        .background(.regularMaterial, in: .rect(cornerRadius: 18))
        .overlay(RoundedRectangle(cornerRadius: 18).stroke(.white.opacity(0.1), lineWidth: 1))
        .foregroundStyle(.white).tint(.cyan).buttonStyle(.plain)
        .environment(\.colorScheme, .dark)
        .padding(.leading, geometry.insets.leading).padding(.trailing, geometry.insets.trailing)
        .padding(.top, geometry.insets.top).padding(.bottom, geometry.insets.bottom)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("player-options-panel")
        .accessibilityAction(.escape) { closePanel() }
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
        holdingSpeed = false
        coordinator.endTemporaryRate()
        panel = value
        setControlsVisible(true)
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
    private var permitsAutoHide: Bool {
        scenePhase == .active && controlsVisible && coordinator.wantsPlayback && coordinator.errorMessage == nil &&
        !coordinator.isLoading && !coordinator.isBuffering && !coordinator.isSeeking && !voiceOver && !holdingSpeed &&
        !controlPressed && !routePickerPresented && !scrubbing.isSliderEditing && scrubbing.gestureStartTime == nil && panel == nil
    }
    private func scheduleHide() {
        hideTask?.cancel()
        guard permitsAutoHide else { return }
        hideTask = Task { @MainActor in
            do { try await Task.sleep(for: .seconds(3.5)) } catch { return }
            guard permitsAutoHide else { return }
            setControlsVisible(false)
        }
    }
}

private enum PlayerInlinePanel {
    case speed, quality, settings
    var title: String {
        switch self {
        case .speed: "播放速度"
        case .quality: "画质与音轨"
        case .settings: "播放设置"
        }
    }
}

/// Preview changes belong to the progress/HUD views, not the video, controls or
/// open menus. Scrubbing still issues exactly one media seek when released.
@MainActor @Observable
private final class PlayerScrubbingState {
    var seekPreview: Double?
    var gestureStartTime: Double?
    var isSliderEditing = false

    func reset() {
        seekPreview = nil
        gestureStartTime = nil
        isSliderEditing = false
    }
}

private struct PlayerProgressSlider: View {
    let coordinator: PlaybackCoordinator
    let scrubbing: PlayerScrubbingState
    let onInteractionBegan: () -> Void
    let onInteractionEnded: () -> Void

    private var duration: Double { coordinator.duration.isFinite ? max(0, coordinator.duration) : 0 }
    private var position: Double {
        // The native thumb owns its own drag. A picture pan instead previews
        // through this input, without replacing the slider's cancellation clock.
        if !scrubbing.isSliderEditing, scrubbing.gestureStartTime != nil {
            return scrubbing.seekPreview ?? coordinator.currentTime
        }
        return coordinator.currentTime
    }
    var body: some View {
        PlayerSeekBar(value: position, duration: duration, bufferedTime: coordinator.bufferedTime,
                      mediaID: coordinator.currentPart?.id ?? coordinator.currentVideo?.id,
                      onPreview: { scrubbing.seekPreview = $0 },
                      onEditingChanged: { editing in
                          scrubbing.isSliderEditing = editing
                          if editing { onInteractionBegan() }
                          else { scrubbing.seekPreview = nil; onInteractionEnded() }
                      }, onCommit: { coordinator.seek(to: $0) })
            .frame(height: 44)
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
    let onPressChanged: (Bool) -> Void

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .contentShape(Rectangle())
            .opacity(configuration.isPressed ? 0.6 : 1)
            .onChange(of: configuration.isPressed) { _, pressed in
                onPressChanged(pressed)
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
