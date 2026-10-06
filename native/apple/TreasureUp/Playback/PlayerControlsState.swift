import SwiftUI
import Observation

/// A short decoder wait should not flash a spinner over the picture. Stopping
/// the wait invalidates its delayed update, including rapid pause/resume cycles.
@MainActor @Observable
final class PlayerWaitingState {
    private(set) var isVisible = false
    @ObservationIgnored private var pending: Task<Void, Never>?
    @ObservationIgnored private let delay: Duration

    init(delay: Duration = .milliseconds(350)) { self.delay = delay }

    func update(isWaiting: Bool) {
        pending?.cancel()
        pending = nil
        isVisible = false
        guard isWaiting else { return }
        let delay = delay
        pending = Task { @MainActor [weak self] in
            do { try await Task.sleep(for: delay) } catch { return }
            guard !Task.isCancelled else { return }
            self?.isVisible = true
        }
    }
}

struct PlayerWaitingFeedback: View {
    let isLoading: Bool
    let isBuffering: Bool
    let wantsPlayback: Bool
    let hasError: Bool
    @State private var state = PlayerWaitingState()

    private var isWaiting: Bool { !hasError && (isLoading || (isBuffering && wantsPlayback)) }

    var body: some View {
        Group {
            if isWaiting && state.isVisible {
                HStack(spacing: 10) {
                    ProgressView().tint(.white)
                    Text(appPrompt(isLoading ? "正在准备视频" : "正在缓冲"))
                        .font(.caption.weight(.medium))
                }.foregroundStyle(.white).padding(.horizontal, 16).padding(.vertical, 12)
                    .background(.black.opacity(0.65), in: .capsule)
                    .accessibilityIdentifier("player-waiting")
            }
        }
        .allowsHitTesting(false)
        .onChange(of: isWaiting, initial: true) { _, waiting in state.update(isWaiting: waiting) }
        .onDisappear { state.update(isWaiting: false) }
    }
}

/// The panel's header stays fixed while its choices scroll inside the video.
/// Both its content and close button stay within fullscreen's safe rectangle.
struct PlayerPanelGeometry {
    let panelSize: CGSize
    let insets: EdgeInsets

    init(size: CGSize, insets proposedInsets: EdgeInsets, preferredHeight: CGFloat) {
        func finite(_ value: CGFloat) -> CGFloat { value.isFinite ? max(0, value) : 0 }
        let width = finite(size.width)
        let height = finite(size.height)
        let leading = min(width, finite(proposedInsets.leading))
        let trailing = min(width - leading, finite(proposedInsets.trailing))
        let top = min(height, finite(proposedInsets.top))
        let bottom = min(height - top, finite(proposedInsets.bottom))
        insets = EdgeInsets(top: top, leading: leading, bottom: bottom, trailing: trailing)
        panelSize = CGSize(width: min(480, max(0, width - leading - trailing - 24)),
                           height: min(finite(preferredHeight), max(0, height - top - bottom - 24)))
    }
}
