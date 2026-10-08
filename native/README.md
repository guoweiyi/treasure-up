# Treasure Up · iOS / iPadOS

原生 Swift 客户端，支持 iPhone / iPad，最低系统为 iOS / iPadOS 18.0。首次打开填写自己的 Treasure Up HTTPS 或 HTTP 服务器根地址；已有用户更新后保留原地址。HTTP 和局域网连接配置见[安装说明](apple/INSTALL.md#连接与更新)。

## 安装

从 [Releases](https://github.com/guoweiyi/treasure-up/releases) 下载 `treasure-up-v版本号-ios-unsigned.ipa`，按照[自签名安装指引](apple/INSTALL.md)使用自己的 Apple 账号签名并安装。它是支持 iPhone 和 iPad 的 arm64 真机包。

## 开发

需要 macOS、完整 Xcode 和 iOS 26.1 或更新 SDK。已提交工程可直接打开，无需 Node、Rust 或 Tauri：

```sh
# 在仓库根目录运行
open native/apple/TreasureUp.xcodeproj
```

选择 `TreasureUp` scheme 和 iPhone / iPad 模拟器运行；真机调试在 Signing & Capabilities 中选择自己的 Team。Bundle ID 为 `com.guoweiyi.treasureup`，签名团队无法使用此 ID 时换成自己的唯一 ID，后续更新保持一致。

修改工程结构或构建设置时，更新 `apple/project.yml`，再用 XcodeGen 重新生成并一同提交工程。App 版本独立于根目录 `release.json` 中的服务端发行版本。

## 构建与测试

在 `native/apple` 目录构建可自签名的 IPA：

```sh
xcodebuild -project TreasureUp.xcodeproj -scheme TreasureUp \
  -configuration Release -sdk iphoneos -destination 'generic/platform=iOS' \
  -derivedDataPath build/device ARCHS=arm64 ONLY_ACTIVE_ARCH=NO \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY= \
  DEVELOPMENT_TEAM= build
python3 scripts/package_device.py
```

产物位于 `build/distribution/`：`ios-unsigned.ipa` 与 `ios-device-build.json`，后者记录源码提交、版本、SDK、架构和校验值。打包脚本拒绝模拟器、签名材料或平台不符的 App；安装仍需用户重新签名。

[iOS Actions](../.github/workflows/ios.yml) 只构建并校验真机 IPA，不运行模拟器测试或生成模拟器包。修改原生功能后，在 Xcode 中选择模拟器并使用 Product → Test 运行现有测试；命令行测试使用 `CODE_SIGN_IDENTITY=- CODE_SIGNING_ALLOWED=YES` 临时签名，以支持 Keychain。

构建成功表示 IPA 编译与打包校验通过，设备上的 HDR、Atmos、自签名安装和后台播放仍需实机验证。原生注册和登录通行密钥尚未实现，目前使用服务器账号密码登录。
