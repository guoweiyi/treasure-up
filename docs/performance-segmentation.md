# HLS 分片与首播性能研究

记录日期：2026-10-05。测量对象为本机已有、已授权归档的三个 HLS 版本；封装环境使用 FFmpeg 7.1.5，前端依赖 hls.js 1.7.3。本文区分已完成的只读测量、代码审查结论和未验证的设计方案，不代表 Apple 真机性能验收。

**当前未启用片内多 `moof` 方案，也未启用 hls.js `progressive`。** 原档、已发布分片和业务任务未因本研究改变。计划中的隔离小片实验尚未开始；当时 Docker Desktop 有明显磁盘抖动，因此没有追加合成 H.264 或三秒 Dolby Vision 的重新封装实验。

## 现有分片的实测

从只读数据库取得分片的已记录字节数、从已有 HLS index 读取时长；只读首片的 MP4 box 头，跳过媒体载荷。没有为这些统计重新下载、转码或遍历全媒体包。

| 项目 | AV1＋AAC 原档 HLS | DV＋EC-3 原档 HLS | DV＋AAC 副本 HLS |
| --- | ---: | ---: | ---: |
| 分片数量 | 265 | 56 | 56 |
| index 大小，字节 | 22,020 | 4,860 | 4,859 |
| 会话媒体清单大小，字节 | 33,416 | 7,249 | 7,249 |
| init 大小，字节 | 1,471 | 1,527 | 1,565 |
| 首片时长，秒 | 10 | 10 | 10 |
| 首片大小，字节 | 3,259,072 | 3,765,223 | 1,932,324 |
| 分片时长中位数，秒 | 5 | 5 | 5 |
| 最长分片，秒 | 10.583312 | 10 | 10 |
| 最大片，字节 | 7,558,111 | 37,693,054 | 35,861,834 |
| 全部分片平均码率，Mbps | 2.507 | 22.894 | 21.426 |
| index 进程缓存首次读取，ms | 14.131 | 19.347 | 8.109 |
| index 缓存命中读取中位数，ms | 1.424 | 1.105 | 0.757 |
| 20 Mbps 下仅首片传输理论时间，秒 | 1.304 | 1.506 | 0.773 |

平均码率以全部分片字节数乘八再除以总时长计算，包含媒体封装开销。理论传输时间不含 RTT、鉴权、解码及浏览器缓冲，**不是实测首帧时间**。index 首次读取指新 Python 进程中的应用缓存未命中，不表示操作系统磁盘缓存为空；热读为同进程五次调用的中位数。这组本机数据不能外推为云存储或弱网结果。

三个首片均为 `styp`、两个 `sidx`、**一个 `moof`、一个 `mdat`**。它们是 fMP4 分片，并不是已经验证的多块 CMAF 渐进分发方案。

另一次最多读取前 30 秒 packet 信息的关键帧探测触发了 30 秒进程超时，已终止且未重试。因此没有完整 GOP 分布测量，不应从“五秒片长中位数”推断所有 GOP 均为五秒，或断言首片能直接缩短到某个值。

## 无损分片的边界

当前 [HLS 包装器](../backend/app/playback/hls.py) 使用 `-c copy`、六秒目标时长、VOD 和 `hls_list_size=0`。FFmpeg 在到达目标时间后的合适关键帧边界切片；目标六秒并不保证每片六秒。无损封装不能新增关键帧。用 `split_by_time` 强切非关键帧，不能同时声称每片可独立解码或保持原有 seek 行为。[FFmpeg HLS 文档](https://ffmpeg.org/ffmpeg-formats.html#hls)、[FFmpeg 7.1.5 包装器源码](https://raw.githubusercontent.com/FFmpeg/FFmpeg/n7.1.5/libavformat/hlsenc.c)

Apple 的 HLS 编写要求建议约六秒分片，并要求视频分片从 IDR 开始。这里还需区分 FFmpeg 的关键帧标记与经过验证的 IDR/随机访问能力，不能未经检查就加入 `EXT-X-INDEPENDENT-SEGMENTS`。[Apple HLS authoring specification](https://developer.apple.com/documentation/http-live-streaming/hls-authoring-specification-for-apple-devices/)

`hls_init_time` 也不是当前命令的直接修复：FFmpeg 7.1.5 的 `hls_init()` 仅在 `max_nb_segments > 0` 时采用初始时长，本项目显式设置 `hls_list_size=0`。不要只增加该参数就宣称首片已缩短；必须测量实际输出。[对应实现](https://raw.githubusercontent.com/FFmpeg/FFmpeg/n7.1.5/libavformat/hlsenc.c)

两个 Dolby 版本的平均码率均超过 20 Mbps。AAC 副本只减少音频开销，无法保证在持续 20 Mbps 链路上流畅播放。增大缓冲可缓解短暂波动，却不能补足长期吞吐缺口。若需弱网观看，应另行提供用户明确选择的低码率视频版本，并说明视频重编码、HDR/DV 保留能力及原档边界。

## HEVC＋FLAC 原档的包装边界

同轮业务检查另发现一个两 P 的 3840×2160 HEVC＋FLAC 归档：原档完整，HLS 准备却被包装器的音频白名单拒绝。发生错误时白名单只接受 AAC、AC-3、EC-3；这是本项目尚未开放并验证该组合，不是原档损坏，也不能据此判断 FFmpeg 或 Apple 不支持 FLAC。页面约 7.72 Mbps 的数值是媒体码率信息，不是本站链路吞吐或首播实测。

需要分开判断三个层次：

| 层次 | FLAC | Opus | 对本项目的意义 |
| --- | --- | --- | --- |
| FFmpeg 7.1.5 的 MP4 封装 | `fLaC` sample entry，`dfLa` 保存 STREAMINFO | `Opus` sample entry，`dOps` 保存解码配置 | 两者均有 MP4 写入实现；这些分支没有实验级 `strict` 门槛，不能沿用旧版本资料误加全局 `-strict experimental` |
| hls.js 1.7.3 的 fMP4 路径 | 已列为支持，仍取决于浏览器 MSE/解码能力 | 同左 | 库能读取格式不等于设备必能播放；仍需实际播放与 seek 验收 |
| Apple HLS 编写规范 | 2.2、2.5 列出 FLAC；2.25 要求 fMP4 | 未列入该规范的音频编码清单 | FLAC 有正式依据；不能因浏览器支持某种 Opus 文件或录制格式，推断 Apple 原生 HLS 也有相同保证 |

来源：[FFmpeg 7.1.5 `movenc.c`](https://raw.githubusercontent.com/FFmpeg/FFmpeg/n7.1.5/libavformat/movenc.c)、[hls.js 1.7.3 功能范围](https://raw.githubusercontent.com/video-dev/hls.js/v1.7.3/README.md)、[Apple HLS authoring specification](https://developer.apple.com/documentation/http-live-streaming/hls-authoring-specification-for-apple-devices/)。

FFmpeg 的 `mov_write_dfla_tag()` 要求 34 字节 FLAC STREAMINFO，并将其写入 `dfLa`。受控开放 FLAC 时，应在原有分辨率、色彩、时长、声道、采样率校验之外，验证输入与输出的 STREAMINFO、位深及完整音视频压缩包摘要。不能只增加一个白名单值，或把命令退出成功当成无损证据。fMP4 可以容纳 FLAC，不代表产物自动满足所有 CMAF profile 或 Apple authoring 要求。

离线小样实验已完成：FFmpeg 7.1.5，以两种四秒 128×72 视频（H.264、HEVC）分别搭配 48 kHz、24 bit、双声道 FLAC，使用两秒 HLS 目标时长。现有 `strict=unofficial` 下，两种组合均成功生成 fMP4 HLS；各自源与输出的视频包摘要、音频包摘要及解码后 `pcm_s32le` 摘要均一致，位深/采样率/声道保持，输入文件未改变。另作的 `strict=experimental` 对照也通过，但没有必要为此降低现有配置。正式实现已增加 FLAC 窄范围支持及完整音视频包、STREAMINFO 和音频属性守护；生产校验合并两路包摘要读取，不重复解码 PCM。合成回归另覆盖 16 bit / 44.1 kHz 立体声、HDR HEVC＋24 bit / 48 kHz，以及 24 bit / 96 kHz 六声道。

真实两 P 长视频 `BV1r4a96KEfn` 也已完成恢复：分别生成 709 / 763 片，任务均首次成功，原 4K HEVC 画面与 24 bit / 48 kHz 双声道 FLAC 保留，完整音视频包及 STREAMINFO 摘要一致。访客会话、清单及首片 64 KiB Range 均验证成功，Range 返回 206；后台空闲时组合请求单次约 44.39 / 49.94 毫秒，P1 在 P2 后台复制期间相同链路曾等待 13.07 秒，不能忽略宿主 I/O 争用。

当前桌面内嵌浏览器的播放统计确认两 P 均走原生 HLS。P1 从约 26 秒续播并跨片播放，中段跳至 2129 秒后继续至 2161 秒；P2 从约 22 秒续播，中段跳至 2292 秒后继续至 2360 秒，均无媒体解码错误。这不是全片连续观看、物理音频试听或 Apple 真机验收。

对于 STREAMINFO，可用 FFprobe 的 `-show_streams -show_data_hash sha256` 获取 codec extradata 摘要；源与输出都必须存在正确长度的配置，不能把两侧均缺失当作匹配。结合音频属性、压缩包及 PCM 摘要，可以增加窄范围守护而不新造一套完整 MP4 parser。[FFprobe 数据摘要选项](https://ffmpeg.org/ffprobe-all.html#Main-options)

hls.js 的能力检查会尝试 `flac`、`fLaC`、`FLAC` 别名，其源码还记录过浏览器接受某个 MIME 别名却无法播放的情况。测试应包含当前项目使用的实际引擎、连续跨片播放和首中尾 seek，而不是只执行 `MediaSource.isTypeSupported()`。[hls.js 1.7.3 codec 处理](https://raw.githubusercontent.com/video-dev/hls.js/v1.7.3/src/utils/codecs.ts)、[fMP4 sample entry 处理](https://raw.githubusercontent.com/video-dev/hls.js/v1.7.3/src/utils/mp4-tools.ts)

Apple 规范 2.3 仍要求提供相应的立体声 AAC 兼容音频。对于无法播放原音频的设备，可沿用独立的“视频码流复制＋AAC 立体声”派生路线，保留原始 HEVC/色彩/DV 数据，明确音频已进行有损转换；这也是建议扩展的兼容策略，不能将 FLAC 原档静默换成 AAC。只有单独的 AAC 版本，不等于已经实现供 Apple 自动选择音轨的 multivariant master。原档下载或文件播放可继续保留，但同一设备无法解码 FLAC 时，切换到原文件也不是可靠的音频降级方案。[Apple 音频兼容要求](https://developer.apple.com/documentation/http-live-streaming/hls-authoring-specification-for-apple-devices/)

本次最窄范围是带保真校验地开放实际遇到的 FLAC，**不顺带开放 Opus 或任意音频编码**。Opus 后续还需要验证 `dOps` 的 pre-skip、增益、声道映射以及剪裁、时间戳和 seek；压缩包相同不单独证明解码起点及尾部裁剪正确。当前没有 Safari、iPhone、iPad 或 WKWebView 真机的 FLAC HLS 验收结果，不承诺仅凭规范和离线校验即可覆盖所有 Apple 设备。

## 多 moof 与渐进加载：尚未验证的候选

候选是给新生成的 fMP4 片内部增加较短 fragment，例如在现有 `hls_segment_options` 上实验 `frag_duration=1000000`，再评估 hls.js `progressive:true`。`frag_duration` 的单位为微秒；这是 MP4 fragment 划分参数，不等于把每个 HLS media segment 改为一秒，也不会缩短源 GOP。[FFmpeg MOV/MP4 fragmentation 文档](https://ffmpeg.org/ffmpeg-formats.html#Fragmentation)

hls.js 1.7.3 的渐进加载默认关闭，官方仍标为实验能力。其 MP4 demuxer 通过 `segmentValidRange()` 留下最后一个尚未完成的 fragment；不足两个 `moof` 时会保留全部输入，等待后续数据或结束刷新。因此在本次三个单 `moof` 首片上，仅打开渐进加载没有已证实的首帧收益。[版本对应 API](https://raw.githubusercontent.com/video-dev/hls.js/v1.7.3/docs/API.md)、[MP4 demuxer](https://raw.githubusercontent.com/video-dev/hls.js/v1.7.3/src/demux/mp4demuxer.ts)、[fragment 边界处理](https://raw.githubusercontent.com/video-dev/hls.js/v1.7.3/src/utils/mp4-tools.ts)

这个候选尚未验证 FFmpeg HLS 子封装器的最终 box 布局、额外封装开销、CPU/内存代价或浏览器收益。分片内部多个 fragment 可以依赖此前的解码状态，不能把它们各自当成独立 seek 起点。也不能把 `frag_duration`、`progressive` 或 `lowLatencyMode` 等同于实现了 LL-HLS：Apple 的低延迟 HLS 还包含 Partial Segment、播放列表及服务器协作机制；现有静态 VOD 没有这些声明。[Apple Low-Latency HLS](https://developer.apple.com/documentation/http-live-streaming/enabling-low-latency-http-live-streaming-hls)

Native HLS 使用浏览器自身的读取、缓冲及解码策略，不受 hls.js 配置控制。官方对 fMP4/HLS 的支持不能证明本项目拟议命令产物已通过 Safari/WKWebView，当前没有 macOS、iPhone 或 iPad 真机的多 fragment 验收证据。

若后续启动实验，应使用无网络隔离容器的独立 scratch，先运行极小 H.264＋AAC 合成片，再使用明确授权的短 DV＋EC-3/JOC 片段。采用前至少验证：

1. 每片实际 `moof/mdat` 数量、首次可追加 fragment 的字节数及持续时间；不只检查命令是否成功退出。
2. 源文件摘要不变；输出所有视频及音频压缩包摘要分别一致；init 的 DOVI、EC-3/JOC、复杂度字段和色彩信令保持。
3. 时间戳连续、全长一致、音画同步；首、中、尾和跨分片 seek 均能正确解码。短片必须覆盖一个以上实际切片边界。
4. 默认包装与候选包装在相同输入下的时间、峰值 RSS、写入字节、片数和 HTTP 请求数。
5. hls.js 默认/渐进两种配置以及真实 native HLS 的首帧、卡顿和 seek；任何一种失败都不能只凭 FFprobe 通过而上线。

## 应先优化的现有路径

研究开始时，[播放器](../frontend/src/components/ArchivePlayer.vue) 在创建媒体元素前串行等待整包弹幕，且在媒体就绪后才读取续播位置。本轮已改为异步弹幕与提前并行读取进度，并加入请求取消、过期结果和切 P 保护；慢弹幕不再阻塞媒体启动。有效已知进度传入 hls.js `startPosition`，进度等待有 400 毫秒预算，晚到结果不会强制跳转已经开始的播放。[hls.js API](https://raw.githubusercontent.com/video-dev/hls.js/v1.7.3/docs/API.md)

多节点测速也应有整个操作的时间上限。仅为 Range 探测设置超时，不足以约束随后等待的统计提交和第二次会话创建。这些可选操作不应无限延长首播路径。本轮分发层已复用同一次路线查询；正常分片读取不再持有播放会话写锁等待存储 HEAD，故障切换才加锁并重新确认状态，仍逐次验证会话授权与到期状态。

当前 index 已按不可变摘要缓存，同时保留每个会话的权限检查；本机 index/init 很小，没有证据支持先引入新的缓存服务。远端存储冷读仍应另测，不能用本机命中时间代替。当前 NGINX 通过 `X-Accel-Redirect` 转到内部媒体目录并使用 `sendfile`，本地视频主体不经过 Python 代理传输；全局关闭 `proxy_buffering` 不是该路径的直接优化。[NGINX internal location](https://nginx.org/en/docs/http/ngx_http_core_module.html#internal)、[sendfile](https://nginx.org/en/docs/http/ngx_http_core_module.html#sendfile)、[proxy buffering](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_buffering)

## 后台内存与 I/O 观察

同次轻量进程快照发现，download worker 的 cgroup 总内存约 4.57 GB，其中文件缓存约 4.26 GB、匿名内存约 189 MB；media worker 约 7.27 GB，其中文件缓存约 6.90 GB、匿名内存约 173 MB。两个 Celery 子进程 RSS 分别约 146 MB、106 MB，取样时没有 FFmpeg 子进程，OOM 计数均为零。这是一次快照，不是长期泄漏测试。

Docker 在 cgroup v2 下从显示内存中扣除 `inactive_file`，并非扣除全部文件缓存；仍活跃的文件页会造成 worker 显示数值较高。不能仅凭几 GB 的显示值判定 Python 泄漏。应联合观察匿名内存、进程 RSS、文件脏页、I/O 延迟和压力指标。[Docker stats 文档](https://docs.docker.com/reference/cli/docker/container/stats/)

研究开始时响度分析与 HLS 包装各自物化源文件，[materialize_asset](../backend/app/storage/service.py) 内还有复制及完整性验证，多次全文件读写会增加缓存和磁盘压力。本轮已实现同一任务共享一次完整校验的临时源，仍保留摘要、身份和文件状态检查、取消及清理保证，不跨任务缓存。完成的响度分析单独通过租约保护提交，包装失败后重试不必再次分析整段音轨。频繁重启 worker 或全局丢弃页缓存不是此快照支持的解决办法。若需要容器资源控制，应先在代表性任务上测量；`memory.high` 可能触发回收和节流，过低会降低性能，硬上限还可能触发 OOM。[Linux cgroup v2 内存控制](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html)

## 后续量化验收

固定输入、播放起点和网络条件，分别测首次进入、已有缓存、续播、跨片 seek。至少记录点击到首帧、播放会话耗时、manifest/init/首片的请求时序与字节数、首帧前总下载量、卡顿次数与总时长。hls.js 可以结合加载/追加事件，浏览器可用 `requestVideoFrameCallback` 时记录真正首帧；native HLS 不公开的内部数据留空，不用推算值冒充实测。

后台繁忙与空闲情况应分开记录。本次数据不足以声称某参数带来百分比提升，也没有新的 CMAF 合规、Apple master 或硬件空间音频验证结论。当前优先级是移除无关串行等待和重复 I/O，然后再依据受控实验决定是否采用新的片内布局。
