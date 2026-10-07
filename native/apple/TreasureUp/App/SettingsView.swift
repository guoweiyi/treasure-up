import SwiftUI

struct ServerConnectionView: View {
    @Environment(APIClient.self) private var api
    @Environment(\.dismiss) private var dismiss
    var initialError: String?
    @State private var address = ""
    @State private var error: String?
    @State private var busy = false
    @State private var connectionTask: Task<Void, Never>?
    @State private var operation = UUID()
    var body: some View {
        Form {
            Section {
                VStack(alignment: .leading, spacing: 14) {
                    BrandLockup(size: 164)
                    Text(appPrompt("你的收藏，原生呈现。")).font(.title.bold())
                    Text(appPrompt("连接 Treasure Up，浏览已归档的视频、片单与创作者。")).foregroundStyle(.secondary)
                }.padding(.vertical, 16)
            }
            Section {
                TextField("https://example.com", text: $address)
                    .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    .accessibilityIdentifier("serverAddress")
                Button(action: connect) {
                    HStack { Text("连接服务器"); Spacer(); if busy { ProgressView() } else { Image(systemName: "arrow.right") } }
                }
                    .disabled(busy || address.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    .accessibilityIdentifier("connectServer")
            } header: { Text("服务器") } footer: {
                Text(appPrompt("支持 HTTPS 和 HTTP 站点根地址，未填写协议时使用 HTTPS。每台服务器的登录状态独立保存，切换时会停止当前播放。"))
            }
            if let message = error ?? initialError { Section { Text(appPrompt(message)).foregroundStyle(.red).accessibilityLabel(appPrompt("连接失败：\(message)")) } }
        }
        .navigationTitle("连接资料库")
        .onAppear { address = api.hasConfiguredServer ? api.baseURL.absoluteString : "" }
        .onDisappear {
            operation = UUID()
            connectionTask?.cancel()
            connectionTask = nil
            busy = false
        }
        .disabled(busy)
    }

    private func connect() {
        guard !busy else { return }
        let submittedAddress = address
        let token = UUID()
        operation = token
        busy = true
        error = nil
        connectionTask = Task {
            do {
                try await api.connect(submittedAddress)
                guard !Task.isCancelled, token == operation else { return }
                busy = false
                connectionTask = nil
                dismiss()
            } catch {
                guard !Task.isCancelled, token == operation else { return }
                self.error = error.localizedDescription
                busy = false
                connectionTask = nil
            }
        }
    }
}

struct LoginView: View {
    private enum Action { case login, refreshPolicy }

    @Environment(APIClient.self) private var api
    @Environment(\.dismiss) private var dismiss
    var requiresLogin = false
    var authenticationMessage: String?
    @State private var username = ""
    @State private var password = ""
    @State private var serverPresented = false
    @State private var activeAction: Action?
    @State private var error: String?
    @State private var operationTask: Task<Void, Never>?
    @State private var operation = UUID()
    @FocusState private var passwordFocused: Bool

    private var busy: Bool { activeAction != nil }
    private var canUsePassword: Bool { api.serverInitialized != false && api.passwordLoginAvailable }
    private var canSubmit: Bool {
        canUsePassword && !busy && !username.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !password.isEmpty
    }

    var body: some View {
        Form {
            Section {
                BrandIdentity(subtitle: appPrompt(requiresLogin ? "登录后浏览你的资料库" : "登录后同步你的片单和播放进度"))
                    .padding(.vertical, 12)
                if let authenticationMessage {
                    Text(appPrompt(authenticationMessage))
                        .foregroundStyle(.secondary)
                        .accessibilityIdentifier("loginRequestMessage")
                }
                if canUsePassword {
                    TextField("用户名", text: $username).textContentType(.username)
                        .textInputAutocapitalization(.never).autocorrectionDisabled().submitLabel(.next)
                        .onSubmit { passwordFocused = true }.accessibilityIdentifier("loginUsername")
                    SecureField("密码", text: $password).textContentType(.password).focused($passwordFocused)
                        .submitLabel(.go).onSubmit { login() }.accessibilityIdentifier("loginPassword")
                    Button(action: login) {
                        HStack { Text("登录"); Spacer(); if activeAction == .login { ProgressView() } }
                    }.disabled(!canSubmit).accessibilityIdentifier("loginSubmit")
                } else {
                    Label(appPrompt(api.serverInitialized == false ? "服务器尚未初始化" : "密码登录未启用"), systemImage: "info.circle")
                    Text(appPrompt(api.serverInitialized == false
                                   ? "请由管理员在网页端完成首次初始化，再回来重试。"
                                   : "服务器当前未开放密码登录，请联系管理员启用后重试。"))
                        .foregroundStyle(.secondary)
                }
            } footer: { Text(api.baseURL.absoluteString) }
            if let message = error ?? api.accessPolicyError { Text(appPrompt(message)).foregroundStyle(.red) }
            Section {
                Button(action: refreshPolicy) {
                    HStack {
                        Label("刷新登录状态", systemImage: "arrow.clockwise")
                        Spacer()
                        if activeAction == .refreshPolicy { ProgressView() }
                    }
                }.disabled(busy).accessibilityIdentifier("refreshLoginStatus")
            }
        }
        .navigationTitle("登录")
        .disabled(busy)
        .toolbar { ToolbarItem(placement: .cancellationAction) {
            if requiresLogin { Button("切换服务器") { serverPresented = true } }
            else { Button("取消") { cancelOperation(); api.dismissLoginRequest(); dismiss() } }
        } }
        .sheet(isPresented: $serverPresented) {
            NavigationStack {
                ServerConnectionView().toolbar { Button("取消") { serverPresented = false } }
            }
        }
        .onChange(of: api.baseURL) { _, _ in
            cancelOperation()
            username = ""; password = ""; error = nil
        }
        .onChange(of: canUsePassword) { _, available in
            if !available { password = ""; passwordFocused = false }
        }
        .onDisappear { cancelOperation(preservingCommittedLogin: true); password = "" }
    }
    private func login() {
        guard canSubmit else { return }
        let submittedUsername = username
        let submittedPassword = password
        let server = api.baseURL
        let token = UUID()
        operation = token
        activeAction = .login; error = nil; passwordFocused = false
        operationTask = Task {
            do {
                try await api.login(username: submittedUsername, password: submittedPassword)
                guard !Task.isCancelled, operation == token, api.baseURL == server else { return }
                password = ""; activeAction = nil; operationTask = nil
                if !requiresLogin { dismiss() }
            } catch {
                guard !Task.isCancelled, operation == token, api.baseURL == server else { return }
                self.error = error.localizedDescription
                activeAction = nil; operationTask = nil
            }
        }
    }

    private func refreshPolicy() {
        guard !busy else { return }
        let server = api.baseURL
        let token = UUID()
        operation = token
        activeAction = .refreshPolicy; error = nil
        operationTask = Task {
            await api.refreshAccessPolicy()
            guard !Task.isCancelled, operation == token, api.baseURL == server else { return }
            activeAction = nil; operationTask = nil
        }
    }

    private func cancelOperation(preservingCommittedLogin: Bool = false) {
        operation = UUID()
        if !preservingCommittedLogin || api.user == nil { operationTask?.cancel() }
        operationTask = nil
        activeAction = nil
    }
}

struct SettingsView: View {
    @Environment(APIClient.self) private var api
    @Environment(PlaybackCoordinator.self) private var playback
    @State private var serverPresented = false
    @State private var playbackSettingsPresented = false
    @State private var logoutConfirmation = false
    @State private var error: String?
    var body: some View {
        Form {
            Section {
                if let user = api.user {
                    Label { VStack(alignment: .leading, spacing: 4) { Text(user.username).font(.headline); Text(roleName(user.role)).font(.caption).foregroundStyle(.secondary) } } icon: {
                        Image(systemName: "person.crop.circle.fill").font(.largeTitle).foregroundStyle(TreasureBrand.accent)
                    }.padding(.vertical, 6)
                    NavigationLink { SavedPlaylistsView() } label: { Label("收藏片单", systemImage: "bookmark") }
                    NavigationLink { AccountSecurityView() } label: { Label("账户与安全", systemImage: "lock.shield") }
                } else {
                    Button("登录账户", systemImage: "person.crop.circle") { api.requestLogin() }
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
                LabeledContent("当前服务器", value: api.baseURL.absoluteString)
                Button("切换服务器", systemImage: "network") { serverPresented = true }
            }
            Section("关于") {
                HStack { Spacer(); BrandLockup(); Spacer() }.padding(.vertical, 8)
                LabeledContent("版本", value: TreasureBrand.version)
                LabeledContent("构建", value: TreasureBrand.build)
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
