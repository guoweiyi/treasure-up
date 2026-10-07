# 安装与升级

[首页](../README.md)提供一条 Docker 命令的安装方式。下面是地址配置、升级和旧版迁移。

## 访问地址

默认地址为 `http://localhost:8788`，只允许本机访问。在安装命令末尾追加参数可修改：

| 场景 | 参数 |
| --- | --- |
| 局域网 / NAS | `--origin http://192.168.1.20:8788 --bind-address 0.0.0.0` |
| HTTPS 反代 | `--origin https://video.example.com` |
| 代理在另一台机器或独立容器 | 再加 `--bind-address 0.0.0.0` |

IP 和域名替换为实际地址。已有安装重复执行时，只更新明确传入的配置，保留密码和密钥。远程 HTTP 使用密码登录；iOS App 和通行密钥需要可信的 HTTPS 域名。

Nginx 示例：

```nginx
location / {
    proxy_pass http://127.0.0.1:8788;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_read_timeout 300s;
}
```

Nginx Proxy Manager、宝塔、内网穿透也使用同一原则：回源到 Web 的 `8788` 端口，保留浏览器 Host。出现 `Invalid host header` 或“请求来源不匹配”时，检查 `--origin` 的协议、域名、端口是否与地址栏一致。

## 升级与数据

先在后台备份，再重新运行首页安装命令。`--pull always` 会拉取新安装镜像，安装器随后更新同一版本的服务镜像。

配置保存在 `treasure-up-config`，业务数据保存在 `treasure-up_database`、`treasure-up_media` 等卷中。一起保留这些卷，特别是配置中的加密密钥。日常升级不要删除数据卷，也不要运行 `docker compose down -v`。

需要固定版本时，将命令中的 `:latest` 换成具体版本，例如 `:0.3.5`。大版本回退先阅读发布说明，涉及数据库迁移时需使用升级前的备份恢复。

## 从旧版 Compose 迁移

旧版 `.env` 保存着原密码和密钥，迁移时必须导入它。安装器发现已有数据卷但缺少配置时会停止，不会替旧数据生成新密钥。

在原部署目录执行（Linux、macOS 和 PowerShell 均可）：

```sh
docker run --rm -it --pull always --user 0 -v /var/run/docker.sock:/var/run/docker.sock -v treasure-up-config:/config -v "${PWD}:/previous:ro" yunyunjuan/treasure-up-backend:latest python /app/install.py --import-env /previous/.env
```

迁移后继续使用原项目名 `treasure-up` 和原数据卷。有自定义 NAS 挂载或 Compose 扩展时，先保留原 Compose 部署方式。

## 手动 Compose

想自行管理 Compose 文件，可使用 Release 的 `docker.zip` 附件：

```sh
docker compose -f compose.setup.yaml run --rm setup
docker compose pull
docker compose up -d --wait --wait-timeout 180
```

Linux 在 `setup` 前加 `--user "$(id -u):$(id -g)"`，让生成的 `.env` 归当前用户所有。升级保留 `.env`，用新版文件覆盖原部署目录；新版包已固定镜像摘要，不再叠加旧版 `compose.registry*.yaml`。

后台的本地存储路径是容器内路径，默认 `/data/media`。自定义 NAS 挂载时，需要后端 UID / GID `10001:10001` 可读写、Nginx 可读取和遍历目录。对象存储在后台表单配置，浏览器直连还需设置对应的 CORS 和 HTTPS 域名。
