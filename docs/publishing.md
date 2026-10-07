# 发版

## 首次配置

Docker Hub 使用两个公开仓库：

- `yunyunjuan/treasure-up-backend`
- `yunyunjuan/treasure-up-web`

在 GitHub 仓库 **Settings → Secrets and variables → Actions** 添加：

| Secret | 值 |
| --- | --- |
| `DOCKERHUB_USERNAME` | Docker Hub 登录账号 |
| `DOCKERHUB_TOKEN` | 有仓库读写权限的 Access Token |

命名空间默认 `yunyunjuan`。fork 项目可以用 Actions Variable `DOCKERHUB_NAMESPACE` 修改，安装时追加 `--namespace 自己的命名空间`。GitHub 的分支规则需允许发布流程提交版本号和创建标签。

## 发布版本

打开 [Actions → Release](https://github.com/guoweiyi/treasure-up/actions/workflows/release.yml)，点击 **Run workflow**：

1. 选择默认分支。
2. `mode` 选择 `patch`、`minor` 或 `major` 自动升版；`custom` 可在 `version` 指定新版本。
3. `channel` 选择 `stable` 或 `preview`。稳定版更新 Docker 的 `latest`，预览版更新 `preview`。

流程会提交版本号，完成检查后推送 `amd64` / `arm64` 镜像，并发布 GitHub Release。附件包括 iOS 真机 IPA、自签名说明、Compose 部署包、源码和校验清单。

失败后可重跑失败 Job。若版本标签已创建，也可选 `retry` 并填写原版本；它使用原代码和渠道。代码有修改时应发新版本，不要覆盖原标签。部分镜像已发布时，回到原运行记录重跑失败 Job。

普通发版只操作 Release，`containers.yml` 由它调用。历史 GHCR 版本保留，新版统一发布到 Docker Hub。
