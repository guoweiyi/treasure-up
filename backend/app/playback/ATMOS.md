# EC-3 / Dolby Atmos 处理

原始 EC-3 音轨以 stream copy 保存。AAC 兼容音轨另存为副本，不覆盖原档；选择 EC-3 却取得 AAC 时返回 `audio_codec_mismatch`。

## 采集与分片

Atmos 标识依据有界音频样本的 ffprobe 检测结果。MP4 的 `ec-3/dec3` 还需保留 JOC 扩展与实际 `complexity_index_type_a`，后者不能按声道数猜测。新 HLS 的 `ec3_configuration_verified` 记录容器配置校验结果，音视频压缩包哈希用于检查合并前后内容是否一致。

部分 FFmpeg 7 合并路径会遗漏 `dec3` 中的 JOC 扩展。[FFmpeg 8 的 muxer](https://www.ffmpeg.org/doxygen/8.0/movenc_8c_source.html)已包含相关写入支持；当前 `ec3.py` 对缺失配置进行有界修复，来源是所选轨道或完整音频帧中的实测值。修复仅作用于未发布的下载临时文件或新 HLS `init.mp4`，检查子流、偏移、包哈希后才发布。已有归档不被原地修改；输出配置正确时不重写。

高码率 HLS 的首次探测可能只识别 AC-3 核心声道。发现 E-AC-3 声道数或布局不匹配时，只扩大一次有界探测窗口；若仍不匹配，或者 JOC、DOVI、时长、压缩包校验失败，则拒绝发布。新下载还会完整解码音轨，完成后记录 `audio_decode_verified`。

## 兼容播放

`video-copy-aac-v2` 副本保留视频压缩包，音频转为 AAC-LC 立体声，目标码率 192 kbit/s。混音使用归一化矩阵和 1 dB 余量；副本记录 `audio_mix_revision=2`、`compatibility_mode=audio_only` 及原轨道关联，`dolby_atmos=false`。它仍占用一份视频存储空间，也仍要求设备支持原视频编码与 HDR 格式。

原档和兼容副本由用户选择。网页优先使用浏览器支持的原生 HLS，否则使用 hls.js。当前 HLS 为单一复用媒体列表，不生成多音轨 master playlist。AAC 转码有音质损失，不能标为 Atmos；播放器能解码 EC-3 也不等于输出设备正在渲染空间音频。

旧归档可能保留 Atmos 音频包但缺少容器信号。普通统计刷新或重建 HLS 不会自动修复旧原档；当前没有专用的旧原档修复入口，维护时应保留已有文件。HDR 转 SDR 尚无已验证的色调映射路径，任务会返回 `hdr_conversion_unsupported`，不生成或标记虚假的 SDR 副本。

参考：[ETSI TS 102 366](https://www.etsi.org/deliver/etsi_ts/102300_102399/102366/01.03.01_60/ts_102366v010301p.pdf)、[FFmpeg E-AC-3 解析器](https://github.com/FFmpeg/FFmpeg/blob/master/libavcodec/ac3_parser.c)、[Apple HLS 音频格式要求](https://developer.apple.com/documentation/http-live-streaming/hls-authoring-specification-for-apple-devices-appendixes)。
