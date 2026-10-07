# iPhone / iPad 安装

支持 iOS / iPadOS **18.0 及以上**。先部署 Treasure Up 服务器，再从 [Releases](https://github.com/guoweiyi/treasure-up/releases) 下载 `treasure-up-v版本号-ios-unsigned.ipa`。这是供用户自行签名的真机包，不能直接点击安装。

## 使用 Sideloadly

1. 在 Windows 或 macOS 安装 [Sideloadly](https://sideloadly.io/)，所需 Apple 驱动按官网说明准备。
2. 用 USB 连接并解锁 iPhone / iPad，在设备上信任这台电脑。
3. 在 Sideloadly 选择设备，拖入 IPA，使用自己的 Apple 账号完成签名和安装。
4. 按设备提示信任开发者身份，并在「设置 → 隐私与安全性 → 开发者模式」开启[开发者模式](https://developer.apple.com/documentation/xcode/enabling-developer-mode-on-a-device)。
5. 打开 Treasure Up，填写自己的服务器地址并登录。

免费 Apple 账号签名通常有效 **7 天**。可在 Sideloadly 开启自动刷新，保持电脑与设备满足 USB 或 Wi-Fi 连接条件；到期也可以重新签名安装。详见 [Sideloadly FAQ](https://sideloadly.io/faq)。

## 已使用 AltStore Classic

按官方 [Windows](https://faq.altstore.io/altstore-classic/how-to-install-altstore-windows) / [macOS](https://faq.altstore.io/altstore-classic/how-to-install-altstore-macos) 指引安装 AltServer 与 AltStore Classic。在设备的 My Apps 页面点击 `+`，选择下载的 IPA，并保持 AltServer 可连接。

免费账号同样需要每 7 天刷新，通常最多同时启用 3 个侧载 App，包含 AltStore 本身。参见 [AltStore 使用说明](https://faq.altstore.io/altstore-classic/your-altstore)。

## 连接与更新

服务器地址填写设备可访问的 **HTTPS 或 HTTP 根地址**，例如 `https://video.example.com` 或 `http://192.168.1.10:8000`，不要加 `/api/v1`。未填写协议时默认 HTTPS。手机上的 `localhost` 指手机本身，不能用它连接电脑。局域网站点还需允许 App 的「本地网络」权限。

HTTP 部署需设置 `TREASURE_COOKIE_SECURE=false`，否则系统不会发送标记为 Secure 的登录或播放 Cookie，App 会提示调整配置。公网建议使用 HTTPS；App 不会忽略无效证书或自动降级连接。

网站账号由服务器管理员提供，与 Apple 签名账号无关。App 按网页端访客开关决定是否要求登录，访问受保护内容时自动显示登录入口，并在回到前台时刷新策略。

更新时使用同一个 Apple 账号和 Bundle ID，覆盖安装即可；工具自动修改过 Bundle ID 的，后续也保持一致。更换签名身份或卸载后可能需要重新登录，服务器上的视频和片单不受影响。

需要从源码编译时，见 [开发说明](https://github.com/guoweiyi/treasure-up/blob/main/native/README.md)。
