# 发版指南

发版入口：[Actions → Release](https://github.com/guoweiyi/treasure-up/actions/workflows/release.yml)。普通用户见[安装说明](installation.md)。

## 一次性配置 Docker Hub

在 Docker Hub 的 `yunyunjuan` 命名空间下创建两个 **Public** 仓库：

- `yunyunjuan/treasure-up-backend`
- `yunyunjuan/treasure-up-web`

创建有这两个仓库读写权限的 [Docker Hub Access Token](https://docs.docker.com/security/access-tokens/)。GitHub 仓库 → **Settings → Secrets and variables → Actions → Secrets**，添加：

| Secret | 内容 |
| --- | --- |
| `DOCKERHUB_USERNAME` | 登录 Docker Hub 的账号名，例如 `yunyunjuan` |
| `DOCKERHUB_TOKEN` | Docker Hub Access Token，不是账号密码 |

默认镜像命名空间是 `yunyunjuan`。组织账号或 fork 可在同页 **Variables** 设置 `DOCKERHUB_NAMESPACE`；它是镜像归属命名空间，登录账号仍使用 Secret。不要将 Token 写入 YAML、README、Issue 或日志。

Actions 需要允许工作流使用 `GITHUB_TOKEN` 写入版本提交、标签和 Release。流程只给实际需要写入的 Job 授权；仓库分支规则若禁止机器人直接写入默认分支，发版前要按仓库策略配置允许的发布方式。不要为发版关闭其他安全检查。

## 手动发布

1. 合并准备发布的代码，确认默认分支的 Verify 通过。
2. 打开 **Release → Run workflow**，选择默认分支。
3. 选择 `mode`：

   | 模式 | 用法 |
   | --- | --- |
   | `patch` | 自动增加补丁版本，如 `0.3.4 → 0.3.5` |
   | `minor` | 自动增加次版本，如 `0.3.4 → 0.4.0` |
   | `major` | 自动增加主版本，如 `0.3.4 → 1.0.0` |
   | `custom` | 在 `version` 填写目标版本，例如 `0.4.0` |
   | `retry` | 重试一个尚未公开发布的既有版本，在 `version` 填它的版本或标签 |

4. 选择 `channel`：默认 `preview`；确认作为稳定版本推广时选 `stable`。`retry` 使用原标签中记录的渠道。
5. 点击运行。流程检查凭据，准备版本提交与标签，然后对**同一个提交**执行全部验证和构建；不需要自己修改多处版本号或再推一次标签。

新版本按已有版本标签继续递增；已公开版本不覆盖，修复后发布下一个版本。首次切换 Docker Hub 请发布新版本，不要试图把已有 GHCR Release 当作新的 Hub 发布重跑。

## 流程与产物

| 阶段 | 检查或产物 |
| --- | --- |
| 版本准备 | 校验输入与凭据，生成一致的版本声明、发布说明、提交和标签 |
| Verify | 锁定依赖审计、前端测试 / 构建、独立环境中的 Docker 初始化、登录和服务就绪检查 |
| iOS | iPhone / iPad 模拟器离线测试，以及独立的 `iphoneos` / `arm64` Release 构建 |
| Docker | 在 `amd64` / `arm64` 原生 Runner 上分别构建、启动和测试，通过后合并多架构镜像 |
| 发布 | 校验提交、镜像摘要和 IPA 平台，上传附件，最后公开 GitHub Release |

公开 Release 附件包括：

- `treasure-up-vX.Y.Z-docker.zip`：可直接运行的 Compose 部署包，固定镜像摘要。
- `treasure-up-vX.Y.Z-ios-unsigned.ipa`：iPhone / iPad **真机**应用，供用户自签名；安装指引随发布说明提供。
- 源码包及校验清单：以该次 Release 实际附件列表为准。

模拟器 ZIP 只作为 CI 调试附件，不当作 IPA 分发。服务端版本对应整次 Release，IPA 内的 App 版本使用原生工程自己的版本号。

Docker Hub 提供版本标签与渠道标签；`preview` 更新预览通道，`latest` 仅由稳定通道更新。较旧版本重试不会把渠道标签退回旧版。生产部署推荐使用 Release 部署包的摘要固定方式，不依赖可变标签。

## 失败与重试

- **未填写 Secrets**：先填写，再运行；不要把凭据粘到日志或提交中。
- **版本已创建但后续失败**：修复配置 / 外部服务后，可在原工作流重跑失败 Job，或选择 `retry` 和原版本。它继续使用原标签指向的提交。
- **需要修改代码**：修改代码后发新版本；原标签仍指向旧代码，重试不会自动吸收修复。
- **镜像推送失败**：确认 Token 权限、命名空间和两个仓库的可见性。失败时不会公开一个缺少必要附件的新 Release。
- **iOS 构建失败**：查看设备构建与模拟器测试各自日志。不可用模拟器 ZIP 改后缀替代真机包，也不跳过 iOS 检查发版。
- **已有公开 Release**：保持原资产与镜像不变，选择更高版本。

`containers.yml` 是 Release 调用的内部工作流，日常发布只操作一个入口。旧 GHCR 自动推送逻辑已经移除；历史 Release 不删除，已有用户可继续按原版本说明使用。

流程设计参考 [Study-Mate 的手动版本发布入口](https://github.com/Miaotofu01/Study-Mate/blob/main/.github/workflows/release.yml)，结合 [Docker 的推送前测试](https://docs.docker.com/build/ci/github-actions/test-before-push/)与[多架构构建](https://docs.docker.com/build/ci/github-actions/multi-platform/)。版本提交后在同一次运行内继续验证，避免依赖 `GITHUB_TOKEN` 推送产生新的流水线（[GitHub 触发规则](https://docs.github.com/en/actions/how-tos/writing-workflows/choosing-when-your-workflow-runs/triggering-a-workflow)）。
