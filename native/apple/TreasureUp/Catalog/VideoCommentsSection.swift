import SwiftUI

struct VideoCommentsSection: View {
    @Environment(APIClient.self) private var api
    @Environment(\.dynamicTypeSize) private var typeSize
    let videoId: String
    var isActive = true
    @FocusState private var searchFocused: Bool
    @State private var comments: [JSONValue] = []
    @State private var query = ""
    @State private var sort = "likes"
    @State private var error: String?
    @State private var page = 0
    @State private var total = 0
    @State private var loading = true
    @State private var loadingMore = false
    @State private var loadedKey: String?
    @State private var hasMore = true
    @State private var generation = 0
    private var searchKey: String { "\(videoId)|\(sort)|\(query)" }

    var body: some View {
        LazyVStack(alignment: .leading, spacing: 20) {
            let headerLayout = typeSize.isAccessibilitySize ? AnyLayout(VStackLayout(alignment: .leading, spacing: 12)) : AnyLayout(HStackLayout())
            headerLayout {
                Text(loading && comments.isEmpty ? "归档评论" : "归档评论 \(total)").font(.headline)
                if !typeSize.isAccessibilitySize { Spacer() }
                Menu {
                    Picker("排序", selection: $sort) {
                        Text("按热度").tag("likes")
                        Text("最新发布").tag("newest")
                    }
                } label: { Label(sort == "likes" ? "按热度" : "最新", systemImage: "line.3.horizontal.decrease").font(.subheadline) }
            }
            HStack {
                Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                TextField("搜索归档评论", text: $query).textInputAutocapitalization(.never).autocorrectionDisabled()
                    .focused($searchFocused)
                if !query.isEmpty { Button("清除", systemImage: "xmark.circle.fill") { query = "" }.labelStyle(.iconOnly) }
            }.padding(12).background(.quaternary.opacity(0.6), in: .rect(cornerRadius: 12))
            Text(appPrompt("展示归档时的评论、点赞与回复。")).font(.caption).foregroundStyle(.secondary)
            ForEach(Array(comments.enumerated()), id: \.element.commentIdentity) { _, comment in
                ArchivedCommentRow(videoId: videoId, comment: comment)
                Divider().padding(.leading, 48)
            }
            if loading { ProgressView().frame(maxWidth: .infinity) }
            if let error {
                Text(appPrompt(error)).font(.callout).foregroundStyle(.red)
                Button("重试") { Task { await load(more: loadingMore) } }.disabled(loading)
            }
            if !loading && comments.isEmpty && error == nil {
                ContentUnavailableView(appPrompt(query.isEmpty ? "暂无归档评论" : "没有匹配的评论"), systemImage: "bubble.left.and.bubble.right")
            }
            if hasMore && comments.count < total {
                Button("载入更多评论") { Task { await load(more: true) } }
                    .buttonStyle(.bordered).disabled(loading).frame(maxWidth: .infinity)
            }
        }
        .task(id: searchKey) { await load(debounce: !query.isEmpty) }
        .onChange(of: isActive) { _, active in if !active { searchFocused = false } }
    }
    private func load(more: Bool = false, debounce: Bool = false) async {
        if more && (loading || loadedKey != searchKey || !hasMore) { return }
        generation += 1; let request = generation; let key = searchKey
        if !more && loadedKey != key { comments = []; total = 0; page = 0; hasMore = true }
        loading = true; loadingMore = more; error = nil
        defer { if request == generation { loading = false } }
        do {
            if debounce { try await Task.sleep(for: .milliseconds(250)) }
            try Task.checkCancellation()
            let next = more ? page + 1 : 1
            let result: JSONValue = try await api.get("/videos/\(videoId)/comments",
                query: ["q": query, "sort": sort, "page": String(next), "page_size": "30"])
            guard request == generation, key == searchKey, !Task.isCancelled else { return }
            let rows = result["items"]?.arrayValue ?? []
            var existing = Set(more ? comments.map(\.commentIdentity) : [])
            let newRows = rows.filter { existing.insert($0.commentIdentity).inserted }
            comments = more ? comments + newRows : newRows
            total = result["total"]?.intValue ?? comments.count; page = next
            loadedKey = key
            hasMore = !rows.isEmpty && comments.count < total
        } catch { if request == generation && key == searchKey && !Task.isCancelled { self.error = error.localizedDescription } }
    }
}

private extension JSONValue {
    var commentIdentity: String { self["id"]?.stringValue ?? self["rpid"]?.stringValue ?? self["content"]?.stringValue ?? "" }
}

private struct ArchivedCommentRow: View {
    @Environment(APIClient.self) private var api
    @Environment(\.dynamicTypeSize) private var typeSize
    let videoId: String
    let comment: JSONValue
    @State private var expanded = false
    @State private var replies: [JSONValue] = []
    @State private var total = 0
    @State private var page = 0
    @State private var loading = false
    @State private var hasMore = true
    @State private var generation = 0
    @State private var error: String?
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ArchivedCommentBody(comment: comment)
            if (comment["reply_count"]?.intValue ?? 0) > 0 {
                VStack(alignment: .leading, spacing: 14) {
                    Button {
                        expanded.toggle()
                    } label: {
                        HStack {
                            Text(expanded ? "收起回复" : "\(comment["reply_count"]?.intValue ?? 0) 条回复")
                            Image(systemName: expanded ? "chevron.up" : "chevron.down")
                        }.font(.subheadline.weight(.medium)).frame(minHeight: 44)
                    }.accessibilityValue(expanded ? "已展开" : "已收起")
                    if expanded {
                        ForEach(Array(replies.enumerated()), id: \.element.commentIdentity) { _, reply in
                            ArchivedCommentBody(comment: reply, compact: true)
                        }
                        if loading { ProgressView() }
                        if let error {
                            Text(appPrompt(error)).font(.caption).foregroundStyle(.red)
                            Button("重试") { Task { await loadReplies() } }
                        } else if page > 0 && replies.isEmpty { Text(appPrompt("这层回复尚未归档。")).font(.caption).foregroundStyle(.secondary) }
                        if hasMore && replies.count < total { Button("更多回复（已归档 \(total) 条）") { Task { await loadReplies() } }.font(.caption).disabled(loading) }
                    }
                }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
                    .background(.quaternary.opacity(0.55), in: .rect(cornerRadius: 12))
                    .padding(.leading, typeSize.isAccessibilitySize ? 0 : 48)
            }
        }
        .task(id: expanded) { if expanded && page == 0 { await loadReplies(replace: true) } }
    }
    private func loadReplies(replace: Bool = false) async {
        guard (replace || !loading), hasMore else { return }
        generation += 1; let request = generation
        let next = replace ? 1 : page + 1
        loading = true; error = nil
        defer { if request == generation { loading = false } }
        do {
            let result: JSONValue = try await api.get("/videos/\(videoId)/comments", query: [
                "root": comment["rpid"]?.stringValue ?? "", "page": String(next), "page_size": "20"])
            guard request == generation, !Task.isCancelled else { return }
            let rows = result["items"]?.arrayValue ?? []
            var existing = Set(replace ? [] : replies.map(\.commentIdentity))
            let newRows = rows.filter { existing.insert($0.commentIdentity).inserted }
            replies = replace ? newRows : replies + newRows
            total = result["total"]?.intValue ?? replies.count; page = next
            hasMore = !rows.isEmpty && replies.count < total
        } catch { if request == generation && !Task.isCancelled { self.error = error.localizedDescription } }
    }
}

private struct CommentPhoto: Identifiable { let url: URL; var id: String { url.absoluteString } }

private struct ArchivedCommentBody: View {
    @Environment(APIClient.self) private var api
    let comment: JSONValue
    var compact = false
    @State private var photo: CommentPhoto?
    var body: some View {
        HStack(alignment: .top, spacing: compact ? 8 : 12) {
            Artwork(path: comment["author"]?["avatar_url"]?.stringValue, symbol: "person.fill")
                .frame(width: compact ? 28 : 36, height: compact ? 28 : 36).clipShape(.circle)
            VStack(alignment: .leading, spacing: 9) {
                Text(comment["author"]?["name"]?.stringValue ?? "未知作者")
                    .font(.subheadline.weight(.medium)).foregroundStyle(.secondary)
                Text(comment["content"]?.stringValue ?? "").font(compact ? .subheadline : .body)
                    .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                if let images = comment["images"]?.arrayValue, !images.isEmpty {
                    ScrollView(.horizontal) {
                        HStack(spacing: 8) {
                            ForEach(Array(images.enumerated()), id: \.offset) { _, value in
                                let path = value.stringValue ?? value["asset_url"]?.stringValue ?? value["url"]?.stringValue
                                if let url = api.resolveURL(path) {
                                    Button { photo = CommentPhoto(url: url) } label: {
                                        Artwork(path: path, symbol: "photo").frame(width: 136, height: 116).clipShape(.rect(cornerRadius: 10))
                                    }.buttonStyle(.plain).accessibilityLabel("查看评论附图")
                                }
                            }
                        }
                    }.scrollIndicators(.hidden)
                }
                HStack {
                    Text(String((comment["posted_at"]?.stringValue ?? "").prefix(10)))
                    Spacer()
                    Label(archiveCount(comment["like_count"]?.intValue ?? 0), systemImage: "hand.thumbsup")
                        .accessibilityLabel("归档点赞 \(comment["like_count"]?.intValue ?? 0)")
                }.font(.caption).foregroundStyle(.secondary)
            }.frame(maxWidth: .infinity, alignment: .leading)
        }.sheet(item: $photo) { photo in NavigationStack { CommentImageView(url: photo.url) } }
    }
}

struct CommentImageView: View {
    @Environment(\.dismiss) private var dismiss
    let url: URL
    @State private var scale: CGFloat = 1
    @State private var retryID = 0
    @GestureState private var magnification: CGFloat = 1
    var body: some View {
        GeometryReader { geometry in
            ScrollView([.horizontal, .vertical]) {
                AsyncImage(url: url) { phase in
                    switch phase {
                    case .success(let image):
                        image.resizable().scaledToFit()
                            .frame(width: geometry.size.width * min(4, max(1, scale * magnification)))
                            .accessibilityLabel("评论附图")
                            .onTapGesture(count: 2) { scale = scale > 1 ? 1 : 2 }
                    case .failure:
                        ContentUnavailableView {
                            Label(appPrompt("附图未能加载"), systemImage: "photo")
                        } description: {
                            Text(appPrompt("请检查网络连接后重试。"))
                        } actions: {
                            Button("重新加载") { retryID += 1 }.buttonStyle(.borderedProminent)
                        }.frame(width: geometry.size.width, height: geometry.size.height)
                    case .empty:
                        ProgressView(appPrompt("正在加载附图…")).frame(width: geometry.size.width, height: geometry.size.height)
                    @unknown default:
                        EmptyView()
                    }
                }.id(retryID)
                    .frame(minWidth: geometry.size.width, minHeight: geometry.size.height)
            }
            .simultaneousGesture(MagnifyGesture().updating($magnification) { value, state, _ in state = value.magnification }
                .onEnded { scale = min(4, max(1, scale * $0.magnification)) })
        }
        .navigationTitle("评论附图").navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) { Button("完成") { dismiss() }.keyboardShortcut(.cancelAction) }
            ToolbarItem(placement: .primaryAction) { ShareLink(item: url) }
        }
    }
}
