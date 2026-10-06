import SwiftUI

// The backend keeps evolving job diagnostics. Preserve unknown fields while presenting
// stable, explicitly labelled controls for all editable data.
extension JSONValue {
    func adminValue(_ key: String) -> JSONValue { objectValue?[key] ?? .null }
    func adminText(_ key: String, fallback: String = "") -> String { adminValue(key).stringValue ?? fallback }
    func adminInt(_ key: String, fallback: Int = 0) -> Int { adminValue(key).intValue ?? fallback }
    func adminBool(_ key: String, fallback: Bool = false) -> Bool { adminValue(key).boolValue ?? fallback }
    func adminItems(_ key: String = "items") -> [JSONValue] { adminValue(key).arrayValue ?? [] }
    var adminID: String { adminText("id") }
    var adminTitle: String {
        for key in ["target_title", "title", "name", "username", "action", "id"] {
            let value = adminText(key)
            if !value.isEmpty { return value }
        }
        return "未命名"
    }
}

func adminLabel(_ value: String) -> String {
    ["queued": "等待执行", "running": "正在执行", "paused": "已暂停", "blocked": "受阻",
     "partial": "部分完成", "succeeded": "已完成", "failed": "失败", "cancelled": "已取消",
     "ready": "可用", "retired": "已停用", "deleted": "已删除", "missing": "缺失",
     "archive_video": "归档视频", "scan_collection": "扫描来源", "refresh_comments": "更新评论",
     "refresh_stats": "更新统计", "verify_account": "验证采集账号", "backup": "创建备份",
     "probe_storage": "探测存储", "sync_video": "同步副本", "migrate_storage": "迁移存储",
     "prepare_media": "准备流媒体", "create_playback": "生成兼容副本", "delete_video": "删除视频",
     "delete_creator": "删除 UP 主", "local": "本地目录", "s3": "S3 兼容存储", "oss": "阿里云 OSS",
     "admin": "管理员", "editor": "内容编辑", "reader": "只读用户", "favorite": "收藏夹",
     "creator": "UP 主", "complete": "完整", "unverified": "待验证", "valid": "有效",
     "window_complete": "增量检查完成", "visible_traversal_complete": "全量检查完成",
     "unstable": "来源列表有变化，等待复查", "verified_visible_end": "已核对当前可见列表",
     "source_changed_during_scan": "检查期间来源列表发生变化", "incremental_window": "已完成增量窗口",
     "page_budget": "已保存进度，等待继续", "invalid_pagination": "来源分页异常",
     "pagination_loop": "来源分页重复", "risk_control": "触发风控，等待冷却"] [value] ?? value
}

func adminDate(_ value: String) -> String {
    guard !value.isEmpty else { return "—" }
    let fractional = ISO8601DateFormatter()
    fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    let date = fractional.date(from: value) ?? ISO8601DateFormatter().date(from: value)
    return date?.formatted(date: .abbreviated, time: .shortened) ?? value
}

func adminBytes(_ value: Int) -> String {
    ByteCountFormatter.string(fromByteCount: Int64(value), countStyle: .file)
}

struct AdminError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

struct AdminNotice: View {
    let message: String
    var isError = true
    var body: some View {
        Label(appPrompt(message), systemImage: isError ? "exclamationmark.triangle" : "checkmark.circle")
            .font(.callout).foregroundStyle(isError ? .red : .green)
            .textSelection(.enabled).accessibilityAddTraits(.updatesFrequently)
    }
}

struct AdminStatus: View {
    let value: String
    var body: some View {
        Text(adminLabel(value))
            .font(.caption.weight(.medium))
            .foregroundStyle(color)
            .padding(.horizontal, 9).padding(.vertical, 4)
            .background(color.opacity(0.1), in: Capsule())
    }
    private var color: Color {
        switch value {
        case "running", "queued": .blue
        case "succeeded", "ready", "valid", "complete": .green
        case "failed", "blocked", "missing": .red
        case "paused", "partial": .orange
        default: .secondary
        }
    }
}

struct AdminJobReference: Identifiable {
    let id: String
}

enum AdminArea: String, CaseIterable, Identifiable {
    case jobs, accounts, sources, videos, creators, storage, backups, users, audit
    var id: String { rawValue }
    var title: String {
        switch self {
        case .jobs: "任务中心"
        case .accounts: "B 站采集账号"
        case .sources: "自动备份来源"
        case .videos: "视频资料"
        case .creators: "UP 主整理"
        case .storage: "存储位置"
        case .backups: "备份记录"
        case .users: "访问权限"
        case .audit: "操作记录"
        }
    }
    var icon: String {
        switch self {
        case .jobs: "arrow.trianglehead.2.clockwise.rotate.90"
        case .accounts: "person.crop.rectangle.badge.checkmark"
        case .sources: "antenna.radiowaves.left.and.right"
        case .videos: "film.stack"
        case .creators: "person.2"
        case .storage: "externaldrive"
        case .backups: "externaldrive.badge.timemachine"
        case .users: "person.badge.key"
        case .audit: "list.bullet.clipboard"
        }
    }
    var endpoint: String { [.videos, .creators].contains(self) ? "/\(rawValue)" : "/admin/\(rawValue)" }
    var canCreate: Bool { [.jobs, .accounts, .sources, .storage, .users].contains(self) }
    var canEdit: Bool { [.accounts, .sources, .videos, .creators, .storage, .users].contains(self) }
}

struct AdminHomeView: View {
    @Environment(APIClient.self) private var api
    @State private var overview: JSONValue = .null
    @State private var error = ""
    var body: some View {
        List {
            if api.user?.role == "admin" {
                Section {
                    HStack(spacing: 16) {
                        Image(systemName: "server.rack").font(.largeTitle).foregroundStyle(.tint)
                        VStack(alignment: .leading, spacing: 4) {
                            Text("归档控制中心").font(.title3.bold())
                            Text(appPrompt("采集、存储与内容维护")).font(.subheadline).foregroundStyle(.secondary)
                        }
                    }.padding(.vertical, 8)
                    let stats = overview.adminValue("stats")
                    if stats.objectValue != nil {
                        LabeledContent("视频", value: "\(stats.adminInt("videos"))")
                        LabeledContent("UP 主", value: "\(stats.adminInt("creators"))")
                        LabeledContent("收藏夹", value: "\(stats.adminInt("collections"))")
                        LabeledContent("归档文件", value: adminBytes(stats.adminInt("assets_bytes")))
                        LabeledContent("待执行任务", value: "\(stats.adminInt("jobs_pending"))")
                    }
                }
                if !error.isEmpty { Section { AdminNotice(message: error) } }
                let protection = overview.adminValue("backup_protection")
                if protection.objectValue != nil {
                    Section("备份保护") {
                        Label(appPrompt(protection.adminBool("within_24_hours") ? "最近 24 小时内有完整备份" : "最近 24 小时内没有完整备份"), systemImage: protection.adminBool("within_24_hours") ? "checkmark.shield" : "exclamationmark.shield")
                            .foregroundStyle(protection.adminBool("within_24_hours") ? .green : .orange)
                        LabeledContent("最近快照", value: adminDate(protection.adminText("snapshot_at")))
                    }
                }
                Section("采集运行") {
                    areaLink(.jobs); areaLink(.accounts); areaLink(.sources)
                    NavigationLink { AdminIntegrationsView() } label: { Label("浏览器采集", systemImage: "safari") }
                }
            }
            if ["admin", "editor"].contains(api.user?.role ?? "") {
                Section("内容整理") { areaLink(.videos); areaLink(.creators) }
            }
            if api.user?.role == "admin" {
                Section("维护与安全") {
                    areaLink(.storage)
                    NavigationLink { AdminReplicaView() } label: { Label("副本与分发", systemImage: "externaldrive.badge.icloud") }
                    areaLink(.backups)
                    NavigationLink { AdminSettingsView() } label: { Label("系统配置", systemImage: "slider.horizontal.3") }
                    areaLink(.users); areaLink(.audit)
                }
                if !overview.adminItems("jobs").isEmpty {
                    Section("最近任务") {
                        ForEach(overview.adminItems("jobs"), id: \.adminID) { job in
                            NavigationLink { AdminJobDetailView(jobID: job.adminID) } label: { AdminRecordRow(area: .jobs, row: job) }
                        }
                    }
                }
            }
            Section { NavigationLink { AccountSecurityView() } label: { Label("账户安全", systemImage: "lock.shield") } }
        }
        .navigationTitle("管理")
        .task { await load() }
        .refreshable { await load() }
    }
    private func areaLink(_ area: AdminArea) -> some View {
        NavigationLink { AdminListView(area: area) } label: { Label(area.title, systemImage: area.icon) }
    }
    private func load() async {
        guard api.user?.role == "admin" else { return }
        do { overview = try await api.get("/admin/overview"); error = "" }
        catch { self.error = error.localizedDescription }
    }
}
