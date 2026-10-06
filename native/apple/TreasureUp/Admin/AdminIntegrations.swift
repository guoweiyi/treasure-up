import SwiftUI
import UIKit

struct AdminIntegrationsView: View {
    @Environment(APIClient.self) private var api
    @State private var items: [JSONValue] = []
    @State private var error = ""
    @State private var page = 1
    @State private var total = 0
    @State private var loading = false
    @State private var creating = false
    @State private var editing: AdminTokenTarget?
    @State private var revoking: JSONValue?
    @State private var confirm = false
    var body: some View {
        List {
            Section {
                Label(appPrompt("从浏览器提交视频到归档库"), systemImage: "safari")
                Text(appPrompt("令牌只允许提交采集任务，不能读取媒体或执行管理操作。每个令牌可设置独立采集策略。")).font(.subheadline).foregroundStyle(.secondary)
            }
            if !error.isEmpty { Section { AdminNotice(message: error) } }
            if items.isEmpty && !loading { ContentUnavailableView(appPrompt("暂无采集令牌"), systemImage: "key.horizontal", description: Text(appPrompt("创建后可在已安装的浏览器采集脚本中连接此归档库。"))) }
            ForEach(items, id: \.adminID) { item in
                Section {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(item.adminTitle).font(.headline)
                        Text("采集账号：\(item.adminText("account_name"))").font(.subheadline)
                        LabeledContent("到期", value: adminDate(item.adminText("expires_at"))).font(.caption)
                        if !item.adminText("last_used_at").isEmpty { LabeledContent("上次使用", value: adminDate(item.adminText("last_used_at"))).font(.caption) }
                        if !item.adminText("revoked_at").isEmpty { Text("已撤销").foregroundStyle(.secondary) }
                        else {
                            HStack {
                                Button("编辑策略") { editing = AdminTokenTarget(row: item) }
                                Spacer()
                                Button("撤销", role: .destructive) { revoking = item; confirm = true }
                            }.buttonStyle(.borderless).padding(.top, 4)
                        }
                    }.padding(.vertical, 5)
                }
            }
            if page * 50 < total { Button("加载更多") { page += 1; Task { await load(append: true) } }.disabled(loading) }
            if loading { ProgressView() }
        }.navigationTitle("浏览器采集")
        .toolbar { ToolbarItem(placement: .primaryAction) { Button("创建令牌", systemImage: "plus") { creating = true } } }
        .sheet(isPresented: $creating, onDismiss: { Task { page = 1; await load() } }) { NavigationStack { AdminTokenEditorView(initial: nil) }.presentationSizing(.form) }
        .sheet(item: $editing, onDismiss: { Task { page = 1; await load() } }) { value in NavigationStack { AdminTokenEditorView(initial: value.row) }.presentationSizing(.form) }
        .confirmationDialog(appPrompt("撤销“\(revoking?.adminTitle ?? "")”？"), isPresented: $confirm, titleVisibility: .visible) { Button("撤销令牌", role: .destructive) { Task { await revoke() } } } message: { Text(appPrompt("使用该令牌的浏览器将不能继续提交采集，已有任务和归档会保留。")) }
        .task { await load() }
        .refreshable { page = 1; await load() }
    }
    private func load(append: Bool = false) async {
        loading = true; defer { loading = false }
        do { let response: JSONValue = try await api.get("/admin/integrations/userscript-tokens", query: ["page": "\(page)", "page_size": "50"]); items = append ? items + response.adminItems() : response.adminItems(); total = response.adminInt("total"); error = "" }
        catch { self.error = error.localizedDescription }
    }
    private func revoke() async {
        guard let revoking else { return }
        do { let _: JSONValue = try await api.send("/admin/integrations/userscript-tokens/\(revoking.adminID)", method: "DELETE"); page = 1; await load() }
        catch { self.error = error.localizedDescription }
        self.revoking = nil
    }
}

private struct AdminTokenTarget: Identifiable {
    let row: JSONValue
    var id: String { row.adminID }
}

private struct AdminTokenEditorView: View {
    @Environment(APIClient.self) private var api
    @Environment(\.dismiss) private var dismiss
    let initial: JSONValue?
    @State private var name = ""
    @State private var accountID = ""
    @State private var expires = 90
    @State private var policy: [String: JSONValue] = [:]
    @State private var accounts: [JSONValue] = []
    @State private var error = ""
    @State private var loading = true
    @State private var saving = false
    @State private var issuedSecret = ""
    @State private var copied = false
    var body: some View {
        Form {
            if !error.isEmpty { Section { AdminNotice(message: error) } }
            if loading { ProgressView() }
            else if !issuedSecret.isEmpty {
                Section {
                    Label(appPrompt("令牌已创建"), systemImage: "checkmark.shield.fill").foregroundStyle(.green)
                    LabeledContent("服务地址", value: api.baseURL.absoluteString).textSelection(.enabled)
                    Text(issuedSecret).font(.footnote.monospaced()).textSelection(.enabled)
                    Button(copied ? appPrompt("已复制令牌") : "复制令牌") {
                        // Restrict clipboard propagation and expire the transient secret.
                        UIPasteboard.general.setItems([[UIPasteboard.typeAutomatic: issuedSecret]], options: [.localOnly: true, .expirationDate: Date().addingTimeInterval(120)])
                        copied = true
                    }
                } header: { Text(appPrompt("只展示一次")) } footer: { Text(appPrompt("请将令牌粘贴到浏览器采集脚本。关闭后不会再次显示；未保存时可撤销并重新创建。")) }
            } else {
                Section("令牌设置") {
                    TextField("名称，例如 Safari", text: $name)
                    Picker("采集账号", selection: $accountID) { Text(appPrompt("请选择")).tag(""); ForEach(accounts, id: \.adminID) { Text($0.adminTitle).tag($0.adminID) } }
                    if initial == nil { Stepper("有效期：\(expires) 天", value: $expires, in: 1...365) }
                }
                AdminPolicyFields(values: $policy)
            }
        }
        .navigationTitle(initial == nil ? "创建采集令牌" : "编辑采集令牌")
        .toolbar {
            ToolbarItem(placement: .cancellationAction) { Button(issuedSecret.isEmpty ? "取消" : "完成") { issuedSecret = ""; dismiss() }.disabled(saving) }
            if issuedSecret.isEmpty { ToolbarItem(placement: .confirmationAction) { Button(saving ? appPrompt("保存中…") : "保存") { Task { await save() } }.disabled(loading || saving) } }
        }
        .interactiveDismissDisabled(saving)
        .onDisappear { issuedSecret = "" }
        .task {
            defer { loading = false }
            do {
                let response: JSONValue = try await api.get("/admin/accounts", query: ["page_size": "100"])
                accounts = response.adminItems().filter { !["disabled", "invalid", "expired"].contains($0.adminText("status")) }
                if let initial { name = initial.adminText("name"); accountID = initial.adminText("account_id"); policy = initial.adminValue("policy").objectValue ?? [:] }
                else { let settings: JSONValue = try await api.get("/admin/settings"); policy = settings.adminValue("ingest").objectValue ?? [:]; if accounts.count == 1 { accountID = accounts[0].adminID } }
            } catch { self.error = error.localizedDescription }
        }
    }
    private func save() async {
        guard !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, !accountID.isEmpty else { error = "请填写名称并选择采集账号。"; return }
        saving = true; defer { saving = false }
        var body: [String: JSONValue] = ["name": .string(name.trimmingCharacters(in: .whitespacesAndNewlines)), "account_id": .string(accountID), "policy": .object(policy)]
        if initial == nil { body["expires_days"] = .number(Double(expires)) }
        do {
            let response: JSONValue = try await api.send("/admin/integrations/userscript-tokens\(initial.map { "/" + $0.adminID } ?? "")", method: initial == nil ? "POST" : "PATCH", body: body)
            if initial != nil { dismiss() }
            else {
                issuedSecret = response.adminText("token")
                if issuedSecret.isEmpty { error = "未收到完整令牌。请返回刷新列表，撤销刚创建的令牌后再重试。" }
            }
        } catch { self.error = error.localizedDescription + (initial == nil ? "。若连接中断，请先返回列表确认是否已创建。" : "") }
    }
}
