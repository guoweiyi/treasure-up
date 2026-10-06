# 自动构建、发版与部署

当前已发布 [v0.3.2](https://github.com/guoweiyi/treasure-up/releases/tag/v0.3.2)，[release.json](../release.json) 的 `channel` 为 `preview`。下面说明当前主线的发布流程，客户端已收敛为纯 Swift iOS / iPadOS；已发布旧版本的多平台附件保持原样。各版本的镜像和附件，以 [GitHub Releases](https://github.com/guoweiyi/treasure-up/releases) 及对应 [Actions 运行结果](https://github.com/guoweiyi/treasure-up/actions) 为准。

## 我只想部署

在目标 Release 下载 `treasure-up-v0.3.2-docker.zip`，解压并进入其中的 `treasure-up-v0.3.2` 目录。需要 **Python 3.10+、Docker Compose v2**；Windows 使用 Docker Desktop 的 Linux 引擎。

```bash
python deploy/start.py --prebuilt
# 或选择轻量模式：
python deploy/start.py --prebuilt --light
```

部署包只有启动配置和脚本，不需要 Node、Rust、FFmpeg 或后端源码。脚本先拉取镜像，再等待数据库、API 和指定 Worker 队列就绪；替代服务健康后才停用相反模式的处理 Worker。默认访问 `http://localhost:8788`，首次初始化仅在交互式终端显示随机管理员密码。已有 `.env` 和数据卷不被重置。

发布包中的两个镜像默认固定为本次验证过的**多架构镜像摘要**，Docker 从中选择 `linux/amd64` 或 `linux/arm64`。不要把包内的 `@sha256:…` 换成 `latest` 来实现自动升级；更新前先验证备份，再下载新版本部署包、保留原 `.env`，重新执行上述命令。

| 参数 | 行为 |
| --- | --- |
| 无参数 | 仅用于源码目录，从本地源码构建并启动 |
| `--prebuilt` | 拉取 registry 配置的镜像，然后禁止本地构建和隐式二次拉取 |
| `--no-build` | 不构建、不拉取，仅使用本机已有镜像 |
| `--prebuilt --no-build` | 仍执行预构建镜像拉取；不是离线模式 |
| `--light` | 下载和媒体处理共用一个 Worker，元数据、备份仍独立 |

本仓库的 `treasure-up-backend`、`treasure-up-web` 两个 GHCR 包现已 **Public**，已验证可匿名拉取。新 fork 或新包首次创建默认私有，所有者需分别设为 Public 才能免登录拉取；保留私有时，使用有读取权限的账号交互执行 `docker login ghcr.io`。流水线上传成功不等于新包的匿名下载已开放。不要把仓库访问令牌写入 Compose、命令参数、发行附件或共享日志。

如需私有镜像仓库，在 `.env` 中设置 `TREASURE_BACKEND_IMAGE`、`TREASURE_WEB_IMAGE`；它们可以覆盖默认 tag 或 digest。两个镜像必须来自同一个兼容版本。首次站点地址、HTTPS 反代、存储、备份和恢复详见 [部署与维护](operations.md)。不要使用 `docker compose down -v` 升级。

## 维护者如何准备版本

准备、校验和打包使用 [deploy/release.py](../deploy/release.py)，需要 **Python 3.11+**；这与用户运行 `deploy/start.py` 所需的 Python 3.10+ 不同。版本统一为 `MAJOR.MINOR.PATCH`，Git 标签带 `v`，镜像版本标签不带 `v`。

以下用首次发布 `v0.3.2` 举例。若这个版本已经发布，应选择新的版本号，不能复用或移动旧标签。

```bash
python deploy/release.py prepare v0.3.2 --channel preview
```

`prepare` 更新前端、API、Compose 镜像版本及 `release.json`，并创建缺少的发行说明。iOS App 版本在 `native/apple/project.yml` 独立维护，修改后用 XcodeGen 同步工程；服务端发版不会改写 App 版本。它**不会**替你提交、打标签或推送，也不会修改生产配置。

随后完成这些步骤：

1. 编辑 `docs/releases/v0.3.2.md`，写清变更、升级方式和已知限制，移除 `RELEASE_NOTES_REQUIRED` 占位标记。同步 README 等人工维护的版本说明。
2. 检查改动，运行相关测试，再执行 `python deploy/release.py check v0.3.2`。版本不一致、发行说明缺失或占位未删除都会失败。
3. 提交并推送需要发布的代码、版本文件和说明；确认没有 `.env`、Cookie、密钥、媒体或本地运行数据进入提交。
4. 在该提交创建并推送版本标签：

```bash
git tag -a v0.3.2 -m "Treasure Up v0.3.2"
git push origin v0.3.2
```

推送 `v*` 标签会触发 **Release** 工作流。也可在 **Actions → Release → Run workflow** 中输入一个**已经存在**的标签，或使用已登录的 GitHub CLI：

```bash
gh workflow run release.yml -f tag=v0.3.2
```

手动触发不负责创建标签。校验步骤解析该标签的提交，后续回归、原生构建、容器构建和打包都使用这同一个提交；不是取各自开始执行时的最新分支代码。已公开的 Release 不允许原地替换。

## 流水线做什么

入口是 [release.yml](../.github/workflows/release.yml)，复用 [Verify](../.github/workflows/verify.yml)、[iOS client](../.github/workflows/ios.yml) 和 [Container images](../.github/workflows/containers.yml)。

| 阶段 | 实际检查或产物 |
| --- | --- |
| `validate` | 标签格式、服务端发布版本一致性、完整发行说明，以及版本是否已经公开发布 |
| `verify` | 锁定依赖审计、部署与发版脚本测试、前端测试和构建、Docker 内后端与真实 PostgreSQL 回归、Web 健康检查 |
| `ios` | iPhone / iPad 模拟器分别运行 Swift 单元与离线播放器 UI 回归，保存测试结果，打包一份临时签名的模拟器 App |
| `containers` | 分别在 AMD64、ARM64 原生 Linux runner 构建 backend/web；运行同一份镜像的 PostgreSQL 回归与健康检查，成功后上传候选镜像并合并双架构索引 |
| `package` | 收集本次运行的客户端与镜像记录，从标签提交导出源码、生成 digest 固定的部署 ZIP、清单和 SHA-256 校验文件 |
| `promote` | 核对候选来源、版本和 digest，再创建受保护的版本标签及对应通道别名 |
| `publish` | 校验附件哈希、先上传草稿并核对附件列表，最后公开 Release |

`verify` 与 `ios` 可以并行；容器构建依赖回归成功。客户端和容器都成功后才能打包、提升镜像版本并公开 Release。较早完成的候选镜像可能已在 GHCR 中，但不代表整次发版完成。

工作流使用当前仓库的 `GITHUB_TOKEN`：容器上传作业获得 `packages: write`，最终 Release 发布作业获得 `contents: write`，其他作业尽量只读。当前 iOS 模拟器打包使用 ad-hoc 临时签名，不使用私人发布证书；不要为了让作业通过，把后台密码、B 站 Cookie、云存储密钥或个人签名材料加入代码。

## 镜像版本与通道

当前默认仓库为：

```text
ghcr.io/guoweiyi/treasure-up-backend:0.3.2
ghcr.io/guoweiyi/treasure-up-web:0.3.2
```

| 名称 | 用途与保护 |
| --- | --- |
| `0.3.2` | 固定版本标签；已存在时只接受同一个 digest，不覆盖不同构建 |
| `preview` | 只由 `channel: preview` 的发行更新 |
| `latest` | 只由 `channel: stable` 的发行更新；预览版不会改动它 |
| `@sha256:…` | 部署 ZIP 默认使用的精确多架构镜像摘要，记录在 `release-images.json` |

当前 `v0.3.2` 已作为 GitHub **Pre-release** 发布，更新 `0.3.2` 和 `preview`，**不更新 `latest`**。将来准备稳定版时显式使用 `--channel stable`；稳定与否由 `release.json` 决定，不靠版本号大小推测。通道检查还会阻止较旧版本覆盖已经指向更新版本的别名。

维护版本通道时，应分别读取 `linux/amd64` 与 `linux/arm64` 镜像的 config labels，核对 `org.opencontainers.image.version`、`org.opencontainers.image.source`、`org.opencontainers.image.revision` 对应的版本、仓库和提交一致；兼容 Docker v2 manifest list 与 OCI index，不能仅依赖顶层索引 annotations。本版实际产物为 Docker v2 manifest list。

## 下载附件怎么选

新发布的客户端、部署包和源码包以对应标签（如 `treasure-up-vX.Y.Z-`）开头；表格描述当前主线的产物，不改写 v0.3.2 等历史发行附件：

| 附件后缀 / 名称 | 用途 |
| --- | --- |
| `docker.zip` | 服务端预构建镜像部署文件，默认固定 digest |
| `source.zip` | 该标签提交中跟踪的源码，不含本地未提交数据 |
| `ios-simulator-ad-hoc.zip` | Mac 上临时签名的 iOS 模拟器应用（具体架构见包内构建记录），**不是可装到 iPhone / iPad 的 IPA** |
| `treasure-up.user.js` | Tampermonkey 选片助手脚本 |
| `RELEASE-NOTES.md`、`manifest.json`、`release-images.json`、`SHA256SUMS` | 变更说明、来源与镜像记录、文件大小和校验和 |

客户端只连接自己的 Treasure Up 服务；安装客户端不会在手机或电脑里部署 PostgreSQL、下载器或 FFmpeg。当前没有自动更新器。编译通过不等于已完成所有真机、签名、商店或系统解码兼容验收，详见 [原生客户端说明](../native/README.md)。

下载后核对 `SHA256SUMS`。Linux 下载齐附件时可执行 `sha256sum --check SHA256SUMS`；Windows 可用 `Get-FileHash 文件名 -Algorithm SHA256` 对照对应一行。哈希核对用于检查内容完整性，不能替代代码签名或可信发布来源。

## 发版失败如何处理

优先打开失败运行，查看失败阶段，然后使用 GitHub 的 **Re-run failed jobs**。这会尽量保留此前已成功阶段的产物；不要默认选择重新执行所有作业或新建一轮相同标签的全量构建。

| 现象 | 处理方向 |
| --- | --- |
| `validate` 失败 | 核对版本文件、标签对应提交和发行说明；代码需要改变时创建新版本，不移动已经发布的标签 |
| 依赖审计、回归或编译失败 | 根据明确日志修复；不要跳过验证后手动发布不完整附件 |
| GHCR 上传被拒绝 | 核对仓库 / 包权限与 Actions 写包权限；不要把认证或网络失败当成“版本不存在” |
| 用户拉取提示 denied / unauthorized | 检查 backend、web 两个包是否都公开；私有部署使用有权限的 Docker 登录配置 |
| `package` 或 `publish` 临时失败 | 优先仅重跑失败作业；草稿可以续传，已公开的 Release 不允许替换 |
| 版本标签已指向不同 digest | 不覆盖旧版本。全量重建可能因构建时间或工具链等产生不同 digest；保留原版本，必要时递增版本号重新发布 |
| 部署 `pull` 或健康检查失败 | 保留 `.env`、数据卷和原版本信息，核对仓库访问与服务状态；不要用删除卷解决升级错误 |

镜像提升可能已经成功，而最终 Release 发布尚未成功，所以失败不等于所有远程操作都已回滚。受保护的版本标签和 digest 检查会阻止把一次重建悄悄换成旧版本的内容。数据库升级也不承诺自动回滚；恢复操作遵循 [备份与恢复说明](operations.md#备份和恢复)。
