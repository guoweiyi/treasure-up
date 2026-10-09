# 安装与升级

[首页](../README.md#快速开始)提供直接从 Docker Hub 启动的命令，需要 Docker Compose 2.34 及以上。下面使用 [Release 中的 compose.yaml](https://github.com/guoweiyi/treasure-up/releases/latest/download/compose.yaml)，方便保存自己的端口和地址设置；同样无需源码、Python 或手动生成密钥。

把文件放在一个固定目录，后续命令都在该目录执行：

```sh
docker compose up -d
docker compose run --rm setup --show-login
```

第二条命令只在交互终端显示初始账号。登录后改过密码的，以新密码为准，它不会重置账号。默认访问 <http://localhost:8788>，只开放本机端口。

## 访问地址

NAS 或局域网访问，在同一目录的 `.env` 中填写实际 IP：

```dotenv
TREASURE_PUBLIC_ORIGIN=http://192.168.1.20:8788
TREASURE_BIND_ADDRESS=0.0.0.0
```

HTTPS 反代将第一行改成 `TREASURE_PUBLIC_ORIGIN=https://video.example.com`。代理与应用在同一主机时，可以保留默认的本机绑定；代理在另一台机器或独立容器时，再开放相应的监听地址。

新安装默认允许 `https://local.gwy.fun` 的访问和密码登录，旧安装保持自己的许可范围。域名解析和 HTTPS 证书仍由自己的反代提供；通行密钥绑定正式站点地址，使用该域名时设置 `TREASURE_PUBLIC_ORIGIN=https://local.gwy.fun`。

改完执行：

```sh
docker compose up -d --force-recreate
```

这会重新读取访问地址，保留密码和密钥。只运行 `restart` 不会执行初始化配置更新。修改端口时还要设置 `TREASURE_PORT=8899`，并让公开地址中的端口与之对应。

远程 HTTP 使用密码登录；iOS App 和通行密钥请使用可信的 HTTPS 域名。Nginx 示例：

```nginx
location / {
    proxy_pass http://127.0.0.1:8788;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_read_timeout 300s;
}
```

Nginx Proxy Manager、宝塔、内网穿透同样回源到 Web 的 `8788` 端口，保留浏览器 Host。出现 `Invalid host header` 或“请求来源不匹配”时，检查 `TREASURE_PUBLIC_ORIGIN` 的协议、域名、端口是否与地址栏一致。

## 升级与数据

先在后台备份，再下载新版本的 `compose.yaml` 覆盖旧文件，保留 `.env`，执行：

```sh
docker compose up -d --pull always --force-recreate
```

从 Docker Hub 直接启动的用户，升级命令为：

```sh
docker compose -f oci://docker.io/yunyunjuan/treasure-up:latest up -d --pull always --force-recreate
```

稳定版入口随发版更新。需要固定版本时，把 `latest` 换成版本号，例如 `0.3.9`。Release 中的 YAML 已固定镜像摘要。不要通过旧版文件直接降级，数据库回退应使用升级前的完整备份。

主配置保存在 `treasure-up-config` 卷，数据库、媒体等保存在 `treasure-up_database`、`treasure-up_media` 等卷。备份时一起保存，特别是主配置中的加密密钥。日常升级不要执行 `docker compose down -v`。

初始化容器没有网络，不接触主机 Docker；它只向数据卷写入配置。后端以普通用户运行，只读取对应角色需要的配置。管理员初始密码和备份密钥不会挂载到 API 容器。

## 从旧版迁移

v0.3.5 自动安装创建的 `treasure-up-config` 可直接复用。使用默认项目名 `treasure-up`，保留原数据卷。有自定义端口或绑定地址的，把原 `TREASURE_PORT`、`TREASURE_BIND_ADDRESS` 同时写进新部署目录的 `.env`；Compose 无法读取卷里的端口设置。

更早的 Compose 部署，原 `.env` 保存着密码和加密密钥，先保留该文件。在原部署目录下载新版 `compose.yaml`，导入一次：

```sh
docker compose run --rm -v "${PWD}/.env:/previous/.env:ro" setup --import-env /previous/.env
docker compose up -d --force-recreate
```

导入仅复制原凭据，不会重设密码。已有数据库却找不到配置时，初始化会停止，避免用新密钥覆盖旧数据。原来有 NAS 自定义挂载、不同项目名或 Compose 扩展的，迁移前需保留这些对应关系。

后台本地存储路径是容器内路径，默认 `/data/media`。自定义 NAS 挂载需要后端 UID / GID `10001:10001` 可读写、Nginx 可读取和遍历目录。对象存储在后台表单配置，浏览器直连还需设置对应的 CORS 和 HTTPS 域名。

源码目录中的 `compose.yaml` 支持在 `.env` 设置 `TREASURE_MEDIA_PATH`，默认使用 `media` 命名卷；例如 `TREASURE_MEDIA_PATH=Q:/BILI` 会将 Windows 主机目录映射到各媒体读写服务的 `/data/media`，Web 和备份服务保持只读。已有数据时，先停止采集、下载等写入服务，将原媒体卷的全部内容复制到目标目录并校验完整性。若使用 root 复制，需将目标媒体目录及其已有子目录、文件的属主恢复为后端 UID / GID `10001:10001`；此权限调整仅针对目标媒体树。再修改配置，用原来相同的 Compose 文件组合执行 `up -d --force-recreate`。以 `10001:10001` 身份检查已有子目录的访问权限，并通过应用的存储写入方法实际写入和清理测试文件，覆盖后续会写入的已有子目录；仅在 `/data/media` 根目录创建探针不足以证明子目录可写。确认读写和播放正常后再处理旧卷；只修改路径不会自动搬迁已有文件。后台存储路径继续填写 `/data/media`。此设置适用于源码部署，Release 和 OCI 发布的 Compose 仍默认使用命名卷。


源码部署还可设置 `TREASURE_SCRATCH_PATH=Q:/BILI-tmp`，将下载断点、兼容转换和手动播放准备的临时目录移出 Docker Desktop 的系统盘。它与 `TREASURE_MEDIA_PATH` 分别映射到 `/data/scratch` 和 `/data/media`。迁移时先停止使用临时目录的 worker 和调度器，保留并校验已有 `downloads` 断点及导入文件，为目标目录设置 `10001:10001` 可读写，再重建服务；确认新挂载后才能清理旧临时卷。不要删除数据库、正式媒体卷或正在使用的下载断点。

worker 默认设置 CPU、内存、进程数硬上限，且不额外使用 swap：媒体处理和轻量部署合并 worker 为 2 CPU / 2 GiB；下载和备份为 1 CPU / 1 GiB；资料采集为 1 CPU / 768 MiB。健康检查间隔为 120 秒。需要调整时可通过 Compose override 覆盖这些值。响度分析和 HLS 分片默认关闭，可按具体视频手动准备；已有显式开启设置需要在后台关闭。关闭自动准备不会删除已有分片。

Docker Desktop 中删除临时文件或构建缓存，可能只释放虚拟磁盘内部空间。应同时检查宿主 C 盘空闲；若需收缩 VHDX，先停止 Docker Desktop，再使用 Windows 支持的虚拟磁盘压缩工具处理已确认的 Docker 数据盘。不要用填零写满系统盘的方式回收空间。

## 查看服务

```sh
docker compose ps
docker compose logs --tail 100 api
```

使用 CLI 维护命令时，通过入口加载配置，例如 `docker compose run --rm api python -m app.cli --help`。直接 `exec` 新进程不会自动继承入口从配置文件读取的环境变量。

如果提示 `setup` 启动失败，执行 `docker compose run --rm setup` 查看原因。初始化容器不保存日志，避免查看初始密码时留下副本。
