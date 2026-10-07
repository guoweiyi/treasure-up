<p align="center"><img src="frontend/public/brand/logo-lockup.png" width="220" alt="Treasure Up" /></p>

<h1 align="center">Treasure Up</h1>

<p align="center"><b>一个 B 站视频归档库</b></p>

<p align="center">
  <a href="https://github.com/guoweiyi/treasure-up/releases">下载版本</a> ·
  <a href="docs/installation.md">安装与升级</a> ·
  <a href="native/apple/INSTALL.md">iPhone / iPad 安装</a> ·
  <a href="docs/publishing.md">发版指南</a>
</p>

## 为什么做这个项目

你是否也遇到过这些问题？

- 给 UP 主充电后看过的视频，想重温时发现包月已经到期。
- 喜欢的视频被删除了，即使找到补档，原来的弹幕和精彩评论也不在了。
- 担心平台后续调整画质，想留住现在能看到的版本。

Treasure Up 是一个自部署的 B 站视频归档库。趁视频仍然可以访问时，把账号有权限观看的内容保存下来，再用自己的网页或 iOS 客户端浏览。保存质量取决于来源实际提供的音视频规格，不会凭空提高画质。

## 能做什么

- **收藏夹与 UP 主自动备份**：发现新收藏、新投稿；支持首次全量、最近若干条或只追踪新增，也可以在浏览器用油猴脚本选择视频。
- **视频之外也留一份**：封面、作者资料、标签、弹幕、评论与置顶评论；定时更新播放、点赞等统计，并记录来源失效状态。
- **保留音画规格**：多 P、充电视频、杜比视界与杜比全景声；提供分片播放和兼容音轨，实际播放能力取决于设备、浏览器及素材。
- **自己的存储**：本地目录、阿里云 OSS、腾讯云 COS、华为云 OBS、又拍云、七牛、S3 兼容服务，以及 OneDrive / SharePoint（含世纪互联）；支持副本同步、节点选择和对象存储直连。
- **日常观看与管理**：搜索、UP 主页、播放列表、星标、弹幕设置、评论图片预览；后台管理采集、存储、备份和权限。
- **账号与访问控制**：默认登录后访问，支持密码和通行密钥；管理员可以开启访客浏览。采集账号凭据由服务端保存。

服务端运行在 Docker 中。电脑、平板和手机均可通过网页访问，iPhone / iPad 另有原生客户端。Windows / macOS 当前使用网页，没有单独的桌面安装包。

## 快速开始

准备好 **Docker Engine / Docker Desktop 与 Docker Compose v2**，无需安装 Python、Node.js 或数据库。支持 Linux `amd64` / `arm64`；Windows 使用 Docker Desktop 的 Linux 容器模式。

1. 在 [Releases](https://github.com/guoweiyi/treasure-up/releases) 下载目标版本的 `treasure-up-vX.Y.Z-docker.zip`，解压并进入目录。
2. 生成本机配置：

   ```bash
   # Linux / NAS：以当前用户身份创建配置文件
   docker compose -f compose.setup.yaml run --rm --user "$(id -u):$(id -g)" setup
   ```

   ```powershell
   # Windows PowerShell / macOS Docker Desktop
   docker compose -f compose.setup.yaml run --rm setup
   ```

3. 启动服务：

   ```bash
   docker compose pull
   docker compose up -d --wait --wait-timeout 180
   ```

打开 **<http://localhost:8788>**，使用 `admin` 和初始化终端显示的随机密码登录。密码同时保存在本机 `.env`，首次登录后请修改。

以上命令适用于包含 `compose.setup.yaml` 的新部署包；旧版附件请按该版本的 Release 说明安装。部署包固定对应版本的镜像摘要，不会在重新启动时悄悄升级。镜像发布到 Docker Hub：[`yunyunjuan/treasure-up-backend`](https://hub.docker.com/r/yunyunjuan/treasure-up-backend) 与 [`yunyunjuan/treasure-up-web`](https://hub.docker.com/r/yunyunjuan/treasure-up-web)。

**NAS、其他设备或公网访问**需要额外设置访问地址和监听地址，见[安装说明](docs/installation.md)。资源有限可选择[轻量模式](docs/installation.md#轻量模式)。

## 第一次使用

1. 在后台添加 B 站账号并验证凭据；需要的充电、会员权限应属于该账号。
2. 添加本地或对象存储，完成连接检测。
3. 在自动备份中选择收藏夹 / UP 主，设定保存范围与更新周期；也可在后台安装油猴脚本进行单次采集。
4. 等待任务完成，在视频库观看。元数据先到、视频仍在下载时，页面会显示对应状态。

iPhone / iPad 用户从同一 Release 下载 `treasure-up-vX.Y.Z-ios-unsigned.ipa`，按[自签名安装指引](native/apple/INSTALL.md)安装，首次打开填写自己的服务地址并登录。IPA 是真机包，需要自行签名后才能安装；模拟器产物仅用于开发测试。

## 升级与数据

下载新版部署包，先备份，再用新文件更新原部署目录，**保留 `.env` 和原数据卷**，重新执行 `docker compose pull` 与启动命令。完整步骤见[安装与升级](docs/installation.md#升级与备份)。

数据库、视频、备份和队列均有持久卷。`.env` 中的加密密钥必须妥善保存；普通停止使用 `docker compose stop`，日常升级不要运行会删除数据卷的 `docker compose down -v`。

## 开发与发版

- [开发指南](docs/development.md)：目录、源码启动、测试和协作约定。
- [发布指南](docs/publishing.md)：配置 Docker Hub Secrets，在 Actions 手动升版、构建和发布。
- [iOS 工程](native/README.md)：本地 Xcode 构建和真机调试。

技术栈：Vue 3 / TypeScript / Artplayer / hls.js，FastAPI，PostgreSQL 17，Celery / Redis，FFmpeg；iOS 使用 SwiftUI。

## 致谢

播放器基于 [Artplayer](https://github.com/zhw2590582/ArtPlayer) 及其弹幕插件，下载使用 [yt-dlp](https://github.com/yt-dlp/yt-dlp)。
