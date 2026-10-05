# Treasure Up · 原生 iOS / iPadOS

这是直接访问现有 Treasure Up `/api/v1` 的 SwiftUI + AVKit 客户端。资料库、个人片单、播放器和管理页面均由原生 Swift 实现，不依赖网页容器、Vue、Rust 或 Tauri 运行时。后端接口与数据库保持现状。

- App Bundle ID：`com.guoweiyi.treasureup`。
- 最低运行系统：iOS / iPadOS 18.0。
- iOS / iPadOS 26 及以上使用系统 Liquid Glass 按钮、玻璃材质和标签栏行为；18–25 使用系统材质及标准按钮作为回退。
- 工程使用 Swift 6 和完整严格并发检查，同时支持 iPhone 与 iPad。
- 当前开发工具为 Xcode 27 / iOS 27 SDK。**SDK 编译、模拟器运行、真机音视频输出是不同的验收项**，各项状态见文末。

原来的跨平台客户端保留在 [`native/src-tauri`](../src-tauri/)；原有 `ios:*` 命令仍属于 Tauri。这个客户端使用 `swift:*` 命令与本目录中的工程，请确认 Xcode 打开的是 `TreasureUp.xcodeproj`。

## 打开和运行

需要 macOS、完整 Xcode，以及至少包含 iOS 26.1 API 的 SDK。仅安装 Command Line Tools 不足以构建此 App。低版本运行回退不代表可以用 iOS 18 SDK 编译源码。

已提交的 Xcode 工程可以直接打开，无需先安装 Node、Rust 或网页依赖：

```sh
# 在仓库根目录运行
open native/apple/TreasureUp.xcodeproj
```

在 Xcode 中选择 `TreasureUp` scheme 和可用的 iPhone / iPad 模拟器，执行 Run。首次启动默认连接 [测试站点](https://test-tp.gwy.fun/)，也可在“设置 → 切换服务器”输入自己的 HTTPS 根地址。地址不带 `/api/v1`，不带子路径、查询参数或账号密码。

资料库支持访客浏览。账户登录后启用个人片单和观看进度，`editor` 与 `admin` 按服务器权限获得对应管理入口。测试账号凭据不写入仓库、构建参数、截图说明或 App 默认配置；请在登录表单中输入。

如果使用已安装的 npm，也可以从仓库根目录运行：

```sh
cd native
npm run swift:xcode
npm run swift:build
```

`swift:build` 使用本地临时签名（ad hoc，`CODE_SIGN_IDENTITY=- CODE_SIGNING_ALLOWED=YES`）构建模拟器 App，输出到 `native/apple/build/`；模拟器不需要配置 Development Team。这不是 App Store 或真机发布签名，模拟器 `.app` / ZIP 不能直接安装到真实 iPhone 或 iPad。

本轮实际运行中，使用 `CODE_SIGNING_ALLOWED=NO` 的模拟器构建写入 Keychain 时返回 `-34018`，导致密码登录无法完成。重新以本地临时签名构建并安装后，Keychain 与登录均正常。需要运行或测试的模拟器 App 请保留临时签名，不要为了跳过真机签名而全局禁用签名。

## 工程生成与版本管理

[`project.yml`](project.yml) 是工程配置来源；[`TreasureUp.xcodeproj`](TreasureUp.xcodeproj/) 及共享 scheme 一并提交，方便克隆后直接打开。生成工具在本机使用 XcodeGen 2.46.0。

添加、删除或移动 Swift 文件，或者修改 target / build settings 后，重新生成工程：

```sh
# 在 native 目录运行；需要本机已有 xcodegen
npm run swift:generate
git diff -- apple/project.yml apple/TreasureUp.xcodeproj
```

也可在本目录执行 `xcodegen generate --spec project.yml`。常规源码修改不需要每次重新生成。避免只在 Xcode 中修改 build settings 而不更新 `project.yml`，下一次生成会覆盖工程中的单独修改。

应忽略 `build/`、模拟器打包文件、`xcuserdata/` 与 `*.xcuserstate` 等本地输出；不要提交 DerivedData、签名证书、provisioning profile 或测试会话 Cookie。

## 测试与命令行构建

先确认当前 Xcode 与可用运行时：

```sh
xcode-select -p
xcodebuild -version
xcrun simctl list devices available
```

不启动模拟器的编译检查，在仓库根目录执行：

```sh
xcodebuild \
  -project native/apple/TreasureUp.xcodeproj \
  -scheme TreasureUp \
  -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath native/apple/build \
  CODE_SIGN_IDENTITY=- CODE_SIGNING_ALLOWED=YES build
```

运行离线单元测试时，使用上一步列出的 **iOS 18 或更新版本**设备 UDID。以下命令在 `native` 目录执行：

```sh
TREASURE_SIMULATOR_ID='替换为可用设备的 UDID'
npm run swift:test -- \
  -destination "platform=iOS Simulator,id=$TREASURE_SIMULATOR_ID" \
  -derivedDataPath apple/build \
  CODE_SIGN_IDENTITY=- CODE_SIGNING_ALLOWED=YES
```

`TreasureUpTests` 包含 API / 数据解码 / 会话隔离，以及字幕、弹幕、断点续播和有限播放恢复策略测试。API 用 `URLProtocol` 模拟响应，不访问正式服务器、不存储测试凭据。可以将设备 UDID 换为 iPad 模拟器重复运行，但单元测试通过不能替代 iPad 窗口布局验收。

`TreasureUpUITests` 是单独的在线冒烟测试。默认跳过；需在 Xcode 的 **Edit Scheme → Test → Arguments → Environment Variables** 中启用 `TREASURE_UI_SMOKE=1`，并选择该测试 target。当前测试依赖测试站资料库中标题含“红豆”的样本，检查目录导航、打开视频及播放器呈现，并保存 XCTest 截图。测试库内容变化时需要更新这个 fixture；它不证明 HDR、Atmos、后台持续播放或所有管理操作已通过。

CI 的 `ios-simulator` job 位于 [原生客户端工作流](../../.github/workflows/native.yml)：选取已安装的 iOS 26.1+ SDK，编译提交的工程，在可用 iPhone 模拟器运行 `TreasureUpTests`，再保存本地临时签名的模拟器 App ZIP。临时签名用于本地运行与 Keychain 访问；该产物不是发布签名，job 不包含真机安装或在线 UI 测试。

## 真机签名与 iPad

1. 在 Xcode 的 Settings → Accounts 登录有权签名的 Apple Developer 账户。
2. 在 `TreasureUp` target 的 Signing & Capabilities 选择自己的 Team，保留 `com.guoweiyi.treasureup`，确认该团队能够使用此标识。如果已有同名 App，需要与现有 App 的签名归属保持一致。
3. 连接并信任设备，按设备提示启用开发者模式，在 Xcode 选择设备后 Run。
4. 正式分发使用 Product → Archive，再由 Organizer 导出或提交；仓库不包含发布证书、私钥或 provisioning profile。

当前未在工程中写死 Development Team。已连接 iPad 并不意味着具备可用签名身份；没有签名配置时，不能把模拟器构建结果视为真机安装成功。

iPad 使用 `NavigationSplitView` 侧栏与独立详情导航，窗口变窄时转换为紧凑导航；目录卡片自适应列数，宽视频详情采用并排信息区域。表单、播放器与片单支持横竖屏和系统窗口尺寸变化。iPad 播放器直接内嵌在视频详情，宽屏将视频与简介 / 评论 / 队列面板并排；展开时收起侧栏和信息区。视频通过稳定布局和 UIKit 容器管理，旋转和返回导航时复用同一 AVPlayerLayer。工程允许 iPad 四个方向，但目前只启用一个 App scene，并未实现独立多窗口资料库会话。

真机验收至少包含：横竖屏、分屏 / 窗口缩放、较大 Dynamic Type、VoiceOver 焦点、外接键盘导航、播放器全屏与画中画返回。现有系统控件和可访问性标签提供基础支持，尚未完成这些项目的全面辅助功能审计。

## 代码结构

| 目录 | 职责 |
| --- | --- |
| [`TreasureUp/App`](TreasureUp/App/) | App 环境、iPhone 标签导航、iPad 侧栏、服务器连接、登录与设置 |
| [`TreasureUp/Core`](TreasureUp/Core/) | Codable 模型、动态 JSON、原生 URLSession、服务端会话与 Keychain Cookie |
| [`TreasureUp/Catalog`](TreasureUp/Catalog/) | 视频、创作者、收藏夹、评论和个人片单 |
| [`TreasureUp/Playback`](TreasureUp/Playback/) | AVPlayer / AVPlayerLayer、原生手势控件与 AVKit PiP、播放会话、进度、音频路由、字幕及弹幕时间轴 |
| [`TreasureUp/Admin`](TreasureUp/Admin/) | 管理列表与表单、采集策略、后台任务、存储副本、账户安全 |
| [`TreasureUp/Shared`](TreasureUp/Shared/) | 缩略图、卡片、分页、错误状态和系统版本外观回退 |
| [`TreasureUpTests`](TreasureUpTests/) | 本地回归测试 |
| [`TreasureUpUITests`](TreasureUpUITests/) | 需显式启用的在线 UI 冒烟测试 |

`APIClient` 是主线程上的可观察状态。模型字段使用 `coverUrl`、`sourceVariantId` 等大小写，与 `convertFromSnakeCase` 对应；管理表单使用 `JSONValue`，字典中的后端原始 `snake_case` 字段不会被转换。

连接先检查 `/server` 的应用标识和 API 版本，再通过 `/auth/me` 恢复身份与 CSRF。写入自动附加当前服务器的 `Origin` 和 `X-CSRF-Token`。请求不依赖 WebKit Cookie，也不混用系统全局 Cookie 仓库。

账户 Cookie 与访客播放 Cookie 按服务器隔离存入设备 Keychain；地址保存在 UserDefaults，密码与 CSRF 不持久化。身份 / 服务器变化使旧请求失效，播放进度与队列同样需要跟随身份清理。外部签名媒体地址不携带本站 Cookie；媒体跨源重定向移除认证头，禁止把写请求重定向到其他来源。

## 与现有前端的覆盖关系

下表描述源码中已实现的入口，不代表所有操作均已在远端执行验收。管理权限仍由后端最终校验。

| 原有功能 | 原生实现与边界 |
| --- | --- |
| 服务器连接与账户 | 根地址验证、访客浏览、密码登录、会话恢复、退出与切换服务器 |
| 资料库 | iPhone 双栏（辅助功能大字号回退单栏）、iPad 自适应视频卡片、查询、排序、标签筛选、分页、视频详情与分 P |
| 收藏夹与 UP 主 | 收藏来源列表、作者列表、对应视频筛选和作者资料 |
| 星标与个人片单 | 稍后看 / 星标、自定义片单、加入或移出、备注、观看状态、条目移动及片单编辑删除 |
| 归档评论 | 视频页内文字、作者、点赞数、附图缩放、就地展开回复、按热度 / 时间排序、查询和分页；未完整复刻网页的内联表情排版 |
| 播放 | 内嵌原生 HLS / 文件流、分 P、完整分页队列、倍速、拖动进度、横滑 seek、双击 ±15 秒、长按临时 2×、清晰度 / 音轨选择、手动分发节点选择与断点进度 |
| 字幕与弹幕 | 系统内嵌字幕；独立字幕和原生弹幕叠层、弹幕字号 / 透明度、按时间轴同步 |
| 系统媒体能力 | 同页展开、AVKit 画中画、AirPlay、锁屏 / 控制中心信息、远程播放控制、音频中断和输出变化处理 |
| 内容整理 | 视频标题 / 简介 / 标签 / 备注与 UP 主资料原生编辑表单 |
| 自动采集 | 采集账号、来源发现、来源策略、定时开关、增量 / 全量检查、检查历史与任务详情 |
| 任务与系统配置 | 新建任务、可用任务控制、采集节奏与预算、展示配置、统计更新、播放分发配置 |
| 存储维护 | 本地 / S3 / OSS 配置、能力探测、迁移、视频资产同步、副本停用 / 恢复 / 永久删除、分片与兼容版准备 |
| 访问与审计 | 用户角色 / 停用 / 密码管理、操作记录、备份记录与手动备份入口 |
| 浏览器采集集成 | 采集令牌创建、策略调整与撤销；浏览器脚本本身仍运行在受支持的浏览器中 |
| 通行密钥 | 已注册密钥查看与撤销；原生注册、原生通行密钥登录尚未实现 |

原生通行密钥还需要签名 App 的 Associated Domains entitlement、站点 AASA 配置，以及与服务器 RP ID / origin 一致的 AuthenticationServices 流程。当前不把密码登录包装成通行密钥，也不声称已经完成这些部署项。

## HDR、Dolby Vision 与 Dolby Atmos

播放器采用系统 `AVPlayer` / `AVPlayerLayer`，画中画使用公开 `AVPictureInPictureController`，音频会话为电影播放类别并声明支持多声道。播放请求沿用后端已保存的原档、HLS 分片与媒体属性，不在 App 中进行视频转码，也不对 Atmos 音轨施加网页式 Web Audio 增益处理。

“Dolby Vision / HDR / Dolby Atmos 原档”标记来自服务端媒体属性，表示已保存内容的特征。播放器同时展示系统的 HDR 播放条件、当前音频输出及系统报告的音频渲染模式。**HDR 播放条件为真不等于当前画面已实际输出 HDR；原档具有 Atmos 也不等于当前输出设备正在渲染 Atmos。** 实际结果取决于片源编码、设备、显示器、系统设置和音频路由，需真机验证。

播放恢复有明确边界：原生 HLS 解码失败可尝试同一来源的文件直读；网络失败可重新取得播放地址，避免无限重试。不会自动改成另一个视频来源或把原始 Atmos 音轨替换为 AAC。若服务器已有符合来源关联的“保留视频、仅转换 AAC 立体声音频”副本，用户可以手动选择，并明确看到该副本不含 Atmos。

独立字幕与弹幕是 App 叠层，在 App 内与全屏显示。画中画和 AirPlay 接收端不包含这些叠层；媒体自身的字幕轨道由系统处理。当前没有离线媒体下载、离线资料库、客户端转码或客户端 Dolby 认证声明。

## 后台行为

Info.plist 声明音频后台模式，播放器处理锁屏信息、远程指令、音频中断、路由变化以及进度同步。用户可以关闭“允许后台继续播放”。这项配置并不保证系统在所有低电量、资源压力或路由条件下永不中断，长时间播放和画中画恢复仍需设备验收。

“管理中心”的归档、扫描、备份、迁移和分片任务在服务器执行，App 提交操作并查询进度。退出 App 后，服务器任务可以继续；本次没有把服务端采集器移到 iOS 后台，也没有增加 BGProcessingTask 或后台下载服务。

## 播放页与队列整改（2026-10-05）

- 底部仅保留资料库、收藏与订阅、UP 主、设置四个入口；已有个人片单移到设置中的“收藏片单”，没有删除服务端收藏数据。
- 视频页顶部直接播放，简介、评论和队列在同页切换；UP 主简介直接显示在作品列表上方，关于页和视频详情提供项目 GitHub 链接。
- 倍速 / 画质使用就地菜单；横滑显示目标时间，抬手后 seek；双击左右区域退 / 进 15 秒；长按临时 2×，松开恢复原速。VoiceOver 保留可见控件和可调整进度。
- 队列保留当前项与已播放条目，支持任意选播、上一 / 下一、拖动 / 菜单排序、移除与清空。搜索、作者、合集、个人片单的范围与排序随入口传入，后台获取全部分页；失败不删除队列项，旧请求不能覆盖新队列。
- 默认自动衔接分 P，整个视频播完暂停；可选单视频循环或列表连播。关闭“自动衔接分 P”则每 P 结束均暂停。模式按服务器与账户分别保存。
- 设置 → 播放 → 导出播放诊断可分享当前进程最近 80 条启播、切轨、音频会话与路由事件；仅保存在内存，不含 URL、Cookie、账号或密码，退出身份时清空。复现声音异常后立即导出，勿先停止并重启 App。
- 《红豆》音爆调查及采样边界见 [调查报告](docs/audio-investigation.md)。已测原档 / AAC / Apple 离线解码没有找到固定首帧音爆，仍需 iOS 真机复现，不能宣称根因已经修复。播放器减少重复 AudioSession 配置，并隔离旧 AVPlayerItem 的迟到结束 / 失败事件，不自动降为 AAC 或添加淡入掩盖噪声。

## 交互与状态修复（2026-10-05 第二轮）

- 简介 / 评论 / 队列的切换栏固定在播放器下方，已打开的栏目保留滚动位置、评论查询和展开回复；离开评论或展开播放器会收起搜索键盘。
- 导航中被覆盖的视频页保存自己的视频快照，返回时不会误显示另一页刚开始播放的内容；连播后停止仍保留最后观看的视频。迷你播放器打开的页面在停止后自动关闭，避免出现空白页。
- 联合投稿作者去重并合并署名角色，标题提前显示；详情接口未提供作者归档计数时，不再显示误导性的“0 个已保存视频”。
- 再次点当前队列项或当前分 P 继续原会话，不会从 P2 跳回 P1。启播时显式拖动的位置优先于观看历史；历史读取只有有限等待窗口。播完后点击播放会从头重播。
- 搜索、排序、刷新和翻页使用请求归属检查，旧响应不能覆盖新条件；翻页去重、空页停止，失败重试保留已有结果。标签输入完成后统一应用，可取消编辑。
- 播放器设置面板阻断底层快进手势，横滑先确定方向；进度条触控高度至少 44pt，窄屏与大字号将次要选项放入更多菜单。切入后台结束临时倍速，离开视频页暂停不可见弹幕动画。
- 评论附图失败后可重试，缩放与滚动可以同时使用；大字号下筛选和评论标题改为纵向，回复减少缩进。

## 当前验收记录

记录日期：2026-10-05。下表原有 UI 记录来自第一轮手动计算机操作（CUA）；整改后的内嵌播放器需独立验收，不能沿用旧播放器结果。在线 XCTest UI 冒烟测试本轮没有执行。以下状态只对应已取得的证据。

| 项目 | 当前状态 |
| --- | --- |
| Core Swift 6 严格并发类型检查 | **通过**；包含 Observation 宏编译 |
| 集成单元测试 | **通过，50 项 / 0 失败（11 项 Core + 8 项 Playback + 25 项 Queue + 6 项 PlayerControls）**；包含当前队列项不重建会话、P2 重试、启播拖动优先于历史、重播期间迟到暂停回调与再次定位，以及手势方向 / 进度边界回归 |
| 完整 iOS App 与测试 target | **通过**；Xcode 27 SDK 下 iOS Simulator 构建及集成测试成功；generic iOS 设备架构编译也取得 `BUILD SUCCEEDED`，后者仅编译且未执行真机安装 |
| iPhone 16 Pro / iOS 18.5 模拟器：访客资料库 | **通过手动验收**；访客目录、视频详情、标记为 Dolby Vision 的 P1 播放和切换到 HDR P2 均实际运行；另确认迷你播放器没有遮挡标签栏；这不证明模拟器输出 Dolby Vision / HDR |
| iPhone 16 Pro / iOS 18.5 模拟器：账户与管理 | **通过手动验收**；临时签名构建的密码 UI 登录成功，重启后仍恢复管理员会话，退出登录也已验证；进入管理中心实际读取 9 个视频、17 位 UP 主、2 个收藏夹及 27.32 GB 统计；没有在文档保存凭据 |
| iPad Pro 11-inch (M4) / iPadOS 18.5 模拟器 | **通过手动验收**；目录、侧栏、横屏双栏详情，以及修正 AVKit 容器后的 HDR P2 实际点播、前进 15 秒、播放中横竖屏切换与右侧分集面板均已确认；分屏 / 窗口缩放仍需进一步验收 |
| 本轮整改后的界面与手势 | **手动验收待补**；macOS 锁屏且自动解锁失败，CUA 无法操作。新代码通过编译与下述单元测试；第一轮旧界面的运行结果不替代本轮验证。 |
| 在线 `TreasureUpUITests` | **本轮未执行**；上述 UI 结果来自手动 CUA，不是自动化 UI 测试结果 |
| iOS / iPadOS 26 或 27 运行时 | **未验证**；当前没有可用的 26 / 27 模拟器运行时，不将 27 SDK 编译视为运行验证 |
| 已连接 iPad 27 真机 | **未安装验收**；当前没有可用签名身份 |
| HDR / Dolby Vision 实际屏幕输出 | **未验证：需要匹配片源和支持的真实设备 / 显示器** |
| Atmos / 空间音频实际渲染 | **未验证：需要匹配片源和支持的真实输出路由** |
| 后台长时播放 / 锁屏控制 / PiP / AirPlay | 已实现原生接入；**设备集成行为未验证** |
| 远端管理写操作全流程 | **未全面验收**；尤其是备份、迁移、永久删除及权限变更，不能由读取接口或编译成功推定通过 |
| CI iOS job | [7cbc83b 的远端运行通过](https://github.com/guoweiyi/treasure-up/actions/runs/37314216823)，Xcode 26.6 / iOS 26.5 SDK 编译与 40 项测试通过；本轮体验修复的远端结果需单独确认 |

交付或发布前，应把上表中的“未验证”替换为具体设备、系统版本、构建版本及运行证据；保留尚未取得证据的限制说明。

## Apple 官方参考

- [Adopting Liquid Glass](https://developer.apple.com/documentation/technologyoverviews/adopting-liquid-glass)：系统组件与玻璃材质的适配。
- [AVPlayerLayer](https://developer.apple.com/documentation/avfoundation/avplayerlayer)：系统原生视频呈现。
- [Adopting Picture in Picture in a Custom Player](https://developer.apple.com/documentation/avkit/adopting-picture-in-picture-in-a-custom-player)：自定义原生控件的公开 PiP 接口。
- [AVURLAssetHTTPCookiesKey](https://developer.apple.com/documentation/avfoundation/avurlassethttpcookieskey)：向媒体资源传递适用的 HTTP Cookie。
- [AVAudioSession.renderingMode](https://developer.apple.com/documentation/avfaudio/avaudiosession/renderingmode-swift.property)：读取系统音频渲染模式；部分路由或未活动会话会返回 `notApplicable`，不能当作通用 Atmos 检测器。
- [Incorporating HDR video with Dolby Vision into your apps（PDF）](https://developer.apple.com/av-foundation/Incorporating-HDR-video-with-Dolby-Vision-into-your-apps.pdf)：Apple 的 Dolby Vision / HDR 播放、设备能力与元数据说明。
