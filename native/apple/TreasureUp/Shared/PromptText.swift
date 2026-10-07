import Foundation

/// Apply the app's prompt tone only at presentation boundaries. Source content,
/// API values, navigation titles and action labels must keep their original text.
func appPrompt(_ message: String) -> String {
    let text = message.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !text.isEmpty, !text.hasSuffix("喵～") else { return text }
    // Keep technical English and multiline details intact. Only short Chinese
    // presentation messages receive the local tone, without stacked punctuation.
    guard text.rangeOfCharacter(from: .newlines) == nil,
          text.range(of: "\\p{Han}", options: .regularExpression) != nil else { return text }
    var phrase = text
    while phrase.hasSuffix("。") || phrase.hasSuffix("…") || phrase.hasSuffix(".") {
        phrase.removeLast()
    }
    if ["成功", "完成", "已断开", "过期"].contains(where: { phrase.hasSuffix($0) }) {
        phrase += "了"
    }
    return phrase + "喵～"
}
