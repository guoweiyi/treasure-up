import SwiftUI
import UIKit

/// Frames are expressed only in the split-view detail's available coordinates.
/// The player includes letterboxing so its controls can use the full column
/// width even when a portrait video's image occupies only a narrow strip.
struct VideoViewportGeometry: Equatable {
    let size: CGSize
    let isWide: Bool
    let playerColumn: CGRect
    let playerFrame: CGRect
    let detailsFrame: CGRect

    init(size proposedSize: CGSize, aspectRatio proposedRatio: CGFloat = 16.0 / 9.0,
         minimumInformationWidth: CGFloat = 320, unobscuredHeight: CGFloat? = nil) {
        let width = proposedSize.width.isFinite ? max(0, proposedSize.width) : 0
        let height = proposedSize.height.isFinite ? max(0, proposedSize.height) : 0
        let ratio = proposedRatio.isFinite && proposedRatio > 0 ? proposedRatio : 16.0 / 9.0
        size = CGSize(width: width, height: height)
        let informationMinimum = minimumInformationWidth.isFinite ? min(420, max(320, minimumInformationWidth)) : 320
        // An iPad portrait window can be wider than 820 points but still needs
        // a full-width player. Reserve readable information and controls columns
        // only when the actual detail area is landscape, including Stage Manager.
        let layoutHeight = unobscuredHeight.flatMap { $0.isFinite && $0 >= 0 ? max(height, $0) : nil } ?? height
        isWide = width >= 500 + informationMinimum && width > layoutHeight

        if isWide {
            let informationWidth = min(420, max(informationMinimum, width * 0.36))
            let columnWidth = width - informationWidth
            let fittedWidth = min(columnWidth, height * ratio)
            let playerHeight = min(height, max(Self.minimumControlsHeight, fittedWidth / ratio))
            playerColumn = CGRect(x: 0, y: 0, width: columnWidth, height: height)
            playerFrame = CGRect(x: 0, y: (height - playerHeight) / 2,
                                 width: columnWidth, height: playerHeight)
            detailsFrame = CGRect(x: columnWidth, y: 0, width: informationWidth, height: height)
        } else {
            let fittedWidth = min(width, height * 0.48 * ratio)
            let playerHeight = min(height, max(Self.minimumControlsHeight, fittedWidth / ratio))
            playerColumn = CGRect(x: 0, y: 0, width: width, height: playerHeight)
            playerFrame = playerColumn
            detailsFrame = CGRect(x: 0, y: playerHeight, width: width,
                                  height: max(0, height - playerHeight))
        }
    }

    // Header, progress slider and transport row each retain a 44-point target.
    // Very wide media must not shrink those rows into one overlapping band.
    private static let minimumControlsHeight: CGFloat = 44 * 3 + 3
}

/// A stable tree keeps the native AVPlayer container and each visited section
/// mounted while a sidebar, rotation or window resize changes their geometry.
struct VideoDetailViewport<Player: View, Details: View>: View {
    @Environment(\.dynamicTypeSize) private var typeSize
    @State private var unobscuredHeight: CGFloat?
    let aspectRatio: CGFloat
    private let player: Player
    private let details: Details

    init(aspectRatio: CGFloat = 16.0 / 9.0,
         @ViewBuilder player: () -> Player,
         @ViewBuilder details: () -> Details) {
        self.aspectRatio = aspectRatio
        self.player = player()
        self.details = details()
    }

    var body: some View {
        VideoDetailLayout(aspectRatio: aspectRatio,
                          minimumInformationWidth: typeSize.isAccessibilitySize ? 420 : 320,
                          unobscuredHeight: unobscuredHeight) {
            Color.black.accessibilityHidden(true)
            player
                .frame(minWidth: 0, maxWidth: .infinity, minHeight: 0, maxHeight: .infinity)
                .background(.black).clipped()
            details
                .frame(minWidth: 0, maxWidth: .infinity, minHeight: 0, maxHeight: .infinity, alignment: .topLeading)
                .background(Color(uiColor: .systemBackground)).clipped()
        }
        .clipped()
        .background {
            VideoViewportKeyboardObserver { unobscuredHeight = $0 }
                .allowsHitTesting(false).accessibilityHidden(true)
        }
    }
}

/// A layout pass places all three surfaces together. Resizing a sidebar no
/// longer rebuilds the view body through GeometryReader before UIKit receives
/// its new player bounds; the same player and section trees remain mounted.
private struct VideoDetailLayout: Layout {
    let aspectRatio: CGFloat
    let minimumInformationWidth: CGFloat
    let unobscuredHeight: CGFloat?

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let size = proposal.replacingUnspecifiedDimensions()
        return VideoViewportGeometry(size: size).size
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        guard subviews.count == 3 else { return }
        let viewport = VideoViewportGeometry(size: bounds.size, aspectRatio: aspectRatio,
                                             minimumInformationWidth: minimumInformationWidth,
                                             unobscuredHeight: unobscuredHeight)
        for (view, frame) in zip(subviews, [viewport.playerColumn, viewport.playerFrame, viewport.detailsFrame]) {
            view.place(at: CGPoint(x: bounds.minX + frame.minX, y: bounds.minY + frame.minY),
                       anchor: .topLeading,
                       proposal: ProposedViewSize(width: frame.width, height: frame.height))
        }
    }
}

/// Preserve only the window area occupied by navigation/toolbars before the
/// keyboard opens. The window height itself stays live during rotation or
/// Stage Manager resizing, so keyboard avoidance cannot change the column mode.
struct VideoViewportKeyboardGeometry: Equatable, Sendable {
    private let surroundingHeight: CGFloat

    init?(viewportHeight: CGFloat, windowHeight: CGFloat) {
        guard viewportHeight.isFinite, windowHeight.isFinite,
              viewportHeight > 0, windowHeight > 0 else { return nil }
        surroundingHeight = max(0, windowHeight - viewportHeight)
    }

    func unobscuredHeight(viewportHeight: CGFloat, windowHeight: CGFloat) -> CGFloat {
        let available = viewportHeight.isFinite ? max(0, viewportHeight) : 0
        guard windowHeight.isFinite else { return available }
        return max(available, windowHeight - surroundingHeight)
    }
}

private struct VideoViewportKeyboardObserver: UIViewRepresentable {
    var onChange: (CGFloat?) -> Void

    func makeUIView(context: Context) -> VideoViewportKeyboardView {
        let view = VideoViewportKeyboardView()
        view.onChange = onChange
        return view
    }

    func updateUIView(_ view: VideoViewportKeyboardView, context: Context) {
        view.onChange = onChange
    }

    func sizeThatFits(_ proposal: ProposedViewSize, uiView: VideoViewportKeyboardView, context: Context) -> CGSize? {
        guard let width = proposal.width, let height = proposal.height else { return nil }
        return CGSize(width: width, height: height)
    }

    static func dismantleUIView(_ view: VideoViewportKeyboardView, coordinator: ()) {
        view.stopObserving()
        view.onChange = nil
    }
}

/// This probe is local to the video's window. It neither changes keyboard safe
/// areas nor observes playback ticks; its callback changes only when the height
/// used to classify the window changes.
private final class VideoViewportKeyboardView: UIView {
    var onChange: ((CGFloat?) -> Void)?
    private var snapshot: VideoViewportKeyboardGeometry?
    private var observations: [NSObjectProtocol] = []
    private var publishedHeight: CGFloat?
    private var publicationRevision = 0

    override func didMoveToWindow() {
        super.didMoveToWindow()
        guard window != nil else { stopObserving(); return }
        guard observations.isEmpty else { return }
        for name in [UIResponder.keyboardWillChangeFrameNotification,
                     UIResponder.keyboardDidChangeFrameNotification,
                     UIResponder.keyboardDidHideNotification] {
            observations.append(NotificationCenter.default.addObserver(forName: name, object: nil, queue: .main) { [weak self] notification in
                // The observer explicitly delivers on the main operation queue.
                // Extract Sendable values before entering actor isolation;
                // Notification.userInfo may contain arbitrary non-Sendable objects.
                let name = notification.name
                let isLocal = notification.userInfo?[UIResponder.keyboardIsLocalUserInfoKey] as? Bool != false
                let frame = (notification.userInfo?[UIResponder.keyboardFrameEndUserInfoKey] as? NSValue)?.cgRectValue
                MainActor.assumeIsolated { self?.keyboardChanged(name: name, frame: frame, isLocal: isLocal) }
            })
        }
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        publishHeight()
    }

    func stopObserving() {
        observations.forEach { NotificationCenter.default.removeObserver($0) }
        observations.removeAll()
        snapshot = nil
        publishHeight()
    }

    private func keyboardChanged(name: Notification.Name, frame: CGRect?, isLocal: Bool) {
        guard let window, isLocal else { return }
        if name == UIResponder.keyboardDidHideNotification {
            snapshot = nil
        } else if let frame {
            let localFrame = window.convert(frame, from: window.screen.coordinateSpace)
            let intersection = window.bounds.intersection(localFrame)
            if !intersection.isNull && intersection.height > 1 {
                if snapshot == nil {
                    snapshot = VideoViewportKeyboardGeometry(viewportHeight: bounds.height, windowHeight: window.bounds.height)
                }
            } else if name == UIResponder.keyboardDidChangeFrameNotification {
                // Keep the original shape until the dismissal animation ends;
                // releasing it at willHide would briefly classify the short area.
                snapshot = nil
            }
        }
        publishHeight()
    }

    private func publishHeight() {
        let height = window.flatMap { window in
            snapshot?.unobscuredHeight(viewportHeight: bounds.height, windowHeight: window.bounds.height)
        }
        guard height != publishedHeight else { return }
        publishedHeight = height
        publicationRevision += 1
        let revision = publicationRevision
        // Layout callbacks must not mutate SwiftUI state during its layout pass.
        // Fence queued publications so a closed keyboard cannot publish an old size.
        DispatchQueue.main.async { [weak self] in
            guard let self, self.publicationRevision == revision else { return }
            self.onChange?(height)
        }
    }
}
