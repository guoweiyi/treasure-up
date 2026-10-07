<p align="center"><img src="frontend/public/brand/logo-lockup.png" width="220" alt="Treasure Up" /></p>

<h1 align="center">Treasure Up</h1>


一个 B 站视频归档库

技术栈：Vue 3 / TypeScript / Artplayer / hls.js、FastAPI、PostgreSQL 17、Celery / Redis、FFmpeg。

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
