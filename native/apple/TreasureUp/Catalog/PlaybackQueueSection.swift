import SwiftUI

struct PlaybackQueueSection: View {
    @Environment(PlaybackCoordinator.self) private var playback
    @State private var playbackTask: Task<Void, Never>?
    @State private var retryTask: Task<Void, Never>?
    var shouldCancelPlaybackOnDisappear: () -> Bool = { true }
    var onStartPlayback: () -> Void = {}
    var body: some View {
        @Bindable var playback = playback
        // Keep the list lazy all the way to its enclosing ScrollView. Large
        // library queues should not decode every offscreen cover on first visit.
        LazyVStack(alignment: .leading, spacing: 16) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text(playback.queueTitle).font(.headline)
                    Text("\(playback.queue.count) 个视频").font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Menu {
                    Button("清空并停止播放", systemImage: "trash", role: .destructive) { cancelPageTasks(); playback.clearQueue() }
                } label: { Image(systemName: "ellipsis").frame(width: 44, height: 44) }
                    .accessibilityLabel("队列操作").disabled(playback.queue.isEmpty)
            }
            Picker("播完当前视频后", selection: $playback.queueMode) {
                ForEach(PlaybackQueueMode.allCases) { mode in Label(mode.title, systemImage: mode.systemImage).tag(mode) }
            }.pickerStyle(.menu)
            Toggle("自动衔接分 P", isOn: $playback.autoAdvance).font(.subheadline)
            Text(appPrompt(playback.autoAdvance ? "全部分 P 播完后，按所选模式暂停、循环或播放下一项。" : "每个分 P 结束时暂停。可随时手动切换。"))
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Button("上一项", systemImage: "backward.end") { startPlayback { await playback.previousPart() } }
                    .disabled(!playback.hasPrevious || playback.isLoading || playback.queueTransitioning)
                Spacer()
                Button("下一项", systemImage: "forward.end") { startPlayback { await playback.nextPart() } }
                    .disabled(!playback.hasNext || playback.isLoading || playback.queueTransitioning)
            }.buttonStyle(.bordered).font(.subheadline)
            if playback.queueLoading { ProgressView(appPrompt("正在读取完整队列…")).font(.caption) }
            if let error = playback.queueError {
                Text(appPrompt(error)).font(.caption).foregroundStyle(.red)
                Button("重试加载队列") { retryLoading() }
            }
            if playback.queue.isEmpty {
                ContentUnavailableView(appPrompt("队列为空"), systemImage: "text.line.first.and.arrowtriangle.forward", description: Text(appPrompt("播放视频会带入当前列表，也可以从视频菜单添加。")))
            }
            ForEach(Array(playback.queue.enumerated()), id: \.element.id) { index, video in
                PlaybackQueueRow(video: video, index: index) {
                    startPlayback { await playback.playQueueItem(id: video.id) }
                }
            }
            if playback.queue.count > 1 { Text(appPrompt("长按拖动可调整顺序，更多菜单中也可移动或移除。")).font(.caption).foregroundStyle(.secondary) }
        }.onDisappear { if shouldCancelPlaybackOnDisappear() { cancelPageTasks() } }
    }
    private func startPlayback(_ action: @escaping @MainActor () async -> Void) {
        playbackTask?.cancel()
        retryTask?.cancel()
        playbackTask = Task {
            guard !Task.isCancelled else { return }
            onStartPlayback()
            await action()
        }
    }

    private func retryLoading() {
        retryTask?.cancel()
        retryTask = Task {
            guard !Task.isCancelled else { return }
            await playback.retryQueueLoading()
        }
    }

    private func cancelPageTasks() {
        playbackTask?.cancel(); playbackTask = nil
        retryTask?.cancel(); retryTask = nil
    }
}

/// Isolate frequent transport-state changes from the queue's enumeration and
/// settings. Only mounted rows observe their own playback label and controls.
private struct PlaybackQueueRow: View {
    @Environment(PlaybackCoordinator.self) private var playback
    let video: ArchiveVideo
    let index: Int
    var onPlay: () -> Void

    var body: some View {
        let active = video.id == playback.currentVideo?.id
        HStack(spacing: 10) {
            Button(action: onPlay) {
                HStack(spacing: 10) {
                    Artwork(path: video.coverUrl).frame(width: 84, height: 52).clipShape(.rect(cornerRadius: 8))
                    VStack(alignment: .leading, spacing: 5) {
                        Text(video.title).font(.subheadline.weight(active ? .semibold : .regular)).lineLimit(2)
                        HStack(spacing: 5) {
                            if active { Image(systemName: playback.isPlaying ? "waveform" : "pause.fill") }
                            Text(active ? "\(activeState) · P\(playback.currentPart?.position ?? 1)" : "\(index + 1) · \(video.creators.first?.name ?? "归档视频")")
                        }.font(.caption).foregroundStyle(active ? Color.accentColor : .secondary).lineLimit(1)
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }
            }.buttonStyle(.plain).disabled(playback.isLoading || playback.queueTransitioning)
            Menu {
                Button("上移", systemImage: "arrow.up") {
                    move(direction: -1)
                }.disabled(index == 0)
                Button("下移", systemImage: "arrow.down") {
                    move(direction: 1)
                }.disabled(index + 1 == playback.queue.count)
                Button(active ? "移除并停止当前播放" : "移出队列", systemImage: "minus.circle", role: .destructive) {
                    playback.removeFromQueue(id: video.id)
                }
            } label: { Image(systemName: "ellipsis").frame(width: 44, height: 44) }
                .accessibilityLabel("\(video.title) 的队列操作")
        }.padding(10).background(active ? Color.accentColor.opacity(0.08) : Color.secondary.opacity(0.05), in: .rect(cornerRadius: 12))
            .accessibilityIdentifier("queueItem-\(video.id)")
            .draggable(video.id)
            .dropDestination(for: String.self) { ids, _ in
                guard let id = ids.first,
                      let source = playback.queue.firstIndex(where: { $0.id == id }),
                      let destination = playback.queue.firstIndex(where: { $0.id == video.id }),
                      source != destination else { return false }
                playback.moveQueueItems(fromOffsets: IndexSet(integer: source),
                                        toOffset: destination > source ? destination + 1 : destination)
                return true
            }
    }
    private var activeState: String {
        if playback.errorMessage != nil { return "播放失败" }
        if playback.isLoading || playback.isBuffering { return "缓冲中" }
        return playback.isPlaying ? "正在播放" : "已暂停"
    }

    private func move(direction: Int) {
        // Resolve the identity when the action executes; another drag or an
        // automatic queue update may have moved this row while its menu was open.
        guard let source = playback.queue.firstIndex(where: { $0.id == video.id }),
              playback.queue.indices.contains(source + direction) else { return }
        playback.moveQueueItems(fromOffsets: IndexSet(integer: source),
                                toOffset: direction > 0 ? source + 2 : source - 1)
    }
}
