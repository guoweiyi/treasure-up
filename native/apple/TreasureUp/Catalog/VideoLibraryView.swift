import SwiftUI

struct VideoLibraryView: View {
    @Environment(APIClient.self) private var api
    @Environment(PlaybackCoordinator.self) private var playback
    var title = "资料库"
    var collectionId: String?
    var creatorId: String?
    var starredOnly = false
    var creatorProfile: ArchiveCreator?
    @Environment(\.horizontalSizeClass) private var sizeClass
    @Environment(\.dynamicTypeSize) private var typeSize
    @State private var videos: [ArchiveVideo] = []
    @State private var query = ""
    @State private var sort = "newest"
    @State private var tag = ""
    @State private var total = 0
    @State private var page = 1
    @State private var loading = false
    @State private var error: String?
    @State private var filters = false
    @State private var generation = 0
    private var columns: [GridItem] {
        if typeSize.isAccessibilitySize { return [GridItem(.flexible())] }
        if sizeClass == .compact { return Array(repeating: GridItem(.flexible(), spacing: 12, alignment: .top), count: 2) }
        return [GridItem(.adaptive(minimum: 220, maximum: 340), spacing: 16, alignment: .top)]
    }
    private var queueContext: PlaybackQueueContext {
        var parameters = ["q": query, "sort": sort, "tag": tag]
        if let collectionId { parameters["collection_id"] = collectionId }
        if let creatorId { parameters["creator_id"] = creatorId }
        if starredOnly { parameters["starred"] = "true" }
        return PlaybackQueueContext(title: query.isEmpty ? title : "搜索：\(query)", query: parameters)
    }
    private var searchKey: String { [query, sort, tag, collectionId ?? "", creatorId ?? "", String(starredOnly)].joined(separator: "|") }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                if let creatorProfile { CreatorProfileHeader(creator: creatorProfile) }
                HStack(alignment: .firstTextBaseline) {
                    VStack(alignment: .leading, spacing: 5) {
                        Text(query.isEmpty ? (creatorProfile == nil ? "你的珍藏" : "已归档作品") : "搜索结果").font(.title3.bold())
                        Text("\(total) 个已保存视频").font(.subheadline).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Menu {
                        Picker("排序", selection: $sort) {
                            Text("最近保存").tag("newest")
                            Text("最新发布").tag("published")
                            Text("最早保存").tag("oldest")
                            Text("标题").tag("title")
                            Text("时长").tag("duration")
                        }
                        Button("筛选标签", systemImage: "tag") { filters = true }
                        if !tag.isEmpty { Button("清除标签筛选") { tag = "" } }
                    } label: { Label("筛选", systemImage: "line.3.horizontal.decrease") }
                        .nativeGlassButton()
                }
                if !tag.isEmpty { Label(tag, systemImage: "tag").font(.subheadline).foregroundStyle(.secondary) }
                if let error { FailureView(message: error) { await load() } }
                if loading && videos.isEmpty { ProgressView().frame(maxWidth: .infinity).padding(60) }
                else if videos.isEmpty && error == nil {
                    ContentUnavailableView(query.isEmpty ? "还没有视频" : "没有匹配的视频", systemImage: "rectangle.stack", description: Text("调整搜索或筛选条件，或在管理中心添加归档来源。"))
                }
                LazyVGrid(columns: columns, alignment: .leading, spacing: 20) {
                    ForEach(videos) { video in
                        NavigationLink { VideoDetailView(videoId: video.id, queueContext: queueContext) } label: { VideoCard(video: video) }
                            .buttonStyle(.plain)
                            .contextMenu {
                                Button("播放", systemImage: "play") { Task { await playback.start(video: video, context: queueContext); playback.requestsPresentation = true } }
                                    .disabled(!video.playable)
                                Button("加入播放队列", systemImage: "text.line.first.and.arrowtriangle.forward") { playback.enqueue(video: video) }
                                if let url = api.resolveURL("/videos/\(video.id)") { ShareLink(item: url) }
                            }
                    }
                }
                if !videos.isEmpty { PaginationFooter(count: videos.count, total: total, busy: loading) { await load(more: true) } }
            }.padding(16).frame(maxWidth: 1600).frame(maxWidth: .infinity)
        }
        .navigationTitle(title)
        .searchable(text: $query, prompt: "搜索标题、简介、UP 主")
        .refreshable { await load() }
        .task(id: searchKey) {
            do { try await Task.sleep(for: .milliseconds(query.isEmpty ? 0 : 300)); try Task.checkCancellation(); await load() }
            catch { }
        }
        .alert("筛选标签", isPresented: $filters) {
            TextField("标签", text: $tag)
            Button("完成") { }
        } message: { Text("输入归档视频的标签名称。") }
    }

    private func load(more: Bool = false) async {
        if more && loading { return }
        generation += 1
        let current = generation
        let target = more ? page + 1 : 1
        loading = true
        error = nil
        var params = ["view": "card", "q": query, "sort": sort, "tag": tag, "page": String(target), "page_size": "24"]
        if let collectionId { params["collection_id"] = collectionId }
        if let creatorId { params["creator_id"] = creatorId }
        if starredOnly { params["starred"] = "true" }
        do {
            let result: Page<ArchiveVideo> = try await api.get("/videos", query: params)
            guard generation == current, !Task.isCancelled else { return }
            videos = more ? videos + result.items : result.items
            total = result.total
            page = target
        } catch {
            if generation == current && !Task.isCancelled { self.error = error.localizedDescription }
        }
        if generation == current { loading = false }
    }
}

struct CollectionsView: View {
    @Environment(APIClient.self) private var api
    @State private var items: [ArchiveCollection] = []
    @State private var error: String?
    @State private var loading = false
    @State private var total = 0
    @State private var page = 0
    var body: some View {
        List {
            Section {
                ForEach(items) { item in
                    NavigationLink {
                        VideoLibraryView(title: item.title, collectionId: item.id)
                    } label: {
                        HStack(spacing: 16) {
                            Artwork(path: item.coverUrl, symbol: "square.stack.fill")
                                .frame(width: 104, height: 70).clipShape(.rect(cornerRadius: 12))
                            VStack(alignment: .leading, spacing: 6) {
                                Text(item.title).font(.headline)
                                Text("\(kind(item.kind)) · \(item.savedCount) 个视频").font(.subheadline).foregroundStyle(.secondary)
                                if !item.enabled { Text("自动归档已暂停").font(.caption).foregroundStyle(.secondary) }
                            }
                        }.padding(.vertical, 6)
                    }
                }
            } header: { Text("\(total) 个来源") } footer: { Text("归档的收藏夹、合集和 UP 主订阅，集中整理于此。") }
            if let error { FailureView(message: error) { await load() } }
            if loading { ProgressView().frame(maxWidth: .infinity) }
            if items.count < total { Button("载入更多") { Task { await load(more: true) } }.disabled(loading) }
        }
        .overlay { if !loading && error == nil && items.isEmpty { ContentUnavailableView("暂无收藏与订阅", systemImage: "square.stack") } }
        .navigationTitle("收藏与订阅")
        .task { await load() }.refreshable { await load() }
    }
    private func kind(_ kind: String) -> String {
        ["creator": "UP 主订阅", "favorite": "收藏夹", "favorites": "收藏夹", "series": "系列", "ugc_season": "合集"][kind] ?? "视频集合"
    }
    private func load(more: Bool = false) async {
        guard !loading else { return }; loading = true; error = nil
        defer { loading = false }
        do {
            let next = more ? page + 1 : 1
            let data: Page<ArchiveCollection> = try await api.get("/collections", query: ["page": String(next), "page_size": "40"])
            items = more ? items + data.items : data.items; total = data.total; page = next
        } catch { self.error = error.localizedDescription }
    }
}

struct CreatorsView: View {
    @Environment(APIClient.self) private var api
    @State private var items: [ArchiveCreator] = []
    @State private var query = ""
    @State private var error: String?
    @State private var total = 0
    @State private var page = 0
    @State private var loading = false
    var body: some View {
        List {
            ForEach(items) { creator in
                NavigationLink { CreatorDetailView(creator: creator) } label: {
                    HStack(spacing: 16) {
                        Artwork(path: creator.avatarUrl, symbol: "person.fill").frame(width: 60, height: 60).clipShape(.circle)
                        VStack(alignment: .leading, spacing: 5) {
                            Text(creator.name).font(.headline)
                            Text("\(creator.savedCount) 个已保存视频").font(.caption).foregroundStyle(.secondary)
                            if let bio = creator.description, !bio.isEmpty { Text(bio).font(.subheadline).foregroundStyle(.secondary).lineLimit(2) }
                        }
                    }.padding(.vertical, 5)
                }
            }
            if let error { FailureView(message: error) { await load() } }
            if loading { ProgressView().frame(maxWidth: .infinity) }
            if items.count < total { Button("载入更多") { Task { await load(more: true) } }.disabled(loading) }
        }
        .overlay { if items.isEmpty && !loading && error == nil { ContentUnavailableView.search(text: query) } }
        .navigationTitle("UP 主")
        .searchable(text: $query, prompt: "搜索 UP 主")
        .task(id: query) {
            do { try await Task.sleep(for: .milliseconds(300)); try Task.checkCancellation(); await load() } catch { }
        }
        .refreshable { await load() }
    }
    private func load(more: Bool = false) async {
        let key = query; loading = true; error = nil
        defer { if key == query { loading = false } }
        do {
            let next = more ? page + 1 : 1
            let data: Page<ArchiveCreator> = try await api.get("/creators", query: ["q": query, "page": String(next), "page_size": "40"])
            guard key == query, !Task.isCancelled else { return }
            items = more ? items + data.items : data.items; total = data.total; page = next
        } catch { if key == query && !Task.isCancelled { self.error = error.localizedDescription } }
    }
}

struct CreatorDetailView: View {
    @Environment(APIClient.self) private var api
    let creator: ArchiveCreator
    @State private var profile: ArchiveCreator?
    var body: some View {
        let current = profile ?? creator
        VideoLibraryView(title: current.name, creatorId: current.id, creatorProfile: current)
            .task(id: creator.id) { do { profile = try await api.get("/creators/\(creator.id)") } catch { } }
    }
}

private struct CreatorProfileHeader: View {
    let creator: ArchiveCreator
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 16) {
                Artwork(path: creator.avatarUrl, symbol: "person.fill")
                    .frame(width: 76, height: 76).clipShape(.circle)
                VStack(alignment: .leading, spacing: 6) {
                    Text(creator.name).font(.title2.bold())
                    Text("\(creator.savedCount) 个已保存视频").font(.subheadline).foregroundStyle(.secondary)
                    if let uid = creator.uid { Text("UID \(uid)").font(.caption).foregroundStyle(.tertiary) }
                }
            }
            if let description = creator.description, !description.isEmpty {
                Text(description).font(.body).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            }
            if let notes = creator.notes, !notes.isEmpty {
                Label(notes, systemImage: "note.text").font(.subheadline).foregroundStyle(.secondary)
            }
            if !creator.tags.isEmpty { Text(creator.tags.map { "#" + $0 }.joined(separator: "  ")).font(.subheadline).foregroundStyle(.secondary) }
            Divider()
        }.padding(.vertical, 8)
    }
}
