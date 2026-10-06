import SwiftUI

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
    @Environment(APIClient.self) private var api
    let path: String?
    var symbol = "play.rectangle"
    var body: some View {
        AsyncImage(url: api.resolveURL(path)) { image in
            image.resizable().scaledToFill()
        } placeholder: {
            Rectangle().fill(.quaternary)
                .overlay { Image(systemName: symbol).font(.largeTitle).foregroundStyle(.tertiary) }
        }.clipped().accessibilityHidden(true)
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
