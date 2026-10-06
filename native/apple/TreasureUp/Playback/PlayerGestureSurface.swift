import SwiftUI
import UIKit

enum PlayerGesturePhase { case began, changed, ended, cancelled }

/// The recognizers survive controls appearing and disappearing. Only the hit
/// region changes; a first tap can reveal controls without replacing the
/// recognizer that is still waiting for the second tap.
struct PlayerGestureSurface: UIViewRepresentable {
    let activeBounds: CGRect
    let controlsVisible: Bool
    let isEnabled: Bool
    let onReveal: () -> Void
    let onToggleControls: () -> Void
    let onSkip: (Double) -> Void
    let onScrub: (CGFloat, PlayerGesturePhase) -> Void
    let onHold: (Bool) -> Void

    func makeUIView(context: Context) -> PlayerGestureView { PlayerGestureView() }

    func updateUIView(_ view: PlayerGestureView, context: Context) {
        view.configure(self)
    }

    static func dismantleUIView(_ view: PlayerGestureView, coordinator: ()) {
        view.cancelInteractions()
    }
}

@MainActor
final class PlayerGestureView: UIView, UIGestureRecognizerDelegate {
    private var configuration: PlayerGestureSurface?
    private var pendingTap: Task<Void, Never>?
    private var lastDoubleTap = Date.distantPast
    private var scrubbing = false
    private var holding = false
    private(set) var singleTap: UITapGestureRecognizer!
    private(set) var doubleTap: UITapGestureRecognizer!
    private(set) var pan: UIPanGestureRecognizer!
    private(set) var hold: UILongPressGestureRecognizer!

    init() {
        super.init(frame: .zero)
        backgroundColor = .clear
        isAccessibilityElement = false
        accessibilityElementsHidden = true
        singleTap = UITapGestureRecognizer(target: self, action: #selector(tapped))
        doubleTap = UITapGestureRecognizer(target: self, action: #selector(doubleTapped))
        doubleTap.numberOfTapsRequired = 2
        pan = UIPanGestureRecognizer(target: self, action: #selector(panned))
        pan.maximumNumberOfTouches = 1
        hold = UILongPressGestureRecognizer(target: self, action: #selector(held))
        hold.minimumPressDuration = 0.35
        hold.allowableMovement = 22
        for recognizer in [singleTap!, doubleTap!, pan!, hold!] {
            recognizer.delegate = self
            recognizer.cancelsTouchesInView = false
            addGestureRecognizer(recognizer)
        }
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("Use init()") }

    func configure(_ value: PlayerGestureSurface) {
        let wasEnabled = configuration?.isEnabled == true
        configuration = value
        if wasEnabled && !value.isEnabled { cancelInteractions() }
        isUserInteractionEnabled = value.isEnabled
    }

    override func point(inside point: CGPoint, with event: UIEvent?) -> Bool {
        guard configuration?.isEnabled == true, let activeBounds = configuration?.activeBounds else { return false }
        return bounds.contains(point) && activeBounds.contains(point)
    }

    override func didMoveToWindow() {
        super.didMoveToWindow()
        if window == nil { cancelInteractions() }
    }

    func cancelInteractions() {
        pendingTap?.cancel()
        pendingTap = nil
        if scrubbing {
            scrubbing = false
            configuration?.onScrub(0, .cancelled)
        }
        if holding {
            holding = false
            configuration?.onHold(false)
        }
        for recognizer in gestureRecognizers ?? [] {
            recognizer.isEnabled = false
            recognizer.isEnabled = true
        }
    }

    @objc private func tapped(_ recognizer: UITapGestureRecognizer) {
        guard recognizer.state == .ended, let configuration,
              Date().timeIntervalSince(lastDoubleTap) > 0.12 else { return }
        pendingTap?.cancel()
        if !configuration.controlsVisible {
            configuration.onReveal()
        } else {
            // Only hiding waits for the double-tap interval. Revealing and every
            // transport button remain immediate; a double tap never flickers
            // controls off or loses its first touch to a new SwiftUI gesture.
            pendingTap = Task { @MainActor [weak self] in
                do { try await Task.sleep(for: .milliseconds(300)) } catch { return }
                guard let self, self.window != nil, self.configuration?.isEnabled == true else { return }
                self.pendingTap = nil
                self.configuration?.onToggleControls()
            }
        }
    }

    @objc private func doubleTapped(_ recognizer: UITapGestureRecognizer) {
        guard recognizer.state == .ended else { return }
        pendingTap?.cancel()
        pendingTap = nil
        lastDoubleTap = Date()
        configuration?.onSkip(recognizer.location(in: self).x < bounds.midX ? -15 : 15)
    }

    @objc private func panned(_ recognizer: UIPanGestureRecognizer) {
        let translation = recognizer.translation(in: self).x
        switch recognizer.state {
        case .began:
            pendingTap?.cancel()
            scrubbing = true
            configuration?.onScrub(translation, .began)
        case .changed where scrubbing:
            configuration?.onScrub(translation, .changed)
        case .ended where scrubbing:
            scrubbing = false
            configuration?.onScrub(translation, .ended)
        case .cancelled, .failed:
            if scrubbing {
                scrubbing = false
                configuration?.onScrub(translation, .cancelled)
            }
        default: break
        }
    }

    @objc private func held(_ recognizer: UILongPressGestureRecognizer) {
        switch recognizer.state {
        case .began:
            pendingTap?.cancel()
            holding = true
            configuration?.onHold(true)
        case .ended, .cancelled, .failed:
            if holding {
                holding = false
                configuration?.onHold(false)
            }
        default: break
        }
    }

    override func gestureRecognizerShouldBegin(_ gestureRecognizer: UIGestureRecognizer) -> Bool {
        guard configuration?.isEnabled == true else { return false }
        if gestureRecognizer === pan {
            let velocity = pan.velocity(in: self)
            return !holding && PlayerGestureMath.dragAxis(horizontal: velocity.x, vertical: velocity.y) == .horizontal
        }
        if gestureRecognizer === hold { return !scrubbing }
        return true
    }

    func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer, shouldReceive touch: UITouch) -> Bool {
        let point = touch.location(in: self)
        guard self.point(inside: point, with: nil) else { return false }
        // Let the navigation back gesture own its leading edge. Buttons in the
        // top/bottom bands are already outside this view's hit region.
        return gestureRecognizer !== pan || point.x >= 24
    }

    func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer,
                           shouldRecognizeSimultaneouslyWith otherGestureRecognizer: UIGestureRecognizer) -> Bool {
        (gestureRecognizer === singleTap && otherGestureRecognizer === doubleTap) ||
        (gestureRecognizer === doubleTap && otherGestureRecognizer === singleTap)
    }
}
