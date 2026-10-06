import XCTest
import UIKit
@testable import TreasureUp

@MainActor
final class VideoPageLifetimeTests: XCTestCase {
    func testAncestorModalCoverDoesNotMistakeNavigationPushForPresentation() {
        let source = UIViewController()
        let anchor = UIViewController()
        source.addChild(anchor)
        let navigation = UINavigationController(rootViewController: source)
        let root = PresentationTestController()
        root.addChild(navigation)
        let lifetime = VideoPageLifetime()
        lifetime.bind(anchor)

        XCTAssertFalse(lifetime.isCoveredByPresentation)
        root.modal = PresentationTestController()
        root.modal?.modalPresentationStyle = .fullScreen
        XCTAssertTrue(lifetime.isCoveredByPresentation)

        let pushed = UIViewController()
        navigation.setViewControllers([source, pushed], animated: false)
        XCTAssertFalse(lifetime.isCoveredByPresentation,
                       "A new navigation destination is a real exit even if an ancestor still has a modal")

        navigation.setViewControllers([source], animated: false)
        XCTAssertTrue(lifetime.isCoveredByPresentation)
        root.modal?.dismissing = true
        XCTAssertFalse(lifetime.isCoveredByPresentation)
    }

    func testChangedTabAndUnboundAnchorCannotKeepExitedVideoAlive() {
        let source = UIViewController()
        let anchor = UIViewController()
        source.addChild(anchor)
        let navigation = UINavigationController(rootViewController: source)
        let tabs = UITabBarController()
        tabs.setViewControllers([navigation, UIViewController()], animated: false)
        tabs.selectedIndex = 0
        let root = PresentationTestController()
        root.addChild(tabs)
        root.modal = PresentationTestController()
        let lifetime = VideoPageLifetime()
        lifetime.bind(anchor)
        XCTAssertTrue(lifetime.isCoveredByPresentation)

        tabs.selectedIndex = 1
        XCTAssertFalse(lifetime.isCoveredByPresentation)
        tabs.selectedIndex = 0
        lifetime.unbind(anchor)
        XCTAssertFalse(lifetime.isCoveredByPresentation)
    }
}

private final class PresentationTestController: UIViewController {
    var modal: PresentationTestController?
    var dismissing = false
    override var presentedViewController: UIViewController? { modal ?? super.presentedViewController }
    override var isBeingDismissed: Bool { dismissing }
}
