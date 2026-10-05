import XCTest
import AVFoundation
@testable import TreasureUp

@MainActor
final class PlaybackQueueTests: XCTestCase {
    private func video(_ id: String, parts: [VideoPart] = []) -> ArchiveVideo {
        ArchiveVideo(id: id, title: id, playable: !parts.isEmpty, parts: parts)
    }
    private func part(_ id: String, position: Int, playable: Bool = true) -> VideoPart {
        VideoPart(id: id, position: position, duration: 120, variants: playable ? [MediaVariant(id: "\(id)-source")] : [])
    }

    func testPartsTakePriorityAndMissingPartsAreSkippedInBothDirections() {
        let parts = [part("p3", position: 3), part("missing", position: 2, playable: false), part("p1", position: 1)]
        XCTAssertEqual(PlaybackQueue.target(parts: parts, partId: "p1", videos: ["a", "b"], videoId: "a", direction: 1, ended: true), .part("p3"))
        XCTAssertEqual(PlaybackQueue.target(parts: parts, partId: "p3", videos: ["a", "b"], videoId: "b", direction: -1), .part("p1"))
        XCTAssertEqual(PlaybackQueue.target(parts: parts, partId: "p1", videos: ["a", "b"], videoId: "b", direction: -1), .video("a"))
    }

    func testEndModesPauseRepeatWholeVideoAndContinueWithoutWrappingQueue() {
        let parts = [part("p1", position: 1), part("p2", position: 2)]
        func target(_ mode: PlaybackQueueMode, videoId: String = "a") -> PlaybackQueueTarget {
            PlaybackQueue.target(parts: parts, partId: "p2", videos: ["a", "b"], videoId: videoId,
                                 direction: 1, ended: true, mode: mode)
        }
        XCTAssertEqual(target(.pause), .stop)
        XCTAssertEqual(target(.repeatVideo), .part("p1"))
        XCTAssertEqual(target(.continuous), .video("b"))
        XCTAssertEqual(target(.continuous, videoId: "b"), .stop)
        XCTAssertEqual(PlaybackQueue.target(parts: [parts[0]], partId: "p1", videos: ["a"], videoId: "a", direction: 1, ended: true, mode: .repeatVideo), .replay)
        XCTAssertEqual(PlaybackQueue.target(parts: [], partId: nil, videos: ["a"], videoId: "a", direction: 1, ended: true, mode: .repeatVideo), .stop)
    }

    func testAutomaticSwitchOffStopsAtPartEndButManualNextStillWorks() {
        let parts = [part("p1", position: 1), part("p2", position: 2)]
        XCTAssertEqual(PlaybackQueue.target(parts: parts, partId: "p1", videos: ["a", "b"], videoId: "a", direction: 1, ended: true, mode: .continuous, autoAdvance: false), .stop)
        XCTAssertEqual(PlaybackQueue.target(parts: parts, partId: "p2", videos: ["a", "b"], videoId: "a", direction: 1, mode: .pause, autoAdvance: false), .video("b"))
    }

    func testSelectionKeepsHistoryAndReorderingTracksCurrentByIdentity() {
        let queue = PlaybackQueue()
        queue.configure(videos: [video("a"), video("b"), video("c"), video("b")], currentVideoId: "b")
        XCTAssertEqual(queue.items.map(\.id), ["a", "b", "c"])
        XCTAssertEqual(queue.currentIndex, 1)
        queue.select(video("c"))
        XCTAssertEqual(queue.items.map(\.id), ["a", "b", "c"])
        XCTAssertEqual(queue.candidates(direction: -1).map(\.id), ["b", "a"])
        queue.move(fromOffsets: IndexSet(integer: 2), toOffset: 0)
        XCTAssertEqual(queue.items.map(\.id), ["c", "a", "b"])
        XCTAssertEqual(queue.currentIndex, 0)
        queue.remove(id: "a")
        XCTAssertEqual(queue.items.map(\.id), ["c", "b"])
        queue.remove(id: "c")
        XCTAssertNil(queue.currentIndex)
        XCTAssertEqual(queue.items.map(\.id), ["b"])
        queue.clear()
        XCTAssertTrue(queue.items.isEmpty)
        XCTAssertNil(queue.currentId)
    }

    func testContextLoadsAllPagesAndPlacesLateCurrentVideoInServerOrder() async {
        let queue = PlaybackQueue()
        let context = PlaybackQueueContext(title: "搜索", query: ["q": "关键词", "sort": "published", "creator_id": "up", "page": "8", "page_size": "24"])
        queue.configure(context: context, current: video("v205", parts: [part("p205", position: 1)]))
        var requests: [Int] = []
        await queue.loadAll { scope, page in
            requests.append(page)
            XCTAssertEqual(scope.parameters(page: page)["q"], "关键词")
            XCTAssertEqual(scope.parameters(page: page)["sort"], "published")
            XCTAssertEqual(scope.parameters(page: page)["page"], String(page))
            XCTAssertEqual(scope.parameters(page: page)["page_size"], "100")
            let start = (page - 1) * 100
            return Page(items: (start..<min(250, start + 100)).map { self.video("v\($0)") }, total: 250, page: page, pageSize: 100)
        }
        XCTAssertEqual(requests, [1, 2, 3])
        XCTAssertEqual(queue.items.count, 250)
        XCTAssertEqual(queue.currentIndex, 205)
        XCTAssertEqual(queue.items[205].parts.first?.id, "p205")
        XCTAssertTrue(queue.isComplete)
        XCTAssertFalse(queue.isLoading)
    }

    func testFailedPageRemainsRetryableWithoutDroppingEarlierEntries() async {
        let queue = PlaybackQueue()
        queue.configure(context: .collection(id: "collection", title: "合集"), current: video("a"))
        var requests: [Int] = []
        await queue.loadAll { _, page in
            requests.append(page)
            if page == 2 { throw URLError(.networkConnectionLost) }
            return Page(items: [self.video("a")], total: 3, page: page, pageSize: 1)
        }
        XCTAssertEqual(queue.items.map(\.id), ["a"])
        XCTAssertNotNil(queue.errorMessage)
        await queue.loadAll { _, page in
            requests.append(page)
            return Page(items: [self.video(page == 2 ? "b" : "c")], total: 3, page: page, pageSize: 1)
        }
        XCTAssertEqual(requests, [1, 2, 2, 3])
        XCTAssertEqual(queue.items.map(\.id), ["a", "b", "c"])
        XCTAssertNil(queue.errorMessage)
    }

    func testEmptyAndRepeatedPagesTerminateInsteadOfLooping() async {
        let queue = PlaybackQueue()
        queue.configure(context: .creator(id: "up", title: "UP"), current: video("a"))
        var count = 0
        await queue.loadAll { _, page in
            count += 1
            return Page(items: page == 1 ? [self.video("a")] : [], total: 9_999, page: page, pageSize: 1)
        }
        XCTAssertEqual(count, 2)
        XCTAssertTrue(queue.isComplete)
        queue.configure(context: .creator(id: "up", title: "UP"), current: video("a"))
        count = 0
        await queue.loadAll { _, page in
            count += 1
            return Page(items: [self.video("a")], total: 9_999, page: page, pageSize: 1)
        }
        XCTAssertEqual(count, 2)
        XCTAssertNotNil(queue.errorMessage)
        XCTAssertFalse(queue.isComplete)
    }

    func testReplacingContextFencesLatePageAndManualEditsSurviveLaterPages() async {
        let queue = PlaybackQueue()
        queue.configure(context: .creator(id: "old", title: "旧"), current: video("a"))
        await queue.loadAll { _, page in
            queue.configure(videos: [self.video("new")], currentVideoId: "new", title: "新")
            return Page(items: [self.video("old")], total: 1, page: page, pageSize: 100)
        }
        XCTAssertEqual(queue.items.map(\.id), ["new"])
        XCTAssertEqual(queue.title, "新")
        queue.configure(context: .personal(id: "saved", title: "片单"), current: video("b"))
        await queue.loadAll { scope, page in
            XCTAssertEqual(scope.path, "/me/playlists/saved/videos")
            XCTAssertEqual(scope.parameters(page: page)["playable_only"], "true")
            if page == 2 {
                queue.enqueue(self.video("custom"))
                queue.remove(id: "a")
                queue.move(fromOffsets: IndexSet(integer: 1), toOffset: 0)
            }
            return Page(items: page == 1 ? [self.video("a"), self.video("b")] : [self.video("c")], total: 3, page: page, pageSize: 2)
        }
        XCTAssertEqual(queue.items.map(\.id), ["custom", "b", "c"])
        XCTAssertEqual(queue.currentIndex, 1)
    }

    func testQueuePreferencesAreIsolatedByBothServerAndAccount() {
        let name = "TreasureQueueTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: name)!
        defer { defaults.removePersistentDomain(forName: name) }
        let first = URL(string: "https://first.example.test")!, second = URL(string: "https://second.example.test")!
        let preferences = PlaybackQueuePreferences(mode: .repeatVideo, autoAdvance: false)
        preferences.save(defaults: defaults, server: first, userId: "same-user")
        XCTAssertEqual(PlaybackQueuePreferences.load(defaults: defaults, server: first, userId: "same-user"), preferences)
        for (server, user) in [(first, "other" as String?), (second, "same-user"), (first, nil)] {
            XCTAssertEqual(PlaybackQueuePreferences.load(defaults: defaults, server: server, userId: user), PlaybackQueuePreferences())
        }
        defaults.set(Data("broken".utf8), forKey: PlaybackQueuePreferences.key(server: first, userId: "same-user"))
        XCTAssertEqual(PlaybackQueuePreferences.load(defaults: defaults, server: first, userId: "same-user"), PlaybackQueuePreferences())
    }

    func testDuplicateEndNotificationIsConsumedOnceAndSeekRearms() {
        var guardState = PlaybackEndGuard()
        let first = UUID(), second = UUID()
        XCTAssertTrue(guardState.consume(first))
        XCTAssertFalse(guardState.consume(first))
        guardState.rearm(second)
        XCTAssertFalse(guardState.consume(first))
        guardState.rearm(first)
        XCTAssertTrue(guardState.consume(first))
        XCTAssertTrue(guardState.consume(second))
        XCTAssertFalse(guardState.consume(second))
    }

    func testCoordinatorLoadsCompleteDetailsSkipsUnavailableAndReturnsToPreviousLastPart() async {
        QueueTestURLProtocol.store.set { request in
            if request.url?.path.hasSuffix("/videos/missing") == true { return .init(body: "{\"id\":\"missing\",\"parts\":[]}") }
            if request.url?.path.hasSuffix("/videos/b") == true {
                return .init(body: "{\"id\":\"b\",\"parts\":[{\"id\":\"b1\",\"position\":1,\"variants\":[{\"id\":\"b-source\"}]}]}")
            }
            return .init(status: 503, body: "{\"detail\":\"Media is deliberately not loaded by this test\"}")
        }
        await withCoordinator { coordinator in
            let first = self.video("a", parts: [self.part("a1", position: 1), self.part("a2", position: 2)])
            coordinator.configureQueue(videos: [first, self.video("missing"), self.video("b")], currentVideoId: "a")
            await coordinator.play(video: first, part: first.parts[1])
            await coordinator.nextPart()
            XCTAssertEqual(coordinator.currentVideo?.id, "b")
            XCTAssertEqual(coordinator.currentPart?.id, "b1")
            XCTAssertEqual(coordinator.queueIndex, 2)
            XCTAssertTrue(coordinator.hasPrevious)
            await coordinator.previousPart()
            XCTAssertEqual(coordinator.currentVideo?.id, "a")
            XCTAssertEqual(coordinator.currentPart?.id, "a2")
            XCTAssertEqual(coordinator.queue.map(\.id), ["a", "missing", "b"])
            XCTAssertEqual(coordinator.queueIndex, 0)
        }
    }

    func testCoordinatorNetworkFailurePreservesFailedEntryAndExplicitRetryTarget() async {
        QueueTestURLProtocol.store.set { _ in .init(status: 503, body: "{\"detail\":\"Temporary failure\"}") }
        await withCoordinator { coordinator in
            coordinator.configureQueue(videos: [self.video("a"), self.video("b")], currentVideoId: nil)
            await coordinator.playQueueItem(id: "a")
            XCTAssertNil(coordinator.currentVideo)
            XCTAssertNotNil(coordinator.queueError)
            XCTAssertEqual(coordinator.queue.map(\.id), ["a", "b"])
            XCTAssertEqual(QueueTestURLProtocol.store.paths, ["/api/v1/videos/a"])
            QueueTestURLProtocol.store.set { request in
                if request.url?.path.hasSuffix("/videos/a") == true {
                    return .init(body: "{\"id\":\"a\",\"parts\":[{\"id\":\"a1\",\"variants\":[{\"id\":\"source\"}]}]}")
                }
                return .init(status: 503, body: "{}")
            }
            await coordinator.retryQueueLoading()
            XCTAssertEqual(coordinator.currentVideo?.id, "a")
            XCTAssertEqual(coordinator.queueIndex, 0)
            XCTAssertNil(coordinator.queueError)
            XCTAssertFalse(QueueTestURLProtocol.store.paths.contains("/api/v1/videos/b"))
            XCTAssertEqual(coordinator.queue.map(\.id), ["a", "b"])
        }
    }

    func testCoordinatorExhaustsUnplayableCandidatesOnceWithoutRemovingThem() async {
        QueueTestURLProtocol.store.set { request in
            .init(body: "{\"id\":\"\(request.url!.lastPathComponent)\",\"parts\":[]}")
        }
        await withCoordinator { coordinator in
            coordinator.configureQueue(videos: [self.video("a"), self.video("b")], currentVideoId: nil)
            await coordinator.playQueueItem(id: "a")
            XCTAssertNil(coordinator.currentVideo)
            XCTAssertNotNil(coordinator.queueError)
            XCTAssertEqual(QueueTestURLProtocol.store.paths, ["/api/v1/videos/a", "/api/v1/videos/b"])
            XCTAssertEqual(coordinator.queue.map(\.id), ["a", "b"])
            XCTAssertFalse(coordinator.queueTransitioning)
        }
    }

    func testLateDetailResponseCannotReplaceNewQueueSelection() async {
        QueueTestURLProtocol.store.set { request in
            let id = request.url!.lastPathComponent
            if ["a", "b"].contains(id) {
                return .init(body: "{\"id\":\"\(id)\",\"parts\":[{\"id\":\"\(id)1\",\"variants\":[{\"id\":\"source\"}]}]}", delay: id == "a" ? 0.2 : 0)
            }
            return .init(status: 503, body: "{}")
        }
        await withCoordinator { coordinator in
            coordinator.configureQueue(videos: [self.video("a"), self.video("b")], currentVideoId: nil)
            let first = Task { await coordinator.playQueueItem(id: "a") }
            for _ in 0..<100 where QueueTestURLProtocol.store.paths.isEmpty {
                try? await Task.sleep(for: .milliseconds(5))
            }
            XCTAssertFalse(QueueTestURLProtocol.store.paths.isEmpty)
            await coordinator.playQueueItem(id: "b")
            await first.value
            XCTAssertEqual(coordinator.currentVideo?.id, "b")
            XCTAssertEqual(coordinator.queueIndex, 1)
            XCTAssertEqual(coordinator.queue.map(\.id), ["a", "b"])
        }
    }

    func testTemporaryRateDoesNotOverwritePreferredRateWhenKVOArrives() async {
        await withCoordinator { coordinator in
            coordinator.setRate(1.25)
            coordinator.player.replaceCurrentItem(with: AVPlayerItem(asset: AVMutableComposition()))
            coordinator.beginTemporaryRate(2)
            coordinator.player.rate = 2
            let player = coordinator.player
            await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
                DispatchQueue.global().async {
                    Self.emitRateKVO(player)
                    continuation.resume()
                }
            }
            await Task.yield()
            XCTAssertEqual(coordinator.temporaryRate, 2)
            XCTAssertEqual(coordinator.preferredRate, 1.25)
            coordinator.endTemporaryRate()
            XCTAssertNil(coordinator.temporaryRate)
            XCTAssertEqual(coordinator.preferredRate, 1.25)
        }
    }

    func testRenewalWhileWaitingForQueueDoesNotLeaveNavigationLocked() async {
        QueueTestURLProtocol.store.set { request in
            if request.url?.path == "/api/v1/videos" {
                return .init(body: "{\"items\":[{\"id\":\"a\"},{\"id\":\"b\"}],\"total\":2,\"page\":1,\"page_size\":100}", delay: 0.2)
            }
            if request.url?.path == "/api/v1/videos/b" {
                return .init(body: "{\"id\":\"b\",\"parts\":[{\"id\":\"b1\",\"variants\":[{\"id\":\"source\"}]}]}")
            }
            return .init(status: 503, body: "{}")
        }
        await withCoordinator { coordinator in
            let first = self.video("a", parts: [self.part("a1", position: 1)])
            await coordinator.start(video: first, context: PlaybackQueueContext(title: "库"))
            let pending = Task { await coordinator.nextPart() }
            for _ in 0..<100 where !coordinator.queueTransitioning { await Task.yield() }
            XCTAssertTrue(coordinator.queueTransitioning)
            await coordinator.renew()
            await pending.value
            XCTAssertFalse(coordinator.queueTransitioning)
            await coordinator.nextPart()
            XCTAssertEqual(coordinator.currentVideo?.id, "b")
        }
    }

    func testRepeatedCurrentSelectionKeepsSecondPartAndDoesNotRecreateSession() async {
        QueueTestURLProtocol.store.set { _ in .init(status: 503, body: "{}") }
        await withCoordinator { coordinator in
            let source = self.video("a", parts: [self.part("a1", position: 1), self.part("a2", position: 2)])
            await coordinator.start(video: source, part: source.parts[1])
            // Supply an existing local player item: this regression concerns row
            // selection and API traffic, not a particular media decoder.
            coordinator.errorMessage = nil
            let item = AVPlayerItem(asset: AVMutableComposition())
            coordinator.player.replaceCurrentItem(with: item)
            let calls = QueueTestURLProtocol.store.paths
            await coordinator.playQueueItem(id: "a")
            await coordinator.start(video: source, part: source.parts[1])
            XCTAssertEqual(coordinator.currentPart?.id, "a2")
            XCTAssertTrue(coordinator.player.currentItem === item)
            XCTAssertEqual(QueueTestURLProtocol.store.paths, calls)
        }
    }

    func testRepeatedSelectionWhileStartingIssuesOnlyOneSessionRequest() async {
        QueueTestURLProtocol.store.set { _ in .init(status: 503, body: "{}", delay: 0.15) }
        await withCoordinator { coordinator in
            let source = self.video("a", parts: [self.part("a1", position: 1), self.part("a2", position: 2)])
            let starting = Task { await coordinator.start(video: source, part: source.parts[1]) }
            for _ in 0..<100 where QueueTestURLProtocol.store.paths.isEmpty {
                try? await Task.sleep(for: .milliseconds(5))
            }
            XCTAssertTrue(coordinator.isLoading)
            coordinator.seek(to: 75)
            await coordinator.start(video: source, part: source.parts[1])
            await coordinator.playQueueItem(id: "a")
            XCTAssertEqual(coordinator.currentTime, 75)
            await starting.value
            XCTAssertEqual(coordinator.currentPart?.id, "a2")
            XCTAssertEqual(QueueTestURLProtocol.store.paths.filter { $0 == "/api/v1/playback-sessions" }.count, 1)
        }
    }

    func testFailedCurrentSelectionRetriesSamePartInsteadOfReturningToFirstPart() async {
        QueueTestURLProtocol.store.set { _ in .init(status: 503, body: "{}") }
        await withCoordinator { coordinator in
            let source = self.video("a", parts: [self.part("a1", position: 1), self.part("a2", position: 2)])
            await coordinator.start(video: source, part: source.parts[1])
            XCTAssertNotNil(coordinator.errorMessage)
            await coordinator.playQueueItem(id: "a")
            XCTAssertEqual(coordinator.currentPart?.id, "a2")
            XCTAssertEqual(QueueTestURLProtocol.store.paths.filter { $0 == "/api/v1/playback-sessions" }.count, 2)
        }
    }

    func testStartupSeekWinsAgainstBothEarlyAndLateHistoryResponses() {
        for historyFirst in [true, false] {
            var position = PlaybackStartupPosition()
            if historyFirst { position.acceptHistory(20, duration: 120) }
            position.requestSeek(75)
            if !historyFirst { position.acceptHistory(20, duration: 120) }
            XCTAssertEqual(position.resolved(duration: 120), 75)
            XCTAssertFalse(position.acceptsHistory)
            let revision = position.revision
            // Zero is a deliberate seek, not the absence of a resume position.
            position.requestSeek(0)
            position.acceptHistory(40, duration: 120)
            XCTAssertNotEqual(position.revision, revision)
            XCTAssertEqual(position.resolved(duration: 120), 0)
            XCTAssertTrue(position.hasExplicitSeek)
        }
    }

    func testExpiredHistoryWindowCannotJumpPlaybackAndAutomaticStartDoesNotResume() {
        var position = PlaybackStartupPosition()
        position.acceptHistory(25, duration: 120)
        position.closeHistoryWindow()
        position.acceptHistory(60, duration: 120)
        XCTAssertEqual(position.resolved(duration: 120), 25)
        var automatic = PlaybackStartupPosition(resumeHistory: false)
        automatic.acceptHistory(60, duration: 120)
        XCTAssertEqual(automatic.resolved(duration: 120), 0)
        automatic.requestSeek(500)
        XCTAssertEqual(automatic.resolved(duration: 120), 120)
    }

    func testReplayOnlyRestartsAtActualEndOfFiniteMedia() {
        XCTAssertTrue(PlaybackStartupPosition.shouldReplay(position: 120, duration: 120))
        XCTAssertTrue(PlaybackStartupPosition.shouldReplay(position: 119.95, duration: 120))
        XCTAssertFalse(PlaybackStartupPosition.shouldReplay(position: 119, duration: 120))
        XCTAssertFalse(PlaybackStartupPosition.shouldReplay(position: 0, duration: 0))
        XCTAssertFalse(PlaybackStartupPosition.shouldReplay(position: .infinity, duration: 120))
        XCTAssertFalse(PlaybackStartupPosition.shouldReplay(position: 120, duration: .nan))
    }

    func testReplayPreservesPlayIntentAcrossLatePausedKVOAndStillHonorsUserPause() async {
        QueueTestURLProtocol.store.set { _ in .init(status: 503, body: "{}") }
        let player = ReplayTestPlayer()
        defer { player.finishRemainingSeeks() }
        await withCoordinator(player: player) { coordinator in
            await coordinator.start(video: self.video("a", parts: [self.part("a1", position: 1)]))
            coordinator.errorMessage = nil
            player.replaceCurrentItem(with: AVPlayerItem(asset: AVMutableComposition()))
            coordinator.seek(to: 120)
            coordinator.resume()
            await self.waitForSeek(player)
            await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
                DispatchQueue.global().async {
                    Self.emitPausedKVO(player)
                    continuation.resume()
                }
            }
            for _ in 0..<10 { await Task.yield() }
            XCTAssertTrue(player.playedRates.isEmpty)
            player.finishNextSeek()
            for _ in 0..<100 where player.playedRates.isEmpty { try? await Task.sleep(for: .milliseconds(5)) }
            XCTAssertEqual(player.playedRates, [1])

            coordinator.seek(to: 120)
            coordinator.resume()
            await self.waitForSeek(player)
            coordinator.pause()
            player.finishNextSeek()
            for _ in 0..<10 { await Task.yield() }
            XCTAssertEqual(player.playedRates, [1], "An explicit pause must win while replay is seeking")
        }
    }

    func testReplayWaitsForLatestGestureBeforeStartingAudio() async {
        QueueTestURLProtocol.store.set { _ in .init(status: 503, body: "{}") }
        let player = ReplayTestPlayer()
        defer { player.finishRemainingSeeks() }
        await withCoordinator(player: player) { coordinator in
            await coordinator.start(video: self.video("a", parts: [self.part("a1", position: 1)]))
            coordinator.errorMessage = nil
            player.replaceCurrentItem(with: AVPlayerItem(asset: AVMutableComposition()))
            coordinator.seek(to: 120)
            coordinator.resume()
            // This can arrive before the replay task gets its first actor turn.
            coordinator.seek(to: 75)
            await self.waitForSeek(player)
            XCTAssertEqual(player.pendingTimes, [75])
            // A second gesture also wins while the first seek is suspended.
            coordinator.seek(to: 90)
            XCTAssertEqual(player.pendingTimes, [75])
            XCTAssertEqual(coordinator.currentTime, 90)
            player.finishNextSeek()
            await self.waitForSeek(player)
            XCTAssertEqual(player.pendingTimes, [90])
            XCTAssertTrue(player.playedRates.isEmpty)
            player.finishNextSeek()
            for _ in 0..<100 where player.playedRates.isEmpty { try? await Task.sleep(for: .milliseconds(5)) }
            XCTAssertEqual(player.playedRates, [1])
            XCTAssertEqual(coordinator.currentTime, 90)
        }
    }

    private func waitForSeek(_ player: ReplayTestPlayer) async {
        for _ in 0..<100 where player.pendingTimes.isEmpty { try? await Task.sleep(for: .milliseconds(5)) }
        XCTAssertFalse(player.pendingTimes.isEmpty, "Replay should issue an awaited seek")
    }

    func testDiagnosticsAreBoundedAndExcludeCredentialsURLsAndTitles() async {
        QueueTestURLProtocol.store.set { _ in .init(status: 503, body: "{}") }
        await withCoordinator { coordinator in
            var source = self.video("opaque-video-id", parts: [self.part("opaque-part-id", position: 1)])
            source.title = "private-title-not-for-diagnostics"
            source.coverUrl = "https://username:private-password@example.test/image?token=private-token"
            source.parts[0].variants[0].audioCodec = "eac3"
            await coordinator.play(video: source)
            for _ in 0..<100 { coordinator.pause() }
            let text = coordinator.diagnosticsText
            XCTAssertEqual(text.components(separatedBy: "\n").filter { $0.contains(" | pause | ") }.count, 80)
            XCTAssertTrue(text.contains("video=opaque-video-id"))
            XCTAssertTrue(text.contains("audioCodec=eac3"))
            XCTAssertTrue(text.contains("outputHz="))
            XCTAssertTrue(text.contains("routeTypes="))
            for excluded in [source.title, "https://", "username", "private-password", "private-token", "example.test"] {
                XCTAssertFalse(text.contains(excluded))
            }
        }
    }

    private nonisolated static func emitRateKVO(_ player: AVPlayer) {
        player.willChangeValue(forKey: "rate")
        player.didChangeValue(forKey: "rate")
    }

    private nonisolated static func emitPausedKVO(_ player: AVPlayer) {
        player.willChangeValue(forKey: "timeControlStatus")
        player.didChangeValue(forKey: "timeControlStatus")
    }

    private func withCoordinator(player: AVPlayer = AVPlayer(), _ operation: @MainActor (PlaybackCoordinator) async -> Void) async {
        let name = "TreasureQueueCoordinatorTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: name)!
        defer { defaults.removePersistentDomain(forName: name) }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [QueueTestURLProtocol.self]
        let api = APIClient(baseURL: URL(string: "https://queue.example.test")!, defaults: defaults,
                            sessionConfiguration: configuration, persistSession: false)
        let coordinator = PlaybackCoordinator(api: api, defaults: defaults, player: player)
        defer { coordinator.stop(clearQueue: true, saveProgress: false) }
        await operation(coordinator)
    }
}

/// Holds seek completions without loading media, so KVO and gestures can be
/// delivered during the actual coordinator's asynchronous replay transition.
private final class ReplayTestPlayer: AVPlayer, @unchecked Sendable {
    private struct Seek {
        let time: CMTime
        let completion: @Sendable (Bool) -> Void
    }
    private let stateLock = NSLock()
    private var seeks: [Seek] = []
    private var position = CMTime.zero
    private var rates: [Float] = []
    var pendingTimes: [Double] { stateLock.withLock { seeks.map { $0.time.seconds } } }
    var playedRates: [Float] { stateLock.withLock { rates } }
    override var timeControlStatus: AVPlayer.TimeControlStatus { .paused }
    override func currentTime() -> CMTime { stateLock.withLock { position } }
    override func pause() { }
    override func playImmediately(atRate rate: Float) { stateLock.withLock { rates.append(rate) } }
    override func seek(to time: CMTime, toleranceBefore: CMTime, toleranceAfter: CMTime) {
        stateLock.withLock { position = time }
    }
    override func seek(to time: CMTime, toleranceBefore: CMTime, toleranceAfter: CMTime,
                       completionHandler: @escaping @Sendable (Bool) -> Void) {
        stateLock.withLock { seeks.append(Seek(time: time, completion: completionHandler)) }
    }
    func finishNextSeek() {
        let seek: Seek? = stateLock.withLock {
            guard !seeks.isEmpty else { return nil }
            let seek = seeks.removeFirst()
            position = seek.time
            return seek
        }
        seek?.completion(true)
    }
    func finishRemainingSeeks() {
        let pending = stateLock.withLock { let pending = seeks; seeks.removeAll(); return pending }
        for seek in pending { seek.completion(false) }
    }
}

private final class QueueTestURLProtocol: URLProtocol, @unchecked Sendable {
    struct Reply: Sendable {
        var status = 200
        var body: String
        var delay: TimeInterval = 0
    }
    final class Store: @unchecked Sendable {
        private let lock = NSLock()
        private var handler: (@Sendable (URLRequest) -> Reply)?
        private var recordedPaths: [String] = []
        var paths: [String] { lock.withLock { recordedPaths } }
        func set(_ handler: @escaping @Sendable (URLRequest) -> Reply) {
            lock.withLock { self.handler = handler; recordedPaths = [] }
        }
        func reply(_ request: URLRequest) -> Reply {
            let responseHandler = lock.withLock {
                recordedPaths.append(request.url?.path ?? "")
                return self.handler
            }
            return responseHandler?(request) ?? Reply(status: 503, body: "{}")
        }
    }
    static let store = Store()
    private let lock = NSLock()
    private var stopped = false
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let reply = Self.store.reply(request)
        if reply.delay > 0 { DispatchQueue.global().asyncAfter(deadline: .now() + reply.delay) { self.finish(reply) } }
        else { finish(reply) }
    }
    private func finish(_ reply: Reply) {
        guard !lock.withLock({ stopped }) else { return }
        let response = HTTPURLResponse(url: request.url!, statusCode: reply.status, httpVersion: "HTTP/1.1", headerFields: [:])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(reply.body.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() { lock.withLock { stopped = true } }
}
