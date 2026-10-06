# 离线音频诊断

`backend/scripts/audio_diagnose.py` 用来检查已下载到本机的原档、兼容副本或本地 HLS 清单。它只读媒体，不连接业务数据库，不重新编码或修复媒体；仅将 JSON 报告写到标准输出。依赖 Python 3 和 FFmpeg，不需要 FFprobe 或后端服务。

```powershell
# 在项目根目录运行；各输入的报告顺序与命令行一致。
.\.venv\Scripts\python.exe backend/scripts/audio_diagnose.py --ffmpeg node_modules/ffmpeg-static/ffmpeg.exe --sha256 '原档.mp4' '兼容副本.mp4' '本地分片/index.m3u8'
```

macOS 或 Linux 已有 FFmpeg 时可运行：

```sh
python3 backend/scripts/audio_diagnose.py --sha256 original.mp4 compatible.mp4 local-hls/index.m3u8 > audio-report.json
```

输入必须是本地文件。HLS 引用的初始化片段、媒体片段及密钥也必须已在本地；工具只允许 `file,crypto,data` 协议，远程地址会被拒绝。不要将输出重定向到输入媒体或清单文件。`--timeout 7200` 是每条音轨的最大处理秒数，默认 7200；发现音轨的初步探测最多 120 秒，每个 FFmpeg 进程的每条输出管道最多保存 1 MiB。达到限制会停止该次检查，并在报告中明确标记。

报告使用 `input_1`、`input_2` 等编号，不写文件名、路径、原始元数据或 URL。FFmpeg 原始日志可能包含签名地址，报告只保留预定义的错误类别、次数、返回码与停止原因。可选 SHA-256 标识实际输入文件；对 HLS，它只标识所传入的清单，不代表片段集合的完整性校验。

工具逐条音轨执行 `-xerror -err_detect explode` 严格解码，使用双精度浮点格式统计采样峰值、样本数及 NaN/Inf，输出到 null，不生成处理后媒体。单轨报告包含编码、采样率、声道布局与解码样本时长；输入报告包含容器声明的起始时间和时长。时间戳异常以 FFmpeg 诊断类别呈现，工具没有逐包验证时间连续性；null 输出的进度时间不可靠，因此不作为音轨截止时间。失败时测量标记为部分或不可用。报告返回码为 0 表示所有输入的音轨检查完成，1 表示存在未完成或失败的检查；超过满幅的峰值是观测值，不单独将严格解码判为失败。

`-xerror` 或退出码为 0 不单独构成通过证据：工具也检查 FFmpeg 的 `error/fatal/panic` 日志级别，出现未知类别的错误、NaN/Inf 或缺失测量时同样判为失败。

`sample_peak_above_full_scale=true` 表示浮点解码采样值超过 ±1，可能为后续整数输出或增益处理提供线索；它不是已发生削波或滋滋声的证明。这里测量的是采样峰值，不是过采样真峰值。成功解码也不能排除已编码进音轨的杂音、时间戳边界问题、浏览器音频图或系统设备故障。

遇到难复现的问题，可保留原档、兼容副本与对应本地 HLS 的报告，并另记出现声音的大致播放时间、所选版本、音量平衡是否开启，以及网页/App、macOS 和浏览器版本。若某轨严格解码失败，优先检查原档和副本在相同位置的差异；若全部成功，应继续对照实际听感与播放端，不能把报告当作“音频绝对正常”的证明。
