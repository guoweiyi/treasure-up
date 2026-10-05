import Foundation

// Model keys intentionally use Url / Id, matching JSONDecoder.convertFromSnakeCase.
// Optional and omitted fields are normal for compact cards and older server versions.

struct ArchiveUser: Codable, Hashable, Sendable, Identifiable {
    var id: String
    var username: String
    var role: String
    var disabled: Bool

    init(id: String = "", username: String = "", role: String = "reader", disabled: Bool = false) {
        self.id = id
        self.username = username
        self.role = role
        self.disabled = disabled
    }

    enum CodingKeys: String, CodingKey {
        case id, username, role, disabled
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(String.self, forKey: .id) ?? ""
        username = try values.decodeIfPresent(String.self, forKey: .username) ?? ""
        role = try values.decodeIfPresent(String.self, forKey: .role) ?? "reader"
        disabled = try values.decodeIfPresent(Bool.self, forKey: .disabled) ?? false
    }
    var name: String { username }
    var canEdit: Bool { role == "admin" || role == "editor" }
    var isAdmin: Bool { role == "admin" }
}

struct ArchiveCreator: Codable, Hashable, Sendable, Identifiable {
    var id: String
    var uid: String?
    var name: String
    var sourceName: String?
    var avatarUrl: String?
    var description: String?
    var notes: String?
    var savedCount: Int
    var tags: [String]
    var role: String?
    var roleTitle: String?
    var alias: String?
    var descriptionOverride: String?
    var sourceDescription: String?

    init(id: String = "", uid: String? = nil, name: String = "未知 UP 主", sourceName: String? = nil, avatarUrl: String? = nil, description: String? = nil, notes: String? = nil, savedCount: Int = 0, tags: [String] = [], role: String? = nil, roleTitle: String? = nil, alias: String? = nil, descriptionOverride: String? = nil, sourceDescription: String? = nil) {
        self.id = id
        self.uid = uid
        self.name = name
        self.sourceName = sourceName
        self.avatarUrl = avatarUrl
        self.description = description
        self.notes = notes
        self.savedCount = savedCount
        self.tags = tags
        self.role = role
        self.roleTitle = roleTitle
        self.alias = alias
        self.descriptionOverride = descriptionOverride
        self.sourceDescription = sourceDescription
    }

    enum CodingKeys: String, CodingKey {
        case id, uid, name, sourceName, avatarUrl, description, notes, savedCount, tags, role, roleTitle, alias, descriptionOverride, sourceDescription
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(String.self, forKey: .id) ?? ""
        uid = try values.decodeIfPresent(String.self, forKey: .uid)
        name = try values.decodeIfPresent(String.self, forKey: .name) ?? "未知 UP 主"
        sourceName = try values.decodeIfPresent(String.self, forKey: .sourceName)
        avatarUrl = try values.decodeIfPresent(String.self, forKey: .avatarUrl)
        description = try values.decodeIfPresent(String.self, forKey: .description)
        notes = try values.decodeIfPresent(String.self, forKey: .notes)
        savedCount = try values.decodeIfPresent(Int.self, forKey: .savedCount) ?? 0
        tags = try values.decodeIfPresent([String].self, forKey: .tags) ?? []
        role = try values.decodeIfPresent(String.self, forKey: .role)
        roleTitle = try values.decodeIfPresent(String.self, forKey: .roleTitle)
        alias = try values.decodeIfPresent(String.self, forKey: .alias)
        descriptionOverride = try values.decodeIfPresent(String.self, forKey: .descriptionOverride)
        sourceDescription = try values.decodeIfPresent(String.self, forKey: .sourceDescription)
    }
}

struct ArchiveCollection: Codable, Hashable, Sendable, Identifiable {
    var id: String
    var title: String
    var kind: String
    var sourceId: String
    var savedCount: Int
    var enabled: Bool
    var coverUrl: String?

    init(id: String = "", title: String = "未命名收藏夹", kind: String = "favorite", sourceId: String = "", savedCount: Int = 0, enabled: Bool = false, coverUrl: String? = nil) {
        self.id = id
        self.title = title
        self.kind = kind
        self.sourceId = sourceId
        self.savedCount = savedCount
        self.enabled = enabled
        self.coverUrl = coverUrl
    }

    enum CodingKeys: String, CodingKey {
        case id, title, kind, sourceId, savedCount, enabled, coverUrl
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(String.self, forKey: .id) ?? ""
        title = try values.decodeIfPresent(String.self, forKey: .title) ?? "未命名收藏夹"
        kind = try values.decodeIfPresent(String.self, forKey: .kind) ?? "favorite"
        sourceId = try values.decodeIfPresent(String.self, forKey: .sourceId) ?? ""
        savedCount = try values.decodeIfPresent(Int.self, forKey: .savedCount) ?? 0
        enabled = try values.decodeIfPresent(Bool.self, forKey: .enabled) ?? false
        coverUrl = try values.decodeIfPresent(String.self, forKey: .coverUrl)
    }
}

struct ArchiveVideo: Codable, Hashable, Sendable, Identifiable {
    var id: String
    var bvid: String
    var title: String
    var sourceTitle: String?
    var description: String
    var duration: Double
    var coverUrl: String?
    var creators: [ArchiveCreator]
    var tags: [String]
    var starred: Bool
    var partsCount: Int
    var playable: Bool
    var captureStatus: String
    var createdAt: String
    var publishedAt: String?
    var notes: String?
    var sourceState: String?
    var parts: [VideoPart]
    var mediaProperties: [String: MediaProperties]
    var stats: VideoStats?
    var captureRuns: [JSONRow]
    var playlistItem: PlaylistMembership?
    var contentFeatures: ContentFeatures?
    var titleOverride: String?
    var descriptionOverride: String?
    var sourceDescription: String?
    var ingestState: JSONRow

    init(id: String = "", bvid: String = "", title: String = "未命名视频", sourceTitle: String? = nil, description: String = "", duration: Double = 0, coverUrl: String? = nil, creators: [ArchiveCreator] = [], tags: [String] = [], starred: Bool = false, partsCount: Int = 0, playable: Bool = false, captureStatus: String = "unknown", createdAt: String = "", publishedAt: String? = nil, notes: String? = nil, sourceState: String? = nil, parts: [VideoPart] = [], mediaProperties: [String: MediaProperties] = [:], stats: VideoStats? = nil, captureRuns: [JSONRow] = [], playlistItem: PlaylistMembership? = nil, contentFeatures: ContentFeatures? = nil, titleOverride: String? = nil, descriptionOverride: String? = nil, sourceDescription: String? = nil, ingestState: JSONRow = [:]) {
        self.id = id
        self.bvid = bvid
        self.title = title
        self.sourceTitle = sourceTitle
        self.description = description
        self.duration = duration
        self.coverUrl = coverUrl
        self.creators = creators
        self.tags = tags
        self.starred = starred
        self.partsCount = partsCount
        self.playable = playable
        self.captureStatus = captureStatus
        self.createdAt = createdAt
        self.publishedAt = publishedAt
        self.notes = notes
        self.sourceState = sourceState
        self.parts = parts
        self.mediaProperties = mediaProperties
        self.stats = stats
        self.captureRuns = captureRuns
        self.playlistItem = playlistItem
        self.contentFeatures = contentFeatures
        self.titleOverride = titleOverride
        self.descriptionOverride = descriptionOverride
        self.sourceDescription = sourceDescription
        self.ingestState = ingestState
    }

    enum CodingKeys: String, CodingKey {
        case id, bvid, title, sourceTitle, description, duration, coverUrl, creators, tags, starred, partsCount, playable, captureStatus, createdAt, publishedAt, notes, sourceState, parts, mediaProperties, stats, captureRuns, playlistItem, contentFeatures, titleOverride, descriptionOverride, sourceDescription, ingestState
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(String.self, forKey: .id) ?? ""
        bvid = try values.decodeIfPresent(String.self, forKey: .bvid) ?? ""
        title = try values.decodeIfPresent(String.self, forKey: .title) ?? "未命名视频"
        sourceTitle = try values.decodeIfPresent(String.self, forKey: .sourceTitle)
        description = try values.decodeIfPresent(String.self, forKey: .description) ?? ""
        duration = try values.decodeIfPresent(Double.self, forKey: .duration) ?? 0
        coverUrl = try values.decodeIfPresent(String.self, forKey: .coverUrl)
        creators = try values.decodeIfPresent([ArchiveCreator].self, forKey: .creators) ?? []
        tags = try values.decodeIfPresent([String].self, forKey: .tags) ?? []
        starred = try values.decodeIfPresent(Bool.self, forKey: .starred) ?? false
        partsCount = try values.decodeIfPresent(Int.self, forKey: .partsCount) ?? 0
        playable = try values.decodeIfPresent(Bool.self, forKey: .playable) ?? false
        captureStatus = try values.decodeIfPresent(String.self, forKey: .captureStatus) ?? "unknown"
        createdAt = try values.decodeIfPresent(String.self, forKey: .createdAt) ?? ""
        publishedAt = try values.decodeIfPresent(String.self, forKey: .publishedAt)
        notes = try values.decodeIfPresent(String.self, forKey: .notes)
        sourceState = try values.decodeIfPresent(String.self, forKey: .sourceState)
        parts = try values.decodeIfPresent([VideoPart].self, forKey: .parts) ?? []
        mediaProperties = try values.decodeIfPresent([String: MediaProperties].self, forKey: .mediaProperties) ?? [:]
        stats = try values.decodeIfPresent(VideoStats.self, forKey: .stats)
        captureRuns = try values.decodeIfPresent([JSONRow].self, forKey: .captureRuns) ?? []
        playlistItem = try values.decodeIfPresent(PlaylistMembership.self, forKey: .playlistItem)
        contentFeatures = try values.decodeIfPresent(ContentFeatures.self, forKey: .contentFeatures)
        titleOverride = try values.decodeIfPresent(String.self, forKey: .titleOverride)
        descriptionOverride = try values.decodeIfPresent(String.self, forKey: .descriptionOverride)
        sourceDescription = try values.decodeIfPresent(String.self, forKey: .sourceDescription)
        ingestState = try values.decodeIfPresent(JSONRow.self, forKey: .ingestState) ?? [:]
    }
    var creatorNames: String { creators.map(\.name).joined(separator: "、") }
    var hasDolbyVision: Bool { contentFeatures?.dolbyVision == true || mediaProperties.values.contains { $0.dolbyVision == true } }
    var hasDolbyAtmos: Bool { contentFeatures?.dolbyAtmos == true || mediaProperties.values.contains { $0.dolbyAtmos == true } }
}

struct VideoPart: Codable, Hashable, Sendable, Identifiable {
    var id: String
    var cid: String
    var position: Int
    var title: String
    var duration: Double
    var variants: [MediaVariant]

    init(id: String = "", cid: String = "", position: Int = 0, title: String = "", duration: Double = 0, variants: [MediaVariant] = []) {
        self.id = id
        self.cid = cid
        self.position = position
        self.title = title
        self.duration = duration
        self.variants = variants
    }

    enum CodingKeys: String, CodingKey {
        case id, cid, position, title, duration, variants
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(String.self, forKey: .id) ?? ""
        cid = try values.decodeIfPresent(String.self, forKey: .cid) ?? ""
        position = try values.decodeIfPresent(Int.self, forKey: .position) ?? 0
        title = try values.decodeIfPresent(String.self, forKey: .title) ?? ""
        duration = try values.decodeIfPresent(Double.self, forKey: .duration) ?? 0
        variants = try values.decodeIfPresent([MediaVariant].self, forKey: .variants) ?? []
    }
}

struct MediaVariant: Codable, Hashable, Sendable, Identifiable {
    var id: String
    var quality: JSONValue
    var kind: String
    var width: Int?
    var height: Int?
    var videoCodec: String?
    var audioCodec: String?
    var metadata: MediaProperties?

    init(id: String = "", quality: JSONValue = .null, kind: String = "archive", width: Int? = nil, height: Int? = nil, videoCodec: String? = nil, audioCodec: String? = nil, metadata: MediaProperties? = nil) {
        self.id = id
        self.quality = quality
        self.kind = kind
        self.width = width
        self.height = height
        self.videoCodec = videoCodec
        self.audioCodec = audioCodec
        self.metadata = metadata
    }

    enum CodingKeys: String, CodingKey {
        case id, quality, kind, width, height, videoCodec, audioCodec, metadata
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(String.self, forKey: .id) ?? ""
        quality = try values.decodeIfPresent(JSONValue.self, forKey: .quality) ?? .null
        kind = try values.decodeIfPresent(String.self, forKey: .kind) ?? "archive"
        width = try values.decodeIfPresent(Int.self, forKey: .width)
        height = try values.decodeIfPresent(Int.self, forKey: .height)
        videoCodec = try values.decodeIfPresent(String.self, forKey: .videoCodec)
        audioCodec = try values.decodeIfPresent(String.self, forKey: .audioCodec)
        metadata = try values.decodeIfPresent(MediaProperties.self, forKey: .metadata)
    }
    var qualityLabel: String { quality.stringValue ?? "原画" }
}

struct MediaProperties: Codable, Hashable, Sendable {
    var colorTransfer: String?
    var primaries: String?
    var pixFmt: String?
    var hdr: Bool?
    var wideGamut: Bool?
    var dolbyVision: Bool?
    var dolbyAtmos: Bool?
    var compatibility: String?
    var compatibilityMode: String?
    var sourceVariantId: String?
    var videoStreamCopy: Bool?
    var audioTranscoded: Bool?
    var audioCodec: String?
    var videoCodec: String?
    var audioChannels: Int?
    var width: Int?
    var height: Int?
    var mimeType: String?
    var segmentCount: Int?

    init(colorTransfer: String? = nil, primaries: String? = nil, pixFmt: String? = nil, hdr: Bool? = nil, wideGamut: Bool? = nil, dolbyVision: Bool? = nil, dolbyAtmos: Bool? = nil, compatibility: String? = nil, compatibilityMode: String? = nil, sourceVariantId: String? = nil, videoStreamCopy: Bool? = nil, audioTranscoded: Bool? = nil, audioCodec: String? = nil, videoCodec: String? = nil, audioChannels: Int? = nil, width: Int? = nil, height: Int? = nil, mimeType: String? = nil, segmentCount: Int? = nil) {
        self.colorTransfer = colorTransfer
        self.primaries = primaries
        self.pixFmt = pixFmt
        self.hdr = hdr
        self.wideGamut = wideGamut
        self.dolbyVision = dolbyVision
        self.dolbyAtmos = dolbyAtmos
        self.compatibility = compatibility
        self.compatibilityMode = compatibilityMode
        self.sourceVariantId = sourceVariantId
        self.videoStreamCopy = videoStreamCopy
        self.audioTranscoded = audioTranscoded
        self.audioCodec = audioCodec
        self.videoCodec = videoCodec
        self.audioChannels = audioChannels
        self.width = width
        self.height = height
        self.mimeType = mimeType
        self.segmentCount = segmentCount
    }

    enum CodingKeys: String, CodingKey {
        case colorTransfer, primaries, pixFmt, hdr, wideGamut, dolbyVision, dolbyAtmos, compatibility, compatibilityMode, sourceVariantId, videoStreamCopy, audioTranscoded, audioCodec, videoCodec, audioChannels, width, height, mimeType, segmentCount
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        colorTransfer = try values.decodeIfPresent(String.self, forKey: .colorTransfer)
        primaries = try values.decodeIfPresent(String.self, forKey: .primaries)
        pixFmt = try values.decodeIfPresent(String.self, forKey: .pixFmt)
        hdr = try values.decodeIfPresent(Bool.self, forKey: .hdr)
        wideGamut = try values.decodeIfPresent(Bool.self, forKey: .wideGamut)
        dolbyVision = try values.decodeIfPresent(Bool.self, forKey: .dolbyVision)
        dolbyAtmos = try values.decodeIfPresent(Bool.self, forKey: .dolbyAtmos)
        compatibility = try values.decodeIfPresent(String.self, forKey: .compatibility)
        compatibilityMode = try values.decodeIfPresent(String.self, forKey: .compatibilityMode)
        sourceVariantId = try values.decodeIfPresent(String.self, forKey: .sourceVariantId)
        videoStreamCopy = try values.decodeIfPresent(Bool.self, forKey: .videoStreamCopy)
        audioTranscoded = try values.decodeIfPresent(Bool.self, forKey: .audioTranscoded)
        audioCodec = try values.decodeIfPresent(String.self, forKey: .audioCodec)
        videoCodec = try values.decodeIfPresent(String.self, forKey: .videoCodec)
        audioChannels = try values.decodeIfPresent(Int.self, forKey: .audioChannels)
        width = try values.decodeIfPresent(Int.self, forKey: .width)
        height = try values.decodeIfPresent(Int.self, forKey: .height)
        mimeType = try values.decodeIfPresent(String.self, forKey: .mimeType)
        segmentCount = try values.decodeIfPresent(Int.self, forKey: .segmentCount)
    }
}

struct VideoStats: Codable, Hashable, Sendable {
    var view: Int?
    var like: Int?
    var coin: Int?
    var favorite: Int?
    var share: Int?
    var reply: Int?
    var danmaku: Int?
    var observedAt: String?

    init(view: Int? = nil, like: Int? = nil, coin: Int? = nil, favorite: Int? = nil, share: Int? = nil, reply: Int? = nil, danmaku: Int? = nil, observedAt: String? = nil) {
        self.view = view
        self.like = like
        self.coin = coin
        self.favorite = favorite
        self.share = share
        self.reply = reply
        self.danmaku = danmaku
        self.observedAt = observedAt
    }

    enum CodingKeys: String, CodingKey {
        case view, like, coin, favorite, share, reply, danmaku, observedAt
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        view = try values.decodeIfPresent(Int.self, forKey: .view)
        like = try values.decodeIfPresent(Int.self, forKey: .like)
        coin = try values.decodeIfPresent(Int.self, forKey: .coin)
        favorite = try values.decodeIfPresent(Int.self, forKey: .favorite)
        share = try values.decodeIfPresent(Int.self, forKey: .share)
        reply = try values.decodeIfPresent(Int.self, forKey: .reply)
        danmaku = try values.decodeIfPresent(Int.self, forKey: .danmaku)
        observedAt = try values.decodeIfPresent(String.self, forKey: .observedAt)
    }
}

struct PlaybackSession: Codable, Hashable, Sendable {
    var id: String?
    var sessionId: String?
    var assetId: String
    var variantId: String
    var sourceVariantId: String?
    var `protocol`: String?
    var routes: [PlaybackRoute]
    var selectedRouteId: String?
    var media: MediaProperties?
    var loudness: Loudness?
    var url: String
    var expiresAt: String?
    var danmakuUrl: String?
    var subtitles: [SubtitleTrack]

    init(id: String? = nil, sessionId: String? = nil, assetId: String = "", variantId: String = "", sourceVariantId: String? = nil, `protocol`: String? = nil, routes: [PlaybackRoute] = [], selectedRouteId: String? = nil, media: MediaProperties? = nil, loudness: Loudness? = nil, url: String = "", expiresAt: String? = nil, danmakuUrl: String? = nil, subtitles: [SubtitleTrack] = []) {
        self.id = id
        self.sessionId = sessionId
        self.assetId = assetId
        self.variantId = variantId
        self.sourceVariantId = sourceVariantId
        self.`protocol` = `protocol`
        self.routes = routes
        self.selectedRouteId = selectedRouteId
        self.media = media
        self.loudness = loudness
        self.url = url
        self.expiresAt = expiresAt
        self.danmakuUrl = danmakuUrl
        self.subtitles = subtitles
    }

    enum CodingKeys: String, CodingKey {
        case id, sessionId, assetId, variantId, sourceVariantId, `protocol`, routes, selectedRouteId, media, loudness, url, expiresAt, danmakuUrl, subtitles
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(String.self, forKey: .id)
        sessionId = try values.decodeIfPresent(String.self, forKey: .sessionId)
        assetId = try values.decodeIfPresent(String.self, forKey: .assetId) ?? ""
        variantId = try values.decodeIfPresent(String.self, forKey: .variantId) ?? ""
        sourceVariantId = try values.decodeIfPresent(String.self, forKey: .sourceVariantId)
        `protocol` = try values.decodeIfPresent(String.self, forKey: .protocol)
        routes = try values.decodeIfPresent([PlaybackRoute].self, forKey: .routes) ?? []
        selectedRouteId = try values.decodeIfPresent(String.self, forKey: .selectedRouteId)
        media = try values.decodeIfPresent(MediaProperties.self, forKey: .media)
        loudness = try values.decodeIfPresent(Loudness.self, forKey: .loudness)
        url = try values.decodeIfPresent(String.self, forKey: .url) ?? ""
        expiresAt = try values.decodeIfPresent(String.self, forKey: .expiresAt)
        danmakuUrl = try values.decodeIfPresent(String.self, forKey: .danmakuUrl)
        subtitles = try values.decodeIfPresent([SubtitleTrack].self, forKey: .subtitles) ?? []
    }
}

struct PlaybackRoute: Codable, Hashable, Sendable, Identifiable {
    var id: String
    var name: String
    var status: String
    var latencyMs: Double?
    var throughputBps: Double?
    var measurementScope: String?
    var measuredAt: String?
    var probeUrl: String?
    var probeBytes: Int?

    init(id: String = "", name: String = "默认线路", status: String = "unmeasured", latencyMs: Double? = nil, throughputBps: Double? = nil, measurementScope: String? = nil, measuredAt: String? = nil, probeUrl: String? = nil, probeBytes: Int? = nil) {
        self.id = id
        self.name = name
        self.status = status
        self.latencyMs = latencyMs
        self.throughputBps = throughputBps
        self.measurementScope = measurementScope
        self.measuredAt = measuredAt
        self.probeUrl = probeUrl
        self.probeBytes = probeBytes
    }

    enum CodingKeys: String, CodingKey {
        case id, name, status, latencyMs, throughputBps, measurementScope, measuredAt, probeUrl, probeBytes
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(String.self, forKey: .id) ?? ""
        name = try values.decodeIfPresent(String.self, forKey: .name) ?? "默认线路"
        status = try values.decodeIfPresent(String.self, forKey: .status) ?? "unmeasured"
        latencyMs = try values.decodeIfPresent(Double.self, forKey: .latencyMs)
        throughputBps = try values.decodeIfPresent(Double.self, forKey: .throughputBps)
        measurementScope = try values.decodeIfPresent(String.self, forKey: .measurementScope)
        measuredAt = try values.decodeIfPresent(String.self, forKey: .measuredAt)
        probeUrl = try values.decodeIfPresent(String.self, forKey: .probeUrl)
        probeBytes = try values.decodeIfPresent(Int.self, forKey: .probeBytes)
    }
}

struct Loudness: Codable, Hashable, Sendable {
    var status: String
    var gainDb: Double
    var gainLinear: Double
    var targetLufs: Double
    var inputLufs: Double?
    var truePeakDbfs: Double?
    var atmosBypass: Bool
    var reason: String?
    var analysisOnly: Bool

    init(status: String = "bypassed", gainDb: Double = 0, gainLinear: Double = 1, targetLufs: Double = -16, inputLufs: Double? = nil, truePeakDbfs: Double? = nil, atmosBypass: Bool = false, reason: String? = nil, analysisOnly: Bool = true) {
        self.status = status
        self.gainDb = gainDb
        self.gainLinear = gainLinear
        self.targetLufs = targetLufs
        self.inputLufs = inputLufs
        self.truePeakDbfs = truePeakDbfs
        self.atmosBypass = atmosBypass
        self.reason = reason
        self.analysisOnly = analysisOnly
    }

    enum CodingKeys: String, CodingKey {
        case status, gainDb, gainLinear, targetLufs, inputLufs, truePeakDbfs, atmosBypass, reason, analysisOnly
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        status = try values.decodeIfPresent(String.self, forKey: .status) ?? "bypassed"
        gainDb = try values.decodeIfPresent(Double.self, forKey: .gainDb) ?? 0
        gainLinear = try values.decodeIfPresent(Double.self, forKey: .gainLinear) ?? 1
        targetLufs = try values.decodeIfPresent(Double.self, forKey: .targetLufs) ?? -16
        inputLufs = try values.decodeIfPresent(Double.self, forKey: .inputLufs)
        truePeakDbfs = try values.decodeIfPresent(Double.self, forKey: .truePeakDbfs)
        atmosBypass = try values.decodeIfPresent(Bool.self, forKey: .atmosBypass) ?? false
        reason = try values.decodeIfPresent(String.self, forKey: .reason)
        analysisOnly = try values.decodeIfPresent(Bool.self, forKey: .analysisOnly) ?? true
    }
}

struct SubtitleTrack: Codable, Hashable, Sendable {
    var id: String?
    var url: String
    var label: String
    var language: String?
    var isAuto: Bool

    init(id: String? = nil, url: String = "", label: String = "字幕", language: String? = nil, isAuto: Bool = false) {
        self.id = id
        self.url = url
        self.label = label
        self.language = language
        self.isAuto = isAuto
    }

    enum CodingKeys: String, CodingKey {
        case id, url, label, language, isAuto
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(String.self, forKey: .id)
        url = try values.decodeIfPresent(String.self, forKey: .url) ?? ""
        label = try values.decodeIfPresent(String.self, forKey: .label) ?? "字幕"
        language = try values.decodeIfPresent(String.self, forKey: .language)
        isAuto = try values.decodeIfPresent(Bool.self, forKey: .isAuto) ?? false
    }
}

struct ArchiveComment: Codable, Hashable, Sendable, Identifiable {
    var id: String
    var rpid: String
    var rootRpid: String?
    var parentRpid: String?
    var content: String
    var author: CommentAuthor
    var postedAt: String
    var likeCount: Int
    var replyCount: Int
    var images: [JSONValue]
    var emotes: [CommentEmote]

    init(id: String = "", rpid: String = "", rootRpid: String? = nil, parentRpid: String? = nil, content: String = "", author: CommentAuthor = CommentAuthor(), postedAt: String = "", likeCount: Int = 0, replyCount: Int = 0, images: [JSONValue] = [], emotes: [CommentEmote] = []) {
        self.id = id
        self.rpid = rpid
        self.rootRpid = rootRpid
        self.parentRpid = parentRpid
        self.content = content
        self.author = author
        self.postedAt = postedAt
        self.likeCount = likeCount
        self.replyCount = replyCount
        self.images = images
        self.emotes = emotes
    }

    enum CodingKeys: String, CodingKey {
        case id, rpid, rootRpid, parentRpid, content, author, postedAt, likeCount, replyCount, images, emotes
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(String.self, forKey: .id) ?? ""
        rpid = try values.decodeIfPresent(String.self, forKey: .rpid) ?? ""
        rootRpid = try values.decodeIfPresent(String.self, forKey: .rootRpid)
        parentRpid = try values.decodeIfPresent(String.self, forKey: .parentRpid)
        content = try values.decodeIfPresent(String.self, forKey: .content) ?? ""
        author = try values.decodeIfPresent(CommentAuthor.self, forKey: .author) ?? CommentAuthor()
        postedAt = try values.decodeIfPresent(String.self, forKey: .postedAt) ?? ""
        likeCount = try values.decodeIfPresent(Int.self, forKey: .likeCount) ?? 0
        replyCount = try values.decodeIfPresent(Int.self, forKey: .replyCount) ?? 0
        images = try values.decodeIfPresent([JSONValue].self, forKey: .images) ?? []
        emotes = try values.decodeIfPresent([CommentEmote].self, forKey: .emotes) ?? []
    }
    var imageUrls: [String] { images.compactMap { $0.stringValue ?? $0["asset_url"]?.stringValue ?? $0["url"]?.stringValue } }
}

struct CommentAuthor: Codable, Hashable, Sendable {
    var uid: String?
    var name: String?
    var avatarUrl: String?
    var creatorId: String?

    init(uid: String? = nil, name: String? = nil, avatarUrl: String? = nil, creatorId: String? = nil) {
        self.uid = uid
        self.name = name
        self.avatarUrl = avatarUrl
        self.creatorId = creatorId
    }

    enum CodingKeys: String, CodingKey {
        case uid, name, avatarUrl, creatorId
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        uid = try values.decodeIfPresent(String.self, forKey: .uid)
        name = try values.decodeIfPresent(String.self, forKey: .name)
        avatarUrl = try values.decodeIfPresent(String.self, forKey: .avatarUrl)
        creatorId = try values.decodeIfPresent(String.self, forKey: .creatorId)
    }
}

struct CommentEmote: Codable, Hashable, Sendable {
    var text: String
    var assetUrl: String

    init(text: String = "", assetUrl: String = "") {
        self.text = text
        self.assetUrl = assetUrl
    }

    enum CodingKeys: String, CodingKey {
        case text, assetUrl
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        text = try values.decodeIfPresent(String.self, forKey: .text) ?? ""
        assetUrl = try values.decodeIfPresent(String.self, forKey: .assetUrl) ?? ""
    }
}

struct PersonalPlaylist: Codable, Hashable, Sendable, Identifiable {
    var id: String
    var name: String
    var description: String
    var kind: String
    var itemCount: Int
    var unwatchedCount: Int
    var createdAt: String?
    var updatedAt: String?

    init(id: String = "", name: String = "未命名片单", description: String = "", kind: String = "custom", itemCount: Int = 0, unwatchedCount: Int = 0, createdAt: String? = nil, updatedAt: String? = nil) {
        self.id = id
        self.name = name
        self.description = description
        self.kind = kind
        self.itemCount = itemCount
        self.unwatchedCount = unwatchedCount
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }

    enum CodingKeys: String, CodingKey {
        case id, name, description, kind, itemCount, unwatchedCount, createdAt, updatedAt
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(String.self, forKey: .id) ?? ""
        name = try values.decodeIfPresent(String.self, forKey: .name) ?? "未命名片单"
        description = try values.decodeIfPresent(String.self, forKey: .description) ?? ""
        kind = try values.decodeIfPresent(String.self, forKey: .kind) ?? "custom"
        itemCount = try values.decodeIfPresent(Int.self, forKey: .itemCount) ?? 0
        unwatchedCount = try values.decodeIfPresent(Int.self, forKey: .unwatchedCount) ?? 0
        createdAt = try values.decodeIfPresent(String.self, forKey: .createdAt)
        updatedAt = try values.decodeIfPresent(String.self, forKey: .updatedAt)
    }
}

struct PlaylistMembership: Codable, Hashable, Sendable {
    var note: String
    var watched: Bool
    var addedAt: String?
    var updatedAt: String?

    init(note: String = "", watched: Bool = false, addedAt: String? = nil, updatedAt: String? = nil) {
        self.note = note
        self.watched = watched
        self.addedAt = addedAt
        self.updatedAt = updatedAt
    }

    enum CodingKeys: String, CodingKey {
        case note, watched, addedAt, updatedAt
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        note = try values.decodeIfPresent(String.self, forKey: .note) ?? ""
        watched = try values.decodeIfPresent(Bool.self, forKey: .watched) ?? false
        addedAt = try values.decodeIfPresent(String.self, forKey: .addedAt)
        updatedAt = try values.decodeIfPresent(String.self, forKey: .updatedAt)
    }
}

struct ContentFeatures: Codable, Hashable, Sendable {
    var chargingExclusive: Bool
    var dolbyVision: Bool
    var dolbyAtmos: Bool

    init(chargingExclusive: Bool = false, dolbyVision: Bool = false, dolbyAtmos: Bool = false) {
        self.chargingExclusive = chargingExclusive
        self.dolbyVision = dolbyVision
        self.dolbyAtmos = dolbyAtmos
    }

    enum CodingKeys: String, CodingKey {
        case chargingExclusive, dolbyVision, dolbyAtmos
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        chargingExclusive = try values.decodeIfPresent(Bool.self, forKey: .chargingExclusive) ?? false
        dolbyVision = try values.decodeIfPresent(Bool.self, forKey: .dolbyVision) ?? false
        dolbyAtmos = try values.decodeIfPresent(Bool.self, forKey: .dolbyAtmos) ?? false
    }
}

struct WatchProgress: Codable, Hashable, Sendable {
    var position: Double
    var duration: Double

    init(position: Double = 0, duration: Double = 0) {
        self.position = position
        self.duration = duration
    }

    enum CodingKeys: String, CodingKey {
        case position, duration
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        position = try values.decodeIfPresent(Double.self, forKey: .position) ?? 0
        duration = try values.decodeIfPresent(Double.self, forKey: .duration) ?? 0
    }
}

struct LibraryStats: Codable, Hashable, Sendable {
    var videos: Int
    var creators: Int
    var collections: Int
    var assetsBytes: Double
    var jobsPending: Int

    init(videos: Int = 0, creators: Int = 0, collections: Int = 0, assetsBytes: Double = 0, jobsPending: Int = 0) {
        self.videos = videos
        self.creators = creators
        self.collections = collections
        self.assetsBytes = assetsBytes
        self.jobsPending = jobsPending
    }

    enum CodingKeys: String, CodingKey {
        case videos, creators, collections, assetsBytes, jobsPending
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        videos = try values.decodeIfPresent(Int.self, forKey: .videos) ?? 0
        creators = try values.decodeIfPresent(Int.self, forKey: .creators) ?? 0
        collections = try values.decodeIfPresent(Int.self, forKey: .collections) ?? 0
        assetsBytes = try values.decodeIfPresent(Double.self, forKey: .assetsBytes) ?? 0
        jobsPending = try values.decodeIfPresent(Int.self, forKey: .jobsPending) ?? 0
    }
}

struct Page<T: Decodable>: Decodable {
    var items: [T]
    var total: Int
    var page: Int
    var pageSize: Int
    var scopeTitle: String?
    var hasMore: Bool { page * pageSize < total }

    init(items: [T] = [], total: Int = 0, page: Int = 1, pageSize: Int = 24, scopeTitle: String? = nil) {
        self.items = items
        self.total = total
        self.page = page
        self.pageSize = pageSize
        self.scopeTitle = scopeTitle
    }

    enum CodingKeys: String, CodingKey { case items, total, page, pageSize, scopeTitle }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        items = try values.decodeIfPresent([T].self, forKey: .items) ?? []
        total = try values.decodeIfPresent(Int.self, forKey: .total) ?? items.count
        page = try values.decodeIfPresent(Int.self, forKey: .page) ?? 1
        pageSize = max(1, try values.decodeIfPresent(Int.self, forKey: .pageSize) ?? max(items.count, 24))
        scopeTitle = try values.decodeIfPresent(String.self, forKey: .scopeTitle)
    }
}

extension Page: Sendable where T: Sendable {}
extension Page: Equatable where T: Equatable {}
extension Page: Hashable where T: Hashable {}
