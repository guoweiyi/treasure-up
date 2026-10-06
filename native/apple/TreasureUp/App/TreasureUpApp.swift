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
            appContent
                .environment(api)
                .environment(playback)
                .tint(.indigo)
        }
    }

    @ViewBuilder private var appContent: some View {
        #if DEBUG
        if OfflinePlayerUITestFixture.isEnabled {
            OfflinePlayerUITestFixture()
        } else if ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] != nil {
            // Hosted unit tests supply their own windows, API stubs and player.
            // Do not start a real restoreSession request against the user's
            // configured server while running the offline regression suite.
            Color(uiColor: .systemBackground)
        } else {
            RootView()
        }
        #else
        RootView()
        #endif
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
    @State private var selection: AppSection? = .library
    @State private var splitVisibility: NavigationSplitViewVisibility = .automatic
    @State private var connecting = true
    @State private var connectionError: String?

    var body: some View {
        Group {
            if connecting {
                ProgressView(appPrompt("正在连接资料库…")).frame(maxWidth: .infinity, maxHeight: .infinity)
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
            // Manual PiP restores the still-mounted video destination.
            if requested { playback.requestsPresentation = false }
        }
        .onChange(of: api.sessionRevision) { _, _ in playback.resetForIdentityChange() }
    }

    @ViewBuilder private var navigation: some View {
        if UIDevice.current.userInterfaceIdiom == .pad {
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
                    .id(selection)
            }
            .navigationSplitViewStyle(.balanced)
        } else {
            phoneTabs
        }
    }

    private var phoneTabs: some View {
        TabView {
            ForEach(AppSection.allCases) { section in
                Tab(section.title, systemImage: section.symbol) {
                    NavigationStack { sectionView(section) }
                }
            }
        }
        .adaptiveTabBar()
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
