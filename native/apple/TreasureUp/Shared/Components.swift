import SwiftUI
import ImageIO

extension View {
    @ViewBuilder func nativeGlassButton(prominent: Bool = false) -> some View {
        if #available(iOS 26.0, *) {
            if prominent { buttonStyle(.glassProminent) } else { buttonStyle(.glass) }
        } else {
            if prominent { buttonStyle(.borderedProminent) } else { buttonStyle(.bordered) }
        }
    }
    @ViewBuilder func playerGlass() -> some View {
        if #available(iOS 26.0, *) { glassEffect(.regular, in: .rect(cornerRadius: 22)) }
        else { background(.regularMaterial, in: .rect(cornerRadius: 22)) }
    }
    @ViewBuilder func adaptiveTabBar() -> some View {
        if #available(iOS 26.0, *) { tabBarMinimizeBehavior(.onScrollDown) } else { self }
    }
}

struct Artwork: View {
    @Environment(\.displayScale) private var displayScale
    let path: String?
    var symbol = "play.rectangle"
    var body: some View {
        GeometryReader { geometry in
            ArchiveAsyncImage(path: path, maximumPixelSize: ArchiveImageRequest.pixelLimit(
                points: max(geometry.size.width, geometry.size.height), scale: displayScale)) { phase in
                if case .success(let image) = phase {
                    image.resizable().scaledToFill()
                } else {
                    Rectangle().fill(.quaternary)
                        .overlay { Image(systemName: symbol).font(.largeTitle).foregroundStyle(.tertiary) }
                }
            }.frame(width: geometry.size.width, height: geometry.size.height).clipped()
        }.clipped().accessibilityHidden(true)
    }
}

/// The request identity also gates rendering, so a previous user's image cannot
/// flash for one frame while SwiftUI starts the replacement .task.
struct ArchiveImageRequest: Hashable, Sendable {
    let client: ObjectIdentifier
    let server: String
    let userID: String?
    let revision: Int
    let path: String?
    let maximumPixelSize: Int

    @MainActor init(api: APIClient, path: String?, maximumPixelSize: Int) {
        client = ObjectIdentifier(api)
        server = api.baseURL.absoluteString
        userID = api.user?.id
        revision = api.sessionRevision
        self.path = api.resolveURL(path)?.absoluteString
        self.maximumPixelSize = min(2048, max(64, maximumPixelSize))
    }

    func hasSameSession(as other: Self) -> Bool {
        client == other.client && server == other.server && userID == other.userID && revision == other.revision
    }

    static func pixelLimit(points: CGFloat, scale: CGFloat) -> Int {
        guard points.isFinite, scale.isFinite, points > 0, scale > 0 else { return 64 }
        // Quantize small layout changes to avoid repeated network and decode work.
        return min(1024, max(64, Int(ceil(min(1024, points * scale) / 64)) * 64))
    }
}

/// CGImage is immutable here. ImageIO finishes decoding on a worker before the
/// reference crosses to the UI actor; no mutable bitmap or decoder is shared.
struct DecodedArchiveImage: @unchecked Sendable {
    let image: CGImage
    var byteCost: Int { image.bytesPerRow * image.height }
}

enum ArchiveImageDecoder {
    nonisolated static func decode(_ data: Data, maximumPixelSize: Int) async throws -> DecodedArchiveImage {
        try Task.checkCancellation()
        let worker = Task.detached(priority: .utility) {
            try Task.checkCancellation()
            guard !data.isEmpty, data.count <= 20 * 1024 * 1024,
                  let source = CGImageSourceCreateWithData(data as CFData, [kCGImageSourceShouldCache: false] as CFDictionary),
                  let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
                  let width = (properties[kCGImagePropertyPixelWidth] as? NSNumber)?.doubleValue,
                  let height = (properties[kCGImagePropertyPixelHeight] as? NSNumber)?.doubleValue,
                  width > 0, height > 0, width * height <= 250_000_000 else { throw APIError.decoding }
            let options: [CFString: Any] = [
                kCGImageSourceCreateThumbnailFromImageAlways: true,
                kCGImageSourceCreateThumbnailWithTransform: true,
                kCGImageSourceThumbnailMaxPixelSize: min(2048, max(64, maximumPixelSize)),
                kCGImageSourceShouldCacheImmediately: true,
            ]
            guard let image = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary) else { throw APIError.decoding }
            try Task.checkCancellation()
            return DecodedArchiveImage(image: image)
        }
        return try await withTaskCancellationHandler {
            let image = try await worker.value
            try Task.checkCancellation()
            return image
        } onCancel: { worker.cancel() }
    }
}

/// A memory-only LRU. One identity at a time, bounded by decoded bytes and count;
/// short expiry also rechecks private assets during a long-running app session.
@MainActor final class ArchiveImageCache {
    static let shared = ArchiveImageCache()
    private struct Entry { let image: DecodedArchiveImage; let created: Date; var access: Int }
    private let maximumBytes: Int
    private let maximumCount: Int
    private let lifetime: TimeInterval
    private var entries: [ArchiveImageRequest: Entry] = [:]
    private var scope: ArchiveImageRequest?
    private var access = 0
    private(set) var byteCount = 0
    var count: Int { entries.count }

    init(maximumBytes: Int = 32 * 1024 * 1024, maximumCount: Int = 128, lifetime: TimeInterval = 120) {
        self.maximumBytes = max(0, maximumBytes)
        self.maximumCount = max(0, maximumCount)
        self.lifetime = max(0, lifetime)
    }
    private func selectSession(_ key: ArchiveImageRequest) {
        if scope?.hasSameSession(as: key) != true {
            entries.removeAll(keepingCapacity: false)
            byteCount = 0
            scope = key
        }
    }
    func image(for key: ArchiveImageRequest, now: Date = Date()) -> DecodedArchiveImage? {
        selectSession(key)
        guard var entry = entries[key] else { return nil }
        guard now.timeIntervalSince(entry.created) < lifetime else { remove(key); return nil }
        access &+= 1
        entry.access = access
        entries[key] = entry
        return entry.image
    }
    func insert(_ image: DecodedArchiveImage, for key: ArchiveImageRequest, now: Date = Date()) {
        selectSession(key)
        remove(key)
        guard maximumCount > 0, image.byteCost <= maximumBytes else { return }
        while entries.count >= maximumCount || byteCount + image.byteCost > maximumBytes {
            guard let oldest = entries.min(by: { $0.value.access < $1.value.access })?.key else { break }
            remove(oldest)
        }
        access &+= 1
        entries[key] = Entry(image: image, created: now, access: access)
        byteCount += image.byteCost
    }
    private func remove(_ key: ArchiveImageRequest) {
        if let entry = entries.removeValue(forKey: key) { byteCount -= entry.image.byteCost }
    }
}

struct ArchiveAsyncImage<Content: View>: View {
    @Environment(APIClient.self) private var api
    let path: String?
    var maximumPixelSize = 1024
    let content: (AsyncImagePhase) -> Content
    @State private var loadedRequest: ArchiveImageRequest?
    @State private var phase = AsyncImagePhase.empty

    init(path: String?, maximumPixelSize: Int = 1024, @ViewBuilder content: @escaping (AsyncImagePhase) -> Content) {
        self.path = path
        self.maximumPixelSize = maximumPixelSize
        self.content = content
    }

    private var request: ArchiveImageRequest { ArchiveImageRequest(api: api, path: path, maximumPixelSize: maximumPixelSize) }

    var body: some View {
        let key = request
        content(loadedRequest == key ? phase : .empty)
            .task(id: key) { await load(key) }
    }

    private func load(_ key: ArchiveImageRequest) async {
        guard key == request, !Task.isCancelled else { return }
        loadedRequest = key
        phase = .empty
        guard let path = key.path else { return }
        do {
            try Task.checkCancellation()
            let image: DecodedArchiveImage
            if let cached = ArchiveImageCache.shared.image(for: key) { image = cached }
            else {
                let data = try await api.data(path)
                try Task.checkCancellation()
                image = try await ArchiveImageDecoder.decode(data, maximumPixelSize: key.maximumPixelSize)
                try Task.checkCancellation()
                guard key == request else { return }
                ArchiveImageCache.shared.insert(image, for: key)
            }
            try Task.checkCancellation()
            guard key == request else { return }
            phase = .success(Image(decorative: image.image, scale: 1, orientation: .up))
        } catch {
            guard !Task.isCancelled, key == request else { return }
            phase = .failure(error)
        }
    }
}

struct FailureView: View {
    let message: String
    var retry: (() async -> Void)?
    var body: some View {
        ContentUnavailableView {
            Label(appPrompt("暂时无法加载"), systemImage: "wifi.exclamationmark")
        } description: { Text(appPrompt(message)) } actions: {
            if let retry { Button("重试") { Task { await retry() } }.buttonStyle(.borderedProminent) }
        }
    }
}

func mediaDuration(_ seconds: Double) -> String {
    guard seconds.isFinite, seconds > 0 else { return "0:00" }
    let value = Int(seconds)
    return value >= 3600 ? String(format: "%d:%02d:%02d", value / 3600, value / 60 % 60, value % 60) : String(format: "%d:%02d", value / 60, value % 60)
}

struct VideoCard: View {
    let video: ArchiveVideo
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Color.clear.aspectRatio(16 / 9, contentMode: .fit)
                .overlay { Artwork(path: video.coverUrl) }
                .overlay(alignment: .bottomTrailing) {
                    Text(mediaDuration(video.duration)).font(.caption.monospacedDigit().weight(.medium))
                        .padding(.horizontal, 7).padding(.vertical, 4)
                        .background(.black.opacity(0.7), in: .capsule).foregroundStyle(.white).padding(8)
                }.clipShape(.rect(cornerRadius: 12))
            Text(video.title).font(.subheadline.weight(.semibold)).foregroundStyle(.primary).lineLimit(2, reservesSpace: true)
                .frame(maxWidth: .infinity, alignment: .leading)
            HStack {
                Text(video.creators.first?.name ?? "已归档视频").lineLimit(1)
                Spacer(minLength: 2)
                if video.starred { Image(systemName: "star.fill").foregroundStyle(.orange) }
                if !video.playable { Text("待归档") }
                else if video.partsCount > 1 { Text("\(video.partsCount) P") }
            }.font(.caption).foregroundStyle(.secondary)
        }
        .contentShape(.rect)
        .accessibilityElement(children: .combine)
    }
}

struct PaginationFooter: View {
    let count: Int
    let total: Int
    let busy: Bool
    var hasMore = true
    let more: () async -> Void
    var body: some View {
        VStack(spacing: 12) {
            if hasMore && count < total {
                Button { Task { await more() } } label: {
                    HStack(spacing: 8) {
                        if busy { ProgressView() }
                        Text(busy ? appPrompt("正在加载…") : "载入更多")
                    }.frame(minHeight: 32)
                }.buttonStyle(.bordered).disabled(busy)
            }
            Text("已显示 \(count) / \(total)").font(.caption).foregroundStyle(.secondary)
        }.frame(maxWidth: .infinity).padding()
    }
}

func archiveCount(_ value: Int) -> String {
    if value >= 10_000 { return String(format: "%.1f万", Double(value) / 10_000).replacingOccurrences(of: ".0万", with: "万") }
    return value.formatted()
}
