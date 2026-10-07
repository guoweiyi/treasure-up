import SwiftUI
import AVFoundation
import Observation

/// A modal remains occupied through its entire UIKit transition, including the
/// interval after SwiftUI removes the page that originally presented it.
@MainActor @Observable
final class FullscreenPresentationState {
    private(set) var isActive = false
    @ObservationIgnored private var dismissals: [UUID: () -> Void] = [:]

    func begin(_ token: UUID, requestDismissal: @escaping () -> Void) {
        dismissals[token] = requestDismissal
        isActive = true
    }

    func end(_ token: UUID) {
        dismissals[token] = nil
        isActive = !dismissals.isEmpty
    }

    func requestDismissal() {
        // A callback may synchronously release a rejected presentation.
        for dismiss in Array(dismissals.values) { dismiss() }
    }
}

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
    private enum Phase: Equatable { case idle, presenting, presented, dismissing }
    private var phase = Phase.idle
    private var fullscreen: FullscreenPlayerController?
    private weak var playback: PlaybackCoordinator?
    private var requested = false
    private var lastBindingValue = false
    private var tornDown = false
    private var presentationToken: UUID?
    private var presentationState: FullscreenPresentationState?
    private var presentedVideoID: String?
    private weak var presentedInlineOwner: UIViewController?
    private var didClose: (() -> Void)?
    private var originalOrientation: UIInterfaceOrientation = .unknown
    private var presentationUpdate: Task<Void, Never>?
    private var orientationRestoration: Task<Void, Never>?
    private var orientationRestorationFailed = false

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
        guard !tornDown else { return }
        self.playback = playback
        self.didClose = didClose
        // Observable playback updates can revisit this representable while the
        // binding intentionally stays true through an exit animation. Only a
        // new binding edge can override the user's close request.
        if lastBindingValue != isPresented {
            lastBindingValue = isPresented
            requested = isPresented
            if isPresented, phase == .idle {
                // The page may leave before the queued presentation task runs.
                // Record ownership at intent time, not only after present().
                presentedVideoID = playback.currentVideo?.id
                presentedInlineOwner = playback.presentation?.controller.parent
            }
        }
        schedulePresentationUpdate()
    }

    private func schedulePresentationUpdate() {
        guard !tornDown, presentationUpdate == nil else { return }
        presentationUpdate = Task { @MainActor [weak self] in
            guard !Task.isCancelled, let self else { return }
            self.presentationUpdate = nil
            self.reconcilePresentation()
        }
    }

    private func reconcilePresentation() {
        switch phase {
        case .presenting, .dismissing:
            // Completion callbacks serialize UIKit transitions. Calling dismiss
            // while present is in flight can strand its controller and binding.
            return
        case .idle:
            if requested && !tornDown { presentIfNeeded() }
        case .presented:
            if fullscreen?.presentingViewController == nil {
                fullscreen = nil
                phase = .idle
                releasePresentation()
                completeCloseRequest()
            } else if !requested || tornDown || playback?.currentVideo == nil {
                dismissCurrent(animated: !tornDown)
            } else {
                fullscreen?.requestVideoOrientation()
            }
        }
    }

    private func presentIfNeeded() {
        guard viewIfLoaded?.window != nil else { return }
        guard let playback, playback.currentVideo != nil else { completeCloseRequest(); return }
        var ancestor: UIViewController? = self
        while let current = ancestor {
            // Do not replace a share sheet/route picker or retain a phantom
            // fullscreen controller when UIKit rejects a second presentation.
            if current.presentedViewController != nil {
                completeCloseRequest()
                return
            }
            ancestor = current.parent
        }
        originalOrientation = view.window?.windowScene?.interfaceOrientation ?? .unknown
        let orientation = FullscreenPlayerController.orientation(for: playback, current: originalOrientation)
        let token = UUID()
        let controller = FullscreenPlayerController(playback: playback, orientation: orientation) { [weak self] in
            self?.requestClose(token: token)
        }
        fullscreen = controller
        presentedVideoID = playback.currentVideo?.id
        presentedInlineOwner = playback.presentation?.controller.parent
        presentationToken = token
        phase = .presenting
        presentationState = playback.fullscreenPresentation
        presentationState?.begin(token) { [weak self] in self?.requestClose(token: token) }
        // UIKit retains this completion until the transition finishes. Keep the
        // anchor alive long enough to honor teardown/cancel during presentation.
        present(controller, animated: true) { [self, weak controller] in
            guard let controller, fullscreen === controller else { return }
            phase = .presented
            // The completion still executes inside UIKit's orientation commit.
            // Re-entering dismiss/requestGeometryUpdate here can request a new
            // orientation token before that transaction has returned.
            schedulePresentationUpdate()
            if tornDown {
                Task { @MainActor [self] in reconcilePresentation() }
            }
        }
        if controller.presentingViewController == nil {
            fullscreen = nil
            phase = .idle
            releasePresentation()
            completeCloseRequest()
        }
    }

    private func requestClose(token: UUID) {
        guard presentationToken == token, !tornDown else { return }
        requested = false
        schedulePresentationUpdate()
    }

    private func dismissCurrent(animated: Bool) {
        guard phase == .presented, let controller = fullscreen else { return }
        phase = .dismissing
        let scene = controller.view.window?.windowScene
        let restore = originalOrientation
        controller.dismiss(animated: animated) { [self, weak controller] in
            // UIKit still owns its orientation transaction inside this callback.
            // Finish on the next main-queue turn before requesting scene geometry.
            DispatchQueue.main.async { [self, weak controller] in
                guard let controller, fullscreen === controller else { return }
                fullscreen = nil
                // A fresh false→true binding edge during dismissal is a new request.
                // Do not restore the previous geometry before immediately reopening.
                if requested && !tornDown {
                    finishDismissal()
                } else if !tornDown, let scene, let window = viewIfLoaded?.window,
                          window.windowScene === scene, restore != .unknown,
                          scene.interfaceOrientation != restore {
                    restoreOrientation(restore, in: scene, window: window)
                } else {
                    finishDismissal()
                }
            }
        }
    }

    private func restoreOrientation(_ orientation: UIInterfaceOrientation, in scene: UIWindowScene, window: UIWindow) {
        guard let token = presentationToken else { finishDismissal(); return }
        let root = window.rootViewController
        root?.setNeedsUpdateOfSupportedInterfaceOrientations()
        if #available(iOS 26.0, *) { root?.setNeedsUpdateOfPrefersInterfaceOrientationLocked() }
        orientationRestorationFailed = false
        // iPhone can restore window bounds while leaving its scene landscape
        // after a locked presentation. Both device families need this request.
        scene.requestGeometryUpdate(.iOS(interfaceOrientations: PlayerFullscreenGeometry.mask(for: orientation))) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard self?.presentationToken == token else { return }
                self?.orientationRestorationFailed = true
            }
        }
        // Geometry updates have no success callback. Keep authentication queued
        // until the scene and its rotation settle, with a bound for system-denied
        // changes (for example, a resized iPad window).
        orientationRestoration = Task { @MainActor [self] in
            for _ in 0..<100 {
                if tornDown || orientationRestorationFailed ||
                    (scene.interfaceOrientation == orientation && root?.transitionCoordinator == nil) { break }
                do { try await Task.sleep(for: .milliseconds(20)) }
                catch { break }
            }
            guard phase == .dismissing, presentationToken == token else { return }
            orientationRestoration = nil
            finishDismissal()
        }
    }

    private func finishDismissal() {
        phase = .idle
        releasePresentation()
        if requested && !tornDown { schedulePresentationUpdate() }
        else { completeCloseRequest() }
    }

    private func completeCloseRequest() {
        requested = false
        lastBindingValue = false
        if !tornDown { didClose?() }
    }

    private func releasePresentation() {
        if let presentationToken { presentationState?.end(presentationToken) }
        presentationToken = nil
        presentationState = nil
    }

    func tearDown() {
        guard !tornDown else { return }
        tornDown = true
        requested = false
        presentationUpdate?.cancel()
        presentationUpdate = nil
        // A retired page must not stop a new video's playback or a shared layer
        // already claimed by another page. Navigation/identity owners otherwise
        // retain their existing stop behavior.
        if let playback, playback.currentVideo?.id == presentedVideoID {
            var owner = playback.presentation?.controller.parent
            if let owner, owner === presentedInlineOwner {
                playback.stop()
            } else if let fullscreen {
                while let current = owner {
                    if current === fullscreen { playback.stop(); break }
                    owner = current.parent
                }
            }
        }
        reconcilePresentation()
    }
}

@MainActor
final class FullscreenPlayerController: UIHostingController<FullscreenPlayerContent> {
    private(set) var videoOrientation: UIInterfaceOrientation
    private weak var playback: PlaybackCoordinator?
    private var playerItemObservation: NSKeyValueObservation?
    private var presentationSizeObservation: NSKeyValueObservation?
    private var pendingOrientationUpdate = false

    init(playback: PlaybackCoordinator, orientation: UIInterfaceOrientation, close: @escaping () -> Void) {
        videoOrientation = orientation
        self.playback = playback
        super.init(rootView: FullscreenPlayerContent(playback: playback, close: close))
        modalPresentationStyle = .fullScreen
        modalPresentationCapturesStatusBarAppearance = true
        view.backgroundColor = .black
        playerItemObservation = playback.player.observe(\.currentItem, options: [.initial, .new]) { @Sendable [weak self] _, _ in
            Task { @MainActor in self?.observeCurrentItem() }
        }
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

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        refreshVideoOrientation()
    }

    static func orientation(for playback: PlaybackCoordinator, current: UIInterfaceOrientation) -> UIInterfaceOrientation {
        let variant = playback.currentVariant
        let media = playback.session?.media ?? variant?.metadata
        return PlayerFullscreenGeometry.orientation(presentationSize: playback.player.currentItem?.presentationSize ?? .zero,
            width: media?.width ?? variant?.width, height: media?.height ?? variant?.height, current: current)
    }

    private func observeCurrentItem() {
        presentationSizeObservation = nil
        guard let item = playback?.player.currentItem else { return }
        presentationSizeObservation = item.observe(\.presentationSize, options: [.initial, .new]) { @Sendable [weak self] _, _ in
            Task { @MainActor in self?.refreshVideoOrientation() }
        }
    }

    private func refreshVideoOrientation() {
        guard let playback, playback.currentVideo != nil, playback.player.currentItem != nil else { return }
        let orientation = Self.orientation(for: playback, current: videoOrientation)
        if orientation != videoOrientation {
            videoOrientation = orientation
            pendingOrientationUpdate = true
        }
        // The initial iPhone presentation still rotates using UIKit's preferred
        // orientation. Only an already-visible player's new media requests an
        // explicit geometry change; never race its entrance/exit animation.
        if pendingOrientationUpdate, viewIfLoaded?.window != nil, !isBeingPresented, !isBeingDismissed {
            pendingOrientationUpdate = false
            requestVideoOrientation(allowPhoneRotation: true)
        }
    }

    func requestVideoOrientation(allowPhoneRotation: Bool = false) {
        setNeedsUpdateOfSupportedInterfaceOrientations()
        if #available(iOS 26.0, *) { setNeedsUpdateOfPrefersInterfaceOrientationLocked() }
        // iPhone's full-screen presentation already rotates using the preferred
        // orientation. A second scene request would race that UIKit transition.
        // iPad's windowing system needs an explicit geometry request instead.
        guard UIDevice.current.userInterfaceIdiom == .pad || allowPhoneRotation,
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
