import SwiftUI

/// Frames are expressed only in the split-view detail's available coordinates.
/// The player includes letterboxing so its controls can use the full column
/// width even when a portrait video's image occupies only a narrow strip.
struct VideoViewportGeometry: Equatable {
    let size: CGSize
    let isWide: Bool
    let playerColumn: CGRect
    let playerFrame: CGRect
    let detailsFrame: CGRect

    init(size proposedSize: CGSize, aspectRatio proposedRatio: CGFloat = 16.0 / 9.0) {
        let width = proposedSize.width.isFinite ? max(0, proposedSize.width) : 0
        let height = proposedSize.height.isFinite ? max(0, proposedSize.height) : 0
        let ratio = proposedRatio.isFinite && proposedRatio > 0 ? proposedRatio : 16.0 / 9.0
        size = CGSize(width: width, height: height)
        isWide = width >= 820

        if isWide {
            let informationWidth = min(420, max(320, width * 0.36))
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
        GeometryReader { geometry in
            let viewport = VideoViewportGeometry(size: geometry.size, aspectRatio: aspectRatio)
            ZStack(alignment: .topLeading) {
                Color.black
                    .frame(width: viewport.playerColumn.width, height: viewport.playerColumn.height)
                    .offset(x: viewport.playerColumn.minX, y: viewport.playerColumn.minY)
                    .accessibilityHidden(true)
                player
                    .frame(width: viewport.playerFrame.width, height: viewport.playerFrame.height)
                    .background(.black).clipped()
                    .offset(x: viewport.playerFrame.minX, y: viewport.playerFrame.minY)
                details
                    .frame(width: viewport.detailsFrame.width, height: viewport.detailsFrame.height)
                    .background(Color(uiColor: .systemBackground)).clipped()
                    .offset(x: viewport.detailsFrame.minX, y: viewport.detailsFrame.minY)
            }
            .frame(width: viewport.size.width, height: viewport.size.height, alignment: .topLeading)
            .clipped()
        }
    }
}
