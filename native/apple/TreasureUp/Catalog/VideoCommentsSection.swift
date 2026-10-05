import SwiftUI

struct VideoCommentsSection: View {
    @Environment(APIClient.self) private var api
    let videoId: String
    @State private var comments: [JSONValue] = []
    @State private var query = ""
    @State private var sort = "likes"
    @State private var error: String?
    @State private var page = 0
    @State private var total = 0
    @State private var loading = false
    @State private var generation = 0
    private var searchKey: String { "\(videoId)|\(sort)|\(query)" }

    var body: some View {
        LazyVStack(alignment: .leading, spacing: 20) {
            HStack {
                Text("归档评论 \(total)").font(.headline)
                Spacer()
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
                if !query.isEmpty { Button("清除", systemImage: "xmark.circle.fill") { query = "" }.labelStyle(.iconOnly) }
            }.padding(12).background(.quaternary.opacity(0.6), in: .rect(cornerRadius: 12))
            Text("展示归档时的评论、点赞与回复。").font(.caption).foregroundStyle(.secondary)
            ForEach(Array(comments.enumerated()), id: \.element.commentIdentity) { _, comment in
                ArchivedCommentRow(videoId: videoId, comment: comment)
                Divider().padding(.leading, 48)
            }
            if loading { ProgressView().frame(maxWidth: .infinity) }
            if let error {
                Text(error).font(.callout).foregroundStyle(.red)
                Button("重试") { Task { await load(more: page > 0) } }
            }
            if !loading && comments.isEmpty && error == nil {
                ContentUnavailableView(query.isEmpty ? "暂无归档评论" : "没有匹配的评论", systemImage: "bubble.left.and.bubble.right")
            }
            if comments.count < total {
                Button("载入更多评论") { Task { await load(more: true) } }
                    .buttonStyle(.bordered).disabled(loading).frame(maxWidth: .infinity)
            }
        }
        .task(id: searchKey) {
            do {
                try await Task.sleep(for: .milliseconds(query.isEmpty ? 0 : 250))
                try Task.checkCancellation()
                await load()
            } catch { }
        }
    }
    private func load(more: Bool = false) async {
        if more && loading { return }
        generation += 1; let request = generation; let key = searchKey
        loading = true; error = nil
        defer { if request == generation { loading = false } }
        do {
            let next = more ? page + 1 : 1
            let result: JSONValue = try await api.get("/videos/\(videoId)/comments",
                query: ["q": query, "sort": sort, "page": String(next), "page_size": "30"])
            guard request == generation, key == searchKey, !Task.isCancelled else { return }
            let rows = result["items"]?.arrayValue ?? []
            if more {
                let existing = Set(comments.map(\.commentIdentity))
                comments += rows.filter { !existing.contains($0.commentIdentity) }
            } else { comments = rows }
            total = result["total"]?.intValue ?? comments.count; page = next
        } catch { if request == generation && key == searchKey && !Task.isCancelled { self.error = error.localizedDescription } }
    }
}

private extension JSONValue {
    var commentIdentity: String { self["id"]?.stringValue ?? self["rpid"]?.stringValue ?? self["content"]?.stringValue ?? "" }
}

private struct ArchivedCommentRow: View {
    @Environment(APIClient.self) private var api
    let videoId: String
    let comment: JSONValue
    @State private var expanded = false
    @State private var replies: [JSONValue] = []
    @State private var total = 0
    @State private var page = 0
    @State private var loading = false
    @State private var error: String?
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ArchivedCommentBody(comment: comment)
            if (comment["reply_count"]?.intValue ?? 0) > 0 {
                VStack(alignment: .leading, spacing: 14) {
                    Button {
                        expanded.toggle()
                        if expanded && page == 0 { Task { await loadReplies() } }
                    } label: {
                        HStack {
                            Text(expanded ? "收起回复" : "\(comment["reply_count"]?.intValue ?? 0) 条回复")
                            Image(systemName: expanded ? "chevron.up" : "chevron.down")
                        }.font(.subheadline.weight(.medium)).frame(minHeight: 30)
                    }.accessibilityValue(expanded ? "已展开" : "已收起")
                    if expanded {
                        ForEach(Array(replies.enumerated()), id: \.element.commentIdentity) { _, reply in
                            ArchivedCommentBody(comment: reply, compact: true)
                        }
                        if loading { ProgressView() }
                        if let error {
                            Text(error).font(.caption).foregroundStyle(.red)
                            Button("重试") { Task { await loadReplies() } }
                        } else if page > 0 && replies.isEmpty { Text("这层回复尚未归档。").font(.caption).foregroundStyle(.secondary) }
                        if replies.count < total { Button("更多回复（已归档 \(total) 条）") { Task { await loadReplies() } }.font(.caption).disabled(loading) }
                    }
                }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
                    .background(.quaternary.opacity(0.55), in: .rect(cornerRadius: 12)).padding(.leading, 48)
            }
        }
    }
    private func loadReplies() async {
        guard !loading else { return }; loading = true; error = nil
        defer { loading = false }
        do {
            let result: JSONValue = try await api.get("/videos/\(videoId)/comments", query: [
                "root": comment["rpid"]?.stringValue ?? "", "page": String(page + 1), "page_size": "20"])
            guard !Task.isCancelled else { return }
            let rows = result["items"]?.arrayValue ?? []
            let existing = Set(replies.map(\.commentIdentity))
            replies += rows.filter { !existing.contains($0.commentIdentity) }
            total = result["total"]?.intValue ?? replies.count; page += 1
        } catch { if !Task.isCancelled { self.error = error.localizedDescription } }
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
    @GestureState private var magnification: CGFloat = 1
    var body: some View {
        GeometryReader { geometry in
            ScrollView([.horizontal, .vertical]) {
                AsyncImage(url: url) { image in
                    image.resizable().scaledToFit().frame(width: geometry.size.width * min(4, max(1, scale * magnification)))
                } placeholder: { ProgressView().frame(width: geometry.size.width, height: geometry.size.height) }
            }
        }
        .gesture(MagnifyGesture().updating($magnification) { value, state, _ in state = value.magnification }
            .onEnded { scale = min(4, max(1, scale * $0.magnification)) })
        .onTapGesture(count: 2) { scale = scale > 1 ? 1 : 2 }
        .navigationTitle("评论附图").navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) { Button("完成") { dismiss() } }
            ToolbarItem(placement: .primaryAction) { ShareLink(item: url) }
        }
    }
}
