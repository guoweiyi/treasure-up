import XCTest
import UIKit
@testable import TreasureUp

final class FullscreenPlayerTests: XCTestCase {
    func testPortraitVideoStaysPortraitAndLandscapeRotatesPhone() {
        XCTAssertEqual(PlayerFullscreenGeometry.orientation(presentationSize: CGSize(width: 1080, height: 1920), width: nil, height: nil, current: .landscapeLeft), .portrait)
        XCTAssertEqual(PlayerFullscreenGeometry.orientation(presentationSize: CGSize(width: 1920, height: 1080), width: nil, height: nil, current: .portrait), .landscapeRight)
        XCTAssertEqual(PlayerFullscreenGeometry.orientation(presentationSize: CGSize(width: 1920, height: 1080), width: nil, height: nil, current: .landscapeLeft), .landscapeLeft)
    }

    func testRotationAwarePresentationSizeTakesPrecedenceOverEncodedDimensions() {
        XCTAssertEqual(PlayerFullscreenGeometry.orientation(presentationSize: CGSize(width: 1080, height: 1920), width: 1920, height: 1080, current: .portrait), .portrait)
        XCTAssertEqual(PlayerFullscreenGeometry.orientation(presentationSize: .zero, width: 1920, height: 1080, current: .portrait), .landscapeRight)
        XCTAssertEqual(PlayerFullscreenGeometry.orientation(presentationSize: CGSize(width: CGFloat.nan, height: 720), width: nil, height: nil, current: .portrait), .portrait)
    }

    @MainActor func testFullscreenControllerUsesWholeScreenPresentationWithVideoOrientation() {
        let playback = PlaybackCoordinator(api: APIClient())
        let landscape = FullscreenPlayerController(playback: playback, orientation: .landscapeLeft, close: {})
        XCTAssertEqual(landscape.modalPresentationStyle, .fullScreen)
        XCTAssertEqual(landscape.supportedInterfaceOrientations, .landscape)
        XCTAssertEqual(landscape.preferredInterfaceOrientationForPresentation, .landscapeLeft)
        XCTAssertTrue(landscape.prefersStatusBarHidden)
        let portrait = FullscreenPlayerController(playback: playback, orientation: .portrait, close: {})
        XCTAssertEqual(portrait.supportedInterfaceOrientations, .portrait)
    }
}
