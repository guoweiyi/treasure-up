import Foundation
import Network
import XCTest
@testable import TreasureUp

@MainActor
final class HTTPConnectionTests: XCTestCase {
    func testBuiltApplicationAllowsHTTPWithoutMoreSpecificATSOverrides() throws {
        let ats = try XCTUnwrap(Bundle.main.object(forInfoDictionaryKey: "NSAppTransportSecurity") as? [String: Any])
        XCTAssertEqual(ats["NSAllowsArbitraryLoads"] as? Bool, true)
        for key in ["NSAllowsArbitraryLoadsForMedia", "NSAllowsArbitraryLoadsInWebContent", "NSAllowsLocalNetworking"] {
            XCTAssertNil(ats[key], "\(key) overrides the general HTTP exception on current iOS")
        }
    }

    func testRealHTTPConnectionAndLoginSendSessionCookieAndCSRF() async throws {
        let server = try LoopbackHTTPServer()
        defer { server.stop() }
        let address = try await server.start()
        let suite = "HTTPConnectionTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [] // Real URLSession TCP traffic, never URLProtocol mocks.
        configuration.connectionProxyDictionary = [:]
        let client = APIClient(defaults: defaults, sessionConfiguration: configuration, persistSession: false)

        try await client.connect(address.absoluteString)
        XCTAssertEqual(client.baseURL, address)
        XCTAssertTrue(client.isConnected)
        XCTAssertTrue(client.allowGuestAccess)
        XCTAssertEqual(client.siteName, "HTTP Loopback Archive")
        XCTAssertEqual(client.serverInitialized, true)
        XCTAssertNil(client.user)
        XCTAssertNil(client.loginRequest, "The initial anonymous /auth/me 401 is a valid guest session")
        let initialPaths = Set(server.requests.map(\.path))
        XCTAssertTrue(initialPaths.isSuperset(of: ["/api/v1/server", "/api/v1/auth/me",
                                                 "/api/v1/auth/status", "/api/v1/library/settings"]))

        try await client.login(username: "loopback-reader", password: "ephemeral-local-test")
        XCTAssertEqual(client.user?.id, "loopback-user")
        XCTAssertEqual(client.csrfToken, "loopback-csrf")
        try await client.mutate("/videos/local-video/star", method: "PUT", body: ["starred": true])
        let mutation = try XCTUnwrap(server.requests.last(where: { $0.path == "/api/v1/videos/local-video/star" }))
        XCTAssertEqual(mutation.method, "PUT")
        XCTAssertEqual(mutation.headers["cookie"], "treasure_session=loopback-session")
        XCTAssertEqual(mutation.headers["x-csrf-token"], "loopback-csrf")
        XCTAssertEqual(mutation.headers["origin"], address.absoluteString)
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: mutation.body) as? [String: Bool])
        XCTAssertEqual(body["starred"], true)
        let cookies = client.mediaCookies(for: address.appendingPathComponent("api/v1/assets/video"))
        XCTAssertEqual(cookies.map(\.name), ["treasure_session"])
        XCTAssertEqual(cookies.first?.isSecure, false)
    }
}

/// Test-target-only HTTP/1.1 fixture. All mutable state and Network callbacks
/// live on queue; the listener binds only IPv4 loopback on an ephemeral port.
private final class LoopbackHTTPServer: @unchecked Sendable {
    struct Request: Sendable {
        let method: String
        let path: String
        let headers: [String: String]
        let body: Data
    }
    private enum Failure: Error { case listenerDidNotStart, invalidPort }
    private let queue = DispatchQueue(label: "TreasureUpTests.HTTP.loopback")
    private let listener: NWListener
    private var connections: [ObjectIdentifier: NWConnection] = [:]
    private var buffers: [ObjectIdentifier: Data] = [:]
    private var recorded: [Request] = []
    private var ready: CheckedContinuation<URL, Error>?
    private var stopped = false

    init() throws {
        let parameters = NWParameters.tcp
        parameters.requiredLocalEndpoint = .hostPort(host: .ipv4(.loopback), port: .any)
        listener = try NWListener(using: parameters)
    }

    var requests: [Request] { queue.sync { recorded } }

    func start() async throws -> URL {
        try await withCheckedThrowingContinuation { continuation in
            queue.async { [self] in
                ready = continuation
                listener.stateUpdateHandler = { [weak self] state in
                    guard let self, let ready = self.ready else { return }
                    switch state {
                    case .ready:
                        self.ready = nil
                        if let port = self.listener.port,
                           let url = URL(string: "http://127.0.0.1:\(port.rawValue)") {
                            ready.resume(returning: url)
                        } else { ready.resume(throwing: Failure.invalidPort) }
                    case .failed(let error):
                        self.ready = nil
                        ready.resume(throwing: error)
                    default: break
                    }
                }
                listener.newConnectionHandler = { [weak self] connection in
                    guard let self, !self.stopped else { connection.cancel(); return }
                    let id = ObjectIdentifier(connection)
                    self.connections[id] = connection
                    self.buffers[id] = Data()
                    connection.start(queue: self.queue)
                    self.receive(connection)
                    self.queue.asyncAfter(deadline: .now() + 10) { [weak self, weak connection] in
                        guard let self, let connection,
                              self.connections[ObjectIdentifier(connection)] != nil else { return }
                        self.close(connection)
                    }
                }
                listener.start(queue: queue)
                queue.asyncAfter(deadline: .now() + 5) { [weak self] in
                    guard let self, let ready = self.ready else { return }
                    self.ready = nil
                    self.listener.cancel()
                    ready.resume(throwing: Failure.listenerDidNotStart)
                }
            }
        }
    }

    func stop() {
        queue.sync {
            stopped = true
            listener.stateUpdateHandler = nil
            listener.newConnectionHandler = nil
            listener.cancel()
            for connection in connections.values { connection.cancel() }
            connections.removeAll()
            buffers.removeAll()
            ready?.resume(throwing: Failure.listenerDidNotStart)
            ready = nil
        }
    }

    private func close(_ connection: NWConnection) {
        let id = ObjectIdentifier(connection)
        connections[id] = nil
        buffers[id] = nil
        connection.cancel()
    }

    private func receive(_ connection: NWConnection) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 16_384) { [weak self] data, _, complete, error in
            guard let self, !self.stopped else { connection.cancel(); return }
            let id = ObjectIdentifier(connection)
            guard var buffer = self.buffers[id] else { return }
            if let data { buffer.append(data) }
            guard buffer.count <= 65_536 else { self.close(connection); return }
            self.buffers[id] = buffer
            if let request = self.parse(buffer) {
                self.recorded.append(request)
                self.respond(to: request, on: connection)
            } else if complete || error != nil { self.close(connection) }
            else { self.receive(connection) }
        }
    }

    private func parse(_ data: Data) -> Request? {
        guard let divider = data.range(of: Data("\r\n\r\n".utf8)),
              let header = String(data: data[..<divider.lowerBound], encoding: .utf8) else { return nil }
        let lines = header.components(separatedBy: "\r\n")
        let first = (lines.first ?? "").split(separator: " ")
        guard first.count == 3 else { return nil }
        var headers: [String: String] = [:]
        for line in lines.dropFirst() {
            guard let colon = line.firstIndex(of: ":") else { continue }
            headers[String(line[..<colon]).lowercased()] = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
        }
        let length = Int(headers["content-length"] ?? "0") ?? 0
        guard length >= 0, length <= 65_536, data.count - divider.upperBound >= length else { return nil }
        return Request(method: String(first[0]), path: String(first[1]), headers: headers,
                       body: data.subdata(in: divider.upperBound..<(divider.upperBound + length)))
    }

    private func respond(to request: Request, on connection: NWConnection) {
        let status: String
        let body: String
        var extraHeaders = ""
        switch request.path {
        case "/api/v1/server":
            status = "200 OK"; body = #"{"application":"treasure-up","api_version":1}"#
        case "/api/v1/auth/me":
            status = "401 Unauthorized"; body = #"{"detail":"Guest session"}"#
        case "/api/v1/auth/status":
            status = "200 OK"; body = #"{"initialized":true,"allow_guest_access":true,"password_login":true}"#
        case "/api/v1/library/settings":
            status = "200 OK"; body = #"{"site_name":"HTTP Loopback Archive","allow_guest_access":true,"default_danmaku":true}"#
        case "/api/v1/auth/login":
            status = "200 OK"
            body = #"{"user":{"id":"loopback-user","username":"loopback-reader","role":"reader"},"csrf_token":"loopback-csrf"}"#
            extraHeaders = "Set-Cookie: treasure_session=loopback-session; Path=/; HttpOnly; SameSite=Lax\r\n"
        case "/api/v1/videos/local-video/star":
            status = "200 OK"; body = #"{"starred":true}"#
        default:
            status = "404 Not Found"; body = #"{"detail":"Unexpected fixture request"}"#
        }
        let response = "HTTP/1.1 \(status)\r\nContent-Type: application/json\r\nContent-Length: \(body.utf8.count)\r\nConnection: close\r\n\(extraHeaders)\r\n\(body)"
        connection.send(content: Data(response.utf8), completion: .contentProcessed { [weak self] _ in
            self?.close(connection)
        })
    }
}
