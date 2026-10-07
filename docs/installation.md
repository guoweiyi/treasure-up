# 安装与升级

返回 [项目首页](../README.md)。日常使用选择 Release 部署包；开发调试见[开发指南](development.md)。

## 准备环境

- Docker Engine 或 Docker Desktop，Docker Compose v2（`docker compose version`）。
- Linux `amd64` / `arm64`；Windows 与 macOS 通过 Docker Desktop 运行 Linux 容器。
- 有足够空间保存视频、下载临时文件和备份。空间需求取决于采集范围，不能只按程序镜像大小估算。

下载 [Release](https://github.com/guoweiyi/treasure-up/releases) 中的 `treasure-up-vX.Y.Z-docker.zip`，解压后在该目录打开终端。不要下载 GitHub 自动生成的源码 ZIP 代替部署包。以下步骤适用于包含 `compose.setup.yaml` 的新版包。

包内的 `compose.yaml`、`compose.light.yaml`、`compose.setup.yaml` 已固定此版本验证过的镜像，不需要源码、不在本机编译，也不需要 Docker Hub 账号（镜像仓库须由维护者设为 Public）。

## 初始化与启动

Linux / NAS：

```bash
docker compose -f compose.setup.yaml run --rm --user "$(id -u):$(id -g)" setup
```

Windows PowerShell / macOS Docker Desktop：

```powershell
docker compose -f compose.setup.yaml run --rm setup
```

初始化仅在本机生成 `.env`，其中包含数据库密码、初始管理员密码及加密密钥。重复运行保留已有文件。交互式终端会显示初始账号密码，重定向输出和 CI 不显示密码。

```bash
docker compose pull
docker compose up -d --wait --wait-timeout 180
docker compose ps
```

访问 <http://localhost:8788>，账号 `admin`。首次启动等待数据库就绪和迁移结束，`init` 容器完成后退出是正常现象。网页服务只在本机监听；数据库与 Redis 不暴露主机端口。

忘记初始密码时，在自己的交互式终端执行（Linux 同样在 `setup` 前添加 `--user "$(id -u):$(id -g)"`）：

```bash
docker compose -f compose.setup.yaml run --rm setup --show-login
```

它只显示 `.env` 保存的初始密码，不会重置已经修改的密码。不要把 `.env`、Cookie 或令牌贴到 Issue 中。

## NAS 与局域网访问

其他设备不能使用你的 `localhost`。以服务器局域网地址 `192.168.1.20` 为例，在初始化时指定实际访问地址，并让 Web 监听局域网：

```bash
docker compose -f compose.setup.yaml run --rm --user "$(id -u):$(id -g)" setup --origin http://192.168.1.20:8788 --bind-address 0.0.0.0
docker compose up -d --wait --wait-timeout 180
```

Windows / macOS Docker Desktop 省略 `--user` 参数。已有部署也可使用同一命令更新地址，密码和密钥保持不变。手机、iPad 和其他电脑的浏览器访问 `http://192.168.1.20:8788`。原生 iOS 客户端请使用下节的可信 HTTPS 域名，填写站点地址时不要加 `/api/v1`。

HTTP 局域网地址使用密码登录；IP 地址不启用通行密钥。需要通行密钥或从外网访问时，使用 HTTPS 域名。IPv6 请使用指向服务器的域名。防火墙只开放需要访问的范围。

## HTTPS、反代与内网穿透

以最终浏览器地址 `https://video.example.com` 为例，初始化或更新站点：

```bash
docker compose -f compose.setup.yaml run --rm setup --origin https://video.example.com
docker compose up -d --wait --wait-timeout 180
```

Linux 加上上述 `--user` 参数。域名配置会一起更新 Host 白名单、Cookie、Origin 和通行密钥的 RP ID，避免只改某一个变量导致“请求来源不匹配”。已有通行密钥绑定原域名，更换域名后使用密码登录并重新注册。

代理在同一宿主机上可转发到 `http://127.0.0.1:8788`。如果代理是另一台机器，或独立 Docker 容器无法访问宿主机回环地址，需要追加 `--bind-address 0.0.0.0`，并用代理可达的主机地址回源。

Nginx 示例（证书和域名由你的代理管理）：

```nginx
location / {
    proxy_pass http://127.0.0.1:8788;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_read_timeout 300s;
}
```

Nginx Proxy Manager、宝塔或穿透服务也要保留原始 Host（含非默认端口）。代理指向 Web 的 `8788` 端口，不要直接公开 API、数据库或 Redis。

仅有 HTTP 公网入口时，需显式传 `--allow-http`；该模式不启用通行密钥。长期部署应配置 HTTPS。`Invalid host header` 或“请求来源不匹配”时，先检查浏览器地址是否与 `--origin` 完全一致，包含协议和端口，再确认已重建应用容器。

## 轻量模式

轻量模式合并下载和媒体处理 Worker，减少常驻进程；元数据采集和备份仍独立。它使用相同数据库和队列，不是功能删减版，繁忙时媒体任务会排队。

首次启动或从完整模式切换：

```bash
docker compose -f compose.yaml -f compose.light.yaml pull
docker compose -f compose.yaml -f compose.light.yaml up -d --wait --wait-timeout 180
# 仅在上一条启动成功后执行，停止原完整模式 Worker
docker compose -f compose.yaml -f compose.light.yaml --profile full-workers stop download-worker media-worker
```

切回完整模式：

```bash
docker compose up -d --wait --wait-timeout 180
# 仅在启动成功后停止轻量 Worker
docker compose -f compose.yaml -f compose.light.yaml stop worker
```

后续维护沿用所选模式的 Compose 参数，不要同时长期运行两套处理 Worker。

## 本地目录与对象存储

默认视频、临时文件、数据库和备份保存在 Docker 持久卷内。后台添加“本地存储”时填写的是**容器内路径**，默认媒体根目录为 `/data/media`。

希望在 NAS 文件管理器直接访问视频，可以在**首次部署前**把媒体卷改为宿主机目录。例如另建 `compose.nas.yaml`：

```yaml
volumes:
  media:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /volume1/treasure-up/media
```

先创建实际目录，让后端使用的 UID / GID `10001:10001` 可以读写，同时让 Web 容器的 Nginx 工作进程能够读取媒体、遍历目录（使用受控组或 ACL，不要设置全员写入权限）。然后用 `docker compose -f compose.yaml -f compose.nas.yaml up -d --wait` 启动。不要对已有媒体卷直接替换挂载来源，否则旧文件不会自动搬过去；已有用户先备份并制定迁移步骤。

对象存储在后台通过表单填写服务、区域、凭据和桶。直连播放需要对象存储允许浏览器请求（CORS），自定义访问域名需要与签名、HTTPS 配置匹配。保存后先做连接探测，再用少量视频验证采集与播放。

## 升级与备份

1. 阅读目标版本的 Release 说明；在后台运行备份并检查完成状态。另行保存 `.env`，特别是数据库、凭据加密和备份加密相关密钥。
2. 把新版部署包解压到临时目录，用其中同名文件更新**原部署目录**。保留原 `.env`、自定义 Compose 文件和数据卷；不要更改项目名 `treasure-up`。
3. 在原目录运行 `docker compose pull`，随后 `docker compose up -d --wait --wait-timeout 180`。轻量模式或自定义挂载沿用原 `-f` 参数。
4. 检查 `docker compose ps` 和网页，确认登录、视频库及播放正常。

新部署包使用镜像摘要锁定本次版本。已有 `.env` 如果设置过 `TREASURE_BACKEND_IMAGE` / `TREASURE_WEB_IMAGE`，它们会覆盖包内默认值；使用官方包升级时移除旧镜像覆盖，其他配置保持不变。数据库迁移由 `init` 执行，失败时后续服务不会被当作初始化成功。

应用备份与 `.env` 都应保存到独立位置。不要只备份容器可写层，也不要把 Redis 当作数据库备份。跨大版本回退可能涉及数据库结构，不能只把镜像标签改回去；保留升级前的可恢复备份。

## 常见问题

| 现象 | 检查方法 |
| --- | --- |
| 无法拉取镜像 / `denied` | 检查网络、Release 是否已完成，以及 Docker Hub 仓库是否 Public；不要用旧 GHCR 地址替换新部署包。 |
| `compose.setup.yaml` 不存在 | 下载的是旧版附件或源码包；使用该版说明，或选择包含新安装流程的版本。 |
| 配置目录无写入权限 | Linux 使用 `--user "$(id -u):$(id -g)"`，确认当前目录可写。不要把 `.env` 改为公开可读写。 |
| 手机打不开、电脑能打开 | 检查监听地址、防火墙、实际服务器 IP / 域名，以及手机网络是否能访问服务器。 |
| 页面打开但登录失败 | 检查 `--origin` 与浏览器地址，HTTPS 终止位置及代理 Host 设置。不要通过关闭来源验证解决。 |
| 启动超时 | 用 `docker compose ps -a` 和 `docker compose logs --tail 100 init api web` 查看原因；日志发给别人前先检查是否有敏感信息。 |
| 忘记已修改的管理员密码 | 在部署目录交互运行 `docker compose exec api python -m app.cli reset-password admin`，按提示设置新密码。 |

日常停止：`docker compose stop`。重新启动：`docker compose up -d --wait`。**不要在日常操作中执行 `docker compose down -v`，它会删除数据库和媒体等持久卷。**
