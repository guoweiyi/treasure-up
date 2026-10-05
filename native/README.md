# Treasure Up 原生连接壳

这是独立 Tauri 2 工程，复用远程 Docker 部署的网页。安装包不附带 FFmpeg、语音模型、BT 引擎或数据库；这些任务仍在服务器执行。

## 当前交付范围

| 平台 | `native.yml` 实际运行步骤 | 输出及边界 |
| --- | --- | --- |
| Windows x64 | Node 测试 → Rust 单元测试/编译 → `tauri build --target x86_64-pc-windows-msvc --bundles nsis` | NSIS 安装包；未配置 Authenticode 签名，需要系统 WebView2。 |
| macOS ARM64 / Intel | Node 测试 → 主机 Rust 单元测试 → 分别编译两种目标并打包 | app/DMG，使用 ad-hoc 签名；没有开发者证书、公证或商店发布。Intel 目标单独编译，测试运行在 runner 主机架构。 |
| Android ARM64 | 安装 CI SDK/NDK → 生成 Android 项目 → `tauri android build --debug --apk --target aarch64 --ci` | 调试 APK，不能作为已完成签名的商店发布包。 |
| iOS ARM64 模拟器 | 安装模拟器 Rust 目标/XcodeGen → 生成 iOS 项目 → `tauri ios build --debug --target aarch64-sim --no-sign --ci` | 模拟器 `.app` 的 ZIP；不是可以装到 iPhone 的签名 IPA。 |

本机已执行 `npm test`（7 项，包含连接页初始化/操作竞态、安全边界与 Android 工具链修正回归）和 `npm run build`。本机缺少 Rust/Cargo/MSVC；原生编译由 CI 验证。[0.3.1 发布构建 37188301393](https://github.com/guoweiyi/treasure-up/actions/runs/37188301393) 的五个原生目标均已成功，包含桌面的 6 项 Rust 导航、连接超时和身份探测测试。本轮缓存与 APK 体积调整需由新 CI 验证，不沿用旧构建结果。各系统实机交互、登录、播放及发布签名仍需下述验收。

npm 与 Cargo 锁均已入库。CLI `2.12.1`、Rust `tauri=2.12.1`、`tauri-build=2.7.1` 在 2026-10-04 通过官方 registry 元数据核实。构建以所选 tag/commit 的锁文件为准，生成的 Android/Xcode 工程位于忽略目录 `src-tauri/gen`；发布所需签名材料不得提交。

两项 Rust crate 的官方版本元数据均声明 MSRV `1.90`，已写入 Cargo.toml；CI 更新并选用 stable 后构建，避免 runner 预装 Rust 太旧。CI 现在执行 `cargo test --locked`，Tauri 构建通过尾部 `-- --locked` 传递给 Cargo（移动构建也同样传递）。本地 `dev`、`desktop:build`、`android:build`、`ios:build` 脚本同样自动附加锁定参数，额外 Tauri 选项保持在 Cargo 分隔符之前。

## CI 缓存与产物

`native.yml` 可通过 `workflow_call` 复用，`source_ref` 为空时构建调用方 ref；发布工作流先解析版本 tag，再传入不可变 commit SHA。三个 job 的 checkout 与 concurrency 使用同一个值，避免手工触发把默认分支代码装入旧版本包，也避免 tag 在作业之间移动造成版本不一致。构建权限保持 `contents: read`，checkout 不持久化 Git 凭据。

[Swatinem/rust-cache v2.9.2](https://github.com/Swatinem/rust-cache/commit/6323deb102c322ba6fcbdcafc7e3dddab59af2b6) 固定完整提交 SHA，按桌面 target、Android ARM64、iOS 模拟器分开。Action 还将 Rust 版本、锁文件及 Cargo/Rust 编译环境纳入缓存键，Android 加入 NDK 环境。缓存仅保存依赖与 registry，不缓存工作区自身 crate、Cargo bin、整个 home 或生成的移动工程。只有默认分支或版本 tag 的非 PR 构建可保存，PR 只恢复；GitHub 的分支/merge-ref 缓存隔离仍生效。缓存不含签名材料，未来引入签名时也不得将密钥放入缓存目录。

仅上传明确的 NSIS `.exe`、DMG、Android `.apk` 和 iOS 模拟器 ZIP，不上传整个 target/gen、Cargo.lock、密钥或本地配置。已移除临时用的 Cargo.lock base64 日志；公开依赖锁从相同 tag 的源码获取。上传已压缩安装包时禁用重复 ZIP 压缩以节省 runner 时间，这不改变安装包字节及签名。

Android CI 仍使用 `--debug`，通过 `CARGO_PROFILE_DEV_DEBUG=0` 与 `CARGO_PROFILE_DEV_STRIP=debuginfo` 去掉 Rust DWARF 信息，保留 debug 构建、调试断言与平台临时调试签名；不改成本地开发默认配置。代价是分发 APK 的 Rust 堆栈不含完整源码行号。需要调试符号时，在本地不设置这两个环境变量重新构建。该方案遵循 [Cargo profile 配置](https://doc.rust-lang.org/cargo/reference/profiles.html#debug)，不是签名或商店发布方案。

`scripts/audit_apk.py` 读取最终 APK，校验 ARM64 ELF、拒绝残留 `.debug_*`/`.zdebug_*` 段与常见私钥/配置文件，报告实际包体、每个 `.so` 的压缩前后大小和 SHA-256；不会重写 APK。CI 与 0.3.1 的 **159,797,407 字节**基线比较并输出缩减百分比，实际收益以新 CI 输出为准。检查脚本使用 Python 标准库，有 4 项独立回归，可运行 `python3 -m unittest discover -s test -p 'test_*.py'`。

## 连接与权限

应用始终从随包附带的本地连接页开始，只持久化规范化的服务器 origin。地址只接受 HTTPS；HTTP 例外仅限 localhost/IPv4 或 IPv6 回环，用于本机开发。不支持反向代理子路径，不接受 URL 凭据、query、fragment、本地协议或保留的 Tauri/IPC 地址。移动设备的 localhost 指移动设备自己；移动发布版本的 ATS/网络安全策略仍可能拒绝 HTTP，应连接有效 HTTPS 站点，不要通过允许任意明文流量绕过。

连接表单也接受以 `/api` 或 `/api/v1` 结尾的 API 地址，提交前规范化为同一服务器根地址；不支持任意挂载前缀。进入后可直接浏览视频库，个人星标、进度保存与管理操作再登录。移动播放器使用站内自定义控件；iOS 27 的实现依据与尚需真机验证的项目见 [播放器说明](../frontend/src/player/IOS_NOTES.md)。

连接页在宽屏/iPad 宽度使用介绍与表单双栏，窄屏叠放；四边保留系统安全区，横屏或键盘压缩高度时允许滚动。地址输入保持 16px 字号，输入框和主按钮分别至少 52px、48px，避免把桌面表单直接缩成小触点。这次仅调整本地 HTML/CSS，不增加 IPC、网络例外或权限；CSS 视窗验收不能替代 iPhone/iPad 的键盘、安全区和旋转实测。

2026-10-04 在 Windows Chrome 静态预览中检查了 1024×768 双栏和 390×844 单栏：没有横向溢出，输入框实测 52px 高、主按钮约 49.6px 高、输入字号 16px。浏览器预览正确禁用原生连接操作；这不构成 Tauri/WKWebView 实机连接或 Atmos 播放验证。临时预览服务和标签已关闭，视窗覆盖已恢复。

Rust 在固定 origin 请求 `GET /api/v1/server`，8 秒超时、16 KiB 响应上限、禁止重定向、正常校验证书；只接收 `{application:"treasure-up",api_version:1}`。服务标识用于兼容性识别，不是替代 TLS 的身份凭证。服务器响应从不注入本地 HTML、JavaScript 或 CSP。

验证成功后，同一 WebView 导航到 `/?client=native`，只允许选定 origin 的顶层导航。B 站及其他外链不会进入本地信任域；云存储/CDN 媒体作为网页子资源由正常 WebView 加载，顶层导航策略不是媒体请求代理。

本地 capability 只允许 `load_connection`、`connect_server`、`forget_connection` 三个 command。通过 `AppManifest::commands` 将应用 command 纳入权限控制，没有任何 remote capability、文件系统、shell、HTTP 插件或新窗口权限。每个 command 还检查调用窗口 label 和实际本地页面 URL。**单 WebView 不等于共享权限**：能力依据页面来源区分，本地文档有权限，远程服务器页面没有原生 IPC 权限。远端即使可以看到 Tauri 注入的函数，也没有调用授权。

连接页初始化读取设置时禁用表单，避免旧地址在输入或清除操作后重新填回；读取失败仍可手动连接或清除。连接页可以重试、清除地址并请求系统清理客户端 WebView 登录数据。普通 Treasure Up 登录 cookie 由系统 WebView 管理，不导出到配置文件；客户端没有读取 B 站 Cookie、云桶密钥的原生接口。Tauri 的清理接口没有返回平台异步清理的完成通知，因此提示只确认请求已发出，不宣称登录数据已同步清空；实际清理完成情况仍需各系统验收。清除客户端数据不删除服务器账户，也不是撤销服务器端全部会话。

前端使用 `https://treasure-up.invalid/connect` 作为“切换服务器”链接；Rust 截获并返回本地，不向该域发请求。桌面还有原生菜单及 `Ctrl/Cmd+Shift+C`，网络 offline 事件和 `Shift+Esc` 也返回连接页；初次加载 20 秒未完成则回退。重启总是进入连接页，因此错误地址不会把应用永久困在远程页面。在线网络下的某些 HTTP 错误页可能被 WebView 视作完成加载，此时用桌面菜单或重启恢复。

验证后的连接提交与超时回退在主线程串行处理，回退执行时重新核对请求代次和加载状态；旧超时不能取消已完成或较新的连接。调用 WebView 导航前释放状态锁，避免导航回调再次取锁时死锁。锁定依赖 Wry 0.57.0 的 [WKWebView 实现](https://github.com/tauri-apps/wry/blob/wry-v0.57.0/src/wkwebview/mod.rs) 已在 iOS 设置 `allowsInlineMediaPlayback=true`，页面仍须保留 `playsinline`，该源码核对不等于 iPhone 实测。

同一版本的 [Android WebChromeClient](https://github.com/tauri-apps/wry/blob/wry-v0.57.0/src/android/kotlin/RustWebChromeClient.kt) 会立即关闭 `onShowCustomView` 全屏请求。播放器因此仅在 Tauri 的 `isTauri === true` 标记与 Android UA 同时存在时隐藏系统全屏按钮，保留网页全屏；普通 Android 浏览器、iOS 和桌面端继续按 API 能力显示。识别不依赖 `client=native` 查询参数或会话存储，也不赋予任何 IPC 权限。

桌面原生 `on_new_window` 拒绝弹窗。该 API 在 Android/iOS 不受支持，移动端使用同一个 WebView，不依赖移动多窗口；另外安装固定、无 IPC 的脚本拒绝 `window.open` 与新窗口链接。平台默认弹窗策略、导航拦截和离线返回仍需各系统实机验收，不能将桌面验证当作移动验证。

## 本地开发与构建

只在构建机安装 [Tauri 官方前置依赖](https://v2.tauri.app/start/prerequisites/)：Node、Rust；Windows 还需 C++ Build Tools/Windows SDK/WebView2，macOS 需 Xcode。当前开发过程中没有在用户电脑安装全局 SDK。

```sh
cd native
npm ci --ignore-scripts
npm test
npm run dev
# 桌面发行构建
npm run desktop:build
```

`npm run dev` 显式使用 `--no-dev-server`：本地连接页保持打包资源的 origin，不会被 CLI 默认启动的 127.0.0.1 开发服务器替换。不要为了开发便利放宽原生 command 的来源限制。

Android 构建机需 JDK、Android SDK/Build Tools/NDK，并设置 `ANDROID_HOME`、`NDK_HOME`。工作流在临时 runner 安装这些依赖，生成 ARM64 调试包。其他 ABI 可以显式加入 `--target`，尚未纳入当前矩阵。

已逐项检查发布的 `tauri-cli 2.12.1` crate 模板：compile/target SDK 为整数 37，AGP 为 9.3.1，Gradle wrapper 为 9.6.1，Kotlin 插件为 2.2.10。但 [Google SDK 仓库](https://dl.google.com/android/repository/repository2-3.xml) 当前发布的包名是 `platforms;android-37.0`，没有模板期待的 `platforms;android-37`；第二轮 CI 已复现安装失败。`npm run android:prepare` 因此将生成工程的 compile/target SDK 明确固定为已发布的稳定整数 SDK 36，保持 minSDK 26 不变；脚本只接受已核验的模板形状，可重复执行，未知版本拒绝处理。

依据 [AGP 9.3 官方兼容表](https://developer.android.com/build/releases/agp-9-3-0-release-notes)，使用 JDK 17、Build Tools 36.0.0、NDK 28.2.13676358。已实际读取 [AGP 9.3.1 POM](https://dl.google.com/dl/android/maven2/com/android/tools/build/gradle/9.3.1/gradle-9.3.1.pom)、上述 SDK XML 和 [Gradle 9.6.1 官方 SHA-256](https://services.gradle.org/distributions/gradle-9.6.1-bin.zip.sha256)，确认精确版本存在，并把 wrapper SHA-256 写入生成工程。发布的 `tauri 2.12.1` crate 内 `mobile/android/build.gradle.kts` 自身使用 compileSDK 36/minSDK 21，本项目未引入额外 Tauri Android 插件；无需改写缓存中的依赖。CI 另记录 `sdkmanager --list` 中所选包。模板没有 `libs.versions.toml`，其 `gradle.properties` 中的 `android.builtInKotlin=false`、`android.newDsl=false` 与旧插件 ProGuard 兼容参数保留。

首次 CI 的 Android job 在安装工具阶段报告 `sdkmanager: command not found`。工作流现显式使用固定提交的 [setup-android](https://github.com/android-actions/setup-android) 配置 command-line tools 22.0（15859902）及 PATH，不依赖 runner 预装 SDK。

```sh
rustup target add aarch64-linux-android
npm run tauri -- android init --ci --skip-targets-install
npm run android:prepare
npm run android:build -- --debug --apk --target aarch64 --ci
```

iOS 命令只存在于 macOS CLI。模拟器不需要发布证书；真机、TestFlight 和 App Store 需要开发者账户、有效 team、签名证书与 provisioning profile，并应配置稳定 bundle ID。没有任何这些材料时，只交付模拟器构建，不宣称真机发布完成。

```sh
# macOS + Xcode + XcodeGen
rustup target add aarch64-apple-ios-sim
npm run tauri -- ios init --ci --skip-targets-install
npm run ios:build -- --debug --target aarch64-sim --no-sign --ci
```

自动更新器没有启用。发布前应建立签名、产物校验和更新密钥管理，不能让远程服务器页面直接替换原生二进制。

## 必须补做的平台验收

1. HTTPS 正常站点、证书错误、非 Treasure Up 服务、跨 origin 302、超大 JSON、超时均按预期处理；配置文件只含 origin。
2. 远程页面尝试调用三个 command 必须被拒绝；远程跳本地 URL、`window.open`、`target=_blank`、非 HTTP(S) scheme 不得获得本地权限或打开任意本地资源。
3. 登录、退出、不同服务器切换、离线/服务器关闭、系统返回键与重启恢复；清除后 WebView 登录数据消失。
4. HLS seek、原档与兼容副本、弹幕、全屏、系统音视频能力。WebView 使用系统解码器，不能宣称支持所有 Dolby/HDR/Atmos 路径。
5. 通行密钥独立测试。WebView 能否使用系统凭据与生物识别取决于 OS、WebView 版本及平台关联域要求；密码登录仍是受支持的基础路径。当前壳未配置 Android Digital Asset Links 或 Apple associated domains，也未声称支持所有通行密钥设备。

## 主要依据

- [Tauri capabilities](https://v2.tauri.app/security/capabilities/)：本地/远端能力边界和应用 command 显式权限化。
- [WebviewWindowBuilder](https://docs.rs/tauri/2.12.1/tauri/webview/struct.WebviewWindowBuilder.html)：导航、初始化脚本、页面加载及平台限制。
- [移动多窗口](https://v2.tauri.app/learn/mobile-multiwindow/)：Android Activity/iOS Scene 要求；本壳因此采用单 WebView。
- [Tauri 配置](https://v2.tauri.app/reference/config/)：CSP、capability、平台 bundle 配置。
- [官方 GitHub pipeline](https://v2.tauri.app/distribute/pipelines/github/)：桌面目标与 macOS ad-hoc 签名说明。
- [CLI 2.12.1 iOS build 源码](https://github.com/tauri-apps/tauri/blob/tauri-cli-v2.12.1/crates/tauri-cli/src/mobile/ios/build.rs)：`--no-sign` 与模拟器导出分支，避免依赖文档页面尚未列出的参数。
- [iOS 签名要求](https://v2.tauri.app/distribute/sign/ios/) 与 [Android 签名](https://v2.tauri.app/distribute/sign/android/)：编译与正式发布是不同验收。
