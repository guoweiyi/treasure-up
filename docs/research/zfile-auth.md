# ZFile 鉴权、初始化与 Treasure Up 实现记录

调查日期：2026-10-04。这里只借鉴公开设计与故障经验，没有运行 ZFile 安装脚本，也没有连接现有 Treasure Up 账户或修改部署密码。

## 实际查阅范围

公开仓库树记录为 `775b0172f3b1d3f68aabca727ef7e083e6bd5aa5`。静态阅读了安装 controller/service、登录 controller/service、密码 verifier、Sa-Token 配置、安全 filter、默认 properties 和示例 Dockerfile；查阅 1 个相关 PR、2 个相关 issue、近期提交元数据。不是全仓安全审计，未验证报告中的攻击方法。后续 GitHub API 请求遇到限流，不据此补写未读 diff。

[ZFile README](https://github.com/zfile-dev/zfile) 说明 5.x 已发布，但捐赠版代码仍有后续拆分开源安排；检查到的公开 Maven 配置仍标记 4.5.0。因此以下结论限定于已读公开代码，不代表已覆盖当前发布二进制或所有 5.x 功能。

| 主题 | 可核实事实 | Treasure Up 决策 |
| --- | --- | --- |
| 首次初始化 | `InstallController` 提供状态与安装入口；`InstallService` 在事务中检查 installed 标记、创建管理员并设置站点状态。[源码](https://github.com/zfile-dev/zfile/blob/main/src/main/java/im/zhaojun/zfile/module/install/service/InstallService.java) | 采用本机初始化脚本，独占创建环境文件；已有文件不读取、不覆盖、不重置账户。无需暴露公开的首次管理员创建页面。 |
| 登录校验 | 登录 service 顺序执行 verifier；密码 verifier 对未知用户与密码不匹配使用相同业务错误。[密码验证源码](https://github.com/zfile-dev/zfile/blob/main/src/main/java/im/zhaojun/zfile/module/user/service/login/verify/impl/PasswordVerifyService.java) | 通行密钥登录不输入用户名、不返回凭据列表；认证失败统一提示。密码登录继续保留。 |
| 管理权限与限流 | Sa-Token 配置约束管理路径；登录 controller 的实际处理经过 Spring 代理以保留 AOP，并标记每分钟 10 次限制。[配置源码](https://github.com/zfile-dev/zfile/blob/main/src/main/java/im/zhaojun/zfile/core/config/security/SaTokenConfigure.java)、[controller](https://github.com/zfile-dev/zfile/blob/main/src/main/java/im/zhaojun/zfile/module/user/controller/UserController.java) | 所有管理写入沿用用户会话及 CSRF；WebAuthn 单独限流，PostgreSQL 事务 advisory lock 序列化配额预留，失败也计数。 |
| SSO | [PR #741](https://github.com/zfile-dev/zfile/pull/741) 已合并，涉及单点登录配置与回调。 | SSO 不等于 WebAuthn。在本次已读鉴权路径中没有找到可直接复用的通行密钥实现，不声称全项目都没有。 |
| 真实问题记录 | [issue #780](https://github.com/zfile-dev/zfile/issues/780) 是 API 登录用法问题，不能当接口规范；[issue #821](https://github.com/zfile-dev/zfile/issues/821) 报告代理下载路径鉴权问题，未在本次复现。 | 媒体、HLS manifest、segment、签名分发仍逐请求鉴权；前端隐藏按钮不构成权限校验。 |
| 提交线索 | [da4493b](https://github.com/zfile-dev/zfile/commit/da4493bf46f7477ff0308d7e4f34f5b424e14fa4) 的登录日志修复标题与当前 controller 代理调用注释相符；[6afb604](https://github.com/zfile-dev/zfile/commit/6afb6046623e5255f25e2f2c9d87fd9bd408462e) 的标题提及 #821 路径隔离修复。 | 本次查到提交元数据，未完成这两个提交的逐行 diff 审计；不把标题等同于已验证漏洞修复。 |
| Docker | 公开 [Dockerfile](https://github.com/zfile-dev/zfile/blob/main/Dockerfile) 注释明确它只是示例，与正式 Graal Native 构建不同。 | 不照搬无法验证的体积/内存数字；保留 PostgreSQL、Redis 与独立备份 worker 的实际保障。 |

## WebAuthn 安全边界

采用 Duo Labs 的 [py_webauthn](https://github.com/duo-labs/py_webauthn)，固定 `webauthn==3.0.1` 及其新增依赖；版本由 [PyPI 元数据](https://pypi.org/pypi/webauthn/json) 核对。官方 [注册](https://duo-labs.github.io/py_webauthn/registration.html) 与 [认证](https://duo-labs.github.io/py_webauthn/authentication.html) 文档说明生成 options、校验响应的入口；文档页标题版本较旧，实际函数与本地安装的 3.0.1 源码和签名测试核对。

服务端固定配置完整 origin 和 RP ID，要求 HTTPS DNS 站点，开发例外仅 `http://localhost:8788`。本实现要求 RP ID 精确等于 origin 主机，不共享父域 RP；`127.0.0.1` 不能代替 localhost RP。能力接口会报告访问地址不匹配、配置错误或功能关闭，代理后的检测使用浏览器上报的 origin 作为展示提示，绝不据此放宽验证策略。

挑战为随机 32 字节、默认 5 分钟、最多 10 分钟，数据库条件更新一次性消费；即使签名失败也不再使用。登录挑战通过专用 HttpOnly、SameSite=Strict、短期 cookie 绑定浏览器，数据库只留绑定 cookie 的 SHA-256。注册挑战还绑定当前用户和当前会话，并在发放前重新验证当前密码；注册和删除均要求 CSRF、精确 Origin。签名验证后重新锁定并读取账户/会话，防止并行禁用、撤销、过期后注册。

注册要求 resident key、用户在场 UP 和用户验证 UV；登录要求 UV 和 UUID 用户句柄绑定。校验 RP hash、origin、challenge、签名、公钥、凭据 ID、计数器；额外拒绝跨 origin 嵌入、外层 ID 与 attested ID 不一致及备份资格位改变。凭据行锁序列化计数器更新；同步通行密钥允许规范中的零计数器，不能只凭零值认定克隆。撤销保留审计记录，并删除由该密钥签发的会话；其他用户不能列出或撤销该凭据。管理密码输错返回 403，避免前端把输入错误当成会话失效。

服务器只存公钥和认证元数据，不存用户的设备私钥或生物特征。仅持有旧会话不能注册新密钥；还须当前密码和设备用户验证。口令登录是仍可用的恢复路径，不绕过现有账户权限。

## 前端协议

统一前缀 `/api/v1/auth/passkeys`，同源携带 cookie。浏览器 `PublicKeyCredential` 的二进制字段需转为无填充 base64url；注册返回 `attestationObject/clientDataJSON/transports`，认证返回 `authenticatorData/clientDataJSON/signature/userHandle`。

| 接口 | 请求/响应 |
| --- | --- |
| `GET /capabilities?origin=...` | `{enabled,available,rp_id,rp_name,origin,reason,password_login:true}`；只用于能力提示。 |
| `POST /login/options` | `{}` → `{challenge_id,public_key,expires_in}`，不需要用户名。 |
| `POST /login/verify` | `{challenge_id,credential}` → `{user,csrf_token}`，设置现有 HttpOnly 登录会话 cookie。 |
| `GET /`（实际无尾斜线） | 已登录 → `{items:[{id,name,created_at,last_used_at,transports,device_type,backed_up}]}`。 |
| `POST /register/options` | 已登录 + CSRF + `{password,name}` → 同形 options。当前密码不保存。 |
| `POST /register/verify` | 已登录 + CSRF + `{challenge_id,credential,name}` → `{item}`。 |
| `DELETE /{id}` | 已登录 + CSRF + `{password}` → `{ok:true}`；撤销当前登录所用密钥会使其会话失效。 |

不要把浏览器取消、生物识别不可用、服务不可达合并成“密码错误”。原生 WebView 是否支持通行密钥受系统版本和关联域配置影响，不能按浏览器通过测试就宣称各客户端都支持。

## 部署简化与备份恢复

新部署运行 `python deploy/start.py`，默认站点 `http://localhost:8788`；生产反向代理使用 `--origin https://你的域名` 并配置证书、正确 Host 转发。脚本生成独立的数据库密码、应用加密密钥、备份加密密钥与管理员随机密码。只在新建文件且终端为交互式时显示一次管理员密码，重定向/CI 输出不打印密码；Docker 启动循环不打印密码。环境文件仍含必需部署秘密，应限制本地文件权限；备份密钥单独保管，不与备份归档共存。

`python deploy/start.py --light` 合并 collector/media 队列到并发数 1 的 worker，减少一个常驻容器。数据库仍为 PostgreSQL，调度器和备份 worker 独立；采集与媒体任务会串行，吞吐能力降低。切换时脚本先等待新模式服务就绪，再在同一项目和环境中显式停止相反模式 worker；遵守停止宽限期，未完成任务按现有租约恢复。不停止独立备份 worker，不删除卷。该覆盖文件没有声称降低了已测量的内存数值。

恢复 PostgreSQL 备份会保留已注册公钥，但必须清除 PasskeyChallenge、PasskeyAttempt，再清除 UserSession，避免旧挑战与旧登录会话复活，且满足 session 外键删除顺序。异站恢复需重新配置 origin/RP；更换 RP 域名后原通行密钥无法用于新 RP，应通过恢复后的口令流程重新注册。认证器计数器超出备份中保存值并不自动失效；只接受严格增长或双方为零的规范情况。恢复仍遵守现有来源暂停、后台批次禁用、密钥分离和资产验证流程。

## 本次验证与未验证项

定向运行 `PYTHONPATH=backend python -m pytest backend/tests/test_passkeys.py -q` 的通行密钥覆盖为 18 passed；`python -m pytest deploy/tests -q` 为 9 passed。覆盖真实 P-256 签名/COSE/CBOR，UV、RP、origin、跨 origin、错签名、用户绑定、计数器、防重放、挑战到期/cookie 绑定、并发一次性消费、CSRF、撤销会话、注册期间账户/会话失效、错误端口配置、首次初始化、重复运行和部署模式切换命令顺序。

主集成流程另已运行真实 Docker 后端测试：208 passed、2 skipped，包括当前/v0.2/v0.1 PostgreSQL 备份恢复、公钥保留与挑战/会话清理；部署助手在容器中亦为 9 passed。随后已由主流程更新本机预览。

通行密钥专项仍是合成认证器数据与临时 SQLite 测试，不是 Windows Hello/Touch ID/手机实机验收；PostgreSQL 限流与密钥计数器竞争仍需专项并发回归。未重置现有账户，未声称 WebView 的平台通行密钥能力已验证。两个 Docker 跳过项不能计为通过。
