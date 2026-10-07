# B 站会话与源站失效检查

更新日期：2026-10-07。当前源码已支持扫码授权、独立刷新令牌和自动续期；这些迭代尚未全部加入公开 Release。Cookie 与刷新令牌只在服务端加密保存，不在账号查询响应中回显。

自动化协议测试使用合成 HTTP 响应，数据库回归使用隔离测试库，不依赖真实 B 站账号。前一轮通过本机后台现有账号确认过登录与会员状态有效；这不能替代本轮扫码及真实续期的在线验收，也不代表可永久保持登录。

2026-10-07 在线调用二维码生成接口时发现，返回的扫码页已从原有 `passport.bilibili.com/h5-app/passport/login/scan` 扩展为 `account.bilibili.com/h5/account-h5/auth/scan-web`（`callback=close`）。本轮按确切官方域名和路径增加兼容，未放宽为接受任意 URL。部署后已验证真实二维码生成、显示、状态轮询及取消后的临时凭据清理；真实手机确认及令牌轮换尚未实测。

## 添加和更新账号

在后台采集账号页选择「扫码添加账号」，填写名称，使用哔哩哔哩 App 扫描并在手机上确认。服务端取得 Cookie 和独立 `refresh_token` 后先短期加密暂存，核对登录 UID 后绑定采集账号；重新授权已有账号必须使用原 B 站账号，不会静默替换为另一个 UID。扫码成功后默认开启自动续期。

一次授权窗口为 180 秒，轮询间隔至少 3 秒，授权绑定发起它的管理员会话。成功、取消或失败会清除临时二维码密钥与临时凭据；过期记录由后台分批清理。如果扫码已确认但身份接口暂时不可用，窗口内会用加密暂存的凭据继续验证，无需重复消费二维码；超过窗口需重新扫码。

也可选择「手动添加」，填写 Cookie，并在独立的刷新令牌字段填入同一次登录的 `refresh_token` / `ac_time_value`。如果浏览器在 B 站站点的 Application → Local Storage 中存在 `ac_time_value`，可在本机读取后填入表单；它不在普通 Cookie 请求头中。只提供 Cookie 仍可采集已有权限内的内容，但无法自动续期；没有该字段时使用扫码授权。

编辑时，Cookie 和刷新令牌都留空表示保留原值；替换 Cookie 而没有同时提供刷新令牌，会清除旧令牌，避免混用不同登录代次的凭据。表单另有移除刷新令牌的选项。账号页的「验证」检查当前登录状态，「检查续期」提交一次续期检查任务，两者用途不同。

## 自动续期和故障恢复

服务端正常每 24 小时检查一次；首次授权后安排检查，只有 B 站返回需要刷新才进行轮换，并非每 24 小时强制重新登录。管理员可关闭自动续期，或通过「检查续期」单次检查；已有任务会复用，失败后的等待时间和账号真实风控冷却仍生效。停用的账号不能执行续期。

一次轮换按以下顺序执行：

1. 校验任务租约、当前账号及凭据代次，确认账号没有被停用；自动任务还要确认自动续期开关仍开启。
2. 取得刷新校验值，在提交轮换请求前再次检查，并持久化正在轮换的状态。
3. 收到成功响应后，先原子保存新 Cookie、新刷新令牌和待确认的旧令牌，再向 B 站确认旧令牌失效。
4. 确认成功后清除待确认旧令牌、归零失败次数，安排下次 24 小时检查。

续期使用独立的账号刷新锁；普通资料 API、来源查询和下载仅在短暂的凭据读写阶段共享互斥，不会等待整段续期 HTTP 请求。凭据代次检查阻止迟到的旧响应覆盖管理员刚更新的账号。上游已成功轮换时，即使任务期间被取消，也必须先保存新凭据，防止丢失可恢复状态；这不会重新启用被停用的账号或打开已关闭的自动续期开关。

| 后台状态或提示 | 系统行为 | 处理方式 |
| --- | --- | --- |
| 仅 Cookie / 未提供刷新令牌 | 保留普通采集，不安排自动续期 | 需要续期时扫码授权，或提供配套令牌 |
| 续期就绪 | 等到下次检查；上游不要求刷新时不轮换 | 无需重复点击 |
| 新凭据已保存、等待确认 | 下次仅重试确认，不再次提交轮换 | 等待计划检查，或在允许时间后点击「检查续期」 |
| 临时网络或风控错误 | 失败后按 15、30、60、120 分钟递增等待；上游 `Retry-After` 可延长等待（最多接纳 24 小时），已有账号冷却仍适用 | 按账号页的下次检查时间等待 |
| 连续失败达到 5 次 | 停止自动检查；保留现有凭据及已有的待确认状态 | 排查后手动检查；成功会清零计数 |
| 上次刷新结果未知、需要重新授权 | 不盲目重发轮换请求 | 重新扫码，或重新提供配套的新 Cookie 和刷新令牌 |
| 自动续期已关闭 | 停止后续自动轮换；已提交的成功响应仍妥善保存 | 需要时手动单次检查或重新开启 |

“结果未知”包括轮换 POST 发出后连接中断、无法可靠解析成功响应，或进程在提交请求与保存结果之间退出。此时不能假设旧令牌仍可重用。后台「检查续期」也不能替代重新授权。

备份恢复会关闭所有来源账号的自动续期并清空下次检查时间、删除扫码临时授权、暂停已排队任务并结束旧执行记录。待确认的账号状态保留用于人工核查，不能让从旧快照恢复的实例自动轮换真实账号凭据。验收恢复数据后，确认所用 Cookie 与刷新令牌仍成对有效，必要时重新扫码，再明确开启自动续期。

实现对应 [续期服务](../backend/app/source_credentials.py)、[管理员授权接口](../backend/app/source_credentials_api.py) 和 [Passport 协议客户端](../backend/app/ingest/passport.py)。刷新能力依赖上游会话仍允许刷新；账号退出、凭据撤销或上游要求重新验证时仍需用户登录。没有永久保活承诺，登录续期也不会增加会员或充电权限。

## 本次已修复

此前，任意接口的 `-101` 都会把采集账号设为失效；`nav` 缺少 `isLogin` 也会被当成退出。现在必须收到明确的登录失败证据：普通接口返回 `-101` 时仅补查一次账号身份；身份仍有效则只阻止当前任务，保留账号有效状态。网络失败、风控和异常响应不会注销账号，也不会立即重试原接口。

被误标为 `invalid` / `expired` 的账号可通过后台“验证”重新检查；普通采集仍禁止使用它们。管理员停用的账号始终不能被验证或并发返回的旧响应重新启用。凭据更新仍受已有的代际校验保护。

## 其他项目提供了什么证据

| 项目 | 本次直接阅读的源文件 | 可借鉴点 |
| --- | --- | --- |
| bili-sync | [credential.rs](https://github.com/amtoaer/bili-sync/blob/main/crates/bili_sync/src/bilibili/credential.rs)、[client.rs](https://github.com/amtoaer/bili-sync/blob/main/crates/bili_sync/src/bilibili/client.rs) | 将凭据、刷新判断、刷新确认和普通请求分开；二维码登录返回 Cookie 与独立刷新令牌。 |
| yt-dlp | [bilibili.py](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/bilibili.py) | WBI 参数单独缓存；播放接口的风控错误提示稍后再试。源码中的“存在 SESSDATA”只是 Cookie 存在性判断，不能作为本项目的账号有效性判据。 |
| bilibili-api-collect | [项目当前主页](https://github.com/SocialSisterYi/bilibili-API-collect) | 当前已停止维护，不把旧接口整理视为持续受支持的官方契约。 |

bili-sync 当前实现会查询 Passport 的刷新状态，需要刷新时取得刷新 CSRF，提交原刷新令牌，再确认新凭据。它的 `ac_time_value` 来自登录响应中的 `refresh_token`，并非普通请求的 Cookie 字段。仅粘贴浏览器 Cookie 请求头无法补出这个令牌；重复请求 `nav` 也不能续期。[源码依据](https://github.com/amtoaer/bili-sync/blob/main/crates/bili_sync/src/bilibili/credential.rs)

## 归档视频的源站状态

统计定时任务同时检查稿件是否仍可获取，默认开启且沿用统计更新周期。仅本客户端稿件信息接口返回 JSON `-404`，并且没有已知充电视频标记，才设置“源站已失效”。泛化的 HTTP 404、评论接口 404、权限不足、网络失败、登录失效、风控、审核或仅自己可见等响应都不能证明稿件被删除。已知充电稿件的 `-404` 记为权限待确认，避免权限到期被当成删除。

失效只改变源站标记，保留文件、封面、弹幕、评论及上次统计；本地视频仍可播放。之后的周期继续检查，恢复成功会移除标记。`source_availability` 保存最新结果和最近 20 个采集运行的检查摘要，另有对应 `CaptureRun` 结果。只记录固定原因、时间及源站数字代码，不保存远端错误正文。该标记描述当前源站可用性，不推断删除原因。

## 任务队列与重启恢复

本轮修复了调度器在投递后误确认新续采事件的竞态：现在投递前固定事件 ID，只确认这次实际发送的事件。快速完成的任务随后写入的新事件独立保留，到达任务的可执行时间便可继续；60 秒仅用于已发布消息丢失后的补发。

领取任务与创建执行记录在同一事务提交。进程租约失效后，每轮最多恢复 100 条任务，保留检查点，关闭旧执行记录；暂停和取消仍保持用户选择。旧 Worker 不能覆盖新租约，也不能改写已经结束的执行历史。租约检查、领取与续期使用 PostgreSQL 实时时钟。

调度只读取任务 ID、类型及租约等必要字段，避免读取大块采集检查点；投递 Redis 时不保留数据库事务，成功提示按批确认。确认事务失败可能重复投递提示，但数据库原子领取只授予一个有效租约，旧租约不能继续提交；Redis 消息不是唯一任务账本。

## 可复现验证

从仓库根目录、安装 `backend/requirements.lock` 的 Python 环境运行。下列协议与任务测试使用合成数据，不需要填入真实 Cookie 或刷新令牌：

```bash
python -m pytest backend/tests/test_passport.py backend/tests/test_source_credentials.py backend/tests/test_source_credentials_api.py backend/tests/test_account_auth_evidence.py -q -p no:cacheprovider
python -m pytest backend/tests/test_job_recovery.py backend/tests/test_jobs.py backend/tests/test_housekeeping.py backend/tests/test_backup.py -q -p no:cacheprovider
```

已初始化 Docker 测试环境并启动 PostgreSQL 后，可从源码重新构建后端镜像，再运行真实 PostgreSQL 回归。预构建发行包不包含测试源码；自定义部署须沿用自己的 Compose 参数。

```bash
docker compose build init
docker compose run --rm --no-deps -e TREASURE_RUN_POSTGRES_TESTS=1 api python -m pytest tests/test_source_credentials_postgres.py tests/test_job_recovery_postgres.py tests/test_housekeeping_postgres.py tests/test_postgres_integration.py::test_postgres_snapshot_encrypted_backup_and_restore_without_primary -q -p no:cacheprovider
```

PostgreSQL 测试仅创建和清理随机 `treasure_test_*` 数据库以及独立临时目录，覆盖凭据代次竞争、租约恢复、发布确认故障、旧 Worker 隔离、到期授权清理和当前/历史版本备份恢复。运行角色需具备创建测试数据库的权限。自动化通过不等于已完成所有真实账号、网络和设备验收；测试报告、截图及日志不得包含二维码授权地址、Cookie 或刷新令牌。
