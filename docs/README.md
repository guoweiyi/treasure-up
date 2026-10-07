# 文档导航

当前使用与维护以这些文档为准：

- [部署、升级、备份恢复](operations.md)
- [存储源与直连分发](storage-providers.md)
- [登录、访问权限、UP 采集与来源元数据](access-and-capture.md)
- [B 站扫码授权、会话续期与源站失效检查](bilibili-session-research.md)
- [标签、置顶评论和前台采集接口](catalog-capture.md)
- [油猴选片助手安装与排查](userscript-guide.md)
- [发布流水线](ci-cd.md)
- [音频诊断](audio-diagnostics.md)
- [iOS / iPadOS 工程与验证](../native/apple/README.md)

`releases/` 是已发布版本的说明；`incidents/` 保留故障原因和修复依据；`research/` 以及带日期的迭代记录描述当时的方案和验收范围，不代表最新产品行为。

代码目录：`backend/` 服务与采集，`frontend/` 网页与油猴脚本，`native/apple/` 原生客户端，`deploy/` 部署与构建，`assets/branding/` 品牌设计源稿。生成的客户端包、媒体、凭据、临时调试记录与依赖不进入版本库。
