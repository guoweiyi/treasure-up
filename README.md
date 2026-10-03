# Treasure Up

一个登录后访问的私人 B 站视频归档库。保存视频、多 P、弹幕、字幕、评论和公开人员资料，并在自己的站点搜索、播放和整理。

技术栈：Vue 3 / TypeScript / Artplayer / hls.js、FastAPI、PostgreSQL 17、Celery / Redis、FFmpeg、Nginx。媒体支持本地磁盘、S3 兼容对象存储和原生阿里云 OSS；Cookie 只在服务端加密保存。

**当前为 0.2 技术预览。** 增加无损 HLS 分片、杜比原档验证、存储节点选路与副本管理、定时统计更新，并重做视频和 UP 页面。真实 S3 / OSS 桶和新一轮 B 站在线采集尚待验收。详细边界见 [实施状态](docs/implementation-status.md)，完整设计见 [技术计划](docs/platform-plan.md)。

## 本机启动

需要 Docker Compose v2 和 Python 3.10+。Windows 需启动 Docker Desktop 的 Linux 引擎。

```bash
git clone https://github.com/guoweiyi/treasure-up.git
cd treasure-up
python deploy/bootstrap.py
docker compose up -d --build
```

访问 <http://127.0.0.1:8788>。初始账号为 `admin`，随机密码位于本机 `.env` 的 `TREASURE_ADMIN_PASSWORD`。初始化不覆盖已有管理员密码；重复运行 bootstrap 会拒绝覆盖 `.env`。

Compose 默认仅监听回环地址，数据库、Redis 不发布主机端口。所有新收藏来源默认关闭，不会自动采集。登录后台后可添加授权账号、验证账号、配置收藏夹 ID 与采集策略，再手动触发扫描；验证与扫描结果都在任务中心查看。

## 已提供的功能

- 视频库、收藏夹、UP 主目录和独立主页，支持标题、标签、昵称及历史昵称搜索。
- 多 P 播放、进度保存、字幕、弹幕字体 / 字号 / 字重 / 描边 / 颜色 / 区域 / 速度配置。
- 原档优先播放、长视频无损 fMP4 HLS 分片、受限缓冲、可选播放音量平衡；杜比视界 / 全景声按媒体证据标注。
- 评论与楼中楼、评论作者昵称和头像快照、评论图片；人工标注与来源数据分离。
- 后台账号、收藏来源、任务暂停 / 继续 / 重试、存储配置 / 探测 / 迁移、备份记录、系统配置、用户权限与审计。
- 可恢复任务检查点、账号限速、过期任务接管与旧 Worker 提交隔离。
- 持久化账号请求 / 视频间隔与风控冷却；按小时更新播放、点赞、投币、收藏、分享、评论及弹幕计数，可选重新采集弹幕。
- 按视频同步完整资产到存储节点、浏览器限额测速选路、播放中有限故障切换；副本停用 / 恢复 / 显式物理删除。
- SHA-256 内容寻址、迁移后读回校验、原副本保留；数据库与媒体的一致快照加密备份和空环境恢复。

“完整”只表示当前接口可见范围已遍历，不承诺找回平台已经删除、隐藏或不可访问的数据。人员资料指公开 UID、昵称、简介、头像等，不推断真实姓名。

弹幕直接使用已保存的数据和显示配置，不包含语义审核、本机模型或模型服务依赖。无损分片保留原始压缩音视频；兼容转码是另存的可选副本，不覆盖原档。杜比归档成功不等于所有浏览器和设备均能输出 HDR 或空间音频。

## 部署与维护

[运维说明](docs/operations.md) 包含存储参数、备份密钥、恢复、升级与验证命令。不要使用 `docker compose down -v` 卸载日常服务，它会删除持久卷。

`.env`、`.private/`、媒体、运行数据、依赖及日志均不提交。首次部署创建空库；仓库不包含账号 Cookie、演示视频或个人收藏数据。此前 Node 实验仅保留在原工作区，不属于本项目运行依赖。

## 验证

```bash
python -m venv .venv
# 激活虚拟环境后：
python -m pip install -r backend/requirements.lock
python -m pytest
npm --prefix frontend ci
npm --prefix frontend run build
```

真实 PostgreSQL / FFmpeg 备份恢复测试需显式启用，在已启动的 Docker 环境运行：

```bash
docker compose exec -T -e TREASURE_RUN_POSTGRES_TESTS=1 backup-worker python -m pytest tests -q
```

该测试只创建和清理随机 `treasure_test_*` 数据库及独立临时目录，不操作业务数据库。GitHub Actions 使用同一 Docker 路径验证。

播放器基于 [Artplayer](https://github.com/zhw2590582/ArtPlayer) 及其弹幕插件；下载使用 [yt-dlp](https://github.com/yt-dlp/yt-dlp)。项目未复制 B 站闭源播放器源码。各依赖按自身许可使用。
