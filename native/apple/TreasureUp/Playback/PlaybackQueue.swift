import Foundation
import Observation

enum PlaybackQueueMode: String, Codable, CaseIterable, Identifiable, Sendable {
    case pause, repeatVideo = "repeat", continuous
    var id: String { rawValue }
    var title: String {
        switch self { case .pause: "播完暂停"; case .repeatVideo: "单视频循环"; case .continuous: "列表连播" }
    }
    var systemImage: String {
        switch self { case .pause: "pause.circle"; case .repeatVideo: "repeat.1"; case .continuous: "text.line.first.and.arrowtriangle.forward" }
    }
}

struct PlaybackQueuePreferences: Codable, Equatable, Sendable {
    var mode: PlaybackQueueMode = .pause
    var autoAdvance = true

    static func key(server: URL, userId: String?) -> String {
        // Keep the origin and account separate: equal user IDs on two servers
        // must never share preferences. JSON avoids delimiter collisions.
        let scope = [server.absoluteString, userId.map { "user:\($0)" } ?? "guest"]
        let encoded = (try? JSONEncoder().encode(scope))?.base64EncodedString() ?? ""
        return "treasure.native.queue.v1.\(encoded)"
    }

    static func load(defaults: UserDefaults, server: URL, userId: String?) -> Self {
        guard let data = defaults.data(forKey: key(server: server, userId: userId)),
              let value = try? JSONDecoder().decode(Self.self, from: data) else { return Self() }
        return value
    }

    func save(defaults: UserDefaults, server: URL, userId: String?) {
        if let data = try? JSONEncoder().encode(self) {
            defaults.set(data, forKey: Self.key(server: server, userId: userId))
        }
    }
}

struct PlaybackQueueContext: Hashable, Sendable {
    var title: String
    var path: String = "/videos"
    var query: [String: String] = [:]

    static func personal(id: String, title: String) -> Self {
        Self(title: title, path: "/me/playlists/\(id)/videos", query: ["playable_only": "true"])
    }
    static func creator(id: String, title: String) -> Self {
        Self(title: title, path: "/playlists", query: ["creator_id": id])
    }
    static func collection(id: String, title: String) -> Self {
        Self(title: title, path: "/playlists", query: ["collection_id": id])
    }
    func parameters(page: Int) -> [String: String] {
        var result = query
        result["page"] = String(page)
        result["page_size"] = "100"
        result["view"] = "card"
        return result
    }
}

enum PlaybackQueueTarget: Equatable, Sendable {
    case part(String), video(String), replay, stop
}

/// Ordered, non-destructive playback state. The selected video remains in the
/// list so previous, arbitrary selection and reordering do not lose history.
@MainActor @Observable
final class PlaybackQueue {
    private(set) var items: [ArchiveVideo] = []
    private(set) var currentId: String?
    private(set) var title = "播放队列"
    private(set) var context: PlaybackQueueContext?
    private(set) var isLoading = false
    private(set) var errorMessage: String?
    private(set) var total = 0
    private(set) var isComplete = true
    private(set) var revision = UUID()
    @ObservationIgnored private var nextPage = 1
    @ObservationIgnored private var sourceItems: [ArchiveVideo] = []
    @ObservationIgnored private var extras: [ArchiveVideo] = []
    @ObservationIgnored private var excludedIds: Set<String> = []
    @ObservationIgnored private var manualOrder: [String]?

    var currentIndex: Int? { items.firstIndex { $0.id == currentId } }
    var hasUnloadedItems: Bool { context != nil && !isComplete }

    func configure(videos: [ArchiveVideo], currentVideoId: String?, title: String = "播放队列") {
        reset()
        self.title = title
        sourceItems = Self.unique(videos)
        currentId = currentVideoId
        total = sourceItems.count
        rebuild()
    }

    func configure(context: PlaybackQueueContext, current: ArchiveVideo) {
        reset()
        self.context = context
        title = context.title
        currentId = current.id
        extras = [current]
        isComplete = false
        rebuild()
    }

    func select(_ video: ArchiveVideo) {
        currentId = video.id
        excludedIds.remove(video.id)
        if let index = sourceItems.firstIndex(where: { $0.id == video.id }) { sourceItems[index] = video }
        else if let index = extras.firstIndex(where: { $0.id == video.id }) { extras[index] = video }
        else { extras.append(video) }
        rebuild()
    }

    func enqueue(_ video: ArchiveVideo) {
        guard !items.contains(where: { $0.id == video.id }) else { return }
        excludedIds.remove(video.id)
        extras.append(video)
        rebuild()
    }

    func remove(id: String) {
        excludedIds.insert(id)
        sourceItems.removeAll { $0.id == id }
        extras.removeAll { $0.id == id }
        manualOrder?.removeAll { $0 == id }
        if currentId == id { currentId = nil }
        rebuild()
    }

    func move(fromOffsets offsets: IndexSet, toOffset destination: Int) {
        let valid = offsets.filter { items.indices.contains($0) }
        guard !valid.isEmpty else { return }
        let moving = valid.map { items[$0] }
        let selected = Set(valid)
        var remainder = items.enumerated().filter { !selected.contains($0.offset) }.map(\.element)
        let insertion = min(remainder.count, max(0, destination - valid.filter { $0 < destination }.count))
        remainder.insert(contentsOf: moving, at: insertion)
        manualOrder = remainder.map(\.id)
        rebuild()
    }

    func clear() { reset() }

    /// Finish every page, including pages beyond the current catalog screen.
    /// Failed pages are retried in place. Repeated/empty responses cannot loop.
    func loadAll(using fetch: @MainActor (PlaybackQueueContext, Int) async throws -> Page<ArchiveVideo>) async {
        guard let context, !isLoading, !isComplete else { return }
        let key = revision
        isLoading = true
        errorMessage = nil
        defer { if revision == key { isLoading = false } }
        while !isComplete {
            do {
                try Task.checkCancellation()
                let requestedPage = nextPage
                let result = try await fetch(context, requestedPage)
                try Task.checkCancellation()
                guard revision == key else { return }
                let existing = Set(sourceItems.map(\.id))
                let additions = Self.unique(result.items).filter { !existing.contains($0.id) }
                if !result.items.isEmpty && additions.isEmpty {
                    errorMessage = "服务器返回了重复分页，已保留当前列表；请稍后重试。"
                    return
                }
                // Preserve full detail already obtained for the current video.
                sourceItems.append(contentsOf: additions.map { summary in
                    extras.first(where: { $0.id == summary.id && !$0.parts.isEmpty }) ?? summary
                })
                if let scopeTitle = result.scopeTitle, !scopeTitle.isEmpty { title = scopeTitle }
                total = max(0, result.total)
                nextPage = requestedPage + 1
                isComplete = result.items.isEmpty || requestedPage * max(1, result.pageSize) >= total
                rebuild()
            } catch {
                guard revision == key else { return }
                if !Task.isCancelled { errorMessage = "播放队列加载失败：\(error.localizedDescription)" }
                return
            }
        }
    }

    func candidates(direction: Int, startingAt id: String? = nil) -> [ArchiveVideo] {
        if let id, let index = items.firstIndex(where: { $0.id == id }) {
            return direction < 0 ? Array(items[...index].reversed()) : Array(items[index...])
        }
        guard let index = currentIndex else { return direction > 0 ? items : [] }
        if direction < 0 { return Array(items[..<index].reversed()) }
        return Array(items.dropFirst(index + 1))
    }

    nonisolated static func playableParts(_ parts: [VideoPart]) -> [VideoPart] {
        parts.filter { !$0.variants.isEmpty }.sorted { $0.position < $1.position }
    }

    nonisolated static func target(parts: [VideoPart], partId: String?, videos: [String], videoId: String?,
                                   direction: Int, ended: Bool = false, mode: PlaybackQueueMode = .pause,
                                   autoAdvance: Bool = true) -> PlaybackQueueTarget {
        if ended && !autoAdvance { return .stop }
        let playable = playableParts(parts)
        if let index = playable.firstIndex(where: { $0.id == partId }), playable.indices.contains(index + direction) {
            return .part(playable[index + direction].id)
        }
        if ended {
            if mode == .pause { return .stop }
            if mode == .repeatVideo {
                guard let first = playable.first else { return .stop }
                return first.id == partId ? .replay : .part(first.id)
            }
        }
        if let index = videos.firstIndex(where: { $0 == videoId }), videos.indices.contains(index + direction) {
            return .video(videos[index + direction])
        }
        return .stop
    }

    private func reset() {
        revision = UUID()
        currentId = nil
        title = "播放队列"
        context = nil
        isLoading = false
        errorMessage = nil
        total = 0
        isComplete = true
        nextPage = 1
        sourceItems = []
        extras = []
        excludedIds = []
        manualOrder = nil
        items = []
    }

    private func rebuild() {
        let all = Self.unique(sourceItems + extras).filter { !excludedIds.contains($0.id) }
        if let manualOrder {
            let lookup = Dictionary(uniqueKeysWithValues: all.map { ($0.id, $0) })
            let ordered = manualOrder.compactMap { lookup[$0] }
            let ids = Set(ordered.map(\.id))
            items = ordered + all.filter { !ids.contains($0.id) }
        } else { items = all }
    }

    private static func unique(_ videos: [ArchiveVideo]) -> [ArchiveVideo] {
        var seen: Set<String> = []
        return videos.filter { !$0.id.isEmpty && seen.insert($0.id).inserted }
    }
}

struct PlaybackEndGuard {
    private var consumed: UUID?
    mutating func consume(_ generation: UUID) -> Bool {
        guard consumed != generation else { return false }
        consumed = generation
        return true
    }
    mutating func rearm(_ generation: UUID) {
        if consumed == generation { consumed = nil }
    }
}
