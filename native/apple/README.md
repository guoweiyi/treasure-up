# Treasure Up · 原生 iOS / iPadOS

这是直接访问现有 Treasure Up `/api/v1` 的 SwiftUI + AVKit 客户端。资料库、个人片单、播放器和管理页面均由原生 Swift 实现，不依赖网页容器、Vue、Rust 或 Tauri 运行时。后端接口与数据库保持现状。

- App Bundle ID：`com.guoweiyi.treasureup`。
- 最低运行系统：iOS / iPadOS 18.0。
- iOS / iPadOS 26 及以上使用系统 Liquid Glass 按钮、玻璃材质和标签栏行为；18–25 使用系统材质及标准按钮作为回退。
- 工程使用 Swift 6 和完整严格并发检查，同时支持 iPhone 与 iPad。
- 当前开发工具为 Xcode 27 / iOS 27 SDK。**SDK 编译、模拟器运行、真机音视频输出是不同的验收项**，各项状态见文末。
- 本机已完成开发签名，并在 iPad Air 11-inch (M3) / iPadOS 27.0 真机安装、启动 App，确认横屏双栏视频页与基础播放画面；HDR、Atmos 和后台行为仍需分别验收。

客户端目录现在只保留本原生工程。直接使用 Xcode / xcodebuild，无需 npm 或跨平台工具链；入口为 `TreasureUp.xcodeproj`。

## 打开和运行

需要 macOS、完整 Xcode，以及至少包含 iOS 26.1 API 的 SDK。仅安装 Command Line Tools 不足以构建此 App。低版本运行回退不代表可以用 iOS 18 SDK 编译源码。

已提交的 Xcode 工程可以直接打开，无需先安装 Node、Rust 或网页依赖：

```sh
# 在仓库根目录运行
open native/apple/TreasureUp.xcodeproj
```

在 Xcode 中选择 `TreasureUp` scheme 和可用的 iPhone / iPad 模拟器，执行 Run。首次启动默认连接 [测试站点](https://test-tp.gwy.fun/)，也可在“设置 → 切换服务器”输入自己的 HTTPS 根地址。地址不带 `/api/v1`，不带子路径、查询参数或账号密码。

资料库支持访客浏览。账户登录后启用个人片单和观看进度，`editor` 与 `admin` 按服务器权限获得对应管理入口。测试账号凭据不写入仓库、构建参数、截图说明或 App 默认配置；请在登录表单中输入。

命令行构建和测试使用下方的 `xcodebuild` 命令。模拟器保留本地临时签名（ad hoc，`CODE_SIGN_IDENTITY=- CODE_SIGNING_ALLOWED=YES`），无需 Development Team；模拟器 `.app` / ZIP 不能直接安装到真实 iPhone 或 iPad。

本轮实际运行中，使用 `CODE_SIGNING_ALLOWED=NO` 的模拟器构建写入 Keychain 时返回 `-34018`，导致密码登录无法完成。重新以本地临时签名构建并安装后，Keychain 与登录均正常。需要运行或测试的模拟器 App 请保留临时签名，不要为了跳过真机签名而全局禁用签名。

## 工程生成与版本管理

[`project.yml`](project.yml) 是工程配置来源；[`TreasureUp.xcodeproj`](TreasureUp.xcodeproj/) 及共享 scheme 一并提交，方便克隆后直接打开。生成工具在本机使用 XcodeGen 2.46.0。

添加、删除或移动 Swift 文件，或者修改 target / build settings 后，重新生成工程：

```sh
# 在仓库根目录运行；需要本机已有 xcodegen
xcodegen generate --spec native/apple/project.yml
git diff -- native/apple/project.yml native/apple/TreasureUp.xcodeproj
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

运行离线单元测试时，使用上一步列出的 **iOS 18 或更新版本**设备 UDID。以下命令在仓库根目录执行：

```sh
TREASURE_SIMULATOR_ID='替换为可用设备的 UDID'
xcodebuild -project native/apple/TreasureUp.xcodeproj -scheme TreasureUp \
  -destination "platform=iOS Simulator,id=$TREASURE_SIMULATOR_ID" \
  -derivedDataPath native/apple/build -parallel-testing-enabled NO \
  -collect-test-diagnostics never -only-testing:TreasureUpTests \
  -only-testing:TreasureUpUITests/OfflinePlayerInteractionTests \
  CODE_SIGN_IDENTITY=- CODE_SIGNING_ALLOWED=YES test
```

`TreasureUpTests` 包含 API / 数据解码 / 会话隔离，以及字幕、弹幕、断点续播、全屏呈现、连续窗口缩放和有限播放恢复策略测试。API 用 `URLProtocol` 模拟响应，DEBUG 测试宿主不启动真实服务器会话恢复，不访问正式服务器、不存储测试凭据。可以将设备 UDID 换为 iPad 模拟器重复运行，但本地回归不能替代真机音视频与触控验收。

`TreasureUpUITests/OfflinePlayerInteractionTests` 使用仅在 DEBUG 模拟器启用的离线入口，现场生成小型 H.264 视频并交给真实 AVPlayer；API 请求全部由 URLProtocol 拦截。覆盖连续暂停 / 恢复、原生进度条、隐藏控件时双击与横向拖动、就地倍速面板、全屏进入 / 返回、竖视频控件与 iPad 侧栏缩放。可在上面的 `xcodebuild test` 命令中指定 `-only-testing:TreasureUpUITests/OfflinePlayerInteractionTests` 单独执行，不需测试站或账号。

`TreasureUpUITests/NativeSmokeTests` 是另一个在线冒烟测试，默认跳过；需在 Xcode 的 **Edit Scheme → Test → Arguments → Environment Variables** 中启用 `TREASURE_UI_SMOKE=1`。它依赖测试站资料库中标题含“红豆”的样本，检查目录导航、打开视频及播放器呈现，并保存 XCTest 截图。测试库内容变化时需要更新这个 fixture；它不证明 HDR、Atmos、后台持续播放或所有管理操作已通过。

[iOS 客户端工作流](../../.github/workflows/ios.yml) 选取已安装的 iOS 26.1+ SDK，在 iPhone / iPad 模拟器分别运行单元测试和离线播放器 UI 回归，保存各自的测试结果，并仅上传一份临时签名的模拟器 App ZIP。工作流不安装 Node / Rust / Android 工具链，不访问 API 测试站，不包含真机安装或在线 UI 测试。

## 真机签名与 iPad

1. 在 Xcode 的 Settings → Accounts 登录有权签名的 Apple Developer 账户。
2. 在 `TreasureUp` target 的 Signing & Capabilities 选择自己的 Team，保留 `com.guoweiyi.treasureup`，确认该团队能够使用此标识。如果已有同名 App，需要与现有 App 的签名归属保持一致。
3. 连接并信任设备，按设备提示启用开发者模式，在 Xcode 选择设备后 Run。
4. 正式分发使用 Product → Archive，再由 Organizer 导出或提交；仓库不包含发布证书、私钥或 provisioning profile。

仓库中的工程不固定 Development Team。本机已使用 Personal Team 完成开发签名，并通过 `devicectl` 在 iPad Air 11-inch (M3) / iPadOS 27.0 安装、启动 App；账户信息、证书和 provisioning profile 保留在本机，不写入仓库。其他设备仍需完成各自的信任与开发者模式配置，模拟器构建产物不能用于真机安装。

iPad 使用 `NavigationSplitView` 侧栏与独立详情导航，窗口变窄时转换为紧凑导航；目录卡片自适应列数，宽视频详情采用并排信息区域。表单、播放器与片单支持横竖屏和系统窗口尺寸变化。iPad 播放器直接内嵌在视频详情，宽屏将视频与简介 / 评论 / 队列面板并排；全屏通过独立的 UIKit fullScreen 呈现覆盖整个 App 窗口，并根据视频方向请求系统旋转。普通详情按侧栏变化后的可用区域计算等比视频矩形，控制栏紧贴视频；旋转和返回时复用同一 AVPlayerLayer。iPad 系统多窗口模式下全屏范围为当前 App 窗口。工程允许 iPad 四个方向，但目前只启用一个 App scene，并未实现独立多窗口资料库会话。

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
| [`TreasureUpUITests`](TreasureUpUITests/) | 离线播放器触控回归，以及需显式启用的在线 UI 冒烟测试 |

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
| 系统媒体能力 | 原生全屏、AVKit 手动画中画、AirPlay、锁屏 / 控制中心信息、远程播放控制、音频中断和输出变化处理 |
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
- 导航中被覆盖的视频页保存自己的视频快照，返回时不会误显示另一页刚开始播放的内容；连播后停止仍保留最后观看的视频。返回资料库或离开视频详情时停止播放并取消未完成的启播请求，不再留下迷你播放器；关闭全屏返回原详情时继续原播放会话。
- 联合投稿作者去重并合并署名角色，标题提前显示；详情接口未提供作者归档计数时，不再显示误导性的“0 个已保存视频”。
- 再次点当前队列项或当前分 P 继续原会话，不会从 P2 跳回 P1。启播时显式拖动的位置优先于观看历史；历史读取只有有限等待窗口。播完后点击播放会从头重播。
- 搜索、排序、刷新和翻页使用请求归属检查，旧响应不能覆盖新条件；翻页去重、空页停止，失败重试保留已有结果。标签输入完成后统一应用，可取消编辑。
- 播放器设置面板阻断底层快进手势，横滑先确定方向；进度条触控高度至少 44pt，窄屏与大字号将次要选项放入更多菜单。切入后台结束临时倍速，离开视频页暂停不可见弹幕动画。
- 评论附图失败后可重试，缩放与滚动可以同时使用；大字号下筛选和评论标题改为纵向，回复减少缩进。

## 暂停、全屏与侧栏修复（2026-10-06）

- 播放 / 暂停按用户当前播放意图立即切换，不等待 AVPlayer 的异步状态回报；缓冲、启动和快速连续点击也能暂停。
- 关键按钮使用完整 44pt 点击区域；控制栏和进度条与视频滑动 / 双击 / 长按区域分开，按住按钮时阻止定时隐藏。
- 内嵌视口已经位于导航安全区内，不再重复加上状态栏 / 导航栏边距；全屏仍保留设备安全区。控件与手势使用相同边距，防止按钮和可触控区域错位。
- 全屏使用原生 fullScreen 控制器和系统方向请求，横视频转横屏、竖视频保持竖屏；退出全屏把同一渲染层交还内嵌播放器。
- 侧栏变化只改变可用区域与坐标，不重建播放器；UIKit 容器与信息区域分别约束，避免相互覆盖。竖视频的控制区保留栏宽，影像在内部等比显示，避免暂停和全屏按钮被窄画面裁掉；超宽视频也保留三行控件所需的高度。
- 弹幕 / 字幕解码调度移出主线程，叠层保留单个宿主；切换到已选择的画质或线路不重复请求。
- App 自有提示统一追加“喵～”；视频标题、简介、评论、后端数据和导航名称保持原文。

## 本地流畅度优化（0.4.1 / build 2）

- 4 Hz 播放进度只驱动滑条与时钟小视图，拖动预览单独观察；菜单、视频容器不再跟随每次进度重算。时钟按整秒生成文字，抬手才提交一次媒体定位。
- 弹幕复用当前可见文字的 `CATextLayer` 与阴影缓存，每帧仅移动位置；缓存随离场删除，最多保留调度器允许的 8 条。字幕独立以 10 Hz 查询，暂停、后台或离开页面时停止动画更新。原生 `AVPlayerLayer` 的 HDR / Dolby 音视频路径不经过截图或图像滤镜。
- 缓冲范围从每秒 4 次读取改为每秒 1 次；相同状态不重复发布。锁屏信息仅在状态、元数据或时间偏移变化时提交，系统依据播放速率自行推进时间；显式定位仍立即同步。
- 相同倍率和偏好不重复写入 UserDefaults，切换 / 停止时取消旧封面与旧播放请求；队列上下项检查移除全列表临时 ID 数组。
- API 列表与评论 JSON、字幕和弹幕解码移到后台任务。解码结束再次检查取消与会话版本，退出登录或切换服务器期间的旧结果不能进入新页面。
- 离线回归包含十万条弹幕 / 字幕：同一批 8 条弹幕连续 120 帧只准备 8 次文字，而不是重复 960 次；这是排版次数与缓存上限验证，不是硬件 FPS 或“零卡顿”保证。

## 播放器交互打磨（0.4.2 / build 3）

- 倍速、画质和播放选项改为播放器内的原生面板，选择后回到画面；关闭面板、取消操作或退出 AirPlay 选择器后恢复正常隐藏计时。短于 350ms 的缓冲不闪现加载提示。
- 进度条使用 44pt 原生触控区域，支持点选、连续拖动、缓冲轨道和 VoiceOver 十秒步进。拖动期间播放时钟不能拉回滑块，松手只提交一次定位，取消则恢复实际进度；横向画面拖动同步预览时间与滑块。
- 画面单击、双击和长按使用持续存在的 UIKit 手势识别器。中央画面双击前后跳转 15 秒；顶部与底部控件保留独立触控区域，按住按钮、拖动滑块及打开选择器时取消旧的隐藏任务。
- 定位请求采用单一工作任务追踪最新目标，每个过期的在途定位最多取消一次。定位过程中的暂停、恢复与新目标按最新用户意图执行，旧回调不能覆盖新视频或新定位。
- 定位到末尾主动结束或按队列模式继续，暂停状态下跳到末尾不会自动换片。回拖使旧缓冲超时任务失效；历史只保存已确认的位置，不把失败的跳转目标当作观看进度。
- 全屏进出由 UIKit 完成回调串行管理，快速关闭、退出中再次展开和其他弹层占用都可收敛；实际视频尺寸更新时同步调整方向。页面销毁时只停止它仍持有的视频，不影响后来打开的播放页。
- 保留独立时钟刷新、弹幕图层复用和后台解码；新面板不订阅每次播放进度。离线测试使用生成的本地媒体和真实 AVPlayer，不访问关闭中的 API 站。

## 当前验收记录

记录日期：2026-10-06。下表中的手动设备记录保留自早期构建；最新一次暂停、全屏和侧栏布局修复按用户要求停止真机交互测试，仅报告本地回归与构建结果，不把早期截图当作最新修复已通过的证据。在线 XCTest UI 冒烟测试本轮没有执行。以下状态只对应已取得的证据。

0.4.2（build 3）本轮仅完成本地代码、模拟器回归和 Release 开发签名构建，没有重新安装或操作真机。API 站关闭期间未访问站点，网络端到端播放与真实设备流畅度仍待恢复条件后验证。

| 项目 | 当前状态 |
| --- | --- |
| Core Swift 6 严格并发类型检查 | **通过**；包含 Observation 宏编译 |
| 集成单元测试 | **通过，iOS / iPadOS 18.5 模拟器各 118 项 / 0 失败**；包含定位取消与末尾状态、暂停时保存实际进度、旧缓冲超时失效、原生触控、全屏快速进出与销毁、宿主连续缩放，以及后台解码和十万条弹幕 / 字幕缓存回归 |
| 完整 iOS App 与测试 target | **通过**；Xcode 27 SDK 下 iOS Simulator 构建及集成测试成功；generic iOS 设备架构编译也取得 `BUILD SUCCEEDED`；开发签名与真机运行证据另列于下方 |
| iPhone 16 Pro / iOS 18.5 模拟器：访客资料库 | **通过手动验收**；访客目录、视频详情、标记为 Dolby Vision 的 P1 播放和切换到 HDR P2 均实际运行；该记录属于早期构建；后续已移除迷你播放器；这不证明模拟器输出 Dolby Vision / HDR |
| iPhone 16 Pro / iOS 18.5 模拟器：账户与管理 | **通过手动验收**；临时签名构建的密码 UI 登录成功，重启后仍恢复管理员会话，退出登录也已验证；进入管理中心实际读取 9 个视频、17 位 UP 主、2 个收藏夹及 27.32 GB 统计；没有在文档保存凭据 |
| iPad Pro 11-inch (M4) / iPadOS 18.5 模拟器 | **通过手动验收**；目录、侧栏、横屏双栏详情，以及修正 AVKit 容器后的 HDR P2 实际点播、前进 15 秒、播放中横竖屏切换与右侧分集面板均已确认；分屏 / 窗口缩放仍需进一步验收 |
| 最新暂停 / 全屏 / 侧栏修复 | **真机交互未复验**；用户要求停止真机操作。新增本地 UIKit 呈现与返回、SwiftUI 宿主连续缩放、播放意图状态回归；不宣称最新按钮触控已获真机验收 |
| 离线 iPhone UI 点击回归 | **4 项通过 / 0 失败，iOS 18.5**；另 1 项 iPad 专用侧栏测试按条件跳过。本地媒体驱动真实 AVPlayer，验证点选进度、隐藏控件时双击、横向拖动、倍速面板关闭后自动隐藏、按住暂停、连续暂停 / 恢复及横竖视频全屏进出 |
| 离线 iPad UI 点击回归 | **5 项通过 / 0 失败，iPadOS 18.5**；侧栏收放与缩放后点击、全窗口呈现与返回、竖视频控件、按住暂停超过隐藏阈值、原生进度条 / 双击 / 横向拖动、倍速面板均通过；未把 iPadOS 18 多任务窗口行为视为 iPadOS 26 / 27 已验证 |
| 在线 `NativeSmokeTests` | **本轮未执行**；早期资料库记录来自手动 CUA；本轮自动化交互结果来自上述离线测试 |
| iOS / iPadOS 26 或 27 运行时 | **iPadOS 27.0 真机已运行**；当前没有可用的 26 / 27 模拟器运行时，不将 27 SDK 编译视为模拟器运行验证 |
| iPad Air 11-inch (M3) / iPadOS 27.0 真机 | **开发签名、安装与启动通过**；本机 Personal Team 签名构建通过 `devicectl` 完成安装与启动，CUA 实际查看到用户正在播放的原生双栏视频页。此项不证明 HDR / Atmos 输出或后台持续播放 |
| iPhone 16 Pro / iOS 27.0 真机 | **已启用开发者模式并安装开发签名 App**；此前启动检查受锁屏限制，未完成本轮交互验收 |
| HDR / Dolby Vision 实际屏幕输出 | **未验证：需要匹配片源和支持的真实设备 / 显示器** |
| Atmos / 空间音频实际渲染 | **未验证：需要匹配片源和支持的真实输出路由** |
| 后台长时播放 / 锁屏控制 / PiP / AirPlay | 已实现原生接入；**设备集成行为未验证** |
| 远端管理写操作全流程 | **未全面验收**；尤其是备份、迁移、永久删除及权限变更，不能由读取接口或编译成功推定通过 |
| CI iOS job | [083323e 的远端工作流全部通过](https://github.com/guoweiyi/treasure-up/actions/runs/37326396930)，其中 [iOS job 111818139423 通过](https://github.com/guoweiyi/treasure-up/actions/runs/37326396930/job/111818139423)；这是上一版构建的远端 CI 结果，不包含真机安装或在线 UI 验收 |

交付或发布前，应把上表中的“未验证”替换为具体设备、系统版本、构建版本及运行证据；保留尚未取得证据的限制说明。

## Apple 官方参考

- [Adopting Liquid Glass](https://developer.apple.com/documentation/technologyoverviews/adopting-liquid-glass)：系统组件与玻璃材质的适配。
- [AVPlayerLayer](https://developer.apple.com/documentation/avfoundation/avplayerlayer)：系统原生视频呈现。
- [Adopting Picture in Picture in a Custom Player](https://developer.apple.com/documentation/avkit/adopting-picture-in-picture-in-a-custom-player)：自定义原生控件的公开 PiP 接口。
- [AVURLAssetHTTPCookiesKey](https://developer.apple.com/documentation/avfoundation/avurlassethttpcookieskey)：向媒体资源传递适用的 HTTP Cookie。
- [AVAudioSession.renderingMode](https://developer.apple.com/documentation/avfaudio/avaudiosession/renderingmode-swift.property)：读取系统音频渲染模式；部分路由或未活动会话会返回 `notApplicable`，不能当作通用 Atmos 检测器。
- [Incorporating HDR video with Dolby Vision into your apps（PDF）](https://developer.apple.com/av-foundation/Incorporating-HDR-video-with-Dolby-Vision-into-your-apps.pdf)：Apple 的 Dolby Vision / HDR 播放、设备能力与元数据说明。
