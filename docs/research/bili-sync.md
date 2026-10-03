# bili-sync 研究与 Treasure Up 来源采集改造

核验日期：2026-10-04。研究对象是 **amtoaer/bili-sync 主线 Rust 项目**，不是同名 fork，也不是旧 Python 版。固定源码快照为 [`c900777e78796653fdfb00421879b6a802d1eedd`](https://github.com/amtoaer/bili-sync/commit/c900777e78796653fdfb00421879b6a802d1eedd)，该提交日期为 2026-09-10。本轮只读取公开源码和讨论；没有执行外部仓库脚本，没有读取本项目登录凭据，没有向 B 站发起账号请求。

这些是第三方开源提取器对 B 站接口的实际实现和维护记录，**不是 B 站官方 API 稳定性承诺**。本项目借鉴工作流程、故障模式与字段含义，以自己的 Python 适配器、作业队列和资产模型实现；没有将上游服务整体嵌入。上游采用 [MIT License](https://github.com/amtoaer/bili-sync/blob/c900777e78796653fdfb00421879b6a802d1eedd/License)，版权信息为 2024 amtoaer。若以后直接复制代码，应随分发保留该许可证和版权声明。

## 1. 覆盖范围与证据矩阵

“获取、分类、精读、验证”分别统计。以下数字不能合并称为“全部讨论和全部 diff 已精读”。

| 层级 | 实际覆盖 | 边界 |
| --- | --- | --- |
| Git 历史索引 | HEAD 可达主线 433 个提交；全部 refs 可达 541 个提交摘要 | 主线提交均提取 message 和完整变更文件清单；不等于 433 份 diff 全文精读 |
| 讨论索引 | 747 条，包含 466 个 issue、281 个 PR | REST 8 页 issue 索引、3 页 PR 索引；按编号核对，没有缺号 |
| 讨论正文 | 747/747 完整正文已获取，共 254,117 个字符 | 140 条正文为空；对全部标题和正文做规则分类，非只看标题；规则标签可能有误报或漏报 |
| 逐段精读 | 87 条讨论：60 issue、27 PR，其中 6 条空正文 | 非空正文 81 条；其他 660 条只完成全文获取和自动分类，未逐句精读 |
| 普通评论 | 37 条讨论中的 146 条普通评论，完整分页获取并阅读 | 不包含所有 747 条讨论的评论；附件图片、附件日志、外部 fork 未逐一打开 |
| PR 评审/行内评论 | PR 290、485、648 的 review 和行内评论端点均查询，结果为空 | 不代表其他 PR 没有评审讨论 |
| 关键修复 diff | 23 个提交的相关业务 diff | 大提交只精读相关采集/数据库/配置文件，不将同时变更的 UI、依赖锁文件计入精读 |
| 当前业务源码 | 按采集、适配器、工作流、存储、调度、配置、API、通知模块阅读，见下表 | 并未逐行阅读整个仓库；未执行其测试或复现作者报告 |

本地可审计资料保存在被 Git 忽略的 `.tools/research/`，不会将公开讨论里的日志、临时签名 URL 或未知脚本混入产品仓库：

| 本地文件 | 内容 | SHA-256 |
| --- | --- | --- |
| `bili-sync-commits.txt` | 主线 433 条摘要 | `b2fe4fd2a690d182193a2e337b82bfd9b71ac46a2776f974ee217af5e0247148` |
| `bili-sync-issues-index.json` | 747 条完整编号索引 | `de9354abafdf1917e4fd0dadc19a7456ee18a84dc5f96808ad4b491526d5ea67` |
| `bili-sync-pulls-index.json` | 281 条 PR 索引 | `1ab6eb325ef789689d04816cc2dc698f0e74f40911c90f4fb1b237d0d746f272` |
| `bili-sync-discussion-bodies.json` | 全部 747 条正文及对应索引元信息 | `cad93beb685ebc6b0f40a03ad9ec45d158aca33800a420a531c096a576d082db` |
| `bili-sync-evidence-matrix.json` | 每条讨论的分类、每个提交的 message/文件清单/分类、分类规则 | `e5582e679af3c81828d37649823eb2f68ace1439b50db41deeebe643a2b64820` |
| `bili-sync-deep-read-register.json` | 87 条精读编号、追加评论数量、23 个 diff 标识 | `403b2a08e5565711143f25a3805fdbf81ff32e6161de78f2480bdf5fdcbde502` |

分类是多标签，列合计不能相加当作去重总数。规则以完整正文、标题为输入，提交分类另包含完整文件路径：

| 主题 | 讨论数 | 主线提交数 |
| --- | ---: | ---: |
| 认证、凭据刷新 | 53 | 37 |
| 来源、分页、增量与漏采 | 221 | 32 |
| 风控、限流、重试 | 64 | 25 |
| 持久化、路径、数据库、删除 | 263 | 93 |
| 媒体、字幕、弹幕 | 172 | 43 |
| 配置、部署、调度 | 284 | 155 |
| Web、API、界面 | 306 | 136 |
| 依赖、编译、CI | 80 | 192 |
| 未命中上述规则 | 87 | 59 |

精读正文编号：64、67、86、88、111、112、122、153、158、159、161、162、199、202、221、228、231、287、289、290、293、304、312、333、342、365、372、373、400、413、414、417、419、420、421、427、430、440、450、461、478、480、485、491、495、496、497、500、503、511、514、520、521、527、533、549、550、552、560、567、571、577、583、587、596、606、613、638、640、646、647、648、661、677、678、679、683、704、710、722、725、726、738、739、744、751、754。

完整普通评论覆盖的讨论编号：原 22 条为 153、304、521、430、533、511、496、491、647、661、372、312、290、342、485、503、527、560、648、678、679、677（49 条评论）；追加 751、577、587、571、500、414、289、221、199、122、638、646、440、417、754（97 条评论）。

## 2. 当前业务源码阅读范围

下列路径均相对于固定快照的 `crates/bili_sync/src/`。目录总文件数是清单覆盖范围，不表示整个目录已逐行精读。源码与证据矩阵之间可通过固定提交链接复核。

| 模块 | 本轮阅读范围 | 关键结论 |
| --- | --- | --- |
| `bilibili/`，21 个 Rust 文件 | `favorite_list`、`submission`、`dynamic`、`client`、`error`、`video`、`credential`、`analyzer` 的生产逻辑；`collection` 分页及 `watch_later`；`danmaku/model`；`mod` 的签名和响应检查 | 收藏夹与 UP 投稿有不同的分页结构；code=0 并不自动排除 v_voucher；弹幕为 WBI 分段 protobuf |
| `adapter/`，5 个文件 | favorite、submission、collection、mod 的扫描契约 | 默认按时间提前终止；collection 特殊处理为全遍历后按时间过滤；投稿只对首条置顶项做特殊处理 |
| `workflow.rs`，946 行 | 当前生产流程全文 | 列表写库、补详情、下载、弹幕更新是分离阶段；下载成功状态不能代表来源遍历完整 |
| `downloader.rs`、`database.rs` | 下载器生产逻辑、数据库初始化全文 | 临时目录下载后复制，长度校验、备选源与合并；SQLite WAL、90 秒 busy timeout、迁移前版本检查 |
| `task/`，3 个文件 | `video_downloader.rs` 全文 | 下载任务互斥、配置快照、每日凭据刷新任务、风控终止本轮；不是持久消息队列 |
| `config/`，8 个文件 | current、versioned_config、versioned_cache；item 中调度/并发/弹幕策略 | 更新先持久化再替换内存，版本号阻止旧表单覆盖；配置缓存随版本更新 |
| `utils/`，13 个文件 | model 全文、convert 生产逻辑、rule 生产逻辑、status 核心位状态、danmaku_schedule 全文 | 批量写入与短事务；普通失败、重试耗尽、成功需区分；弹幕更新以最后成功同步时间判断 |
| `api/`，16 个文件 | routes/mod、config、task；video_sources 的编辑/删除/规则重评估/全量同步/新增来源 | 所有 API 走 token 中间件；全量同步先取全列表再删数据库，可选删本地文件；历史文件操作与数据库事务不构成一个原子事务 |
| `notifier/`，3 个文件；入口 | info 全文、mod 的通知类型和派发入口；main 全文 | 通知只统计全部子任务成功的视频；启动检查 FFmpeg、迁移、配置，退出关闭数据库 |
| 实体及迁移 | video/page 实体字段关系；水位与本次涉及的迁移 diff | 分页 `cid` 是源内容身份，`pid` 是顺序；来源归属不能替代全局媒体去重 |

未覆盖：全部前端组件、全部通知渠道实现、ASS 排版器的全部细节、所有生成实体/历史迁移、第三方依赖、附件与每条讨论全部关联链接。没有以这些未读部分为实现结论的唯一依据。

## 3. 关键修复链与采用的判断

| 证据 | 上游解决的问题 | Treasure Up 的处理 |
| --- | --- | --- |
| [PR 228 / b4177d4](https://github.com/amtoaer/bili-sync/commit/b4177d4ffc41c0e384c535768f331266a5333686)、[PR 231 / 9e5a8b0](https://github.com/amtoaer/bili-sync/commit/9e5a8b0573d4cb6ed19036ba8248055c19594f1d) | 首页面已写库、下一页失败后，下一轮遇到已知视频便停止会永久漏采；流错误必须向上传播，不能写入完成水位 | 每页保存检查点，续采不从“已见 BVID”推断结束；结构/网络错误保留部分状态 |
| [PR 290](https://github.com/amtoaer/bili-sync/pull/290)、[PR 342](https://github.com/amtoaer/bili-sync/pull/342)、[#289 评论](https://github.com/amtoaer/bili-sync/issues/289) | 合集排序并不可靠，全量拉取后再筛选；不能仅信任页面“最新添加”文案 | 收藏夹和 UP 的增量窗口不因旧时间、相同时间、已见 ID 提前停止；定期全量校对补偿窗口之外的新条目 |
| [PR 485](https://github.com/amtoaer/bili-sync/pull/485)、[PR 503](https://github.com/amtoaer/bili-sync/pull/503) | 动态 API 可包含普通投稿列表之外的视频；换接口、WBI、额外风控检测 | 本轮 creator 明确指标准 UP 投稿列表；不将动态 API 偷换为故障时的替代列表 |
| [PR 527](https://github.com/amtoaer/bili-sync/pull/527)、[PR 640 / 580a66e](https://github.com/amtoaer/bili-sync/commit/580a66eb17f891ccc3a4aa00510b875d7f7c84cf)、[PR 211](https://github.com/amtoaer/bili-sync/pull/211) | 风控中断应跨子任务传播，轮次不能继续无节制请求；HTTP 403/412 也需保守处理 | 保留账户串行、请求/视频间隔、持久冷却、Retry-After；403/412/429 安全分类，不用 UA 轮换等规避方式 |
| [PR 560](https://github.com/amtoaer/bili-sync/pull/560)、[c854e4e](https://github.com/amtoaer/bili-sync/commit/c854e4e)、[#440](https://github.com/amtoaer/bili-sync/issues/440)、[#533](https://github.com/amtoaer/bili-sync/issues/533) | 刷新依赖完整凭据、时间戳和 CSRF，能访问视频不代表可以刷新；允许用户自行维护凭据 | 继续支持用户原位更新账号凭据；获取账号锁后刷新 ORM 对象，再解密创建客户端，保留账户冷却；不声称只凭 cookies.txt 可自动续期 |
| [PR 726 / 83b0708](https://github.com/amtoaer/bili-sync/commit/83b07087fb9b6e87c6ed40423941d038d6c27497) | 将 Cookie 合为单一请求头，移除不是普通 Cookie 的 ac_time_value | 不从请求 Cookie 推导出 localStorage refresh token；日志不输出凭据或完整响应 |
| [PR 648](https://github.com/amtoaer/bili-sync/pull/648)、[#647](https://github.com/amtoaer/bili-sync/issues/647) | `attr == 4` 仍可能有效，播放量显示“--”也不能证明视频失效 | 不凭收藏列表提示直接否定可下载性；实际鉴权/播放错误单独记录 |
| [PR 677](https://github.com/amtoaer/bili-sync/pull/677)、[#587](https://github.com/amtoaer/bili-sync/issues/587) | 真空列表应正常返回，但第一页有后页标记、第二页突然为空是另一类问题 | 验证 has_more 类型、UP 页码/页大小/总量；总量非零或还有后页却返回空页必须报错 |
| [PR 678](https://github.com/amtoaer/bili-sync/pull/678)、[PR 679](https://github.com/amtoaer/bili-sync/pull/679)、[#293](https://github.com/amtoaer/bili-sync/issues/293) | 增加手动全量同步和本地清理，修复空路径删除 | 本轮只把来源关系标为 not_observed，不删除 Video 或归档资产；未见可能是撤销、不可见、列表变化，不能断言源视频已删除 |
| [PR 420 / 4f780fa](https://github.com/amtoaer/bili-sync/commit/4f780faf64bbaa452f0ca54a7a4cb3dbe4b43b2d)、[PR 421 / 66079f3](https://github.com/amtoaer/bili-sync/commit/66079f3adc6a2615f571ee4ebba5f442b343f5ed)、[#414](https://github.com/amtoaer/bili-sync/issues/414)、[#417](https://github.com/amtoaer/bili-sync/issues/417) | 网络并发不应等价于无界写事务；数据库锁冲突需要缩短事务并控制并发 | 复用 PostgreSQL 作业/租约架构；分页边界提交，心跳独立会话，不长持 Job 行锁等待网络 |
| [PR 725 / bf762b1](https://github.com/amtoaer/bili-sync/commit/bf762b1b0ac950c24191a01d1cf8f8cbadf768ad) | 详情每 15 条事务、列表每 30 条，减少事务开销 | 本项目每个源页作为持久检查点边界；新任务与观察结果一起提交，避免成功游标先行 |
| [PR 495 / de70243](https://github.com/amtoaer/bili-sync/commit/de702435af1928db2a16303d2e7124705b7583e0)、[#122](https://github.com/amtoaer/bili-sync/issues/122)、[#158](https://github.com/amtoaer/bili-sync/issues/158) | 临时文件与最终文件分离；数据库“处理过”不等于本地文件完整存在 | 继续使用已有受控 scratch、资产哈希与状态；不把上游普通 fs::copy 误称为跨文件系统原子发布 |
| [PR 583 / 96c11bb](https://github.com/amtoaer/bili-sync/commit/96c11bb077cab2b8bd7a6b87f5ee0050e22db7cf)、[#571](https://github.com/amtoaer/bili-sync/issues/571) | 不支持的升级路径必须在迁移前检测，避免升级后新旧版本都无法启动 | 本项目已有独立迁移和恢复后向前迁移流程；本轮不通过删除数据库重建解决采集状态问题 |
| [PR 333 / 1ec0158](https://github.com/amtoaer/bili-sync/commit/1ec015856ba70b66eff96040e27d096eaacdee76) | Dolby Vision 合并需要保留特殊容器元数据 | 沿用本项目已有 FFmpeg 保留与 ffprobe/码流验证；不能只见 HEVC 就显示杜比 |
| [PR 744 / c6b47e5](https://github.com/amtoaer/bili-sync/commit/c6b47e525430de1902d4857b1437684640b9650b) | 刚发布就下载的视频弹幕不足，需要按成功同步时间定期补采 | 源码现在已支持弹幕更新；本项目保留已有统计/弹幕刷新任务，本轮不重复创建另一套调度 |

上表对应精读 diff 共 23 个：`33a61ec 34d3e47 ed54ca1 ff6db0a c69a88f d1eac3e 980f74a 29f3623 09604fd 980779d 0113bf7 c854e4e 580a66e 4f780fa 66079f3 96c11bb b4177d4 9e5a8b0 bf762b1 83b0708 1ec0158 de70243 c6b47e5`。PR 正文、当前源码和相关 diff 互相核对，旧评论不能代替当前行为。

两项容易误读的证据：[#754](https://github.com/amtoaer/bili-sync/issues/754#issuecomment-5910778161) 的数据库损坏来自另一个 fork，维护者已经指出，本报告不将其列为本主线确认缺陷；[#577](https://github.com/amtoaer/bili-sync/issues/577) 对多 P 新增/删除/重排的讨论后来对应到 [PR 596](https://github.com/amtoaer/bili-sync/pull/596) 的手动重置，不能据此宣称所有既有视频分 P 会持续自动同步。

## 4. 本轮实现与持久状态

实现入口为 `backend/app/source_monitoring.py`，由现有 `scan_collection` 作业调用。收藏和 UP 共享作业基础设施，但分别验证响应结构。

| 来源 | 当前请求 | 验证与范围 |
| --- | --- | --- |
| `favorite` | `/x/v3/fav/resource/list`，`media_id`、`pn`、`ps=20`、`order=mtime`、`type=0`、`tid=0` | has_more 为布尔或 0/1；可用时检查 info.media_count；保留 type:id 身份 |
| `creator` | `/x/space/wbi/arc/search`，`mid`、`pn`、`ps=30`、`order=pubdate` 等，与 WBI 签名 | 验证 page.count、pn、ps、list.vlist；标准投稿列表；不含所有动态/直播/课程/番剧内容 |
| UP 资料 | 项目已有的 `/x/space/wbi/acc/info` | 返回 mid 必须匹配来源 UID，写入人物快照、独立 Creator、VideoCreator 关系 |

源码接口依据：[favorite_list.rs](https://github.com/amtoaer/bili-sync/blob/c900777e78796653fdfb00421879b6a802d1eedd/crates/bili_sync/src/bilibili/favorite_list.rs)、[submission.rs](https://github.com/amtoaer/bili-sync/blob/c900777e78796653fdfb00421879b6a802d1eedd/crates/bili_sync/src/bilibili/submission.rs)、[dynamic.rs](https://github.com/amtoaer/bili-sync/blob/c900777e78796653fdfb00421879b6a802d1eedd/crates/bili_sync/src/bilibili/dynamic.rs)。上游 UP 资料使用 card 接口；本项目的 acc/info 是已有适配，不将二者写成同一个接口。

`Collection.monitor_state` 保存 version、status、scan_mode、run_id、last_started_at、last_completed_at、last_full_scan_at、baseline_started_at、baseline_completed_at、next_page、counts 和 end_reason。日期是 UTC ISO 字符串；未知字段不伪造时间。`CollectionItem.observation` 保存 first/last_seen、first_run_id、published_at、favorited_at 和 archive_decision。

| 配置 | 默认 | 实际语义 |
| --- | --- | --- |
| `initial_strategy` | `all` | all 归档首轮发现的全部可用稿件；latest 归档返回顺序中前 N 个；new_only 只建立首轮基线 |
| `initial_limit` | 100 | latest 的 N，1–10000；不是跳过剩余来源分页 |
| `incremental_pages` | 3 | 增量最多读 3 页，1–100；窗口内不遇旧即停 |
| `full_scan_interval_hours` | 24 | 到期执行全量遍历，1–720 小时；受任务排队、源订阅间隔与账号冷却影响 |
| `force_full_scan` | false | 手动完整检查；仍遵循同一请求预算、续采和风控规则 |

首轮即使选择 latest/new_only 也要完整建立来源基线，所以大收藏夹第一次仍有列表请求成本。latest 的含义是**当前接口返回顺序前 N 项**，没有按全量发布时间二次排序；不能宣传为跨乱序列表的严格“最新 N 个”。new_only 对缺少有效源时间的条目记录 unknown_source_time 并跳过，避免把旧视频当成新发布。

检查点包含模式、下一页、当前阶段、页面指纹、首屏指纹、预期总量、累计计数和首轮选择数量。请求预算用尽返回 continuation，队列继续相同作业。终止页后额外复查首屏与总量，只有稳定、没有跨页重复、已知总量相符时，才推进全量/基线完成时间。

| 状态 | 含义 |
| --- | --- |
| running | 正在读取来源 |
| partial | 请求预算或错误中断，保留下一页；不是完整扫描 |
| blocked | 账号/风控等条件阻塞，保留上次已验证时间 |
| window_complete | 增量窗口处理完毕，不能证明来源全量遍历完成 |
| unstable | 遍历中首屏/总量/重复情况不一致，不推进全量水位，不标记缺席 |
| complete | 已到可见结束边界且一致性检查通过；CaptureRun 对应 visible_traversal_complete |

计数分开记录 `observed/new_items/new_videos/newly_published/newly_favorited/queued/reused/skipped/not_observed`。new_items 是该来源第一次看到的记录；new_videos 是全库新增稿件；只有非首轮且源时间晚于基线时，才计为新发布/新收藏。旧视频重新收藏可能是新收藏事件，不等于全库新视频。

全库 Video 按 BVID 复用，多来源通过 CollectionItem 关联。同一有效归档策略已完整完成则不重复派发；策略变化允许重新评估，但底层仍复用已有合适媒体。正在排队/运行/暂停/阻塞的作业不重复新增；失败、取消或部分结束的历史任务标为 needs_attention，不借每次扫描重置用户停止或重试次数。

## 5. 已知边界和验证

1. 页码式接口没有服务器快照。首屏/总量复查能发现部分变化，**不能证明扫描期间中间页面完全未变**；周期全量校对是补偿机制，不能承诺永不漏采或瞬时精确同步。持续变化可能使首轮保持 unstable，需后续重试。
2. 完成来源观察不代表视频、评论、弹幕、字幕全部归档。媒体归档子任务与来源 CaptureRun 分别显示。列表 total 若包含不可见项且返回数不匹配，本轮宁可保持 unstable，也不报告全量完成。
3. 本轮检测新 BVID/新来源条目。已归档稿件增加 P、替换同 CID 媒体、修改简介等，不会仅因来源窗口扫描而自动全部重下；需要既有明确刷新/归档任务。平台隐藏、无权限、已删除内容不能靠会员 Cookie保证可取。
4. 后续修改首轮策略不会自动追回之前标记 initial_limit/initial_baseline 的全部历史条目；现有已完成记录也不会仅因修改来源画质设置就被全库重下。需要显式归档/重试选择，避免意外扩大下载量。
5. 本轮新 UP 分页及账号轮换只用 mock 验证，未重新做登录实测。账号有效性和平台接口可用性仍需小样本受控验收。没有添加任何语义审核、模型部署或后台模型配置。

2026-10-04 本地验证：`test_ingest_runner.py + test_ingest_sources.py + test_ingest_client.py` **49 passed**；进一步运行全部 `test_ingest*.py + test_jobs.py` **107 passed、3 skipped**。跳过的是需要本地 FFmpeg/ffprobe、显式离线 Dolby 样本或启用 PostgreSQL 实测环境的测试，不将跳过计为通过。主代理另在最终 Docker 镜像执行完整后端回归，报告 **208 passed、2 skipped**，包括冻结后的本模块和新增旧版本恢复测试；这个完整数量来自主代理的执行结果，不是上述本地子集数量。

新增/调整测试覆盖：跨页同时间条目、预算续采、三种首轮策略、旧/已知项不触发增量早停、周期全量补采、无删除的缺席标记、首屏变化、非法/重复/异常空页、风控后继续相同页、UP 资料身份和投稿分页、attr=4、跨来源策略复用、取消任务不复活、真空收藏、账号等待锁期间轮换后的新密文读取、解密失败安全错误。

对 root 来源 API 的只读契约审计：kind/source_id、monitor 包装、history 的 collection_id、手动 full→force_full_scan 与 runner 对齐；定时和手动扫描均锁 SourceSubscription 并检查未结束扫描，runner 再以来源锁保护执行。本轮未发现该路径的严重权限或互斥缺陷。来源列表逐条查询 Collection 的 N+1 优化建议已交 root，主代理已改为双实体 join 复用结果，避免每条再次查询。
