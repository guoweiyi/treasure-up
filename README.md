<p align="center"><img src="frontend/public/brand/logo-lockup.png" width="220" alt="Treasure Up" /></p>
<h1 align="center">Treasure Up</h1>
<p align="center"><b>一个 B 站视频归档库</b></p>

## 前言

你是否遇到过这些问题？

- 给 UP 主充电后看过的视频，想重温时发现包月已经到期。
- 视频被删除了，找到了补档，却找不回原来的弹幕和精彩评论。
- 担心平台调整画质，想留住现在能看到的版本。

Treasure Up 可以把账号有权限观看的视频保存到自己的存储里，连同弹幕、评论和作者资料一起留下，随时用网页或 iPhone / iPad 观看。

## 功能

- 备份收藏夹、UP 主投稿，定时发现新视频；也支持用油猴脚本勾选采集。
- 保存封面、标签、作者资料、弹幕、评论及置顶评论，更新视频统计和失效状态。
- 支持多 P、充电视频、杜比视界和杜比全景声，提供分片播放与兼容音轨。
- 支持本地、OSS、COS、OBS、又拍云、七牛、S3 兼容存储、OneDrive 和 SharePoint，可同步副本、切换播放节点。
- 支持搜索、播放列表、星标、弹幕设置、账号权限和备份恢复。

## 快速开始

先安装并启动 Docker。支持 Linux `amd64` / `arm64`，Windows、macOS 使用 Docker Desktop。

```sh
docker run --rm -it --pull always --user 0 -v /var/run/docker.sock:/var/run/docker.sock -v treasure-up-config:/config yunyunjuan/treasure-up-backend:latest python /app/install.py
```

命令会拉取镜像、生成配置并启动服务。完成后打开 **<http://localhost:8788>**，使用终端显示的 `admin` 和随机密码登录。

安装时临时挂载 Docker socket，用于创建服务容器；安装结束即退出，业务容器不保留这个挂载。密码和配置保存在 `treasure-up-config` 数据卷中。

<details>
<summary>部署在 NAS，或需要从其他设备访问</summary>

把下面的 `192.168.1.20` 换成服务器 IP：

```sh
docker run --rm -it --pull always --user 0 -v /var/run/docker.sock:/var/run/docker.sock -v treasure-up-config:/config yunyunjuan/treasure-up-backend:latest python /app/install.py --origin http://192.168.1.20:8788 --bind-address 0.0.0.0
```

使用反向代理时，把 `--origin` 改为实际 HTTPS 域名。iOS App 和通行密钥登录请使用 HTTPS 域名。

</details>

登录后，在后台添加 B 站账号、存储位置和备份来源。升级时再次运行安装命令即可，原配置和数据保留。旧版 Compose 用户先看[迁移说明](docs/installation.md)。

## iPhone / iPad

从 [Releases](https://github.com/guoweiyi/treasure-up/releases) 下载 `ios-unsigned.ipa` 附件，按[自签名指引](native/apple/INSTALL.md)安装。首次打开填写自己的服务器地址并登录。支持 iOS / iPadOS 18 及以上。

## 文档

- [安装、升级与反向代理](docs/installation.md)
- [开发](docs/development.md) · [手动发版](docs/publishing.md) · [iOS 工程](native/README.md)

技术栈：Vue 3 / TypeScript、FastAPI、PostgreSQL、Celery / Redis、FFmpeg、SwiftUI。

## 致谢

播放器使用 [Artplayer](https://github.com/zhw2590582/ArtPlayer) 及其弹幕插件，视频下载使用 [yt-dlp](https://github.com/yt-dlp/yt-dlp)。
