"""Turn a text script into a narrated video with burned-in captions.

Script format (plain .txt):
  - Each paragraph (separated by a blank line) is one scene.
  - A line like  [visual: myclip.mp4]  sets that scene's footage or image
    (looked up in the "Raw Foorage" / "Raw Footage" folder, then next to the script).
  - Lines starting with # are comments.
  Scenes without a visual reuse files from the footage folder in order,
  or fall back to a generated title card showing the scene text.

Usage:
  .venv\\Scripts\\python make_video.py Scripts\\demo.txt
  .venv\\Scripts\\python make_video.py Scripts\\demo.txt --engine kokoro --voice af_heart
  .venv\\Scripts\\python make_video.py Scripts\\demo.txt --vertical --music song.mp3
"""
import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

ROOT = Path(__file__).resolve().parent
MODELS = ROOT / "models"
FOOTAGE_DIRS = [ROOT / "Raw Foorage", ROOT / "Raw Footage"]
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
FPS = 30
PAUSE = 0.35  # seconds of silence after each scene's narration


def run(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"Command failed: {' '.join(map(str, cmd))}\n{r.stderr[-2000:]}")
    return r.stdout


def duration(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)])
    return float(json.loads(out)["format"]["duration"])


def parse_script(path):
    scenes = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig")):
        visual, lines = None, []
        for line in block.strip().splitlines():
            line = line.strip()
            m = re.fullmatch(r"\[visual:\s*(.+?)\]", line, re.I)
            if m:
                visual = m.group(1)
            elif line and not line.startswith("#"):
                lines.append(line)
        if lines:
            scenes.append({"text": " ".join(lines), "visual": visual})
    return scenes


def resolve_visual(name, script_dir):
    for d in [*FOOTAGE_DIRS, script_dir, ROOT]:
        p = d / name
        if p.exists():
            return p
    sys.exit(f"Visual not found: {name}")


def footage_pool():
    return sorted(p for d in FOOTAGE_DIRS if d.exists() for p in d.iterdir()
                  if p.suffix.lower() in VIDEO_EXT | IMAGE_EXT)


# ---------- voice over ----------

def tts_edge(text, out, voice, rate):
    import edge_tts
    asyncio.run(edge_tts.Communicate(text, voice, rate=rate).save(str(out)))


_kokoro = None


def tts_kokoro(text, out, voice, speed):
    global _kokoro
    import soundfile as sf
    from kokoro_onnx import Kokoro
    if _kokoro is None:
        _kokoro = Kokoro(str(MODELS / "kokoro-v1.0.onnx"), str(MODELS / "voices-v1.0.bin"))
    samples, sr = _kokoro.create(text, voice=voice, speed=speed, lang="en-us")
    sf.write(str(out), samples, sr)


def narrate(scenes, work, args):
    for i, s in enumerate(scenes):
        raw = work / f"vo_{i:03}.{'mp3' if args.engine == 'edge' else 'wav'}"
        if args.engine == "edge":
            tts_edge(s["text"], raw, args.voice or "en-US-AriaNeural", args.rate)
        else:
            speed = 1 + int(args.rate.rstrip("%")) / 100
            tts_kokoro(s["text"], raw, args.voice or "af_heart", speed)
        wav = work / f"vo_{i:03}_pad.wav"
        run(["ffmpeg", "-y", "-i", raw, "-af", f"apad=pad_dur={PAUSE}", "-ar", "48000", "-ac", "2", wav])
        s["audio"], s["dur"] = wav, duration(wav)
        print(f"  scene {i + 1}: {s['dur']:.1f}s")
    lst = work / "audio.txt"
    lst.write_text("".join(f"file '{s['audio'].name}'\n" for s in scenes))
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst.name, "-c", "copy", "narration.wav"], cwd=work)
    return work / "narration.wav"


# ---------- visuals ----------

def title_card(text, out, w, h):
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (w, h), (18, 24, 38))
    d = ImageDraw.Draw(img)
    size = w // 28
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", size)
    except OSError:
        font = ImageFont.load_default(size)
    wrapped = "\n".join(textwrap.wrap(text, width=int(w / size * 1.7))[:8])
    d.multiline_text((w / 2, h / 2), wrapped, font=font, fill=(240, 240, 240), anchor="mm",
                     align="center", spacing=size // 2)
    img.save(out)
    return out


def render_scene(i, s, work, w, h):
    out = work / f"scene_{i:03}.mp4"
    src, dur = s["src"], s["dur"]
    fit = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"
    if src.suffix.lower() in VIDEO_EXT:
        inp = ["-stream_loop", "-1", "-i", src]
        vf = f"{fit},fps={FPS}"
    else:  # still image: slow Ken Burns zoom
        frames = int(dur * FPS) + 1
        inp = ["-loop", "1", "-framerate", str(FPS), "-i", src]
        vf = (f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2},"
              f"zoompan=z='min(zoom+0.0006,1.12)':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2'"
              f":d={frames}:s={w}x{h}:fps={FPS},setsar=1")
    run(["ffmpeg", "-y", *inp, "-t", f"{dur:.3f}", "-vf", vf, "-an", "-c:v", "libx264",
         "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS), out])
    return out


# ---------- captions ----------

def srt_time(t):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def make_captions(audio, out, model_size, max_words):
    from faster_whisper import WhisperModel
    import numpy as np
    # decode with ffmpeg ourselves: faster-whisper's PyAV decoder breaks on newer av releases
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(audio), "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
                         capture_output=True, check=True).stdout
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    # no initial_prompt: feeding the script itself makes whisper skip the opening words
    segments, _ = model.transcribe(np.frombuffer(pcm, np.float32), word_timestamps=True, language="en")
    words = [w for seg in segments for w in seg.words]
    chunks, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= max_words or w.word.rstrip().endswith((".", "!", "?", ",")):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    out.write_text("".join(
        f"{n}\n{srt_time(c[0].start)} --> {srt_time(c[-1].end)}\n{''.join(w.word for w in c).strip()}\n\n"
        for n, c in enumerate(chunks, 1)), encoding="utf-8")
    return out


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("script", type=Path)
    ap.add_argument("-o", "--output", type=Path, help="default: Exports/<script name>.mp4")
    ap.add_argument("--engine", choices=["edge", "kokoro"], default="edge",
                    help="edge = Microsoft neural voices (online); kokoro = fully offline")
    ap.add_argument("--voice", help="edge default en-US-AriaNeural; kokoro default af_heart")
    ap.add_argument("--rate", default="+0%", help="speaking rate, e.g. +10%% or -5%%")
    ap.add_argument("--vertical", action="store_true", help="1080x1920 for Shorts/Reels/TikTok")
    ap.add_argument("--music", type=Path, help="background music, auto-lowered under the voice")
    ap.add_argument("--music-volume", type=float, default=0.12)
    ap.add_argument("--no-captions", action="store_true")
    ap.add_argument("--whisper-model", default="base", help="tiny/base/small/medium")
    ap.add_argument("--caption-words", type=int, default=6, help="max words per caption line")
    ap.add_argument("--keep-work", action="store_true", help="keep intermediate files")
    args = ap.parse_args()

    script = args.script.resolve()
    w, h = (1080, 1920) if args.vertical else (1920, 1080)
    out = (args.output or ROOT / "Exports" / f"{script.stem}.mp4").resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    work = out.parent / f".work_{script.stem}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()

    scenes = parse_script(script)
    if not scenes:
        sys.exit("Script has no text.")
    print(f"{len(scenes)} scenes. Generating voice over ({args.engine})...")
    narration = narrate(scenes, work, args)

    print("Rendering visuals...")
    pool = footage_pool()
    for i, s in enumerate(scenes):
        if s["visual"]:
            s["src"] = resolve_visual(s["visual"], script.parent)
        elif pool:
            s["src"] = pool[i % len(pool)]
        else:
            s["src"] = title_card(s["text"], work / f"card_{i:03}.png", w, h)
        render_scene(i, s, work, w, h)
    (work / "video.txt").write_text("".join(f"file 'scene_{i:03}.mp4'\n" for i in range(len(scenes))))
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "video.txt", "-c", "copy", "video.mp4"], cwd=work)

    vf = []
    if not args.no_captions:
        print(f"Transcribing captions (whisper {args.whisper_model})...")
        srt = make_captions(narration, work / "captions.srt", args.whisper_model, args.caption_words)
        shutil.copy(srt, out.with_suffix(".srt"))
        fs = 14 if args.vertical else 20
        vf = ["-vf", f"subtitles=captions.srt:force_style='FontName=Segoe UI,Bold=1,FontSize={fs},"
                     f"PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,"
                     f"Shadow=0,MarginV={60 if args.vertical else 40}'"]

    print("Mixing and encoding final video...")
    inputs = ["-i", "video.mp4", "-i", "narration.wav"]
    if args.music:
        inputs += ["-stream_loop", "-1", "-i", str(args.music.resolve())]
        audio = ["-filter_complex",
                 f"[2:a]volume={args.music_volume}[m];[1:a][m]amix=inputs=2:duration=first:normalize=0[a]",
                 "-map", "0:v", "-map", "[a]"]
    else:
        audio = ["-map", "0:v", "-map", "1:a"]
    run(["ffmpeg", "-y", *inputs, *audio, *vf, "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
         str(out)], cwd=work)

    if not args.keep_work:
        shutil.rmtree(work, ignore_errors=True)
    print(f"Done: {out} ({duration(out):.1f}s)")


if __name__ == "__main__":
    main()
