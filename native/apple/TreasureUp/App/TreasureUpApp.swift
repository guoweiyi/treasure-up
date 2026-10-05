import SwiftUI

@main
struct TreasureUpApp: App {
    @State private var api: APIClient
    @State private var playback: PlaybackCoordinator

    init() {
        let client = APIClient()
        _api = State(initialValue: client)
        _playback = State(initialValue: PlaybackCoordinator(api: client))
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(api)
                .environment(playback)
                .tint(.indigo)
        }
    }
}

enum AppSection: String, CaseIterable, Identifiable {
    case library, collections, creators, settings
    var id: Self { self }
    var title: String {
        switch self {
        case .library: "资料库"
        case .collections: "收藏与订阅"
        case .creators: "UP 主"
        case .settings: "设置"
        }
    }
    var symbol: String {
        switch self {
        case .library: "play.rectangle.on.rectangle"
        case .collections: "square.stack"
        case .creators: "person.2"
        case .settings: "gearshape"
        }
    }
}

struct RootView: View {
    @Environment(APIClient.self) private var api
    @Environment(PlaybackCoordinator.self) private var playback
    @Environment(\.horizontalSizeClass) private var sizeClass
    @State private var selection: AppSection? = .library
    @State private var playerPresented = false
    @State private var expandedPlayback = false
    @State private var visiblePlayback = false
    @State private var splitVisibility: NavigationSplitViewVisibility = .automatic
    @State private var connecting = true
    @State private var connectionError: String?

    var body: some View {
        Group {
            if connecting {
                ProgressView("正在连接资料库…").frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if !api.isConnected {
                NavigationStack { ServerConnectionView(initialError: connectionError) }
            } else {
                navigation
                    .id(api.baseURL.absoluteString + (api.user?.id ?? "guest") + (api.user?.role ?? ""))

            }
        }
        .task {
            do { try await api.restoreSession() }
            catch { connectionError = error.localizedDescription }
            connecting = false
        }
        .onChange(of: playback.requestsPresentation) { _, requested in
            if requested { playerPresented = true; playback.requestsPresentation = false }
        }
        .onChange(of: api.sessionRevision) { _, _ in playback.resetForIdentityChange(); playerPresented = false }
        .fullScreenCover(isPresented: $playerPresented) {
            if let video = playback.currentVideo {
                NavigationStack {
                    VideoDetailView(videoId: video.id)
                        .toolbar { ToolbarItem(placement: .cancellationAction) { Button("完成") { playerPresented = false } } }
                }
            }
        }
        .onPreferenceChange(PlayerExpandedPreferenceKey.self) { expanded in
            expandedPlayback = expanded
            splitVisibility = expanded ? .detailOnly : .automatic
        }
        .onPreferenceChange(PlayerVisiblePreferenceKey.self) { visiblePlayback = $0 }
    }

    @ViewBuilder private var navigation: some View {
        if sizeClass == .regular {
            NavigationSplitView(columnVisibility: $splitVisibility) {
                List(AppSection.allCases, selection: $selection) { section in
                    Label(section.title, systemImage: section.symbol).tag(section)
                }
                .navigationTitle(api.siteName)
                .navigationSplitViewColumnWidth(min: 220, ideal: 250, max: 320)
                .safeAreaInset(edge: .bottom) {
                    Label(api.user?.username ?? "访客浏览", systemImage: "person.crop.circle")
                        .font(.subheadline).foregroundStyle(.secondary).padding()
                }
            } detail: {
                NavigationStack { sectionView(selection ?? .library) }
                    .safeAreaInset(edge: .bottom, spacing: 0) { floatingMiniPlayer }
                    .id(selection)
            }
            .navigationSplitViewStyle(.balanced)
        } else {
            phoneNavigation
        }
    }

    @ViewBuilder private var phoneNavigation: some View {
        if #available(iOS 26.1, *) {
            phoneTabs.tabViewBottomAccessory(isEnabled: playback.currentVideo != nil && !visiblePlayback && !expandedPlayback) {
                TabMiniPlayer { playerPresented = true }
            }
        } else if #available(iOS 26.0, *) {
            phoneTabs.tabViewBottomAccessory {
                if playback.currentVideo != nil && !visiblePlayback && !expandedPlayback { TabMiniPlayer { playerPresented = true } }
            }
        } else {
            phoneTabs
        }
    }

    private var phoneTabs: some View {
        TabView {
            ForEach(AppSection.allCases) { section in
                Tab(section.title, systemImage: section.symbol) {
                    NavigationStack { sectionView(section) }
                        .safeAreaInset(edge: .bottom, spacing: 0) {
                            if #unavailable(iOS 26.0) { floatingMiniPlayer }
                        }
                }
            }
        }
        .adaptiveTabBar()
    }

    @ViewBuilder private var floatingMiniPlayer: some View {
        if playback.currentVideo != nil && !visiblePlayback && !expandedPlayback {
            MiniPlayerContent { playerPresented = true }
                .padding(10).playerGlass()
                .padding(.horizontal, 14).padding(.bottom, 6)
        }
    }

    @ViewBuilder private func sectionView(_ section: AppSection) -> some View {
        switch section {
        case .library: VideoLibraryView()
        case .collections: CollectionsView()
        case .creators: CreatorsView()
        case .settings: SettingsView()
        }
    }
}

private struct MiniPlayerContent: View {
    @Environment(PlaybackCoordinator.self) private var playback
    var compact = false
    let openPlayer: () -> Void

    var body: some View {
        if let video = playback.currentVideo {
            HStack(spacing: compact ? 8 : 12) {
                Button(action: openPlayer) {
                    HStack(spacing: 12) {
                        if !compact {
                            Artwork(path: video.coverUrl).frame(width: 60, height: 42)
                                .clipShape(.rect(cornerRadius: 8))
                        }
                        VStack(alignment: .leading, spacing: 3) {
                            Text(video.title).font(.subheadline.weight(.semibold)).lineLimit(1)
                            if !compact {
                                Text(playback.isPlaying ? "正在播放 · 打开播放器" : "已暂停 · 打开播放器")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }.frame(maxWidth: .infinity, alignment: .leading)
                    }.contentShape(.rect)
                }.buttonStyle(.plain)
                    .accessibilityLabel("打开播放器：\(video.title)")
                Button(playback.isPlaying ? "暂停" : "继续播放", systemImage: playback.isPlaying ? "pause.fill" : "play.fill") {
                    playback.togglePlayback()
                }.labelStyle(.iconOnly).frame(minWidth: 44, minHeight: 44)
                if !compact {
                    Button("停止播放", systemImage: "xmark") { playback.stop() }
                        .labelStyle(.iconOnly).frame(minWidth: 44, minHeight: 44)
                }
            }
        }
    }
}

@available(iOS 26.0, *)
private struct TabMiniPlayer: View {
    @Environment(\.tabViewBottomAccessoryPlacement) private var placement
    let openPlayer: () -> Void

    var body: some View {
        MiniPlayerContent(compact: placement == .inline, openPlayer: openPlayer)
            .padding(.horizontal, 12)
            .padding(.vertical, placement == .inline ? 0 : 6)
    }
}

struct PlayerExpandedPreferenceKey: PreferenceKey {
    static let defaultValue = false
    static func reduce(value: inout Bool, nextValue: () -> Bool) { value = value || nextValue() }
}
struct PlayerVisiblePreferenceKey: PreferenceKey {
    static let defaultValue = false
    static func reduce(value: inout Bool, nextValue: () -> Bool) { value = value || nextValue() }
}
