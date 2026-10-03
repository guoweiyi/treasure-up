# Treasure Up 前端

正式 Vue 3 / TypeScript / Vite SPA，消费同源 `/api/v1`，不访问 B 站接口。

```sh
npm ci
npm run dev
npm run typecheck
npm run build
```

开发代理默认 `http://127.0.0.1:8000`，可用 `API_PROXY_TARGET` 覆盖。生产输出在 `dist/`；Nginx 对应用路由回退到 `index.html`，并代理 `/api/`。登录使用服务端 HttpOnly 会话 Cookie，写请求带 `X-CSRF-Token`。

浏览器 localStorage 只存弹幕显示偏好。采集 Cookie、本站密码和存储凭据仅通过表单发给服务端，不写入浏览器存储。界面展示后端返回状态，不提供演示数据。

前台：视频库/本地搜索、收藏筛选、UP 目录/主页、多 P/画质/字幕切换、Artplayer 弹幕、进度保存、评论分页和楼中楼。

后台：总览、采集账号/验证、来源/扫描、任务操作、视频和 UP 标注、本地/S3/OSS 配置/探测/迁移、备份任务/记录、系统配置、用户权限、审计。具体执行能力由后端返回；已提交任务不表示已经完成。
