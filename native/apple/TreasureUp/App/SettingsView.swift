import SwiftUI

struct ServerConnectionView: View {
    @Environment(APIClient.self) private var api
    @Environment(\.dismiss) private var dismiss
    var initialError: String?
    @State private var address = ""
    @State private var error: String?
    @State private var busy = false
    var body: some View {
        Form {
            Section {
                VStack(alignment: .leading, spacing: 14) {
                    Image(systemName: "play.rectangle.on.rectangle.fill").font(.system(size: 46)).foregroundStyle(.indigo)
                    Text(appPrompt("你的收藏，原生呈现。")).font(.title.bold())
                    Text(appPrompt("连接 Treasure Up，浏览已归档的视频、片单与创作者。")).foregroundStyle(.secondary)
                }.padding(.vertical, 16)
            }
            Section {
                TextField("https://example.com", text: $address)
                    .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    .accessibilityIdentifier("serverAddress")
                Button {
                    busy = true; error = nil
                    Task {
                        do { try await api.connect(address); try await api.restoreSession(); dismiss() }
                        catch { self.error = error.localizedDescription }
                        busy = false
                    }
                } label: { HStack { Text("连接服务器"); Spacer(); if busy { ProgressView() } else { Image(systemName: "arrow.right") } } }
                    .disabled(busy || address.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    .accessibilityIdentifier("connectServer")
            } header: { Text("服务器") } footer: { Text(appPrompt("使用 HTTPS 站点根地址。每台服务器的登录状态独立保存，切换时会停止当前播放。")) }
            if let message = error ?? initialError { Section { Text(appPrompt(message)).foregroundStyle(.red).accessibilityLabel(appPrompt("连接失败：\(message)")) } }
        }
        .navigationTitle("连接资料库")
        .onAppear { address = api.baseURL.absoluteString }
        .disabled(busy)
    }
}

struct LoginView: View {
    @Environment(APIClient.self) private var api
    @Environment(\.dismiss) private var dismiss
    @State private var username = ""
    @State private var password = ""
    @State private var busy = false
    @State private var error: String?
    @FocusState private var passwordFocused: Bool
    var body: some View {
        Form {
            Section {
                Label(appPrompt("登录后同步你的片单和播放进度"), systemImage: "person.crop.circle.badge.checkmark")
                    .font(.headline).padding(.vertical, 12)
                TextField("用户名", text: $username).textContentType(.username)
                    .textInputAutocapitalization(.never).autocorrectionDisabled().submitLabel(.next)
                    .onSubmit { passwordFocused = true }.accessibilityIdentifier("loginUsername")
                SecureField("密码", text: $password).textContentType(.password).focused($passwordFocused)
                    .submitLabel(.go).onSubmit { login() }.accessibilityIdentifier("loginPassword")
                Button(action: login) {
                    HStack { Text("登录"); Spacer(); if busy { ProgressView() } }
                }.disabled(busy || username.isEmpty || password.isEmpty).accessibilityIdentifier("loginSubmit")
            } footer: { Text(api.baseURL.host() ?? "") }
            if let error { Text(appPrompt(error)).foregroundStyle(.red) }
        }
        .navigationTitle("登录")
        .toolbar { ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() } } }
    }
    private func login() {
        guard !busy && !username.isEmpty && !password.isEmpty else { return }
        busy = true; error = nil
        Task {
            do { try await api.login(username: username, password: password); password = ""; dismiss() }
            catch { self.error = error.localizedDescription }
            busy = false
        }
    }
}

struct SettingsView: View {
    @Environment(APIClient.self) private var api
    @Environment(PlaybackCoordinator.self) private var playback
    @State private var loginPresented = false
    @State private var serverPresented = false
    @State private var playbackSettingsPresented = false
    @State private var logoutConfirmation = false
    @State private var error: String?
    var body: some View {
        Form {
            Section {
                if let user = api.user {
                    Label { VStack(alignment: .leading, spacing: 4) { Text(user.username).font(.headline); Text(roleName(user.role)).font(.caption).foregroundStyle(.secondary) } } icon: {
                        Image(systemName: "person.crop.circle.fill").font(.largeTitle).foregroundStyle(.indigo)
                    }.padding(.vertical, 6)
                    NavigationLink { SavedPlaylistsView() } label: { Label("收藏片单", systemImage: "bookmark") }
                    NavigationLink { AccountSecurityView() } label: { Label("账户与安全", systemImage: "lock.shield") }
                } else {
                    Button("登录账户", systemImage: "person.crop.circle") { loginPresented = true }
                }
            }
            Section("播放") {
                Button("播放与音视频", systemImage: "play.rectangle") { playbackSettingsPresented = true }
                ShareLink(item: playback.diagnosticsText) {
                    Label("导出播放诊断", systemImage: "waveform.path.ecg")
                }
                Text(appPrompt("遇到异常声音后导出最近的播放与音频路由事件，便于定位问题。"))
                    .font(.caption).foregroundStyle(.secondary)
            }
            if ["admin", "editor"].contains(api.user?.role ?? "") {
                Section("管理") { NavigationLink { AdminHomeView() } label: { Label("管理中心", systemImage: "slider.horizontal.3") } }
            }
            Section("服务器") {
                LabeledContent("当前服务器", value: api.baseURL.host() ?? "")
                Button("切换服务器", systemImage: "network") { serverPresented = true }
            }
            Section("关于") {
                LabeledContent("Treasure Up", value: "0.4.0")
                Text(appPrompt("为 iPhone 和 iPad 设计")).foregroundStyle(.secondary)
                Link(destination: URL(string: "https://github.com/guoweiyi/treasure-up")!) { Label("开源项目 · GitHub", systemImage: "chevron.left.forwardslash.chevron.right") }
                Text(appPrompt("HDR、杜比视界及杜比全景声的实际呈现取决于片源、设备与当前播放输出。"))
                    .font(.footnote).foregroundStyle(.secondary)
            }
            if api.user != nil { Section { Button("退出登录", role: .destructive) { logoutConfirmation = true } } }
            if let error { Text(appPrompt(error)).foregroundStyle(.red) }
            if let warning = api.persistenceWarning { Text(appPrompt(warning)).font(.footnote).foregroundStyle(.orange) }
        }
        .navigationTitle("设置")
        .sheet(isPresented: $loginPresented) { NavigationStack { LoginView() } }
        .sheet(isPresented: $playbackSettingsPresented) { PlaybackSettingsView(coordinator: playback) }
        .sheet(isPresented: $serverPresented) {
            NavigationStack { ServerConnectionView().toolbar { Button("取消") { serverPresented = false } } }
        }
        .confirmationDialog(appPrompt("退出当前账户？"), isPresented: $logoutConfirmation, titleVisibility: .visible) {
            Button("退出登录", role: .destructive) { Task { do { try await api.logout(); playback.stop() } catch { self.error = error.localizedDescription } } }
        }
    }
    private func roleName(_ role: String) -> String { ["admin": "管理员", "editor": "编辑者", "reader": "普通用户"][role] ?? "已登录" }
}
