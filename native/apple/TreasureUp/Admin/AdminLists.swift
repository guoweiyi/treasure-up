import SwiftUI

struct AdminRecordRow: View {
    let area: AdminArea
    let row: JSONValue
    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(area == .jobs ? adminLabel(row.adminText("kind")) : row.adminTitle)
                .font(.headline).lineLimit(2)
            if area == .jobs, !row.adminText("target_title").isEmpty {
                Text(row.adminText("target_title")).font(.subheadline).lineLimit(2)
            }
            HStack(spacing: 8) {
                switch area {
                case .jobs, .accounts, .backups: AdminStatus(value: row.adminText("status"))
                case .sources:
                    Image(systemName: row.adminBool("enabled") ? "checkmark.circle.fill" : "pause.circle")
                        .foregroundStyle(row.adminBool("enabled") ? .green : .secondary)
                    Text(adminLabel(row.adminText("kind")))
                    Text("每 \(row.adminInt("interval_minutes")) 分钟")
                case .storage:
                    Text(adminLabel(row.adminText("kind")))
                    if row.adminBool("is_default") { Text("默认写入").foregroundStyle(.tint) }
                    if !row.adminBool("enabled") { Text("已停用") }
                case .users: Text(adminLabel(row.adminText("role"))); if row.adminBool("disabled") { Text("已停用").foregroundStyle(.red) }
                case .videos: Text(row.adminText("bvid")); AdminStatus(value: row.adminText("capture_status"))
                case .creators: Text("UID \(row.adminText("uid"))"); Text("\(row.adminInt("saved_count")) 个视频")
                case .audit: Text(row.adminText("entity_type")); Text(adminDate(row.adminText("created_at")))
                }
            }.font(.caption).foregroundStyle(.secondary)
            if !row.adminText("error").isEmpty { Text(row.adminText("error")).font(.caption).foregroundStyle(.red).lineLimit(2) }
        }.padding(.vertical, 5).accessibilityElement(children: .combine)
    }
}

struct AdminListView: View {
    @Environment(APIClient.self) private var api
    @Environment(\.scenePhase) private var scenePhase
    let area: AdminArea
    @State private var rows: [JSONValue] = []
    @State private var page = 1
    @State private var total = 0
    @State private var query = ""
    @State private var status = ""
    @State private var loading = false
    @State private var error = ""
    @State private var editor: AdminEditorTarget?
    @State private var operation: AdminJobReference?
    @State private var backupConfirmation = false
    private let pageSize = 30
    var body: some View {
        List {
            if !error.isEmpty { Section { AdminNotice(message: error) } }
            if area == .jobs {
                Section {
                    Picker("状态", selection: $status) {
                        Text("全部").tag("")
                        ForEach(["queued", "running", "paused", "blocked", "partial", "succeeded", "failed", "cancelled"], id: \.self) { Text(adminLabel($0)).tag($0) }
                    }
                }
            }
            if rows.isEmpty && !loading && error.isEmpty {
                ContentUnavailableView("暂无\(area.title)", systemImage: area.icon, description: Text(area.canCreate ? "使用右上角添加按钮创建。" : "新的记录会在这里显示。"))
            }
            ForEach(rows, id: \.adminID) { row in
                if area == .jobs {
                    NavigationLink { AdminJobDetailView(jobID: row.adminID) } label: { AdminRecordRow(area: area, row: row) }
                } else if area.canEdit {
                    Button { editor = AdminEditorTarget(area: area, row: row) } label: { AdminRecordRow(area: area, row: row).foregroundStyle(.primary) }
                } else {
                    NavigationLink { AdminRecordDetail(area: area, row: row) } label: { AdminRecordRow(area: area, row: row) }
                }
            }
            if total > pageSize {
                Section {
                    HStack {
                        Button("上一页", systemImage: "chevron.left") { page -= 1; Task { await load() } }.disabled(page <= 1 || loading)
                        Spacer()
                        Text("\(page) / \(max(1, (total + pageSize - 1) / pageSize))").font(.caption).monospacedDigit()
                        Spacer()
                        Button("下一页", systemImage: "chevron.right") { page += 1; Task { await load() } }.disabled(page * pageSize >= total || loading)
                    }
                    Text("共 \(total) 条记录").font(.caption).foregroundStyle(.secondary)
                }
            }
        }
        .navigationTitle(area.title)
        .searchable(text: $query, prompt: area == .videos || area == .creators ? "搜索名称、标题" : "筛选当前页")
        .onSubmit(of: .search) { page = 1; Task { await load() } }
        .onChange(of: status) { _, _ in page = 1; Task { await load() } }
        .overlay { if loading && rows.isEmpty { ProgressView() } }
        .toolbar {
            if area.canCreate { ToolbarItem(placement: .primaryAction) { Button("添加", systemImage: "plus") { editor = AdminEditorTarget(area: area, row: nil) } } }
            if area == .backups { ToolbarItem(placement: .primaryAction) { Button("立即备份", systemImage: "plus") { backupConfirmation = true } } }
        }
        .sheet(item: $editor, onDismiss: { Task { await load() } }) { target in
            NavigationStack { AdminEditorView(area: target.area, initial: target.row) }
                .presentationSizing(.form)
        }
        .sheet(item: $operation) { job in NavigationStack { AdminJobDetailView(jobID: job.id).toolbar { ToolbarItem(placement: .cancellationAction) { Button("完成") { operation = nil } } } }.presentationSizing(.form) }
        .confirmationDialog("创建当前归档库的备份？", isPresented: $backupConfirmation, titleVisibility: .visible) {
            Button("创建备份") { Task { await createBackup() } }
        } message: { Text("备份任务在服务器执行，可在任务中心查看进度。") }
        .task(id: area) { await load() }
        .task(id: scenePhase) {
            guard scenePhase == .active, area == .jobs else { return }
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(10)) } catch { return }
                if editor == nil { await load(quiet: true) }
            }
        }
        .refreshable { await load() }
    }
    private func load(quiet: Bool = false) async {
        if !quiet { loading = true }
        defer { loading = false }
        let requestedPage = page
        let requestedQuery = query
        let requestedStatus = status
        do {
            let response: JSONValue = try await api.get(area.endpoint, query: ["page": "\(requestedPage)", "page_size": "\(pageSize)", "q": requestedQuery, "status": requestedStatus])
            guard requestedPage == page, requestedQuery == query, requestedStatus == status else { return }
            let all = response.adminItems()
            rows = requestedQuery.isEmpty || [.videos, .creators].contains(area) ? all : all.filter { $0.adminTitle.localizedCaseInsensitiveContains(requestedQuery) }
            total = response.adminInt("total")
            error = ""
        } catch is CancellationError { } catch { self.error = error.localizedDescription }
    }
    private func createBackup() async {
        do { let job: JSONValue = try await api.send("/admin/backups"); operation = AdminJobReference(id: job.adminID); await load() }
        catch { self.error = error.localizedDescription }
    }
}

struct AdminEditorTarget: Identifiable {
    let area: AdminArea
    let row: JSONValue?
    var id: String { "\(area.rawValue)-\(row?.adminID ?? "new")" }
}

struct AdminRecordDetail: View {
    let area: AdminArea
    let row: JSONValue
    var body: some View {
        Form {
            Section("记录") {
                LabeledContent("编号", value: row.adminID)
                if !row.adminText("status").isEmpty { LabeledContent("状态", value: adminLabel(row.adminText("status"))) }
                ForEach(["created_at", "snapshot_at", "completed_at"], id: \.self) { key in
                    if !row.adminText(key).isEmpty { LabeledContent(["created_at": "创建时间", "snapshot_at": "快照时间", "completed_at": "完成时间"][key] ?? key, value: adminDate(row.adminText(key))) }
                }
            }
            if area == .audit {
                Section("管理操作") {
                    LabeledContent("操作", value: row.adminText("action"))
                    LabeledContent("对象类型", value: row.adminText("entity_type"))
                    LabeledContent("对象编号", value: row.adminText("entity_id"))
                    LabeledContent("操作人", value: row.adminText("actor_id"))
                    let fields = row.adminValue("details").adminItems("fields").compactMap(\.stringValue)
                    if !fields.isEmpty { LabeledContent("修改字段", value: fields.joined(separator: "、")) }
                }
            }
            if !row.adminText("error").isEmpty { Section("错误") { AdminNotice(message: row.adminText("error")) } }
        }.navigationTitle(area == .audit ? "操作详情" : "备份详情").textSelection(.enabled)
    }
}

struct AdminJobDetailView: View {
    @Environment(APIClient.self) private var api
    @Environment(\.scenePhase) private var scenePhase
    let jobID: String
    @State private var job: JSONValue = .null
    @State private var error = ""
    @State private var busy = false
    @State private var cancelConfirmation = false
    @State private var reimportConfirmation = false
    var body: some View {
        Form {
            if !error.isEmpty { Section { AdminNotice(message: error) } }
            Section {
                Text(adminLabel(job.adminText("kind", fallback: "后台任务"))).font(.title3.bold())
                if !job.adminText("target_title").isEmpty { Text(job.adminText("target_title")) }
                AdminStatus(value: job.adminText("status"))
                let transfer = job.adminValue("result").adminValue("progress")
                let downloaded = transfer.adminInt("downloaded_bytes")
                let total = transfer.adminInt("total_bytes")
                if total > 0 { ProgressView(value: Double(downloaded), total: Double(total)); LabeledContent("已下载", value: "\(adminBytes(downloaded)) / \(adminBytes(total))") }
                if transfer.adminInt("speed_bytes_per_second") > 0 { LabeledContent("下载速度", value: "\(adminBytes(transfer.adminInt("speed_bytes_per_second")))/秒") }
                if transfer.adminInt("eta_seconds") > 0 { LabeledContent("预计剩余", value: "\(transfer.adminInt("eta_seconds")) 秒") }
                let progress = job.adminValue("checkpoint").adminValue("progress")
                if !progress.adminText("phase").isEmpty { LabeledContent("当前阶段", value: progress.adminText("phase")) }
                if !job.adminText("error").isEmpty { AdminNotice(message: job.adminText("error")) }
            }
            let capture = job.adminValue("capture")
            if capture.objectValue != nil {
                Section("保存结果") {
                    LabeledContent("视频媒体", value: adminLabel(capture.adminText("media", fallback: "尚未确认")))
                    if !capture.adminText("media_error").isEmpty { AdminNotice(message: capture.adminText("media_error")) }
                    if capture.adminBool("retry_media") || capture.adminBool("resume_media") {
                        Button(capture.adminBool("resume_media") ? "继续下载媒体" : "重试下载媒体") { Task { await run(capture.adminBool("resume_media") ? "resume" : "retry", id: capture.adminText("media_job_id")) } }
                    }
                }
            }
            if job.adminText("kind") == "probe_storage" {
                Section("存储探测") {
                    let checks = job.adminValue("result").adminValue("checks")
                    ForEach(["put", "head", "sha256_readback", "single_range"], id: \.self) { key in
                        let label = ["put": "写入对象", "head": "读取对象信息", "sha256_readback": "完整读取与 SHA-256", "single_range": "视频范围读取"][key] ?? key
                        LabeledContent(label, value: checks.adminValue(key).boolValue.map { $0 ? "通过" : "失败" } ?? "未测试")
                    }
                }
            }
            if job.adminText("kind") == "verify_account", job.adminValue("result").adminBool("logged_in") {
                Section { Label("B 站登录有效", systemImage: "checkmark.shield"); if job.adminValue("result").adminBool("vip") { Text("会员状态有效") } }
            }
            if !actions.isEmpty {
                Section("任务控制") {
                    ForEach(actions, id: \.self) { action in
                        Button(actionTitle(action), role: action == "cancel" ? .destructive : nil) {
                            if action == "cancel" { cancelConfirmation = true } else { Task { await run(action) } }
                        }.disabled(busy)
                    }
                }
            }
            if ["delete_video", "delete_creator"].contains(job.adminText("kind")) && job.adminText("status") == "succeeded" {
                Section("重新导入") {
                    if job.adminValue("result").adminBool("reimport_allowed") { Label("已允许重新导入", systemImage: "checkmark.circle") }
                    else { Button("解除重新导入限制") { reimportConfirmation = true }.disabled(busy) }
                    Text("此操作不会恢复已删除文件，已停用的备份来源仍保持停用。")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            Section("运行信息") {
                ForEach(["created_at", "started_at", "finished_at", "available_at"], id: \.self) { key in
                    if !job.adminText(key).isEmpty { LabeledContent(["created_at": "创建", "started_at": "开始", "finished_at": "结束", "available_at": "可执行时间"][key] ?? key, value: adminDate(job.adminText(key))) }
                }
                LabeledContent("尝试次数", value: "\(job.adminInt("attempts")) / \(job.adminInt("max_attempts"))")
                LabeledContent("任务编号", value: jobID)
            }.textSelection(.enabled)
        }
        .navigationTitle("任务详情")
        .confirmationDialog("取消此任务？", isPresented: $cancelConfirmation, titleVisibility: .visible) { Button("取消任务", role: .destructive) { Task { await run("cancel") } } } message: { Text("已归档的内容会保留。") }
        .confirmationDialog("允许重新导入已删除的内容？", isPresented: $reimportConfirmation, titleVisibility: .visible) {
            Button("允许重新导入") { Task { await allowReimport() } }
        } message: { Text("解除防止重复采集的标记，不会恢复已删除文件或自动启用备份来源。") }
        .task(id: jobID) { await load() }
        .task(id: scenePhase) {
            guard scenePhase == .active else { return }
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(5)) } catch { return }
                if ["queued", "running", "partial"].contains(job.adminText("status")) { await load() }
            }
        }
        .refreshable { await load() }
    }
    private var actions: [String] {
        let status = job.adminText("status")
        return ["retry", "pause", "resume", "cancel"].filter {
            ["retry": ["failed", "blocked", "partial", "cancelled"], "pause": ["queued", "running"], "resume": ["paused"], "cancel": ["queued", "running", "paused", "blocked", "partial"]][$0]?.contains(status) == true
        }
    }
    private func actionTitle(_ key: String) -> String { ["retry": "重试任务", "pause": "暂停任务", "resume": "继续任务", "cancel": "取消任务"][key] ?? key }
    private func load() async { do { job = try await api.get("/admin/jobs/\(jobID)"); error = "" } catch is CancellationError {} catch { self.error = error.localizedDescription } }
    private func allowReimport() async {
        busy = true; defer { busy = false }
        do { let _: JSONValue = try await api.send("/admin/deletions/\(jobID)/allow-reimport"); await load() }
        catch { self.error = error.localizedDescription }
    }
    private func run(_ action: String, id: String? = nil) async {
        busy = true; defer { busy = false }
        do { let _: JSONValue = try await api.send("/admin/jobs/\(id ?? jobID)/\(action)"); await load() }
        catch { self.error = error.localizedDescription }
    }
}
