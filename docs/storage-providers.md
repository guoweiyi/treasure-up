# 存储源与视频直连

视频和音频以原始媒体文件保存，不另加应用层加密。访问密钥、OAuth 刷新令牌仍加密保存，前台只获得短期读取地址，不会获得 AK/SK。稿件文件采用可读名称；摘要仍用于完整性校验和安全删除。

## 访问方式

对象存储的默认播放路径是：浏览器向 Treasure Up 请求播放信息，服务端校验副本并生成短期地址，浏览器直接向存储服务或 CDN 读取视频字节。保存视频、校验文件和创建播放地址需要服务器参与，但不意味着播放流量经服务器中转。

表单将地址分为三个用途：

- **API 地址**：服务器上传、校验和删除对象使用的地址，可以是内网入口。
- **外网签名 API 地址**：浏览器能访问的对象存储 API 入口，用于服务端生成签名。私有 S3 签名绑定 Host，不能签完再替换成另一个域名。
- **访问域名**：已绑定的自定义域名或 CDN 域名；对象路径会进行正确的 Unicode/空格编码。OSS、OBS 使用原生 CNAME 签名；七牛使用原生 CDN 下载签名；又拍云使用 CDN Token 防盗链。S3 类服务的自定义 CDN 入口应选择公开读取或 CDN 回源鉴权。

“私有空间”描述的是浏览器读取入口是否需要存储签名。腾讯云 CDN 已开启回源鉴权时，即使后面的桶本身私有，CDN 的读取入口可能无需对象签名，此时应关闭该选项。它不会修改桶 ACL，也不会自动将桶公开。

“自动”优先直连；“直连”明确使用直接地址；“兼容跳转”保留应用地址，由应用返回重定向到存储服务。重定向仍由浏览器接收对象存储字节。Microsoft Graph 获取每个文件的临时地址需要一次远程 API 请求，因此其 HLS 分片采用按需重定向，避免打开视频时先请求数百个分片地址。Graph 单文件可直接访问。

## 服务支持范围

| 服务 | 数据协议 | 自动选择 | 上传与续传 | 自定义域名读取 |
| --- | --- | --- | --- | --- |
| 本地 / NAS | 文件系统 | 手填挂载路径 | 原子写入、完整性校验 | 站点媒体入口 |
| 阿里云 OSS | OSS V4 原生 SDK | 桶、区域、外网 API | 原生分片、记录恢复 | 原生 CNAME 签名或公开读取 |
| 腾讯云 COS | 官方支持的 S3 兼容 API | 桶；响应提供区域时回填 | S3 分片、记录恢复 | 公开/CDN 回源鉴权；私有桶用签名 API |
| 华为云 OBS | OBS 原生 SDK | 桶、区域、API | 原生分片、记录恢复 | 原生 CNAME 签名或公开读取 |
| 又拍云 | 原生 REST API | 操作员 API 无服务枚举能力，手填服务名 | 流式上传，失败后整文件重试 | CDN Token 或公开读取 |
| 七牛 Kodo | 官方支持的 S3 兼容 API | 桶 | S3 分片、记录恢复 | 原生私有 CDN 签名或公开读取 |
| MinIO、雨云 S3 | S3 | 桶 | S3 分片、记录恢复 | 公开 CDN 或签名 API |
| DigitalOcean Spaces | S3 | 桶 | S3 分片、记录恢复 | 公开 CDN 或签名 API |
| Cloudflare R2 | S3，区域 `auto` | 桶，需账号级列举权限 | S3 分片、记录恢复 | 公开自定义域名；私有读取使用 S3 API |
| Oracle 对象存储 | S3 兼容 API，Customer Secret Key | 桶 | S3 分片、记录恢复 | 公开 CDN 或签名 API |
| Backblaze B2 | S3 兼容 API | 桶，受应用密钥范围限制 | S3 分片、记录恢复 | 公开 CDN 或签名 API |
| 通用 S3 | SigV4 | 桶 | S3 分片、记录恢复 | 公开 CDN 或签名 API |
| OneDrive 国际 / 世纪互联 | 各自 Microsoft Graph API | 云盘 | 小文件上传；大文件上传会话与续传 | Microsoft 返回的临时下载地址 |
| SharePoint 国际 / 世纪互联 | 各自 Microsoft Graph API | 指定站点的文档库 | 同 OneDrive | Microsoft 返回的临时下载地址 |

厂商 API 的兼容范围、账号权限、区域、桶策略仍需在实际账号上验证。无需凭据的自动测试覆盖适配器协议、签名、字节范围、配置校验与错误隐藏，不能替代各厂商真实账号联调。即时探测验证小文件的上传、元数据、读取、Range 与删除，并单独检查外网地址的 Range/CORS 响应头；这是服务端检查，不能代替实际用户网络下的浏览器播放，也不代表大文件分片上传已通过。

2026-10-05 已在 Docker 预览环境联调本地存储和阿里云 OSS：即时读写及清理通过，保存的凭据能够获取桶列表，私有桶签名地址直接返回 `206 Partial Content`，没有转发站点 Cookie 或 CSRF 凭据。联调发现桶未配置 CORS，按实际站点域名补齐只读跨域规则后通过。其他厂商目前完成协议与模拟传输测试，尚未逐一使用真实账号验证；不要将适配器测试视为已通过全部厂商的实际部署验收。

## 自动发现与权限

填写 AK/SK 或刷新令牌后，可获取桶或文档库列表，再选择并回填区域与地址。编辑已有存储时，密钥留空表示使用已保存凭据。列表只返回名称、标识、区域和地址，不返回密钥。

发现最多返回 500 个桶/文档库，Microsoft 分页最多 5 页。网络连接与读取使用短超时且不重试长任务。列举权限通常独立于指定桶的读写权限；若账号只能访问一个桶，自动获取可能被拒绝，但仍可手工填写该桶并探测。又拍云的操作员不能列出账户全部服务，因此需从控制台填写服务名。

探测在请求内完成，不创建后台任务；它会写入随机小文件、读取校验并删除，要求对应目录有写、读、删除权限。不可仅为了浏览器播放而授予公开写入权限。

## NAS 文件布局

新采集的原视频保存为 `videos/稿件-UP主 [BV号]-P01-分P标题 [SHA256].mp4`，保留实际的 MP4、MKV 等容器扩展名；兼容播放副本另有标识。摘要用于区别同名稿件和不同编码版本，文件内容没有应用层加密，可直接在 NAS 上打开。标题会过滤非法路径字符并限制文件名长度。

现有归档不自动重命名或搬运，避免影响正在播放及续传的文件。将旧视频显式同步到新存储节点时，会为目标生成可读名称；已有目标副本和未完成上传继续沿用其原来的对象键。封面、弹幕、评论素材与分片继续使用原有布局。备份恢复、副本清理均识别两种命名。

## Microsoft 授权

先在对应云的 Microsoft Entra 中注册应用。个人 OneDrive 需支持个人账号；世纪互联应用和令牌必须从中国云获取，不能与国际云混用。记录应用 ID，按注册类型填写客户端密钥，并通过 Microsoft 官方授权码流程获取刷新令牌。申请 `offline_access` 和匹配读写目标的委托权限；SharePoint 文档库还需账号本身有站点访问与写入权限。表单接收既有授权结果，不托管 OAuth 回调或要求用户提供 Microsoft 密码。

OneDrive 选择“获取云盘”；SharePoint 先输入站点 ID，或类似 `tenant.sharepoint.com:/sites/Archive` 的站点标识，再获取文档库。世纪互联站点使用相应中国云域名。完整的 `https://` 网页链接不能直接作为站点 ID。

刷新后的令牌继续加密保存；并发刷新通过条件更新避免覆盖管理员的新凭据。大文件按 10 MiB 顺序上传，满足 Graph 分片为 320 KiB 倍数的要求。上传会话 URL 具有临时写入能力，因此续传记录只保存其加密值。读取地址由 Microsoft 控制有效期；前端应及时续期，而不是永久收藏带令牌的下载 URL。

参考 [Microsoft 授权码流程](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow)、[Graph 国家云端点](https://learn.microsoft.com/zh-cn/graph/deployments)、[上传会话规范](https://learn.microsoft.com/graph/api/driveitem-createuploadsession)、[下载文件内容](https://learn.microsoft.com/en-us/graph/api/driveitem-get-content)。

## CORS 与 HTTPS

跨域播放尤其是浏览器 HLS 需要存储桶/CDN 允许站点的 HTTPS Origin、`GET`、`HEAD` 和 `Range` 请求，并暴露播放器或探测所需的 `Content-Length`、`Content-Range`、`Accept-Ranges`、`ETag` 响应头。允许的 Origin 填实际网站地址；签名 URL 不依赖站点登录 Cookie。网站使用 HTTPS 时，对象地址也应为 HTTPS，以免浏览器阻止混合内容。应用不会未经选择修改厂商 CORS、ACL 或 CDN 策略。

## 研究依据

本实现参考 ZFile 将上传 API、下载域名、私有签名与代理入口分别配置的方式；没有移植其 Java 服务或引入对应运行时。

- [ZFile 存储读取基础类](https://github.com/zfile-dev/zfile/blob/main/src/main/java/im/zhaojun/zfile/module/storage/service/base/AbstractS3BaseFileService.java)
- [ZFile 桶列表发现](https://github.com/zfile-dev/zfile/blob/main/src/main/java/im/zhaojun/zfile/module/storage/controller/helper/S3HelperController.java)
- [ZFile 又拍云读取与 Token](https://github.com/zfile-dev/zfile/blob/main/src/main/java/im/zhaojun/zfile/module/storage/service/impl/UpYunServiceImpl.java)
- [ZFile 七牛 S3 与 CDN 分离](https://github.com/zfile-dev/zfile/blob/main/src/main/java/im/zhaojun/zfile/module/storage/service/impl/QiniuServiceImpl.java)
- [ZFile 前端](https://github.com/zfile-dev/zfile-vue)
- [腾讯云官方 S3 兼容说明](https://cloud.tencent.com/document/faq/436/50743)
- [七牛官方 S3 工具兼容说明](https://developer.qiniu.com/kodo/4096/s3-compatible-sdk)
- [华为云 OBS Python SDK](https://github.com/huaweicloud/huaweicloud-sdk-python-obs)
- [又拍云官方 REST 客户端](https://github.com/upyun/python-sdk/blob/master/upyun/rest.py)与[鉴权算法](https://github.com/upyun/python-sdk/blob/master/upyun/modules/sign.py)

S3 SigV4 的 Host 参与签名，因此本项目没有照搬“签名后任意替换域名”的做法；OBS SDK 默认关闭主机名核验，本适配器显式启用证书链和主机名核验。OSS、OBS 服务端读写和列表请求也拒绝自动重定向，避免对象正文或临时 STS 凭据被带到其他主机。

七牛区域与地址依据[当前官方 S3 示例](https://developer.qiniu.com/kodo/12572/aws-sdk-rust-examples)使用具体区域（例如 `cn-east-1`）和 `https://s3.cn-east-1.qiniucs.com`；不沿用旧版 ZFile 中固定 `kodo` 区域的写法。
