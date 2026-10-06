import SwiftUI
import UIKit

/// A native slider with a small thumb, buffered track and a full 44pt touch
/// target. Clock ticks cannot move its thumb while the user's finger owns it.
struct PlayerSeekBar: UIViewRepresentable {
    let value: Double
    let duration: Double
    let bufferedTime: Double
    let mediaID: String?
    let onPreview: (Double) -> Void
    let onEditingChanged: (Bool) -> Void
    let onCommit: (Double) -> Void

    func makeUIView(context: Context) -> PlayerSeekSlider { PlayerSeekSlider() }
    func updateUIView(_ slider: PlayerSeekSlider, context: Context) { slider.configure(self) }
    static func dismantleUIView(_ slider: PlayerSeekSlider, coordinator: ()) { slider.cancelInteraction() }
}

@MainActor
final class PlayerSeekSlider: UISlider {
    private let track = CALayer()
    private let bufferedTrack = CALayer()
    private let playedTrack = CALayer()
    private var configuration: PlayerSeekBar?
    private(set) var isScrubbing = false
    private var previewTime = 0.0
    private var editingRevision: UInt64 = 0
    private var interactionWidth: CGFloat?
    private var deferredEditingEnd: Task<Void, Never>?
    private var accessibilitySnapshot: PlayerClockSnapshot?
    private static let idleThumb = thumb(diameter: 12)
    private static let activeThumb = thumb(diameter: 18)

    init() {
        super.init(frame: .zero)
        minimumValue = 0
        maximumValue = 1
        isContinuous = true
        accessibilityLabel = "播放进度"
        accessibilityIdentifier = "player-progress"
        accessibilityTraits = .adjustable
        let empty = UIGraphicsImageRenderer(size: CGSize(width: 1, height: 1)).image { _ in }
        setMinimumTrackImage(empty, for: .normal)
        setMaximumTrackImage(empty, for: .normal)
        setThumbImage(Self.idleThumb, for: .normal)
        setThumbImage(Self.activeThumb, for: .highlighted)
        for (index, sublayer) in [track, bufferedTrack, playedTrack].enumerated() {
            sublayer.cornerRadius = 1.5
            sublayer.backgroundColor = UIColor.white.withAlphaComponent([0.18, 0.4, 1][index]).cgColor
            layer.insertSublayer(sublayer, at: UInt32(index))
        }
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("Use init()") }

    func configure(_ configuration: PlayerSeekBar) {
        let seekable = configuration.duration.isFinite && configuration.duration > 0
        if isScrubbing && (self.configuration?.mediaID != configuration.mediaID || !seekable) {
            invalidateInteraction()
        }
        self.configuration = configuration
        isEnabled = seekable
        if !isScrubbing {
            previewTime = PlayerGestureMath.clampedTime(configuration.value, duration: configuration.duration)
            setValue(Float(fraction(previewTime)), animated: false)
        }
        updateAccessibilityValue()
        setNeedsLayout()
    }

    /// A new item or temporarily unknown duration invalidates the old touch.
    /// End the owning SwiftUI preview after updateUIView has returned; publishing
    /// from inside that update would reenter the view's current render pass.
    private func invalidateInteraction() {
        let didEnd = configuration?.onEditingChanged
        isScrubbing = false
        isHighlighted = false
        interactionWidth = nil
        editingRevision &+= 1
        let revision = editingRevision
        deferredEditingEnd?.cancel()
        deferredEditingEnd = Task { @MainActor [weak self] in
            guard let self, !Task.isCancelled, self.editingRevision == revision else { return }
            self.deferredEditingEnd = nil
            didEnd?(false)
        }
    }

    override var intrinsicContentSize: CGSize { CGSize(width: UIView.noIntrinsicMetric, height: 44) }
    override func trackRect(forBounds bounds: CGRect) -> CGRect {
        CGRect(x: bounds.minX + 9, y: bounds.midY - (isScrubbing ? 2 : 1.5),
               width: max(0, bounds.width - 18), height: isScrubbing ? 4 : 3)
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        if isScrubbing, let interactionWidth, interactionWidth != bounds.width {
            cancelInteraction()
        }
        let rect = trackRect(forBounds: bounds)
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        track.frame = rect
        bufferedTrack.frame = CGRect(x: rect.minX, y: rect.minY,
                                     width: rect.width * fraction(configuration?.bufferedTime ?? 0), height: rect.height)
        playedTrack.frame = CGRect(x: rect.minX, y: rect.minY, width: rect.width * CGFloat(value), height: rect.height)
        for sublayer in [track, bufferedTrack, playedTrack] { sublayer.cornerRadius = rect.height / 2 }
        CATransaction.commit()
    }

    override func beginTracking(_ touch: UITouch, with event: UIEvent?) -> Bool {
        beginInteraction(at: touch.location(in: self).x)
    }

    override func continueTracking(_ touch: UITouch, with event: UIEvent?) -> Bool {
        guard isScrubbing else { return false }
        updateInteraction(at: touch.location(in: self).x)
        return true
    }

    override func endTracking(_ touch: UITouch?, with event: UIEvent?) {
        if let touch, isScrubbing { updateInteraction(at: touch.location(in: self).x) }
        finishInteraction()
        super.endTracking(touch, with: event)
    }

    override func cancelTracking(with event: UIEvent?) {
        cancelInteraction()
        super.cancelTracking(with: event)
    }

    @discardableResult
    func beginInteraction(at x: CGFloat) -> Bool {
        guard isEnabled, !isScrubbing, configuration != nil, x.isFinite,
              bounds.width.isFinite, trackRect(forBounds: bounds).width > 0 else { return false }
        deferredEditingEnd?.cancel()
        deferredEditingEnd = nil
        editingRevision &+= 1
        isScrubbing = true
        interactionWidth = bounds.width
        isHighlighted = true
        configuration?.onEditingChanged(true)
        updateInteraction(at: x)
        return true
    }

    func updateInteraction(at x: CGFloat) {
        guard isScrubbing, let configuration else { return }
        let rect = trackRect(forBounds: bounds)
        guard rect.width > 0, x.isFinite else { return }
        let progress = min(1, max(0, (x - rect.minX) / rect.width))
        previewTime = Double(progress) * configuration.duration
        setValue(Float(progress), animated: false)
        configuration.onPreview(previewTime)
        updateAccessibilityValue()
        setNeedsLayout()
    }

    func finishInteraction() {
        guard isScrubbing, let configuration else { return }
        isScrubbing = false
        isHighlighted = false
        interactionWidth = nil
        previewTime = PlayerGestureMath.clampedTime(previewTime, duration: configuration.duration)
        setValue(Float(fraction(previewTime)), animated: false)
        configuration.onCommit(previewTime)
        configuration.onEditingChanged(false)
        updateAccessibilityValue()
        setNeedsLayout()
    }

    func cancelInteraction() {
        guard isScrubbing else { return }
        isScrubbing = false
        isHighlighted = false
        interactionWidth = nil
        previewTime = PlayerGestureMath.clampedTime(configuration?.value ?? 0, duration: configuration?.duration ?? 0)
        setValue(Float(fraction(previewTime)), animated: false)
        configuration?.onEditingChanged(false)
        updateAccessibilityValue()
        setNeedsLayout()
    }

    override func accessibilityIncrement() { accessibleSeek(by: 10) }
    override func accessibilityDecrement() { accessibleSeek(by: -10) }

    private func accessibleSeek(by delta: Double) {
        guard isEnabled, !isScrubbing, let configuration else { return }
        previewTime = PlayerGestureMath.clampedTime(previewTime + delta, duration: configuration.duration)
        setValue(Float(fraction(previewTime)), animated: false)
        configuration.onEditingChanged(true)
        configuration.onPreview(previewTime)
        configuration.onCommit(previewTime)
        configuration.onEditingChanged(false)
        updateAccessibilityValue()
        setNeedsLayout()
    }

    private func fraction(_ time: Double) -> CGFloat {
        guard let duration = configuration?.duration, duration.isFinite, duration > 0, time.isFinite else { return 0 }
        return CGFloat(min(1, max(0, time / duration)))
    }

    private func updateAccessibilityValue() {
        let snapshot = PlayerClockSnapshot(position: previewTime, duration: configuration?.duration ?? 0)
        guard snapshot != accessibilitySnapshot else { return }
        accessibilitySnapshot = snapshot
        accessibilityValue = "\(PlayerGestureMath.timeLabel(snapshot.position))，共 \(PlayerGestureMath.timeLabel(snapshot.duration))"
    }

    private static func thumb(diameter: CGFloat) -> UIImage {
        UIGraphicsImageRenderer(size: CGSize(width: diameter, height: diameter)).image { _ in
            UIColor.white.setFill()
            UIBezierPath(ovalIn: CGRect(x: 0, y: 0, width: diameter, height: diameter)).fill()
        }
    }
}
