"""CPU speaker and lip-motion analysis in the local speech environment."""

import json
import subprocess
import sys
from itertools import count
from pathlib import Path

import numpy as np

RATE = 16_000
FPS = 12
WIDTH, HEIGHT = 640, 384
WINDOW_MS = 2200
VOICE_CLUSTER_DISTANCE = 0.60


def voice_windows(start: int, end: int) -> list[tuple[int, int]]:
    bounds = [(left, min(left + WINDOW_MS, end)) for left in range(start, end, WINDOW_MS)]
    if len(bounds) > 1 and bounds[-1][1] - bounds[-1][0] < 650:
        bounds[-2] = (bounds[-2][0], end)
        bounds.pop()
    return [(left, right) for left, right in bounds if right - left >= 650]


def speaker_groups(windows: list[tuple[np.ndarray, int]]) -> list[int | None]:
    from scipy.cluster.hierarchy import fcluster, linkage

    long = [index for index, (_, voiced) in enumerate(windows) if voiced >= 1250]
    if not long:
        return [None] * len(windows)
    vectors = np.stack([windows[i][0] for i in long])
    labels = (
        fcluster(linkage(vectors, method="average", metric="cosine"), VOICE_CLUSTER_DISTANCE, criterion="distance")
        if len(long) > 1
        else [1]
    )
    groups = list(dict.fromkeys(labels))
    centers = np.stack([vectors[np.array(labels) == label].mean(axis=0) for label in groups])
    centers /= np.linalg.norm(centers, axis=1, keepdims=True).clip(1e-6)
    assignments = dict(zip(long, (groups.index(label) + 1 for label in labels), strict=True))
    for index, (vector, _) in enumerate(windows):
        if index in assignments:
            continue
        scores = centers @ vector
        order = scores.argsort()
        best = order[-1]
        if scores[best] >= 0.64 and (len(order) == 1 or scores[best] - scores[order[-2]] >= 0.10):
            assignments[index] = int(best) + 1
    return [assignments.get(i) for i in range(len(windows))]


def measurements(f0: np.ndarray) -> dict:
    voiced = f0[np.isfinite(f0) & (f0 > 0)]
    if len(voiced) < 26:
        return {"voiced_ms": len(voiced) * 10}
    semitones = 12 * np.log2(voiced)
    endpoint = max(8, len(voiced) // 4)
    change = float(np.median(semitones[-endpoint:]) - np.median(semitones[:endpoint]))
    low, high = np.percentile(semitones, [10, 90])
    return {
        "voiced_ms": len(voiced) * 10,
        "median_f0_hz": round(float(np.median(voiced)), 1),
        "pitch_range_st": round(float(high - low), 1),
        "pitch_change_st": round(change, 1),
        "pitch_trend": "rising" if change >= 1.7 else "falling" if change <= -1.7 else "level",
    }


def iou(a: tuple, b: tuple) -> float:
    x = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    y = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return x * y / max((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - x * y, 1e-6)


def face_tracks(video: str, ffmpeg: str, model: str, yunet: str, duration_ms: int, progress) -> list[list[tuple]]:
    import cv2
    import mediapipe as mp

    cv2.setNumThreads(2)
    locator = cv2.FaceDetectorYN.create(yunet, "", (WIDTH, HEIGHT), score_threshold=0.72)
    vision = mp.tasks.vision
    options = vision.FaceLandmarkerOptions(base_options=mp.tasks.BaseOptions(model_asset_path=model))
    filters = (
        f"fps={FPS}:start_time=0,scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2"
    )
    args = [
        ffmpeg,
        "-nostdin",
        "-v",
        "error",
        "-threads",
        "2",
        "-i",
        video,
        "-t",
        str(duration_ms / 1000),
        "-an",
        "-filter_threads",
        "2",
        "-vf",
        filters,
        "-threads",
        "1",
        "-pix_fmt",
        "rgb24",
        "-f",
        "rawvideo",
        "pipe:1",
    ]
    tracks: list[list[tuple]] = []
    active: dict[int, tuple] = {}
    size = WIDTH * HEIGHT * 3
    with (
        vision.FaceLandmarker.create_from_options(options) as detector,
        subprocess.Popen(args, stdout=subprocess.PIPE) as pipe,
    ):
        for index in count():
            frame = pipe.stdout.read(size)
            if len(frame) != size:
                break
            ms = round(index * 1000 / FPS)
            rgb = np.frombuffer(frame, dtype=np.uint8).reshape(HEIGHT, WIDTH, 3)
            _, faces = locator.detect(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
            active = {i: face for i, face in active.items() if ms - face[0] <= 250}
            used = set()
            for face in faces[:4] if faces is not None else ():
                x, y, w, h = face[:4]
                left, right = max(0, int(x - w * 0.20)), min(WIDTH, round(x + w * 1.20))
                top, bottom = max(0, int(y - h * 0.20)), min(HEIGHT, round(y + h * 1.20))
                if min(right - left, bottom - top) < 30:
                    continue
                crop = np.ascontiguousarray(rgb[top:bottom, left:right])
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=crop)
                landmarks = detector.detect(image).face_landmarks
                if not landmarks:
                    continue
                points = landmarks[0]
                mouth_width = abs(points[291].x - points[61].x) * crop.shape[1]
                if mouth_width < 9:
                    continue
                mouth = abs(points[14].y - points[13].y) * crop.shape[0] / mouth_width
                box = (float(x / WIDTH), float(y / HEIGHT), float((x + w) / WIDTH), float((y + h) / HEIGHT))
                matches = [(iou(face[2], box), i) for i, face in active.items() if i not in used]
                overlap, face_id = max(matches, default=(0, -1))
                if overlap < 0.30:
                    face_id = len(tracks)
                    tracks.append([])
                active[face_id] = (ms, mouth, box)
                tracks[face_id].append(active[face_id])
                used.add(face_id)
            if index % FPS == 0:
                progress("正在核对画面口型", min(ms // 1000, duration_ms // 1000), (duration_ms + 999) // 1000, "秒")
        pipe.stdout.close()
        if pipe.wait():
            raise RuntimeError("FFmpeg failed while decoding speaker video")
    return tracks


def correlation(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a - a.mean(), b - b.mean()
    product = np.linalg.norm(a) * np.linalg.norm(b)
    return float(a @ b / product) if product > 1e-8 else 0.0


def match_face(audio: np.ndarray, tracks: list[list[tuple]], start: int, end: int) -> dict | None:
    candidates = []
    half = RATE // FPS // 2
    for track in tracks:
        visible = [(ms, mouth, box) for ms, mouth, box in track if start <= ms < end]
        if len(visible) < max(12, round((end - start) * FPS / 2000)):
            continue
        times, mouth, boxes = zip(*visible, strict=True)
        mouth = np.array(mouth)
        if np.ptp(np.percentile(mouth, [10, 90])) < 0.035:
            continue
        energy = []
        for ms in times:
            center = round(ms * RATE / 1000)
            span = audio[max(0, center - half) : center + half]
            energy.append(np.sqrt(np.mean(span * span)) if len(span) else 0.0)
        rms = np.array(energy)
        loudness = np.log(rms + 1e-4)
        motion = np.abs(np.gradient(mouth))
        score = max(
            correlation(mouth, loudness),
            correlation(mouth[1:], loudness[:-1]),
            correlation(mouth[:-1], loudness[1:]),
            correlation(motion, rms),
            correlation(motion[1:], rms[:-1]),
            correlation(motion[:-1], rms[1:]),
        )
        frame = int(np.argmax(mouth))
        x = (boxes[frame][0] + boxes[frame][2]) / 2
        candidates.append((score, times[frame], "left" if x < 0.38 else "right" if x > 0.62 else "center"))
    candidates.sort(reverse=True)
    if (
        not candidates
        or candidates[0][0] < 0.26
        or (len(candidates) > 1 and candidates[0][0] - candidates[1][0] < 0.14)
    ):
        return None
    score, time_ms, side = candidates[0]
    return {"side": side, "time_ms": time_ms, "mouth_audio_score": round(score, 3)}


def analyze(data: dict, progress) -> dict:
    import onnxruntime
    import pyworld
    import soundfile
    import torch
    from torchaudio.compliance.kaldi import fbank

    torch.set_num_threads(4)
    audio, rate = soundfile.read(data["audio"], dtype="float32")
    if audio.ndim != 1 or rate != RATE:
        raise ValueError("Speaker analysis expects mono 16 kHz audio")
    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = 4
    options.inter_op_num_threads = 1
    session = onnxruntime.InferenceSession(
        data["embedding_model"], sess_options=options, providers=["CPUExecutionProvider"]
    )
    input_name = session.get_inputs()[0].name
    speech = data["speech_ms"]
    windows = []
    line_windows = []
    lines = data["lines"]
    for i, line in enumerate(lines):
        start, end = line["start_ms"], line["end_ms"]
        first = len(windows)
        for left, right in voice_windows(start, end):
            voiced = sum(max(0, min(right, stop) - max(left, begin)) for begin, stop in speech)
            if voiced < 565 or voiced < (right - left) * 0.55:
                continue
            chunk = audio[left * 16 : right * 16]
            if len(chunk) < 0.6 * RATE:
                continue
            features = fbank(torch.from_numpy(chunk.copy())[None, :], num_mel_bins=80, dither=0, sample_frequency=RATE)
            features -= features.mean(dim=0, keepdim=True)
            vector = session.run(None, {input_name: features[None, :, :].numpy()})[0].ravel()
            norm = float(np.linalg.norm(vector))
            if np.isfinite(norm) and norm > 1e-6:
                windows.append((vector / norm, voiced))
        line_windows.append((first, len(windows)))
        progress("正在提取声纹和区分说话人", i + 1, len(lines), "句")
    groups = speaker_groups(windows)
    tracks = []
    vision_status = "unavailable"
    if data["face_model"] and data["face_detector"] and data["video"]:
        try:
            tracks = face_tracks(
                data["video"],
                data["ffmpeg"],
                data["face_model"],
                data["face_detector"],
                max(line["end_ms"] for line in lines),
                progress,
            )
            vision_status = "complete"
        except Exception as error:
            print(f"Vision stage unavailable: {type(error).__name__}: {error}", file=sys.stderr)
    result = {}
    for i, (line, (first, last)) in enumerate(zip(lines, line_windows, strict=True)):
        start, end = line["start_ms"], line["end_ms"]
        speakers = sorted({group for group in groups[first:last] if group is not None})
        row = {"speaker_ids": speakers, "start_ms": start, "end_ms": end}
        if len(speakers) == 1:
            duration = sum(max(0, min(end, stop) - max(start, begin)) for begin, stop in speech)
            if duration >= 550:
                row["speech_ms"] = duration
            samples = np.ascontiguousarray(audio[start * 16 : end * 16], dtype=np.float64)
            if len(samples) >= 0.30 * RATE:
                f0, times = pyworld.dio(samples, RATE, f0_floor=55, f0_ceil=520, frame_period=10)
                pitch = pyworld.stonemask(samples, f0, times, RATE)
                row["pitch"] = measurements(pitch)
            face = match_face(audio, tracks, start, end)
            if face:
                row["face"] = face
        result[line["line_id"]] = row
        progress("正在测量每句原声的语调与语速", i + 1, len(lines), "句")
    return {"lines": result, "vision_status": vision_status, "voice_cluster_distance": VOICE_CLUSTER_DISTANCE}


def main(request: Path, output: Path) -> None:
    data = json.loads(request.read_text(encoding="utf-8"))
    state = Path(data["status_file"])

    def progress(message, completed, total, unit):
        temporary = state.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {"status": "running", "message": message, "completed": completed, "total": total, "unit": unit},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        temporary.replace(state)

    output.write_text(json.dumps(analyze(data, progress), ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
