import UIKit

/// Tracks the UIKit presentation context without retaining its hosting page.
/// SwiftUI disappearance alone also fires when a system modal covers a page.
@MainActor
final class VideoPageLifetime {
    private weak var anchor: UIViewController?

    func bind(_ controller: UIViewController) { anchor = controller }

    func unbind(_ controller: UIViewController) {
        if anchor === controller { anchor = nil }
    }

    var isCoveredByPresentation: Bool {
        guard let anchor else { return false }
        var hierarchy: [UIViewController] = []
        var controller: UIViewController? = anchor
        while let current = controller {
            hierarchy.append(current)
            if let navigation = current.parent as? UINavigationController,
               navigation.topViewController !== current {
                // A push/pop changed the visible destination. An unrelated or
                // dismissing presentation must not suppress leaving this page.
                return false
            }
            if let tabs = current.parent as? UITabBarController,
               tabs.selectedViewController !== current {
                return false
            }
            controller = current.parent
        }
        // Share sheets, route pickers and adaptive fullscreen presentations may
        // belong to the hosting, navigation or root controller, not the anchor.
        return hierarchy.contains { current in
            guard let presented = current.presentedViewController else { return false }
            return !presented.isBeingDismissed
        }
    }
}
