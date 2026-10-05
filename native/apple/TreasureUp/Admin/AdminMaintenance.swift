import SwiftUI

struct AdminDeletionView: View {
    @Environment(APIClient.self) private var api
    @Environment(\.dismiss) private var dismiss
    let area: AdminArea
    let targetID: String
    let completed: () -> Void
    @State private var preview: JSONValue = .null
    @State private var loading = true
    @State private var saving = false
    @State private var error = ""
    @State private var confirmation = false
    @State private var submittedJobID: String?
    var body: some View {
        Form {
            if loading { ProgressView("正在核对关联内容…") }
            if !error.isEmpty { AdminNotice(message: error) }
            if let submittedJobID {
                Section {
                    Label("删除任务已提交", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                    Text("服务器会停止相关任务并按已核对范围清理归档。")
                    NavigationLink("查看删除进度") { AdminJobDetailView(jobID: submittedJobID) }
                    Button("完成") { completed() }
                }
            } else if preview.objectValue != nil {
                Section("即将删除") {
                    Text(preview.adminText("label")).font(.headline)
                    LabeledContent("视频", value: "\(preview.adminInt("video_count"))")
                    LabeledContent("评论", value: "\(preview.adminInt("comment_count"))")
                    LabeledContent("关联文件", value: adminBytes(preview.adminInt("asset_bytes")))
                }
                Section("影响范围") {
                    Text(area == .creators ? "将删除此 UP 主的归档资料及其视频、弹幕和评论，并清理不再使用的文件。" : "将删除本站视频、弹幕、评论和关联资料，并清理不再使用的文件。")
                    if preview.adminInt("source_count") > 0 { Text("停用 \(preview.adminInt("source_count")) 个关联来源，防止再次自动导入。") }
                    if preview.adminInt("active_job_count") > 0 { Text("停止 \(preview.adminInt("active_job_count")) 个相关任务后开始清理。") }
                    if preview.adminInt("collaboration_count") > 0 { Text("保留 \(preview.adminInt("collaboration_count")) 条其他 UP 主的联合投稿，仅移除署名关联。") }
                    if preview.adminInt("shared_asset_count") > 0 { Text("其他内容仍在使用的 \(preview.adminInt("shared_asset_count")) 个文件会保留。") }
                    if preview.adminInt("backup_protected_asset_count") > 0 { Text("已有备份引用的 \(preview.adminInt("backup_protected_asset_count")) 个文件会保留。") }
                    Text("此操作无法撤销。仅清理本站归档，不影响 B 站上的内容。").foregroundStyle(.red)
                }
                Section { Button("删除归档", role: .destructive) { confirmation = true }.disabled(saving || loading) }
            } else if !loading { Button("重新核对") { Task { await load() } } }
        }
        .navigationTitle("核对删除范围")
        .toolbar { ToolbarItem(placement: .cancellationAction) { Button(submittedJobID == nil ? "保留" : "完成") { if submittedJobID != nil { completed() } else { dismiss() } }.disabled(saving) } }
        .interactiveDismissDisabled(saving || submittedJobID != nil)
        .confirmationDialog("永久删除上述归档？", isPresented: $confirmation, titleVisibility: .visible) { Button("确认删除", role: .destructive) { Task { await remove() } } }
        .task { await load() }
    }
    private func load() async {
        loading = true; preview = .null
        defer { loading = false }
        do { preview = try await api.get("/admin/\(area.rawValue)/\(targetID)/deletion-preview") }
        catch { self.error = error.localizedDescription }
    }
    private func remove() async {
        saving = true; defer { saving = false }
        do {
            let result: JSONValue = try await api.send("/admin/\(area.rawValue)/\(targetID)/deletion", body: ["confirm": .bool(true), "preview_token": preview.adminValue("preview_token")])
            submittedJobID = result.adminText("job_id")
        } catch {
            self.error = error.localizedDescription
            // A changed preview invalidates the old token. Reload the scope, requiring
            // another explicit confirmation before another deletion attempt.
            await load()
        }
    }
}

struct AdminMigrationView: View {
    @Environment(APIClient.self) private var api
    let source: JSONValue
    @State private var profiles: [JSONValue] = []
    @State private var targetID = ""
    @State private var assetIDs = ""
    @State private var error = ""
    @State private var busy = false
    @State private var confirm = false
    @State private var operation: AdminJobReference?
    var body: some View {
        Form {
            Section("迁移") {
                LabeledContent("来源", value: source.adminTitle)
                Picker("目标位置", selection: $targetID) { Text("请选择").tag(""); ForEach(profiles, id: \.adminID) { Text($0.adminTitle).tag($0.adminID) } }
                TextField("资产 ID，用逗号或换行分隔；留空迁移全部", text: $assetIDs, axis: .vertical).textInputAutocapitalization(.never).autocorrectionDisabled()
                Text("迁移会复制并校验资产，原存储文件保留。").font(.footnote).foregroundStyle(.secondary)
                Button("开始迁移") { confirm = true }.disabled(targetID.isEmpty || busy)
            }
            if !error.isEmpty { AdminNotice(message: error) }
        }.navigationTitle("迁移存储")
        .confirmationDialog("开始复制并校验这些资产？", isPresented: $confirm, titleVisibility: .visible) { Button("开始迁移") { Task { await migrate() } } }
        .sheet(item: $operation) { job in NavigationStack { AdminJobDetailView(jobID: job.id).toolbar { ToolbarItem(placement: .cancellationAction) { Button("完成") { operation = nil } } } }.presentationSizing(.form) }
        .task { do { let data: JSONValue = try await api.get("/admin/storage", query: ["page_size": "100"]); profiles = data.adminItems().filter { $0.adminID != source.adminID && $0.adminBool("enabled") } } catch { self.error = error.localizedDescription } }
    }
    private func migrate() async {
        busy = true; defer { busy = false }
        var body: [String: JSONValue] = ["target_profile_id": .string(targetID)]
        let ids = assetIDs.components(separatedBy: CharacterSet(charactersIn: ",，\n")).map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
        if !ids.isEmpty { body["asset_ids"] = .array(ids.map(JSONValue.string)) }
        do { let job: JSONValue = try await api.send("/admin/storage/\(source.adminID)/migrate", body: body); operation = AdminJobReference(id: job.adminID) }
        catch { self.error = error.localizedDescription }
    }
}

struct AdminReplicaView: View {
    @Environment(APIClient.self) private var api
    var initialVideoID = ""
    @State private var videoID = ""
    @State private var query = ""
    @State private var videos: [JSONValue] = []
    @State private var profiles: [JSONValue] = []
    @State private var locations: [JSONValue] = []
    @State private var variants: [JSONValue] = []
    @State private var targetID = ""
    @State private var error = ""
    @State private var busy = false
    @State private var selectedLocation: JSONValue?
    @State private var purgeText = ""
    @State private var retireConfirmation = false
    @State private var purgeConfirmation = false
    @State private var operation: AdminJobReference?
    var body: some View {
        Form {
            if !error.isEmpty { Section { AdminNotice(message: error) } }
            Section("视频") {
                TextField("搜索视频", text: $query).onSubmit { Task { await search() } }
                Picker("选择视频", selection: $videoID) { Text("请选择").tag(""); ForEach(videos, id: \.adminID) { Text($0.adminTitle).tag($0.adminID) } }
            }
            if !videoID.isEmpty {
                Section("同步全部关联资产") {
                    Picker("目标存储", selection: $targetID) { Text("请选择").tag(""); ForEach(profiles, id: \.adminID) { Text($0.adminTitle).tag($0.adminID) } }
                    Button("同步到此位置") { Task { await run("/admin/videos/\(videoID)/sync", body: ["target_profile_id": .string(targetID)]) } }.disabled(targetID.isEmpty || busy)
                }
                if !variants.isEmpty {
                    Section("播放分发") {
                        ForEach(variants, id: \.adminID) { variant in
                            VStack(alignment: .leading, spacing: 10) {
                                Text("\(variant.adminText("label", fallback: variant.adminText("kind"))) · \(variant.adminText("video_codec"))").font(.headline)
                                if variant.adminText("kind") != "hls" { Button("准备原码流分片和音量分析") { Task { await run("/admin/variants/\(variant.adminID)/prepare") } }.disabled(busy) }
                                if variant.adminText("kind") == "archive" { Button("生成兼容副本") { Task { await run("/admin/variants/\(variant.adminID)/compatible") } }.disabled(busy) }
                            }
                        }
                    }
                }
                Section("\(locations.count) 个副本位置") {
                    ForEach(locations, id: \.adminID) { location in
                        VStack(alignment: .leading, spacing: 10) {
                            HStack { Text(location.adminText("profile_name")).font(.headline); Spacer(); AdminStatus(value: location.adminText("state")) }
                            Text("\(location.adminText("kind")) · \(adminBytes(location.adminInt("size")))").font(.caption).foregroundStyle(.secondary)
                            Text("校验：\(adminDate(location.adminText("verified_at")))").font(.caption).foregroundStyle(.secondary)
                            if location.adminText("state") == "retired" {
                                Button("恢复此副本") { Task { await run("/admin/storage/locations/\(location.adminID)/restore") } }.disabled(busy)
                                Button("永久删除副本", role: .destructive) { selectedLocation = location; purgeText = ""; purgeConfirmation = true }.disabled(busy)
                            } else if location.adminText("state") == "ready" {
                                Button("停用副本", role: .destructive) { selectedLocation = location; retireConfirmation = true }.disabled(busy)
                            }
                        }.padding(.vertical, 5)
                    }
                }
            }
        }.navigationTitle("副本与分发")
        .task {
            await search()
            do { let data: JSONValue = try await api.get("/admin/storage", query: ["page_size": "100"]); profiles = data.adminItems().filter { $0.adminBool("enabled") } } catch { self.error = error.localizedDescription }
            if !initialVideoID.isEmpty {
                if !videos.contains(where: { $0.adminID == initialVideoID }) {
                    do { let video: JSONValue = try await api.get("/videos/\(initialVideoID)"); videos.insert(video, at: 0) } catch { self.error = error.localizedDescription }
                }
                videoID = initialVideoID
            }
        }
        .task(id: videoID) { await load() }
        .refreshable { await load() }
        .confirmationDialog("停用此副本？", isPresented: $retireConfirmation, titleVisibility: .visible) { Button("停用副本", role: .destructive) { if let id = selectedLocation?.adminID { Task { await run("/admin/storage/locations/\(id)", method: "DELETE") } } } } message: { Text("文件仍会保留，不释放磁盘空间，可在这里恢复。") }
        .alert("永久删除副本", isPresented: $purgeConfirmation) {
            TextField("输入 DELETE", text: $purgeText).textInputAutocapitalization(.characters).autocorrectionDisabled()
            Button("取消", role: .cancel) { selectedLocation = nil }
            Button("永久删除", role: .destructive) { if purgeText == "DELETE", let id = selectedLocation?.adminID { Task { await run("/admin/storage/locations/\(id)/purge", body: ["confirm": .string("DELETE")]) } } }.disabled(purgeText != "DELETE")
        } message: { Text("服务器会先验证另一完整副本。此操作无法从本站恢复，请输入 DELETE 确认。") }
        .sheet(item: $operation, onDismiss: { Task { await load() } }) { job in NavigationStack { AdminJobDetailView(jobID: job.id).toolbar { ToolbarItem(placement: .cancellationAction) { Button("完成") { operation = nil } } } }.presentationSizing(.form) }
    }
    private func search() async {
        do { let data: JSONValue = try await api.get("/videos", query: ["q": query, "page_size": "100"]); videos = data.adminItems() }
        catch { self.error = error.localizedDescription }
    }
    private func load() async {
        guard !videoID.isEmpty else { locations = []; variants = []; return }
        do {
            let data: JSONValue = try await api.get("/admin/videos/\(videoID)/storage"); locations = data.adminItems()
            let video: JSONValue = try await api.get("/videos/\(videoID)")
            variants = video.adminItems("parts").flatMap { $0.adminItems("variants") }
        } catch is CancellationError {} catch { self.error = error.localizedDescription }
    }
    private func run(_ path: String, method: String = "POST", body: [String: JSONValue] = [:]) async {
        busy = true; defer { busy = false }
        do { let job: JSONValue = try await api.send(path, method: method, body: body); operation = AdminJobReference(id: job.adminID); error = "" }
        catch { self.error = error.localizedDescription }
    }
}

struct AccountSecurityView: View {
    @Environment(APIClient.self) private var api
    @State private var keys: [JSONValue] = []
    @State private var capabilities: JSONValue = .null
    @State private var error = ""
    @State private var loading = true
    @State private var selected: JSONValue?
    @State private var confirm = false
    @State private var password = ""
    @State private var saving = false
    var body: some View {
        Form {
            Section("当前账户") {
                LabeledContent("用户名", value: api.user?.username ?? "")
                LabeledContent("权限", value: adminLabel(api.user?.role ?? ""))
                Text("登录会话保存在系统钥匙串，媒体请求使用受保护的会话。").font(.footnote).foregroundStyle(.secondary)
            }
            if !error.isEmpty { Section { AdminNotice(message: error) } }
            Section {
                if loading { ProgressView("读取通行密钥…") }
                ForEach(keys, id: \.adminID) { key in
                    VStack(alignment: .leading, spacing: 8) {
                        Label(key.adminTitle, systemImage: "person.badge.key.fill").font(.headline)
                        Text("添加于 \(adminDate(key.adminText("created_at")))").font(.caption).foregroundStyle(.secondary)
                        if !key.adminText("last_used_at").isEmpty { Text("上次使用 \(adminDate(key.adminText("last_used_at")))").font(.caption).foregroundStyle(.secondary) }
                        Button("移除此通行密钥", role: .destructive) { selected = key; password = ""; confirm = true }.disabled(saving)
                    }.padding(.vertical, 5)
                }
                if !loading && keys.isEmpty { Text("尚未添加通行密钥。").foregroundStyle(.secondary) }
            } header: { Text("通行密钥") } footer: {
                Text(capabilities.adminBool("available") ? "已注册的通行密钥可在此管理。原生新增与登录需要服务器发布关联域名配置，并为 App 启用对应签名能力。" : capabilities.adminText("reason", fallback: "服务器尚未启用通行密钥。"))
            }
            if api.user?.role == "admin", let id = api.user?.id {
                Section {
                    NavigationLink("管理账户权限与重置密码") { AdminEditorView(area: .users, initial: .object(["id": .string(id), "username": .string(api.user?.username ?? ""), "role": .string("admin"), "disabled": .bool(false)])) }
                } footer: { Text("服务器目前未提供普通用户自助修改密码与会话列表接口。需要重置密码时请联系管理员。") }
            } else {
                Section { Text("需要修改密码时请联系管理员。当前服务器未提供自助修改密码或会话列表接口。").font(.footnote).foregroundStyle(.secondary) }
            }
        }
        .navigationTitle("账户安全")
        .task { await load() }
        .refreshable { await load() }
        .alert("移除通行密钥", isPresented: $confirm) {
            SecureField("当前账户密码", text: $password)
            Button("取消", role: .cancel) { password = ""; selected = nil }
            Button("确认移除", role: .destructive) { Task { await revoke() } }.disabled(password.isEmpty)
        } message: { Text("移除“\(selected?.adminTitle ?? "")”后，使用该密钥的登录会话也会撤销。请输入当前密码确认。") }
    }
    private func load() async {
        defer { loading = false }
        do { let response: JSONValue = try await api.get("/auth/passkeys"); keys = response.adminItems(); capabilities = try await api.get("/auth/passkeys/capabilities"); error = "" }
        catch { self.error = error.localizedDescription }
    }
    private func revoke() async {
        guard let selected else { return }
        saving = true; defer { saving = false; password = ""; self.selected = nil }
        do { let _: JSONValue = try await api.send("/auth/passkeys/\(selected.adminID)", method: "DELETE", body: ["password": .string(password)]); await load() }
        catch { self.error = error.localizedDescription }
    }
}
