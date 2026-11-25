# 多模态翻译功能构建指南（交差版）

> 2026-11-24 | 目标：用大约 2 天做出一个能演示的“多模态字幕翻译”功能，**不建数据集**。  
> **这份文档写的是离线部分**：上传完整视频，处理完再出字幕。实时翻译见 `docs/realtime_design.md`，它复用本文的第 2–9 步。  
> 文中的代码是参考实现，**没有在本环境运行过**（这里没有 GPU，也下载不了模型）。在 GPU 机器上第一次运行时要逐段验证；参数名以实际安装的版本为准。

---

## 0. 结论

- **做什么**：上传一段日语或中文视频，自动生成双语字幕。每一句同时给出“只看文字”和“看了画面、听了声音”两种译文，不同的地方高亮显示。
- **用什么**：
  - faster-whisper：识别语音，给出每句的起止时间；
  - Qwen2.5-Omni：一个模型同时看画面、听声音、读文字，负责翻译；
  - Gradio：做网页界面。
- **为什么够交差**：
  1. 功能完整：输入视频，输出字幕；
  2. 多模态的作用直接看得见：有逐句对比视图；
  3. 有对照：同一个模型，只换输入；
  4. 有可控的演示：用自录的最小对视频。
- **工期**：核心功能约 1.5–2 天，不含下载等待。加分项“WMT26 小规模实验”再加 0.5–1 天。
- **最大的风险**：模型不一定会根据声音和画面改译文。对策有两个：一是多准备两种提示词模式（引导、先感知后翻译）；二是演示时成功和失败的例子都展示，如实说明。

---

## 1. 功能定义

### 1.1 用户怎么用

1. 打开网页，上传视频（建议 2 分钟以内），选方向：日→中或中→日。
2. 选模式：
   - **对比**：四种输入各译一遍，看差别；
   - **引导翻译**：给出最好的一版字幕；
   - **先感知后翻译**：先让模型说出它看到、听到了什么，再翻译。
3. 点“开始”，等待进度条走完。
4. 看结果：
   - 逐句表格：时间、原文、各条件的译文、差异标记、模型的观察；
   - 下载 SRT 字幕；
   - 可选：带字幕的预览视频。

### 1.2 输入和输出

| | 内容 |
|---|---|
| 输入 | 视频文件（mp4 等）；方向；模式 |
| 输出 | 逐句结果（JSON）、双语 SRT、对比网页、可选的带字幕预览视频 |
| 不做 | 语音合成配音；长视频（超过 2 分钟的先切短）；数据集构建 |

---

## 2. 三条实现路线

| 路线 | 做法 | 需要的模型 | 工作量 | 能看出多模态的作用吗 | 对应课程汇报 |
|---|---|---|---|---|---|
| 级联 | 识别文字 → 测音高写出“[句尾上扬]” → 图文模型写出“[对象：老师]” → 文本模型带着这些标签翻译 | Whisper + Praat + 图文模型 + 文本大模型 | 大 | 能，而且每一步可以检查 | C 组 |
| **端到端（推荐，必须做）** | 识别出的文字，加上片段的画面和声音，一起交给全模态模型 | Whisper + Qwen2.5-Omni | **小** | 能，用对比视图看 | E 组 |
| 先感知后翻译（推荐，最好有） | 同一个全模态模型先回答四个问题（语调、动作表情、说话对象、语气），再带着答案翻译 | Whisper + Qwen2.5-Omni | 小（多一次调用） | 能，并且能显示模型看到、听到了什么 | 介于 C 和 E 之间 |

**建议**：

- 端到端是主线，一定要做。
- 先感知后翻译只多一次调用，演示效果很直观（“模型听出句尾上扬，所以译成了问句”），时间够就做。
- 级联要多装两套工具，交差版不做。如果想和汇报对应，可以只做“测句尾音高”这一项。

---

## 3. 关键技术决策

### 3.1 为什么还需要 Whisper

Qwen2.5-Omni 听得懂语音，但不给时间戳。字幕需要每句的起止时间，切片段也要用到这些时间。所以分工是：

- **Whisper 负责“什么时候说了什么”**：时间戳和原文；
- **Omni 负责“怎么译”**。

### 3.2 按句翻译，不按整段翻译

- **做法**：每句单独翻译，附上前 3 句的文字作为上下文，画面取这句话开始前 3 秒到结束后 1 秒。
- **理由**：
  - 字幕要逐句对齐；
  - 对比视图要逐句比较；
  - 整段翻译时，输出的行数经常和原文对不上。
- 整段模式只在 WMT 实验（加分项）里用。

### 3.3 四种输入条件，以及怎样交给模型

| 条件 | 文字 | 给模型的媒体 | `use_audio_in_video` | 回答什么 |
|---|---|---|---|---|
| `T` 只看文字 | 识别出的文字，句末问号换成句号 | 无 | False | 基线 |
| `T+A` 加声音 | 同上 | 门控音频（wav） | False | 声音有没有用 |
| `T+V` 加画面 | 同上 | 无声片段（mp4，没有音轨） | False | 画面有没有用 |
| `T+V+A` 全给 | 同上 | 画面加门控音频，封装成一个 mp4 | **True** | 两者一起 |

三个要点：

1. **为什么要把问号换成句号**：识别模型加的问号是它“听”出来的。留着问号，模型只看文字也知道是问句，声音的作用就看不出来了。对比模式用换掉问号的文字；引导翻译模式可以用原文字，目标是译得最好。
2. **为什么要门控音频**：片段的画面比这句话长，如果声音也一样长，模型会听到别人接下来说的话。所以声音只保留这一句前后 0.2 秒，其余静音。
3. **为什么“全给”要封装成一个 mp4**：Qwen2.5-Omni 会把每 2 秒的画面和同一时段的声音交错排在一起处理（课程汇报第 15 页）。封装进同一个 mp4 并开启 `use_audio_in_video=True`，才是它原生的用法。

### 3.4 三种提示词模式

| 模式 | 提示词 | 用途 | 注意 |
|---|---|---|---|
| `compare` 对比 | 各条件只差一句“请参考随附的语音/画面” | 对比视图，看多模态有没有作用 | 必须完全一致，否则比较不公平 |
| `guided` 引导 | 在对比模式的提示词后加一句“请特别注意说话人的语调是否表示提问、动作表情表示同意还是拒绝、说话对象是否需要敬语” | 生成最终字幕 | 要比较的话，也要在引导模式下比较“只看文字”和“全给” |
| `perceive` 先感知后翻译 | 第一次调用只回答四个问题（第 4 题是语气，见 `docs/tone_preservation.md`）；第二次带着答案翻译，并保留语气 | 演示“模型看到、听到了什么” | 模型的回答可能是错的，页面上标“模型观察，未核实” |

### 3.5 选哪个模型

| 选项 | 显存（官方数字或估计） | 什么时候用 |
|---|---|---|
| Qwen2.5-Omni-7B，BF16 | 官方：15 秒视频理论最低 31.11 GB，实际约 ×1.2。单句片段（4–8 秒）会更少，具体以实测为准 | 有 40 GB 以上的卡（例如 48 GB、80 GB） |
| Qwen2.5-Omni-7B-AWQ 或 -GPTQ-Int4 | 官方称显存减少一半以上；GPTQ 版速度稍慢 | 只有 24 GB 的卡（例如 4090） |
| Qwen2.5-Omni-3B | 官方：15 秒视频 18.38 GB | 开发调试；显存很紧时 |
| API（例如阿里云百炼的 Qwen-Omni） | 不需要 GPU | 完全没有 GPU 时；要求流式调用；会上传视频，自录视频要征得出镜者同意 |

同一次演示、同一张对比表里只能用一个模型。

### 3.6 怎样证明多模态起了作用

1. **对比视图**：每句看“只看文字”和“全给”有没有不同，不同在哪里（是不是问句、礼貌程度、意思）。
2. **自录最小对**：同一句话录两版，只改动作或语调（见 `docs/HUMAN_TASKS.md` H2）。“只看文字”时两版的输入完全一样，译文必然相同；“全给”时如果两版译文不同、而且各自对上了预期，就是多模态起作用的直接证据。
3. **（加分项）WMT26 小规模实验**：在真实视频上给出数字（§10）。

---

## 4. 分步实现

目录约定：每段视频的中间文件放在 `work/<视频名>/`，结果放在 `results/<运行编号>/`。

### 第 1 步：环境（以 AutoDL 为例）

```bash
# 选 PyTorch 2.x + CUDA 12.x 的镜像。访问 GitHub 或 Hugging Face 慢时，先开 AutoDL 的学术资源加速（以 AutoDL 文档为准）
apt-get update && apt-get install -y ffmpeg
pip install accelerate "qwen-omni-utils[decord]" faster-whisper soundfile librosa gradio pandas pyyaml
pip install -U transformers      # 版本按 Qwen2.5-Omni 模型卡的要求
python -c "from transformers import Qwen2_5OmniForConditionalGeneration; print('ok')"   # 能打印 ok 才说明版本对
pip install modelscope
modelscope download --model Qwen/Qwen2.5-Omni-7B --local_dir /root/autodl-tmp/models/Qwen2.5-Omni-7B
```

- 模型权重很大，**第一天一开始就下载**。
- **RTX 50 系（例如 RTX 5090）**：镜像必须是 PyTorch 2.7 以上、CUDA 12.8 的；PyTorch 2.5.1 + CUDA 12.4 的镜像在 50 系上用不了 GPU。flash-attn 先不装，`attn_implementation` 用 `sdpa`。详见 `docs/environment.md` §10。
- **decord 装不上**（Python 3.12 的镜像上可能遇到）：改装 `qwen-omni-utils` 和 `av`，让它用 torchvision 读视频。
- 系统盘只有 30 GB 左右时，把缓存都放到数据盘：`export HF_HOME=/root/autodl-tmp/hf MODELSCOPE_CACHE=/root/autodl-tmp/modelscope PIP_CACHE_DIR=/root/autodl-tmp/pip-cache`。
- faster-whisper 的模型从 Hugging Face 下载，访问不了时设置镜像（`HF_ENDPOINT`），或者在自己电脑上下载后上传。
- flash-attn 可选，装不上就用默认的注意力实现。

### 第 2 步：读视频、抽音轨

```bash
ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 input.mp4      # 时长（秒）
ffmpeg -y -i input.mp4 -vn -ac 1 -ar 16000 work/demo/audio16k.wav                     # 16 kHz 单声道音轨
```

### 第 3 步：语音识别

```python
from faster_whisper import WhisperModel

asr = WhisperModel("large-v3", device="cuda", compute_type="float16")   # 用 CPU 调试时改成 "small"、"cpu"、"int8"

def transcribe(wav_path, lang):
    """返回词级结果：[{start, end, text, suspect}]，时间单位是秒。"""
    kwargs = dict(language=lang, beam_size=5, vad_filter=True,
                  word_timestamps=True, condition_on_previous_text=False)
    if lang == "zh":
        kwargs["initial_prompt"] = "以下是普通话的句子。"   # 让输出偏向简体中文
    segments, info = asr.transcribe(wav_path, **kwargs)
    words = []
    for seg in segments:                 # segments 是生成器，遍历时才真正开始识别
        suspect = seg.compression_ratio > 2.4 or (seg.avg_logprob < -1.0 and seg.no_speech_prob > 0.6)
        for w in (seg.words or []):
            words.append({"start": w.start, "end": w.end, "text": w.word, "suspect": suspect})
    return words
```

`suspect` 标出可能是幻觉的段落，例如静音时冒出来的「ご視聴ありがとうございました」。这些句子保留，但在页面上标出来。

**备选后端（RTX 50 系上 faster-whisper 报错时）**：社区报告 faster-whisper（CTranslate2）在 RTX 5090 上出现 `CUBLAS_STATUS_NOT_SUPPORTED`。先确认用的是 `compute_type="float16"`；还不行就改用 transformers 版 Whisper（PyTorch 实现，PyTorch 认得这张卡就能用），输出格式和 `transcribe` 一样，`asr.backend` 记为 `hf-whisper`：

```python
import torch
from transformers import pipeline

asr_hf = pipeline("automatic-speech-recognition", model="openai/whisper-large-v3-turbo",
                  torch_dtype=torch.float16, device="cuda:0")

def transcribe_hf(wav_path, lang):
    """和 transcribe 一样返回 [{start, end, text, suspect}]；这个后端没有幻觉判据，suspect 一律为 False。"""
    out = asr_hf(wav_path, return_timestamps="word", chunk_length_s=30,
                 generate_kwargs={"language": lang, "task": "transcribe"})
    words = []
    for c in out["chunks"]:
        start, end = c["timestamp"]
        words.append({"start": start, "end": end if end is not None else start,
                      "text": c["text"], "suspect": False})
    return words
```

也可以用 SenseVoice-Small（FunASR，PyTorch 实现，很快），或者把 faster-whisper 放到 CPU 上（`device="cpu", compute_type="int8"`，模型用 small 或 medium，离线够用，实时偏慢）。同一次实验、同一张对比表只能用同一个识别后端。

### 第 4 步：切句

```python
import re

FINAL = tuple("。？！?!")

def split_lines(words, pause=0.6, max_chars=40, max_dur=10.0):
    """遇到句末标点、停顿超过 pause 秒、字数或时长超限，就切一句。"""
    lines, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        text = "".join(x["text"] for x in cur).strip()
        gap = words[i + 1]["start"] - w["end"] if i + 1 < len(words) else 999.0
        if (text.endswith(FINAL) or gap >= pause or len(text) >= max_chars
                or cur[-1]["end"] - cur[0]["start"] >= max_dur):
            lines.append({"start": cur[0]["start"], "end": cur[-1]["end"],
                          "text_asr": text, "suspect": any(x["suspect"] for x in cur)})
            cur = []
    return lines

def neutralize(text):
    """把句末的问号、叹号、省略号统一换成句号。对比模式用这个版本。"""
    t = re.sub(r"[？?！!…‥]+", "。", text)
    return re.sub(r"。+", "。", t)
```

中文的 `max_chars` 建议用 30。前文取同一视频里前 3 句的 `text_asr`。

### 第 5 步：片段与门控音频

```python
import subprocess
import numpy as np
import soundfile as sf

def run(cmd, timeout=120):
    subprocess.run(cmd, check=True, timeout=timeout,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def make_clip(src_video, wav16k, line, prefix, duration,
              pre=3.0, post=1.0, max_win=15.0, margin=0.2, fade=0.02, sr=16000):
    ws = max(0.0, line["start"] - pre)
    we = min(duration, line["end"] + post)
    if we - ws > max_win:                       # 太长时先缩短前面的留白
        ws = max(0.0, we - max_win)
    # 1) 无声画面：去掉音轨，高度缩到 480
    run(["ffmpeg", "-y", "-ss", f"{ws:.3f}", "-t", f"{we - ws:.3f}", "-i", src_video,
         "-an", "-c:v", "libx264", "-preset", "veryfast", "-vf", "scale=-2:480", f"{prefix}_v.mp4"])
    # 2) 门控音频：只保留这句话前后 margin 秒，其余置零，边界淡入淡出
    y, sr0 = sf.read(wav16k, dtype="float32")
    assert sr0 == sr
    seg = y[int(ws * sr):int(we * sr)].copy()
    a = max(0, int((line["start"] - margin - ws) * sr))
    b = min(len(seg), int((line["end"] + margin - ws) * sr))
    mask = np.zeros(len(seg), dtype="float32")
    mask[a:b] = 1.0
    f = min(int(fade * sr), max(0, (b - a) // 2))
    if f > 0:
        mask[a:a + f] = np.linspace(0.0, 1.0, f, dtype="float32")
        mask[b - f:b] = np.linspace(1.0, 0.0, f, dtype="float32")
    sf.write(f"{prefix}_a.wav", seg * mask, sr)
    # 3) 画面 + 门控音频封装成一个 mp4，给“全给”条件用
    run(["ffmpeg", "-y", "-i", f"{prefix}_v.mp4", "-i", f"{prefix}_a.wav",
         "-c:v", "copy", "-c:a", "aac", "-shortest", f"{prefix}_av.mp4"])
    return {"win": (ws, we), "v": f"{prefix}_v.mp4", "a": f"{prefix}_a.wav", "av": f"{prefix}_av.mp4"}
```

检查点：
- 用 `ffprobe` 确认 `*_v.mp4` 没有音频流；
- 播放 `*_a.wav`，应该只听得到这一句。

### 第 6 步：调用 Qwen2.5-Omni（核心）

```python
import torch
from transformers import Qwen2_5OmniForConditionalGeneration, Qwen2_5OmniProcessor
from qwen_omni_utils import process_mm_info

MODEL = "/root/autodl-tmp/models/Qwen2.5-Omni-7B"   # 显存不够换成 AWQ / GPTQ-Int4 版或 3B 版
model = Qwen2_5OmniForConditionalGeneration.from_pretrained(
    MODEL, torch_dtype=torch.bfloat16, device_map="auto")   # 装了 flash-attn 可加 attn_implementation="flash_attention_2"
model.disable_talker()            # 只要文字输出；官方 README 说可省约 2 GB 显存
processor = Qwen2_5OmniProcessor.from_pretrained(MODEL)

def omni_generate(system, user_text, video=None, audio=None, use_audio_in_video=False,
                  fps=2.0, max_pixels=448 * 448, max_new_tokens=128):
    content = []
    if video:
        content.append({"type": "video", "video": video, "fps": fps, "max_pixels": max_pixels})
    if audio:
        content.append({"type": "audio", "audio": audio})
    content.append({"type": "text", "text": user_text})
    conv = [{"role": "system", "content": [{"type": "text", "text": system}]},
            {"role": "user", "content": content}]
    text = processor.apply_chat_template(conv, add_generation_prompt=True, tokenize=False)
    audios, images, videos = process_mm_info(conv, use_audio_in_video=use_audio_in_video)
    inputs = processor(text=text, audio=audios, images=images, videos=videos, return_tensors="pt",
                       padding=True, use_audio_in_video=use_audio_in_video)
    inputs = inputs.to(model.device).to(model.dtype)
    with torch.inference_mode():
        out = model.generate(**inputs, use_audio_in_video=use_audio_in_video, return_audio=False,
                             thinker_max_new_tokens=max_new_tokens, thinker_do_sample=False)
    new_tokens = out[:, inputs["input_ids"].shape[1]:]          # 只取新生成的部分
    return processor.batch_decode(new_tokens, skip_special_tokens=True)[0].strip()
```

要点：

- `use_audio_in_video` 在 `process_mm_info`、`processor`、`generate` 三处必须一致（官方 README 的要求）。
- 生成参数前面加了 `thinker_` 前缀，意思是交给负责文字的部分。如果所装版本报“参数重复”或“参数无效”，按该版本的说明改。
- 视频路径用绝对路径。如果读不到，试试加 `file://` 前缀。
- 不需要语音输出时可以用自己的系统提示词。官方的固定系统提示词只在需要语音输出时才必须用。

### 第 7 步：四种条件与缓存

```python
import hashlib, json, os

SYSTEM = {"ja2zh": "你是专业的日语→简体中文口语翻译。只输出译文本身，写成一行，不加引号、解释或备选译法。",
          "zh2ja": "你是专业的中文→日语口语翻译。只输出译文本身，写成一行，不加引号、解释或备选译法。"}
NOTE = {"T": "", "T+A": "请参考随附的语音。", "T+V": "请参考随附的视频画面（无声）。",
        "T+V+A": "请参考随附的视频画面和语音。"}
GUIDE = "请特别注意：说话人的语调是否表示提问，动作和表情表示同意还是拒绝，说话对象是否需要敬语。"

def build_user(direction, text, context, cond, mode):
    src, tgt = ("日语", "简体中文") if direction == "ja2zh" else ("中文", "日语")
    ctx = ("前文：\n" + "\n".join(context) + "\n") if context else ""
    guide = GUIDE if mode == "guided" and cond != "T" else ""
    return f"{ctx}{NOTE[cond]}{guide}请把下面这句{src}翻译成自然的{tgt}口语。只输出译文。\n原文：{text}"

def media_for(cond, clip):
    return {"T": (None, None, False), "T+A": (None, clip["a"], False),
            "T+V": (clip["v"], None, False), "T+V+A": (clip["av"], None, True)}[cond]

def file_hash(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest() if p else ""

def translate(direction, line, context, clip, cond, mode="compare", cache_dir="cache"):
    text = neutralize(line["text_asr"]) if mode == "compare" else line["text_asr"]
    user = build_user(direction, text, context, cond, mode)
    video, audio, av = media_for(cond, clip)
    key = hashlib.sha256(json.dumps([MODEL, SYSTEM[direction], user, file_hash(video), file_hash(audio), av]).encode()).hexdigest()
    path = os.path.join(cache_dir, key + ".json")
    if os.path.exists(path):                      # 缓存：同样的输入不再调用模型
        return json.load(open(path, encoding="utf-8"))["out"]
    out = omni_generate(SYSTEM[direction], user, video=video, audio=audio, use_audio_in_video=av)
    out = out.splitlines()[0].strip().strip("「」\"“”") if out else ""
    os.makedirs(cache_dir, exist_ok=True)
    json.dump({"cond": cond, "user": user, "out": out}, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    return out
```

对比模式下，四个条件的提示词只差 `NOTE` 那一句；引导模式只在加了媒体的条件上加 `GUIDE`。“只看文字”加 `GUIDE` 没有意义，因为它没有声音和画面可以注意。

### 第 8 步：先感知后翻译（最好有）

```python
import re

TONE_LABELS = ["平静", "开心", "兴奋", "亲切", "生气", "不耐烦", "惊讶", "怀疑",
               "讽刺", "撒娇", "犹豫", "难过", "害怕", "不确定"]

PERCEIVE = ("请只根据随附的视频和语音回答，每题一行，不确定就写“不确定”：\n"
            "1. 这句话句尾的语调：上扬 / 下降 / 平；\n"
            "2. 说话人的动作和表情（例如点头、摇头、摆手、微笑、皱眉），没有就写“无”；\n"
            "3. 说话人在对谁说话：长辈或上级 / 平辈或朋友 / 晚辈 / 店员或陌生人 / 不确定；\n"
            "4. 说话人的语气：" + " / ".join(TONE_LABELS) + "；强度：弱 / 中 / 强。")

TONE_NOTE = ("请保留说话人的语气（{tone}，{strength}）：用目标语言里自然的方式表达，例如语气词、句末助词、"
             "感叹号或问号、口语化的说法。不要用括号写出情绪，不要增加原文没有的意思。")

def parse_tone(obs):
    """从第 4 题的回答里取出 (语气, 强度)。一个标签都没有，或者把选项整个抄了一遍，都算“不确定”。"""
    line = next((l for l in obs.splitlines() if l.strip().startswith("4")), "")
    found = [t for t in TONE_LABELS if t in line]
    if len(found) != 1 or found[0] == "不确定":
        return "不确定", ""
    after = line.split("强度", 1)[1] if "强度" in line else ""
    levels = set(re.findall(r"[弱中强]", after))
    return found[0], (levels.pop() if len(levels) == 1 else "")

def perceive_then_translate(direction, line, context, clip):
    obs = omni_generate("你是视频和语音分析助手，只回答问题，不翻译。", PERCEIVE,
                        video=clip["av"], use_audio_in_video=True, max_new_tokens=128)
    tone, strength = parse_tone(obs)
    if tone == "不确定":
        tone_note = "请用自然的语气翻译，不要用括号写出情绪，不要增加原文没有的意思。"
    else:
        tone_note = TONE_NOTE.format(tone=tone, strength=strength or "中")
    src, tgt = ("日语", "简体中文") if direction == "ja2zh" else ("中文", "日语")
    ctx = ("前文：\n" + "\n".join(context) + "\n") if context else ""
    user = (f"{ctx}已知（模型的观察，可能有误）：\n{obs}\n{tone_note}\n"
            f"请参考随附的视频画面和语音，把下面这句{src}翻译成自然的{tgt}口语。只输出译文。\n原文：{line['text_asr']}")
    out = omni_generate(SYSTEM[direction], user, video=clip["av"], use_audio_in_video=True)
    return obs, (tone, strength), out.splitlines()[0].strip() if out else ""
```

页面上把 `obs` 显示在“模型的观察”一栏，把 `(tone, strength)` 显示成语气标签，例如“不耐烦（强）”，都注明“模型输出，未核实”。语气的设计、对照表和评估见 `docs/tone_preservation.md`。

### 第 9 步：差异与规则检查

```python
import re

def is_question_ja(t):
    t = t.strip().rstrip("。")
    return t.endswith(("か", "かい", "かな", "かしら", "？", "?"))

def is_question_zh(t):
    t = t.strip()
    return t.endswith(("？", "?")) or t.rstrip("。").endswith(("吗", "么"))

def politeness_ja(t):
    if re.search(r"(いらっしゃ|おっしゃ|なさ|くださ|召し上が|ご覧にな|お.{1,6}にな)", t):
        return "尊敬"
    if re.search(r"(です|ます|でした|ました|ません|でしょう|ましょう)", t):
        return "敬体"
    return "常体"

def compare_line(direction, outs):
    """outs: {"T": ..., "T+A": ..., ...}；返回每个条件相对“只看文字”的变化。"""
    q = is_question_zh if direction == "ja2zh" else is_question_ja
    base = outs["T"]
    marks = {}
    for cond, o in outs.items():
        if cond == "T":
            continue
        m = []
        if o != base:
            m.append("文字不同")
        if q(o) != q(base):
            m.append("问句↔陈述")
        if direction == "zh2ja" and politeness_ja(o) != politeness_ja(base):
            m.append("礼貌程度")
        marks[cond] = m
    return marks
```

这些是简化规则：只看句末形式和几个关键词，口语里会有误判。要更准，可以把日语礼貌程度改用 fugashi 分词判断。

### 第 10 步：输出字幕

```python
def ts(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def write_srt(lines, cond, path):
    with open(path, "w", encoding="utf-8") as f:
        for i, ln in enumerate(lines, 1):
            f.write(f"{i}\n{ts(ln['start'])} --> {ts(ln['end'])}\n{ln['text_asr']}\n{ln['out'][cond]}\n\n")
```

可选：用 `ffmpeg -i input.mp4 -vf subtitles=out.srt preview.mp4` 生成带字幕的预览。这需要 ffmpeg 带 libass 和中日文字体；做不出来就跳过，只提供 SRT 下载。

### 第 11 步：网页（Gradio）

```python
import gradio as gr
import pandas as pd

def handle(video_path, direction, mode):
    res = run_pipeline(video_path, direction=direction, mode=mode)   # 串起第 2–10 步，返回逐句结果和 SRT 路径
    rows = [{"时间": f"{l['start']:.1f}–{l['end']:.1f}s", "原文": l["text_asr"],
             "只看文字": l["out"].get("T", ""), "加声音": l["out"].get("T+A", ""),
             "加画面": l["out"].get("T+V", ""), "全给": l["out"].get("T+V+A", ""),
             "变化": "；".join(sum(l.get("marks", {}).values(), [])),
             "模型的观察（未核实）": l.get("obs", "")} for l in res["lines"]]
    return pd.DataFrame(rows), res["srt_path"]

with gr.Blocks(title="中日多模态字幕翻译") as demo:
    gr.Markdown("## 中日多模态字幕翻译（课程原型）\n同一个模型，比较“只看文字”和“看画面、听声音”的译文。")
    with gr.Row():
        video = gr.Video(label="上传视频（建议 2 分钟以内）")
        with gr.Column():
            direction = gr.Radio(["ja2zh", "zh2ja"], value="ja2zh", label="方向")
            mode = gr.Radio(["compare", "guided", "perceive"], value="compare", label="模式：对比 / 引导 / 先感知后翻译")
            btn = gr.Button("开始翻译")
    table = gr.Dataframe(label="逐句结果", wrap=True)
    srt = gr.File(label="下载字幕")
    btn.click(handle, inputs=[video, direction, mode], outputs=[table, srt])

demo.launch(server_name="0.0.0.0", server_port=6006)
```

- **在 AutoDL 上打开**：用“自定义服务”对外开放端口（以 AutoDL 文档为准），或者在自己电脑上做 SSH 端口转发：`ssh -L 6006:127.0.0.1:6006 <AutoDL 给的登录地址>`。
- **回放模式（课堂必备）**：`run_pipeline` 先查 `results/` 里有没有这段视频已经算好的结果，有就直接读取，不调用模型。这样课堂上在没有 GPU 的笔记本上也能演示。

### 第 12 步：一条命令跑完

```bash
python -m cjvmt.run --input data/raw/own/pair01_B.mp4 --direction ja2zh --mode compare
```

输出到 `results/<运行编号>/`，包括：
- `lines.json`：逐句结果；
- `<视频名>.srt`；
- `compare.html`：静态对比页，不依赖网络；
- `manifest.json`：模型名、提示词、参数、时间、显存。

---

## 5. 一句话走完整个流程（示意，不是运行结果）

自录视频 `pair01_B.mp4`：说话人一边摆手一边转身，说「いいよ。」，视频长 4 秒。

| 步骤 | 产出 |
|---|---|
| 识别 | 「いい」2.10–2.45 秒、「よ」2.45–2.90 秒 |
| 切句 | 一句：2.10–2.90 秒，「いいよ。」 |
| 片段 | 画面 0–3.9 秒（前 3 秒被视频开头截断）；声音只保留 1.9–3.1 秒 |
| 翻译 | 四个条件各一次。“只看文字”和“全给”的提示词只差一句“请参考随附的视频画面和语音” |
| 理想情况 | “只看文字”译成“好啊。”，“全给”译成“不用了。”，这一行标上“文字不同”。A 版（点头）的“全给”应该译成“好啊。” |
| 页面 | 两版视频并排显示，下面是各条件的译文和变化标记 |

如果模型两版都译成“好啊。”，就说明它没有利用手势。演示时照实展示这个结果，并说明这和已有研究的发现一致。

---

## 6. 最少要做的测试

| 测试 | 怎么做 |
|---|---|
| 句末中性化 | 「行く？」→「行く。」；「本当？！」→「本当。」 |
| 门控音频 | 用合成信号（全程正弦波）测试：门控区间外的采样全为 0 |
| 无声片段 | `ffprobe` 检查没有音频流 |
| 条件隔离 | 用假模型（Mock）记录收到了什么：`T+V` 只收到无声视频，`T+A` 只收到 wav，`T+V+A` 收到 mp4 且 `use_audio_in_video=True` |
| 提示词一致 | 对比模式下，四个条件的提示词去掉模态说明那一句以后完全相同 |
| 缓存 | 第二次运行同一段视频，模型调用次数为 0 |
| 规则检查 | 问句、礼貌程度各写 4 个正例、4 个反例 |
| SRT 格式 | 时间格式正确，序号连续 |

这些测试都不需要 GPU，用假模型就能跑，可以让 Agent 先写好。

---

## 7. 演示与交付物

**课前准备**：
1. 用真实模型把要演示的视频都跑一遍，结果存在 `results/`，课堂上用回放模式。
2. **录一段 2–3 分钟的屏幕录像作为备份**。网页或网络出问题时，直接播放录像。
3. 幻灯片里的画面截图只用自录视频，不用 WMT 的视频。

**交付物清单**：

| 交付物 | 说明 |
|---|---|
| 网页原型 | 上传视频 → 逐句对比 → 下载字幕；带回放模式 |
| 结果文件 | 演示视频的 `lines.json`、SRT、`compare.html` |
| 自录最小对的结果表 | 每对的两个版本 × 四个条件的译文，以及是否符合预期 |
| 简短报告 | 问题、方法（§2–3）、系统（§4）、演示结果、局限、后续计划（数据集建设） |
| README | 怎么安装、怎么运行、怎么打开回放模式 |
| 屏幕录像 | 备份 |

**自录最小对的结果表**（模板，跑完再填，不能先填）：

| 对 | 版本 | 预期 | 只看文字 | 加声音 | 加画面 | 全给 | 全给是否符合预期 |
|---|---|---|---|---|---|---|---|
| pair01 いいよ。 | A 点头 | 接受 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 |
| pair01 いいよ。 | B 摆手 | 谢绝 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 |
| pair02 田中さんも出ます | A 上扬 | 疑问 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 |
| pair02 田中さんも出ます | B 下降 | 陈述 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 |

---

## 8. 时间表（约 2 天）

| 时间 | 做什么 | 结束时能看到 |
|---|---|---|
| 第 1 天上午 | 租 GPU、装环境、**开始下载模型**；你录 3–5 对视频；Agent 用假模型写好第 2–5 步和测试 | 测试通过；视频切成了片段 |
| 第 1 天下午 | 第 6–7 步接上真实模型，先跑“只看文字”，再跑四个条件；第 10 步出 SRT | 一段视频的双语字幕和四条件译文 |
| 第 2 天上午 | 第 9 步差异标记；第 11 步网页加回放模式；第 8 步先感知后翻译（有时间就做） | 能在网页上演示 |
| 第 2 天下午 | 跑完所有演示视频；填结果表；写报告和 README；录屏备份 | 全部交付物 |
| 第 3 天（可选） | WMT26 小规模实验（§10） | 一张有统计检验的结果表 |

---

## 9. 常见问题与排错

| 现象 | 可能原因 | 处理 |
|---|---|---|
| 显存不够（OOM） | 片段太长、帧率或分辨率太高 | 把 `fps` 降到 1、`max_pixels` 降到 224×224；缩短片段；换 AWQ / GPTQ 版或 3B 版 |
| 每句都很慢 | 视频帧太多；没装 flash-attn | 降帧率；先用短视频演示；结果都会缓存 |
| 识别出「ご視聴ありがとうございました」之类的无关句子 | Whisper 在静音段的幻觉 | 打开 `vad_filter`；页面上标出 `suspect`；必要时删掉这一句 |
| 中文识别成了繁体 | Whisper 的常见现象 | 加 `initial_prompt="以下是普通话的句子。"`；或者用 OpenCC 转成简体 |
| 模型输出带解释或引号 | 提示词约束不够 | 只取第一行，去掉引号；在系统提示词里强调“只输出译文” |
| 模型输出的是原文的语言 | 方向没写清楚 | 用户提示词里写明源语言和目标语言；做语言检查 |
| 加了视频，译文完全没变 | 模型没利用画面 | 试引导模式或先感知后翻译；如实展示 |
| `use_audio_in_video` 相关的报错 | 三处取值不一致 | 统一从条件推出这个值 |
| 读不到音频 | 缺 ffmpeg，或者音频解码库有问题 | 确认 `ffmpeg -version` 能运行；先手动转成 wav 再传入 |
| 网页在外面打不开 | 端口没开放 | AutoDL 的自定义服务，或 SSH 端口转发；最后的办法是回放模式加录屏 |
| 带字幕的预览视频做不出来 | ffmpeg 没有 libass，或缺中日文字体 | 跳过，只提供 SRT |
| `no kernel image is available for execution on the device`，或提示显卡的 `sm_120` 不兼容 | 显卡是 RTX 50 系（Blackwell），PyTorch 太旧 | 换 PyTorch 2.7 以上、CUDA 12.8 的镜像（`docs/environment.md` §10） |
| faster-whisper 报 `CUBLAS_STATUS_NOT_SUPPORTED` | CTranslate2 和 RTX 50 系的兼容问题 | 确认 `compute_type="float16"`；不行就换第 3 步的 `transcribe_hf`、SenseVoice-Small，或者放到 CPU 上 |
| faster-whisper 报找不到 `libcudnn` | 找不到 cuDNN 9 的库 | 按 faster-whisper 的说明，把 pip 装的 cuDNN 和 cuBLAS 的目录加进 `LD_LIBRARY_PATH` |
| 装不上 decord | 它多年没有更新，新版 Python 上可能装不上 | 不装它；装 `av`，让 qwen-omni-utils 用 torchvision 读视频 |
| 译文里出现“（生气地）”这类括号说明 | 模型把语气写成了说明 | 检查提示词里有没有“不要用括号写出情绪”；规则检查会标出来 |

---

## 10. 加分项，以及它和创新点的关系

**加分项：WMT26 小规模实验**

- 用固定种子抽 30 段 WMT26 中→日视频，跑 `T`（问号去掉的人工转写）和 `T+V+A` 两个条件，时间够再加 `T+A`。
- 整段翻译，按问句分层（尤其是没有疑问标记、只靠语调的 Q5 类）算问句判对率，用 McNemar 检验比较。
- 方法见 `AGENTS.md` §7.10。工作量约半天，外加约 1 GPU 小时（估计）；还需要先下载约 1.1 GB 的视频。

**取舍**：

| 做不做 | 能讲的创新点（`docs/innovation.md`） | 结论的分量 |
|---|---|---|
| 不做 | 创新点一（研究对象）、四（系统）有演示支撑；二（分层评测）、三（防泄漏对照）只能作为“方法设计”来讲 | 只有定性的演示 |
| 做 30 段子集 | 四条都有东西支撑，二、三有真实数字 | 有定量结果，样本小，置信区间宽 |

**建议**：时间哪怕只多半天，也做 30 段子集。它把“演示”变成了“有数字的初步实验”，老师问创新点和结论时更站得住。

**其他可选项**：

- 级联对照：只加“测句尾音高 → 写成[句尾上扬]”这一项，和课程汇报的级联方法对应。
- 原生交错和分开输入的对比：多跑一个条件 `T+V+A_sep`。
