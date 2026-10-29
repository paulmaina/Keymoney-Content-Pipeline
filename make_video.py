"""Turn a text script into narrated videos with burned-in captions.

By default both formats are rendered from one narration and one caption track:
  Exports/<name>_horizontal.mp4   1920x1080 for YouTube / Facebook
  Exports/<name>_vertical.mp4     1080x1920 for Instagram Reels / TikTok / Shorts
  Exports/<name>.srt              caption file (same timings for both)

Script format (plain .txt):
  - Each paragraph (separated by a blank line) is one scene.
  - [video] at the start of a line makes that scene an AI video clip (Kaggle).
  - [visual: myclip.mp4] at the start of a line sets that scene's footage or image
    (looked up in the "Raw Footage" folder, then next to the script).
  - "# character: Name = description" declares a character for consistent AI images.
  - Any other line starting with # is a comment: never spoken, captioned or used in prompts.
  Scenes without a visual reuse files from the footage folder in order,
  or fall back to a generated title card showing the scene text.

Usage:
  .venv\\Scripts\\python make_video.py Scripts\\demo.txt
  .venv\\Scripts\\python make_video.py Scripts\\demo.txt --format vertical --music "Raw Footage\\song.mp3"
  .venv\\Scripts\\python make_video.py Scripts\\demo.txt --engine kokoro --voice am_michael
  .venv\\Scripts\\python make_video.py Scripts\\demo.txt --generate-visuals --style "cinematic, warm tones"

AI video clips (scenes with a [video] line, made on a free Kaggle GPU):
  .venv\\Scripts\\python make_video.py Scripts\\demo.txt --export-video-job
  .venv\\Scripts\\python make_video.py Scripts\\demo.txt --generate-visuals --clips clips.zip
"""
import argparse
import asyncio
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

import ai_visuals
import kaggle_runner
import video_job
from download_models import MODEL_FILES, MODELS_DIR

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

ROOT = Path(__file__).resolve().parent
# "Raw Foorage" is the folder's original misspelling, still accepted
FOOTAGE_DIRS = [ROOT / "Raw Footage", ROOT / "Raw Foorage"]
DEFAULT_FONT = ROOT / "assets" / "fonts" / "NotoSans-Bold.ttf"
CONFIG_FILE = ROOT / "make_video.toml"  # your defaults for any option, e.g. style = "3D animated film style"
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
FPS = 30
PAUSE = 0.35  # seconds of silence after each scene's narration
MUSIC_FADE = 2.0  # seconds of music fade-out at the end
TTS_ATTEMPTS = 3
FORMATS = {
    "horizontal": {"size": (1920, 1080), "font_size": 20, "margin": 40},
    "vertical": {"size": (1080, 1920), "font_size": 14, "margin": 60},
}


def run(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"Command failed: {' '.join(map(str, cmd))}\n{r.stderr[-2000:]}")
    return r.stdout


def duration(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)])
    return float(json.loads(out)["format"]["duration"])


def refresh_windows_path():
    """Terminals opened before FFmpeg was installed keep a stale PATH; re-read it from the registry."""
    import winreg
    dirs = []
    for root, key in [(winreg.HKEY_CURRENT_USER, "Environment"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")]:
        try:
            with winreg.OpenKey(root, key) as k:
                dirs += os.path.expandvars(winreg.QueryValueEx(k, "Path")[0]).split(";")
        except OSError:
            pass
    os.environ["PATH"] = os.pathsep.join([os.environ.get("PATH", ""), *filter(None, dirs)])


def check_ffmpeg():
    missing = [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]
    if missing and os.name == "nt":
        refresh_windows_path()
        missing = [t for t in missing if not shutil.which(t)]
    if missing:
        sys.exit(f"{' and '.join(missing)} not found on PATH.\n"
                 "Install FFmpeg (Windows: winget install Gyan.FFmpeg, macOS: brew install ffmpeg,\n"
                 "Linux: apt install ffmpeg), then open a new terminal and try again.")


# [video] and [visual: file] tags at the start of a line, alone or followed by narration on the same line
LEADING_TAG = re.compile(r"^\[(?:(video)|visual:\s*([^\]]+?))\s*\]\s*", re.I)
CHARACTER_LINE = re.compile(r"^#\s*character\s*:\s*(.+?)\s*(?:=|:| - | – | — )\s*(.+)$", re.I)
# "# HACK 1 NIGERIA - Visuals: market in harsh sun", "# Shot: ...": what the scene should show. Used in that scene's image
# and motion prompts (never spoken or captioned). Any other '#' line is a plain comment.
DESCRIPTION_LINE = re.compile(r"^#.*?\b(?:visuals?|shot|description)\s*:\s*\S", re.I)


def parse_script(path):
    scenes = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig")):
        visual, lines, video, described = None, [], False, []
        for line in block.strip().splitlines():
            line = line.strip()
            if line.startswith("#"):
                if DESCRIPTION_LINE.match(line) and not CHARACTER_LINE.match(line):
                    described.append(line.lstrip("#").strip())
                continue  # never spoken or captioned; plain comments aren't used anywhere
            while (m := LEADING_TAG.match(line)):
                if m.group(1):
                    video = True  # make this scene an AI video clip (see --export-video-job)
                else:
                    visual = m.group(2).strip()
                line = line[m.end():]
            if line:
                lines.append(line)
        if lines:
            scenes.append({"text": " ".join(lines), "visual": visual, "video": video,
                           "description": "; ".join(described)})
    return scenes


def parse_comments(path):
    """(line number, text) of every plain '#' comment the script skips: not character or description lines."""
    return [(n, line.strip()) for n, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1)
            if line.strip().startswith("#") and not CHARACTER_LINE.match(line.strip())
            and not DESCRIPTION_LINE.match(line.strip())]


def parse_characters(path):
    """Character sheet from '# character: Name = description' lines anywhere in the script (not spoken)."""
    sheet = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if (m := CHARACTER_LINE.match(line.strip())):
            sheet[m.group(1).strip()] = m.group(2).strip()
    return sheet


def resolve_visual(name, script_dir):
    for d in [*FOOTAGE_DIRS, script_dir, ROOT]:
        p = d / name
        if p.exists():
            return p
    return None


def footage_pool():
    return sorted(p for d in FOOTAGE_DIRS if d.exists() for p in d.iterdir()
                  if p.suffix.lower() in VIDEO_EXT | IMAGE_EXT)


def assign_visuals(scenes, script_dir, use_pool=True):
    """Pick each scene's source before any audio is generated, so a bad path fails fast.
    A src of None means the scene gets a title card (or an AI image with --generate-visuals)."""
    missing = [s["visual"] for s in scenes if s["visual"] and not resolve_visual(s["visual"], script_dir)]
    if missing:
        sys.exit("Visual(s) not found: " + ", ".join(missing) +
                 f"\nPut them in \"{FOOTAGE_DIRS[0].name}\" or next to the script.")
    pool = footage_pool() if use_pool else []
    for i, s in enumerate(scenes):
        if s["visual"]:
            s["src"] = resolve_visual(s["visual"], script_dir)
        else:
            s["src"] = pool[i % len(pool)] if pool else None


def output_base(output, script):
    """Base path for outputs; _horizontal/_vertical and .srt are added to it."""
    base = (Path(output) if output else ROOT / "Exports" / script.stem).resolve()
    if base.suffix.lower() == ".mp4":
        base = base.with_suffix("")
    return base


# ---------- voice over ----------

def tts_edge(text, out, voice, rate):
    import edge_tts
    for attempt in range(1, TTS_ATTEMPTS + 1):
        try:
            asyncio.run(edge_tts.Communicate(text, voice, rate=rate).save(str(out)))
            return
        except Exception as e:  # edge-tts surfaces network trouble as several exception types
            if attempt == TTS_ATTEMPTS:
                sys.exit(f"edge-tts failed after {TTS_ATTEMPTS} attempts: {e}\n"
                         "Check your internet connection and the --voice name, "
                         "or use the offline voice with --engine kokoro.")
            print(f"  edge-tts error ({e}); retrying...")
            time.sleep(2 * attempt)


_kokoro = None


def check_kokoro_model():
    missing = [n for n in MODEL_FILES if not (MODELS_DIR / n).exists()]
    if missing:
        sys.exit(f"Offline voice model missing from {MODELS_DIR}: {', '.join(missing)}\n"
                 "Download it (about 340 MB, once) with:\n"
                 "  .venv\\Scripts\\python download_models.py\n"
                 "or use the online voice with --engine edge.")


def tts_kokoro(text, out, voice, speed):
    global _kokoro
    import soundfile as sf
    from kokoro_onnx import Kokoro
    if _kokoro is None:
        _kokoro = Kokoro(str(MODELS_DIR / MODEL_FILES[0]), str(MODELS_DIR / MODEL_FILES[1]))
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
        s["dur"] = duration(wav)
        print(f"  scene {i + 1}: {s['dur']:.1f}s")
    lst = work / "audio.txt"
    lst.write_text("".join(f"file 'vo_{i:03}_pad.wav'\n" for i in range(len(scenes))))
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst.name, "-c", "copy", "narration.wav"], cwd=work)
    return work / "narration.wav"


def music_filter(dur, volume, fade=MUSIC_FADE):
    """Filter graph for inputs 1 (narration) and 2 (looped music) -> [a].
    The music is trimmed to the narration, faded out, and ducked whenever the voice is speaking."""
    return (f"[2:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{dur:.3f},volume={volume},"
            f"afade=t=out:st={max(dur - fade, 0):.3f}:d={fade}[m];"
            f"[1:a]asplit=2[vo][key];"
            f"[m][key]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=400[duck];"
            f"[vo][duck]amix=inputs=2:duration=first:normalize=0[a]")


# ---------- visuals ----------

def wrap_px(text, font, max_w):
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if not cur or font.getlength(trial) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def fit_text(text, font_path, w, h, min_size=12):
    """Largest font size whose wrapped text fits in the card's safe area (never truncates)."""
    from PIL import Image, ImageDraw, ImageFont
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    max_w, max_h = w * 0.84, h * 0.8
    for size in range(min(w, h) // 16, min_size - 1, -2):
        font = ImageFont.truetype(str(font_path), size)
        lines = wrap_px(text, font, max_w)
        wrapped = "\n".join(lines)
        box = measure.multiline_textbbox((0, 0), wrapped, font=font, spacing=size // 2)
        if box[2] - box[0] <= max_w and box[3] - box[1] <= max_h:
            break
    return font, wrapped, size


def title_card(text, out, w, h, font_path):
    from PIL import Image, ImageDraw
    font, wrapped, size = fit_text(text, font_path, w, h)
    img = Image.new("RGB", (w, h), (18, 24, 38))
    ImageDraw.Draw(img).multiline_text((w / 2, h / 2), wrapped, font=font, fill=(240, 240, 240),
                                       anchor="mm", align="center", spacing=size // 2)
    img.save(out)
    return out


def render_scene(i, src, dur, work, w, h):
    out = work / f"scene_{i:03}.mp4"
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


def render_clip(i, clip, dur, work, w, h, fill):
    """An AI clip, upscaled with lanczos and centre-cropped, filled out to the narration length."""
    out = work / f"scene_{i:03}.mp4"
    fit = f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h},setsar=1"
    plan = video_job.clip_fill_plan(duration(clip), dur, fill)
    run(["ffmpeg", "-y", "-i", clip, "-filter_complex", video_job.clip_filter(plan, fit), "-map", "[v]",
         "-t", f"{dur:.3f}", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-pix_fmt", "yuv420p", "-r", str(FPS), out])
    return out


# ---------- captions ----------

class Word(NamedTuple):
    text: str
    start: float
    end: float


def srt_time(t):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def normalize(word):
    return re.sub(r"[^\w]", "", word.lower())


def script_tokens(text):
    """Script words; punctuation-only tokens such as dashes stick to the previous word."""
    tokens = []
    for t in text.split():
        if normalize(t) or not tokens:
            tokens.append(t)
        else:
            tokens[-1] += " " + t
    return tokens


def spread(tokens, start, end):
    """Share a time span between tokens in proportion to their length."""
    total = sum(len(t) for t in tokens)
    out, t0 = [], start
    for tok in tokens:
        t1 = t0 + (end - start) * len(tok) / total
        out.append(Word(tok, t0, t1))
        t0 = t1
    return out


def align_words(heard, script_text):
    """Keep Whisper's timings but spell captions the way the script does.
    Where the two can't be lined up, Whisper's own words are kept."""
    heard = [w for w in heard if normalize(w.text)]
    tokens = script_tokens(script_text)
    if not heard or not tokens:
        return heard
    a, b = [normalize(w.text) for w in heard], [normalize(t) for t in tokens]
    out, lead = [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        n, m = i2 - i1, j2 - j1
        if tag == "equal" or (tag == "replace" and n == m):  # same words, or misheard 1:1
            out += [Word(tokens[j], heard[i].start, heard[i].end) for i, j in zip(range(i1, i2), range(j1, j2))]
        elif tag == "replace" and max(n, m) <= 3 * min(n, m):  # e.g. "2026" heard as "twenty twenty six"
            out += spread(tokens[j1:j2], heard[i1].start, heard[i2 - 1].end)
        elif tag in ("replace", "delete"):  # can't line up: fall back to what Whisper heard
            out += heard[i1:i2]
        elif out:  # insert: script words Whisper missed ride along with the previous word
            prev = out[-1]
            out[-1] = Word(" ".join([prev.text, *tokens[j1:j2]]), prev.start, prev.end)
        else:
            lead += tokens[j1:j2]
    if lead and out:
        out[0] = Word(" ".join([*lead, out[0].text]), out[0].start, out[0].end)
    return out


def ends_clause(word):
    return word.text.rstrip("\"')”’").endswith((".", "!", "?", ",", ":", ";"))


def chunk_words(words, max_words):
    """Group words into caption lines, breaking at punctuation or max_words.
    A line may run one word over max_words rather than leave a lone word like "studio." behind."""
    chunks, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        if ends_clause(w) or len(cur) > max_words:
            chunks.append(cur)
            cur = []
        elif len(cur) == max_words and not (nxt and ends_clause(nxt)):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def write_srt(chunks, out):
    out.write_text("".join(
        f"{n}\n{srt_time(c[0].start)} --> {srt_time(c[-1].end)}\n{' '.join(w.text for w in c)}\n\n"
        for n, c in enumerate(chunks, 1)), encoding="utf-8")
    return out


def make_captions(audio, out, script_text, model_size, max_words):
    from faster_whisper import WhisperModel
    import numpy as np
    # decode with ffmpeg ourselves: faster-whisper's PyAV decoder breaks on newer av releases
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(audio), "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
                         capture_output=True, check=True).stdout
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    # no initial_prompt: feeding the script itself makes whisper skip the opening words
    segments, _ = model.transcribe(np.frombuffer(pcm, np.float32), word_timestamps=True, language="en")
    heard = [Word(w.word.strip(), w.start, w.end) for seg in segments for w in seg.words]
    return write_srt(chunk_words(align_words(heard, script_text), max_words), out)


# ---------- per-format render ----------

def render_format(fmt, scenes, work, narration, srt, font_path, args, out):
    spec = FORMATS[fmt]
    w, h = spec["size"]
    fwork = work / fmt
    fwork.mkdir()
    print(f"Rendering {fmt} ({w}x{h})...")
    for i, s in enumerate(scenes):
        if s.get("clip"):
            render_clip(i, s["clip"], s["dur"], fwork, w, h, args.clip_fill)
            continue
        src = s["src"] or title_card(s["text"], fwork / f"card_{i:03}.png", w, h, font_path)
        render_scene(i, src, s["dur"], fwork, w, h)
    (fwork / "video.txt").write_text("".join(f"file 'scene_{i:03}.mp4'\n" for i in range(len(scenes))))
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "video.txt", "-c", "copy", "video.mp4"], cwd=fwork)

    vf = []
    if srt:
        from PIL import ImageFont
        family, style = ImageFont.truetype(str(font_path), 10).getname()
        # relative paths sidestep ffmpeg's filter escaping of Windows drive letters and spaces
        shutil.copy(srt, fwork / "captions.srt")
        (fwork / "fonts").mkdir()
        shutil.copy(font_path, fwork / "fonts")
        vf = ["-vf", f"subtitles=captions.srt:fontsdir=fonts:force_style='FontName={family},"
                     f"Bold={int('Bold' in style)},FontSize={spec['font_size']},"
                     f"PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,"
                     f"Shadow=0,MarginV={spec['margin']}'"]

    inputs = ["-i", "video.mp4", "-i", str(narration)]
    if args.music:
        inputs += ["-stream_loop", "-1", "-i", str(args.music)]
        audio = ["-filter_complex", music_filter(duration(narration), args.music_volume),
                 "-map", "0:v", "-map", "[a]"]
    else:
        audio = ["-map", "0:v", "-map", "1:a"]
    run(["ffmpeg", "-y", *inputs, *audio, *vf, "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
         str(out)], cwd=fwork)
    return out


# ---------- main ----------

PATH_OPTIONS = {"font", "music", "output"}


def load_config(ap):
    """Defaults from make_video.toml ([defaults] table), then VIDEO_STYLE from .env or the environment.
    Command-line options still win. Returns {option_dest: value}."""
    import tomllib
    known = {a.dest for a in ap._actions}
    defaults = {}
    if CONFIG_FILE.exists():
        try:
            data = tomllib.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            sys.exit(f"{CONFIG_FILE.name}: {e}")
        for key, value in data.get("defaults", data).items():
            dest = key.replace("-", "_")
            if dest not in known or dest in ("script", "help"):
                print(f"Note: {CONFIG_FILE.name}: unknown option '{key}' ignored")
                continue
            defaults[dest] = Path(value) if dest in PATH_OPTIONS else value
    ai_visuals.load_env()
    if os.environ.get("VIDEO_STYLE", "").strip():
        defaults["style"] = os.environ["VIDEO_STYLE"].strip()
    return defaults


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("script", type=Path)
    ap.add_argument("-o", "--output", type=Path,
                    help="base name for outputs, e.g. Exports\\promo -> promo_horizontal.mp4 "
                         "(default: Exports\\<script name>)")
    ap.add_argument("--format", choices=["both", *FORMATS], default="both",
                    help="which video(s) to render (default: both)")
    ap.add_argument("--vertical", action="store_true", help=argparse.SUPPRESS)  # deprecated
    ap.add_argument("--engine", choices=["edge", "kokoro"], default="edge",
                    help="edge = Microsoft neural voices (online); kokoro = fully offline")
    ap.add_argument("--voice", help="edge default en-US-AriaNeural; kokoro default af_heart")
    ap.add_argument("--rate", default="+0%", help="speaking rate, e.g. +10%% or -5%%")
    ap.add_argument("--music", type=Path, help="background music, ducked under the voice and faded out")
    ap.add_argument("--music-volume", type=float, default=0.2, help="music level between sentences")
    ap.add_argument("--font", type=Path, default=DEFAULT_FONT,
                    help="TTF/OTF for captions and title cards (default: bundled Noto Sans Bold)")
    ap.add_argument("--generate-visuals", action="store_true",
                    help="AI images for scenes without a [visual:] line (see --image-provider)")
    ap.add_argument("--image-provider", choices=ai_visuals.PROVIDERS, default="auto",
                    help="auto = Cloudflare, then Pollinations once Cloudflare's daily allowance is used up; "
                         "kaggle = images made by the Kaggle notebook and imported with --clips")
    ap.add_argument("--text-model", default=ai_visuals.TEXT_MODEL,
                    help="Workers AI model that writes image and motion prompts")
    ap.add_argument("--style", default="", help='appended to every image prompt, e.g. "cinematic, warm tones"')
    ap.add_argument("--prompts-only", action="store_true",
                    help="write Exports/<name>_prompts.json for review, then stop (no images, audio or video)")
    ap.add_argument("--regenerate", action="store_true",
                    help="ignore cached images and saved prompts for this run (rewrites your edited prompts)")
    ap.add_argument("--new-images", action="store_true",
                    help="keep the saved prompts but draw every image again with a new random seed "
                         "(the new images replace the cached ones)")
    ap.add_argument("--image-model", default=ai_visuals.IMAGE_MODEL, help="Workers AI image model ID")
    ap.add_argument("--video-scenes", default="",
                    help='scene numbers to make into AI video clips, e.g. "3,7,12" (same as a [video] line)')
    ap.add_argument("--export-video-job", action="store_true",
                    help="write Exports/<name>_video_job.zip for the Kaggle notebook, then stop")
    ap.add_argument("--clips", type=Path, help="clips.zip (or its folder) downloaded from the Kaggle notebook")
    ap.add_argument("--clip-fill", choices=video_job.FILL_MODES, default="pingpong",
                    help="how a clip shorter than its narration fills the scene (default: pingpong)")
    ap.add_argument("--kaggle-video", action="store_true",
                    help="the one-command route: AI images for every scene, Kaggle clips for [video] scenes "
                         "(skipped if there are none), then render (Kaggle needs KAGGLE_API_TOKEN in .env)")
    ap.add_argument("--kaggle-resume", action="store_true",
                    help="continue an interrupted --kaggle-video run without redoing finished clips")
    ap.add_argument("--kaggle-size", choices=list(video_job.VIDEO_SIZES), default="960x544",
                    help="Wan 2.2 clip size on Kaggle (default 960x544, which fits a T4; 1280x704 is full 720p)")
    ap.add_argument("--kaggle-hours", type=float,
                    help="hard time limit per Kaggle run (default: ~45 min + 1.5 h per clip, at most 12)")
    ap.add_argument("--no-captions", action="store_true")
    ap.add_argument("--whisper-model", default="base", help="tiny/base/small/medium")
    ap.add_argument("--caption-words", type=int, default=6, help="max words per caption line")
    ap.add_argument("--keep-work", action="store_true", help="keep intermediate files")
    ap.add_argument("--check", action="store_true",
                    help="check the script and estimate length, Cloudflare neurons and Kaggle GPU time, then stop "
                         "(no audio, images or network)")
    ap.set_defaults(**load_config(ap))
    args = ap.parse_args(argv)
    if args.vertical:
        print("Note: --vertical is deprecated; use --format vertical.")
        args.format = "vertical"
    args.generate_visuals |= args.prompts_only
    args.kaggle_video |= args.kaggle_resume
    if args.kaggle_video:
        if args.clips or args.export_video_job:
            ap.error("--kaggle-video downloads its own clips; don't combine it with --clips or --export-video-job")
        args.generate_visuals = True  # the scenes around the clips need their still images
    return args


def main():
    args = parse_args()
    script = args.script.resolve()
    if not script.exists():
        sys.exit(f"Script not found: {script}")
    if args.check:
        import script_check
        script_check.report(script, args)
        return
    check_ffmpeg()
    font_path = args.font.resolve()
    if not font_path.exists():
        sys.exit(f"Font not found: {font_path}")
    if args.music:
        args.music = args.music.resolve()
        if not args.music.exists():
            sys.exit(f"Music file not found: {args.music}")
    if args.engine == "kokoro":
        check_kokoro_model()

    scenes = parse_script(script)
    if not scenes:
        sys.exit("Script has no text.")
    video_job.apply_video_scenes(scenes, video_job.parse_scene_numbers(args.video_scenes))
    assign_visuals(scenes, script.parent, use_pool=not (args.generate_visuals or args.export_video_job))

    base = output_base(args.output, script)
    base.parent.mkdir(parents=True, exist_ok=True)
    providers = None
    if args.generate_visuals or args.export_video_job:
        providers = ai_visuals.Providers(args.image_provider, args.image_model, args.text_model,
                                         new_images=args.new_images)
    if args.export_video_job:
        video_job.export_job(scenes, base, providers, style=args.style, regenerate=args.regenerate,
                             video_size=args.kaggle_size, characters=parse_characters(script))
        return
    video_scenes = [i + 1 for i, s in enumerate(scenes) if s.get("video") and not s["visual"]]
    if args.kaggle_video and not video_scenes:
        # one command for every script: the [video] tags decide whether Kaggle is needed
        print("No [video] scenes in this script, so nothing goes to Kaggle: making images and rendering locally.")
    elif args.kaggle_video:
        print(f"Video scenes {', '.join(map(str, video_scenes))} go to Kaggle; the rest get AI images.")
        args.clips = kaggle_runner.run(scenes, base, providers, style=args.style, resume=args.kaggle_resume,
                                       hours=args.kaggle_hours, video_size=args.kaggle_size,
                                       characters=parse_characters(script))
        if args.clips is None:
            return

    if args.clips:
        n_clips, n_images = video_job.import_clips(args.clips.resolve(), scenes, base.parent / f".clips_{base.name}")
        print(f"Imported {n_clips} clip(s) and {n_images} image(s) from {args.clips}")
        missing = [i + 1 for i, s in enumerate(scenes) if s.get("video") and not s.get("clip")]
        if missing:
            print(f"  no clip for scene(s) {', '.join(map(str, missing))}: they keep their still image")
    failed_visuals = []
    if args.generate_visuals:
        failed_visuals = ai_visuals.generate_scene_visuals(
            scenes, base.with_name(base.name + "_prompts.json"), model=args.image_model,
            style=args.style, regenerate=args.regenerate, prompts_only=args.prompts_only,
            provider=args.image_provider, providers=providers, characters=parse_characters(script),
            only={i for i, s in enumerate(scenes) if not s.get("clip")})
        if args.prompts_only:
            print("Prompts only: review and edit the file above, then rerun with --generate-visuals.")
            return
    elif args.regenerate:
        print("Note: --regenerate only applies with --generate-visuals.")
    work = base.parent / f".work_{base.name}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()

    print(f"{len(scenes)} scenes. Generating voice over ({args.engine})...")
    narration = narrate(scenes, work, args)

    srt = None
    if not args.no_captions:
        print(f"Transcribing captions (whisper {args.whisper_model})...")
        srt = make_captions(narration, work / "captions.srt", " ".join(s["text"] for s in scenes),
                            args.whisper_model, args.caption_words)
        shutil.copy(srt, base.with_name(base.name + ".srt"))

    formats = list(FORMATS) if args.format == "both" else [args.format]
    outs = [render_format(fmt, scenes, work, narration, srt, font_path, args,
                          base.with_name(f"{base.name}_{fmt}.mp4")) for fmt in formats]

    if not args.keep_work:
        shutil.rmtree(work, ignore_errors=True)
    print("Done:")
    for out in outs:
        print(f"  {out} ({duration(out):.1f}s)")
    if srt:
        print(f"  {base.with_name(base.name + '.srt')}")
    if failed_visuals:
        print(f"Image generation failed for scene(s) {', '.join(map(str, failed_visuals))}; "
              "they use title cards. Rerun later to retry (successful images are cached).")


if __name__ == "__main__":
    main()
