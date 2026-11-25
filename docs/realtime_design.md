# 实时多模态翻译系统设计（交差版）

> 2026-11-24 | 对照文档：`docs/build_multimodal_translation.md`（离线版）。  
> 文中的代码是参考实现，**没有在本环境运行过**；延迟数字都是估计，必须在 GPU 机器上实测后替换。

---

## 0. 结论

- **之前的文档写的是离线系统**：上传一段完整视频，系统处理完以后才出字幕。它不是实时的。
- **实时系统建议做成“句级实时”**：说话人说完一句、停顿一下，大约 2–4 秒后屏幕上出现这一句的译文（估计值）。这相当于交替传译。“边说边出译文”的同声传译在课程时间内做不了，理由见 §2。
- **多模态的作用可以实时地展示出来**：每句先出“只看文字”的快速译文，1–2 秒后被“看了画面、听了声音”的译文替换。如果两者不同，就高亮标出“声音或画面改变了译文”。
- **离线版写的东西大部分能直接复用**：语音识别、提示词、调用 Qwen2.5-Omni、规则检查都用得上。要新写的是：实时采集、停顿检测、滚动缓存、两阶段字幕、延迟记录。
- **工期**：在离线核心的基础上，再加约 1 天做“模拟直播”（必须），再加约半天到 1 天做“摄像头直播”（最好有）。

---

## 1. 离线和实时有什么不同

| 环节 | 离线版（现有文档） | 实时版 |
|---|---|---|
| 输入 | 上传完整的视频文件 | 摄像头和麦克风的连续数据流（或者按真实速度播放的视频文件） |
| 切句 | 先识别整段，再按标点和停顿切句 | 停顿检测（VAD）一发现“这句说完了”就立刻切出来 |
| 画面窗口 | 句子前 3 秒到句子后 1 秒 | 句子前 3 秒到句子结束（看不到“以后”的画面） |
| 声音 | 从整段音轨里截取并门控 | 停顿检测切出的这一句，本身就只有这一句的声音 |
| 前文 | 前 3 句（事先都知道） | 这次会话里已经说过的前 3 句 |
| 翻译条件 | 四种条件都跑，做对比表 | 现场只跑两种：只看文字（快）和全给；四种条件的对比在会话结束后用录下的数据补跑 |
| 媒体怎么传给模型 | 画面和声音封装进一个 mp4（原生交错） | 帧图片列表加音频文件分开传，省掉编码时间；离线补跑时再用原生交错 |
| 结果 | 字幕文件、对比页 | 屏幕上滚动的实时字幕，加上会话记录、延迟统计；会话结束后也能导出字幕 |
| 主要难点 | 显存、输出格式 | 延迟、积压、浏览器权限、网络 |

---

## 2. 实时有三个层级，建议做第二级

| 层级 | 效果 | 难度 | 建议 |
|---|---|---|---|
| L0 离线 | 处理完整个视频后再看 | 低 | 已经有了，作为基础 |
| **L1 句级实时** | 说完一句，约 2–4 秒后出译文 | 中 | **做这个** |
| L2 同声传译 | 一边说一边出译文，一句话还没说完就开始译 | 很高 | 不做 |

**为什么中日翻译适合句级实时，不适合同传**：

- **日→中**：日语的谓语在句末，否定、疑问、礼貌形式都在最后几个音节才出现，例如「行きます」「行きません」「行きますか」。没听到句末就开译，很可能译反。
- **中→日**：日语要先决定用敬体还是常体、是不是疑问句，这些取决于整句的语气和句末的语调。
- **语调本身就在句末**：本项目最关心的“句尾上扬表示提问”，只有一句话说完才能听到。

所以，等一句说完再翻译，对这个研究问题来说是必要的，不只是图省事。这一点可以写进报告。

---

## 3. 架构

```text
 浏览器（摄像头 + 麦克风）  或  视频文件按真实速度播放（模拟直播）
          │  音频：16 kHz，每块 32 毫秒      画面：每秒 2 帧，缩到 448 像素
          ▼
 ┌──────────────── 服务端（GPU 机器）────────────────┐
 │ 滚动缓存：保存最近 20 秒的音频和画面（带时间戳）     │
 │ 停顿检测（Silero VAD）：发现一句开始、一句结束        │
 │        │ 一句结束                                     │
 │        ▼                                              │
 │ 任务队列 → 工作线程：                                 │
 │   ① 取这一句的音频；取句前 3 秒到句末的画面（最多 12 帧）│
 │   ② 语音识别 → 先把原文推到屏幕                        │
 │   ③ 第一遍：只看文字翻译（快）→ 推到屏幕                │
 │   ④ 第二遍：画面 + 声音 + 文字翻译 → 替换第一遍；不同就高亮 │
 │   ⑤ 记录各步的时间、文件、结果 → 会话日志              │
 │   ⑥（可选）按语气合成译文语音 → 浏览器播放；积压就跳过  │
 └──────────────────────────────────────────────────────┘
          ▼
 浏览器：滚动字幕（原文 / 只看文字 / 多模态 / 是否改变 / 延迟）
 会话结束：导出 SRT；用日志离线补跑四种条件的对比表
```

**三种输入方式**（同一个服务端核心，只换输入）：

| 输入方式 | 做法 | 优点 | 缺点 | 优先级 |
|---|---|---|---|---|
| 模拟直播 | 读取本地视频，按真实速度推送音频和画面 | 不需要浏览器授权，结果可复现，能提前跑好 | 不是真人现场 | **必须** |
| 摄像头直播 | 浏览器采集摄像头和麦克风，推到服务端（FastRTC 或 Gradio 的流式组件） | 现场效果最好 | 浏览器权限、网络、延迟都可能出问题 | 最好有 |
| 云端实时接口 | 阿里云百炼的 Qwen-Omni-Realtime：WebSocket 推送音频和画面帧，服务端自动做停顿检测 | 不需要本地 GPU，延迟可能更低 | 要 API key 和费用，画面会上传；无法严格控制对照条件 | 备选 |

---

## 4. 延迟预算（估计值，必须实测）

延迟从“说话人停止说话”算起，到“译文出现在屏幕上”为止。

| 步骤 | 估计 | 怎么缩短 |
|---|---|---|
| 停顿检测等待 | 0.5–0.7 秒（判定“说完了”所需的静音时长） | 调小 `min_silence_ms`，但太小会把一句切成两句 |
| 语音识别 | SenseVoice-Small 不到 0.2 秒（官方称 10 秒音频约 70 毫秒）；faster-whisper 约 0.3–1 秒 | 用 SenseVoice-Small，或 Whisper 的 turbo 版 |
| 第一遍：只看文字 | 约 0.3–1 秒（没有画面和声音，输入很短） | 限制输出长度 |
| 第二遍：多模态 | 约 1–3 秒（12 帧 + 几秒音频） | 减少帧数、降低分辨率、用量化版 |
| 传输和显示 | 0.1–0.3 秒 | — |
| 第三阶段：语音合成（可选） | 约 0.5–2 秒（CosyVoice，估计；官方称流式首包约 150 毫秒） | 流式输出；积压就跳过语音 |
| **原文出现** | 约 1 秒 | — |
| **快速译文出现** | 约 1.5–2 秒 | — |
| **多模态译文出现** | 约 2.5–4.5 秒 | — |
| **译文语音开始播放**（可选） | 多模态译文出现后再过约 0.5–2 秒 | — |

**积压问题**：如果多模态那一遍比说话还慢，任务会越排越长，字幕越来越落后。处理方法：

- 队列里积压超过 `realtime.queue_max` 句时，跳过积压句的第二遍，只保留快速译文，并在日志里记录跳过的句子。
- 或者降低帧数和分辨率、换量化版模型。

报告里要写实测的延迟中位数和 90 分位数，以及跳过率。

---

## 5. 两阶段字幕：把多模态的作用实时地展示出来

每句话在屏幕上依次经历四个状态：

| 状态 | 屏幕上显示 |
|---|---|
| ① 正在说 | “……”（检测到有人在说话） |
| ② 识别完成 | 原文 |
| ③ 快速译文 | 只看文字的译文，灰色 |
| ④ 多模态译文 | 看了画面、听了声音的译文，黑色。如果和③不同，标“★ 声音/画面改变了译文”，并显示③的内容作对比 |

- 这就是在实时场景下的“只看文字 vs 全给”对照：同一个模型、同一句话，两遍只差输入。
- **对比模式下，两遍都用句末问号换成句号的文字**，否则识别出的问号会让文字版也知道是问句。
- **正式字幕模式下，两遍都用识别出的原文**，目标是译得最好。
- 两种模式不能混着比较。
- 先感知后翻译模式也可以放进第④步：在旁边小字显示“模型观察：句尾上扬；说话人摆手”，并标“未核实”。
- **语气**（`docs/tone_preservation.md`）：引导和先感知后翻译模式下，第④步的译文保留语气，旁边显示语气标签（未核实）。对比模式不加语气要求。加了语气要求时，两遍要用同样的提示词，★ 才只反映输入的差别。
- **语音**（可选）：第④步之后，按语气标签合成这一句的译文语音并播放，作为第三阶段。语音单独排队，积压就跳过，不能拖慢字幕。显存只有 24–32 GB 时，实时模式不开语音。

---

## 6. 参考实现

### 6.1 滚动缓存

```python
import collections
import numpy as np

class RingBuffer:
    """保存最近 seconds 秒的音频采样和画面帧，时间单位是秒（会话开始为 0）。"""
    def __init__(self, seconds=20.0, sr=16000):
        self.sr, self.seconds = sr, seconds
        self.audio = collections.deque()    # (t0, np.ndarray[float32])
        self.frames = collections.deque()   # (t, 图像，numpy BGR 或 PIL)

    def add_audio(self, t0, chunk):
        self.audio.append((t0, chunk))
        while self.audio and self.audio[0][0] < t0 - self.seconds:
            self.audio.popleft()

    def add_frame(self, t, img):
        self.frames.append((t, img))
        while self.frames and self.frames[0][0] < t - self.seconds:
            self.frames.popleft()

    def audio_between(self, t0, t1):
        parts = []
        for start, chunk in self.audio:
            end = start + len(chunk) / self.sr
            if end <= t0 or start >= t1:
                continue
            a = max(0, int((t0 - start) * self.sr))
            b = min(len(chunk), int((t1 - start) * self.sr))
            parts.append(chunk[a:b])
        return np.concatenate(parts) if parts else np.zeros(0, dtype="float32")

    def frames_between(self, t0, t1, max_frames=12):
        sel = [(t, f) for t, f in self.frames if t0 <= t <= t1]
        if len(sel) > max_frames:                      # 均匀抽取，保留时间顺序
            idx = np.linspace(0, len(sel) - 1, max_frames).round().astype(int)
            sel = [sel[i] for i in idx]
        return sel
```

### 6.2 停顿检测

```python
from silero_vad import load_silero_vad, VADIterator   # pip install silero-vad

vad_model = load_silero_vad()
vad = VADIterator(vad_model, sampling_rate=16000, threshold=0.5,
                  min_silence_duration_ms=600, speech_pad_ms=100)

def feed_vad(t0, chunk, on_start, on_end):
    """chunk 必须正好 512 个采样（16 kHz）；不足时补零。"""
    if len(chunk) < 512:
        chunk = np.pad(chunk, (0, 512 - len(chunk)))
    ev = vad(chunk, return_seconds=True)               # 返回 None、{'start': 秒} 或 {'end': 秒}
    if ev and "start" in ev:
        on_start(ev["start"])
    if ev and "end" in ev:
        on_end(ev["end"])
```

另外要做：一句话超过 `realtime.max_utt_sec` 秒还没停顿，就强制切一句，避免一直等。

### 6.3 一句话的处理（工作线程）

```python
import os, queue, threading, time
import cv2
import soundfile as sf

jobs = queue.Queue()

def on_utterance_end(t_start, t_end, wall_end):
    jobs.put((t_start, t_end, wall_end))

def worker(buf, session, direction, mode, push):
    """push(dict) 把更新推到网页；session 保存前文和日志。"""
    while True:
        t_start, t_end, wall_end = jobs.get()
        uid = session.next_id()
        rec = {"id": uid, "start": t_start, "end": t_end, "lat": {}}
        d = os.path.join(session.dir, f"u{uid:04d}")
        os.makedirs(d, exist_ok=True)
        # ① 取这一句的声音和画面：声音只有这一句；画面取句前 3 秒到句末
        wav = os.path.join(d, "a.wav")
        sf.write(wav, buf.audio_between(t_start, t_end), 16000)
        frames = []
        for i, (t, img) in enumerate(buf.frames_between(t_start - 3.0, t_end, max_frames=12)):
            p = os.path.join(d, f"f{i:02d}.jpg")
            cv2.imwrite(p, img)
            frames.append(p)
        # ② 语音识别，原文先上屏
        rec["text_asr"] = asr_once(wav, "ja" if direction == "ja2zh" else "zh")
        rec["lat"]["asr"] = time.time() - wall_end
        push({"id": uid, "src": rec["text_asr"]})
        text = neutralize(rec["text_asr"]) if mode == "compare" else rec["text_asr"]
        ctx = session.context(3)
        # ③ 第一遍：只看文字
        rec["fast"] = translate_live(direction, text, ctx, frames=None, wav=None)
        rec["lat"]["fast"] = time.time() - wall_end
        push({"id": uid, "fast": rec["fast"]})
        # ④ 第二遍：画面 + 声音；积压太多就跳过
        if jobs.qsize() > session.queue_max:
            rec["mm"], rec["skipped"] = None, True
        else:
            rec["mm"] = translate_live(direction, text, ctx, frames=frames, wav=wav)
            rec["lat"]["mm"] = time.time() - wall_end
            rec["changed"] = rec["mm"] != rec["fast"]
            push({"id": uid, "mm": rec["mm"], "changed": rec["changed"]})
        # ⑤ 记录
        session.log(rec)
        session.add_context(rec["text_asr"])
```

`translate_live` 复用离线版 `omni_generate`，只是视频项换成帧图片列表：

```python
def translate_live(direction, text, context, frames=None, wav=None):
    cond = "T+V+A" if frames else "T"
    user = build_user(direction, text, context, cond, mode="compare")   # 离线版第 7 步的函数
    if frames:
        # 帧图片列表作为视频输入（qwen-omni-utils 支持“预先抽好的帧”，以所装版本为准）
        content_video = [p if p.startswith("file://") else "file://" + os.path.abspath(p) for p in frames]
        return omni_generate(SYSTEM[direction], user, video=content_video, audio=wav,
                             use_audio_in_video=False, fps=2.0, max_pixels=448 * 448)
    return omni_generate(SYSTEM[direction], user)
```

实时模式下，画面和声音是分开传入的：现场来不及把它们封装成一个 mp4。这一点要在报告里写明；会话结束后的离线补跑可以改用原生交错方式。

### 6.4 模拟直播：把视频文件当成实时数据流

```python
def simulate_live(video_path, wav16k, on_audio, on_frame, frame_fps=2.0, max_side=448):
    """按真实速度推送：音频每 512 个采样一块（32 毫秒），画面每 0.5 秒一帧。"""
    y, sr = sf.read(wav16k, dtype="float32")
    cap = cv2.VideoCapture(video_path)
    t_wall0 = time.time()
    next_t = 0.0
    for i in range(0, len(y), 512):
        t = i / sr
        on_audio(t, y[i:i + 512])
        while next_t <= t:
            cap.set(cv2.CAP_PROP_POS_MSEC, next_t * 1000)
            ok, img = cap.read()
            if ok:
                h, w = img.shape[:2]
                s = max_side / max(h, w)
                on_frame(next_t, cv2.resize(img, (int(w * s), int(h * s))) if s < 1 else img)
            next_t += 1.0 / frame_fps
        delay = t_wall0 + t - time.time()
        if delay > 0:
            time.sleep(delay)
```

- `on_audio` 里同时调用 `buf.add_audio` 和 `feed_vad`；`on_frame` 调用 `buf.add_frame`。
- 网页上一边播放这段视频，一边显示滚动字幕，效果和直播一样。
- 这个方式结果可复现，可以提前跑好录屏。

### 6.5 摄像头直播（最好有）

推荐两种做法，都以所装版本的官方文档为准：

| 做法 | 说明 |
|---|---|
| FastRTC | Gradio 团队的实时流库，支持音频、视频和音视频（`modality="audio-video"`），可以走 WebRTC 或 WebSocket；自带的 `ReplyOnPause` 能做停顿检测（需要装 `fastrtc[vad]`）。在服务器上跑 WebRTC 可能需要 TURN 服务器 |
| Gradio 流式组件 | 麦克风和摄像头组件开启 `streaming=True`，用 `.stream()` 事件按固定间隔把数据送到服务端。走 HTTP 或 WebSocket，比 WebRTC 容易穿过 SSH 隧道 |

**两个容易卡住的地方**：

1. **浏览器只在安全环境下允许用摄像头和麦克风**：页面必须是 https，或者 `localhost`。
   - 在 AutoDL 上最简单的做法是 SSH 端口转发：`ssh -L 6006:127.0.0.1:6006 <登录地址>`，然后在自己电脑上打开 `http://localhost:6006`。
   - 这条隧道对 HTTP 和 WebSocket 有效；对 WebRTC 的媒体流不一定有效。
2. **不要把 30 帧每秒的原始视频全推上去**：浏览器端或服务端先降到每秒 2 帧、长边 448 像素。

### 6.6 云端实时接口（备选）

阿里云百炼的 Qwen-Omni-Realtime 是 WebSocket 接口。根据官方文档：

- 可以推送流式音频，以及从视频流里截取的图片帧；
- 开启服务端停顿检测（server_vad）后，一句话说完会自动触发回复；
- 单次会话最长 120 分钟。

做法：

1. 在会话设置里写明“你是日语到中文的口译员，只输出译文”，并且只要文字输出。
2. 持续推送麦克风音频，每秒推 1–2 帧画面。
3. 收到文字结果就显示出来。

需要注意：

- 模型名在不同文档页上不一致（例如 qwen3.8-omni-flash-realtime、qwen3.5-omni-plus-realtime），以百炼控制台为准。
- 音频格式、帧率限制要查 API 参考。
- 会产生费用，画面会上传到云端，用自录视频要征得出镜者同意。
- 它是黑盒，没法像本地模型那样严格控制对照条件。只能通过“推不推画面帧”粗略比较。

---

## 7. 会话日志与事后分析

每次会话的逐句记录和字幕保存在 `results/<运行编号>/session/`。

| 文件 | 内容 |
|---|---|
| `audio/L001.wav`、`frames/*.jpg` | 供句级识别或翻译复核的音频与画面 |
| `session.jsonl` | 每句的时间、原文、快译、可用的音画修订、跳过原因。 `latency_ms` 从媒体句末计到服务侧事件，`stage_ms` 记录各处理阶段 |
| `latency_summary.csv` | 原文、快译与修订的端到端中位数和 P90。新增 `stage_*` 与 `startup_*` 行 |
| `subtitles.srt`、`subtitles_quick.srt` | 最终双语字幕与纯文字快译字幕。跳过修订的句子在最终字幕中沿用快译 |

`stage_vad_wait` 从标定的句末按音频采样计到切句时已读到的位置。强制切句或输入结束时，这个值可能为零。 `stage_queue_wait` 从入队计到工作线程开始处理，包括摄像头自动语向时等待语种确认的时间。 `stage_asr_inference` 计语音识别调用。 `stage_quick_translation`、`stage_observation`、`stage_multimodal_translation` 取相应后端请求的耗时，命中缓存时取读缓存的用时。各阶段中位数使用各自有记录的句子，端到端延迟另包含输入节奏及其它处理。

播放前的媒体准备、文件语种探测、ASR 预热及翻译模型预热记录在 `manifest.json` 的 `startup_ms`，也写入 CSV。两阶段字幕的端到端延迟从会话开始喂入音视频才计时。回放页支持较早的三项延迟记录。

会话结束后，可以用离线版第 7 步，对日志里的每句补跑四种条件（只看文字、加声音、加画面、全给），生成和离线版一样的对比表。这样实时系统也保留了研究对照。

---

## 8. 网页布局建议

```text
┌──────────────────────────┬───────────────────────────────────────┐
│ 摄像头画面或正在播放的视频  │ 滚动字幕（最新的在最下面）              │
│                          │ 12.3s  いいよ。                         │
│ [开始] [停止] 方向 ja→zh   │   只看文字：好啊。（灰色）               │
│ 模式：对比 / 正式字幕       │   多模态：不用了。★ 声音/画面改变了译文   │
│                          │   模型观察（未核实）：说话人摆手、转身     │
│                          │   语气（未核实）：不耐烦（强）  ▶ 译文语音 │
│ 延迟：识别 0.9s  多模态 3.1s │                                       │
└──────────────────────────┴───────────────────────────────────────┘
```

---

## 9. 测试

| 测试 | 怎么做 |
|---|---|
| 滚动缓存 | 合成数据：按时间写入音频块和帧，检查 `audio_between`、`frames_between` 取出的时长和帧数 |
| 停顿检测 | 合成“0.5 秒正弦 + 1 秒静音 + 0.8 秒正弦”的信号，应该检测出两句（Silero 对纯正弦不一定判为语音，最好用一小段真人录音做测试素材） |
| 强制切句 | 连续 15 秒有声，应该在 `max_utt_sec` 处切开 |
| 工作线程 | 用假的识别和翻译函数，检查推送顺序是原文 → 快速译文 → 多模态译文；积压时跳过第二遍并记录 |
| 模拟直播 | 一段 20 秒的视频按真实速度推送，总耗时约 20 秒；会话日志的句数与人工数的一致 |
| 延迟统计 | 用假的耗时，检查中位数和 90 分位数的计算 |

---

## 10. 风险与备用方案

| 风险 | 对策 |
|---|---|
| 多模态那一遍太慢，字幕越来越落后 | 减帧数、降分辨率、用量化版；积压时跳过第二遍；演示时说话放慢、句间多停一下 |
| 停顿检测把一句切成两句，或者两句连成一句 | 调 `min_silence_ms`；设最长句时长；演示时句间清楚地停顿 |
| 浏览器打不开摄像头 | 用 localhost（SSH 端口转发）或 https；不行就用模拟直播 |
| WebRTC 连不上 | 改用 WebSocket 或 Gradio 流式组件；还不行就用模拟直播 |
| 识别把短句识别错（例如「いいよ」） | 演示时说清楚、离麦克风近；页面先显示原文，错了也一目了然 |
| 现场网络不稳定 | 课前用模拟直播跑好，录屏备份；现场直播只作为加分展示 |
| 语音合成拖慢字幕，或者显存不够 | 语音放在字幕之后单独排队，积压就跳过；24–32 GB 的卡上实时模式不开语音，只做离线配音 |

---

## 11. 对计划的影响

| 文件 | 改动 |
|---|---|
| `GOALS.md` | 新增 M2R 实时模式：M2R-a 模拟直播（必须）、M2R-b 摄像头直播（最好有）、M2R-c 云端接口（备选）；交差路线改为约 3 天 |
| `AGENTS.md` | 新增 §7.13 实时模式的规则；交付物加 D8 |
| `configs/mvp.yaml` | 新增 `realtime.*` |
| `docs/tone_preservation.md` | 第④步保留语气；可选的第三阶段语音合成（`tts.*`，`tts.realtime_play`） |

离线版的 M1（语音识别、切片段、调用模型）是实时版的基础，不能跳过。M2 的四条件对比可以简化：现场用两阶段字幕展示，四条件对比在会话结束后补跑。
