# Treasure Up · iOS / iPadOS 客户端

本目录只保留原生 Swift 客户端，直接访问 Treasure Up 后端 API。支持 iPhone 和 iPad，Bundle ID 为 `com.guoweiyi.treasureup`。

```sh
# 在仓库根目录打开 Xcode 工程
open native/apple/TreasureUp.xcodeproj
```

- [工程、构建命令与验收记录](apple/README.md)
- [XcodeGen 工程配置](apple/project.yml)
- [iPhone / iPad 离线构建与测试流水线](../.github/workflows/ios.yml)

`apple/` 包含 App、单元测试、离线 UI 测试及播放器排查记录。构建只需要完整 Xcode；修改工程结构时使用 XcodeGen，不再依赖 Node、npm、Rust 或 Tauri。

iOS App 版本由 `apple/project.yml` 独立维护；服务端发布版本由仓库根目录的 `release.json` 管理。CI 产物为临时签名的模拟器应用，不能作为真机 IPA 安装；真机签名请参照工程说明。
