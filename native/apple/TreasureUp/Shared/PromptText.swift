import Foundation

/// Apply the app's prompt tone only at presentation boundaries. Source content,
/// API values, navigation titles and action labels must keep their original text.
func appPrompt(_ message: String) -> String {
    let text = message.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !text.isEmpty, !text.hasSuffix("") else { return text }
    return text + ""
}
