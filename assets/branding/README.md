# Treasure Up 品牌资源

`logo-original.png` 是小云提供的 1254 × 1254 原始 logo，保留原文件。`logo-transparent.png` 是经核对的透明母图，由内置 imagegen 做背景提取；人物、电视、星形和完整英文名称保留。应用界面不要用 CSS 色彩滤镜重着色，也不要改变图片比例。

小尺寸入口使用去掉文字的角色 `logo-mark.png`，登录和关于页使用完整 `logo-lockup.png`。深色容器用浅色品牌底保留深蓝描边和英文名称的对比度。应用图标用白底 RGB；PWA maskable 图标单独保留中心安全圆，不和普通图标混用。Web 与原生展示资源逐字节相同。

生成脚本只做裁切、尺寸与格式适配。开发机安装 `sharp` 后运行 `node deploy/build_branding.mjs`，也可将已安装模块的路径作为唯一参数。执行后运行项目的 Prettier 格式化及 `python deploy/check_branding.py`。原始画面坐标用于裁切文字，更换不同尺寸母图时需同时修改脚本并核对输出。

前端资源位于 `frontend/public/brand/`，清单 `assets.json` 记录尺寸、透明通道和 SHA-256。favicon、旧 `app-icon.svg` 路径、Apple 180px、PWA 192/512px、1024px原生图标、1200 × 630分享图和选片助手的内嵌图片统一生成。

网页组件按实际显示尺寸加载 128 / 256 / 512 / 1024px 图片，导航人物通常加载 128px（约 24KB），减少小图占用的下载带宽。

透明母图的生成提示：

> Remove only the white/light textured background to actual transparency. Keep the existing blue-haired chibi mascot, every shape, face, hands, little television/star, dark navy outlines, original pale blue palette, and the complete exact 'Treasure Up' wordmark below exactly as supplied. Preserve the white/cream face and television interior as opaque artwork. Keep the entire original logo and wordmark centered with transparent outside margins. No redesign, added frame, or extra text.
