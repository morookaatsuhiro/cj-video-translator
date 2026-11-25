from pathlib import Path

from cjvmt.config import Settings
from cjvmt.media import ffmpeg, video_encode


def generate(path: Path) -> None:
    settings = Settings.load()
    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg(
        settings,
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=25:duration=8",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=480:sample_rate=16000:duration=8",
            "-af",
            "volume='if(between(t,0.6,1.9)+between(t,3.3,4.8)+between(t,6,7),0.2,0)':eval=frame",
            *video_encode(),
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
    )


if __name__ == "__main__":
    generate(Path("tests/fixtures/synthetic_dialogue.mp4"))
