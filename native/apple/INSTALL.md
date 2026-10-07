# 在 iPhone / iPad 安装 Treasure Up

Treasure Up App 连接你自己的服务器，支持 iOS / iPadOS **18.0 及以上**。先完成服务器部署，再安装客户端。首次打开会要求填写服务器地址；已有用户更新后保留原地址。

## 下载什么

打开 [GitHub Releases](https://github.com/guoweiyi/treasure-up/releases)，下载该版本附件中的 `treasure-up-v版本号-ios-unsigned.ipa`。它是为真实 iPhone / iPad 编译的 arm64 Release 包，**安装前需要用自己的 Apple 账号重新签名**。在 Safari 下载后直接点开 IPA 不会完成安装。

同一发布页中的 `ios-device-build.json` 记录源码提交、App 版本、最低系统要求、SDK 与架构；`SHA256SUMS` 用于核对下载完整性。服务端发行版本与 App 内显示的版本可能不同，App 版本独立维护。

Actions 中的 `treasure-up-ios-simulator` 是开发者测试产物，不能安装到真实设备；不要把模拟器 ZIP 改成 `.ipa`。

## 方法一：Sideloadly（Windows / macOS）

1. 从 [Sideloadly 官网](https://sideloadly.io/)安装工具。Windows 所需 Apple 驱动、iTunes / iCloud 版本请按官网当前说明安装。
2. 用 USB 连接并解锁 iPhone / iPad，在设备上选择信任这台电脑。
3. 在 Sideloadly 选择设备，将下载的 `ios-unsigned.ipa` 拖入窗口，填入用于签名的 Apple 账号，开始签名与安装；按工具提示完成验证。
4. 在设备「设置 → 通用 → VPN 与设备管理」中信任自己的开发者身份。若系统要求，前往「设置 → 隐私与安全性 → 开发者模式」开启并按提示重启、确认。
5. 打开 Treasure Up，填写服务器根地址，例如 `https://video.example.com`，连接后使用该服务器的账号登录。

Apple 签名账号只在你选择的签名工具中使用，不需要填入 Treasure Up 后台、GitHub Actions Secrets 或提交到本仓库。

普通免费账号签名通常有效 7 天，到期需要重新签名；Sideloadly 可在电脑与设备连接条件满足时自动续签。升级 IPA 时保持同一个 Apple 账号及工具使用的 Bundle ID，并覆盖安装，避免先卸载。具体限制与续签方式见 [Sideloadly 官方 FAQ](https://sideloadly.io/faq)。

## 方法二：AltStore Classic

1. 按官方 [Windows](https://faq.altstore.io/altstore-classic/how-to-install-altstore-windows) 或 [macOS](https://faq.altstore.io/altstore-classic/how-to-install-altstore-macos) 指引，在电脑安装 AltServer 并为设备安装 **AltStore Classic**。
2. 将本项目 Release 的 IPA 保存到设备的「文件」，在 AltStore Classic 的 My Apps 页面用 `+` 选择它；保持 AltServer 可连接，完成签名安装。
3. 按系统要求开启开发者模式，打开 Treasure Up 并连接自己的服务器。

AltStore Classic 的免费签名同样需要定期刷新，免费账号通常最多同时启用 3 个侧载 App，包含 AltStore 自身。刷新需要满足 AltServer 的网络或 USB 连接条件。详见 [AltStore 使用说明](https://faq.altstore.io/altstore-classic/your-altstore)与 [AltServer 说明](https://faq.altstore.io/altstore-classic/altserver)。本指引使用 Classic 的自签名功能，不是 AltStore PAL 商店上架流程。

## 连接与升级

- 地址填写你部署后能够从设备访问的 **HTTPS 根地址**，不要附加 `/api/v1`、`/login`、访问令牌或账号密码。手机上的 `localhost` 指手机本身，不能用它连接电脑。
- 网站账号由你的 Treasure Up 管理员提供，与 Apple 签名账号无关。是否可以不登录浏览，由服务器的访客访问设置决定。
- 网页能访问而 App 连接失败时，检查证书是否被设备信任、反向代理是否放行 `/api/v1`、服务器公开地址配置是否正确。
- 更新使用同一个签名身份和 Bundle ID。工具自动修改 Bundle ID 时，后续更新也需保持一致；不要手动把其他人的 Team ID 或 Keychain access group 写进 App。
- App 使用系统 Keychain 保存服务器会话。更换签名团队、Bundle ID 或卸载重装后，可能需要重新登录；视频和服务端片单仍在服务器上。网站通行密钥不能通过删除签名权限“修复”，原生通行密钥登录尚未实现。
- 如果提示 App 不再可用，先检查签名是否过期；若提示无法验证完整性，重新下载官方附件，核对 SHA-256，并使用自己的有效签名安装。

## 构建与验证边界

CI 分别执行 iPhone / iPad 模拟器的单元及离线播放器测试，并额外编译真实设备的 arm64 IPA；发布流程核对其平台与源码提交后再附上安装包。IPA 中不包含开发者证书、私钥或 provisioning profile，也不包含服务器账号。

这些检查不代表每台设备上的自签名安装、HDR / Dolby Vision、Atmos 或后台播放都已验收。实际音视频效果仍取决于片源、系统、设备与输出路由。开发者可以从源码用 Xcode 选择自己的 Team 进行签名；见 [工程与测试说明](https://github.com/guoweiyi/treasure-up/blob/main/native/README.md)。

Apple 参考：[开启开发者模式](https://developer.apple.com/documentation/xcode/enabling-developer-mode-on-a-device)、[签名与 provisioning profile 的关系](https://developer.apple.com/documentation/technotes/tn3125-inside-code-signing-provisioning-profiles)。
