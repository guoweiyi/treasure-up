# 2026-10-05 扫描漏洞修复

本次对照 Codex Security Cloud 中 `guoweiyi/treasure-up` 的两条中危记录修复当前源码。扫描针对历史提交，工作区已含其他功能改动和临时记录清理代码；本次保留这些改动，补齐创建配额、弹幕资源预算及清理失败隔离。

| 扫描记录 | 扫描提交 | 问题与修复 |
| --- | --- | --- |
| `c9c0866e2c4c819195ef9a8ef3e764cd` | `2962f7eda786ea789a9c143abe4208009ec0e767` | 播放会话及测速记录累积。新增用户/访客绑定、客户端地址、全局三层活动数与一分钟新增配额；PostgreSQL 事务锁保护最终配额检查及插入提交，成功创建时同时小批清理旧会话和旧测速记录。 |
| `57c5c40620d48191909f23b5e026767a` | `a8bd2eca6515dccec6df545d5f18e727d0b7185d` | 公开弹幕请求重复读取、校验、解析和序列化归档。新增有界序列化结果缓存、冷加载并发与频率限制、请求和响应字节预算；Nginx 另限制请求频率及正在处理的弹幕请求数。 |

## 播放会话

访客播放和原有四小时有效期保留；超出创建预算返回 `429` 和 `Retry-After`，已创建会话的媒体读取不消耗新增预算。

| 范围 | 活动会话上限 | 每 60 秒成功创建上限 |
| --- | ---: | ---: |
| 同一用户或访客绑定 | 256 | 30 |
| 同一客户端地址（IPv6 按 /64） | 512 | 120 |
| 全站、跨 API 进程 | 10000 | 300 |

客户端地址只保存哈希；换访客 Cookie 不能重置地址和全站预算。昂贵的 HLS 索引加载与存储选路在配额事务锁之外，最终写入前再次检查，避免把慢存储请求串行到其他用户的播放创建上。

沿用已有保留期：播放会话过期超过一小时、测速记录超过七天后可删除。每次成功创建最多删除 8 条旧播放会话、24 条旧测速记录，因此调度器停机时，持续创建也带有回收机制。调度器仍每分钟每表最多清理 1000 条；清理使用独立会话和错误处理，在采集派发之前运行。派发失败不会跳过清理，清理失败也不会停止派发。

Nginx 的会话创建限制仍为每地址每秒 1 次、突发 20 次，并覆盖尾部斜杠形式。数据库配额不依赖 Nginx。

## 公开弹幕

最新快照仍每次从数据库解析，只缓存该快照引用的不可变资产的序列化字节；新快照或资产变更立即生效。重复请求直接复用字节响应，缓存容量最多 64 MiB、128 项；读取失败短期缓存五秒，避免连续重读损坏或离线资产。

- 每 API 进程最多一个冷加载；突发最多四次，此后每十秒恢复一个冷加载名额。其他冷请求及时返回 `503` 和 `Retry-After`，不占住线程排队。
- 每地址请求突发 20 次、每三秒恢复一次；全进程突发 120 次、每秒恢复两次；客户端预算表最多 4096 项。IPv6 按 /64 归并。
- 响应字节预算：每地址突发 64 MiB、每秒恢复 1 MiB；全进程突发 128 MiB、每秒恢复 4 MiB。缓存命中也计入预算，超出返回 `429`。
- 公共渲染只读取最多 **16 MiB 的原始弹幕 JSON**，序列化响应上限同为 16 MiB。更大的归档返回 `503`，不会截断后冒充完整弹幕；归档文件保留。该上限比原来的 64 MiB 更保守，用于控制 JSON 对象在内存中的膨胀；缓存字节上限不代表 Python 解析过程或 API 进程的总内存上限。超大弹幕需要后续按时间分段交付。
- Nginx 每地址每秒 2 次、突发 10 次，同时限制每地址 4 个、全站 16 个正在处理的弹幕请求；拒绝返回 `429`。随机查询参数、不同分 P、尾部斜杠与 Cookie 不产生新的地址预算。

Nginx 限制作用于正在处理的请求，其 HTTP/2 和 HTTP/3 并发流按请求分别计数，见 [Nginx 连接限制文档](https://nginx.org/en/docs/http/ngx_http_limit_conn_module.html)；请求频率使用 [Nginx 请求限制模块](https://nginx.org/en/docs/http/ngx_http_limit_req_module.html)。

## 部署边界与验证

默认 Compose 仅公开 Web 端口，API 留在内部网络；Nginx 会覆盖客户端传入的转发头。当前 Uvicorn 信任代理转发头，因此不能另行向不可信客户端直接暴露 API 端口。自定义多层代理应正确配置受信代理，否则地址预算可能按代理地址合并。弹幕内存预算按 API 进程计算，多进程部署应相应规划总内存；数据库会话配额跨进程共享。

相关回归覆盖数据库并发配额、访客换 Cookie、正常媒体读取、缓存复用、快照更新、冷加载并发、缓存和请求预算、失败恢复，以及 Nginx 实际限流与慢响应并发。PostgreSQL 测试只创建随机 `treasure_test_*` 数据库；Nginx 测试使用独立容器及回环随机端口，不修改运行服务。

最终验证共 **108 passed、1 skipped**：应用/API/存储及安全头回归 94 项通过；真实 PostgreSQL 配额竞争、慢存储锁隔离和清理回归 11 项通过；独立 Nginx 行为回归 3 项通过。跳过项为 Windows 主机无法创建符号链接的既有存储测试。仅有既有 Starlette 测试客户端弃用提示。八路 HTTP 请求竞争三份数据库配额时，各范围均严格只有三次成功，五次返回 `429`。

```powershell
.venv/Scripts/python.exe -m pytest backend/tests/test_playback_limits.py backend/tests/test_danmaku_security.py backend/tests/test_housekeeping.py backend/tests/test_scheduler.py -q
$env:TREASURE_RUN_NGINX_TESTS = '1'
.venv/Scripts/python.exe -m pytest deploy/tests/test_nginx_resource_limits.py deploy/tests/test_nginx.py -q
docker compose run --rm --no-deps -e TREASURE_RUN_POSTGRES_TESTS=1 -e TREASURE_SCRATCH_DIR=/tmp/treasure-security-tests -v "${PWD}/backend:/app:ro" init python -m pytest tests/test_playback_limits_postgres.py tests/test_housekeeping_postgres.py -q -p no:cacheprovider
```

补丁需要重新构建并部署 backend/web 后才对运行服务生效。本次没有推送、发布或把云端扫描记录提前关闭。
