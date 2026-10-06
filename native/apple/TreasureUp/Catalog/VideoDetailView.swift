import SwiftUI

private enum VideoPage: String, CaseIterable, Identifiable {
    case info = "简介", comments = "评论", queue = "队列"
    var id: Self { self }
}

struct VideoDetailView: View {
    @Environment(APIClient.self) private var api
    @Environment(PlaybackCoordinator.self) private var playback
    @Environment(\.dismiss) private var dismiss
    let videoId: String
    var queueContext: PlaybackQueueContext?
    @State private var video: ArchiveVideo?
    @State private var error: String?
    @State private var savePresented = false
    @State private var loginPresented = false
    @State private var actionBusy = false
    @State private var lifetime = VideoPageLifetime()
    @State private var fullscreen = false
    @State private var playTask: Task<Void, Never>?
    @State private var followsPlayback = false
    @State private var isPageVisible = false
    @State private var page: VideoPage = .info
    @State private var visitedPages: Set<VideoPage> = [.info]
    @State private var starredOverrides: [String: Bool] = [:]

    private var displayedVideo: ArchiveVideo? {
        var result = followsPlayback && isPageVisible ? playback.currentVideo ?? video : video
        if let id = result?.id, let starred = starredOverrides[id] { result?.starred = starred }
        return result
    }
    private var isCurrent: Bool { isPageVisible && displayedVideo?.id == playback.currentVideo?.id && playback.currentVideo != nil }

    var body: some View {
        Group {
            if let video = displayedVideo {
                VideoDetailViewport(aspectRatio: inlineAspectRatio) {
                    playerSurface(video)
                } details: {
                    VStack(spacing: 0) {
                        Picker("视频内容", selection: $page) {
                            ForEach(VideoPage.allCases) { Text($0.rawValue).tag($0) }
                        }.pickerStyle(.segmented).accessibilityIdentifier("videoSections")
                            .padding(12)
                        Divider()
                        // Keep visited sections mounted so switching back preserves
                        // comment searches, expanded replies and each scroll position.
                        ZStack(alignment: .top) {
                            ForEach(VideoPage.allCases) { section in
                                if visitedPages.contains(section) {
                                    ScrollView {
                                        sectionContent(section, video: video)
                                            .padding(16).frame(maxWidth: 800).frame(maxWidth: .infinity)
                                    }
                                    .id(section == .queue ? "playback-queue" : video.id)
                                    .scrollDismissesKeyboard(.interactively)
                                    .opacity(page == section ? 1 : 0)
                                    .allowsHitTesting(page == section)
                                    .accessibilityHidden(page != section)
                                    .zIndex(page == section ? 1 : 0)
                                }
                            }
                        }
                    }
                }
            } else if let error { FailureView(message: error) { await load() } }
            else { ProgressView(appPrompt("正在读取视频…")) }
        }
        .navigationTitle("").navigationBarTitleDisplayMode(.inline)
        .background {
            FullscreenPlayerPresenter(isPresented: $fullscreen, playback: playback, lifetime: lifetime)
                .frame(width: 0, height: 0)
        }
        .onAppear {
            isPageVisible = true
            followsPlayback = playback.currentVideo?.id == (video?.id ?? videoId)
            if video == nil && followsPlayback { video = playback.currentVideo }
        }
        .onDisappear {
            // UIKit full-screen presentation covers this destination without leaving it.
            guard !fullscreen, !savePresented, !loginPresented, !lifetime.isCoveredByPresentation else { return }
            playTask?.cancel()
            if followsPlayback {
                if let current = playback.currentVideo { video = current }
                playback.stop()
            }
            followsPlayback = false
            isPageVisible = false
        }
        .onChange(of: page) { _, section in visitedPages.insert(section) }
        .onChange(of: playback.currentVideo?.id) { _, _ in
            if playback.currentVideo == nil { fullscreen = false }
            if isPageVisible && followsPlayback, let current = playback.currentVideo { video = current }
        }
        .task(id: videoId) {
            if video == nil { await load() }
        }
        .sheet(isPresented: $savePresented) { NavigationStack { SaveToPlaylistView(videoId: displayedVideo?.id ?? videoId) } }
        .sheet(isPresented: $loginPresented) { NavigationStack { LoginView() } }
    }

    private var inlineAspectRatio: CGFloat {
        guard isCurrent else { return 16.0 / 9.0 }
        let size = playback.player.currentItem?.presentationSize ?? .zero
        if size.width.isFinite, size.height.isFinite, size.width > 0, size.height > 0 {
            return size.width / size.height
        }
        let variant = playback.currentVariant
        let metadata = playback.session?.media ?? variant?.metadata
        if let width = metadata?.width ?? variant?.width,
           let height = metadata?.height ?? variant?.height, width > 0, height > 0 {
            return CGFloat(width) / CGFloat(height)
        }
        return 16.0 / 9.0
    }

    @ViewBuilder private func sectionContent(_ section: VideoPage, video: ArchiveVideo) -> some View {
        switch section {
        case .info:
            LazyVStack(alignment: .leading, spacing: 20) {
                metadata(video)
                parts(video)
                RelatedVideosView(videoId: video.id, creatorId: video.creators.first?.id)
                Link(destination: URL(string: "https://github.com/guoweiyi/treasure-up")!) {
                    Label("Treasure Up · 开源项目", systemImage: "chevron.left.forwardslash.chevron.right")
                        .font(.footnote)
                }.padding(.top, 12)
            }
        case .comments:
            VideoCommentsSection(videoId: video.id, isActive: page == .comments && !fullscreen && isPageVisible)
        case .queue:
            PlaybackQueueSection(shouldCancelPlaybackOnDisappear: {
                !fullscreen && !savePresented && !loginPresented && !lifetime.isCoveredByPresentation
            }) { followsPlayback = true }
        }
    }

    @ViewBuilder private func playerSurface(_ video: ArchiveVideo) -> some View {
        if isCurrent {
            InlineNativePlayer(coordinator: playback,
                               onToggleExpanded: { fullscreen = true }, onBack: { dismiss() })
        } else {
            ZStack {
                Artwork(path: video.coverUrl)
                Color.black.opacity(0.15)
                Button { play(video) } label: {
                    Image(systemName: "play.fill").font(.title).frame(width: 60, height: 60)
                }.nativeGlassButton(prominent: true).buttonBorderShape(.circle)
                    .disabled(!video.playable).accessibilityLabel("播放视频").accessibilityIdentifier("playVideo")
            }.clipped()
        }
    }
    private func metadata(_ video: ArchiveVideo) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(video.title).font(.title3.bold()).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            ForEach(uniqueCreators(video.creators)) { creator in
                NavigationLink { CreatorDetailView(creator: creator) } label: {
                    HStack(spacing: 12) {
                        Artwork(path: creator.avatarUrl, symbol: "person.fill").frame(width: 44, height: 44).clipShape(.circle)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(creator.name).font(.subheadline.weight(.semibold)).foregroundStyle(.primary)
                            Text(creator.roleTitle ?? "投稿 UP 主")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Image(systemName: "chevron.right").font(.caption).foregroundStyle(.tertiary)
                    }
                }.buttonStyle(.plain)
            }
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 14) { stats(video) }
                VStack(alignment: .leading, spacing: 8) { stats(video) }
            }.font(.caption).foregroundStyle(.secondary)
            HStack(spacing: 12) {
                Button(video.starred ? "已星标" : "星标", systemImage: video.starred ? "star.fill" : "star") {
                    if api.user == nil { loginPresented = true } else { Task { await toggleStar(video) } }
                }.disabled(actionBusy)
                Button("收藏", systemImage: "bookmark") { if api.user == nil { loginPresented = true } else { savePresented = true } }
                Spacer()
                if let url = api.resolveURL("/videos/\(video.id)") { ShareLink(item: url).labelStyle(.iconOnly) }
            }.buttonStyle(.bordered).font(.subheadline)
            if !video.playable { Label(appPrompt("视频尚未完成归档"), systemImage: "clock.badge.exclamationmark").foregroundStyle(.orange) }
            if !video.description.isEmpty { Text(video.description).font(.subheadline).textSelection(.enabled) }
            if let notes = video.notes, !notes.isEmpty { Label(notes, systemImage: "note.text").font(.subheadline).foregroundStyle(.secondary) }
            if !video.tags.isEmpty {
                Text(video.tags.map { "#" + $0 }.joined(separator: "  ")).font(.caption).foregroundStyle(.secondary)
            }
            if let error { Text(appPrompt(error)).font(.callout).foregroundStyle(.red) }
        }
    }
    private func uniqueCreators(_ creators: [ArchiveCreator]) -> [ArchiveCreator] {
        var people: [ArchiveCreator] = []
        var indexes: [String: Int] = [:]
        var roles: [String: [String]] = [:]
        for creator in creators {
            let key = creator.id.isEmpty ? (creator.uid ?? creator.name) : creator.id
            let title = creator.roleTitle?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            let role = title.isEmpty ? (creator.role == "staff" ? "联合投稿" : "投稿 UP 主") : title
            if !roles[key, default: []].contains(role) { roles[key, default: []].append(role) }
            if indexes[key] == nil { indexes[key] = people.count; people.append(creator) }
            if let index = indexes[key] { people[index].roleTitle = roles[key]?.joined(separator: " · ") }
        }
        return people
    }
    @ViewBuilder private func stats(_ video: ArchiveVideo) -> some View {
        if let views = video.stats?.view { Label(archiveCount(views), systemImage: "play.rectangle") }
        if let comments = video.stats?.reply { Label(archiveCount(comments), systemImage: "bubble.left") }
        Label(mediaDuration(video.duration), systemImage: "clock")
        if let date = video.publishedAt { Text(String(date.prefix(10))) }
    }
    private func parts(_ video: ArchiveVideo) -> some View {
        LazyVStack(alignment: .leading, spacing: 10) {
            Divider()
            HStack {
                Text("选集 · \(video.parts.count)").font(.headline)
                Spacer()
                Button("加入队列", systemImage: "text.badge.plus") { playback.enqueue(video: video); page = .queue }
                    .font(.subheadline).disabled(!video.playable)
            }
            ForEach(video.parts) { part in
                let selected = isCurrent && playback.currentPart?.id == part.id
                Button { play(video, part: part) } label: {
                    HStack(spacing: 12) {
                        Image(systemName: selected ? "waveform" : "play.circle").frame(width: 24)
                        Text("P\(part.position)  \(part.title)").lineLimit(2)
                        Spacer(minLength: 6)
                        Text(part.variants.isEmpty ? "未归档" : mediaDuration(part.duration)).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                    }.font(.subheadline).padding(12).frame(maxWidth: .infinity, alignment: .leading)
                        .background(selected ? Color.accentColor.opacity(0.1) : Color.secondary.opacity(0.07), in: .rect(cornerRadius: 12))
                }.buttonStyle(.plain).foregroundStyle(selected ? Color.accentColor : .primary).disabled(part.variants.isEmpty)
            }
        }
    }
    private func play(_ video: ArchiveVideo, part: VideoPart? = nil) {
        followsPlayback = true
        playTask?.cancel()
        playTask = Task {
            guard !Task.isCancelled, isPageVisible else { return }
            // A resumed player has no new catalog scope; keep its established queue.
            let context = queueContext ?? (playback.currentVideo == nil ? PlaybackQueueContext(title: "资料库") : nil)
            await playback.start(video: video, part: part, context: context)
        }
    }
    private func load() async {
        error = nil
        do {
            let response: ArchiveVideo = try await api.get("/videos/\(videoId)")
            guard !Task.isCancelled else { return }
            video = response
        }
        catch { if !Task.isCancelled { self.error = error.localizedDescription } }
    }
    private func toggleStar(_ current: ArchiveVideo) async {
        guard !actionBusy else { return }
        actionBusy = true; defer { actionBusy = false }
        do {
            try await api.mutate("/videos/\(current.id)/star", method: "PUT", body: ["starred": .bool(!current.starred)])
            starredOverrides[current.id] = !current.starred
        } catch { self.error = error.localizedDescription }
    }
}

struct RelatedVideosView: View {
    @Environment(APIClient.self) private var api
    let videoId: String
    var creatorId: String?
    @State private var items: [ArchiveVideo] = []
    @State private var relatedContext = PlaybackQueueContext(title: "更多视频")
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if !items.isEmpty {
                Divider()
                Text("更多视频").font(.headline)
                ForEach(items) { video in
                    NavigationLink { VideoDetailView(videoId: video.id, queueContext: relatedContext) } label: {
                        HStack(spacing: 12) {
                            Artwork(path: video.coverUrl).frame(width: 118, height: 70).clipShape(.rect(cornerRadius: 10))
                            VStack(alignment: .leading, spacing: 6) {
                                Text(video.title).font(.subheadline).lineLimit(2).foregroundStyle(.primary)
                                Text(video.creators.first?.name ?? "已归档视频").font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer(minLength: 0)
                        }
                    }.buttonStyle(.plain)
                }
            }
        }.task(id: videoId) {
            do {
                var query = ["view": "card", "page_size": "7", "sort": "newest"]
                if let creatorId { query["creator_id"] = creatorId }
                var result: Page<ArchiveVideo> = try await api.get("/videos", query: query)
                if result.items.filter({ $0.id != videoId }).isEmpty {
                    query.removeValue(forKey: "creator_id")
                    result = try await api.get("/videos", query: query)
                }
                guard !Task.isCancelled else { return }
                var contextQuery = query
                contextQuery.removeValue(forKey: "page_size")
                contextQuery.removeValue(forKey: "view")
                relatedContext = PlaybackQueueContext(title: "更多视频", query: contextQuery)
                items = Array(result.items.filter { $0.id != videoId }.prefix(6))
            } catch { if !Task.isCancelled { items = [] } }
        }
    }
}
