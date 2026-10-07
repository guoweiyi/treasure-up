import SwiftUI

struct SavedPlaylistsView: View {
    @Environment(APIClient.self) private var api
    @State private var lists: [PersonalPlaylist] = []
    @State private var error: String?
    @State private var loading = false
    @State private var loginPresented = false
    @State private var createPresented = false
    @State private var name = ""
    @State private var description = ""
    var body: some View {
        Group {
            if api.user == nil {
                ContentUnavailableView {
                    BrandMark(size: 76)
                    Text(appPrompt("收藏属于你的好视频"))
                } description: { Text(appPrompt("登录后创建片单，记录观看状态与收藏笔记。")) } actions: {
                    Button("登录") { loginPresented = true }.buttonStyle(.borderedProminent)
                }
            } else {
                List {
                    Section { NavigationLink { VideoLibraryView(title: "我的星标", starredOnly: true) } label: { Label("我的星标", systemImage: "star.fill").foregroundStyle(.orange) } }
                    Section("我的片单") {
                        ForEach(lists) { list in
                            NavigationLink { PlaylistDetailView(playlist: list) } label: {
                                HStack(spacing: 14) {
                                    Image(systemName: list.kind == "watch_later" ? "clock.fill" : "rectangle.stack.fill")
                                        .font(.title2).foregroundStyle(TreasureBrand.accent).frame(width: 44, height: 48)
                                    VStack(alignment: .leading, spacing: 5) {
                                        Text(list.name).font(.headline)
                                        Text("\(list.itemCount) 个视频 · \(list.unwatchedCount) 个未看").font(.caption).foregroundStyle(.secondary)
                                        if !list.description.isEmpty { Text(list.description).font(.subheadline).foregroundStyle(.secondary).lineLimit(2) }
                                    }
                                }.padding(.vertical, 5)
                            }
                        }
                    }
                    if let error { FailureView(message: error) { await load() } }
                    if loading { ProgressView().frame(maxWidth: .infinity) }
                }.refreshable { await load() }
            }
        }
        .navigationTitle("我的片单")
        .toolbar { if api.user != nil { Button("新建片单", systemImage: "plus") { name = ""; description = ""; createPresented = true } } }
        .task(id: api.user?.id) { if api.user != nil { await load() } }
        .sheet(isPresented: $loginPresented) { NavigationStack { LoginView() } }
        .sheet(isPresented: $createPresented) {
            NavigationStack {
                Form {
                    TextField("片单名称", text: $name)
                    TextField("描述", text: $description, axis: .vertical).lineLimit(3...6)
                    if let error { Text(appPrompt(error)).foregroundStyle(.red) }
                }.navigationTitle("新建片单")
                    .toolbar {
                        ToolbarItem(placement: .cancellationAction) { Button("取消") { createPresented = false } }
                        ToolbarItem(placement: .confirmationAction) {
                            Button("创建") { Task { await create() } }.disabled(loading || name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                        }
                    }
            }.presentationDetents([.medium, .large])
        }
    }
    private func load() async {
        loading = true; error = nil; defer { loading = false }
        do { let result: PlaylistListResponse = try await api.get("/me/playlists"); lists = result.items }
        catch { self.error = error.localizedDescription }
    }
    private func create() async {
        loading = true; error = nil
        do {
            try await api.mutate("/me/playlists", body: ["name": .string(name.trimmingCharacters(in: .whitespacesAndNewlines)), "description": .string(description)])
            createPresented = false; await load()
        } catch { self.error = error.localizedDescription }
        loading = false
    }
}

private struct PlaylistListResponse: Decodable, Sendable { let items: [PersonalPlaylist] }

struct SaveToPlaylistView: View {
    @Environment(APIClient.self) private var api
    @Environment(\.dismiss) private var dismiss
    let videoId: String
    @State private var lists: [PersonalPlaylist] = []
    @State private var membership: Set<String> = []
    @State private var busy = false
    @State private var error: String?
    var body: some View {
        List {
            Section("选择片单") {
                ForEach(lists) { list in
                    Button { Task { await toggle(list) } } label: {
                        HStack {
                            Label(list.name, systemImage: list.kind == "watch_later" ? "clock" : "rectangle.stack")
                            Spacer()
                            if membership.contains(list.id) { Image(systemName: "checkmark.circle.fill") }
                        }.padding(.vertical, 8)
                    }.disabled(busy)
                }
            }
            if busy { ProgressView() }
            if let error { Text(appPrompt(error)).foregroundStyle(.red) }
        }.navigationTitle("存入片单").toolbar { Button("完成") { dismiss() } }
            .task {
                busy = true; defer { busy = false }
                do {
                    async let listResult: PlaylistListResponse = api.get("/me/playlists")
                    async let membershipResult: JSONValue = api.get("/me/videos/\(videoId)/playlists")
                    let (result, ids) = try await (listResult, membershipResult)
                    lists = result.items; membership = Set(ids["playlist_ids"]?.arrayValue?.compactMap(\.stringValue) ?? [])
                } catch { self.error = error.localizedDescription }
            }
    }
    private func toggle(_ list: PersonalPlaylist) async {
        busy = true; error = nil; defer { busy = false }
        let removing = membership.contains(list.id)
        do {
            try await api.mutate("/me/playlists/\(list.id)/videos/\(videoId)", method: removing ? "DELETE" : "PUT")
            if removing { membership.remove(list.id) } else { membership.insert(list.id) }
        } catch { self.error = error.localizedDescription }
    }
}

struct PlaylistDetailView: View {
    @Environment(APIClient.self) private var api
    @Environment(PlaybackCoordinator.self) private var playback
    @Environment(\.dismiss) private var dismiss
    let playlist: PersonalPlaylist
    @State private var items: [ArchiveVideo] = []
    @State private var total = 0
    @State private var page = 0
    @State private var query = ""
    @State private var watched = "all"
    @State private var error: String?
    @State private var busy = false
    @State private var editing = false
    @State private var deleteConfirmation = false
    @State private var name = ""
    @State private var description = ""
    @State private var selected: ArchiveVideo?
    @State private var removal: ArchiveVideo?
    @State private var note = ""
    @State private var moveTarget = ""
    @State private var destinations: [PersonalPlaylist] = []
    var body: some View {
        List {
            Section {
                Picker("观看状态", selection: $watched) {
                    Text("全部").tag("all"); Text("未看").tag("unwatched"); Text("已看").tag("watched")
                }.pickerStyle(.segmented)
                Button("播放当前列表", systemImage: "play.fill") {
                    let playable = items.filter(\.playable)
                    guard let first = playable.first else { return }
                    Task { await playback.start(video: first, context: queueContext); playback.requestsPresentation = true }
                }.disabled(!items.contains(where: \.playable))
            }
            ForEach(items) { video in
                NavigationLink { VideoDetailView(videoId: video.id, queueContext: queueContext) } label: {
                    HStack(spacing: 12) {
                        Artwork(path: video.coverUrl).frame(width: 110, height: 68).clipShape(.rect(cornerRadius: 10))
                        VStack(alignment: .leading, spacing: 6) {
                            Text(video.title).font(.headline).lineLimit(2)
                            HStack {
                                Text(mediaDuration(video.duration))
                                if video.playlistItem?.watched == true { Label("已看", systemImage: "checkmark.circle.fill") }
                            }.font(.caption).foregroundStyle(.secondary)
                            if let note = video.playlistItem?.note, !note.isEmpty { Text(note).font(.caption).foregroundStyle(.secondary).lineLimit(1) }
                        }
                    }.padding(.vertical, 5)
                }
                .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                    Button("移除", role: .destructive) { removal = video }
                    Button("编辑") { select(video) }.tint(TreasureBrand.accent)
                }
                .swipeActions(edge: .leading) {
                    Button(video.playlistItem?.watched == true ? "标为未看" : "标为已看", systemImage: "checkmark") {
                        Task { await update(video, body: ["watched": .bool(!(video.playlistItem?.watched ?? false))]) }
                    }.tint(.green)
                }
                .contextMenu {
                    Button("编辑笔记或移动", systemImage: "square.and.pencil") { select(video) }
                    Button("加入播放队列", systemImage: "text.badge.plus") { playback.enqueue(video: video) }
                    Button("移出片单", role: .destructive) { removal = video }
                }
            }
            if let error { FailureView(message: error) { await load() } }
            if busy { ProgressView().frame(maxWidth: .infinity) }
            if items.count < total { Button("载入更多") { Task { await load(more: true) } }.disabled(busy) }
        }
        .navigationTitle(name.isEmpty ? playlist.name : name)
        .searchable(text: $query, prompt: "搜索片单")
        .toolbar { if playlist.kind != "watch_later" { Button("编辑片单", systemImage: "ellipsis") { if name.isEmpty { name = playlist.name; description = playlist.description }; editing = true } } }
        .task(id: query + "|" + watched) {
            do { try await Task.sleep(for: .milliseconds(250)); try Task.checkCancellation(); await load() } catch { }
        }.refreshable { await load() }
        .sheet(isPresented: $editing) { playlistEditor }
        .sheet(item: $selected) { video in itemEditor(video) }
        .confirmationDialog(appPrompt("删除片单？已归档的视频仍会保留。"), isPresented: $deleteConfirmation, titleVisibility: .visible) {
            Button("删除片单", role: .destructive) { Task { await deletePlaylist() } }
        }
        .confirmationDialog(appPrompt("从片单移除此视频？"), isPresented: Binding(get: { removal != nil }, set: { if !$0 { removal = nil } }), titleVisibility: .visible) {
            if let video = removal { Button("移出片单", role: .destructive) { Task { await update(video, method: "DELETE") }; removal = nil } }
        }
    }
    private var queueContext: PlaybackQueueContext {
        var context = PlaybackQueueContext.personal(id: playlist.id, title: name.isEmpty ? playlist.name : name)
        context.query["q"] = query
        context.query["watched"] = watched
        return context
    }
    private var playlistEditor: some View {
        NavigationStack {
            Form {
                TextField("名称", text: $name)
                TextField("描述", text: $description, axis: .vertical).lineLimit(3...6)
                Button("删除片单", role: .destructive) { editing = false; deleteConfirmation = true }
                if let error { Text(appPrompt(error)).foregroundStyle(.red) }
            }.navigationTitle("编辑片单").toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("取消") { editing = false } }
                ToolbarItem(placement: .confirmationAction) { Button("保存") { Task { await savePlaylist() } }.disabled(busy || name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty) }
            }
        }.presentationDetents([.medium, .large])
    }
    private func itemEditor(_ video: ArchiveVideo) -> some View {
        NavigationStack {
            Form {
                Section("收藏笔记") { TextField("写下收藏它的理由", text: $note, axis: .vertical).lineLimit(4...8) }
                Section("移动到") {
                    Picker("目标片单", selection: $moveTarget) {
                        Text("保留在当前片单").tag("")
                        ForEach(destinations.filter { $0.id != playlist.id }) { Text($0.name).tag($0.id) }
                    }
                }
                if let error { Text(appPrompt(error)).foregroundStyle(.red) }
            }.navigationTitle("编辑收藏").toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("取消") { selected = nil } }
                ToolbarItem(placement: .confirmationAction) { Button("保存") { Task { await saveItem(video) } }.disabled(busy) }
            }.task {
                do { let result: PlaylistListResponse = try await api.get("/me/playlists"); destinations = result.items }
                catch { self.error = error.localizedDescription }
            }
        }.presentationDetents([.medium, .large])
    }
    private func select(_ video: ArchiveVideo) { note = video.playlistItem?.note ?? ""; moveTarget = ""; selected = video }
    private func load(more: Bool = false) async {
        let key = query + "|" + watched; busy = true; error = nil
        defer { if key == query + "|" + watched { busy = false } }
        do {
            let next = more ? page + 1 : 1
            let result: Page<ArchiveVideo> = try await api.get("/me/playlists/\(playlist.id)/videos", query: ["q": query, "watched": watched, "page": String(next), "page_size": "24"])
            guard key == query + "|" + watched, !Task.isCancelled else { return }
            items = more ? items + result.items : result.items; total = result.total; page = next
        } catch { if !Task.isCancelled { self.error = error.localizedDescription } }
    }
    private func update(_ video: ArchiveVideo, method: String = "PUT", body: [String: JSONValue] = [:]) async {
        do { try await api.mutate("/me/playlists/\(playlist.id)/videos/\(video.id)", method: method, body: body); await load() }
        catch { self.error = error.localizedDescription }
    }
    private func savePlaylist() async {
        busy = true; defer { busy = false }
        do { try await api.mutate("/me/playlists/\(playlist.id)", method: "PATCH", body: ["name": .string(name), "description": .string(description)]); editing = false }
        catch { self.error = error.localizedDescription }
    }
    private func deletePlaylist() async {
        do { try await api.mutate("/me/playlists/\(playlist.id)", method: "DELETE"); dismiss() }
        catch { self.error = error.localizedDescription }
    }
    private func saveItem(_ video: ArchiveVideo) async {
        busy = true; error = nil
        do {
            try await api.mutate("/me/playlists/\(playlist.id)/videos/\(video.id)", method: "PUT", body: ["note": .string(note)])
            if !moveTarget.isEmpty { try await api.mutate("/me/playlists/\(playlist.id)/videos/\(video.id)/move", body: ["target_playlist_id": .string(moveTarget)]) }
            selected = nil; await load()
        } catch { self.error = error.localizedDescription }
        busy = false
    }
}
