# 开发

## 目录

- `backend/`：API、数据模型、采集和后台任务。
- `frontend/`：网页、播放器、管理后台和油猴脚本。
- `native/apple/`：iPhone / iPad App。
- `deploy/`：安装、镜像构建和发布脚本。
- `.github/workflows/`：持续检查和发版。

## 本地启动

需要 Docker Compose v2、Python 3.11+：

```sh
git clone https://github.com/guoweiyi/treasure-up.git
cd treasure-up
python deploy/start.py
```

前端开发使用 Node.js 22，在 `frontend` 目录执行 `npm ci` 和 `npm run dev`。iOS 需要 macOS 与 Xcode，见 [原生工程](../native/README.md)。

提交前执行与改动有关的检查。前端使用 `npm run test`、`npm run build`，发版 Web 镜像构建时也会执行；后端和安装镜像检查 Python 编译，Web 镜像检查 Nginx 配置；iOS 由 macOS Actions 打包，原生测试按需在 Xcode 运行。账号、Cookie、`.env`、媒体与构建产物不提交。

## 提交与发版

提交说明使用类型加中文描述，例如：

```text
feat: 增加收藏夹自动备份
fix: 修复长视频下载中断后的续传
docs: 精简安装说明
chore: 更新发布配置
```

服务端版本由 `release.json` 管理，原生 App 独立维护版本。Verify 在 PR 和 `main` 推送时检查依赖，Release 会再校验所发布版本的依赖。

合并后打开 [Release Actions](https://github.com/guoweiyi/treasure-up/actions/workflows/release.yml)，选择 `main` 并点击 **Run workflow**：

- 新版本使用 `patch`、`minor`、`major`，或用 `custom` 填写指定版本。正式版选择 `stable`，预览版选择 `preview`。
- 已创建标签但尚未发布成功的版本使用 `retry`，并填写原版本号，例如 `0.3.9`；渠道沿用该标签中的配置。
- 修改流水线后，重新 **Run workflow** 才会使用新的流程；旧运行的 **Re-run jobs** 仍使用旧流程。`retry` 使用原标签的源码，因此源码或发布脚本修复需要发新版本。

发布流程保留依赖审计、amd64/arm64 镜像构建与合并、iOS IPA 打包、附件校验和 Docker Hub / GitHub Release 发布。不手动覆盖已发布的版本标签。
