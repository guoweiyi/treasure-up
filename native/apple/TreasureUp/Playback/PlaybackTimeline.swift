import Foundation

/// Pure timeline rules shared by resume, subtitles and the native overlay.
enum PlaybackTimeline {
    static func resumePosition(_ position: Double, duration: Double) -> Double {
        guard position.isFinite, duration.isFinite, position > 0, duration > 3,
              position < duration - 3 else { return 0 }
        return position
    }

    static func timestamp(_ value: String) -> Double? {
        let fields = value.replacingOccurrences(of: ",", with: ".").split(separator: ":")
        guard fields.count == 2 || fields.count == 3,
              let seconds = Double(fields.last!), seconds >= 0, seconds < 60,
              let minutes = Double(fields[fields.count - 2]), minutes >= 0, minutes < 60 else { return nil }
        let hours = fields.count == 3 ? Double(fields[0]) : 0
        guard let hours, hours.isFinite, hours >= 0 else { return nil }
        return hours * 3_600 + minutes * 60 + seconds
    }

    static func subtitles(_ text: String) -> [NativeSubtitleCue] {
        let normalized = text.replacingOccurrences(of: "\r\n", with: "\n").replacingOccurrences(of: "\r", with: "\n")
        return normalized.components(separatedBy: "\n\n").compactMap { block in
            let lines = block.components(separatedBy: "\n")
            guard let index = lines.firstIndex(where: { $0.contains("-->") }) else { return nil }
            let endpoints = lines[index].components(separatedBy: "-->")
            guard endpoints.count == 2,
                  let start = timestamp(endpoints[0].trimmingCharacters(in: .whitespaces)),
                  let endToken = endpoints[1].split(whereSeparator: \.isWhitespace).first,
                  let end = timestamp(String(endToken)), end > start else { return nil }
            let content = lines.dropFirst(index + 1).joined(separator: "\n")
                .replacingOccurrences(of: "<[^>]*>", with: "", options: .regularExpression)
                .replacingOccurrences(of: "&lt;", with: "<")
                .replacingOccurrences(of: "&gt;", with: ">")
                .replacingOccurrences(of: "&nbsp;", with: " ")
                .replacingOccurrences(of: "&quot;", with: "\"")
                .replacingOccurrences(of: "&amp;", with: "&")
            guard !content.isEmpty else { return nil }
            return NativeSubtitleCue(start: start, end: end, text: String(content.prefix(4_000)))
        }.sorted { $0.start < $1.start }
    }

    static func subtitleText(_ cues: [NativeSubtitleCue], at time: Double) -> String {
        guard time.isFinite else { return "" }
        var low = 0, high = cues.count
        while low < high {
            let middle = (low + high) / 2
            if cues[middle].start <= time { low = middle + 1 } else { high = middle }
        }
        // Most VTT tracks contain one cue; include nearby overlapping bilingual cues.
        var text = ""
        for cue in cues[max(0, low - 16)..<low] where cue.end > time {
            if !text.isEmpty { text.append("\n") }
            text.append(cue.text)
        }
        return text
    }

    static func scheduleDanmaku(_ rows: [NativeDanmakuCue]) -> [ScheduledDanmaku] {
        // A lane remains reserved for the whole cue, so long messages never collide.
        var available = Array(repeating: 0.0, count: 8)
        var result: [ScheduledDanmaku] = []
        for (index, cue) in rows.filter({ $0.time.isFinite && $0.time >= 0 && (0...2).contains($0.mode) && !$0.text.isEmpty })
            .sorted(by: { $0.time < $1.time }).prefix(100_000).enumerated() {
            let slots = cue.mode == 0 ? 0..<6 : (cue.mode == 1 ? 0..<2 : 6..<8)
            guard let slot = slots.first(where: { available[$0] <= cue.time }) else { continue }
            let duration = cue.mode == 0 ? 8.0 : 4.0
            available[slot] = cue.time + duration + 0.15
            result.append(ScheduledDanmaku(id: index, cue: cue, lane: cue.mode == 2 ? slot - 6 : slot, duration: duration))
        }
        return result
    }

    static func activeDanmaku(_ rows: [ScheduledDanmaku], at time: Double) -> ArraySlice<ScheduledDanmaku> {
        guard time.isFinite else { return [] }
        var lower = 0, upper = rows.count
        while lower < upper {
            let middle = (lower + upper) / 2
            if rows[middle].cue.time < time - 8 { lower = middle + 1 } else { upper = middle }
        }
        let start = lower
        upper = rows.count
        while lower < upper {
            let middle = (lower + upper) / 2
            if rows[middle].cue.time <= time { lower = middle + 1 } else { upper = middle }
        }
        return rows[start..<lower]
    }
}

struct NativeSubtitleCue: Sendable {
    let start: Double
    let end: Double
    let text: String
}

struct NativeDanmakuCue: Decodable, Sendable {
    let text: String
    let time: Double
    let color: UInt32
    let mode: Int

    enum CodingKeys: String, CodingKey { case text, time, color, mode }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        text = String((try values.decodeIfPresent(String.self, forKey: .text) ?? "").prefix(200))
        time = try values.decode(Double.self, forKey: .time)
        mode = try values.decodeIfPresent(Int.self, forKey: .mode) ?? 0
        if let number = try? values.decode(Int.self, forKey: .color) {
            color = UInt32(clamping: min(0xFFFFFF, max(0, number)))
        } else if let hex = try? values.decode(String.self, forKey: .color) {
            let digits = hex.trimmingCharacters(in: CharacterSet(charactersIn: "#"))
            let expanded = digits.count == 3 ? digits.map { "\($0)\($0)" }.joined() : digits
            color = UInt32(expanded, radix: 16).map { min(0xFFFFFF, $0) } ?? 0xFFFFFF
        } else { color = 0xFFFFFF }
    }
}

struct ScheduledDanmaku: Identifiable, Sendable {
    let id: Int
    let cue: NativeDanmakuCue
    let lane: Int
    let duration: Double
}
