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

    func testContinuousSeekCoalescesTargetsAndRejectsOldClockSamplesUntilLastCompletion() async {
        QueueTestURLProtocol.store.set { _ in .init(status: 503, body: "{}") }
        let player = ReplayTestPlayer()
        defer { player.finishRemainingSeeks() }
        await withCoordinator(player: player) { coordinator in
            await coordinator.start(video: self.video("a", parts: [self.part("a1", position: 1)]))
            coordinator.errorMessage = nil
            player.replaceCurrentItem(with: AVPlayerItem(asset: AVMutableComposition()))
            coordinator.resume()
            player.reportTimeControlStatus(.playing)
            await self.waitForTransport("Initial playback should be active") { coordinator.isPlaying }
            let plays = player.playedRates.count
            coordinator.seek(to: 40)
            await self.waitForSeek(player)
            for target in [50.0, 60, 75, 90] { coordinator.seek(to: target) }
            XCTAssertEqual(player.pendingTimes, [40], "Only one AVPlayer seek may be in flight")
            XCTAssertTrue(coordinator.isSeeking)
            player.reportPeriodicTime(12)
            XCTAssertEqual(coordinator.currentTime, 90, "Old clock samples cannot undo the gesture preview")
            XCTAssertEqual(coordinator.presentationTime(at: Date().addingTimeInterval(10)), 90)
            player.reportTimeControlStatus(.paused)
            for _ in 0..<10 { await Task.yield() }
            XCTAssertTrue(coordinator.wantsPlayback, "Temporary seek suspension is not a user pause")
            player.finishNextSeek()
            await self.waitForSeek(player)
            XCTAssertEqual(player.pendingTimes, [90], "Intermediate drag targets should be coalesced")
            player.reportPeriodicTime(40)
            XCTAssertEqual(coordinator.currentTime, 90)
            XCTAssertEqual(player.playedRates.count, plays)
            player.finishNextSeek()
            await self.waitForTransport("Last seek should complete") { !coordinator.isSeeking }
            XCTAssertEqual(coordinator.currentTime, 90)
            XCTAssertEqual(player.playedRates.count, plays + 1)
            player.reportPeriodicTime(91)
            XCTAssertEqual(coordinator.currentTime, 91, "The real clock resumes after the latest seek completes")
        }
    }

    func testPauseDuringSeekWinsAndResumeWaitsForLatestPosition() async {
        QueueTestURLProtocol.store.set { _ in .init(status: 503, body: "{}") }
        let player = ReplayTestPlayer()
        defer { player.finishRemainingSeeks() }
        await withCoordinator(player: player) { coordinator in
            await coordinator.start(video: self.video("a", parts: [self.part("a1", position: 1)]))
            coordinator.errorMessage = nil
            player.replaceCurrentItem(with: AVPlayerItem(asset: AVMutableComposition()))
            coordinator.resume()
            let plays = player.playedRates.count
            coordinator.seek(to: 45)
            await self.waitForSeek(player)
            coordinator.pause()
            XCTAssertFalse(coordinator.isBuffering)
            player.finishNextSeek()
            await self.waitForTransport("Paused seek should finish") { !coordinator.isSeeking }
            XCTAssertEqual(player.playedRates.count, plays)
            XCTAssertFalse(coordinator.wantsPlayback)
            XCTAssertEqual(coordinator.currentTime, 45)
            coordinator.seek(to: 70)
            await self.waitForSeek(player)
            coordinator.resume()
            XCTAssertEqual(player.playedRates.count, plays, "Resume must wait for a pending seek")
            player.finishNextSeek()
            await self.waitForTransport("Resumed seek should finish") { !coordinator.isSeeking }
            XCTAssertEqual(player.playedRates.count, plays + 1)
            XCTAssertTrue(coordinator.wantsPlayback)
        }
    }

    func testLateSeekCompletionCannotChangeStoppedOrReplacementPlayback() async {
        QueueTestURLProtocol.store.set { _ in .init(status: 503, body: "{}") }
        let player = ReplayTestPlayer()
        defer { player.finishRemainingSeeks() }
        await withCoordinator(player: player) { coordinator in
            await coordinator.start(video: self.video("a", parts: [self.part("a1", position: 1)]))
            coordinator.errorMessage = nil
            player.replaceCurrentItem(with: AVPlayerItem(asset: AVMutableComposition()))
            coordinator.resume()
            coordinator.seek(to: 60)
            await self.waitForSeek(player)
            coordinator.stop(saveProgress: false)
            let plays = player.playedRates.count
            player.finishNextSeek()
            for _ in 0..<10 { await Task.yield() }
            XCTAssertNil(coordinator.currentVideo)
            XCTAssertFalse(coordinator.isSeeking)
            XCTAssertEqual(coordinator.currentTime, 0)
            XCTAssertEqual(player.playedRates.count, plays)

            await coordinator.start(video: self.video("b", parts: [self.part("b1", position: 1)]))
            coordinator.errorMessage = nil
            player.replaceCurrentItem(with: AVPlayerItem(asset: AVMutableComposition()))
            coordinator.seek(to: 50)
            await self.waitForSeek(player)
            await coordinator.start(video: self.video("c", parts: [self.part("c1", position: 1)]))
            player.replaceCurrentItem(with: AVPlayerItem(asset: AVMutableComposition()))
            coordinator.seek(to: 80)
            XCTAssertTrue(coordinator.isSeeking)
            // Old B completion must neither clear C's seeking flag nor publish 50.
            player.finishNextSeek()
            for _ in 0..<10 { await Task.yield() }
            XCTAssertEqual(coordinator.currentVideo?.id, "c")
            XCTAssertTrue(coordinator.isSeeking)
            XCTAssertEqual(coordinator.currentTime, 80)
            await self.waitForSeek(player)
            XCTAssertEqual(player.pendingTimes, [80])
            player.finishNextSeek()
            await self.waitForTransport("Replacement seek should finish") { !coordinator.isSeeking }
            XCTAssertEqual(coordinator.currentTime, 80)
        }
    }

    func testInterruptedSeekDoesNotPublishUnreachedTarget() async {
        QueueTestURLProtocol.store.set { _ in .init(status: 503, body: "{}") }
        let player = ReplayTestPlayer()
        defer { player.finishRemainingSeeks() }
        await withCoordinator(player: player) { coordinator in
            await coordinator.start(video: self.video("a", parts: [self.part("a1", position: 1)]))
            coordinator.errorMessage = nil
            player.replaceCurrentItem(with: AVPlayerItem(asset: AVMutableComposition()))
            player.reportPeriodicTime(12)
            coordinator.seek(to: 100)
            await self.waitForSeek(player)
            player.finishNextSeek(success: false)
            await self.waitForTransport("Interrupted seek should settle") { !coordinator.isSeeking }
            XCTAssertEqual(coordinator.currentTime, 12)
            XCTAssertNotNil(coordinator.statusMessage)
            XCTAssertFalse(coordinator.isBuffering)
        }
    }

    func testObsoleteSeekIsCancelledOnceAndImmediatelyChasesLatestTarget() async {
        QueueTestURLProtocol.store.set { _ in .init(status: 503, body: "{}") }
        let player = ReplayTestPlayer()
        player.completeCancelledSeeksImmediately = true
        defer { player.finishRemainingSeeks() }
        await withCoordinator(player: player) { coordinator in
            await coordinator.start(video: self.video("a", parts: [self.part("a1", position: 1)]))
            coordinator.errorMessage = nil
            player.replaceCurrentItem(with: player.makeCancellationItem())
            coordinator.resume()
            let plays = player.playedRates.count
            coordinator.seek(to: 40)
            await self.waitForSeek(player)
            let cancellations = player.seekCancellationCount
            for target in [50.0, 60, 75, 90] { coordinator.seek(to: target) }
            XCTAssertEqual(player.seekCancellationCount, cancellations + 1)
            await self.waitForSeek(player)
            XCTAssertEqual(player.pendingTimes, [90], "The obsolete network seek needs no manual completion before its replacement starts")
            XCTAssertTrue(coordinator.isSeeking)
            XCTAssertEqual(coordinator.currentTime, 90)
            XCTAssertNil(coordinator.statusMessage, "Superseding a request is not a seek failure")
            XCTAssertEqual(player.playedRates.count, plays)
            for _ in 0..<20 { coordinator.seek(to: 90) }
            XCTAssertEqual(player.seekCancellationCount, cancellations + 1, "Identical destinations must not cancel the replacement")
            player.finishNextSeek()
            await self.waitForTransport("Latest seek should finish") { !coordinator.isSeeking }
            XCTAssertEqual(player.playedRates.count, plays + 1)
            XCTAssertEqual(coordinator.currentTime, 90)
        }
    }

    func testSeekCancellationCanCompleteLateWithoutRepeatedCancellationOrStaleProgress() async {
        QueueTestURLProtocol.store.set { _ in .init(status: 503, body: "{}") }
        let player = ReplayTestPlayer()
        defer { player.finishRemainingSeeks() }
        await withCoordinator(player: player) { coordinator in
            await coordinator.start(video: self.video("a", parts: [self.part("a1", position: 1)]))
            coordinator.errorMessage = nil
            player.replaceCurrentItem(with: player.makeCancellationItem())
            let cancellations = player.seekCancellationCount
            coordinator.seek(to: 30)
            // The task has not yet issued an AVPlayer operation: do not cancel.
            coordinator.seek(to: 40)
            XCTAssertEqual(player.seekCancellationCount, cancellations)
            await self.waitForSeek(player)
            XCTAssertEqual(player.pendingTimes, [40])
            for target in [50.0, 60, 70, 70] { coordinator.seek(to: target) }
            XCTAssertEqual(player.seekCancellationCount, cancellations + 1)
            XCTAssertEqual(player.pendingTimes, [40], "This fixture deliberately delays the canceled completion")
            coordinator.pause()
            player.finishNextSeek(success: false)
            await self.waitForSeek(player)
            XCTAssertEqual(player.pendingTimes, [70])
            XCTAssertEqual(coordinator.currentTime, 70)
            XCTAssertFalse(coordinator.wantsPlayback)
            XCTAssertNil(coordinator.statusMessage)
            player.finishNextSeek()
            await self.waitForTransport("Paused replacement seek should finish") { !coordinator.isSeeking }
            XCTAssertTrue(player.playedRates.isEmpty)
        }
    }

    func testReplaySeekCancellationHonorsNewTargetAndPause() async {
        QueueTestURLProtocol.store.set { _ in .init(status: 503, body: "{}") }
        let player = ReplayTestPlayer()
        player.completeCancelledSeeksImmediately = true
        defer { player.finishRemainingSeeks() }
        await withCoordinator(player: player) { coordinator in
            await coordinator.start(video: self.video("a", parts: [self.part("a1", position: 1)]))
            coordinator.errorMessage = nil
            player.replaceCurrentItem(with: player.makeCancellationItem())
            coordinator.seek(to: 120)
            coordinator.resume()
            await self.waitForSeek(player)
            XCTAssertEqual(player.pendingTimes, [0])
            let cancellations = player.seekCancellationCount
            coordinator.seek(to: 75)
            coordinator.pause()
            await self.waitForSeek(player)
            XCTAssertEqual(player.seekCancellationCount, cancellations + 1)
            XCTAssertEqual(player.pendingTimes, [75])
            player.finishNextSeek()
            await self.waitForTransport("Replay replacement should finish") { !coordinator.isSeeking }
            XCTAssertEqual(coordinator.currentTime, 75)
            XCTAssertFalse(coordinator.wantsPlayback)
            XCTAssertTrue(player.playedRates.isEmpty)
        }
    }

    func testPlayingSeekToEndSettlesPauseWithoutAnotherEndNotification() async {
        QueueTestURLProtocol.store.set { _ in .init(status: 503, body: "{}") }
        let player = ReplayTestPlayer()
        defer { player.finishRemainingSeeks() }
        await withCoordinator(player: player) { coordinator in
            await coordinator.start(video: self.video("a", parts: [self.part("a1", position: 1)]))
            coordinator.errorMessage = nil
            player.replaceCurrentItem(with: player.makeCancellationItem())
            coordinator.queueMode = .pause
            coordinator.resume()
            let plays = player.playedRates.count
            coordinator.seek(to: 120)
            await self.waitForSeek(player)
            player.finishNextSeek()
            await self.waitForTransport("EOF seek should settle the transport without an AVPlayer end notification") { !coordinator.isSeeking }
            XCTAssertEqual(coordinator.currentTime, 120)
            XCTAssertFalse(coordinator.wantsPlayback)
            XCTAssertFalse(coordinator.isBuffering)
            XCTAssertFalse(coordinator.isPlaying)
            XCTAssertEqual(player.playedRates.count, plays, "Do not request playback at an already completed position")
        }
    }

    func testPlayingSeekToEndPreservesRepeatAndContinuousModes() async {
        for mode in [PlaybackQueueMode.repeatVideo, .continuous] {
            QueueTestURLProtocol.store.set { _ in .init(status: 503, body: "{}") }
            let player = ReplayTestPlayer()
            await withCoordinator(player: player) { coordinator in
                let source = self.video("a", parts: [self.part("a1", position: 1)])
                let next = self.video("b", parts: [self.part("b1", position: 1)])
                coordinator.configureQueue(videos: [source, next], currentVideoId: "a")
                await coordinator.start(video: source)
                coordinator.errorMessage = nil
                player.replaceCurrentItem(with: player.makeCancellationItem())
                coordinator.queueMode = mode
                coordinator.resume()
                let plays = player.playedRates.count
                coordinator.seek(to: 120)
                await self.waitForSeek(player)
                player.finishNextSeek()
                if mode == .repeatVideo {
                    await self.waitForSeek(player)
                    XCTAssertEqual(player.pendingTimes, [0])
                    XCTAssertEqual(coordinator.currentVideo?.id, "a")
                    XCTAssertEqual(player.playedRates.count, plays)
                    player.finishNextSeek()
                    await self.waitForTransport("Loop seek should finish") { !coordinator.isSeeking }
                    XCTAssertEqual(player.playedRates.count, plays + 1)
                    XCTAssertEqual(coordinator.currentTime, 0)
                } else {
                    await self.waitForTransport("Continuous mode should select the next video") { coordinator.currentVideo?.id == "b" }
                    XCTAssertEqual(coordinator.currentPart?.id, "b1")
                }
            }
            player.finishRemainingSeeks()
        }
    }

    func testPausedSeekToEndNeverAdvancesEvenWhenAutoplayIsEnabled() async {
        for mode in [PlaybackQueueMode.repeatVideo, .continuous] {
            QueueTestURLProtocol.store.set { _ in .init(status: 503, body: "{}") }
            let player = ReplayTestPlayer()
            await withCoordinator(player: player) { coordinator in
                let source = self.video("a", parts: [self.part("a1", position: 1)])
                coordinator.configureQueue(videos: [source, self.video("b", parts: [self.part("b1", position: 1)])], currentVideoId: "a")
                await coordinator.start(video: source)
                coordinator.errorMessage = nil
                player.replaceCurrentItem(with: player.makeCancellationItem())
                coordinator.queueMode = mode
                coordinator.pause()
                coordinator.seek(to: 120)
                await self.waitForSeek(player)
                player.finishNextSeek()
                await self.waitForTransport("Paused EOF seek should finish") { !coordinator.isSeeking }
                for _ in 0..<10 { await Task.yield() }
                XCTAssertEqual(coordinator.currentVideo?.id, "a")
                XCTAssertEqual(coordinator.currentTime, 120)
                XCTAssertFalse(coordinator.wantsPlayback)
                XCTAssertTrue(player.playedRates.isEmpty)
                XCTAssertTrue(player.pendingTimes.isEmpty)
            }
            player.finishRemainingSeeks()
        }
    }

    func testOldStallCheckpointCannotRecoverAfterBackwardSeekOrPauseResume() {
        let stalled = PlaybackStallCheckpoint(intentRevision: 10, position: 100)
        XCTAssertTrue(stalled.shouldRecover(intentRevision: 10, position: 100.05, wantsPlayback: true, isSeeking: false))
        XCTAssertFalse(stalled.shouldRecover(intentRevision: 11, position: 10, wantsPlayback: true, isSeeking: false),
                       "A successful backward seek cannot be compared with the old stalled position")
        XCTAssertFalse(stalled.shouldRecover(intentRevision: 12, position: 100, wantsPlayback: true, isSeeking: false),
                       "A pause/resume cycle also invalidates an old transport watchdog")
        XCTAssertFalse(stalled.shouldRecover(intentRevision: 10, position: 100, wantsPlayback: false, isSeeking: false))
        XCTAssertFalse(stalled.shouldRecover(intentRevision: 10, position: 100, wantsPlayback: true, isSeeking: true))
        XCTAssertFalse(stalled.shouldRecover(intentRevision: 10, position: 101, wantsPlayback: true, isSeeking: false))
    }

    func testProgressPersistsOnlySettledPositionsDuringPausedAndFailedSeeks() async {
        let progress = PlaybackProgressRequestStore()
        QueueTestURLProtocol.store.set { request in
            if request.url?.path == "/api/v1/auth/login" {
                return .init(body: "{\"user\":{\"id\":\"fixture-user\",\"username\":\"fixture\"},\"csrf_token\":\"fixture-token\"}")
            }
            if request.httpMethod == "PUT", request.url?.path == "/api/v1/progress/a1" {
                progress.record(request)
                return .init(body: "{}")
            }
            return .init(status: 503, body: "{}")
        }
        let player = ReplayTestPlayer()
        defer { player.finishRemainingSeeks() }
        await withCoordinator(player: player, authenticated: true) { coordinator in
            await coordinator.start(video: self.video("a", parts: [self.part("a1", position: 1)]))
            coordinator.errorMessage = nil
            player.replaceCurrentItem(with: player.makeCancellationItem())
            player.reportPeriodicTime(12)
            coordinator.seek(to: 100)
            await self.waitForSeek(player)
            coordinator.pause()
            await self.waitForTransport("Pause should persist the last confirmed position") { !progress.positions.isEmpty }
            XCTAssertEqual(progress.positions.last, 12)
            player.finishNextSeek(success: false)
            await self.waitForTransport("Failed seek should persist its true position") { !coordinator.isSeeking && progress.positions.count >= 2 }
            XCTAssertEqual(coordinator.currentTime, 12)
            XCTAssertFalse(progress.positions.contains(100), "An optimistic preview is not watch history")
            coordinator.seek(to: 80)
            await self.waitForSeek(player)
            player.finishNextSeek()
            await self.waitForTransport("Successful paused seek should persist its new position") { progress.positions.last == 80 }
            XCTAssertFalse(coordinator.wantsPlayback)
        }
    }

    private func waitForSeek(_ player: ReplayTestPlayer) async {
        for _ in 0..<100 where player.pendingTimes.isEmpty { try? await Task.sleep(for: .milliseconds(5)) }
        XCTAssertFalse(player.pendingTimes.isEmpty, "Replay should issue an awaited seek")
    }

    func testTogglePausesDuringStartupBeforeActualPlaybackBegins() async {
        QueueTestURLProtocol.store.set { _ in .init(status: 503, body: "{}", delay: 0.15) }
        await withCoordinator { coordinator in
            XCTAssertFalse(coordinator.wantsPlayback)
            let source = self.video("a", parts: [self.part("a1", position: 1)])
            let starting = Task { await coordinator.start(video: source) }
            await self.waitForRequest("/api/v1/playback-sessions")
            XCTAssertTrue(coordinator.wantsPlayback)
            XCTAssertFalse(coordinator.isPlaying)
            coordinator.togglePlayback()
            XCTAssertFalse(coordinator.wantsPlayback, "A loading video must be pausable before its first playing callback")
            await starting.value
            XCTAssertFalse(coordinator.wantsPlayback)
        }
    }

    func testBufferedPlaybackAndRapidTapsUseIntentRatherThanDelayedPlayerStatus() async {
        QueueTestURLProtocol.store.set { _ in .init(status: 503, body: "{}") }
        let player = ReplayTestPlayer()
        await withCoordinator(player: player) { coordinator in
            let source = self.video("a", parts: [self.part("a1", position: 1)])
            await coordinator.start(video: source)
            coordinator.errorMessage = nil
            player.replaceCurrentItem(with: AVPlayerItem(asset: AVMutableComposition()))
            coordinator.resume()
            player.reportTimeControlStatus(.waitingToPlayAtSpecifiedRate)
            await self.waitForTransport("Buffering callback should be observed") { coordinator.isBuffering }
            XCTAssertTrue(coordinator.wantsPlayback)
            XCTAssertFalse(coordinator.isPlaying)
            let played = player.playedRates.count
            coordinator.togglePlayback()
            XCTAssertFalse(coordinator.wantsPlayback)
            coordinator.togglePlayback()
            XCTAssertTrue(coordinator.wantsPlayback)
            coordinator.togglePlayback()
            XCTAssertFalse(coordinator.wantsPlayback)
            XCTAssertEqual(player.playedRates.count, played + 1, "Only the middle tap requests playback")

            // Deliver an old 'playing' status while the latest pause still waits
            // for acknowledgement; it must not flip the user's pause back on.
            player.reportTimeControlStatus(.playing)
            await self.waitForTransport("Delayed AVPlayer status should arrive") { coordinator.isPlaying }
            XCTAssertFalse(coordinator.wantsPlayback)
            player.reportTimeControlStatus(.paused)
            await self.waitForTransport("Pause acknowledgement should arrive") { !coordinator.isPlaying }
            XCTAssertFalse(coordinator.wantsPlayback)
        }
    }

    func testStopAndFailedSessionResetPlaybackIntent() async {
        QueueTestURLProtocol.store.set { _ in .init(status: 503, body: "{}") }
        await withCoordinator { coordinator in
            let source = self.video("a", parts: [self.part("a1", position: 1)])
            await coordinator.start(video: source)
            XCTAssertNotNil(coordinator.errorMessage)
            XCTAssertFalse(coordinator.wantsPlayback)
            coordinator.errorMessage = nil
            coordinator.player.replaceCurrentItem(with: AVPlayerItem(asset: AVMutableComposition()))
            coordinator.resume()
            XCTAssertTrue(coordinator.wantsPlayback)
            coordinator.stop()
            XCTAssertFalse(coordinator.wantsPlayback)
            XCTAssertFalse(coordinator.isPlaying)
            coordinator.resume()
            XCTAssertFalse(coordinator.wantsPlayback, "A stopped player has no item to resume")
        }
    }

    func testInterruptionRestoresBufferingIntentButManualPauseCancelsAutomaticResume() async {
        QueueTestURLProtocol.store.set { _ in .init(status: 503, body: "{}") }
        let player = ReplayTestPlayer()
        await withCoordinator(player: player) { coordinator in
            let source = self.video("a", parts: [self.part("a1", position: 1)])
            await coordinator.start(video: source)
            coordinator.errorMessage = nil
            player.replaceCurrentItem(with: AVPlayerItem(asset: AVMutableComposition()))
            coordinator.resume()
            XCTAssertFalse(coordinator.isPlaying)
            self.postInterruption(.began)
            await self.waitForTransport("Interruption should pause pending playback") { !coordinator.wantsPlayback }
            self.postInterruption(.ended)
            await self.waitForTransport("Buffering intent should resume after interruption") { coordinator.wantsPlayback }

            self.postInterruption(.began)
            await self.waitForTransport("Second interruption should pause") { !coordinator.wantsPlayback }
            coordinator.pause()
            let requests = player.playedRates.count
            let events = coordinator.diagnosticsText.components(separatedBy: "interruption:0:").count
            self.postInterruption(.ended)
            await self.waitForTransport("Second end notification should be processed") {
                coordinator.diagnosticsText.components(separatedBy: "interruption:0:").count > events
            }
            XCTAssertFalse(coordinator.wantsPlayback)
            XCTAssertEqual(player.playedRates.count, requests)
        }
    }

    private func postInterruption(_ type: AVAudioSession.InterruptionType) {
        NotificationCenter.default.post(name: AVAudioSession.interruptionNotification,
                                        object: AVAudioSession.sharedInstance(), userInfo: [
            AVAudioSessionInterruptionTypeKey: type.rawValue,
            AVAudioSessionInterruptionOptionKey: AVAudioSession.InterruptionOptions.shouldResume.rawValue
        ])
    }

    private func waitForTransport(_ message: String, condition: @MainActor () -> Bool) async {
        for _ in 0..<100 {
            if condition() { return }
            try? await Task.sleep(for: .milliseconds(5))
        }
        XCTAssertTrue(condition(), message)
    }

    func testStopWhileDetailIsLoadingCannotCreatePlaybackSession() async {
        QueueTestURLProtocol.store.set { request in
            if request.url?.path == "/api/v1/videos/a" {
                return .init(body: #"{"id":"a","parts":[{"id":"a1","variants":[{"id":"a1-source"}]}]}"#, delay: 0.15)
            }
            return .init(status: 503, body: "{}")
        }
        await withCoordinator { coordinator in
            let starting = Task { await coordinator.play(video: self.video("a")) }
            await self.waitForRequest("/api/v1/videos/a")
            coordinator.requestsPresentation = true
            coordinator.stop()
            await starting.value
            XCTAssertNil(coordinator.currentVideo)
            XCTAssertNil(coordinator.currentPart)
            XCTAssertNil(coordinator.player.currentItem)
            XCTAssertNil(coordinator.session)
            XCTAssertFalse(coordinator.requestsPresentation)
            XCTAssertFalse(coordinator.isLoading)
            XCTAssertFalse(QueueTestURLProtocol.store.paths.contains("/api/v1/playback-sessions"))
        }
    }

    func testStopWhileSuccessfulSessionIsLoadingCannotAttachItsPlayerItem() async {
        QueueTestURLProtocol.store.set { request in
            if request.url?.path == "/api/v1/playback-sessions" {
                return .init(body: #"{"variant_id":"a1-source","url":"https://queue.example.test/media.mp4","protocol":"file"}"#, delay: 0.15)
            }
            return .init(status: 503, body: "{}")
        }
        await withCoordinator { coordinator in
            let source = self.video("a", parts: [self.part("a1", position: 1)])
            let starting = Task { await coordinator.start(video: source) }
            await self.waitForRequest("/api/v1/playback-sessions")
            coordinator.stop()
            await starting.value
            XCTAssertNil(coordinator.currentVideo)
            XCTAssertNil(coordinator.player.currentItem)
            XCTAssertNil(coordinator.session)
            XCTAssertFalse(coordinator.isLoading)
            XCTAssertFalse(coordinator.isPlaying)
            XCTAssertEqual(coordinator.queue.map(\.id), ["a"])
        }
    }

    func testStopDuringQueueSelectionPreservesQueueWithoutPlayingLateVideo() async {
        QueueTestURLProtocol.store.set { request in
            if request.url?.path == "/api/v1/videos/b" {
                return .init(body: #"{"id":"b","parts":[{"id":"b1","variants":[{"id":"b1-source"}]}]}"#, delay: 0.15)
            }
            return .init(status: 503, body: "{}")
        }
        await withCoordinator { coordinator in
            let source = self.video("a", parts: [self.part("a1", position: 1)])
            coordinator.configureQueue(videos: [source, self.video("b")], currentVideoId: "a")
            await coordinator.play(video: source)
            let switching = Task { await coordinator.playQueueItem(id: "b") }
            await self.waitForRequest("/api/v1/videos/b")
            coordinator.stop()
            await switching.value
            XCTAssertNil(coordinator.currentVideo)
            XCTAssertNil(coordinator.player.currentItem)
            XCTAssertFalse(coordinator.queueTransitioning)
            XCTAssertEqual(coordinator.queue.map(\.id), ["a", "b"])
            XCTAssertEqual(QueueTestURLProtocol.store.paths.filter { $0 == "/api/v1/playback-sessions" }.count, 1)
        }
    }

    func testStopCancelsRemainingQueuePagesAndExplicitRetryKeepsLoadedEntries() async {
        QueueTestURLProtocol.store.set { request in
            if request.url?.path == "/api/v1/videos" {
                let page = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first { $0.name == "page" }?.value
                if page == "1" { return .init(body: #"{"items":[{"id":"a"}],"total":2,"page":1,"page_size":1}"#) }
                return .init(body: #"{"items":[{"id":"b"}],"total":2,"page":2,"page_size":1}"#, delay: 0.5)
            }
            return .init(status: 503, body: "{}")
        }
        await withCoordinator { coordinator in
            let source = self.video("a", parts: [self.part("a1", position: 1)])
            await coordinator.start(video: source, context: PlaybackQueueContext(title: "保留的队列"))
            await self.waitForRequest("/api/v1/videos", count: 2)
            coordinator.stop()
            // Retry immediately, while cancellation of the old loader may still
            // be unwinding, to exercise the loading-state handoff.
            QueueTestURLProtocol.store.set { _ in
                .init(body: #"{"items":[{"id":"b"}],"total":2,"page":2,"page_size":1}"#)
            }
            await coordinator.retryQueueLoading()
            XCTAssertEqual(coordinator.queue.map(\.id), ["a", "b"])
            XCTAssertFalse(coordinator.queueLoading)
            XCTAssertNil(coordinator.currentVideo)
            XCTAssertNil(coordinator.player.currentItem)
        }
    }

    func testSelectingCurrentQualityOrRouteKeepsItemWithoutRequestAndStillAllowsErrorRetry() async {
        QueueTestURLProtocol.store.set { _ in .init(status: 503, body: "{}") }
        await withCoordinator { coordinator in
            let source = self.video("a", parts: [self.part("a1", position: 1)])
            await coordinator.start(video: source)
            coordinator.errorMessage = nil
            let item = AVPlayerItem(asset: AVMutableComposition())
            coordinator.player.replaceCurrentItem(with: item)
            let requests = QueueTestURLProtocol.store.paths
            await coordinator.selectVariant(source.parts[0].variants[0])
            await coordinator.selectRoute("")
            XCTAssertTrue(coordinator.player.currentItem === item)
            XCTAssertEqual(QueueTestURLProtocol.store.paths, requests)
            coordinator.errorMessage = "临时错误"
            await coordinator.selectVariant(source.parts[0].variants[0])
            XCTAssertEqual(QueueTestURLProtocol.store.paths.count, requests.count + 1)
            XCTAssertEqual(coordinator.currentPart?.id, "a1")
        }
    }

    func testBackgroundSidecarDecodingPreservesFormatsAndHonorsCancellation() async throws {
        let cues = try await PlaybackSidecarDecoder.danmaku(Data(##"[{"text":"测试","time":2,"color":"#abc","mode":0}]"##.utf8))
        XCTAssertEqual(cues.count, 1)
        XCTAssertEqual(cues.first?.cue.color, 0xAABBCC)
        XCTAssertEqual(cues.first?.cue.text, "测试")
        let subtitles = try await PlaybackSidecarDecoder.subtitles(Data("WEBVTT\n\n00:01.000 --> 00:03.000\n字幕 &amp; text".utf8))
        XCTAssertEqual(subtitles.first?.text, "字幕 & text")
        let canceled = Task { try await PlaybackSidecarDecoder.danmaku(Data("not-json".utf8)) }
        canceled.cancel()
        do {
            _ = try await canceled.value
            XCTFail("Canceled work must not parse or publish sidecars")
        } catch is CancellationError { }
        catch { XCTFail("Expected cancellation, got \(error)") }
    }

    private func waitForRequest(_ path: String, count: Int = 1) async {
        for _ in 0..<100 where QueueTestURLProtocol.store.paths.filter({ $0 == path }).count < count {
            try? await Task.sleep(for: .milliseconds(5))
        }
        XCTAssertGreaterThanOrEqual(QueueTestURLProtocol.store.paths.filter { $0 == path }.count, count)
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

    func testNowPlayingPublicationSkipsNormalClockTicksAndIdenticalPausedState() {
        var publication = PlaybackNowPlayingPublication()
        let base = Date(timeIntervalSinceReferenceDate: 100)
        var state = nowPlayingState()
        var writes = 0
        // A minute of the coordinator's two-second metadata checks requires one
        // system write: the lock screen advances its own elapsed-time clock.
        for second in stride(from: 0, through: 60, by: 2) {
            if publication.shouldPublish(state: state, position: 10 + Double(second),
                                         at: base.addingTimeInterval(Double(second))) { writes += 1 }
        }
        XCTAssertEqual(writes, 1)
        state.rate = 0
        XCTAssertTrue(publication.shouldPublish(state: state, position: 70, at: base.addingTimeInterval(60)))
        for second in stride(from: 62, through: 120, by: 2) {
            XCTAssertFalse(publication.shouldPublish(state: state, position: 70, at: base.addingTimeInterval(Double(second))))
        }
    }

    func testNowPlayingPublicationImmediatelyReportsSeekDriftAndMetadataChanges() {
        var publication = PlaybackNowPlayingPublication()
        let base = Date(timeIntervalSinceReferenceDate: 100)
        var state = nowPlayingState()
        XCTAssertTrue(publication.shouldPublish(state: state, position: 10, at: base))
        XCTAssertFalse(publication.shouldPublish(state: state, position: 12.2, at: base.addingTimeInterval(2)))
        XCTAssertTrue(publication.shouldPublish(state: state, position: 12.3, at: base.addingTimeInterval(2), forcePosition: true),
                      "An explicit subsecond seek still updates the system timeline")
        XCTAssertTrue(publication.shouldPublish(state: state, position: 30, at: base.addingTimeInterval(4)),
                      "Unexpected playback drift must resynchronize the system clock")
        state.title = "Changed title"
        XCTAssertTrue(publication.shouldPublish(state: state, position: 30, at: base.addingTimeInterval(4)))
        state.hasNext = false
        XCTAssertTrue(publication.shouldPublish(state: state, position: 30, at: base.addingTimeInterval(4)))
        state.rate = 1.5
        XCTAssertTrue(publication.shouldPublish(state: state, position: 30, at: base.addingTimeInterval(4)))
        XCTAssertFalse(publication.shouldPublish(state: state, position: 33, at: base.addingTimeInterval(6)))
        let artwork = NSObject()
        state.artwork = ObjectIdentifier(artwork)
        XCTAssertTrue(publication.shouldPublish(state: state, position: 33, at: base.addingTimeInterval(6)))
        XCTAssertFalse(publication.shouldPublish(state: state, position: .nan, at: base))
    }

    func testRepeatedRateCommandsAndKVOOnlyPersistActualPreferenceChanges() async {
        let name = "TreasurePlaybackWriteTests.\(UUID().uuidString)"
        let defaults = CountingPlaybackDefaults(suiteName: name)!
        defer { defaults.removePersistentDomain(forName: name) }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [QueueTestURLProtocol.self]
        let api = APIClient(baseURL: URL(string: "https://queue.example.test")!, defaults: defaults,
                            sessionConfiguration: configuration, persistSession: false)
        let player = ReplayTestPlayer()
        let coordinator = PlaybackCoordinator(api: api, defaults: defaults, player: player)
        defer { coordinator.stop(clearQueue: true, saveProgress: false) }
        let rateKey = "treasure.native.playback.rate"
        let baseline = defaults.writeCount(forKey: rateKey)
        for _ in 0..<100 { coordinator.setRate(1.25) }
        XCTAssertEqual(defaults.writeCount(forKey: rateKey) - baseline, 1)
        for _ in 0..<100 { player.reportRate(1.25) }
        // Enqueue a different final value after repeated callbacks. Once it has
        // been consumed, every earlier callback has also observed that value.
        player.reportRate(1.5)
        await waitForTransport("External rate should update the preference") { coordinator.preferredRate == 1.5 }
        XCTAssertEqual(defaults.writeCount(forKey: rateKey) - baseline, 2)
        XCTAssertEqual(defaults.float(forKey: rateKey), 1.5)
        let fitKey = "treasure.native.playback.fitToFill"
        let fitBaseline = defaults.writeCount(forKey: fitKey)
        for _ in 0..<100 { coordinator.fitToFill = true }
        XCTAssertEqual(defaults.writeCount(forKey: fitKey) - fitBaseline, 1)
    }

    func testAdjacentAvailabilityPreservesManualQueueTargetSemantics() {
        let parts = [part("p3", position: 3), part("missing", position: 2, playable: false), part("p1", position: 1)]
        // Include a video with no known playable parts: availability must still
        // allow the existing navigation path to hydrate and skip it as needed.
        let videos = [video("a", parts: parts), video("unhydrated"), video("z")]
        let partIDs: [String?] = [nil, "p1", "p3", "missing", "unknown"]
        let videoIDs: [String?] = [nil, "a", "unhydrated", "z", "outside"]
        for partID in partIDs {
            for videoID in videoIDs {
                for direction in [-1, 1] {
                    let target = PlaybackQueue.target(parts: parts, partId: partID, videos: videos.map(\.id),
                                                      videoId: videoID, direction: direction)
                    XCTAssertEqual(PlaybackCoordinator.hasAdjacentItem(parts: parts, partID: partID, videos: videos,
                                                                       videoID: videoID, direction: direction), target != .stop)
                }
            }
        }
    }

    private func nowPlayingState() -> PlaybackNowPlayingState {
        PlaybackNowPlayingState(videoID: "v", partID: "p", title: "Title", artist: "Creator", album: "P1",
                                duration: 120, rate: 1, preferredRate: 1, artwork: nil, hasNext: true, hasPrevious: false)
    }

    private nonisolated static func emitRateKVO(_ player: AVPlayer) {
        player.willChangeValue(forKey: "rate")
        player.didChangeValue(forKey: "rate")
    }

    private nonisolated static func emitPausedKVO(_ player: AVPlayer) {
        player.willChangeValue(forKey: "timeControlStatus")
        player.didChangeValue(forKey: "timeControlStatus")
    }

    private func withCoordinator(player: AVPlayer = AVPlayer(), authenticated: Bool = false,
                                 _ operation: @MainActor (PlaybackCoordinator) async -> Void) async {
        let name = "TreasureQueueCoordinatorTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: name)!
        defer { defaults.removePersistentDomain(forName: name) }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [QueueTestURLProtocol.self]
        let api = APIClient(baseURL: URL(string: "https://queue.example.test")!, defaults: defaults,
                            sessionConfiguration: configuration, persistSession: false)
        if authenticated {
            do { try await api.login(username: "fixture", password: "fixture") }
            catch { XCTFail("Offline authentication fixture failed: \(error)"); return }
        }
        let coordinator = PlaybackCoordinator(api: api, defaults: defaults, player: player)
        defer { coordinator.stop(clearQueue: true, saveProgress: false) }
        await operation(coordinator)
    }
}

/// Holds seek completions without loading media, so KVO and gestures can be
/// delivered during the actual coordinator's asynchronous replay transition.
private final class ReplayTestPlayer: AVPlayer, @unchecked Sendable {
    // AVPlayer's Objective-C callbacks are nonisolated. Only this immutable,
    // Sendable reference crosses that boundary; all mutable state owns its lock.
    private nonisolated let testState = ReplayTestPlayerState()
    var pendingTimes: [Double] { testState.pendingTimes }
    var playedRates: [Float] { testState.playedRates }
    var seekCancellationCount: Int { testState.cancellationCount }
    var completeCancelledSeeksImmediately: Bool {
        get { testState.completesCancelledSeeksImmediately }
        set { testState.setCompletesCancelledSeeksImmediately(newValue) }
    }
    func makeCancellationItem() -> AVPlayerItem { CancellationTestPlayerItem(state: testState) }
    override var timeControlStatus: AVPlayer.TimeControlStatus { testState.timeControlStatus }
    override var rate: Float {
        get { testState.rate }
        set { testState.setRate(newValue) }
    }
    func reportRate(_ rate: Float) {
        willChangeValue(forKey: "rate")
        testState.setRate(rate)
        didChangeValue(forKey: "rate")
    }
    func reportTimeControlStatus(_ status: AVPlayer.TimeControlStatus) {
        willChangeValue(forKey: "timeControlStatus")
        testState.setTimeControlStatus(status)
        didChangeValue(forKey: "timeControlStatus")
    }
    override func currentTime() -> CMTime { testState.currentTime }
    override func pause() { testState.setRate(0) }
    override func addPeriodicTimeObserver(forInterval interval: CMTime, queue: DispatchQueue?,
                                          using block: @escaping @Sendable (CMTime) -> Void) -> Any {
        testState.setTimeObserver(block)
        return UUID()
    }
    override func removeTimeObserver(_ observer: Any) { testState.setTimeObserver(nil) }
    func reportPeriodicTime(_ seconds: Double) {
        testState.setPosition(CMTime(seconds: seconds, preferredTimescale: 600))
        testState.emitTimeObserver()
    }
    override func playImmediately(atRate rate: Float) { testState.recordPlayback(rate: rate) }
    override func seek(to time: CMTime, toleranceBefore: CMTime, toleranceAfter: CMTime) {
        testState.setPosition(time)
    }
    override func seek(to time: CMTime, toleranceBefore: CMTime, toleranceAfter: CMTime,
                       completionHandler: @escaping @Sendable (Bool) -> Void) {
        testState.appendSeek(time: time, completion: completionHandler)
    }
    func finishNextSeek(success: Bool = true) { testState.finishNextSeek(success: success) }
    func finishRemainingSeeks() { testState.finishRemainingSeeks() }
}

/// Independent of AVPlayer's inherited actor isolation. Every read and write is
/// synchronized; callbacks are removed under the lock and invoked after release.
private final class ReplayTestPlayerState: @unchecked Sendable {
    private struct Seek: Sendable {
        let time: CMTime
        let completion: @Sendable (Bool) -> Void
    }
    private let lock = NSLock()
    private var seeks: [Seek] = []
    private var position = CMTime.zero
    private var rates: [Float] = []
    private var reportedStatus: AVPlayer.TimeControlStatus = .paused
    private var reportedRate: Float = 0
    private var timeObserver: (@Sendable (CMTime) -> Void)?
    private var cancellations = 0
    private var immediateCancellationCompletion = false

    var pendingTimes: [Double] { lock.withLock { seeks.map { $0.time.seconds } } }
    var playedRates: [Float] { lock.withLock { rates } }
    var cancellationCount: Int { lock.withLock { cancellations } }
    var completesCancelledSeeksImmediately: Bool { lock.withLock { immediateCancellationCompletion } }
    func setCompletesCancelledSeeksImmediately(_ value: Bool) { lock.withLock { immediateCancellationCompletion = value } }
    func cancelPendingSeeks() {
        let pending: [Seek] = lock.withLock {
            cancellations += 1
            guard immediateCancellationCompletion else { return [] }
            let pending = seeks
            seeks.removeAll()
            return pending
        }
        for seek in pending { seek.completion(false) }
    }
    var timeControlStatus: AVPlayer.TimeControlStatus { lock.withLock { reportedStatus } }
    var rate: Float { lock.withLock { reportedRate } }
    func setRate(_ rate: Float) { lock.withLock { reportedRate = rate } }
    var currentTime: CMTime { lock.withLock { position } }
    func setTimeControlStatus(_ status: AVPlayer.TimeControlStatus) { lock.withLock { reportedStatus = status } }
    func recordPlayback(rate: Float) { lock.withLock { rates.append(rate); reportedRate = rate } }
    func setTimeObserver(_ callback: (@Sendable (CMTime) -> Void)?) { lock.withLock { timeObserver = callback } }
    func emitTimeObserver() {
        let (callback, time) = lock.withLock { (timeObserver, position) }
        callback?(time)
    }
    func setPosition(_ time: CMTime) { lock.withLock { position = time } }
    func appendSeek(time: CMTime, completion: @escaping @Sendable (Bool) -> Void) {
        lock.withLock { seeks.append(Seek(time: time, completion: completion)) }
    }
    func finishNextSeek(success: Bool) {
        let seek: Seek? = lock.withLock {
            guard !seeks.isEmpty else { return nil }
            let seek = seeks.removeFirst()
            if success { position = seek.time }
            return seek
        }
        seek?.completion(success)
    }
    func finishRemainingSeeks() {
        let pending = lock.withLock { let pending = seeks; seeks.removeAll(); return pending }
        for seek in pending { seek.completion(false) }
    }
}

/// Delivers cancellation back through the fake player's real seek completion,
/// optionally holding it to reproduce delayed AVFoundation callbacks.
private final class CancellationTestPlayerItem: AVPlayerItem, @unchecked Sendable {
    private nonisolated let state: ReplayTestPlayerState
    init(state: ReplayTestPlayerState) {
        self.state = state
        super.init(asset: AVMutableComposition(), automaticallyLoadedAssetKeys: nil)
    }
    override func cancelPendingSeeks() { state.cancelPendingSeeks() }
}

/// Captures actual API request payloads while URLProtocol handles all traffic
/// locally. HTTP body streams are consumed only by this protocol fixture.
private final class PlaybackProgressRequestStore: @unchecked Sendable {
    private let lock = NSLock()
    private var values: [Double] = []
    var positions: [Double] { lock.withLock { values } }

    func record(_ request: URLRequest) {
        var body = request.httpBody ?? Data()
        if body.isEmpty, let stream = request.httpBodyStream {
            stream.open()
            defer { stream.close() }
            var buffer = [UInt8](repeating: 0, count: 1024)
            while stream.hasBytesAvailable {
                let count = stream.read(&buffer, maxLength: buffer.count)
                guard count > 0 else { break }
                body.append(contentsOf: buffer.prefix(count))
            }
        }
        guard let json = try? JSONSerialization.jsonObject(with: body) as? [String: Any],
              let position = json["position"] as? NSNumber else { return }
        lock.withLock { values.append(position.doubleValue) }
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

/// Count real coordinator preference writes without relying on notification
/// coalescing or touching any server. The override's mutable state owns a lock.
private final class CountingPlaybackDefaults: UserDefaults, @unchecked Sendable {
    private let writes = PlaybackPreferenceWriteCounts()
    override func set(_ value: Float, forKey defaultName: String) {
        writes.record(defaultName)
        super.set(value, forKey: defaultName)
    }
    override func set(_ value: Bool, forKey defaultName: String) {
        writes.record(defaultName)
        super.set(value, forKey: defaultName)
    }
    func writeCount(forKey key: String) -> Int { writes.count(key) }
}

private final class PlaybackPreferenceWriteCounts: @unchecked Sendable {
    private let lock = NSLock()
    private var values: [String: Int] = [:]
    func record(_ key: String) { lock.withLock { values[key, default: 0] += 1 } }
    func count(_ key: String) -> Int { lock.withLock { values[key, default: 0] } }
}
