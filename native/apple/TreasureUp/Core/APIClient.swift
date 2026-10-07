import Foundation
import Observation

struct AuthenticationRequest: Identifiable, Equatable, Sendable {
    let id = UUID()
    let message: String
}

enum APIError: LocalizedError, Sendable {
    case invalidServer(String)
    case staleSession
    case invalidResponse
    case decoding
    case network(String)
    case http(status: Int, message: String, retryAfterSeconds: Double?)

    var status: Int? { if case .http(let status, _, _) = self { status } else { nil } }
    var retryAfterSeconds: Double? { if case .http(_, _, let seconds) = self { seconds } else { nil } }
    var errorDescription: String? {
        switch self {
        case .invalidServer(let message), .network(let message): message
        case .staleSession: "账号或服务器已切换，请重新打开当前内容。"
        case .invalidResponse: "服务器返回了无法识别的响应。"
        case .decoding: "服务器数据格式不兼容，请更新 App 或服务器后重试。"
        case .http(_, let message, _): message
        }
    }
}

/// A native API session has no dependency on WebKit or its cookie storage.
@MainActor @Observable
final class APIClient {
    private(set) var baseURL: URL
    private(set) var hasConfiguredServer: Bool
    private(set) var user: ArchiveUser?
    private(set) var csrfToken = ""
    private(set) var siteName = "Treasure Up"
    private(set) var isConnected = false
    private(set) var defaultDanmaku = true
    private(set) var allowGuestAccess = false
    private(set) var sessionRevision = 0
    private(set) var persistenceWarning: String?
    private(set) var loginRequest: AuthenticationRequest?
    private(set) var serverInitialized: Bool?
    private(set) var passwordLoginAvailable = true
    private(set) var accessPolicyError: String?

    @ObservationIgnored private var cookies: [HTTPCookie] = []
    @ObservationIgnored private var session: URLSession
    @ObservationIgnored private let vault = SessionCookieVault()
    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private let sessionConfiguration: URLSessionConfiguration?
    @ObservationIgnored private let persistSession: Bool
    @ObservationIgnored private var connectionRevision = 0
    @ObservationIgnored private var policyRefreshTask: Task<Void, Never>?
    @ObservationIgnored private var policyRefreshID = UUID()
    private static let serverDefaultsKey = "treasure.native.server"

    init(baseURL: URL? = nil, defaults: UserDefaults = .standard,
         sessionConfiguration: URLSessionConfiguration? = nil, persistSession: Bool = true) {
        self.defaults = defaults
        self.sessionConfiguration = sessionConfiguration
        self.persistSession = persistSession
        let configuredServer = baseURL
            ?? defaults.string(forKey: Self.serverDefaultsKey).flatMap { try? Self.normalizedServer($0) }
        // Keep URL consumers non-optional without contacting an example or personal
        // server. All requests remain disabled until the user selects a server.
        self.baseURL = configuredServer ?? URL(string: "https://unconfigured.invalid")!
        self.hasConfiguredServer = configuredServer != nil
        session = Self.makeSession(configuration: sessionConfiguration)
        if persistSession && hasConfiguredServer {
            do { cookies = try vault.load(for: self.baseURL) }
            catch { persistenceWarning = error.localizedDescription }
        }
    }

    /// Validates the native API before accepting a server change. A rejected address
    /// does not destroy the current session; accepted changes fence outstanding work.
    func connect(_ address: String) async throws {
        let candidate = try Self.normalizedServer(address)
        connectionRevision += 1
        let revision = connectionRevision
        let probeSession = Self.makeSession(configuration: sessionConfiguration)
        defer { probeSession.finishTasksAndInvalidate() }
        let url = candidate.appendingPathComponent("api/v1/server")
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 20)
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let data: Data
        let response: URLResponse
        do { (data, response) = try await probeSession.data(for: request) }
        catch is CancellationError { throw CancellationError() }
        catch { throw Self.translateNetwork(error) }
        try Task.checkCancellation()
        guard revision == connectionRevision else { throw APIError.staleSession }
        try Self.validateHTTP(response, data: data)
        let capability = try await APIResponseDecoder.decode(ServerCapabilities.self, from: data)
        guard revision == connectionRevision else { throw APIError.staleSession }
        guard capability.application == "treasure-up", capability.apiVersion == 1 else {
            throw APIError.invalidServer("该地址不是兼容的 Treasure Up 服务器。")
        }
        // Prepare the complete destination off-screen. Cancellation, a failed
        // session probe or a late response must leave the current server intact.
        let prepared = APIClient(baseURL: candidate, defaults: defaults,
                                 sessionConfiguration: sessionConfiguration, persistSession: false)
        defer { prepared.session.finishTasksAndInvalidate() }
        if candidate == baseURL {
            prepared.cookies = cookies
            prepared.persistenceWarning = persistenceWarning
        } else if persistSession {
            do { prepared.cookies = try vault.load(for: candidate) }
            catch { prepared.persistenceWarning = error.localizedDescription }
        }
        try await prepared.restoreSession(verifyServer: false)
        try Task.checkCancellation()
        guard revision == connectionRevision else { throw APIError.staleSession }
        resetMemory()
        baseURL = candidate
        cookies = prepared.cookies
        user = prepared.user
        csrfToken = prepared.csrfToken
        siteName = prepared.siteName
        defaultDanmaku = prepared.defaultDanmaku
        allowGuestAccess = prepared.allowGuestAccess
        serverInitialized = prepared.serverInitialized
        passwordLoginAvailable = prepared.passwordLoginAvailable
        accessPolicyError = prepared.accessPolicyError
        persistenceWarning = prepared.persistenceWarning
        loginRequest = nil
        if persistSession {
            do { try vault.save(cookies, for: candidate) }
            catch { persistenceWarning = error.localizedDescription }
        }
        defaults.set(candidate.absoluteString, forKey: Self.serverDefaultsKey)
        hasConfiguredServer = true
        isConnected = true
    }

    /// A 401 is a valid guest state. Network failures remain visible to the caller.
    func restoreSession() async throws {
        try await restoreSession(verifyServer: true)
    }

    private func restoreSession(verifyServer: Bool) async throws {
        guard hasConfiguredServer else { return }
        let revision = connectionRevision
        let server = baseURL
        if verifyServer {
            let capabilities: ServerCapabilities = try await get("/server")
            guard capabilities.application == "treasure-up", capabilities.apiVersion == 1 else {
                throw APIError.invalidServer("该地址不是兼容的 Treasure Up 服务器。")
            }
        }
        guard revision == connectionRevision, server == baseURL else { throw APIError.staleSession }
        do {
            let envelope: SessionEnvelope = try await get("/auth/me")
            guard revision == connectionRevision, server == baseURL else { throw APIError.staleSession }
            apply(envelope)
        } catch let error as APIError where error.status == 401 {
            // The display policy below decides whether an unsigned-in viewer can browse.
        }
        await loadDisplaySettings()
        await refreshAccessPolicy()
        try Task.checkCancellation()
        guard revision == connectionRevision, server == baseURL else { throw APIError.staleSession }
        isConnected = true
    }

    func login(username: String, password: String) async throws {
        let normalizedUsername = username.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalizedUsername.isEmpty, !password.isEmpty else {
            throw APIError.invalidServer("请输入用户名和密码。")
        }
        connectionRevision += 1
        let revision = connectionRevision
        let server = baseURL
        // Fence already-mounted views and outstanding reads before replacing identity.
        resetMemory()
        if persistSession { try vault.clear(for: baseURL) }
        let envelope: SessionEnvelope = try await send("/auth/login", body: [
            "username": .string(normalizedUsername), "password": .string(password)
        ])
        guard revision == connectionRevision, server == baseURL else { throw APIError.staleSession }
        if baseURL.scheme == "http", cookies.contains(where: { $0.name == "treasure_session" && $0.isSecure }) {
            invalidateIdentity()
            throw APIError.invalidServer(Self.httpCookieConfigurationHint)
        }
        apply(envelope)
        isConnected = true
        await loadDisplaySettings()
        await refreshAccessPolicy()
        try Task.checkCancellation()
        guard revision == connectionRevision, server == baseURL else { throw APIError.staleSession }
    }

    func logout() async throws {
        // Do not silently claim the server-side session was revoked if offline.
        connectionRevision += 1
        loginRequest = nil
        let revision = sessionRevision
        let server = baseURL
        var failure: Error?
        if user != nil {
            do { try await mutate("/auth/logout") }
            catch let error as APIError where error.status == 401 { return }
            catch { failure = error }
        }
        guard revision == sessionRevision else { throw APIError.staleSession }
        resetMemory()
        do { if persistSession { try vault.clear(for: server) }; persistenceWarning = nil }
        catch { persistenceWarning = error.localizedDescription; if failure == nil { failure = error } }
        if let failure { throw failure }
    }

    func requestLogin(message: String = "请先登录后继续。") {
        guard loginRequest == nil else { return }
        loginRequest = AuthenticationRequest(message: message)
    }

    func dismissLoginRequest() { loginRequest = nil }

    /// Foreground refreshes and concurrent denied requests share one policy read.
    func refreshAccessPolicy() async {
        guard hasConfiguredServer else { return }
        if let pending = policyRefreshTask { await pending.value; return }
        let id = UUID()
        policyRefreshID = id
        let task = Task { [weak self] in
            guard let self else { return }
            await self.loadAccessPolicy()
        }
        policyRefreshTask = task
        await withTaskCancellationHandler {
            await task.value
        } onCancel: {
            // The initiating view owns this refresh. Cancellation also releases
            // a staged connection immediately instead of waiting for its timeout.
            Task { @MainActor [weak self] in
                guard let self, self.policyRefreshID == id else { return }
                self.cancelPolicyRefresh()
            }
        }
        if policyRefreshID == id { policyRefreshTask = nil }
    }

    func get<T: Decodable & Sendable>(_ path: String, query: [String: String] = [:]) async throws -> T {
        let revision = sessionRevision
        let data = try await perform(path, method: "GET", query: query)
        let value = try await APIResponseDecoder.decode(T.self, from: data)
        guard revision == sessionRevision else { throw APIError.staleSession }
        return value
    }

    func send<T: Decodable & Sendable>(_ path: String, method: String = "POST", body: [String: JSONValue] = [:]) async throws -> T {
        let revision = sessionRevision
        let data = try await perform(path, method: method, body: body)
        let value = try await APIResponseDecoder.decode(T.self, from: data)
        guard revision == sessionRevision else { throw APIError.staleSession }
        return value
    }

    func mutate(_ path: String, method: String = "POST", body: [String: JSONValue] = [:]) async throws {
        _ = try await perform(path, method: method, body: body)
    }

    /// Downloads authenticated sidecars. Absolute external URLs never receive our cookie.
    func data(_ path: String) async throws -> Data {
        guard let url = resolveURL(path) else { throw APIError.invalidServer("媒体地址无效。") }
        return try await performURL(url, method: "GET", body: nil)
    }

    func resolveURL(_ path: String?) -> URL? {
        guard let path, !path.isEmpty,
              let url = URL(string: path, relativeTo: baseURL)?.absoluteURL,
              ["http", "https"].contains(url.scheme?.lowercased() ?? ""),
              url.user == nil, url.password == nil else { return nil }
        if baseURL.scheme == "https" && url.scheme != "https" { return nil }
        return url
    }

    /// Feed only these cookies to AVURLAssetHTTPCookiesKey, never a global cookie jar.
    func mediaCookies(for url: URL) -> [HTTPCookie] {
        guard Self.sameOrigin(url, baseURL) else { return [] }
        return cookies.filter { cookie in
            let prefix = cookie.path.hasSuffix("/") ? cookie.path : cookie.path + "/"
            return SessionCookieVault.belongsToServer(cookie, server: baseURL)
                && (!cookie.isSecure || url.scheme == "https")
                && (cookie.expiresDate ?? .distantFuture) > Date()
                && (url.path == cookie.path || url.path.hasPrefix(prefix))
        }
    }

    private func perform(_ path: String, method: String, query: [String: String] = [:], body: [String: JSONValue]? = nil) async throws -> Data {
        guard !path.contains("://"), !path.hasPrefix("//") else {
            throw APIError.invalidServer("API 请求必须使用当前服务器的相对路径。")
        }
        let prefix = path.hasPrefix("/api/v1/") || path == "/api/v1" ? "" : "/api/v1"
        let fullPath = prefix + (path.hasPrefix("/") ? path : "/" + path)
        guard let resolved = URL(string: fullPath, relativeTo: baseURL)?.absoluteURL,
              Self.sameOrigin(resolved, baseURL), var components = URLComponents(url: resolved, resolvingAgainstBaseURL: true) else {
            throw APIError.invalidServer("API 请求地址无效。")
        }
        if !query.isEmpty {
            components.queryItems = (components.queryItems ?? []) + query.sorted(by: { $0.key < $1.key }).map {
                URLQueryItem(name: $0.key, value: $0.value)
            }
        }
        guard let url = components.url else { throw APIError.invalidServer("API 请求参数无效。") }
        return try await performURL(url, method: method, body: body)
    }

    private func performURL(_ url: URL, method: String, body: [String: JSONValue]?) async throws -> Data {
        guard hasConfiguredServer else {
            throw APIError.invalidServer("请先连接自己的 Treasure Up 服务器。")
        }
        let revision = sessionRevision
        let requestSession = session
        let ownOrigin = Self.sameOrigin(url, baseURL)
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 45)
        request.httpMethod = method.uppercased()
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if ownOrigin {
            for (name, value) in HTTPCookie.requestHeaderFields(with: mediaCookies(for: url)) {
                request.setValue(value, forHTTPHeaderField: name)
            }
            if !["GET", "HEAD", "OPTIONS"].contains(request.httpMethod ?? "GET") {
                request.setValue(baseURL.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/")), forHTTPHeaderField: "Origin")
                request.setValue(csrfToken, forHTTPHeaderField: "X-CSRF-Token")
            }
        }
        if let body {
            request.httpBody = try JSONEncoder().encode(body)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let data: Data
        let response: URLResponse
        do { (data, response) = try await requestSession.data(for: request) }
        catch {
            guard revision == sessionRevision else { throw APIError.staleSession }
            if Task.isCancelled { throw CancellationError() }
            throw Self.translateNetwork(error)
        }
        guard revision == sessionRevision else { throw APIError.staleSession }
        guard let http = response as? HTTPURLResponse else { throw APIError.invalidResponse }
        let responseIsSameOrigin = http.url.map { Self.sameOrigin($0, baseURL) } ?? false
        if let responseURL = http.url, responseIsSameOrigin {
            acceptCookies(from: http, url: responseURL)
        }
        // An asset can redirect to signed object storage. Its expired signature
        // does not revoke the user's independent application session.
        let isPublicMetadata = ["/api/v1/auth/status", "/api/v1/server", "/api/v1/library/settings"].contains(url.path)
        if http.statusCode == 401 && ownOrigin && responseIsSameOrigin,
           url.path.hasPrefix("/api/v1/"), !isPublicMetadata {
            let hadUser = user != nil
            invalidateIdentity()
            let isSessionProbe = ["/api/v1/auth/me", "/api/v1/auth/login", "/api/v1/auth/logout",
                                  "/api/v1/auth/status", "/api/v1/server", "/api/v1/library/settings"].contains(url.path)
            if !isSessionProbe {
                // Guests have no identity to clear, but their protected images,
                // player and outstanding reads must still be invalidated once.
                if loginRequest == nil, revision == sessionRevision { sessionRevision += 1 }
                requestLogin(message: hadUser ? "登录状态已失效，请重新登录后继续。" : "请登录后继续浏览。")
                cancelPolicyRefresh()
                Task { [weak self] in await self?.refreshAccessPolicy() }
            }
        }
        try Self.validateHTTP(http, data: data)
        if ownOrigin, responseIsSameOrigin, baseURL.scheme == "http", user == nil,
           url.path == "/api/v1/playback-sessions", method.uppercased() == "POST",
           cookies.contains(where: { $0.name == "treasure_viewer" && $0.isSecure }) {
            throw APIError.invalidServer(Self.httpCookieConfigurationHint)
        }
        return data
    }

    private func acceptCookies(from response: HTTPURLResponse, url: URL) {
        var headers: [String: String] = [:]
        for (key, value) in response.allHeaderFields { headers[String(describing: key)] = String(describing: value) }
        // Foundation can discard a Secure cookie while parsing an HTTP response.
        // Inspect its metadata using the same host/path with an HTTPS parsing
        // origin so we can report that deployment error. The actual request URL
        // stays unchanged, and mediaCookies still refuses to send it over HTTP.
        var cookieOrigin = URLComponents(url: url, resolvingAgainstBaseURL: false)
        if url.scheme == "http" {
            cookieOrigin?.scheme = "https"
            cookieOrigin?.port = url.port ?? 80
        }
        let received = HTTPCookie.cookies(withResponseHeaderFields: headers, for: cookieOrigin?.url ?? url)
            .filter { SessionCookieVault.belongsToServer($0, server: baseURL) }
        guard !received.isEmpty else { return }
        for cookie in received {
            cookies.removeAll { $0.name == cookie.name && $0.path == cookie.path }
            if (cookie.expiresDate ?? .distantFuture) > Date() { cookies.append(cookie) }
        }
        do { if persistSession { try vault.save(cookies, for: baseURL) }; persistenceWarning = nil }
        catch { persistenceWarning = error.localizedDescription }
    }

    private func apply(_ envelope: SessionEnvelope) {
        if user != envelope.user || csrfToken != envelope.csrfToken { sessionRevision += 1 }
        user = envelope.user
        csrfToken = envelope.csrfToken
        loginRequest = nil
    }

    private func invalidateIdentity() {
        let hadIdentity = user != nil || !csrfToken.isEmpty || cookies.contains { $0.name == "treasure_session" }
        user = nil
        csrfToken = ""
        cookies.removeAll { $0.name == "treasure_session" }
        if hadIdentity { sessionRevision += 1 }
        do { if persistSession { try vault.save(cookies, for: baseURL) } }
        catch { persistenceWarning = error.localizedDescription }
    }

    private func resetMemory() {
        cancelPolicyRefresh()
        sessionRevision += 1
        session.invalidateAndCancel()
        session = Self.makeSession(configuration: sessionConfiguration)
        user = nil
        csrfToken = ""
        cookies = []
    }

    private func loadDisplaySettings() async {
        let revision = sessionRevision
        let connection = connectionRevision
        do {
            let settings: DisplaySettings = try await get("/library/settings")
            guard revision == sessionRevision, connection == connectionRevision, !Task.isCancelled else { return }
            siteName = settings.siteName.flatMap { $0.isEmpty ? nil : $0 } ?? "Treasure Up"
            defaultDanmaku = settings.defaultDanmaku ?? true
        } catch { /* Display metadata does not override the authentication policy. */ }
    }

    private func loadAccessPolicy() async {
        let revision = sessionRevision
        let connection = connectionRevision
        let isCurrent = { revision == self.sessionRevision && connection == self.connectionRevision && !Task.isCancelled }
        do {
            let status: AuthenticationStatus
            do { status = try await get("/auth/status") }
            catch let error as APIError where error.status == 404 {
                let settings: DisplaySettings = try await get("/library/settings")
                status = AuthenticationStatus(initialized: nil, allowGuestAccess: settings.allowGuestAccess,
                                              passwordLogin: true)
            }
            guard isCurrent() else { return }
            guard let allowed = status.allowGuestAccess else { throw APIError.decoding }
            serverInitialized = status.initialized
            passwordLoginAvailable = status.passwordLogin ?? true
            accessPolicyError = nil
            updateGuestAccess(allowed)
        } catch {
            guard isCurrent(), !(error is CancellationError) else { return }
            accessPolicyError = error.localizedDescription
            // Fail closed until an explicit retry succeeds. An unavailable policy
            // is shown as an error, never as an expired-password explanation.
            updateGuestAccess(false)
        }
    }

    private func updateGuestAccess(_ allowed: Bool) {
        if allowGuestAccess && !allowed && user == nil { sessionRevision += 1 }
        allowGuestAccess = allowed
    }

    private func cancelPolicyRefresh() {
        policyRefreshTask?.cancel()
        policyRefreshTask = nil
        policyRefreshID = UUID()
    }

    private static let httpCookieConfigurationHint = "服务器将登录或播放 Cookie 标记为 Secure，HTTP 无法使用。请改用 HTTPS，或由管理员在 HTTP 部署中设置 TREASURE_COOKIE_SECURE=false 后重试。"

    static func normalizedServer(_ address: String) throws -> URL {
        var value = address.trimmingCharacters(in: .whitespacesAndNewlines)
        if !value.contains("://") { value = "https://" + value }
        guard var parts = URLComponents(string: value),
              let scheme = parts.scheme?.lowercased(), ["https", "http"].contains(scheme),
              let host = parts.host?.lowercased(), !host.isEmpty,
              parts.user == nil, parts.password == nil,
              parts.port.map({ (1...65535).contains($0) }) ?? true,
              parts.query == nil, parts.fragment == nil,
              parts.path.isEmpty || parts.path == "/" else {
            throw APIError.invalidServer("请输入服务器根地址，例如 https://video.example.com。")
        }
        parts.scheme = scheme
        parts.host = host
        parts.path = ""
        if (scheme == "https" && parts.port == 443) || (scheme == "http" && parts.port == 80) { parts.port = nil }
        guard let url = parts.url else { throw APIError.invalidServer("服务器地址无效。") }
        return url
    }

    private static func sameOrigin(_ lhs: URL, _ rhs: URL) -> Bool {
        lhs.scheme?.lowercased() == rhs.scheme?.lowercased()
            && lhs.host?.lowercased() == rhs.host?.lowercased()
            && (lhs.port ?? (lhs.scheme == "https" ? 443 : 80)) == (rhs.port ?? (rhs.scheme == "https" ? 443 : 80))
    }

    private static func makeSession(configuration injected: URLSessionConfiguration? = nil) -> URLSession {
        let configuration = injected ?? URLSessionConfiguration.ephemeral
        configuration.httpCookieStorage = nil
        configuration.httpShouldSetCookies = false
        configuration.urlCredentialStorage = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.urlCache = nil
        configuration.timeoutIntervalForRequest = 45
        configuration.timeoutIntervalForResource = 120
        // Interactive screens need an immediate offline error and an explicit retry;
        // waiting for connectivity would leave initial connection UI spinning.
        configuration.waitsForConnectivity = false
        return URLSession(configuration: configuration, delegate: CredentialSafeRedirectDelegate(), delegateQueue: nil)
    }

    private static func validateHTTP(_ response: URLResponse, data: Data) throws {
        guard let response = response as? HTTPURLResponse else { throw APIError.invalidResponse }
        guard !(200...299).contains(response.statusCode) else { return }
        let json = try? JSONDecoder().decode(JSONValue.self, from: data)
        let detail = json?["detail"]
        let validationMessages = detail?.arrayValue?.compactMap { $0["msg"]?.stringValue }.joined(separator: "；")
        let message = detail?.stringValue ?? (validationMessages?.isEmpty == false ? validationMessages : nil)
            ?? (response.statusCode == 401 ? "请先登录。" : "请求失败（\(response.statusCode)）。")
        let retry = response.value(forHTTPHeaderField: "Retry-After").flatMap { value -> Double? in
            if let seconds = Double(value) { return min(86_400, max(1, seconds)) }
            let formatter = DateFormatter()
            formatter.locale = Locale(identifier: "en_US_POSIX")
            formatter.timeZone = TimeZone(secondsFromGMT: 0)
            formatter.dateFormat = "EEE',' dd MMM yyyy HH':'mm':'ss z"
            return formatter.date(from: value).map { min(86_400, max(1, $0.timeIntervalSinceNow)) }
        }
        throw APIError.http(status: response.statusCode, message: message, retryAfterSeconds: retry)
    }

    private static func translateNetwork(_ error: Error) -> Error {
        guard let error = error as? URLError else { return APIError.network(error.localizedDescription) }
        switch error.code {
        case .cancelled: return CancellationError()
        case .notConnectedToInternet, .networkConnectionLost: return APIError.network("网络连接已断开，请检查网络后重试。")
        case .timedOut: return APIError.network("服务器响应超时，请稍后重试。")
        case .cannotFindHost, .cannotConnectToHost, .dnsLookupFailed: return APIError.network("无法连接服务器，请检查地址和网络。")
        case .secureConnectionFailed, .serverCertificateUntrusted, .serverCertificateHasBadDate:
            return APIError.network("无法验证服务器的 HTTPS 证书，请检查服务器配置。")
        case .appTransportSecurityRequiresSecureConnection:
            return APIError.network("当前安装版本不支持此 HTTP 连接，请更新 App 或使用 HTTPS 地址。")
        default: return APIError.network(error.localizedDescription)
        }
    }

    private struct SessionEnvelope: Decodable, Sendable { var user: ArchiveUser; var csrfToken: String }
    private struct ServerCapabilities: Decodable, Sendable { var application: String; var apiVersion: Int }
    private struct DisplaySettings: Decodable, Sendable {
        var siteName: String?
        var defaultDanmaku: Bool?
        var allowGuestAccess: Bool?
    }
    private struct AuthenticationStatus: Decodable, Sendable {
        var initialized: Bool?
        var allowGuestAccess: Bool?
        var passwordLogin: Bool?
    }
}

/// Network I/O is asynchronous, but JSONDecoder itself is synchronous. Large
/// catalog/comment payloads must not monopolize the UI actor during playback.
enum APIResponseDecoder {
    nonisolated static func decode<T: Decodable & Sendable>(_ type: T.Type, from data: Data) async throws -> T {
        try Task.checkCancellation()
        let worker = Task.detached(priority: .userInitiated) {
            try Task.checkCancellation()
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            let value: T
            do { value = try decoder.decode(type, from: data) }
            catch { throw APIError.decoding }
            try Task.checkCancellation()
            return value
        }
        return try await withTaskCancellationHandler {
            let value = try await worker.value
            try Task.checkCancellation()
            return value
        } onCancel: {
            worker.cancel()
        }
    }
}

/// Foundation may preserve explicit headers across redirects. Strip every credential
/// when a media request is redirected to signed object storage, and never redirect writes.
private final class CredentialSafeRedirectDelegate: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        guard let old = response.url, let new = request.url,
              ["http", "https"].contains(new.scheme?.lowercased() ?? ""),
              !(old.scheme == "https" && new.scheme != "https") else { completionHandler(nil); return }
        let oldPort = old.port ?? (old.scheme == "https" ? 443 : 80)
        let newPort = new.port ?? (new.scheme == "https" ? 443 : 80)
        let crossedOrigin = old.host?.lowercased() != new.host?.lowercased() || old.scheme != new.scheme || oldPort != newPort
        guard !crossedOrigin || ["GET", "HEAD"].contains(task.originalRequest?.httpMethod ?? "GET") else {
            completionHandler(nil); return
        }
        var safe = request
        if crossedOrigin {
            for header in ["Cookie", "Authorization", "X-CSRF-Token", "Origin"] { safe.setValue(nil, forHTTPHeaderField: header) }
        }
        completionHandler(safe)
    }
}
