import XCTest
import ImageIO
@testable import TreasureUp

final class CoreTests: XCTestCase {
    func testCommentTimelinePreservesFullwidthTimesAndRejectsMalformedOrOutsidePart() {
        let part = VideoPart(id: "p2", position: 2, duration: 4000, variants: [MediaVariant(id: "v2")])
        let content = "0：00 开始 4：02 中段 1:02:03 结尾 1:99 无效 1:60:03 无效 66:40 越界"
        let attributed = ArchivedCommentTimeline.attributed(content, parts: [part], currentPartID: "p2")
        XCTAssertEqual(String(attributed.characters), content)
        let targets = attributed.runs.compactMap { $0.link }.compactMap { ArchivedCommentTimeline.seconds($0, duration: 4000) }
        XCTAssertEqual(targets, [0, 242, 3723])
        XCTAssertNil(ArchivedCommentTimeline.seconds(URL(string: "https://example.com/?seconds=5")!, duration: 300))
        XCTAssertNil(ArchivedCommentTimeline.seconds(URL(string: "treasureup-comment://seek?seconds=300")!, duration: 300))
        XCTAssertNil(ArchivedCommentTimeline.seconds(URL(string: "treasureup-comment://seek?seconds=-1")!, duration: 300))
        XCTAssertNil(ArchivedCommentTimeline.seconds(URL(string: "treasureup-comment://seek?seconds=nan")!, duration: 300))
    }

    func testLongCommentPreviewIsBoundedByLengthAndLines() {
        XCTAssertEqual(ArchivedCommentTimeline.preview("短评论"), "短评论")
        XCTAssertEqual(ArchivedCommentTimeline.preview(String(repeating: "😀", count: 400)), String(repeating: "😀", count: 360) + "…")
        XCTAssertEqual(ArchivedCommentTimeline.preview("a\nb\nc\nd\ne\nf\ng"), "a\nb\nc\nd\ne\nf…")
        XCTAssertEqual(ArchivedCommentTimeline.preview(String(repeating: "字", count: 357) + "4:02结尾"), String(repeating: "字", count: 357) + "…")
    }

    func testCommentTimelineChoosesExplicitPlayablePartWithoutFallback() {
        let parts = [VideoPart(id: "p1", position: 1, duration: 300, variants: [MediaVariant(id: "v1")]),
                     VideoPart(id: "p2", position: 2, duration: 4000, variants: [MediaVariant(id: "v2")]),
                     VideoPart(id: "p3", position: 3, duration: 4000)]
        func targets(_ content: String) -> [String] {
            ArchivedCommentTimeline.attributed(content, parts: parts, currentPartID: "p1").runs
                .compactMap { $0.link }.compactMap { ArchivedCommentTimeline.target($0, parts: parts)?.part.id }
        }
        XCTAssertEqual(targets("part2@天阙 总结\n0：00"), ["p2"])
        XCTAssertEqual(targets(" P2 0:00"), ["p2"])
        XCTAssertEqual(targets("4：02 中段"), ["p1"])
        XCTAssertEqual(targets("P3 0:00"), [])
        XCTAssertEqual(targets("part4 0:00"), [])
    }

    func testCommentTimelineMixedPartDirectoriesKeepSectionTargets() {
        let parts = [VideoPart(id: "p1", position: 1, duration: 300, variants: [MediaVariant(id: "v1")]),
                     VideoPart(id: "p2", position: 2, duration: 4000, variants: [MediaVariant(id: "v2")]),
                     VideoPart(id: "p3", position: 3, duration: 4000)]
        let content = "总结一下其他佬的传送门~\n0:15 前言\npart1@某位\n00:40 开场[笑]\n5:00 超时\npart2@天阙QAQ 总结~\n0：00不需要陪伴的闲暇[笑]4：02冲动是小天使\n40：47离去之原\nP3 0:00未归档\npart9 0:00不存在\n0:12仍无效\nP1 0:25恢复"
        let attributed = ArchivedCommentTimeline.attributed(content, parts: parts, currentPartID: "p1")
        XCTAssertEqual(String(attributed.characters), content)
        let targets = attributed.runs.compactMap { $0.link }.compactMap { ArchivedCommentTimeline.target($0, parts: parts) }
        XCTAssertEqual(targets.map { $0.part.id }, ["p1", "p1", "p2", "p2", "p2", "p1"])
        XCTAssertEqual(targets.map { $0.seconds }, [15, 40, 0, 242, 2447, 25])
        XCTAssertNil(ArchivedCommentTimeline.target(URL(string: "treasureup-comment://seek?part=p3&seconds=5")!, parts: parts))
        XCTAssertNil(ArchivedCommentTimeline.target(URL(string: "treasureup-comment://seek?part=p1&seconds=300")!, parts: parts))
        let preview = ArchivedCommentTimeline.attributed(ArchivedCommentTimeline.preview(content), parts: parts, currentPartID: "p1")
        let previewTargets = preview.runs.compactMap { $0.link }.compactMap { ArchivedCommentTimeline.target($0, parts: parts) }
        XCTAssertEqual(previewTargets.map { $0.part.id }, ["p1", "p1"])
    }

    private func isolatedDefaults() -> UserDefaults {
        let suite = "com.guoweiyi.treasureup.tests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defaults.removePersistentDomain(forName: suite)
        addTeardownBlock {
            UserDefaults(suiteName: suite)?.removePersistentDomain(forName: suite)
        }
        return defaults
    }

    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(type, from: Data(json.utf8))
    }

    func testCompactCatalogCardsAllowMinimalCreatorsAndNullableStats() throws {
        let video = try decode(ArchiveVideo.self, """
        {"id":"video-a","bvid":"BV123","title":"Archive","duration":65.5,
         "cover_url":"/api/v1/assets/cover","creators":[{"id":"creator-a","name":"UP", "avatar_url":null,"role":"owner"}],
         "stats":{"view":null,"like":42,"observed_at":null},"parts_count":3,
         "content_features":{"dolby_vision":true,"dolby_atmos":true,"charging_exclusive":false}}
        """)
        XCTAssertEqual(video.coverUrl, "/api/v1/assets/cover")
        XCTAssertEqual(video.creators.first?.name, "UP")
        XCTAssertEqual(video.creators.first?.savedCount, 0)
        XCTAssertEqual(video.parts, [])
        XCTAssertNil(video.stats?.view)
        XCTAssertEqual(video.stats?.like, 42)
        XCTAssertTrue(video.hasDolbyVision)
        XCTAssertTrue(video.hasDolbyAtmos)
    }

    func testPlaybackPreservesHDRAtmosAndStringQuality() throws {
        let part = try decode(VideoPart.self, """
        {"id":"part-a","cid":"123","position":1,"title":"Part 1","duration":90,
         "variants":[{"id":"variant-a","quality":"2160p","kind":"archive",
           "video_codec":"hevc","audio_codec":"eac3",
           "metadata":{"dolby_vision":true,"dolby_atmos":true,"hdr":true,"audio_channels":6,
           "source_variant_id":"variant-original","audio_transcoded":false}}]}
        """)
        XCTAssertEqual(part.variants.first?.quality.stringValue, "2160p")
        XCTAssertEqual(part.variants.first?.metadata?.sourceVariantId, "variant-original")
        XCTAssertEqual(part.variants.first?.metadata?.audioChannels, 6)
        XCTAssertEqual(part.variants.first?.metadata?.dolbyVision, true)
        XCTAssertEqual(part.variants.first?.metadata?.audioTranscoded, false)

        let session = try decode(PlaybackSession.self, """
        {"id":"session-a","session_id":"session-a","asset_id":"asset-a","variant_id":"variant-a",
         "protocol":"hls","url":"/api/v1/playback-sessions/session-a/manifest.m3u8",
         "selected_route_id":"route-a","media":{"dolby_atmos":true},
         "routes":[{"id":"route-a","name":"Storage","status":"available","latency_ms":null}],
         "subtitles":[{"id":"sub-a","url":"/api/v1/assets/sub","label":"中文","is_auto":false}],
         "loudness":{"status":"bypassed","gain_linear":1,"atmos_bypass":true,"reason":"atmos"}}
        """)
        XCTAssertEqual(session.sessionId, "session-a")
        XCTAssertEqual(session.protocol, "hls")
        XCTAssertEqual(session.routes.first?.id, "route-a")
        XCTAssertEqual(session.subtitles.first?.isAuto, false)
        XCTAssertTrue(session.loudness?.atmosBypass == true)
    }

    func testDynamicJSONKeepsSnakeCaseAtEveryDepth() throws {
        let row = try decode(JSONValue.self, """
        {"account_id":"account-a","policy":{"prefer_dolby_vision":true},"items":[{"asset_url":"/asset"}]}
        """)
        XCTAssertEqual(row["account_id"]?.stringValue, "account-a")
        XCTAssertEqual(row["policy"]?["prefer_dolby_vision"]?.boolValue, true)
        XCTAssertEqual(row["items"]?.arrayValue?.first?["asset_url"]?.stringValue, "/asset")
        XCTAssertNil(row["accountId"])
        let encoded = try JSONEncoder().encode(row)
        XCTAssertEqual(try JSONDecoder().decode(JSONValue.self, from: encoded), row)
    }

    func testCollectionsAndPlaylistsDecodeTheirDistinctPaginationContracts() throws {
        let page = try decode(Page<PersonalPlaylist>.self, """
        {"items":[{"id":"list-a","name":"稍后看","kind":"watch_later","item_count":9,"unwatched_count":7}],"total":1}
        """)
        XCTAssertEqual(page.items.first?.itemCount, 9)
        XCTAssertEqual(page.page, 1)
        XCTAssertFalse(page.hasMore)
        let comments = try decode(Page<ArchiveComment>.self, """
        {"items":[{"id":"comment-a","content":"Great","images":["/a",{"asset_url":"/b"}]}],
         "total":21,"page":1,"page_size":20}
        """)
        XCTAssertEqual(comments.items.first?.imageUrls, ["/a", "/b"])
        XCTAssertTrue(comments.hasMore)
    }

    @MainActor
    func testServerNormalizationAndMediaURLBoundaries() throws {
        XCTAssertEqual(try APIClient.normalizedServer("  HTTPS://Example.COM:443/ ").absoluteString, "https://example.com")
        XCTAssertEqual(try APIClient.normalizedServer("example.com").absoluteString, "https://example.com")
        for address in ["https://name:password@example.com", "https://example.com/path", "file:///tmp", "https://example.com?token=abc"] {
            XCTAssertThrowsError(try APIClient.normalizedServer(address))
        }
        let client = APIClient(baseURL: URL(string: "https://example.com")!,
                               defaults: isolatedDefaults(), persistSession: false)
        XCTAssertEqual(client.resolveURL("/api/v1/assets/a")?.absoluteString, "https://example.com/api/v1/assets/a")
        XCTAssertEqual(client.resolveURL("https://storage.example.net/signed?key=abc")?.host, "storage.example.net")
        XCTAssertNil(client.resolveURL("http://example.com/file"))
        XCTAssertNil(client.resolveURL("https://name:password@example.com/file"))
        XCTAssertNil(client.resolveURL("javascript:alert(1)"))
        XCTAssertTrue(client.mediaCookies(for: URL(string: "https://unrelated.example.net")!).isEmpty)
    }

    @MainActor
    func testNativeSessionSendsCSRFAndIsolatesCookiesByOriginAndPort() async throws {
        let recorder = RequestRecorder()
        MockURLProtocol.store.set { request in
            recorder.append(request)
            switch request.url?.path {
            case "/api/v1/auth/login":
                return .init(body: "{\"user\":{\"id\":\"user-a\",\"username\":\"tester\",\"role\":\"editor\"},\"csrf_token\":\"csrf-test\"}",
                             headers: ["Set-Cookie": "treasure_session=test-token; Path=/; Secure; HttpOnly"])
            case "/api/v1/library/settings":
                return .init(body: "{\"site_name\":\"Native Test\",\"default_danmaku\":true}")
            case "/api/v1/videos/video-a/star": return .init(body: "{\"starred\":true}")
            default: return .init(body: "{}")
            }
        }
        let client = makeClient()
        try await client.login(username: "tester", password: "temporary-test-secret")
        XCTAssertEqual(client.user?.username, "tester")
        XCTAssertEqual(client.siteName, "Native Test")
        try await client.mutate("/videos/video-a/star", method: "PUT", body: ["starred": true])
        let mutation = try XCTUnwrap(recorder.requests.last)
        XCTAssertEqual(mutation.value(forHTTPHeaderField: "Origin"), "https://api.example.test")
        XCTAssertEqual(mutation.value(forHTTPHeaderField: "X-CSRF-Token"), "csrf-test")
        XCTAssertTrue(mutation.value(forHTTPHeaderField: "Cookie")?.contains("treasure_session=test-token") == true)
        XCTAssertEqual(client.mediaCookies(for: URL(string: "https://api.example.test/api/v1/playback-sessions/a/manifest.m3u8")!).count, 1)
        XCTAssertTrue(client.mediaCookies(for: URL(string: "https://api.example.test:8443/asset")!).isEmpty)
        XCTAssertTrue(client.mediaCookies(for: URL(string: "https://sibling.example.test/asset")!).isEmpty)
        XCTAssertTrue(client.mediaCookies(for: URL(string: "http://api.example.test/asset")!).isEmpty)
    }

    @MainActor
    func testRateLimitAndValidationErrorsAreReadable() async throws {
        MockURLProtocol.store.set { _ in
            .init(status: 429, body: "{\"detail\":[{\"msg\":\"稍后重试\"}]}", headers: ["Retry-After": "30"])
        }
        do {
            let _: JSONValue = try await makeClient().get("/videos")
            XCTFail("Expected HTTP error")
        } catch let error as APIError {
            XCTAssertEqual(error.status, 429)
            XCTAssertEqual(error.retryAfterSeconds, 30)
            XCTAssertEqual(error.localizedDescription, "稍后重试")
        }
    }

    @MainActor
    func testGuestPolicyDefaultsToPrivateAndPasswordCreatesOnlySession() async throws {
        let recorder = RequestRecorder()
        MockURLProtocol.store.set { request in
            recorder.append(request)
            switch request.url?.path {
            case "/api/v1/auth/login":
                return .init(body: "{\"user\":{\"id\":\"user-a\",\"username\":\"tester\",\"role\":\"reader\"},\"csrf_token\":\"csrf-test\"}",
                             headers: ["Set-Cookie": "treasure_session=test-token; Path=/; Secure; HttpOnly"])
            default:
                return .init(body: "{\"site_name\":\"Native Test\",\"default_danmaku\":true,\"allow_guest_access\":false}")
            }
        }
        let client = makeClient()
        XCTAssertFalse(client.allowGuestAccess)
        try await client.login(username: "tester", password: "temporary-test-secret")
        XCTAssertEqual(client.user?.role, "reader")
        XCTAssertFalse(client.allowGuestAccess)
        let calls = recorder.requests
        XCTAssertEqual(calls.first?.url?.path, "/api/v1/auth/login")
        XCTAssertNil(calls.last?.value(forHTTPHeaderField: "Authorization"))
        XCTAssertTrue(client.mediaCookies(for: URL(string: "https://other.example.test/asset")!).isEmpty)
    }

    @MainActor
    func testGuestPlaybackCookieIsRestrictedToSessionRoutes() async throws {
        MockURLProtocol.store.set { _ in
            .init(body: "{}", headers: ["Set-Cookie": "treasure_viewer=guest-token; Path=/api/v1/playback-sessions; Secure; HttpOnly"])
        }
        let client = makeClient()
        try await client.mutate("/playback-sessions", body: ["part_id": "part-a"])
        XCTAssertEqual(client.mediaCookies(for: URL(string: "https://api.example.test/api/v1/playback-sessions/a/assets/media")!).count, 1)
        XCTAssertTrue(client.mediaCookies(for: URL(string: "https://api.example.test/api/v1/playback-sessions-evil")!).isEmpty)
        XCTAssertTrue(client.mediaCookies(for: URL(string: "https://api.example.test/api/v1/videos")!).isEmpty)
    }

    @MainActor
    func testRemoteMedia401DoesNotRevokeServerSession() async throws {
        MockURLProtocol.store.set { request in
            switch request.url?.path {
            case "/api/v1/auth/login":
                return .init(body: "{\"user\":{\"id\":\"user-a\",\"username\":\"tester\",\"role\":\"reader\"},\"csrf_token\":\"csrf-test\"}",
                             headers: ["Set-Cookie": "treasure_session=test-token; Path=/; Secure; HttpOnly"])
            case "/api/v1/assets/sidecar":
                // Model the final response after the asset endpoint redirects to
                // signed storage. Storage authentication is not App authentication.
                return .init(status: 401, body: "{\"detail\":\"签名已过期\"}",
                             responseURL: URL(string: "https://storage.example.test/signed-sidecar"))
            case "/api/v1/auth/me":
                return .init(status: 401, body: "{\"detail\":\"会话已失效\"}")
            default: return .init(body: "{\"site_name\":\"Native Test\",\"default_danmaku\":true}")
            }
        }
        let client = makeClient()
        try await client.login(username: "tester", password: "temporary-test-secret")
        let revision = client.sessionRevision
        do {
            _ = try await client.data("/api/v1/assets/sidecar")
            XCTFail("Expected the storage signature error to remain visible")
        } catch let error as APIError { XCTAssertEqual(error.status, 401) }
        XCTAssertEqual(client.user?.id, "user-a")
        XCTAssertEqual(client.csrfToken, "csrf-test")
        XCTAssertEqual(client.sessionRevision, revision)
        XCTAssertEqual(client.mediaCookies(for: URL(string: "https://api.example.test/")!).count, 1)

        do {
            let _: JSONValue = try await client.get("/auth/me")
            XCTFail("Expected the server's own session rejection")
        } catch let error as APIError { XCTAssertEqual(error.status, 401) }
        XCTAssertNil(client.user)
        XCTAssertEqual(client.csrfToken, "")
        XCTAssertGreaterThan(client.sessionRevision, revision)
        XCTAssertTrue(client.mediaCookies(for: URL(string: "https://api.example.test/")!).isEmpty)
    }

    @MainActor
    func testServerSwitchClearsIdentityAndNeverProbesWithPriorCookie() async throws {
        let savedAppServer = UserDefaults.standard.string(forKey: "treasure.native.server")
        let recorder = RequestRecorder()
        MockURLProtocol.store.set { request in
            recorder.append(request)
            switch request.url?.path {
            case "/api/v1/auth/login":
                return .init(body: "{\"user\":{\"id\":\"user-a\",\"username\":\"tester\",\"role\":\"admin\"},\"csrf_token\":\"csrf-test\"}",
                             headers: ["Set-Cookie": "treasure_session=test-token; Path=/; Secure; HttpOnly"])
            case "/api/v1/server": return .init(body: "{\"application\":\"treasure-up\",\"api_version\":1}")
            default: return .init(body: "{\"site_name\":\"Native Test\",\"default_danmaku\":true}")
            }
        }
        let client = makeClient()
        try await client.login(username: "tester", password: "temporary-test-secret")
        let revision = client.sessionRevision
        try await client.connect("https://other.example.test")
        XCTAssertNil(client.user)
        XCTAssertEqual(client.csrfToken, "")
        XCTAssertGreaterThan(client.sessionRevision, revision)
        XCTAssertEqual(client.baseURL.host, "other.example.test")
        XCTAssertTrue(client.mediaCookies(for: URL(string: "https://other.example.test/")!).isEmpty)
        let probe = try XCTUnwrap(recorder.requests.first { $0.url?.path == "/api/v1/server" })
        XCTAssertNil(probe.value(forHTTPHeaderField: "Cookie"))
        XCTAssertNil(probe.value(forHTTPHeaderField: "X-CSRF-Token"))
        XCTAssertEqual(UserDefaults.standard.string(forKey: "treasure.native.server"), savedAppServer,
                       "Tests must not change the host app's server preference")
    }

    @MainActor
    func testIdentityChangeRejectsInFlightResponse() async throws {
        let recorder = RequestRecorder()
        MockURLProtocol.store.set { request in
            recorder.append(request)
            return .init(body: "{\"old_account_data\":true}", delay: 0.3)
        }
        let client = makeClient()
        let pending = Task { @MainActor in try await client.get("/slow") as JSONValue }
        for _ in 0..<100 where recorder.requests.isEmpty {
            try await Task.sleep(for: .milliseconds(10))
        }
        XCTAssertFalse(recorder.requests.isEmpty)
        try await client.logout()
        do {
            _ = try await pending.value
            XCTFail("An old session must not publish data into the new identity")
        } catch APIError.staleSession { }
    }

    @MainActor
    func testResponseDecodingDoesNotBlockUIAndRejectsIdentityChangedDuringDecode() async throws {
        MockURLProtocol.store.set { _ in .init(body: "{}") }
        let client = makeClient()
        let gate = BlockingDecodeProbe.gate
        defer { gate.release() }
        let pending = Task { try await client.get("/large-response") as BlockingDecodeProbe }
        for _ in 0..<100 where !gate.hasStarted {
            try await Task.sleep(for: .milliseconds(5))
        }
        XCTAssertTrue(gate.hasStarted)
        XCTAssertFalse(gate.decodedOnMainThread, "A synchronous JSON decode must not occupy the UI thread")
        // This UI-actor operation must be able to run while decoding is still
        // suspended on the worker. Its identity change fences that old result.
        try await client.logout()
        gate.release()
        do {
            _ = try await pending.value
            XCTFail("A response decoded after logout must not escape the identity fence")
        } catch APIError.staleSession { }
    }

    @MainActor
    func testCancelledDecodeDoesNotPublishACompletedPayload() async throws {
        let pending = Task {
            try await APIResponseDecoder.decode(Page<ArchiveVideo>.self, from: Data("{\"items\":[]}".utf8))
        }
        pending.cancel()
        do {
            _ = try await pending.value
            XCTFail("Cancelled parsing must not publish a payload")
        } catch is CancellationError { }
    }

    @MainActor
    func testPrivateArtworkUsesAPISessionAndNeverSendsCookieToStorage() async throws {
        let recorder = RequestRecorder()
        let png = try makeArtworkData(width: 48, height: 32)
        MockURLProtocol.store.set { request in
            recorder.append(request)
            switch request.url?.path {
            case "/api/v1/auth/login":
                return .init(body: "{\"user\":{\"id\":\"user-a\",\"username\":\"tester\",\"role\":\"reader\"},\"csrf_token\":\"csrf-test\"}",
                             headers: ["Set-Cookie": "treasure_session=test-token; Path=/; Secure; HttpOnly"])
            case "/api/v1/library/settings": return .init(body: "{}")
            default: return .init(body: "", headers: ["Content-Type": "image/png"], data: png)
            }
        }
        let client = makeClient()
        try await client.login(username: "tester", password: "temporary-test-secret")
        let privateData = try await client.data("/api/v1/assets/avatar")
        XCTAssertEqual(privateData, png)
        XCTAssertTrue(recorder.requests.last?.value(forHTTPHeaderField: "Cookie")?.contains("treasure_session=test-token") == true)
        _ = try await client.data("https://storage.example.test/image.png")
        XCTAssertNil(recorder.requests.last?.value(forHTTPHeaderField: "Cookie"))
        XCTAssertNil(recorder.requests.last?.value(forHTTPHeaderField: "X-CSRF-Token"))
    }

    @MainActor
    func testArtworkImageCacheIsBoundedAndExpiresWithoutExtendingHits() async throws {
        let image = try await ArchiveImageDecoder.decode(makeArtworkData(width: 128, height: 64), maximumPixelSize: 64)
        let client = makeClient()
        let a = ArchiveImageRequest(api: client, path: "/api/v1/assets/a", maximumPixelSize: 64)
        let b = ArchiveImageRequest(api: client, path: "/api/v1/assets/b", maximumPixelSize: 64)
        let c = ArchiveImageRequest(api: client, path: "/api/v1/assets/c", maximumPixelSize: 64)
        let cache = ArchiveImageCache(maximumBytes: image.byteCost * 2, maximumCount: 2, lifetime: 10)
        let now = Date(timeIntervalSince1970: 100)
        cache.insert(image, for: a, now: now)
        cache.insert(image, for: b, now: now)
        XCTAssertNotNil(cache.image(for: a, now: now.addingTimeInterval(1)))
        cache.insert(image, for: c, now: now.addingTimeInterval(2))
        XCTAssertNil(cache.image(for: b, now: now.addingTimeInterval(2)), "Least recently used entry must be evicted")
        XCTAssertEqual(cache.count, 2)
        XCTAssertEqual(cache.byteCount, image.byteCost * 2)
        XCTAssertNotNil(cache.image(for: a, now: now.addingTimeInterval(9)))
        XCTAssertNil(cache.image(for: a, now: now.addingTimeInterval(10)), "Cache hits must not postpone authorization refresh forever")
        let tooSmall = ArchiveImageCache(maximumBytes: image.byteCost - 1)
        tooSmall.insert(image, for: a)
        XCTAssertEqual(tooSmall.count, 0)
        XCTAssertEqual(tooSmall.byteCount, 0)
    }

    @MainActor
    func testArtworkRequestIdentityFencesSessionAndServerChanges() async throws {
        MockURLProtocol.store.set { request in
            if request.url?.path == "/api/v1/server" { return .init(body: "{\"application\":\"treasure-up\",\"api_version\":1}") }
            return .init(body: "{}")
        }
        let client = makeClient()
        let before = ArchiveImageRequest(api: client, path: "/api/v1/assets/a", maximumPixelSize: 128)
        let image = try await ArchiveImageDecoder.decode(makeArtworkData(width: 128, height: 64), maximumPixelSize: 64)
        let cache = ArchiveImageCache()
        cache.insert(image, for: before)
        try await client.logout()
        let loggedOut = ArchiveImageRequest(api: client, path: "/api/v1/assets/a", maximumPixelSize: 128)
        XCTAssertNotEqual(before, loggedOut)
        XCTAssertFalse(before.hasSameSession(as: loggedOut))
        XCTAssertNil(cache.image(for: loggedOut))
        XCTAssertEqual(cache.byteCount, 0)
        try await client.connect("https://other.example.test")
        let switched = ArchiveImageRequest(api: client, path: "/api/v1/assets/a", maximumPixelSize: 128)
        XCTAssertFalse(loggedOut.hasSameSession(as: switched))
        XCTAssertEqual(switched.path, "https://other.example.test/api/v1/assets/a")
        let otherClient = makeClient()
        XCTAssertFalse(before.hasSameSession(as: ArchiveImageRequest(api: otherClient, path: before.path, maximumPixelSize: 128)))
    }

    @MainActor
    func testArchiveImageDownsamplesAndRejectsInvalidOrCancelledInput() async throws {
        let data = try makeArtworkData(width: 1200, height: 800)
        let image = try await ArchiveImageDecoder.decode(data, maximumPixelSize: 128)
        XCTAssertLessThanOrEqual(image.image.width, 128)
        XCTAssertLessThanOrEqual(image.image.height, 128)
        XCTAssertGreaterThan(image.image.width, image.image.height)
        XCTAssertLessThanOrEqual(image.byteCost, 128 * 128 * 8)
        do {
            _ = try await ArchiveImageDecoder.decode(Data("not an image".utf8), maximumPixelSize: 128)
            XCTFail("Invalid images must remain placeholders")
        } catch APIError.decoding { }
        let pending = Task { try await ArchiveImageDecoder.decode(data, maximumPixelSize: 128) }
        pending.cancel()
        do {
            _ = try await pending.value
            XCTFail("Cancelled image requests cannot publish decoded content")
        } catch is CancellationError { }
        XCTAssertEqual(ArchiveImageRequest.pixelLimit(points: 36, scale: 3), 128)
        XCTAssertEqual(ArchiveImageRequest.pixelLimit(points: 3000, scale: 3), 1024)
        XCTAssertEqual(ArchiveImageRequest.pixelLimit(points: .nan, scale: 3), 64)
    }

    func testCommentUploaderAndPinnedFlagsRemainIndependentFromNames() throws {
        let comment = try decode(JSONValue.self, """
        {"author":{"name":"名字包含UP主"},"is_uploader":false,"is_pinned":true}
        """)
        XCTAssertEqual(comment["is_uploader"]?.boolValue, false)
        XCTAssertEqual(comment["is_pinned"]?.boolValue, true)
        let legacy = try decode(JSONValue.self, "{\"author\":{\"name\":\"UP主\"}}")
        XCTAssertNil(legacy["is_uploader"]?.boolValue)
    }

    private func makeArtworkData(width: Int, height: Int) throws -> Data {
        let context = try XCTUnwrap(CGContext(data: nil, width: width, height: height, bitsPerComponent: 8,
            bytesPerRow: width * 4, space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue))
        context.setFillColor(CGColor(red: 0.2, green: 0.6, blue: 0.5, alpha: 1))
        context.fill(CGRect(x: 0, y: 0, width: CGFloat(width), height: CGFloat(height)))
        let image = try XCTUnwrap(context.makeImage())
        let data = NSMutableData()
        let destination = try XCTUnwrap(CGImageDestinationCreateWithData(data as CFMutableData, "public.png" as CFString, 1, nil))
        CGImageDestinationAddImage(destination, image, nil)
        XCTAssertTrue(CGImageDestinationFinalize(destination))
        return data as Data
    }

    @MainActor
    private func makeClient() -> APIClient {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [MockURLProtocol.self]
        return APIClient(baseURL: URL(string: "https://api.example.test")!,
                         defaults: isolatedDefaults(),
                         sessionConfiguration: configuration, persistSession: false)
    }
}

private struct BlockingDecodeProbe: Decodable, Sendable {
    static let gate = DecodeWorkerGate()
    init(from decoder: Decoder) throws {
        _ = try decoder.singleValueContainer().decode(JSONValue.self)
        Self.gate.blockWorker()
    }
}

/// A bounded barrier proves the UI actor remains usable while the synchronous
/// decoder is busy, without depending on CPU speed or a live API response.
private final class DecodeWorkerGate: @unchecked Sendable {
    private let lock = NSLock()
    private let semaphore = DispatchSemaphore(value: 0)
    private var started = false
    private var wasMainThread = false
    private var released = false
    var hasStarted: Bool { lock.withLock { started } }
    var decodedOnMainThread: Bool { lock.withLock { wasMainThread } }
    func blockWorker() {
        lock.withLock { started = true; wasMainThread = Thread.isMainThread }
        _ = semaphore.wait(timeout: .now() + 5)
    }
    func release() {
        let shouldSignal = lock.withLock {
            guard !released else { return false }
            released = true
            return true
        }
        if shouldSignal { semaphore.signal() }
    }
}

private final class RequestRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var values: [URLRequest] = []
    var requests: [URLRequest] { lock.withLock { values } }
    func append(_ request: URLRequest) { lock.withLock { values.append(request) } }
}

private final class MockURLProtocol: URLProtocol, @unchecked Sendable {
    struct Reply: Sendable {
        var status = 200
        var body: String
        var headers: [String: String] = [:]
        var delay: TimeInterval = 0
        var responseURL: URL?
        var data: Data?
    }
    final class Store: @unchecked Sendable {
        private let lock = NSLock()
        private var handler: (@Sendable (URLRequest) throws -> Reply)?
        func set(_ handler: @escaping @Sendable (URLRequest) throws -> Reply) { lock.withLock { self.handler = handler } }
        func reply(to request: URLRequest) throws -> Reply {
            guard let handler = lock.withLock({ handler }) else { throw URLError(.badServerResponse) }
            return try handler(request)
        }
    }
    static let store = Store()
    private let loadingLock = NSLock()
    private var stopped = false
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        do {
            let reply = try Self.store.reply(to: request)
            if reply.delay > 0 {
                DispatchQueue.global().asyncAfter(deadline: .now() + reply.delay) { self.finish(reply) }
            } else { finish(reply) }
        } catch { client?.urlProtocol(self, didFailWithError: error) }
    }
    private func finish(_ reply: Reply) {
        guard !loadingLock.withLock({ stopped }) else { return }
        let response = HTTPURLResponse(url: reply.responseURL ?? request.url!, statusCode: reply.status, httpVersion: "HTTP/1.1", headerFields: reply.headers)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: reply.data ?? Data(reply.body.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() { loadingLock.withLock { stopped = true } }
}
