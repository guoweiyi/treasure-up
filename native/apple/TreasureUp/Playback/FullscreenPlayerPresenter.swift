import SwiftUI
import AVFoundation

/// A real full-screen UIKit presentation escapes the split-view detail column.
/// Both sizes borrow the same AVPlayer and rendering controller without reloading.
struct FullscreenPlayerPresenter: UIViewControllerRepresentable {
    @Binding var isPresented: Bool
    let playback: PlaybackCoordinator
    let lifetime: VideoPageLifetime

    func makeUIViewController(context: Context) -> PlayerPresentationAnchor {
        let anchor = PlayerPresentationAnchor()
        lifetime.bind(anchor)
        return anchor
    }

    func updateUIViewController(_ anchor: PlayerPresentationAnchor, context: Context) {
        lifetime.bind(anchor)
        anchor.update(isPresented: isPresented, playback: playback) { isPresented = false }
    }

    static func dismantleUIViewController(_ anchor: PlayerPresentationAnchor, coordinator: ()) {
        anchor.tearDown()
    }
}

@MainActor
final class PlayerPresentationAnchor: UIViewController {
    private var fullscreen: FullscreenPlayerController?
    private weak var playback: PlaybackCoordinator?
    private var requested = false
    private var closing = false
    private var didClose: (() -> Void)?
    private var originalOrientation: UIInterfaceOrientation = .unknown
    private var presentationUpdate: Task<Void, Never>?

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .clear
        view.isUserInteractionEnabled = false
    }

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        schedulePresentationUpdate()
    }

    func update(isPresented: Bool, playback: PlaybackCoordinator, didClose: @escaping () -> Void) {
        self.playback = playback
        self.didClose = didClose
        requested = isPresented
        schedulePresentationUpdate()
    }

    private func schedulePresentationUpdate() {
        guard presentationUpdate == nil else { return }
        // UIKit appearance callbacks update the shared rendering container.
        // Present after SwiftUI's update pass to avoid re-entrant layout/state
        // cycles when the user presses fullscreen during a sidebar transition.
        presentationUpdate = Task { @MainActor [weak self] in
            guard !Task.isCancelled, let self else { return }
            self.presentationUpdate = nil
            if self.requested { self.presentIfNeeded() }
            else if self.fullscreen != nil { self.close() }
        }
    }

    private func presentIfNeeded() {
        guard requested, fullscreen == nil, !closing, viewIfLoaded?.window != nil,
              let playback, playback.currentVideo != nil else { return }
        originalOrientation = view.window?.windowScene?.interfaceOrientation ?? .unknown
        let size = playback.player.currentItem?.presentationSize ?? .zero
        let variant = playback.currentVariant
        let media = playback.session?.media ?? variant?.metadata
        let orientation = PlayerFullscreenGeometry.orientation(
            presentationSize: size,
            width: media?.width ?? variant?.width,
            height: media?.height ?? variant?.height,
            current: originalOrientation)
        let controller = FullscreenPlayerController(playback: playback, orientation: orientation) { [weak self] in
            self?.close()
        }
        fullscreen = controller
        present(controller, animated: true) { [weak controller] in
            controller?.requestVideoOrientation()
        }
    }

    private func close() {
        guard let controller = fullscreen, !closing else { return }
        closing = true
        requested = false
        // Keep the SwiftUI binding true until dismissal finishes: the inline
        // destination's disappearance during presentation must not stop playback.
        let scene = controller.view.window?.windowScene
        let restore = originalOrientation
        controller.dismiss(animated: true) { [weak self] in
            guard let self else { return }
            self.fullscreen = nil
            self.closing = false
            if UIDevice.current.userInterfaceIdiom == .pad,
               restore != .unknown, scene?.interfaceOrientation != restore {
                self.view.window?.rootViewController?.setNeedsUpdateOfSupportedInterfaceOrientations()
                scene?.requestGeometryUpdate(.iOS(interfaceOrientations: PlayerFullscreenGeometry.mask(for: restore)))
            }
            self.didClose?()
        }
    }

    func tearDown() {
        presentationUpdate?.cancel()
        presentationUpdate = nil
        // A navigation reset (logout/server change) can remove the presenter
        // while the full-screen controller is still covering its source view.
        if fullscreen != nil {
            fullscreen?.dismiss(animated: false)
            fullscreen = nil
            playback?.stop()
        }
    }
}

@MainActor
final class FullscreenPlayerController: UIHostingController<FullscreenPlayerContent> {
    let videoOrientation: UIInterfaceOrientation

    init(playback: PlaybackCoordinator, orientation: UIInterfaceOrientation, close: @escaping () -> Void) {
        videoOrientation = orientation
        super.init(rootView: FullscreenPlayerContent(playback: playback, close: close))
        modalPresentationStyle = .fullScreen
        modalPresentationCapturesStatusBarAppearance = true
        view.backgroundColor = .black
    }

    @MainActor required dynamic init?(coder aDecoder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    override var prefersStatusBarHidden: Bool { true }
    override var prefersHomeIndicatorAutoHidden: Bool { true }
    @available(iOS 26.0, *)
    override var prefersInterfaceOrientationLocked: Bool { true }
    override var supportedInterfaceOrientations: UIInterfaceOrientationMask {
        videoOrientation.isLandscape ? .landscape : .portrait
    }
    override var preferredInterfaceOrientationForPresentation: UIInterfaceOrientation { videoOrientation }

    func requestVideoOrientation() {
        setNeedsUpdateOfSupportedInterfaceOrientations()
        if #available(iOS 26.0, *) { setNeedsUpdateOfPrefersInterfaceOrientationLocked() }
        // iPhone's full-screen presentation already rotates using the preferred
        // orientation. A second scene request would race that UIKit transition.
        // iPad's windowing system needs an explicit geometry request instead.
        guard UIDevice.current.userInterfaceIdiom == .pad,
              let scene = view.window?.windowScene,
              !supportedInterfaceOrientations.contains(PlayerFullscreenGeometry.mask(for: scene.interfaceOrientation)) else { return }
        scene.requestGeometryUpdate(.iOS(interfaceOrientations: supportedInterfaceOrientations))
    }
}

struct FullscreenPlayerContent: View {
    let playback: PlaybackCoordinator
    let close: () -> Void
    var body: some View {
        InlineNativePlayer(coordinator: playback, isExpanded: true, onToggleExpanded: close)
            .background(.black).ignoresSafeArea()
            .accessibilityElement(children: .contain)
            .accessibilityIdentifier("fullscreen-player")
    }
}

enum PlayerFullscreenGeometry {
    static func orientation(presentationSize: CGSize, width: Int?, height: Int?, current: UIInterfaceOrientation) -> UIInterfaceOrientation {
        let size: CGSize
        if presentationSize.width.isFinite, presentationSize.height.isFinite,
           presentationSize.width > 0, presentationSize.height > 0 {
            // AVPlayer has already accounted for the source's rotation transform.
            size = presentationSize
        } else if let width, let height, width > 0, height > 0 {
            size = CGSize(width: width, height: height)
        } else {
            return current == .unknown ? .portrait : current
        }
        if size.height >= size.width { return .portrait }
        return current.isLandscape ? current : .landscapeRight
    }

    static func mask(for orientation: UIInterfaceOrientation) -> UIInterfaceOrientationMask {
        switch orientation {
        case .landscapeLeft: .landscapeLeft
        case .landscapeRight: .landscapeRight
        case .portraitUpsideDown: .portraitUpsideDown
        default: .portrait
        }
    }
}
