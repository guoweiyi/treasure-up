<p align="center"><img src="frontend/public/brand/logo-lockup.png" width="220" alt="Treasure Up" /></p>

<h1 align="center">Treasure Up</h1>

<p align="center"><b>一个 B 站视频归档库</b></p>


技术栈：Vue 3 / TypeScript / Artplayer / hls.js、FastAPI、PostgreSQL 17、Celery / Redis、FFmpeg

## 前言

你是否遇到过以下问题？
- 之前给 UP 主充电，看过的充电视频想重温时，发现包月刚过期
- 视频因不可抗力被删除，即使找到了补档，原来的弹幕和精彩评论也不在了
- 因为叔叔压画质，担心以后回看，清晰度不如现在

本项目目标就是解决这些问题，让你在本地也保留一份完整的 b 站视频备份

## ✨ 亮点

- 支持采集视频各种数据，视频、三连数据、弹幕、评论，支持定时更新
- 支持从 up 主、收藏夹自动拉取新视频
- 支持采集充电视频（需要账号充电），多 P 视频，杜比世界、杜比全景声视频
- 支持 S3、OneDrive、SharePoint、Google Drive、阿里云、又拍云、本地存储等存储源
- 支持多存储源相互复制、分发
- 原站级别的视频浏览体验

## 快速开始

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

## 致谢

播放器基于 [Artplayer](https://github.com/zhw2590582/ArtPlayer) 及其弹幕插件

下载使用 [yt-dlp](https://github.com/yt-dlp/yt-dlp)。
