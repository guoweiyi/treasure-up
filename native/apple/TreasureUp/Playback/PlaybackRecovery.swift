import Foundation

/// Recovery can change delivery, never the selected source or audio rendition.
struct NativePlaybackRecovery {
    enum Action: Equatable { case useFile(sourceID: String), renewAddress, stop }
    private(set) var fileFallbackUsed = false
    private(set) var networkRetryUsed = false

    mutating func resetNetworkBudget() { networkRetryUsed = false }

    mutating func nextAction(isNetworkFailure: Bool, transport: String?, sourceID: String?,
                             selectedSourceID: String?, availableSources: Set<String>) -> Action {
        if isNetworkFailure {
            guard !networkRetryUsed else { return .stop }
            networkRetryUsed = true
            return .renewAddress
        }
        guard transport == "hls", !fileFallbackUsed,
              let sourceID, sourceID == selectedSourceID, availableSources.contains(sourceID) else { return .stop }
        fileFallbackUsed = true
        return .useFile(sourceID: sourceID)
    }

    static func isNetworkError(_ error: Error?, httpStatus: Int? = nil) -> Bool {
        if let httpStatus, [401, 403, 404, 408, 429].contains(httpStatus) || httpStatus >= 500 { return true }
        var current = error as NSError?
        for _ in 0..<8 {
            guard let candidate = current else { break }
            if candidate.domain == NSURLErrorDomain { return true }
            current = candidate.userInfo[NSUnderlyingErrorKey] as? NSError
        }
        return false
    }
}
