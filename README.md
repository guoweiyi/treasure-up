# Treasure Up

<p align="center"><img src="frontend/public/brand/logo-lockup.png" width="220" alt="Treasure Up" /></p>

一个部署在自己服务器上的 B 站视频归档库。默认登录后浏览和播放，管理员可在后台开启游客访问；个人片单、进度保存与管理操作需要登录。保存视频、多 P、弹幕、字幕、评论和公开人员资料。

技术栈：Vue 3 / TypeScript / Artplayer / hls.js、FastAPI、PostgreSQL 17、Celery / Redis、FFmpeg、Nginx。媒体支持本地磁盘、S3 兼容对象存储和原生阿里云 OSS；B 站 Cookie 与独立刷新令牌只在服务端加密保存。

**本次版本为 [0.3.3 技术预览（preview）](https://github.com/guoweiyi/treasure-up/releases/tag/v0.3.3)，iOS / iPadOS 客户端为 0.4.3。** 新 logo 已适配网页、浏览器、PWA、离线页、选片助手和原生客户端。Docker 负责采集、存储和媒体处理，客户端连接自己的服务。

[本版说明与下载选择](docs/releases/v0.3.3.md) 包含更新内容、安装包和升级方式；[品牌资源说明](assets/branding/README.md) 记录原图、透明母图及尺寸生成方法。

## 预构建镜像部署

需要 Docker Compose v2 和 Python 3.10+。从目标 Release 下载 `treasure-up-v0.3.3-docker.zip`，解压进入包内目录后运行：

```bash
python deploy/start.py --prebuilt
# 资源有限时：
python deploy/start.py --prebuilt --light
```

部署包不需要本地编译，默认固定本次验证过的多架构镜像摘要；保留原 `.env` 和数据卷升级。本仓库的 GHCR backend / web 两个包现已 Public，可匿名拉取。新 fork 或新包首次创建默认私有，需所有者分别设为 Public，或使用有权限的账号执行 `docker login ghcr.io`。当前预览通道更新 `preview`，不会更新 `latest`。升级说明见本版 Release。

## 从源码本机启动

需要 Docker Compose v2 和 Python 3.10+。Windows 需启动 Docker Desktop 的 Linux 引擎。

```bash
git clone https://github.com/guoweiyi/treasure-up.git
cd treasure-up
python deploy/start.py
```

访问 <http://localhost:8788>。初始账号为 `admin`，首次初始化会在交互式终端显示随机密码，并保存在本机 `.env` 的 `TREASURE_ADMIN_PASSWORD`。重定向日志与 CI 不打印密码；普通重复启动保留原配置，显式传入 `--origin` 时只更新站点相关配置，保留密码与密钥。使用 `localhost` 才与本地通行密钥的站点地址一致。

资源有限时运行 `python deploy/start.py --light`，下载和媒体处理共用一个 Worker，元数据采集仍独立运行；数据库、队列和独立备份 Worker 保留。脚本启动成功后停止上一模式的处理 Worker，不删除数据卷。Compose 默认仅监听回环地址，数据库、Redis 不发布主机端口。

登录后台添加并验证授权账号，在“自动备份”选择自己创建 / 已收藏的收藏夹，或按最常访问排序的关注 UP；也可粘贴收藏夹链接或 UP 主页解析名称。选择首次全量、最近 N 个或只追踪新增。新建表单默认开启自动检查，API 未传 enabled 时仍默认关闭。首次元数据基线尚未完成时，不把发现的历史视频当成新发布。

## 反向代理与内网穿透

外部访问域名需要写入应用站点配置。遇到 `Invalid host header` 时，在部署目录执行：

```bash
python deploy/bootstrap.py --origin https://video.example.com
# 源码部署；使用自定义 Compose 文件时，沿用原来的 -f / --env-file 参数：
docker compose up -d --no-deps --no-build --wait api
```

这会同步精确域名白名单、登录 / 通行密钥 Origin、RP ID、安全 Cookie 和通行密钥开关；不更换账号密码、加密密钥、端口或数据。外层代理应保留浏览器的 `Host`（包括非默认端口）。以后更换域名时重复执行；已有通行密钥绑定原 RP 域名，新域名需要使用密码登录后重新注册。

如果穿透服务暂时仅提供 HTTP，显式运行 `python deploy/bootstrap.py --origin http://video.example.com --allow-http` 后按上面命令重建 API。该模式保留密码登录，禁用不受浏览器支持的远程 HTTP 通行密钥；HTTP 不加密登录凭据和会话，长期使用请为穿透入口配置 HTTPS。预构建部署升级时仍使用 `python deploy/start.py --prebuilt`。

## 已提供的功能

浏览器选片助手已更新到 1.3.0，可在后台“浏览器采集”下载安装，并使用专用令牌连接服务。

- 视频库、收藏夹、UP 主目录和独立主页，支持标题、标签、昵称及历史昵称搜索。
- 稍后看与自定义片单、已看状态、个人备注、移动和列表播放；原个人星标兼容迁入稍后看。
- 稿件发布时间、联合投稿职责、源站最高可用规格与已存文件码率；只有多 P 视频显示选集。
- 多 P 播放、进度保存、播放器内的画质 / 字幕 / 节点 / 音量平衡；弹幕字体 / 字号 / 字重 / 描边 / 区域 / 速度 / 密度 / 时间偏移，偏好本地保存。
- 原档优先播放、长视频无损 fMP4 HLS 分片、受限缓冲、可选播放音量平衡；杜比视界 / 全景声按媒体证据标注。
- 有数量和素材预算的热门评论归档，评论与楼中楼按需加载、表情行内显示，附图站内预览；人工标注与来源数据分离。
- 后台账号与凭据轮换、UP / 收藏来源、检查历史和新增统计、任务暂停 / 继续 / 重试、存储配置 / 探测 / 迁移、备份记录、系统配置、用户权限与审计。
- B 站扫码授权，或手动填写 Cookie 与配套的 `refresh_token` / `ac_time_value`；正常每 24 小时检查续期，只有上游要求时才轮换，支持自动开关和手动检查。会话续期支持确认重试、重新授权和备份恢复后的行为，不承诺永久在线。
- 账号密码与通行密钥登录；账户安全支持改密码和会话撤销，保留本机重置入口。
- B 站来源标签与人工标签分离；UP / 置顶评论标识；编辑者可在 UP 主页筛选最新投稿和充电视频进行采集。
- 浏览器选片助手：油猴脚本勾选 B 站视频，使用可撤销的专用令牌批量提交采集，后台统一处理与去重。
- 可安装 Web 应用及 [iOS / iPadOS 原生客户端](native/README.md)：纯 Swift App 直接使用后端 API，提供 iPad 分栏和原生播放器。客户端目录仅保留 iOS 工程，不执行服务器采集任务；真机签名、设备适配与商店分发有独立验收要求。
- 可恢复任务检查点、批量下载限速、过期任务接管与旧 Worker 提交隔离。
- 封面、人员资料、评论、弹幕等元数据先于媒体下载；下载、兼容副本独立任务，原档无需等待转码即可播放。
- 普通 API 与批量下载分开调度，视频下载间隔、图片素材预算和实际风控冷却可配置；按小时更新播放、点赞、投币、收藏、分享、评论及弹幕计数，可选重新采集弹幕。
- 收藏夹 / UP 播放列表，播完暂停、单集循环与连播；播放器提供格式、码率、丢帧与可测的分发统计。
- 后台整条视频 / UP 主归档删除，确认范围后异步清理；共享文件、联合投稿与已有备份分别保护，失败可重试。
- 按账号现有权限归档充电视频，来源级确认后保存已识别的全部可访问充电内容，拒绝把试看当完整归档；充电、杜比视界和全景声分别标注。
- 按视频同步完整资产到存储节点、浏览器限额测速选路、播放中有限故障切换；副本停用 / 恢复 / 显式物理删除。
- SHA-256 内容寻址、迁移后读回校验、原副本保留；数据库与媒体的一致快照加密备份和空环境恢复。

“完整”只表示当前接口可见范围已遍历，不承诺找回平台已经删除、隐藏或不可访问的数据。人员资料指公开 UID、昵称、简介、头像等，不推断真实姓名。

弹幕直接使用已保存的数据和显示配置，不包含语义审核、本机模型或模型服务依赖。无损分片保留原始压缩音视频；兼容转码是另存的可选副本，不覆盖原档。杜比归档成功不等于所有浏览器和设备均能输出 HDR 或空间音频。

## 部署与维护

升级时保留 `.env`、加密密钥、媒体和数据卷。不要使用 `docker compose down -v` 卸载日常服务，它会删除持久卷。

维护者使用 Python 3.11+ 运行 `python deploy/release.py prepare vX.Y.Z --channel preview`，补完对应 Release 说明并运行 `python deploy/release.py check vX.Y.Z` 后推送版本标签。GitHub Actions 验证前后端、iPhone / iPad 离线回归及品牌资产，再构建双架构镜像和附件；全部通过后公开版本。当前 iOS 附件是临时签名的模拟器包，真机分发需独立开发签名。用户部署入口仍只需 Python 3.10+。

`.env`、`.private/`、媒体、运行数据、依赖及日志均不提交。首次部署创建空库；仓库不包含账号 Cookie、演示视频或个人收藏数据。此前 Node 实验仅保留在原工作区，不属于本项目运行依赖。

## 验证

```bash
python -m venv .venv
# 激活虚拟环境后：
python -m pip install -r backend/requirements.lock
python -m pytest -c backend/pytest.ini backend/tests
npm --prefix frontend ci
npm --prefix frontend run test
npm --prefix frontend run build
python deploy/check_branding.py
python -m pytest deploy/tests -q
```

真实 PostgreSQL / FFmpeg 备份恢复测试需显式启用，在已启动的 Docker 环境运行：

```bash
docker compose exec -T -e TREASURE_RUN_POSTGRES_TESTS=1 backup-worker python -m pytest tests -q
```

该测试只创建和清理随机 `treasure_test_*` 数据库及独立临时目录，不操作业务数据库。GitHub Actions 使用同一 Docker 路径验证。

播放器基于 [Artplayer](https://github.com/zhw2590582/ArtPlayer) 及其弹幕插件；下载使用 [yt-dlp](https://github.com/yt-dlp/yt-dlp)。项目未复制 B 站闭源播放器源码。各依赖按自身许可使用。
