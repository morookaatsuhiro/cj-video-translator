# 运行环境与显卡配置分析（交差版）

> 2026-11-24 | 适用于交差版：离线主链路（M1）、四条件对比（M2）、句级实时（M2R）、语气保留（M2T），以及加分项 WMT 实验（M3）。  
> 标“官方”的数字来自模型的官方 README 或模型卡；标“估计”的是推算，**必须在租到的机器上实测**（§8），再写进 `reports/env.md`。

---

## 0. 结论

| 方案 | 显卡 | 能跑什么 | 适合谁 |
|---|---|---|---|
| **推荐** | 1 张 48 GB（例如 AutoDL 的 vGPU-48GB、L20、A6000、A40；Ampere 或 Ada 架构） | Qwen2.5-Omni-7B 原版（BF16）和 Whisper large-v3 同时常驻；离线、实时都能跑；WMT 整段降低帧率后也能跑 | 想要最稳、译文质量最好 |
| **省钱** | 1 张 24 GB（例如 RTX 4090、3090） | 7B 的 AWQ 或 GPTQ-Int4 量化版，或者 3B 原版；Whisper 用 int8 或 turbo 版 | 预算有限；质量和速度略差，但能交差 |
| 能用，但要改环境 | 1 张 32 GB 的 RTX 5090（Blackwell） | 7B 原版的单句模式估计放得下，但很紧，要实测；必须换 PyTorch 2.7 以上、CUDA 12.8；WMT 整段放不下 | 速度比 4090 快，但显存比 48 GB 紧，有几个库要换用法（§10） |
| 宽裕 | 1 张 80 GB（例如 A800、H800） | 全部用原版，WMT 整段 30 秒的视频不用降低设置 | 只在跑加分项批量实验时临时租几小时 |
| 不推荐 | V100、T4、RTX 20 系等老卡 | 不支持 BF16，也不支持 FlashAttention-2 | — |
| 没有 GPU | 只有笔记本 | 只能用假模型开发、用回放模式演示；真实运行要租卡或改用云端 API | — |

**一句话**：能租 48 GB 就租 48 GB；只能租 24 GB 就用 7B 量化版。**24 GB 的卡跑不了 7B 原版**，因为光模型权重就有约 22 GB（§1）。

---

## 1. 每个模型要多少显存

| 模型 | 版本 | 权重大小 | 运行时显存 | 说明 |
|---|---|---|---|---|
| Qwen2.5-Omni-7B | BF16 原版 | **约 22.4 GB**（5 个权重文件相加） | 官方理论最低值：15 秒视频 31.11 GB，30 秒 41.85 GB，60 秒 60.19 GB；实际约 ×1.2 | 关闭语音输出约省 2 GB（官方）。本项目单句片段只有几秒、最多 12 帧，显存会低于 15 秒的数字，但权重本身就约 20 GB，**估计需要 24–30 GB** |
| Qwen2.5-Omni-7B | GPTQ-Int4 | 估计约 10 GB 量级 | 官方表里的一行：11.64 / 17.43 / 29.51 GB（列名没核实到，很可能对应 15 / 30 / 60 秒视频） | 官方称显存减少一半以上；部分权重放在内存里按需加载，**速度比原版慢** |
| Qwen2.5-Omni-7B | AWQ | 估计约 10 GB 量级 | 官方称显存减少一半以上，具体表格没核实到；估计和 GPTQ 版相近 | 速度一般比 GPTQ 版快，以实测为准 |
| Qwen2.5-Omni-3B | BF16 原版 | 估计约 10 GB 量级 | 官方理论最低值：15 秒 18.38 GB，30 秒 22.43 GB，60 秒 28.22 GB | 最小最快，翻译质量会差一些 |
| faster-whisper large-v3 | fp16 / int8 | 约 3 GB | 估计 fp16 约 3–6 GB，int8 约 3 GB | turbo 版更小更快；显存紧张时可以放到 CPU 上跑小模型 |
| SenseVoice-Small | — | 很小 | 估计 1 GB 以内；CPU 也能跑 | 实时模式想要更快的识别时用（官方称 10 秒音频约 70 毫秒） |
| Silero VAD | — | 很小 | 在 CPU 上跑 | 实时模式的停顿检测 |
| CosyVoice2-0.5B（语气配音，可选） | — | 估计几 GB | 估计 2–4 GB | 装在单独的 Python 3.10 环境里；官方依赖固定了旧版 PyTorch，RTX 50 系上要换成 CUDA 12.8 的版本（§10） |
| emotion2vec+（评测语气，可选） | — | 估计约 1 GB | 估计 1–2 GB | 只在评测时加载 |
| COMET（wmt22-comet-da） | — | 约 2.3 GB | 估计 3–4 GB；CPU 也能跑（慢） | 只有加分项 WMT 实验才用 |
| PaddleOCR | — | 小 | CPU 即可 | 只有加分项检查画面字幕时才用 |

**为什么 24 GB 不够跑 7B 原版**：权重约 22.4 GB，关掉语音输出后约 20 GB；再加上 CUDA 本身占用的约 0.5–1 GB，以及处理视频和音频的中间数据，24 GB 基本没有余量。

---

## 2. 几个部分同时运行时的显存

所有模型在同一个进程里常驻，先识别、再翻译。显存峰值出现在“多模态翻译”那一步。

| 场景 | 48 GB 卡（推荐方案） | 24 GB 卡（省钱方案） |
|---|---|---|
| 离线单句模式（M1、M2） | 7B 原版 + Whisper large-v3 fp16，估计合计 30–36 GB，宽裕 | 7B AWQ / GPTQ + Whisper int8 或 turbo，估计 15–20 GB；或者 3B 原版 + Whisper int8 |
| 实时模式（M2R） | 同上。两遍翻译是先后进行的，峰值不叠加 | 同上；GPTQ 版偏慢，实时延迟会变长，优先用 AWQ 或 3B |
| WMT 整段（M3 加分项，视频约 30 秒） | 7B 原版官方理论值 41.85 GB，实际约 50 GB，有超出 48 GB 的风险。按 `configs/mvp.yaml` 降低帧率和分辨率（`video_input.doc_fps`、`doc_max_pixels`）后估计能放下，必须实测 | 量化版约 17 GB（官方表，30 秒），可以跑 |
| 加上语气配音（M2T 语音层，可选） | CosyVoice 估计 2–4 GB，可以同时常驻 | 不要同时常驻：翻译全部完成后再单独合成；32 GB 的 RTX 5090 也一样 |
| 以上全部加上 COMET 打分 | 打分在翻译全部结束后单独运行，不和翻译同时占显存 | 同左；也可以放到 CPU 上跑 |

**省显存的办法**（按影响从大到小）：
1. 换量化版或 3B 版；
2. 降低每帧的分辨率（`max_pixels`）和帧率（`fps`）；
3. 缩短片段；
4. 关闭语音输出（`disable_talker()`）；
5. 把语音识别放到 CPU 上（SenseVoice-Small，或者 faster-whisper 的 small 版 int8）。

---

## 3. 显卡架构要求

| 架构 | 例子 | 能不能用 |
|---|---|---|
| Ampere | RTX 3090、A100、A800、A40、A6000 | 能。支持 BF16 和 FlashAttention-2 |
| Ada | RTX 4090、L20、L40 | 能。同上 |
| Hopper | H100、H800、H20 | 能。同上 |
| Blackwell | RTX 5090（32 GB）、RTX PRO 6000 等 | 能用，但**必须 PyTorch 2.7 以上、CUDA 12.8 以上**：算力架构是 sm_120，旧版 PyTorch 没有对应的内核。flash-attn 先不装，用 `sdpa`；faster-whisper、CosyVoice、量化推理库在这一代卡上都有兼容问题的报告，见 §10 |
| Volta、Turing | V100、T4、RTX 20 系 | **不推荐**。不支持 BF16，也不支持 FlashAttention-2，改用 FP16 可能数值溢出，而且慢 |

FlashAttention-2 是可选的，装不上就用 PyTorch 自带的注意力实现（`sdpa`），只是稍慢、稍占显存。

---

## 4. 其他硬件

| 项目 | 建议 | 原因 |
|---|---|---|
| CPU | 8 核以上 | 视频解码、ffmpeg 切片段 |
| 内存 | 32 GB 以上；用 GPTQ 版时建议 64 GB | 加载约 22 GB 的权重；GPTQ 版会把一部分权重放在内存里 |
| 数据盘 | 100 GB 左右，模型放数据盘，不要放系统盘 | 7B 原版约 22.4 GB；量化版和 3B 版各约 10 GB 量级（估计，以下载页面为准）；Whisper 约 3 GB；Python 环境约 10 GB；WMT 视频约 1.1 GB；结果和缓存若干 GB |
| 网络 | 能访问 GitHub、Hugging Face（或镜像）、ModelScope | 下载模型和数据；访问慢就开平台的学术加速，或者在自己电脑上下载后上传 |

---

## 5. 软件环境

| 组件 | 建议 | 说明 |
|---|---|---|
| 系统 | Ubuntu 22.04（直接用 AutoDL 的镜像） | — |
| Python | 3.10–3.12 | 3.12 上 decord 可能装不上，见下面 qwen-omni-utils 一行 |
| 显卡驱动 | 支持 CUDA 12.x | `nvidia-smi` 右上角显示驱动支持的 CUDA 版本 |
| PyTorch | 2.x 的 CUDA 12 版本（选平台现成的镜像，不要自己从头装）；RTX 50 系必须 2.7 以上的 CUDA 12.8 版本 | 要能装上模型卡要求的 transformers 版本。装其他包前后各看一次 torch 的版本，防止被顺带换掉 |
| transformers | 按 Qwen2.5-Omni 模型卡的要求 | 检验方法：`from transformers import Qwen2_5OmniForConditionalGeneration` 不报错 |
| qwen-omni-utils | 安装 `qwen-omni-utils[decord]`；decord 装不上就装 `qwen-omni-utils` 和 `av` | 处理视频和音频输入。没有 decord 时改用 torchvision 读视频（读取后端可用环境变量 `FORCE_QWENVL_VIDEO_READER` 指定，以所装版本的源码为准） |
| accelerate | 最新版 | `device_map="auto"` 要用 |
| 量化版的依赖 | 按 AWQ 或 GPTQ 模型卡安装推理库，并用模型卡里的加载方式 | 原版的加载代码不能直接用于量化版 |
| faster-whisper | 最新版需要 CUDA 12 和 cuDNN 9 | 环境里只有 cuDNN 8 时，按官方说明装 `ctranslate2==4.4.0`。RTX 50 系上可能报 cuBLAS 错误，见 §10 |
| flash-attn | 可选；只支持 Ampere 及更新的卡 | 用预编译的安装包；装不上就用 `sdpa` |
| ffmpeg | `apt-get install -y ffmpeg` | 切片段、抽音轨、封装 mp4 |
| 基础依赖 | soundfile、librosa、numpy、opencv-python-headless、gradio、pandas、pyyaml | — |
| 实时模式 | silero-vad；摄像头直播可选 `fastrtc[vad]` | — |
| 加分项 | sacrebleu、unbabel-comet、statsmodels、paddleocr | 只有做 WMT 实验才装 |
| 语气配音（可选） | CosyVoice 单独建一个 Python 3.10 的 conda 环境，放在数据盘上（`conda create -p /root/autodl-tmp/envs/cosyvoice python=3.10`） | 它的依赖固定了旧版 PyTorch，装进主环境会冲突；RTX 50 系上要换成 CUDA 12.8 的 PyTorch |

---

## 6. 你自己的电脑（演示用）

| 用途 | 需要什么 |
|---|---|
| 回放模式、静态 HTML、录屏 | 不需要 GPU，任何能装浏览器的电脑都可以 |
| 摄像头直播 | 摄像头和麦克风；Chrome 等浏览器；用 SSH 端口转发在 `localhost` 上打开 GPU 服务器的页面（浏览器只在 https 或 localhost 下允许用摄像头） |
| 课堂现场 | 稳定的网络；**提前录好屏幕录像作备份** |

---

## 7. 租卡建议（以 AutoDL 为例）

1. **装环境、下载模型时不用开 GPU**：用平台的无卡模式或最便宜的配置开机，装好环境、下载好模型（以平台说明为准），存在数据盘上。
2. **写代码、跑测试时也不用 GPU**：用假模型和合成数据（M0）。
3. **真正需要 GPU 的时候**：M1 第一次接上真实模型、M2 和 M2R 跑演示视频、彩排。预计总共 6–12 个 GPU 小时（估计，取决于调试顺不顺利）。
4. **加分项 WMT 实验**：临时换 80 GB 的卡跑 1–3 小时（估计）；或者用 48 GB 的卡，降低帧率后跑。
5. **做完就关机**。数据盘保留，下次开机不用重新下载模型。
6. **缓存都放数据盘**：系统盘只有 30 GB 左右。`export HF_HOME=/root/autodl-tmp/hf MODELSCOPE_CACHE=/root/autodl-tmp/modelscope PIP_CACHE_DIR=/root/autodl-tmp/pip-cache`，写进 `~/.bashrc`；conda 环境也建在数据盘上。
7. **克隆实例会带上原来的镜像**：换到 RTX 50 系的机器时，要把镜像换成 PyTorch 2.7 以上、CUDA 12.8 的（§10）。换镜像一般会重置系统盘、保留数据盘，以平台说明为准。

---

## 8. 开机后第一件事：10 分钟检查

```bash
nvidia-smi                                   # 显卡型号、显存、驱动支持的 CUDA 版本
python -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available(), torch.cuda.get_device_name(0), 'bf16:', torch.cuda.is_bf16_supported())"
python -c "import torch; print('capability', torch.cuda.get_device_capability(0), 'archs', torch.cuda.get_arch_list())"   # RTX 5090 是 (12, 0)，列表里要有 sm_120
python -c "import torch; x = torch.randn(2048, 2048, device='cuda', dtype=torch.bfloat16); print('matmul ok', (x @ x).float().abs().mean().item())"   # 真正跑一次计算
ffmpeg -version | head -1
python -c "from transformers import Qwen2_5OmniForConditionalGeneration; print('transformers ok')"
python -c "import ctranslate2; print('ctranslate2', ctranslate2.__version__, 'cuda devices:', ctranslate2.get_cuda_device_count())"
df -h /root/autodl-tmp                       # 数据盘剩余空间
free -g                                      # 内存
```

然后做一次**显存实测**，用来替换上面所有的“估计”：

0. 先用一段 5 秒的录音跑一次语音识别，确认识别后端在这张卡上能用（RTX 50 系上 faster-whisper 可能报错，§10）。
1. 加载模型，关闭语音输出。
2. 用一段 5 秒左右的片段，依次跑“只看文字、加声音、加画面、全给”四种条件。
3. 每种条件跑完都记录 `torch.cuda.max_memory_allocated()`，跑下一种之前先用 `torch.cuda.reset_peak_memory_stats()` 清零；同时记录每次的耗时。
4. 把结果写进 `reports/env.md`，格式如下：

| 显卡 | 模型版本 | 条件 | 片段长度 | 帧数 / 分辨率 | 峰值显存 | 耗时 |
|---|---|---|---|---|---|---|
| （实测后填写） | | | | | | |

如果峰值显存超过显卡容量的 90%，就按 §2 的办法降低设置，再测一次。

---

## 9. 怎么选：决策流程

```text
能租到 48 GB 以上的卡吗？
├─ 能 → 7B 原版 + Whisper large-v3。离线、实时、对比全部按默认设置
└─ 不能
   ├─ 能租到 32 GB 的 RTX 5090 → 换 PyTorch 2.7 以上、CUDA 12.8 的镜像；7B 原版单句模式（先实测显存）；注意力用 sdpa；语音识别报错就换后端；配音不和翻译模型同时常驻；WMT 整段换卡（§10）
   ├─ 能租到 24 GB 的卡 → 7B AWQ 版（优先）或 GPTQ-Int4 版（慢一些）或 3B 原版；Whisper 用 int8 或 turbo，必要时放到 CPU 上
   └─ 一张卡都租不到 → 用云端 API（阿里云百炼 Qwen-Omni，以及实时接口）加本地 CPU 做语音识别；或者只演示假模型流程，在报告里写明“未在真实模型上运行”
要做加分项 WMT 实验吗？
└─ 要 → 临时用 80 GB 的卡；或者 48 GB 的卡降低帧率和分辨率；24 GB 的卡用量化版
```

**注意**：同一张对比表、同一次演示里，只能用同一个模型版本。中途换了版本，所有条件都要重跑。报告里要写明用的是哪张卡、哪个版本。

---

## 10. 两台实例的评估（2026-11-24，按 AutoDL 控制台截图）

两台的镜像都是 PyTorch 2.5.1、Python 3.12、CUDA 12.4，系统盘 30 GB，数据盘 50 GB 免费加 250 GB 付费，开放了 http 的 6006、6008 端口。

| 项目 | 实例一：vGPU-48GB | 实例二：RTX 5090（32 GB） |
|---|---|---|
| 显卡是什么 | 网上的 AutoDL 部署教程把它写作 RTX 4090 的 48 GB 版（Ada 架构）。不会是 4080 改的：4080 的显存位宽是 256 位，加倍以后最多 32 GB；48 GB 要 384 位的 4090 或 4090D。开机后用 `nvidia-smi` 确认 | Blackwell 架构，算力架构 sm_120，32 GB 显存 |
| 显存够不够 | 够。7B 原版 + Whisper large-v3 估计 30–36 GB，还能放下 CosyVoice；WMT 整段降低帧率后估计能放下 | 单句模式估计放得下，但很紧：7B 原版关掉语音输出约 20 GB 权重，加上片段数据、CUDA 本身和 Whisper，估计 26–30 GB，必须实测。WMT 整段（约 30 秒）官方理论最低 41.85 GB，放不下。CosyVoice 不能同时常驻 |
| 算力够不够 | 够。单句翻译计算量很小，瓶颈是显存，不是算力 | 更快。生成文字主要受显存带宽限制，5090 约 1.8 TB/s，4090 约 1.0 TB/s |
| 现在的镜像 | 能直接用 | **用不了 GPU**：PyTorch 2.5.1 没有 sm_120 的内核，要换 PyTorch 2.7 以上、CUDA 12.8 的镜像 |
| flash-attn | 有预编译包可以试；装不上用 `sdpa` | 先不装，用 `sdpa` |
| 语音识别 | faster-whisper 正常 | 社区报告 faster-whisper 在 5090 上报 `CUBLAS_STATUS_NOT_SUPPORTED`。先试 float16，不行就换 transformers 版 Whisper（`docs/build_multimodal_translation.md` 第 3 步）或 SenseVoice-Small |
| 语气配音（CosyVoice） | 按官方说明建 Python 3.10 环境 | 官方固定的 PyTorch 2.3.1（cu121）在 Blackwell 上报 `no kernel image`，要自己换成 CUDA 12.8 的 PyTorch 再调依赖，有风险 |
| 量化版（AWQ、GPTQ） | 可以用 | 推理库在新架构上的支持不确定，先别依赖 |
| 价格（截图） | 2.88 元/小时 | 2.98 元/小时 |
| 结论 | **推荐**：省心，交差版的全部功能都能跑 | 主要功能能跑，但要换镜像、换注意力实现、可能换识别后端；WMT 整段和实时配音不方便 |

两台都要注意：
- Python 3.12 上 decord 可能装不上，按 §5 改用 `av`。
- 模型、缓存、conda 环境都放 `/root/autodl-tmp`（§7 第 6 条）。
- 只开了 http 端口。网页可以用 AutoDL 的自定义服务打开，摄像头直播要用 SSH 端口转发，在自己电脑的 `localhost` 上打开（§6）。

### 10.1 用 RTX 5090 时的装机步骤

```bash
# 0. 关机，在控制台“更换镜像”，选 PyTorch 2.7 或 2.8、CUDA 12.8 的基础镜像，再开机。
#    列表里没有的话，选 CUDA 12.8 的 Miniconda 镜像，再装：
#    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
# 1. 确认 PyTorch 认得这张卡，并且真能算
python -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.get_device_name(0), torch.cuda.get_device_capability(0), 'sm_120' in torch.cuda.get_arch_list())"
python -c "import torch; x = torch.randn(2048, 2048, device='cuda', dtype=torch.bfloat16); print('matmul ok', (x @ x).float().abs().mean().item())"
# 2. 缓存放数据盘
export HF_HOME=/root/autodl-tmp/hf MODELSCOPE_CACHE=/root/autodl-tmp/modelscope PIP_CACHE_DIR=/root/autodl-tmp/pip-cache
# 3. 装依赖，不要重装 torch；装完再看一次 torch 的版本有没有变
python -c "import torch; print(torch.__version__)"
pip install accelerate qwen-omni-utils av soundfile librosa gradio pandas pyyaml modelscope faster-whisper
pip install -U transformers
python -c "import torch; print(torch.__version__)"
python -c "from transformers import Qwen2_5OmniForConditionalGeneration; print('transformers ok')"
```

然后：
1. `configs/mvp.yaml` 里 `models.omni.attn_implementation` 改成 `sdpa`。
2. 用一段 5 秒的录音试 faster-whisper（`compute_type: float16`）。报错就把 `asr.backend` 和 `realtime.asr_backend` 改成 `hf-whisper`。
3. 按 §8 做显存实测：7B 原版 + 识别模型一起加载，跑四种条件。峰值超过约 29 GB（容量的 90%）就降低 `max_pixels` 或帧数、识别换 turbo 版或放到 CPU，还不行就换 3B。
4. 语气配音：实时模式不开；离线配音放在翻译全部完成、翻译模型释放之后单独跑。CosyVoice 装不上就只做文字层。
5. WMT 整段实验（加分项）放不下：换回 48 GB 以上的卡跑，或者不做。

### 10.2 建议

- 价格几乎一样，**优先用 vGPU-48GB**：显存余量大，现在的镜像就能用，语气配音和 WMT 整段也方便。
- 只有 RTX 5090 可用时，按 §10.1 装，交差版的必须项（M0、M1、M2R-a、M2T 文字层、M5）都能做。
- 不论用哪台，同一张对比表只能用同一张卡、同一个模型版本、同一个识别后端；报告写明用的是哪一台。
