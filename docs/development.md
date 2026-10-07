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

提交前执行与改动有关的检查。前端使用 `npm run test`、`npm run build`；安装流程在独立 Docker 项目中验证；iOS 由 macOS Actions 构建和测试。账号、Cookie、`.env`、媒体与构建产物不提交。

## 提交与发版

提交说明使用类型加中文描述，例如：

```text
feat: 增加收藏夹自动备份
fix: 修复长视频下载中断后的续传
docs: 精简安装说明
chore: 更新发布配置
```

服务端版本由 `release.json` 管理，原生 App 独立维护版本。合并后通过 [Release Actions](publishing.md)发版，不手动覆盖已发布的版本标签。
