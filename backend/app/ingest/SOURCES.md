# 音视频采集参考

采集使用账号有权访问的 B 站网页接口，并通过 yt-dlp 获取音视频流。这些接口可能变化；权限不足、格式缺失或校验失败时，任务会报告失败，不以低规格文件冒充所选原档。

- [yt-dlp Bilibili 提取器](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/bilibili.py)：DASH 视频、普通 / Dolby 音轨及 FLAC 的获取方式。
- [yt-dlp FFmpeg 合并器](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/postprocessor/ffmpeg.py)：通过 stream copy 合并音视频。本项目另加取消、超时、大小限制和媒体校验。
- [FFmpeg stream copy](https://ffmpeg.org/ffmpeg.html#Streamcopy)：复制压缩包而不重新编码。兼容转码另存为副本，原档保留。
- [FFmpeg MP4 muxer](https://github.com/FFmpeg/FFmpeg/blob/master/libavformat/movenc.c) 与 [DOVI 结构](https://github.com/FFmpeg/FFmpeg/blob/master/libavutil/dovi_meta.h)：Dolby Vision 配置与颜色元数据。
- [FFmpeg E-AC-3 解析器](https://github.com/FFmpeg/FFmpeg/blob/master/libavcodec/ac3_parser.c) 与 [ffprobe](https://ffmpeg.org/ffprobe.html)：音轨、声道和 Atmos / JOC 信号检测。

`dolby_vision` 依据 DOVI profile 与 RPU 标记；`dolby_atmos` 依据有界 E-AC-3 音频样本的 Atmos 检测结果。HEVC、E-AC-3 或多声道名称本身不足以设置这些标识。容器信号保留和兼容音轨规则见 [Atmos 处理](../playback/ATMOS.md)。

依赖按各自许可使用：[yt-dlp](https://github.com/yt-dlp/yt-dlp/blob/master/LICENSE)、[FFmpeg](https://ffmpeg.org/legal.html)。FFmpeg 二进制的许可还取决于构建时启用的组件。
