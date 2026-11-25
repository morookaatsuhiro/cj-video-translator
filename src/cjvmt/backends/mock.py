import re

from cjvmt.backends import Request


class Mock:
    identity = {"backend": "mock", "model": "mock-omni", "revision": "1"}

    def __init__(self):
        self.calls: list[Request] = []

    def generate(self, request: Request) -> dict:
        self.calls.append(request)
        has_audio = request.audio is not None or request.interleaved
        has_visual = request.video is not None or bool(request.frames)

        def translate(text: str) -> str:
            if request.direction == "zh2ja":
                if "明天" in text:
                    return "明日も行くの？" if has_audio or "？" in text else "明日も行くよ。"
                return "本当なの？" if "真的" in text else "いいですよ。"
            if "田中" in text:
                return "田中也参加吗？" if has_audio or "？" in text else "田中也参加。"
            if "本当" in text:
                return "真的假的？" if has_audio else "真的吗？"
            return "不用了。" if has_visual else "好啊。"

        if request.task == "observe":
            result = "1. 上扬\n2. 摆手\n3. 不确定\n4. 语气：怀疑；强度：中。"
        elif request.task == "document":
            lines = re.findall(r"^([0-9]+)\. (.+)$", request.user, re.M)
            result = "\n".join(f"{index}. {translate(line)}" for index, line in lines)
        else:
            result = translate(request.source)
        return {"text": result, "output_tokens": None, "peak_vram_mib": None}
