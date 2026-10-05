import SwiftUI
import AVKit

/// A single native video surface with controls that stay in the detail page.
/// The caller owns the frame and expanded layout; no modal player is presented.
struct InlineNativePlayer: View {
    @Bindable var coordinator: PlaybackCoordinator
    var isExpanded = false
    let onToggleExpanded: () -> Void
    var onBack: (() -> Void)? = nil

    @Environment(\.accessibilityVoiceOverEnabled) private var voiceOver
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var controlsVisible = true
    @State private var presentation: NativePlaybackPresentation?
    @State private var hideTask: Task<Void, Never>?
    @State private var noticeTask: Task<Void, Never>?
    @State private var seekPreview: Double?
    @State private var gestureStartTime: Double?
    @State private var isSliderEditing = false
    @State private var feedback: String?
    @State private var panel: PlayerInlinePanel?
    @GestureState private var holdingSpeed = false

    private var displayedTime: Double { seekPreview ?? coordinator.currentTime }

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                NativePlayerView(coordinator: coordinator)
                gestureSurface(width: geometry.size.width)
                if coordinator.isPictureInPictureActive {
                    VStack(spacing: 10) {
                        Image(systemName: "pip.fill").font(.largeTitle)
                        Text("正在画中画中播放").font(.subheadline)
                    }.foregroundStyle(.white.opacity(0.8)).allowsHitTesting(false)
                }
                if controlsVisible || voiceOver {
                    controls(size: geometry.size, insets: geometry.safeAreaInsets)
                        .transition(.opacity)
                }
                if holdingSpeed {
                    feedbackPill("2× 快进中", symbol: "forward.fill")
                        .frame(maxHeight: .infinity, alignment: .top)
                        .padding(.top, max(12, geometry.safeAreaInsets.top + 8))
                } else if let preview = seekPreview, !isSliderEditing {
                    VStack(spacing: 8) {
                        Image(systemName: preview >= (gestureStartTime ?? 0) ? "goforward" : "gobackward")
                            .font(.title2)
                        Text(PlayerGestureMath.timeLabel(preview)).font(.title2.monospacedDigit().bold())
                        Text("松手跳转 · 共 \(PlayerGestureMath.timeLabel(coordinator.duration))")
                            .font(.caption)
                    }.foregroundStyle(.white).padding(20)
                        .background(.black.opacity(0.7), in: .rect(cornerRadius: 16))
                        .allowsHitTesting(false)
                } else if let feedback {
                    feedbackPill(feedback, symbol: "play.fill").allowsHitTesting(false)
                }
                if coordinator.isLoading || coordinator.isBuffering {
                    ProgressView().tint(.white).controlSize(.large)
                        .padding(18).background(.black.opacity(0.35), in: .circle)
                        .allowsHitTesting(false)
                }
                if let error = coordinator.errorMessage {
                    VStack(spacing: 10) {
                        Label("播放遇到问题", systemImage: "exclamationmark.triangle")
                            .font(.subheadline.bold())
                        Text(error).font(.caption).lineLimit(isExpanded ? 4 : 2)
                        Button("重新尝试") { Task { await coordinator.retry() } }
                            .buttonStyle(.borderedProminent).tint(.white.opacity(0.25))
                    }.foregroundStyle(.white).multilineTextAlignment(.center)
                        .padding(16).frame(maxWidth: 380)
                        .background(.black.opacity(0.82), in: .rect(cornerRadius: 16))
                        .padding(20)
                }
                if let panel { inlinePanel(panel, size: geometry.size) }
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
        .accessibilityIdentifier("inline-player")
        .onChange(of: coordinator.isPlaying) { _, playing in
            if !playing { revealControls() }
            else { scheduleHide() }
        }
        .onChange(of: controlsVisible) { _, visible in presentation?.setControlsVisible(visible || voiceOver) }
        .onChange(of: coordinator.backgroundPlayback) { _, _ in presentation?.refreshBackgroundConfiguration() }
        .onChange(of: holdingSpeed) { _, active in
            if active {
                hideTask?.cancel()
                coordinator.beginTemporaryRate(2)
            } else {
                coordinator.endTemporaryRate()
                scheduleHide()
            }
        }
        .onChange(of: coordinator.currentPart?.id) { _, _ in
            seekPreview = nil
            gestureStartTime = nil
            isSliderEditing = false
            coordinator.endTemporaryRate()
            revealControls()
        }
        .onChange(of: isExpanded) { _, _ in revealControls() }
        .onDisappear {
            hideTask?.cancel()
            noticeTask?.cancel()
            seekPreview = nil
            gestureStartTime = nil
            isSliderEditing = false
            coordinator.endTemporaryRate()
            coordinator.flushProgress()
        }
        .accessibilityAction(named: Text("前进十五秒")) { coordinator.skip(15) }
        .accessibilityAction(named: Text("后退十五秒")) { coordinator.skip(-15) }
    }

    private func gestureSurface(width: CGFloat) -> some View {
        Color.clear
            .contentShape(Rectangle())
            .gesture(
                SpatialTapGesture(count: 2)
                    .exclusively(before: SpatialTapGesture(count: 1))
                    .onEnded { result in
                        switch result {
                        case .first(let value):
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
                        guard !holdingSpeed, coordinator.duration > 0,
                              abs(value.translation.width) > abs(value.translation.height) * 1.3 else { return }
                        if gestureStartTime == nil { gestureStartTime = coordinator.currentTime; hideTask?.cancel() }
                        seekPreview = PlayerGestureMath.scrubPosition(start: gestureStartTime ?? coordinator.currentTime,
                            translation: value.translation.width, width: width, duration: coordinator.duration)
                    }
                    .onEnded { _ in
                        if let preview = seekPreview, gestureStartTime != nil { coordinator.seek(to: preview) }
                        seekPreview = nil
                        gestureStartTime = nil
                        scheduleHide()
                    }
            )
            .simultaneousGesture(
                LongPressGesture(minimumDuration: 0.35, maximumDistance: 22)
                    .sequenced(before: DragGesture(minimumDistance: 0))
                    .updating($holdingSpeed) { value, state, _ in
                        if case .second(true, _) = value, coordinator.isPlaying { state = true }
                    }
            )
            .accessibilityHidden(true)
    }

    private func controls(size: CGSize, insets: EdgeInsets) -> some View {
        VStack(spacing: 0) {
            HStack(spacing: 4) {
                if isExpanded {
                    controlButton("收起播放器", symbol: "chevron.down", action: onToggleExpanded)
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
                Slider(value: Binding(get: { displayedTime }, set: { value in
                    if voiceOver { coordinator.seek(to: value); seekPreview = nil }
                    else { seekPreview = value }
                }),
                       in: 0...max(0.01, coordinator.duration)) { editing in
                    isSliderEditing = editing
                    if editing { seekPreview = coordinator.currentTime; hideTask?.cancel() }
                    else {
                        if let preview = seekPreview { coordinator.seek(to: preview) }
                        seekPreview = nil
                        scheduleHide()
                    }
                }
                .tint(.cyan)
                .disabled(coordinator.duration <= 0)
                .accessibilityLabel("播放进度")
                .accessibilityValue("\(PlayerGestureMath.timeLabel(displayedTime))，共 \(PlayerGestureMath.timeLabel(coordinator.duration))")
                .accessibilityIdentifier("player-progress")
                .frame(height: isExpanded ? 36 : 28)
                HStack(spacing: 4) {
                    controlButton(coordinator.isPlaying ? "暂停" : "播放", symbol: coordinator.isPlaying ? "pause.fill" : "play.fill") {
                        coordinator.togglePlayback()
                        revealControls()
                    }
                    Text("\(PlayerGestureMath.timeLabel(displayedTime)) / \(PlayerGestureMath.timeLabel(coordinator.duration))")
                        .font(.system(size: isExpanded ? 14 : 11, weight: .medium, design: .monospaced))
                        .lineLimit(1).minimumScaleFactor(0.75)
                        .accessibilityIdentifier("player-time")
                    Spacer(minLength: 2)
                    if isExpanded {
                        controlButton(coordinator.danmakuEnabled ? "关闭弹幕" : "显示弹幕", symbol: coordinator.danmakuEnabled ? "text.bubble.fill" : "text.bubble") {
                            coordinator.danmakuEnabled.toggle()
                            scheduleHide()
                        }
                    }
                    if size.width >= 350 { rateMenu }
                    if size.width >= 390 { qualityMenu }
                    controlButton(isExpanded ? "退出全屏" : "展开播放器", symbol: isExpanded ? "arrow.down.right.and.arrow.up.left" : "arrow.up.left.and.arrow.down.right") {
                        coordinator.endTemporaryRate()
                        onToggleExpanded()
                    }
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
        .buttonStyle(.plain)
        .font(.system(size: 17, weight: .medium))
    }

    private var rateMenu: some View {
        Menu {
            ForEach([0.5, 0.75, 1, 1.25, 1.5, 2.0], id: \.self) { rate in
                Button {
                    coordinator.setRate(Float(rate)); revealControls()
                } label: {
                    if abs(Double(coordinator.preferredRate) - rate) < 0.01 { Label("\(rate.formatted())×", systemImage: "checkmark") }
                    else { Text("\(rate.formatted())×") }
                }
            }
        } label: {
            Text("\(Double(coordinator.preferredRate).formatted())×")
                .font(.subheadline.weight(.semibold)).frame(minWidth: 44, minHeight: 44)
        }.accessibilityLabel("播放速度")
            .simultaneousGesture(TapGesture().onEnded { hideTask?.cancel() })
    }

    private var qualityMenu: some View {
        Menu {
            qualityChoices
        } label: {
            Text(coordinator.currentVariant?.height.map { "\($0)p" } ?? "原画")
                .font(.caption.weight(.semibold)).frame(minWidth: 44, minHeight: 44)
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
            Menu("速度") {
                ForEach([0.5, 0.75, 1, 1.25, 1.5, 2.0], id: \.self) { rate in
                    Button("\(rate.formatted())×") { coordinator.setRate(Float(rate)); revealControls() }
                }
            }
            Menu("字幕") {
                Button("跟随系统") { Task { await coordinator.selectSubtitle("auto") } }
                Button("关闭") { Task { await coordinator.selectSubtitle("off") } }
                ForEach(coordinator.embeddedSubtitleOptions) { option in
                    Button(option.title) { Task { await coordinator.selectSubtitle(option.id) } }
                }
                ForEach(Array(coordinator.availableSubtitles.enumerated()), id: \.offset) { index, track in
                    Button(track.label) { Task { await coordinator.selectSubtitle("sidecar:\(index)") } }
                }
            }
            if !coordinator.audioOptions.isEmpty {
                Menu("内嵌音轨") {
                    Button("跟随系统") { coordinator.selectAudio("auto") }
                    ForEach(coordinator.audioOptions) { option in
                        Button(option.title) { coordinator.selectAudio(option.id) }
                    }
                }
            }
            Toggle("填满画面", isOn: $coordinator.fitToFill)
            Toggle("响度平衡", isOn: $coordinator.loudnessBalance)
            Toggle("允许后台播放", isOn: $coordinator.backgroundPlayback)
            Menu("分发节点") {
                Button("自动选择") { Task { await coordinator.selectRoute("") } }
                ForEach(coordinator.session?.routes ?? []) { route in
                    Button(route.name) { Task { await coordinator.selectRoute(route.id) } }
                        .disabled(route.status == "unavailable")
                }
            }
            Divider()
            Button("弹幕设置", systemImage: "text.bubble") { openPanel(.danmaku) }
            Button("播放信息", systemImage: "info.circle") { openPanel(.information) }
        } label: {
            Image(systemName: "ellipsis").frame(width: 44, height: 44)
        }.accessibilityLabel("更多播放选项")
            .simultaneousGesture(TapGesture().onEnded { hideTask?.cancel() })
    }

    private func inlinePanel(_ panel: PlayerInlinePanel, size: CGSize) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(panel == .danmaku ? "弹幕设置" : "播放信息").font(.headline)
                Spacer()
                Button("关闭", systemImage: "xmark") { self.panel = nil; revealControls() }
                    .labelStyle(.iconOnly).frame(width: 44, height: 44)
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    if panel == .danmaku {
                        Toggle("显示弹幕", isOn: $coordinator.danmakuEnabled)
                        Text("透明度 \(Int(coordinator.danmakuOpacity * 100))%")
                        Slider(value: $coordinator.danmakuOpacity, in: 0.2...1)
                        Text("字号 \(Int(coordinator.danmakuFontSize))")
                        Slider(value: $coordinator.danmakuFontSize, in: 14...26, step: 1)
                    } else {
                        Text(coordinator.sourceFeatures.joined(separator: " · ")).fontWeight(.medium)
                        Text(coordinator.deliveryDescription)
                        Text(coordinator.outputRoute)
                        Text(coordinator.renderingStatus)
                        Text("原档标签描述媒体格式，实际 HDR 与空间音频输出由设备和输出路线决定。")
                        Text("独立字幕和弹幕仅显示在 App 画面中；PiP 与 AirPlay 只包含视频中的字幕。")
                        if let message = coordinator.progressWarning ?? coordinator.ancillaryWarning ?? coordinator.statusMessage {
                            Text(message)
                        }
                        if let error = coordinator.errorMessage { Text(error) }
                    }
                }.font(.footnote).frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(16)
        .frame(width: min(340, max(200, size.width - 28)), height: min(300, max(130, size.height - 24)))
        .background(.regularMaterial, in: .rect(cornerRadius: 18))
        .environment(\.colorScheme, .dark)
        .padding(12)
    }

    private func controlButton(_ title: String, symbol: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol).frame(width: 44, height: 44)
        }.accessibilityLabel(title)
    }

    private func feedbackPill(_ text: String, symbol: String) -> some View {
        Label(text, systemImage: symbol).font(.subheadline.weight(.semibold))
            .foregroundStyle(.white).padding(.horizontal, 16).padding(.vertical, 10)
            .background(.black.opacity(0.65), in: .capsule)
    }

    private func openPanel(_ value: PlayerInlinePanel) {
        hideTask?.cancel()
        panel = value
    }
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
        guard coordinator.isPlaying, !voiceOver, !holdingSpeed, !isSliderEditing, gestureStartTime == nil, panel == nil else { return }
        hideTask = Task { @MainActor in
            do { try await Task.sleep(for: .seconds(3.5)) } catch { return }
            guard coordinator.isPlaying, !isSliderEditing, panel == nil else { return }
            setControlsVisible(false)
        }
    }
}

private enum PlayerInlinePanel { case danmaku, information }

/// A full-width drag covers a bounded time window, so long recordings remain
/// controllable and very short clips never seek past their actual duration.
enum PlayerGestureMath {
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
