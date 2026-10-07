import XCTest
@testable import TreasureUp

final class PromptTextTests: XCTestCase {
    func testAddsToneToLocalAndDynamicMessages() {
        XCTAssertEqual(appPrompt("正在加载…"), "正在加载喵～")
        XCTAssertEqual(appPrompt("网络连接已断开。"), "网络连接断开了喵～")
        XCTAssertEqual(appPrompt("Server returned 503"), "Server returned 503")
    }

    func testRepeatedPresentationDoesNotDuplicateSuffix() {
        let prompt = appPrompt("保存成功。")
        XCTAssertEqual(appPrompt(prompt), prompt)
        XCTAssertEqual(appPrompt("保存成功了喵～\n"), prompt)
    }

    func testEmptyAndMultilineMessages() {
        XCTAssertEqual(appPrompt(""), "")
        XCTAssertEqual(appPrompt(" \n "), "")
        XCTAssertEqual(appPrompt("第一行\n第二行"), "第一行\n第二行")
    }
}
