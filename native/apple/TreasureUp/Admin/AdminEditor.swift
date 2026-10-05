import SwiftUI

struct AdminEditorView: View {
    @Environment(APIClient.self) private var api
    @Environment(\.dismiss) private var dismiss
    let area: AdminArea
    let initial: JSONValue?
    @State private var form: [String: JSONValue] = [:]
    @State private var policy: [String: JSONValue] = [:]
    @State private var config: [String: JSONValue] = [:]
    @State private var choices: [JSONValue] = []
    @State private var targets: [JSONValue] = []
    @State private var original: JSONValue = .null
    @State private var loading = true
    @State private var loaded = false
    @State private var saving = false
    @State private var error = ""
    @State private var cookie = ""
    @State private var password = ""
    @State private var accessKey = ""
    @State private var secretKey = ""
    @State private var securityToken = ""
    @State private var tags = ""
    @State private var discovery = false
    @State private var deletion = false
    @State private var operation: AdminJobReference?
    @State private var history = false
    private var id: String { initial?.adminID ?? "" }
    private var isEditing: Bool { !id.isEmpty }
    var body: some View {
        Form {
            if !error.isEmpty { Section { AdminNotice(message: error) } }
            if loading { ProgressView("正在读取…") }
            else if !loaded { Button("重新读取") { Task { await load() } } }
            else {
                switch area {
                case .videos, .creators: contentFields
                case .accounts: accountFields
                case .sources: sourceFields; sourceMonitorFields
                case .storage: storageFields
                case .users: userFields
                case .jobs: jobFields
                default: EmptyView()
                }
                if area == .sources || area == .jobs { AdminPolicyFields(values: $policy, source: area == .sources) }
                if isEditing && [.videos, .creators].contains(area) && api.user?.role == "admin" {
                    Section {
                        if area == .videos { NavigationLink("副本与分发") { AdminReplicaView(initialVideoID: id) } }
                        Button(area == .videos ? "删除此视频归档" : "删除此 UP 主与归档", role: .destructive) { deletion = true }
                    }
                }
            }
        }
        .navigationTitle(isEditing ? "编辑\(area.title)" : "添加\(area.title)")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() }.disabled(saving) }
            ToolbarItem(placement: .confirmationAction) { Button(saving ? "正在保存…" : "保存") { Task { await save() } }.bold().disabled(saving || loading || !loaded) }
        }
        .interactiveDismissDisabled(saving)
        .task { await load() }
        .sheet(isPresented: $discovery) {
            NavigationStack { AdminSourceDiscoveryView(accountID: form["account_id"]?.stringValue ?? "") { selected in
                form["kind"] = selected.adminValue("kind")
                form["source_id"] = selected.adminValue("source_id")
                form["title"] = selected.adminValue("title")
                discovery = false
            } }.presentationSizing(.form)
        }
        .sheet(isPresented: $deletion) { NavigationStack { AdminDeletionView(area: area, targetID: id) { deletion = false; dismiss() } }.presentationSizing(.form) }
        .sheet(item: $operation, onDismiss: { if area == .jobs { dismiss() } }) { job in NavigationStack { AdminJobDetailView(jobID: job.id).toolbar { ToolbarItem(placement: .cancellationAction) { Button("完成") { operation = nil } } } }.presentationSizing(.form) }
        .sheet(isPresented: $history) { NavigationStack { AdminSourceHistoryView(sourceID: id) }.presentationSizing(.form) }
    }
    @ViewBuilder private var contentFields: some View {
        Section(area == .videos ? "视频资料" : "UP 主资料") {
            if area == .videos {
                TextField("视频标题", text: text("title"), axis: .vertical)
                TextField("简介", text: text("description"), axis: .vertical).lineLimit(4...12)
                Button("恢复来源标题和简介") {
                    form["title"] = original.adminValue("source_title")
                    form["description"] = original.adminValue("source_description")
                }
                LabeledContent("BV 号", value: original.adminText("bvid"))
            } else {
                LabeledContent("来源昵称", value: original.adminText("source_name"))
                TextField("本地别名，留空使用来源", text: text("alias"))
                TextField("本地简介，留空使用来源", text: text("description_override"), axis: .vertical).lineLimit(4...12)
            }
        }
        Section("整理") {
            TextField("笔记", text: text("notes"), axis: .vertical).lineLimit(4...12)
            TextField("标签，用逗号或换行分隔", text: $tags, axis: .vertical)
        }
    }
    private var accountFields: some View {
        Section {
            TextField("账号名称", text: text("name"))
            SecureField(isEditing ? "新 Cookie，留空保留" : "授权使用的 Cookie", text: $cookie)
                .textInputAutocapitalization(.never).autocorrectionDisabled()
            if isEditing {
                LabeledContent("账号状态", value: adminLabel(original.adminText("status")))
                LabeledContent("上次验证", value: adminDate(original.adminText("last_verified_at")))
                if !original.adminText("cooldown_until").isEmpty { LabeledContent("风控冷却至", value: adminDate(original.adminText("cooldown_until"))) }
                Button("验证已保存的账号") { Task { await action("/admin/accounts/\(id)/verify") } }.disabled(saving)
            }
        } header: { Text("B 站采集账号") } footer: { Text("Cookie 仅传送给归档服务器用于采集。保存后清空输入，本站登录与 B 站账号相互独立。") }
    }
    @ViewBuilder private var sourceFields: some View {
        Section("来源") {
            if !isEditing {
                Picker("类型", selection: text("kind")) { Text("收藏夹").tag("favorite"); Text("UP 主").tag("creator") }
                TextField("来源 ID 或链接", text: text("source_id"), axis: .vertical).textInputAutocapitalization(.never).autocorrectionDisabled()
            } else { LabeledContent("来源", value: "\(adminLabel(original.adminText("kind"))) · \(original.adminText("source_id"))") }
            TextField("显示名称", text: text("title"))
            accountPicker
            if !isEditing { Button("从账号发现收藏夹与关注的 UP 主") { discovery = true }.disabled((form["account_id"]?.stringValue ?? "").isEmpty) }
        }
        Section("自动检查") {
            Toggle("启用定期检查", isOn: boolean("enabled"))
            Stepper("每 \(form["interval_minutes"]?.intValue ?? 60) 分钟", value: integer("interval_minutes", default: 60), in: 15...525600, step: 15)
            if isEditing {
                LabeledContent("上次检查", value: adminDate(original.adminText("last_scan_at")))
                LabeledContent("下次检查", value: adminDate(original.adminText("next_run_at")))
                Button("立即增量检查") { Task { await action("/admin/sources/\(id)/scan", body: ["full": .bool(false)]) } }.disabled(saving)
                Button("立即全量检查") { Task { await action("/admin/sources/\(id)/scan", body: ["full": .bool(true)]) } }.disabled(saving)
                Button("查看检查历史") { history = true }
                Text("立即检查使用服务器上已保存的策略；修改配置后请先保存。")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
    }
    @ViewBuilder private var sourceMonitorFields: some View {
        let monitor = original.adminValue("monitor")
        if isEditing && monitor.objectValue != nil {
            Section("检查结果") {
                LabeledContent("状态", value: adminLabel(monitor.adminText("status", fallback: "尚未检查")))
                LabeledContent("上次完成", value: adminDate(monitor.adminText("last_completed_at")))
                let counts = monitor.adminValue("counts")
                LabeledContent("本轮发现", value: "\(counts.adminInt("new_items"))")
                LabeledContent("等待归档", value: "\(counts.adminInt("queued"))")
                LabeledContent("已复用", value: "\(counts.adminInt("reused"))")
                let paid = monitor.adminValue("paid")
                if paid.adminInt("detected_count") > 0 {
                    LabeledContent("发现充电视频", value: "\(paid.adminInt("detected_count"))")
                    LabeledContent("等待确认", value: "\(paid.adminInt("pending_count"))")
                    Text("需要保存时，请在下方首次检查策略中允许保存账号有权观看的充电视频，保存配置后再运行全量检查。")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
        }
    }
    @ViewBuilder private var storageFields: some View {
        Section("存储位置") {
            TextField("名称", text: text("name"))
            Picker("类型", selection: text("kind")) { Text("本地目录").tag("local"); Text("S3 兼容存储").tag("s3"); Text("阿里云 OSS").tag("oss") }
                .onChange(of: form["kind"]?.stringValue) { previous, current in
                    if previous != nil && previous != current { config = [:]; accessKey = ""; secretKey = ""; securityToken = "" }
                }
            Toggle("启用", isOn: boolean("enabled"))
            Toggle("设为默认写入位置", isOn: boolean("is_default"))
        }
        Section("连接参数") {
            if (form["kind"]?.stringValue ?? "local") == "local" {
                TextField("媒体目录（服务器路径）", text: configText("root")).textInputAutocapitalization(.never).autocorrectionDisabled()
            } else {
                TextField("存储桶 Bucket", text: configText("bucket"))
                TextField("服务 Endpoint", text: configText("endpoint")).keyboardType(.URL)
                TextField("播放 Endpoint（可选）", text: configText("public_endpoint")).keyboardType(.URL)
                TextField("区域 Region", text: configText("region"))
                TextField("对象前缀", text: configText("prefix"))
                if form["kind"]?.stringValue == "s3" { Picker("桶寻址方式", selection: configText("addressing_style")) { Text("自动").tag(""); Text("Path").tag("path"); Text("Virtual host").tag("virtual") } }
            }
            Stepper("读取优先级：\(config["read_priority"]?.intValue ?? 100)", value: Binding(get: { config["read_priority"]?.intValue ?? 100 }, set: { config["read_priority"] = .number(Double($0)) }), in: 0...10000)
        }.textInputAutocapitalization(.never).autocorrectionDisabled()
        if (form["kind"]?.stringValue ?? "local") != "local" {
            Section {
                SecureField("Access Key ID", text: $accessKey)
                SecureField("Access Key Secret", text: $secretKey)
                SecureField("临时安全令牌（可选）", text: $securityToken)
            } header: { Text("凭据") } footer: { Text(isEditing ? "同一存储类型的凭据留空时保留现有值。修改类型时请重新填写。" : "凭据由服务器加密保存，不会回显。") }
        }
        if isEditing {
            Section("维护") {
                Button("探测已保存的连接能力") { Task { await action("/admin/storage/\(id)/probe") } }.disabled(saving)
                NavigationLink("迁移到其他存储位置") { AdminMigrationView(source: original) }
            }
        }
        Section { Text("切换默认写入位置不移动已有文件；迁移会复制并校验资产。修改后请先保存，再进行探测。 ").font(.footnote).foregroundStyle(.secondary) }
    }
    private var userFields: some View {
        Section {
            TextField("用户名", text: text("username")).disabled(isEditing).textInputAutocapitalization(.never).autocorrectionDisabled()
            SecureField(isEditing ? "重置密码，留空不修改" : "初始密码（至少 12 位）", text: $password).textContentType(.newPassword)
            Picker("权限", selection: text("role")) {
                Text("只读：浏览与播放").tag("reader"); Text("编辑：浏览与整理").tag("editor"); Text("管理员：全部功能").tag("admin")
            }.disabled(id == api.user?.id)
            if isEditing { Toggle("停用账号", isOn: boolean("disabled")).disabled(id == api.user?.id) }
        } header: { Text("访问权限") } footer: { Text("修改权限或重置密码会撤销此用户现有登录会话。") }
    }
    private var jobFields: some View {
        Section("采集任务") {
            Picker("类型", selection: text("kind")) {
                ForEach(["archive_video", "refresh_comments", "refresh_stats", "scan_collection", "verify_account"], id: \.self) { Text(adminLabel($0)).tag($0) }
            }.onChange(of: form["kind"]?.stringValue) { _, _ in form["target_id"] = .string(""); Task { await loadTargets() } }
            Picker("选择已有目标", selection: text("target_id")) {
                Text("选择或在下面输入").tag("")
                ForEach(targets, id: \.adminID) { Text($0.adminTitle).tag($0.adminID) }
            }
            TextField("本站目标 ID / 视频 BV 号", text: text("target_id"), axis: .vertical).textInputAutocapitalization(.never).autocorrectionDisabled()
            accountPicker
        }
    }
    private var accountPicker: some View {
        Picker("采集账号", selection: text("account_id")) {
            Text("请选择").tag("")
            ForEach(choices, id: \.adminID) { Text($0.adminTitle).tag($0.adminID) }
        }
    }
    private func text(_ key: String) -> Binding<String> { Binding(get: { form[key]?.stringValue ?? "" }, set: { form[key] = .string($0) }) }
    private func boolean(_ key: String) -> Binding<Bool> { Binding(get: { form[key]?.boolValue ?? false }, set: { form[key] = .bool($0) }) }
    private func integer(_ key: String, default fallback: Int) -> Binding<Int> { Binding(get: { form[key]?.intValue ?? fallback }, set: { form[key] = .number(Double($0)) }) }
    private func configText(_ key: String) -> Binding<String> { Binding(get: { config[key]?.stringValue ?? "" }, set: { config[key] = .string($0) }) }
    private func load() async {
        loading = true; error = ""
        defer { loading = false }
        do {
            var value = initial ?? .object([:])
            if isEditing && [.videos, .creators].contains(area) { value = try await api.get("/\(area.rawValue)/\(id)") }
            original = value
            form = value.objectValue ?? [:]
            tags = value.adminItems("tags").compactMap(\.stringValue).joined(separator: "，")
            config = value.adminValue("config").objectValue ?? [:]
            policy = value.adminValue("policy").objectValue ?? [:]
            if area == .jobs || area == .sources {
                let response: JSONValue = try await api.get("/admin/accounts", query: ["page_size": "100"])
                choices = response.adminItems()
                if form["account_id"] == nil { form["account_id"] = choices.first?.adminValue("id") ?? .string("") }
                if !isEditing {
                    let settings: JSONValue = try await api.get("/admin/settings")
                    policy = settings.adminValue("ingest").objectValue ?? [:]
                    policy["include_paid_videos"] = .bool(false)
                }
            }
            if !isEditing {
                switch area {
                case .jobs: form["kind"] = .string("archive_video"); await loadTargets()
                case .sources: form["kind"] = .string("favorite"); form["enabled"] = .bool(false); form["interval_minutes"] = .number(60)
                case .storage: form["kind"] = .string("local"); form["enabled"] = .bool(true)
                case .users: form["role"] = .string("reader")
                default: break
                }
            }
            loaded = true
        } catch { self.error = error.localizedDescription }
    }
    private func loadTargets() async {
        do {
            let kind = form["kind"]?.stringValue ?? "archive_video"
            let path = kind == "scan_collection" ? "/collections" : kind == "verify_account" ? "/admin/accounts" : "/videos"
            let response: JSONValue = try await api.get(path, query: ["page_size": "100"])
            guard kind == (form["kind"]?.stringValue ?? "archive_video") else { return }
            targets = response.adminItems()
        } catch { self.error = error.localizedDescription }
    }
    private func save() async {
        saving = true; error = ""; defer { saving = false; password = ""; cookie = ""; accessKey = ""; secretKey = ""; securityToken = "" }
        do {
            let payload = try await makePayload()
            let result: JSONValue = try await api.send("/admin/\(area.rawValue)\(isEditing ? "/" + id : "")", method: isEditing ? "PATCH" : "POST", body: payload)
            if area == .jobs { operation = AdminJobReference(id: result.adminID) } else { dismiss() }
        } catch { self.error = error.localizedDescription }
    }
    private func makePayload() async throws -> [String: JSONValue] {
        func value(_ key: String) -> JSONValue { form[key] ?? .null }
        func trimmed(_ key: String) -> String { value(key).stringValue?.trimmingCharacters(in: .whitespacesAndNewlines) ?? "" }
        func tagValues() throws -> JSONValue {
            var result: [String] = []
            for tag in tags.components(separatedBy: CharacterSet(charactersIn: ",，\n")).map({ $0.trimmingCharacters(in: .whitespacesAndNewlines) }) where !tag.isEmpty && !result.contains(tag) { result.append(tag) }
            guard result.count <= 50, result.allSatisfy({ $0.count <= 100 }) else { throw AdminError(message: "最多 50 个标签，每个标签最长 100 字。") }
            return .array(result.map { .string($0) })
        }
        switch area {
        case .videos:
            guard !trimmed("title").isEmpty else { throw AdminError(message: "请填写视频标题。") }
            return ["title_override": trimmed("title") == original.adminText("source_title") ? .null : .string(trimmed("title")), "description_override": value("description").stringValue == original.adminText("source_description") ? .null : value("description"), "notes": value("notes").stringValue.map(JSONValue.string) ?? .string(""), "tags": try tagValues()]
        case .creators:
            return ["alias": trimmed("alias").isEmpty ? .null : value("alias"), "description_override": trimmed("description_override").isEmpty ? .null : value("description_override"), "notes": value("notes").stringValue.map(JSONValue.string) ?? .string(""), "tags": try tagValues()]
        case .accounts:
            let cleanCookie = cookie.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !trimmed("name").isEmpty, isEditing || !cleanCookie.isEmpty else { throw AdminError(message: "请填写名称和 Cookie。") }
            var payload: [String: JSONValue] = ["name": .string(trimmed("name"))]
            if !cleanCookie.isEmpty { payload["cookie"] = .string(cleanCookie) }
            return payload
        case .users:
            guard (isEditing && password.isEmpty) || password.count >= 12 else { throw AdminError(message: "密码至少需要 12 位。") }
            var payload: [String: JSONValue] = ["role": value("role")]
            if isEditing { payload["disabled"] = value("disabled") } else { guard !trimmed("username").isEmpty else { throw AdminError(message: "请填写用户名。") }; payload["username"] = .string(trimmed("username")) }
            if !password.isEmpty { payload["password"] = .string(password) }
            return payload
        case .sources:
            guard !trimmed("account_id").isEmpty else { throw AdminError(message: "请先添加并选择采集账号。") }
            var payload: [String: JSONValue] = ["title": .string(trimmed("title")), "account_id": value("account_id"), "enabled": value("enabled"), "interval_minutes": value("interval_minutes"), "policy": .object(policy)]
            if !isEditing {
                let resolved: JSONValue = try await api.send("/admin/sources/resolve", body: ["value": .string(trimmed("source_id")), "kind": value("kind")])
                payload["source_id"] = resolved.adminValue("source_id"); payload["kind"] = resolved.adminValue("kind")
                if trimmed("title").isEmpty { payload["title"] = .string("\(adminLabel(resolved.adminText("kind"))) \(resolved.adminText("source_id"))") }
            } else if trimmed("title").isEmpty { throw AdminError(message: "请填写显示名称。") }
            return payload
        case .jobs:
            guard !trimmed("target_id").isEmpty else { throw AdminError(message: "请选择或填写目标。") }
            guard trimmed("kind") == "verify_account" || !trimmed("account_id").isEmpty else { throw AdminError(message: "请选择采集账号。") }
            var payload: [String: JSONValue] = ["kind": value("kind"), "target_id": .string(trimmed("target_id")), "policy": .object(policy)]
            if !trimmed("account_id").isEmpty { payload["account_id"] = value("account_id") }
            return payload
        case .storage:
            guard !trimmed("name").isEmpty else { throw AdminError(message: "请填写存储名称。") }
            guard !(value("is_default").boolValue == true && value("enabled").boolValue != true) else { throw AdminError(message: "默认写入位置必须启用。") }
            let kind = trimmed("kind")
            let cleaned = config.filter { $0.value.stringValue != "" }
            var payload: [String: JSONValue] = ["name": .string(trimmed("name")), "kind": value("kind"), "enabled": value("enabled"), "is_default": .bool(value("is_default").boolValue ?? false), "config": .object(cleaned)]
            if kind != "local" {
                guard !(cleaned["bucket"]?.stringValue ?? "").isEmpty else { throw AdminError(message: "请填写存储桶。") }
                if kind == "oss" && ((cleaned["endpoint"]?.stringValue ?? "").isEmpty || (cleaned["region"]?.stringValue ?? "").isEmpty) { throw AdminError(message: "OSS 需要 Endpoint 和 Region。") }
                for key in ["endpoint", "public_endpoint"] {
                    if let raw = cleaned[key]?.stringValue, !raw.isEmpty {
                        guard let url = URLComponents(string: raw), ["https", "http"].contains(url.scheme ?? ""), url.host != nil, url.user == nil, url.password == nil, url.query == nil, url.fragment == nil, ["", "/"].contains(url.path) else { throw AdminError(message: "Endpoint 必须为完整 HTTP(S) 地址，不能包含路径或凭据。") }
                    }
                }
                let changedKind = original.adminText("kind") != kind
                if !accessKey.isEmpty || !secretKey.isEmpty || !securityToken.isEmpty || !isEditing || changedKind || !original.adminBool("has_credentials") {
                    guard !accessKey.isEmpty, !secretKey.isEmpty else { throw AdminError(message: "请完整填写 Access Key ID 与密钥。") }
                    var credentials: [String: JSONValue] = ["access_key_id": .string(accessKey), kind == "s3" ? "secret_access_key" : "access_key_secret": .string(secretKey)]
                    if !securityToken.isEmpty { credentials[kind == "s3" ? "session_token" : "security_token"] = .string(securityToken) }
                    payload["credentials"] = .object(credentials)
                }
            } else if isEditing && original.adminText("kind") != "local" { payload["credentials"] = .object([:]) }
            return payload
        default: throw AdminError(message: "此记录不支持编辑。")
        }
    }
    private func action(_ path: String, body: [String: JSONValue] = [:]) async {
        saving = true; defer { saving = false }
        do { let job: JSONValue = try await api.send(path, body: body); operation = AdminJobReference(id: job.adminID) }
        catch { self.error = error.localizedDescription }
    }
}
