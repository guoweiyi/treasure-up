# 《红豆》启动音爆调查

调查日期：2026-10-05（Asia/Shanghai）。问题由用户描述为“有时打开《红豆》时，杜比全景声音轨短暂刺啦；耳机和扬声器均会发生”。

## 当前结论

**尚未在 iOS 真机复现，也没有证据把问题定性为某个单一原因。** 本次取得的《红豆》两个分 P 归档，前 20 秒 E-AC-3 音频压缩包及时间戳逐个一致，前 15 秒的软件解码 PCM 也完全一致。两种离线解码器均未显示可解释明显启动刺啦的固定首帧尖峰、削波或解码错误。这个结果缩小了排查范围，但不能证明完整片源、Apple Atmos 对象渲染、历史进度跳转以及 iOS 输出链路没有问题。

本次只登录授权测试站并 GET 现有目录、元数据、存储清单和受字节数限制的资产。没有触发下载任务、重采集、转码、HLS 打包、播放会话创建、远端配置修改或进度写入。未修改 Swift/后端代码。

## 环境与取样边界

- 测试站：`https://test-tp.gwy.fun`，调查时目录共 9 个视频。
- macOS 27.0，Homebrew FFmpeg / FFprobe 9.0.1；未安装新工具。
- 原档为服务器已保存的归档，**不是本次从 B 站源 CDN 独立重取的音频**。
- 请求使用 `Range`，核实 `206` 与 `Content-Range`，没有下载完整电影文件。
- 首部字节样本含 MP4 元数据和约前 20 秒以上可用包；统计只分析前 15 秒 PCM、前 20 秒包。
- 解码只落本地 PCM 或 null sink，不向耳机/扬声器播放。
- 账号密码及 Cookie 只保留在采样进程内存，没有写入仓库、取样脚本、证据文件或日志。进程已退出。
- 工具记录与统计摘要位于 `/private/tmp/treasure-audio-investigation/`。因本机空间不足，调查完成后已删除本任务生成的媒体与 PCM 临时文件，保留探测 JSON、解码日志及统计摘要；可按本文重新有界取样。

## 重点样本

视频 `BV1RUfgBiEiY`，本站视频 ID `1ddff9b2-1e3b-4c23-8a1a-e73f5eeddbba`。

| 分 P / 版本 | part ID | variant ID | asset ID | 首部取样 |
|---|---|---|---|---:|
| P1 杜比视界 / 原档 | `b4414567-abb3-4c5c-be76-9173cba3148b` | `0b41025c-9995-43d5-9057-71cc8b62c560` | `11b0a759-df1b-4935-aaa8-ff86fb35b996` | 16 MiB |
| P1 / AAC 兼容版 | 同上 | `7503cbff-b554-4bcf-a519-f4f3914f82b3` | `d903c71e-a261-43f1-b95d-3ae886044f0a` | 16 MiB |
| P2 HDR / 原档 | `21678633-f33e-418e-b404-96c4ca7628be` | `dc063214-7f68-4ce7-baf2-555a56f5394c` | `bc85bab6-a0b9-4fb4-af54-fb91a0501a89` | 12 MiB |
| P2 / AAC 兼容版 | 同上 | `b00a1fa4-e522-466f-ab50-2eee62f4ff5c` | `d48d95b7-b2e2-41b7-9860-33cc4a302717` | 10 MiB |

两份原档均为 HEVC + E-AC-3，音频 FFprobe 报告 `Dolby Digital Plus + Dolby Atmos`、48 kHz、5.1(side)、1024 kb/s。两个 AAC 兼容版均为保留 HEVC 视频、AAC-LC 立体声、48 kHz、约 192 kb/s。调查时两个分 P **均无 HLS 版本**，不能把当前《红豆》的启动问题归因于本站现有 HLS 分片。

## 包与波形实测

软件解码采用 FFmpeg 原生 `eac3` / `aac`，输出 `pcm_f32le`，没有降混、音量滤镜或重采样。

| 样本 | 前 100 ms 峰值 | 前 5 s 峰值 | 前 15 s 峰值 | `abs(sample) >= 1` | 15 s 解码 |
|---|---:|---:|---:|---:|---|
| 红豆 P1 E-AC-3 | −107.638 dBFS | −106.449 dBFS | −20.263 dBFS | 0 | 无警告、exit 0 |
| 红豆 P2 E-AC-3 | −107.638 dBFS | −106.449 dBFS | −20.263 dBFS | 0 | 无警告、exit 0 |
| 红豆 P1 AAC | −104.039 dBFS | −102.885 dBFS | −17.313 dBFS | 0 | 无警告、exit 0 |
| 红豆 P2 AAC | −104.039 dBFS | −102.885 dBFS | −17.313 dBFS | 0 | 无警告、exit 0 |

没有非有限 PCM 样本。原档前 6 秒在约 −106 dBFS，6–7 秒约 −90 dBFS，7 秒之后才出现较明显节目声音。这些数值针对解码输出的声道波形，不能测出一个尚未运行的 iOS 硬件输出瞬态。

- E-AC-3：首包 `PTS = DTS = 0`，每包 1536 样本 / 32 ms / 4096 字节。P1/P2 前 20 秒共 625 个包的 SHA-256、PTS、DTS、duration、size **逐个相同**，没有大于一个采样周期的时间戳间断。前 15 秒 PCM 字节完全相同。
- AAC：首包 `PTS = DTS = -0.021333`，包含 `Skip Samples = 1024`；这是本次实际读取的编码前导及裁剪信息，不能仅凭负 PTS 判断音频损坏。P1/P2 前 20 秒共 938 个包及前 15 秒解码 PCM 彼此完全相同，没有上述时间戳间断。
- E-AC-3 第一包 SHA-256：`ccb9725644902d6ad39b982bf81ed4967dc4469745093194dd1ffd9492661911`。
- 两个原档服务器元数据均记录整条音频 payload hash `10004ecb6a36f99b08852a86cff2c96f35b5375fa82696e7a92b3ca1600cabab`、`payloads_verified = true`。这是采集时服务器记录，和本次独立验证前 20 秒包不是同一证据；没有在本次独立重算源 CDN 的整条摘要。

### Apple AudioToolbox 离线对照

同一 P1 头部由 `ffmpeg -c:a eac3_at` 调用 macOS AudioToolbox 解码。第一次受限沙箱运行返回 `AudioToolbox init error: 1718449215`（fourcc `fmt?`）；**退出沙箱后相同素材成功解码**，因此不能把首次初始化失败归为片源问题。

成功结果：48 kHz、5.1(side)，输出 14.994 秒，前 5 秒 PCM 为零，整体峰值 −20.260 dBFS，满幅样本 0、非有限样本 0、无解码错误。其内部输出为 s16 再转换到浮点，不能要求它与 FFmpeg 浮点解码字节完全一致。该测试验证的是 macOS 声道解码，**没有验证 iOS AVPlayer、Atmos 空间对象渲染、耳机空间化、扬声器或 AirPlay 硬件输出**。

## HLS 与其他素材对照

由于《红豆》没有 HLS，未为了调查创建它。另选库内 `BV124YF68Ekd`（《One more time, One more chance》）的现成原档/AAC/HLS作分发链路对照，不能将该片结果替代《红豆》的结论。

- 原档资产 `3d203b61-138b-4d3f-befe-bb281992edff`，E-AC-3 / Atmos，48 kHz，7.1。
- AAC 资产 `8b98c1b2-cc27-4b49-9d17-fb72c473a1bb`。
- E-AC-3 HLS index `b52a70c1-4333-497e-9f86-6cde77171134`，AAC HLS index `8ad61e10-4fe8-464b-b8b8-45a6ffadb7b0`。
- 各取已有 init 和前两个分片（标称 10 s + 5 s），按原次序生成本地只含这些分片的 playlist。
- E-AC-3 HLS 前 468 个包与原档相同，PTS 固定增加 25 ms，无包间断。其 14.976 秒解码 PCM 与原档对应前缀字节完全相同，无削波/解码警告。
- AAC HLS 前 703 个包与兼容 MP4 相同，PTS 固定增加约 24.333 ms，无包间断。本机 FFmpeg 解码 HLS 时保留 1024 个前导样本，去掉这些样本后与 MP4 对应 PCM 前缀完全相同。MP4 首包有 `Skip Samples = 1024`，HLS 首包不报告该 side data。**这是一个应单独回归的前导处理差异；本次没有证明它产生可听音爆，更不能用它解释无 HLS 的《红豆》。**
- 对照 `BV14EHv6YEt8` 的普通 AAC 原档/兼容版前 15 秒均无满幅样本或解码警告。未做整库、整片穷尽验证。

## 代码链路核对

以下为读代码所能确认的行为，不等于故障复现：

1. `backend/app/ingest/media.py` 的 `ControlledMerger.run`（约 221 行）使用 `-c copy` 合流，EC-3 使用 `ec-3` sample entry，保留/修复 `dec3` 配置；杜比源对合流前后音视频做 packet hash 比对。这降低了“合流把压缩音频重新编码坏了”的可能性，不能排除源自身内容或特定解码器对容器信令的差异。
2. `_audio_compatible_copy`（约 570 行）复制视频，将 E-AC-3 编码为 AAC-LC、192 kb/s、2 声道。没有额外增益/限幅/淡入；兼容版已经不含 Atmos。不能把自动降为 AAC 当作修复 Atmos 根因。
3. `backend/app/playback/hls.py`（约 232–310 行）使用 `-c copy` 和 fMP4。源关键帧决定真实分段，所以配置 `hls_time = 6` 不等于所有分片恰好 6 秒。EC-3 配置、声道与相关码流摘要会核验。
4. `backend/app/playback/loudness.py` 只解码到 null sink 保存测量结果，没有改写音频。增益公式只衰减不放大；潜在 Atmos、多声道旁路。《红豆》原档 metadata 为 `atmos_bypass = true`，不是全片施加了响度处理。
5. `frontend/src/components/ArchivePlayer.vue`：创建 Artplayer 时 `autoplay = false`；应用音量偏好、读取进度，在 `ready` 后按播放意图继续。`applyVolume` 只在用户启用平衡且非 Atmos 旁路时应用 <=1 的增益。
6. `frontend/src/player/mediaAdapter.ts`：iOS 原生 HLS 走 HTMLVideoElement，文件直读直接设置 `video.src`；没有 WebAudio 自定义增益/AudioWorklet 路径。读取这些逻辑没有复现浏览器输出音爆。
7. 本次已将上述媒体证据交给负责 `PlaybackCoordinator` 的 agent。应结合带 generation/item 身份的实际启动、恢复历史位置、路由/中断通知和 AudioSession 激活日志复现；本报告不把并行开发中的播放器修改视为已验证修复。

## 原生客户端的后续取证

新版客户端在“设置 → 播放 → 导出播放诊断”中提供最近 80 条内存事件，含毫秒时间、视频 / 分 P / 版本 ID、源编码和声道、当前进度 / 倍速、输出采样率 / 声道 / 路由类型、AudioSession 配置与系统报告的 rendering mode。不会记录认证材料、媒体 URL、设备私有名称或视频标题。

真机复现刺啦声后立即导出，并补充大致发生时刻、是否从历史进度恢复、外放或耳机。用它对齐 play / ready / playing、route change、interrupt、renew 事件。导出日志本身没有声波录音，仍不能单独证明解码器或硬件输出产生了音爆。

客户端已减少重复 AudioSession 配置，并给旧 AVPlayerItem 的迟到通知增加 generation 隔离；这些属于启播流程加固，**尚未证明解决了该声音异常**。没有添加淡入、限幅或自动降为 AAC。

## 可复现命令

所有命令输出只写临时目录；不要把凭据、Cookie、播放会话 URL 或对象存储签名 URL放入 shell 参数、仓库或报告。

### 有界读取一个已归档资产

下面脚本交互输入账号与密码，Cookie 只在内存。默认取 P1 原档前 16 MiB；换成上表对应 asset ID 可复测其他版本。不要移除 `206` / `Content-Range` / 大小核验。

```python
import getpass, http.cookiejar, json, pathlib, urllib.request

origin = "https://test-tp.gwy.fun"
asset_id = "11b0a759-df1b-4935-aaa8-ff86fb35b996"
maximum = 16 * 1024 * 1024
folder = pathlib.Path("/private/tmp/treasure-audio-recheck")
folder.mkdir(mode=0o700, exist_ok=True)
jar = http.cookiejar.CookieJar()
client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
username = input("测试账号: ")
password = getpass.getpass("测试密码: ")
request = urllib.request.Request(
    origin + "/api/v1/auth/login",
    data=json.dumps({"username": username, "password": password}).encode(),
    headers={"Content-Type": "application/json", "Origin": origin},
)
with client.open(request, timeout=30) as response:
    json.load(response)
password = None
request = urllib.request.Request(
    origin + "/api/v1/assets/" + asset_id,
    headers={"Range": f"bytes=0-{maximum - 1}"},
)
with client.open(request, timeout=45) as response:
    assert response.status == 206
    assert response.headers.get("Content-Range", "").startswith("bytes 0-")
    data = response.read(maximum + 1)
    assert len(data) <= maximum
(folder / (asset_id + ".mp4")).write_bytes(data)
print("Saved bounded sample:", len(data), "bytes")
```

### 时戳、压缩包与软件解码

```sh
ffprobe -v error -select_streams a:0 -read_intervals '%+20' \
  -show_streams -show_packets -show_data_hash sha256 -of json \
  /private/tmp/treasure-audio-recheck/11b0a759-df1b-4935-aaa8-ff86fb35b996.mp4 \
  > /private/tmp/treasure-audio-recheck/probe.json

ffmpeg -nostdin -hide_banner -v warning \
  -i /private/tmp/treasure-audio-recheck/11b0a759-df1b-4935-aaa8-ff86fb35b996.mp4 \
  -map 0:a:0 -vn -sn -dn -t 15 -c:a pcm_f32le -f f32le \
  /private/tmp/treasure-audio-recheck/decoded.f32
```

若出现样本 EOF、partial file 等错误，先核对失败位置是否超出已取前缀；不能把有意截断的尾部误判为服务器媒体损坏。本次上述 15 秒解码没有此类错误。

```python
import array, math, pathlib
samples = array.array("f")
samples.frombytes(pathlib.Path("/private/tmp/treasure-audio-recheck/decoded.f32").read_bytes())
channels, rate = 6, 48000  # 必须与本次 probe 匹配；AAC 为 2
for seconds in [0.1, 1, 5, 15]:
    values = samples[:int(seconds * channels * rate)]
    peak = max(map(abs, values), default=0)
    print(seconds, "peak dBFS", 20 * math.log10(peak) if peak else "-inf",
          "full-scale samples", sum(abs(x) >= 1 for x in values),
          "nonfinite", sum(not math.isfinite(x) for x in values))
```

### Apple 声道解码对照

在可访问系统 AudioToolbox 的终端环境执行，不播放声音：

```sh
ffmpeg -nostdin -hide_banner -v info -c:a eac3_at \
  -i /private/tmp/treasure-audio-recheck/11b0a759-df1b-4935-aaa8-ff86fb35b996.mp4 \
  -map 0:a:0 -vn -sn -dn -t 15 -c:a pcm_f32le -f f32le \
  /private/tmp/treasure-audio-recheck/apple-decoded.f32
```

核对日志确认实际调用 `eac3_at`，并记录它报告的声道数、采样格式、解码延时/输出长度。仅此命令成功不能宣布 iPhone Atmos 输出正常。

## 后续真机复现矩阵

必须在发生问题的设备上记录确切分 P、iOS 版本、设备型号、输出路由、选择的 variant/protocol、恢复位置以及是否切换过其他音轨。最低对照：

1. P1/P2 Atmos，从 0 秒冷启动 vs 从历史位置恢复，各重复 10 次。
2. 同一分 P 的 AAC 兼容版重复上述动作；兼容版只作定位对照，不替代 Atmos 验收。
3. 内置扬声器、发生问题的耳机，固定系统音量；记录空间音频开关及是否刚切换路由。
4. 开启/关闭播放器、连续切换视频、前后台、锁屏/解锁、耳机断连/重连分别测试，记录声响发生在“会话激活”“item ready”“seek 完成”“开始出声”哪一步。
5. 如果只在非零历史位置出现，取该位置前后有限时间段对比包与解码；当前前 15 秒测试不能覆盖任意历史位置。
6. 只有在相同资产、相同起点的声道解码或真机可控复现中观察到相同异常，才进一步归因；不要用无条件静音/淡入掩盖尚未定位的问题。

**未验证项**：B 站源 CDN 同版本独立下载、整片所有时间位置、iOS 原生 Atmos 对象渲染、真机耳机/扬声器声学录音、AirPlay、外接 DAC/HDMI，以及本轮并行播放器改动之后的实际音爆消失情况。
