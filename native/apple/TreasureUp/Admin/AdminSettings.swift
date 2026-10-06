import SwiftUI

struct AdminPolicyFields: View {
    @Binding var values: [String: JSONValue]
    var source = false
    var body: some View {
        if source {
            Section("首次检查") {
                Picker("初始范围", selection: text("initial_strategy", "all")) { Text("全部可见投稿").tag("all"); Text("最近投稿").tag("latest"); Text("仅新增投稿").tag("new_only") }
                number("首次采集上限", "initial_limit", 100, 1...10000)
                number("增量检查页数", "incremental_pages", 3, 1...100)
                number("全量检查间隔（小时）", "full_scan_interval_hours", 24, 1...720)
                Toggle("保存账号有权观看的充电视频", isOn: flag("include_paid_videos", false))
            }
        }
        Section("画质与声音") {
            Picker("目标画质", selection: text("quality", "best")) {
                Text("最高可见画质").tag("best")
                ForEach(["4320p", "2160p", "1440p", "1080p", "720p", "480p", "360p"], id: \.self) { Text($0.uppercased()).tag($0) }
            }
            Toggle("优先保存杜比视界原档", isOn: flag("prefer_dolby_vision", true))
            Toggle("优先保存杜比全景声原档", isOn: flag("prefer_dolby_atmos", true))
            Toggle("优先 H.264", isOn: flag("prefer_h264", false))
            Toggle("按需生成兼容副本", isOn: flag("create_compatible_copy", true))
            Text(appPrompt("原档画质与音轨取决于采集账号权限和来源。兼容副本保留原档；实际 HDR 与 Atmos 播放能力由设备、输出链路及媒体版本共同决定。"))
                .font(.footnote).foregroundStyle(.secondary)
        }
        Section("保存内容") {
            Toggle("下载媒体", isOn: flag("download_media", true))
            Toggle("采集评论", isOn: flag("fetch_comments", true))
            Toggle("采集弹幕", isOn: flag("fetch_danmaku", true))
            Toggle("采集字幕", isOn: flag("fetch_subtitles", true))
            Toggle("包括自动字幕", isOn: flag("include_auto_subtitles", true))
            number("单轮请求预算", "request_budget", 100, 1...10000)
            number("分页上限", "max_pages", 100, 1...10000)
            number("单视频字节上限", "max_download_bytes", 30_000_000_000, 1_000_000...500_000_000_000)
        }
        Section("评论保存预算") {
            number("主评论上限", "comment_top_limit", 500, 0...10000)
            number("扫描评论上限", "comment_scan_limit", 2000, 1...100000)
            number("回复总数上限", "comment_reply_total_limit", 200, 0...100000)
            number("每条主评论的回复上限", "comment_reply_per_root_limit", 10, 0...1000)
            number("评论图片数量上限", "comment_asset_count_limit", 300, 0...10000)
            number("评论图片字节上限", "comment_asset_bytes_limit", 26_214_400, 0...1_073_741_824)
        }
        Section("采集节奏") {
            decimal("请求间隔（秒）", "request_interval_seconds", 3, 1...120)
            decimal("图片请求间隔（秒）", "asset_interval_seconds", 0.1, 0.05...5)
            decimal("视频间隔（秒）", "video_interval_seconds", 60, 10...3600)
            decimal("随机间隔（秒）", "interval_jitter_seconds", 10, 0...300)
            number("风控冷却（秒）", "risk_cooldown_seconds", 900, 60...86400)
            Stepper("分片并发：\(values["fragment_concurrency"]?.intValue ?? 1)", value: integer("fragment_concurrency", 1, 1...3), in: 1...3)
            Toggle("限制下载速度", isOn: Binding(get: { values["download_rate_bytes"]?.intValue != nil }, set: { values["download_rate_bytes"] = $0 ? .number(10_000_000) : .null }))
            if values["download_rate_bytes"]?.intValue != nil { number("每秒字节上限", "download_rate_bytes", 10_000_000, 10000...1_000_000_000) }
        }
    }
    private func text(_ key: String, _ fallback: String) -> Binding<String> { Binding(get: { let value = values[key]?.stringValue ?? fallback; return value == "8k" ? "4320p" : value == "4k" ? "2160p" : value }, set: { values[key] = .string($0) }) }
    private func flag(_ key: String, _ fallback: Bool) -> Binding<Bool> { Binding(get: { values[key]?.boolValue ?? fallback }, set: { values[key] = .bool($0) }) }
    private func integer(_ key: String, _ fallback: Int, _ range: ClosedRange<Int>) -> Binding<Int> { Binding(get: { values[key]?.intValue ?? fallback }, set: { values[key] = .number(Double(min(range.upperBound, max(range.lowerBound, $0)))) }) }
    private func number(_ label: String, _ key: String, _ fallback: Int, _ range: ClosedRange<Int>) -> some View {
        LabeledContent(label) { TextField(label, value: integer(key, fallback, range), format: .number.grouping(.never)).keyboardType(.numberPad).multilineTextAlignment(.trailing).frame(minWidth: 70, maxWidth: 150) }
    }
    private func decimal(_ label: String, _ key: String, _ fallback: Double, _ range: ClosedRange<Double>) -> some View {
        LabeledContent(label) { TextField(label, value: Binding(get: { values[key]?.doubleValue ?? fallback }, set: { values[key] = .number(min(range.upperBound, max(range.lowerBound, $0))) }), format: .number).keyboardType(.decimalPad).multilineTextAlignment(.trailing).frame(minWidth: 70, maxWidth: 150) }
    }
}

struct AdminSettingsView: View {
    @Environment(APIClient.self) private var api
    @State private var groups: [String: JSONValue] = [:]
    @State private var ingest: [String: JSONValue] = [:]
    @State private var accounts: [JSONValue] = []
    @State private var loading = true
    @State private var saving = false
    @State private var error = ""
    @State private var message = ""
    var body: some View {
        Form {
            if loading { ProgressView(appPrompt("读取配置…")) }
            if !error.isEmpty { Section { AdminNotice(message: error) } }
            if !message.isEmpty { Section { AdminNotice(message: message, isError: false) } }
            if !loading && !groups.isEmpty {
                Section("网站展示") {
                    TextField("网站名称", text: text("display", "site_name"))
                    Toggle("默认显示弹幕", isOn: flag("display", "default_danmaku"))
                    Toggle("允许未登录用户浏览和播放", isOn: flag("display", "allow_guest_access"))
                }
                AdminPolicyFields(values: $ingest)
                Section("统计与弹幕更新") {
                    Toggle("自动更新 B 站统计", isOn: flag("statistics", "enabled"))
                    Picker("采集账号", selection: text("statistics", "account_id")) { Text(appPrompt("自动选择可用账号")).tag(""); ForEach(accounts, id: \.adminID) { Text($0.adminTitle).tag($0.adminID) } }
                    numeric("更新间隔（小时）", "statistics", "interval_hours", 1...720)
                    Toggle("同时刷新弹幕", isOn: flag("statistics", "refresh_danmaku"))
                    Toggle("同时刷新评论与置顶", isOn: flag("statistics", "refresh_comments"))
                    Button("立即刷新统计") { Task { await refreshStatistics() } }.disabled(saving)
                }
                Section("播放与分发") {
                    Toggle("为长视频准备原码流分片", isOn: flag("playback", "package_long_videos"))
                    Toggle("分析音量平衡参数", isOn: flag("playback", "analyze_loudness"))
                    numeric("分片准备时长下限（秒）", "playback", "min_duration_seconds", 0...86400)
                    numeric("目标分片时长（秒）", "playback", "segment_seconds", 2...20)
                    numeric("线路探测字节数", "playback", "probe_bytes", 16384...131072)
                }
                Section {
                    Toggle("启用定期备份", isOn: flag("backup", "enabled"))
                    TextField("独立备份目录", text: text("backup", "destination")).textInputAutocapitalization(.never).autocorrectionDisabled()
                    TextField("加密密钥标识", text: text("backup", "key_id")).textInputAutocapitalization(.never).autocorrectionDisabled()
                    numeric("备份间隔（小时）", "backup", "interval_hours", 1...720)
                    numeric("保留天数", "backup", "retention_days", 1...3650)
                } header: { Text("独立备份") } footer: { Text(appPrompt("备份目录位于服务器；密钥标识对应服务器已配置的加密密钥。")) }
            }
        }
        .navigationTitle("系统配置")
        .toolbar { ToolbarItem(placement: .confirmationAction) { Button(saving ? appPrompt("保存中…") : "保存") { Task { await save() } }.disabled(loading || saving || groups.isEmpty) } }
        .task { await load() }
    }
    private func group(_ name: String) -> [String: JSONValue] { groups[name]?.objectValue ?? [:] }
    private func set(_ name: String, _ key: String, _ value: JSONValue) { var current = group(name); current[key] = value; groups[name] = .object(current) }
    private func text(_ name: String, _ key: String) -> Binding<String> { Binding(get: { group(name)[key]?.stringValue ?? "" }, set: { set(name, key, .string($0)) }) }
    private func flag(_ name: String, _ key: String) -> Binding<Bool> { Binding(get: { group(name)[key]?.boolValue ?? false }, set: { set(name, key, .bool($0)) }) }
    private func numeric(_ label: String, _ name: String, _ key: String, _ range: ClosedRange<Int>) -> some View {
        LabeledContent(label) { TextField(label, value: Binding(get: { group(name)[key]?.intValue ?? range.lowerBound }, set: { set(name, key, .number(Double(min(range.upperBound, max(range.lowerBound, $0))))) }), format: .number.grouping(.never)).keyboardType(.numberPad).multilineTextAlignment(.trailing).frame(maxWidth: 130) }
    }
    private func load() async {
        defer { loading = false }
        do {
            let response: JSONValue = try await api.get("/admin/settings")
            groups = response.objectValue ?? [:]; ingest = response.adminValue("ingest").objectValue ?? [:]
            let choices: JSONValue = try await api.get("/admin/accounts", query: ["page_size": "100"]); accounts = choices.adminItems()
        } catch { self.error = error.localizedDescription }
    }
    private func save() async {
        saving = true; error = ""; message = ""; defer { saving = false }
        groups["ingest"] = .object(ingest)
        if group("statistics")["account_id"]?.stringValue == "" { set("statistics", "account_id", .null) }
        do { let _: JSONValue = try await api.send("/admin/settings", method: "PATCH", body: groups); message = "配置已保存" }
        catch { self.error = error.localizedDescription }
    }
    private func refreshStatistics() async {
        saving = true; defer { saving = false }
        do { let result: JSONValue = try await api.send("/admin/statistics/refresh"); message = "已加入 \(result.adminInt("queued")) 个统计更新任务" }
        catch { self.error = error.localizedDescription }
    }
}

struct AdminSourceDiscoveryView: View {
    @Environment(APIClient.self) private var api
    @Environment(\.dismiss) private var dismiss
    let accountID: String
    let selected: (JSONValue) -> Void
    @State private var category = "created"
    @State private var items: [JSONValue] = []
    @State private var page = 1
    @State private var more = false
    @State private var error = ""
    @State private var loading = false
    var body: some View {
        List {
            Picker("来源", selection: $category) { Text("创建的收藏夹").tag("created"); Text("收藏的收藏夹").tag("collected"); Text("关注的 UP 主").tag("following") }
            if !error.isEmpty { AdminNotice(message: error) }
            ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                Button { selected(item) } label: { VStack(alignment: .leading, spacing: 5) { Text(item.adminText("title")); Text("\(adminLabel(item.adminText("kind"))) · \(item.adminText("source_id"))").font(.caption).foregroundStyle(.secondary) } }
            }
            if loading { ProgressView() }
            if more { Button("加载更多") { page += 1; Task { await load(append: true) } }.disabled(loading) }
            if !loading && items.isEmpty && error.isEmpty { ContentUnavailableView(appPrompt("未发现来源"), systemImage: "antenna.radiowaves.left.and.right", description: Text(appPrompt("可返回手动输入来源链接或 ID。"))) }
        }.navigationTitle("发现备份来源")
        .toolbar { ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() } } }
        .task(id: category) { page = 1; await load() }
        .refreshable { page = 1; await load(refresh: true) }
    }
    private func load(append: Bool = false, refresh: Bool = false) async {
        loading = true; defer { loading = false }
        do { let result: JSONValue = try await api.get("/admin/source-discovery", query: ["account_id": accountID, "category": category, "page": "\(page)", "refresh": refresh ? "true" : "false"]); items = append ? items + result.adminItems() : result.adminItems(); more = result.adminBool("has_more"); error = "" }
        catch is CancellationError {} catch { self.error = error.localizedDescription }
    }
}

struct AdminSourceHistoryView: View {
    @Environment(APIClient.self) private var api
    @Environment(\.dismiss) private var dismiss
    let sourceID: String
    @State private var rows: [JSONValue] = []
    @State private var error = ""
    var body: some View {
        List {
            if !error.isEmpty { AdminNotice(message: error) }
            ForEach(rows, id: \.adminID) { row in
                VStack(alignment: .leading, spacing: 5) {
                    AdminStatus(value: row.adminText("status"))
                    Text(adminDate(row.adminText("started_at"))).font(.subheadline)
                    Text(adminLabel(row.adminText("end_reason"))).font(.caption).foregroundStyle(.secondary)
                }
            }
            if rows.isEmpty && error.isEmpty { ContentUnavailableView(appPrompt("暂无检查记录"), systemImage: "clock.arrow.circlepath") }
        }.navigationTitle("最近检查历史")
        .toolbar { ToolbarItem(placement: .cancellationAction) { Button("完成") { dismiss() } } }
        .task { do { let response: JSONValue = try await api.get("/admin/sources/\(sourceID)/history", query: ["page_size": "100"]); rows = response.adminItems() } catch { self.error = error.localizedDescription } }
    }
}
