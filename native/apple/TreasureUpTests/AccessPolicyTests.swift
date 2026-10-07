import XCTest
@testable import TreasureUp

@MainActor
final class AccessPolicyTests: XCTestCase {
    func testPublicAndPrivateServersRestoreGuestsWithoutRequestingAnotherLogin() async throws {
        for allowsGuests in [true, false] {
            let server = AccessPolicyServer(allowsGuests: allowsGuests)
            let client = makeClient(server)
            try await client.restoreSession()
            XCTAssertTrue(client.isConnected)
            XCTAssertEqual(client.allowGuestAccess, allowsGuests)
            XCTAssertEqual(client.serverInitialized, true)
            XCTAssertTrue(client.passwordLoginAvailable)
            XCTAssertNil(client.user)
            XCTAssertNil(client.loginRequest, "An anonymous session probe must not stack a login sheet over the root gate")
            XCTAssertNil(client.accessPolicyError)
        }
    }

    func testAuthStatusIsAuthoritativeAndOldServersCanUseDisplayPolicy() async throws {
        let server = AccessPolicyServer(allowsGuests: false)
        server.statusOverride = .init(body: """
        {"initialized":false,"allow_guest_access":false,"password_login":false,"passkeys_enabled":true}
        """)
        server.displayAllowsGuests = true
        let client = makeClient(server)
        await client.refreshAccessPolicy()
        XCTAssertFalse(client.allowGuestAccess, "The current authentication policy wins over stale display settings")
        XCTAssertEqual(client.serverInitialized, false)
        XCTAssertFalse(client.passwordLoginAvailable)

        server.statusOverride = .init(status: 404, body: "{\"detail\":\"Not Found\"}")
        await client.refreshAccessPolicy()
        XCTAssertTrue(client.allowGuestAccess, "Servers predating /auth/status still expose /library/settings")
    }

    func testPolicyTighteningInvalidatesGuestContentAndUnavailablePolicyFailsClosed() async throws {
        let server = AccessPolicyServer(allowsGuests: true)
        let client = makeClient(server)
        await client.refreshAccessPolicy()
        let originalRevision = client.sessionRevision
        let artwork = ArchiveImageRequest(api: client, path: "/api/v1/assets/cover", maximumPixelSize: 128)
        server.allowsGuests = false
        await client.refreshAccessPolicy()
        XCTAssertFalse(client.allowGuestAccess)
        XCTAssertGreaterThan(client.sessionRevision, originalRevision)
        XCTAssertFalse(artwork.hasSameSession(as: ArchiveImageRequest(api: client, path: artwork.path, maximumPixelSize: 128)))

        server.allowsGuests = true
        await client.refreshAccessPolicy()
        XCTAssertTrue(client.allowGuestAccess)
        server.statusOverride = .init(status: 503, body: "{\"detail\":\"维护中\"}")
        await client.refreshAccessPolicy()
        XCTAssertFalse(client.allowGuestAccess, "A failed current policy fetch cannot reopen a private library via old display data")
        XCTAssertNotNil(client.accessPolicyError)
        server.statusOverride = .init(body: "{\"initialized\":true}")
        await client.refreshAccessPolicy()
        XCTAssertFalse(client.allowGuestAccess)
        XCTAssertNotNil(client.accessPolicyError, "A missing access flag must remain a visible contract error")
    }

    func testConcurrentUnauthorizedCatalogReadsRequestOneLoginAndRefreshPolicy() async throws {
        let server = AccessPolicyServer(allowsGuests: true)
        let client = makeClient(server)
        await client.refreshAccessPolicy()
        let revision = client.sessionRevision
        server.allowsGuests = false
        server.catalogReply = .init(status: 401, body: "{\"detail\":\"请登录后浏览媒体库\"}", delay: 0.02)
        let requests = ["/videos", "/creators", "/assets/cover"].map { path in
            Task { try? await client.get(path) as JSONValue }
        }
        for request in requests { _ = await request.value }
        try await waitUntil { !client.allowGuestAccess && client.loginRequest != nil }
        let firstRequest = try XCTUnwrap(client.loginRequest)
        XCTAssertGreaterThan(client.sessionRevision, revision, "A guest losing access must also invalidate mounted content")
        _ = try? await client.get("/videos") as JSONValue
        XCTAssertEqual(client.loginRequest?.id, firstRequest.id)
        client.requestLogin(message: "请登录后继续")
        XCTAssertEqual(client.loginRequest?.id, firstRequest.id, "Concurrent protected screens share the same login presentation")
        client.dismissLoginRequest()
        XCTAssertNil(client.loginRequest)
    }

    func testProbeAndWrongPasswordDoNotCreateLoginLoopsAndLogoutDismissesRequest() async throws {
        let server = AccessPolicyServer()
        server.loginReply = .init(status: 401, body: "{\"detail\":\"用户名或密码不正确\"}")
        let client = makeClient(server)
        try await client.restoreSession()
        XCTAssertNil(client.loginRequest)
        await expectHTTP(401) { try await client.login(username: "reader", password: "invalid-password") }
        XCTAssertNil(client.loginRequest)

        server.loginReply = nil
        try await client.login(username: "reader", password: "test-only-password")
        client.requestLogin(message: "重新登录")
        server.logoutReply = .init(status: 401, body: "{\"detail\":\"会话已失效\"}")
        try await client.logout()
        XCTAssertNil(client.user)
        XCTAssertNil(client.loginRequest)
    }

    func testSuccessfulLoginClearsPendingRequest() async throws {
        let server = AccessPolicyServer()
        let client = makeClient(server)
        client.requestLogin(message: "请登录后继续")
        try await client.login(username: "reader", password: "test-only-password")
        XCTAssertEqual(client.user?.id, "reader-id")
        XCTAssertNil(client.loginRequest)
    }

    func testForbiddenOperationsAndExternalMediaFailuresKeepAuthenticatedSession() async throws {
        let server = AccessPolicyServer()
        let client = makeClient(server)
        try await client.login(username: "reader", password: "test-only-password")
        let revision = client.sessionRevision
        for detail in ["需要管理员权限", "操作校验失败，请刷新后重试", "播放会话已过期，请重新获取播放地址"] {
            server.catalogReply = .init(status: 403, body: "{\"detail\":\"\(detail)\"}")
            await expectHTTP(403) { let _: JSONValue = try await client.get("/videos") }
            XCTAssertNil(client.loginRequest)
            XCTAssertEqual(client.user?.id, "reader-id")
        }
        server.catalogReply = .init(status: 401, body: "{\"detail\":\"签名已过期\"}",
                                    responseURL: URL(string: "https://storage.example.test/expired"))
        await expectHTTP(401) { _ = try await client.data("/api/v1/assets/sidecar") }
        await expectHTTP(401) { _ = try await client.data("https://storage.example.test/expired") }
        XCTAssertNil(client.loginRequest)
        XCTAssertEqual(client.user?.id, "reader-id")
        XCTAssertEqual(client.sessionRevision, revision)
        XCTAssertNil(server.requests.last?.value(forHTTPHeaderField: "Cookie"))
    }

    func testDelayedOldUnauthorizedResponseCannotUndoANewLogin() async throws {
        let server = AccessPolicyServer()
        server.catalogReply = .init(status: 401, body: "{\"detail\":\"会话已失效\"}", delay: 0.3)
        let client = makeClient(server)
        let pending = Task { try? await client.get("/videos") as JSONValue }
        try await waitUntil { server.requests.contains { $0.url?.path == "/api/v1/videos" } }
        try await client.login(username: "reader", password: "test-only-password")
        let revision = client.sessionRevision
        _ = await pending.value
        XCTAssertEqual(client.user?.id, "reader-id")
        XCTAssertEqual(client.sessionRevision, revision)
        XCTAssertNil(client.loginRequest)
    }

    func testServerChangeRejectsOldPolicyResponse() async throws {
        let server = AccessPolicyServer(allowsGuests: false)
        server.statusOverride = .init(body: """
        {"initialized":true,"allow_guest_access":false,"password_login":true}
        """, delay: 0.3)
        let client = makeClient(server)
        let pending = Task { await client.refreshAccessPolicy() }
        try await waitUntil { server.requests.contains { $0.url?.path == "/api/v1/auth/status" } }
        server.statusOverride = nil
        server.allowsGuests = true
        try await client.connect("https://new-api.example.test")
        await pending.value
        XCTAssertEqual(client.baseURL.host, "new-api.example.test")
        XCTAssertTrue(client.allowGuestAccess)
        XCTAssertNil(client.loginRequest)
        XCTAssertNil(client.accessPolicyError)
    }

    func testFailedCandidateSessionProbePreservesSignedInServer() async throws {
        let server = AccessPolicyServer(allowsGuests: true)
        let client = makeClient(server)
        try await client.login(username: "reader", password: "test-only-password")
        client.requestLogin(message: "切换前的登录提示")
        let requestID = client.loginRequest?.id
        let revision = client.sessionRevision
        server.sessionReply = .init(status: 503, body: "{\"detail\":\"会话服务暂不可用\"}")
        await expectHTTP(503) { try await client.connect("https://candidate.example.test") }
        XCTAssertEqual(client.baseURL.host, "api.example.test")
        XCTAssertEqual(client.user?.id, "reader-id")
        XCTAssertEqual(client.csrfToken, "csrf-reader")
        XCTAssertEqual(client.sessionRevision, revision)
        XCTAssertEqual(client.loginRequest?.id, requestID)
        XCTAssertTrue(client.isConnected)
        XCTAssertTrue(client.allowGuestAccess)
        let candidate = server.requests.filter { $0.url?.host == "candidate.example.test" }
        XCTAssertTrue(candidate.contains { $0.url?.path == "/api/v1/auth/me" })
        XCTAssertTrue(candidate.allSatisfy { $0.value(forHTTPHeaderField: "Cookie") == nil })
    }

    func testCancelledCandidateSessionProbePreservesSignedInServer() async throws {
        let server = AccessPolicyServer()
        let client = makeClient(server)
        try await client.login(username: "reader", password: "test-only-password")
        let revision = client.sessionRevision
        server.sessionReply = .init(status: 401, body: "{\"detail\":\"请先登录\"}", delay: 0.3)
        let pending = Task { try await client.connect("https://candidate.example.test") }
        try await waitUntil {
            server.requests.contains { $0.url?.host == "candidate.example.test" && $0.url?.path == "/api/v1/auth/me" }
        }
        pending.cancel()
        do { try await pending.value; XCTFail("A cancelled connection must not be accepted") }
        catch is CancellationError { }
        XCTAssertEqual(client.baseURL.host, "api.example.test")
        XCTAssertEqual(client.user?.id, "reader-id")
        XCTAssertEqual(client.sessionRevision, revision)
        XCTAssertEqual(client.mediaCookies(for: URL(string: "https://api.example.test/")!).map(\.value), ["session-reader"])
        XCTAssertNil(client.loginRequest)
    }

    func testCancelledCandidatePolicyProbeDoesNotCommitDestination() async throws {
        let server = AccessPolicyServer()
        let client = makeClient(server)
        try await client.login(username: "reader", password: "test-only-password")
        let revision = client.sessionRevision
        server.statusOverride = .init(body: "{\"initialized\":true,\"allow_guest_access\":true}", delay: 0.3)
        let pending = Task { try await client.connect("https://candidate.example.test") }
        try await waitUntil {
            server.requests.contains { $0.url?.host == "candidate.example.test" && $0.url?.path == "/api/v1/auth/status" }
        }
        pending.cancel()
        do { try await pending.value; XCTFail("Cancelling the policy probe must abandon its prepared destination") }
        catch is CancellationError { }
        XCTAssertEqual(client.baseURL.host, "api.example.test")
        XCTAssertEqual(client.user?.id, "reader-id")
        XCTAssertEqual(client.sessionRevision, revision)
        XCTAssertNil(client.accessPolicyError)
    }

    func testRestoredAuthenticatedSessionSurvivesGuestPolicyTightening() async throws {
        let server = AccessPolicyServer(allowsGuests: true)
        let client = makeClient(server)
        try await client.login(username: "reader", password: "test-only-password")
        // Reconnecting to the same server probes the existing native cookie;
        // it must not briefly publish an anonymous destination.
        try await client.connect("https://api.example.test")
        XCTAssertEqual(client.user?.id, "reader-id")
        XCTAssertEqual(client.csrfToken, "csrf-reader")
        let revision = client.sessionRevision
        server.allowsGuests = false
        await client.refreshAccessPolicy()
        XCTAssertFalse(client.allowGuestAccess)
        XCTAssertEqual(client.user?.id, "reader-id")
        XCTAssertEqual(client.sessionRevision, revision, "Closing guest access must not interrupt an authorized viewer")
        XCTAssertNil(client.loginRequest)
        let probe = try XCTUnwrap(server.requests.last { $0.url?.path == "/api/v1/auth/me" })
        XCTAssertTrue(probe.value(forHTTPHeaderField: "Cookie")?.contains("treasure_session=session-reader") == true)
    }

    func testPublicPolicyRejectionFailsClosedWithoutRevokingTheAccount() async throws {
        let server = AccessPolicyServer(allowsGuests: true)
        let client = makeClient(server)
        try await client.login(username: "reader", password: "test-only-password")
        let revision = client.sessionRevision
        server.statusOverride = .init(status: 401, body: "{\"detail\":\"代理身份校验失败\"}")
        await client.refreshAccessPolicy()
        XCTAssertFalse(client.allowGuestAccess)
        XCTAssertNotNil(client.accessPolicyError)
        XCTAssertEqual(client.user?.id, "reader-id", "A public metadata endpoint does not validate the account session")
        XCTAssertEqual(client.sessionRevision, revision)
        XCTAssertNil(client.loginRequest)
    }

    func testHTTPLoginAndGuestPlaybackRetainCookieOriginAndPathBoundaries() async throws {
        let server = AccessPolicyServer(allowsGuests: true)
        let client = makeClient(server, origin: "http://api.example.test:8788")
        XCTAssertEqual(try APIClient.normalizedServer(" HTTP://API.EXAMPLE.TEST:8788/ ").absoluteString,
                       "http://api.example.test:8788")
        try await client.login(username: "reader", password: "test-only-password")
        try await client.mutate("/videos/video/star", method: "PUT", body: ["starred": true])
        let mutation = try XCTUnwrap(server.requests.last)
        XCTAssertEqual(mutation.value(forHTTPHeaderField: "Origin"), "http://api.example.test:8788")
        XCTAssertEqual(mutation.value(forHTTPHeaderField: "X-CSRF-Token"), "csrf-reader")
        XCTAssertTrue(mutation.value(forHTTPHeaderField: "Cookie")?.contains("treasure_session=session-reader") == true)
        XCTAssertTrue(client.mediaCookies(for: URL(string: "http://api.example.test:8789/api/v1/assets/a")!).isEmpty)
        XCTAssertTrue(client.mediaCookies(for: URL(string: "http://other.example.test:8788/api/v1/assets/a")!).isEmpty)

        let guest = makeClient(server, origin: "http://api.example.test:8788")
        try await guest.mutate("/playback-sessions", body: ["part_id": "part-id"])
        let media = URL(string: "http://api.example.test:8788/api/v1/playback-sessions/session/assets/media")!
        XCTAssertEqual(guest.mediaCookies(for: media).map(\.name), ["treasure_viewer"])
        XCTAssertFalse(try XCTUnwrap(guest.mediaCookies(for: media).first).isSecure)
        XCTAssertTrue(guest.mediaCookies(for: URL(string: "http://api.example.test:8788/api/v1/assets/a")!).isEmpty)
        XCTAssertTrue(guest.mediaCookies(for: URL(string: "http://api.example.test:8788/api/v1/playback-sessions-evil")!).isEmpty)
    }

    func testSecureCookieOnHTTPRejectsUnusableLoginWithoutDowngradingCookie() async throws {
        let server = AccessPolicyServer()
        server.secureSessionCookie = true
        let client = makeClient(server, origin: "http://api.example.test:8788")
        do {
            try await client.login(username: "reader", password: "test-only-password")
            XCTFail("A Secure cookie cannot establish a usable HTTP session")
        } catch let error as APIError {
            guard case .invalidServer = error else { return XCTFail("Expected an actionable HTTP deployment error, got \(error)") }
            XCTAssertTrue(error.localizedDescription.contains("HTTPS") || error.localizedDescription.contains("COOKIE_SECURE"))
        }
        XCTAssertNil(client.user)
        XCTAssertEqual(client.csrfToken, "")
        XCTAssertTrue(client.mediaCookies(for: URL(string: "http://api.example.test:8788/api/v1/assets/a")!).isEmpty)
        XCTAssertNil(client.loginRequest)
    }

    func testSecureGuestCookieOnHTTPReportsDeploymentErrorBeforePlayback() async throws {
        let server = AccessPolicyServer(allowsGuests: true)
        server.secureViewerCookie = true
        let client = makeClient(server, origin: "http://api.example.test:8788")
        await client.refreshAccessPolicy()
        do {
            try await client.mutate("/playback-sessions", body: ["part_id": "part-id"])
            XCTFail("A guest player cannot use Secure session cookies over HTTP")
        } catch let error as APIError {
            guard case .invalidServer = error else { return XCTFail("Expected HTTP cookie configuration guidance, got \(error)") }
            XCTAssertTrue(error.localizedDescription.contains("HTTPS") || error.localizedDescription.contains("COOKIE_SECURE"))
        }
        XCTAssertNil(client.user)
        XCTAssertNil(client.loginRequest, "This is a server deployment error, not a missing account")
        XCTAssertTrue(client.allowGuestAccess)
        let media = URL(string: "http://api.example.test:8788/api/v1/playback-sessions/session/assets/media")!
        XCTAssertTrue(client.mediaCookies(for: media).isEmpty)
    }

    private func makeClient(_ server: AccessPolicyServer, origin: String = "https://api.example.test") -> APIClient {
        AccessPolicyURLProtocol.server = server
        let name = "com.guoweiyi.treasureup.access-policy-tests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: name)!
        addTeardownBlock { UserDefaults(suiteName: name)?.removePersistentDomain(forName: name) }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [AccessPolicyURLProtocol.self]
        return APIClient(baseURL: URL(string: origin)!, defaults: defaults,
                         sessionConfiguration: configuration, persistSession: false)
    }

    private func expectHTTP(_ expectedStatus: Int, operation: () async throws -> Void,
                            file: StaticString = #filePath, line: UInt = #line) async {
        do { try await operation(); XCTFail("Expected HTTP \(expectedStatus)", file: file, line: line) }
        catch let error as APIError { XCTAssertEqual(error.status, expectedStatus, file: file, line: line) }
        catch { XCTFail("Unexpected error: \(error)", file: file, line: line) }
    }

    private func waitUntil(_ predicate: () -> Bool, file: StaticString = #filePath, line: UInt = #line) async throws {
        for _ in 0..<200 {
            if predicate() { return }
            try await Task.sleep(for: .milliseconds(5))
        }
        XCTFail("Timed out waiting for the offline request", file: file, line: line)
    }
}

private final class AccessPolicyServer: @unchecked Sendable {
    struct Reply: Sendable {
        var status = 200
        var body = "{}"
        var headers: [String: String] = [:]
        var delay: TimeInterval = 0
        var responseURL: URL?
    }
    private let lock = NSLock()
    private var guestAccess: Bool
    private var displayGuestAccess: Bool?
    private var statusReply: Reply?
    private var login: Reply?
    private var identity: Reply?
    private var logout = Reply()
    private var catalog = Reply()
    private var secureCookie: Bool?
    private var secureGuestCookie: Bool?
    private var received: [URLRequest] = []
    init(allowsGuests: Bool = false) { guestAccess = allowsGuests }
    var allowsGuests: Bool { get { lock.withLock { guestAccess } } set { lock.withLock { guestAccess = newValue } } }
    var displayAllowsGuests: Bool? { get { lock.withLock { displayGuestAccess } } set { lock.withLock { displayGuestAccess = newValue } } }
    var statusOverride: Reply? { get { lock.withLock { statusReply } } set { lock.withLock { statusReply = newValue } } }
    var loginReply: Reply? { get { lock.withLock { login } } set { lock.withLock { login = newValue } } }
    var sessionReply: Reply? { get { lock.withLock { identity } } set { lock.withLock { identity = newValue } } }
    var logoutReply: Reply { get { lock.withLock { logout } } set { lock.withLock { logout = newValue } } }
    var catalogReply: Reply { get { lock.withLock { catalog } } set { lock.withLock { catalog = newValue } } }
    var secureSessionCookie: Bool? { get { lock.withLock { secureCookie } } set { lock.withLock { secureCookie = newValue } } }
    var secureViewerCookie: Bool? { get { lock.withLock { secureGuestCookie } } set { lock.withLock { secureGuestCookie = newValue } } }
    var requests: [URLRequest] { lock.withLock { received } }

    func reply(to request: URLRequest) -> Reply {
        lock.withLock {
            received.append(request)
            switch request.url?.path {
            case "/api/v1/server":
                return .init(body: "{\"application\":\"treasure-up\",\"api_version\":1}")
            case "/api/v1/auth/status":
                return statusReply ?? .init(body: "{\"initialized\":true,\"allow_guest_access\":\(guestAccess),\"password_login\":true,\"passkeys_enabled\":true}")
            case "/api/v1/library/settings":
                return .init(body: "{\"site_name\":\"Offline Archive\",\"default_danmaku\":true,\"allow_guest_access\":\(displayGuestAccess ?? guestAccess)}")
            case "/api/v1/auth/me":
                if let identity { return identity }
                if request.value(forHTTPHeaderField: "Cookie")?.contains("treasure_session=session-reader") == true {
                    return .init(body: "{\"user\":{\"id\":\"reader-id\",\"username\":\"reader\",\"role\":\"reader\"},\"csrf_token\":\"csrf-reader\"}")
                }
                return .init(status: 401, body: "{\"detail\":\"请先登录\"}")
            case "/api/v1/auth/login":
                let secure = (secureCookie ?? (request.url?.scheme == "https")) ? "; Secure" : ""
                return login ?? .init(body: "{\"user\":{\"id\":\"reader-id\",\"username\":\"reader\",\"role\":\"reader\"},\"csrf_token\":\"csrf-reader\"}",
                                      headers: ["Set-Cookie": "treasure_session=session-reader; Path=/; HttpOnly\(secure)"])
            case "/api/v1/auth/logout":
                return logout
            case "/api/v1/playback-sessions":
                let secure = (secureGuestCookie ?? (request.url?.scheme == "https")) ? "; Secure" : ""
                return .init(headers: ["Set-Cookie": "treasure_viewer=guest-playback; Path=/api/v1/playback-sessions; HttpOnly\(secure)"])
            default:
                return catalog
            }
        }
    }
}

private final class AccessPolicyURLProtocol: URLProtocol, @unchecked Sendable {
    private static let lock = NSLock()
    nonisolated(unsafe) private static var currentServer: AccessPolicyServer?
    static var server: AccessPolicyServer? {
        get { lock.withLock { currentServer } }
        set { lock.withLock { currentServer = newValue } }
    }
    private let loadingLock = NSLock()
    private var stopped = false
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        guard let server = Self.server else { client?.urlProtocol(self, didFailWithError: URLError(.badServerResponse)); return }
        let reply = server.reply(to: request)
        if reply.delay > 0 {
            DispatchQueue.global().asyncAfter(deadline: .now() + reply.delay) { self.finish(reply) }
        } else { finish(reply) }
    }
    private func finish(_ reply: AccessPolicyServer.Reply) {
        guard !loadingLock.withLock({ stopped }) else { return }
        let response = HTTPURLResponse(url: reply.responseURL ?? request.url!, statusCode: reply.status,
                                       httpVersion: "HTTP/1.1", headerFields: reply.headers)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(reply.body.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() { loadingLock.withLock { stopped = true } }
}
