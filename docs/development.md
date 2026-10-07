# 开发指南

普通用户从 [Release 部署包](installation.md)安装；本文用于修改代码和验证。

## 目录与边界

| 目录 | 职责 |
| --- | --- |
| `backend/` | FastAPI、数据模型与迁移、采集、播放分发和 Celery 任务 |
| `frontend/` | Vue / TypeScript 网页、播放器、管理后台、油猴脚本 |
| `native/apple/` | iPhone / iPad 客户端、Xcode 工程、真机 IPA 打包和测试 |
| `deploy/` | Docker 镜像、初始化、版本管理和发布检查 |
| `.github/workflows/` | 持续验证、iOS 构建、Docker Hub 构建和统一发版入口 |
| `docs/releases/` | 每个服务端版本的发布说明 |

本地 `.env`、账号凭据、数据库、视频、下载缓存、日志和构建目录不进入 Git。仓库不包含可用于登录的默认密码或演示账号数据。不要手动更新 lockfile 中少数版本字段而遗漏对应依赖树。

## 从源码启动

需要 Docker Compose v2 与 Python 3.11+。Windows 使用 Linux 容器模式。

```bash
git clone https://github.com/guoweiyi/treasure-up.git
cd treasure-up
python deploy/start.py
# 较少常驻进程：python deploy/start.py --light
```

脚本先生成或保留 `.env`，再构建本机镜像、初始化数据库和启动服务。访问 <http://localhost:8788>。它不替代 Release 部署包的 Docker-only 安装入口。

前端单独开发需要 Node.js 22：

```bash
npm --prefix frontend ci
npm --prefix frontend run dev
```

开发代理配置见 [前端说明](../frontend/README.md)。iOS 使用 macOS 与 Xcode，见 [Apple 工程说明](../native/README.md)。原生客户端版本可独立于服务端版本演进，发服务端版本不会覆盖用户已更新的客户端版本。

## 验证

前端测试与类型 / 生产构建：

```bash
npm --prefix frontend ci
npm --prefix frontend run test
npm --prefix frontend run build
```

部署与打包检查（创建并激活虚拟环境后）：

```bash
python -m pip install pytest==9.1.1 PyYAML==6.0.3
python -m pytest deploy/tests -q
python deploy/release.py check vX.Y.Z
```

`vX.Y.Z` 替换为 `release.json` 中的当前版本。服务启动后验证连接、初始化账号和读取接口：

```bash
python deploy/check_service.py --url http://127.0.0.1:8788 --env-file .env
```

上述检查使用 `.env` 中的初始管理员密码，只适合新建验证环境；修改过密码的业务实例不要用它做自动检查。CI 的安装检查使用随机项目、独立目录与数据卷，验证全新初始化、登录和服务就绪，不读取现有业务数据。设置 `TREASURE_RUN_INSTALL_TESTS=1` 可在已有本地构建镜像的机器上运行同一安装检查；未开启时会跳过这一项。

## 合并与发版

日常提交只进入持续验证；合并前关注迁移、权限检查、凭据脱敏、任务重试幂等性以及已有客户端兼容性。测试应覆盖实际故障边界，避免只检查实现文本。

发布统一使用 [Actions 发版流程](publishing.md)，不再维护独立 GHCR 发布入口。流程固定同一提交完成安装检查、原生平台回归、Docker 双架构运行检查与 IPA 打包。不要绕过失败的检查手工覆盖已经发布的版本镜像。

服务端版本在 `release.json` 统一管理，`deploy/release.py` 同步相关声明；iOS App 版本在 Apple 工程中独立管理。变更 API 时兼顾已安装客户端，变更配置时提供已有部署的迁移方式。
