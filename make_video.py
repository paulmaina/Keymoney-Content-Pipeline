"""Turn a text script into narrated videos with burned-in captions.

By default both formats are rendered from one narration and one caption track:
  Exports/<name>_horizontal.mp4   1920x1080 for YouTube / Facebook
  Exports/<name>_vertical_partN.mp4  1080x1920 Shorts, the script split into parts under 2:50 (shorts.py);
                                  a script that fits in one is a single <name>_vertical.mp4
  Exports/<name>.srt              caption file (same timings for both)
  Exports/<name>_youtube.md       title options, description with chapters, tags (--no-metadata skips)
  Exports/<name>_thumbnail.jpg    1280x720 thumbnail

Rendering (--renderer): remotion (default; remotion_render.py and remotion/) animates stills with eased pans, zooms and
subtle parallax, crossfades, animated captions, place labels, a world map and a branded intro/outro. ffmpeg is the fast,
simple renderer, and the fallback when Node.js isn't installed.

Script format (plain .txt):
  - Each paragraph (separated by a blank line) is one scene.
  - [video] at the start of a line gives that scene a stronger camera move (for action paragraphs).
  - [visual: myclip.mp4] at the start of a line sets that scene's footage or image
    (looked up in the "Raw Footage" folder, then next to the script).
  - "# character: Name = description" declares a character for consistent AI images.
  - "# media: ai" or "# media: stock" in a paragraph forces that scene's picture source (see --media).
  - Any other line starting with # is a comment: never spoken, captioned or used in prompts.
  Scenes without a visual reuse files from the footage folder in order,
  or fall back to a generated title card showing the scene text.

Usage:
  .venv\\Scripts\\python make_video.py Scripts\\demo.txt
  .venv\\Scripts\\python make_video.py Scripts\\demo.txt --format vertical --music "Raw Footage\\song.mp3"
  .venv\\Scripts\\python make_video.py Scripts\\demo.txt --engine kokoro --voice am_michael
  .venv\\Scripts\\python make_video.py Scripts\\demo.txt --generate-visuals --style "cinematic, warm tones"
  .venv\\Scripts\\python make_video.py Scripts\\demo.txt --generate-visuals --upscale   (sharper images via Kaggle)
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
MEDIA_LINE = re.compile(r"^#\s*media\s*:\s*(ai|stock)\s*$", re.I)


def parse_script(path):
    scenes = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig")):
        visual, lines, video, described, media = None, [], False, [], None
        for line in block.strip().splitlines():
            line = line.strip()
            if line.startswith("#"):
                if (m := MEDIA_LINE.match(line)):
                    media = m.group(1).lower()
                elif DESCRIPTION_LINE.match(line) and not CHARACTER_LINE.match(line):
                    described.append(line.lstrip("#").strip())
                continue  # never spoken or captioned; plain comments aren't used anywhere
            while (m := LEADING_TAG.match(line)):
                if m.group(1):
                    video = True  # an action scene: a stronger camera move
                else:
                    visual = m.group(2).strip()
                line = line[m.end():]
            if line:
                lines.append(line)
        if lines:
            scenes.append({"text": " ".join(lines), "visual": visual, "video": video,
                           "description": "; ".join(described), **({"media": media} if media else {})})
    return scenes


# "# HOOK", "# HACK 4 LADAKH, INDIA - Visuals: ...", "# OUTRO:": an all-caps section heading, used for YouTube chapters
SECTION_LINE = re.compile(r"^#\s*([A-Z][A-Z0-9&'/, ]*?[A-Z0-9])\s*(?:$|[-–—:])")
NOT_SECTIONS = {"NOTE", "TODO", "FIXME", "TIP"}


def parse_sections(path):
    """Section heading of each scene (parallel to parse_script), or None before the first heading.
    A heading applies to its own paragraph and every later one until the next heading."""
    sections, current = [], None
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig")):
        spoken = False
        for line in block.strip().splitlines():
            line = line.strip()
            if line.startswith("#"):
                if ((m := SECTION_LINE.match(line)) and len(m.group(1)) >= 3
                        and m.group(1).split()[0] not in NOT_SECTIONS):
                    current = " ".join(m.group(1).split())
                continue
            while (m := LEADING_TAG.match(line)):
                line = line[m.end():]
            spoken |= bool(line)
        if spoken:
            sections.append(current)
    return sections


def parse_comments(path):
    """(line number, text) of every plain '#' comment the script skips: not character, description, media or
    location lines."""
    from places import LOCATION_LINE
    used = (CHARACTER_LINE, DESCRIPTION_LINE, MEDIA_LINE, LOCATION_LINE)
    return [(n, line.strip()) for n, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1)
            if line.strip().startswith("#") and not any(r.match(line.strip()) for r in used)]


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


def chatterbox_or_fallback(scenes, args):
    """Chatterbox lines from Kaggle ({index: wav}), or None after switching args to the Edge fallback voice."""
    import chatterbox_tts
    lines = chatterbox_tts.narrate_lines(scenes, args)
    if lines is None:
        args.engine = "edge"
        if not (args.voice or "").endswith("Neural"):
            args.voice = chatterbox_tts.FALLBACK_VOICE
        print(f"  Falling back to the Edge voice {args.voice or chatterbox_tts.FALLBACK_VOICE} for the whole video "
              "(never two voices in one video).")
    return lines


def narrate(scenes, work, args):
    made = chatterbox_or_fallback(scenes, args) if args.engine == "chatterbox" else None
    speed = 1 + int(args.rate.rstrip("%")) / 100
    for i, s in enumerate(scenes):
        raw = work / f"vo_{i:03}.{'mp3' if args.engine == 'edge' else 'wav'}"
        tempo = ""
        if made:  # Chatterbox has no speed setting, so --rate is applied afterwards (pitch kept)
            raw = made[i]
            tempo = f"atempo={min(2.0, max(0.5, speed)):.3f}," if abs(speed - 1) > 1e-3 else ""
        elif args.engine == "edge":
            tts_edge(s["text"], raw, args.voice or "en-US-AriaNeural", args.rate)
        else:
            tts_kokoro(s["text"], raw, args.voice or "af_heart", speed)
        wav = work / f"vo_{i:03}_pad.wav"
        run(["ffmpeg", "-y", "-i", raw, "-af", f"{tempo}apad=pad_dur={PAUSE}", "-ar", "48000", "-ac", "2", wav])
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


def render_scene(i, src, dur, work, w, h, strong=False):
    out = work / f"scene_{i:03}.mp4"
    fit = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"
    if src.suffix.lower() in VIDEO_EXT:
        inp = ["-stream_loop", "-1", "-i", src]
        vf = f"{fit},fps={FPS}"
    else:  # still image: slow Ken Burns zoom ([video] scenes zoom further)
        frames = int(dur * FPS) + 1
        step, top = (0.0011, 1.22) if strong else (0.0006, 1.12)
        inp = ["-loop", "1", "-framerate", str(FPS), "-i", src]
        vf = (f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2},"
              f"zoompan=z='min(zoom+{step},{top})':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2'"
              f":d={frames}:s={w}x{h}:fps={FPS},setsar=1")
    run(["ffmpeg", "-y", *inp, "-t", f"{dur:.3f}", "-vf", vf, "-an", "-c:v", "libx264",
         "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS), out])
    return out


def scene_src(s, fmt):
    """The scene's picture for one format: its own 16:9 or 9:16 image when the model drew one."""
    return (s.get("srcs") or {}).get(fmt) or s.get("src")


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


def shift_srt(srt, seconds, out):
    """Copy an .srt to out with every cue moved later by seconds (the Remotion intro)."""
    def move(m):
        h, mi, s, ms = map(int, m.groups())
        return srt_time(h * 3600 + mi * 60 + s + ms / 1000 + seconds)
    text = srt.read_text(encoding="utf-8")
    if seconds:
        text = re.sub(r"(\d\d):(\d\d):(\d\d),(\d\d\d)", move, text)
    out.write_text(text, encoding="utf-8")
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
    chunks = chunk_words(align_words(heard, script_text), max_words)
    return write_srt(chunks, out), chunks


# ---------- per-format render ----------

def render_format(fmt, scenes, work, narration, srt, font_path, args, out):
    spec = FORMATS[fmt]
    w, h = spec["size"]
    fwork = work / fmt
    fwork.mkdir()
    print(f"Rendering {fmt} ({w}x{h})...")
    for i, s in enumerate(scenes):
        src = scene_src(s, fmt) or title_card(s["text"], fwork / f"card_{i:03}.png", w, h, font_path)
        render_scene(i, src, s["dur"], fwork, w, h, strong=bool(s.get("video")))
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

    return mux("video.mp4", narration, args, out, cwd=fwork, filters=vf)


def cut_parts(video, scenes, parts, base):
    """--renderer ffmpeg: the vertical video cut into its Shorts parts at scene boundaries (re-encoded, so the cuts are
    exact); the full-length vertical file is removed."""
    import shorts
    starts = [sum(s["dur"] for s in scenes[:i]) for i in range(len(scenes) + 1)]
    outs = []
    for k, (a, b) in enumerate(parts, 1):
        out = shorts.part_path(base, k)
        run(["ffmpeg", "-y", "-ss", f"{starts[a]:.3f}", "-t", f"{starts[b] - starts[a]:.3f}", "-i", video,
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
             out])
        outs.append(out)
    video.unlink(missing_ok=True)
    return outs


def mux(video, narration, args, out, cwd, filters=(), video_codec=None):
    """Video + narration (+ ducked, faded music) -> out. Both renderers finish here, so they sound the same."""
    inputs = ["-i", str(video), "-i", str(narration)]
    if args.music:
        inputs += ["-stream_loop", "-1", "-i", str(args.music)]
        audio = ["-filter_complex", music_filter(duration(narration), args.music_volume),
                 "-map", "0:v", "-map", "[a]"]
    else:
        audio = ["-map", "0:v", "-map", "1:a"]
    codec = video_codec or ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p"]
    run(["ffmpeg", "-y", *inputs, *audio, *filters, *codec, "-c:a", "aac", "-b:a", "192k", "-shortest",
         "-movflags", "+faststart", str(out)], cwd=cwd)
    return out


# ---------- main ----------

PATH_OPTIONS = {"font", "music", "output", "chatterbox_voice"}


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
    ap.add_argument("--engine", choices=["edge", "kokoro", "chatterbox"], default="edge",
                    help="edge = Microsoft neural voices (online); kokoro = fully offline; chatterbox = Chatterbox on a "
                         "free Kaggle GPU (needs KAGGLE_API_TOKEN; falls back to Edge Emma if Kaggle fails)")
    ap.add_argument("--chatterbox-voice", type=Path,
                    help="chatterbox: a reference clip (6-15 s of clean speech) to speak in that voice, only one you have "
                         "the right to use; default: Chatterbox's built-in voice")
    ap.add_argument("--chatterbox-emotion", type=float, default=0.5,
                    help="chatterbox: emotion / exaggeration, 0.25 (flat) to 2.0 (theatrical); default 0.5")
    ap.add_argument("--chatterbox-cfg", type=float, default=0.5,
                    help="chatterbox: lower (~0.3) keeps the pace natural at high emotion; default 0.5")
    ap.add_argument("--voice", help="edge default en-US-AriaNeural; kokoro default af_heart")
    ap.add_argument("--rate", default="+0%", help="speaking rate, e.g. +10%% or -5%%")
    ap.add_argument("--music", type=Path, help="background music, ducked under the voice and faded out")
    ap.add_argument("--music-volume", type=float, default=0.2, help="music level between sentences")
    ap.add_argument("--font", type=Path, default=DEFAULT_FONT,
                    help="TTF/OTF for captions and title cards (default: bundled Noto Sans Bold)")
    # on/off options you may set in make_video.toml also get a --no-... form to turn them off for one run
    on_off = argparse.BooleanOptionalAction
    ap.add_argument("--generate-visuals", action=on_off, default=False,
                    help="AI images (or stock, see --media) for scenes without a [visual:] line (see --image-provider)")
    ap.add_argument("--media", choices=["mixed", "stock", "ai"], default="mixed",
                    help="with --generate-visuals: mixed = Pexels/Pixabay footage or photos where one matches well, "
                         "else AI images (default); stock = stock wherever anything relevant exists; ai = never stock. "
                         "'# media: ai' / '# media: stock' lines force a scene. --media stock turns on --generate-visuals")
    ap.add_argument("--stock", choices=["auto", "pexels", "pixabay"], default="auto",
                    help="stock provider: auto = every one with a key in .env (PEXELS_API_KEY, PIXABAY_API_KEY)")
    ap.add_argument("--image-provider", choices=ai_visuals.PROVIDERS, default="auto",
                    help="auto = Cloudflare, then Pollinations once Cloudflare's daily allowance is used up")
    ap.add_argument("--text-model", default=ai_visuals.TEXT_MODEL,
                    help="Workers AI model that writes the image prompts (~50 neurons a scene)")
    ap.add_argument("--style", default="", help='appended to every image prompt, e.g. "cinematic, warm tones"')
    ap.add_argument("--visuals-only", action="store_true",
                    help="make and cache the prompts and images (with their checks, stock and --upscale), then stop "
                         "before the voice and video: for scripts that need several days of Cloudflare allowance")
    ap.add_argument("--prompts-only", action="store_true",
                    help="write Exports/<name>_prompts.json for review, then stop (no images, audio or video)")
    ap.add_argument("--regenerate", action="store_true",
                    help="ignore cached images and saved prompts for this run (rewrites your edited prompts)")
    ap.add_argument("--new-images", action="store_true",
                    help="keep the saved prompts but draw every image again with a new random seed "
                         "(the new images replace the cached ones)")
    ap.add_argument("--redo-scenes", default="", metavar='"3,7"',
                    help="draw only these scenes' images again with a new seed; every other scene keeps its cached "
                         "images and prompts (turns on --generate-visuals)")
    ap.add_argument("--check-images", action=on_off, default=False,
                    help="look at every AI image with a Cloudflare vision model for extra or missing limbs, fingers or "
                         "faces and stray text; failures are redrawn with a new seed up to 2 times, then the best is kept "
                         "and flagged (~40 neurons a check)")
    ap.add_argument("--vision-model", default=ai_visuals.VISION_MODEL, help="Workers AI model for --check-images")
    ap.add_argument("--image-model", default=ai_visuals.IMAGE_MODEL,
                    help="Workers AI image model: a short name (" + ", ".join(ai_visuals.IMAGE_MODELS) +
                         ") or a full @cf/... ID (default flux-2-klein-4b, ~132 neurons an image, one per format)")
    ap.add_argument("--upscale", action=on_off, default=False,
                    help="sharpen scene images under 2048 px with Real-ESRGAN on a free Kaggle GPU, in one batch "
                         "(results are cached; needs KAGGLE_API_TOKEN in .env)")
    ap.add_argument("--renderer", choices=["remotion", "ffmpeg"], default="remotion",
                    help="remotion = eased pans and zooms, 2.5D parallax, crossfades, animated captions, place labels, "
                         "a world map and a branded intro/outro (needs Node.js; slower); ffmpeg = simple zooms, fast. "
                         "Falls back to ffmpeg when Node.js is missing")
    ap.add_argument("--no-branding", action="store_true", help="remotion: leave out the channel intro and outro")
    ap.add_argument("--no-parallax", action="store_true",
                    help="remotion: plain pans and zooms only (skips the depth model, ~1 s per image)")
    ap.add_argument("--caption-style", choices=["auto", "bold", "clean", "minimal"], default="auto",
                    help="remotion captions: bold = big words popping in 2-4 at a time; clean = one or two calm lines "
                         "with the spoken word highlighted; minimal = a small plain line. auto (default) = bold for "
                         "vertical, clean for horizontal")
    ap.add_argument("--no-captions", action="store_true")
    ap.add_argument("--whisper-model", default="base", help="tiny/base/small/medium")
    ap.add_argument("--caption-words", type=int, default=6, help="max words per caption line")
    ap.add_argument("--no-metadata", action="store_true",
                    help="don't write Exports/<name>_youtube.md (titles, description, chapters, tags) "
                         "and <name>_thumbnail.jpg after rendering")
    ap.add_argument("--metadata-model", default=ai_visuals.METADATA_MODEL,
                    help="Workers AI model that writes the YouTube titles, description and tags (~65 neurons)")
    ap.add_argument("--channel", default="Smart Village Boy", help="channel name used in the YouTube metadata")
    ap.add_argument("--keep-work", action="store_true", help="keep intermediate files")
    ap.add_argument("--check", action="store_true",
                    help="check the script and estimate length, Cloudflare neurons and render time, then stop "
                         "(no audio, images or network)")
    ap.set_defaults(**load_config(ap))
    args = ap.parse_args(argv)
    if args.vertical:
        print("Note: --vertical is deprecated; use --format vertical.")
        args.format = "vertical"
    args.redo = parse_scene_numbers(args.redo_scenes, ap)
    args.generate_visuals |= args.prompts_only or args.visuals_only or bool(args.redo) or args.media == "stock"
    return args


def parse_scene_numbers(text, ap=None):
    """{0-based index} from "3,7" (1-based, as --check and the log number scenes)."""
    try:
        return {int(n) - 1 for n in re.split(r"[,\s]+", text.strip()) if n} if text.strip() else set()
    except ValueError:
        (ap.error if ap else sys.exit)(f'--redo-scenes expects scene numbers like "3,7", got "{text}"')


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
    if args.renderer == "remotion":
        import remotion_render
        ok, why = remotion_render.available()
        if not ok:
            print(f"Note: the Remotion renderer isn't available ({why}); using --renderer ffmpeg.")
            args.renderer = "ffmpeg"

    scenes = parse_script(script)
    if not scenes:
        sys.exit("Script has no text.")
    assign_visuals(scenes, script.parent, use_pool=not args.generate_visuals)

    base = output_base(args.output, script)
    base.parent.mkdir(parents=True, exist_ok=True)
    formats = list(FORMATS) if args.format == "both" else [args.format]
    failed_visuals = []
    if args.generate_visuals:
        if (bad := sorted(n + 1 for n in args.redo if not 0 <= n < len(scenes))):
            sys.exit(f"--redo-scenes: there is no scene {', '.join(map(str, bad))} (the script has {len(scenes)})")
        if (own := sorted(n + 1 for n in args.redo if scenes[n]["visual"])):
            print(f"Note: scene(s) {', '.join(map(str, own))} use your own [visual:] file, so --redo-scenes skips them.")
        providers = ai_visuals.Providers(args.image_provider, args.image_model, args.text_model,
                                         new_images=args.new_images, vision_model=args.vision_model)
        plan = {}
        if not args.prompts_only:
            import script_check
            import stock
            plan = stock.fill_stock(scenes, formats, args.media, args.stock, providers,
                                    script_check.estimated_seconds(scenes, args), parse_characters(script))
        failed_visuals = ai_visuals.generate_scene_visuals(
            scenes, base.with_name(base.name + "_prompts.json"), style=args.style, regenerate=args.regenerate,
            prompts_only=args.prompts_only, provider=args.image_provider, providers=providers,
            characters=parse_characters(script), formats=formats, redo=args.redo, check=args.check_images)
        if args.prompts_only:
            print("Prompts only: review and edit the file above, then rerun with --generate-visuals.")
            return
        stock.write_media_json(scenes, plan, base.with_name(base.name + "_media.json"))
    elif args.regenerate:
        print("Note: --regenerate only applies with --generate-visuals.")
    if args.upscale:
        import upscale
        upscale.upscale_scenes(scenes, formats)
    if args.visuals_only:
        missing = f" Still missing: scene(s) {', '.join(map(str, failed_visuals))}." if failed_visuals else ""
        print(f"Visuals only: prompts and images are saved and cached; no voice or video was made.{missing} "
              "Run the same command without --visuals-only to render.")
        return
    work = base.parent / f".work_{base.name}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()

    print(f"{len(scenes)} scenes. Generating voice over ({args.engine})...")
    narration = narrate(scenes, work, args)

    srt, chunks = None, None
    if not args.no_captions:
        print(f"Transcribing captions (whisper {args.whisper_model})...")
        srt, chunks = make_captions(narration, work / "captions.srt", " ".join(s["text"] for s in scenes),
                                    args.whisper_model, args.caption_words)

    import shorts
    parts = shorts.plan(scenes, script, args) if "vertical" in formats else []
    if len(parts) > 1:
        print(f"The vertical video becomes a series of {len(parts)} Shorts:")
        for line in shorts.describe(parts, [s["dur"] for s in scenes], parse_sections(script), shorts.branding_of(args)):
            print(f"  {line}")
    lead_in, total = 0.0, duration(narration)
    if args.renderer == "remotion":
        import remotion_render
        outs, lead_in, total = remotion_render.render_all(scenes, work, narration, chunks, font_path, args, formats,
                                                          base, script, parts)
    else:
        outs = [render_format(fmt, scenes, work, narration, srt, font_path, args,
                              base.with_name(f"{base.name}_{fmt}.mp4")) for fmt in formats]
        if len(parts) > 1:  # the plain renderer has no intro or outro, so the parts are cut from the full video
            outs = [o for o in outs if o.name != f"{base.name}_vertical.mp4"] + cut_parts(
                base.with_name(f"{base.name}_vertical.mp4"), scenes, parts, base)
    if srt:  # the uploadable caption file, on the video's own timeline (after any intro)
        shift_srt(srt, lead_in, base.with_name(base.name + ".srt"))

    meta = None
    if not args.no_metadata:
        import youtube_meta
        try:
            meta = youtube_meta.write(scenes, script, base, args, work, total, lead_in=lead_in,
                                      parts=parts if len(parts) > 1 else None)
        except Exception as e:  # the videos are done; never lose them over the upload notes
            print(f"YouTube metadata failed ({e.__class__.__name__}: {e}); the videos are fine. "
                  "Rerun with --no-metadata to skip it.")

    if not args.keep_work:
        shutil.rmtree(work, ignore_errors=True)
    print("Done:")
    for out in outs:
        print(f"  {out} ({duration(out):.1f}s)")
    if srt:
        print(f"  {base.with_name(base.name + '.srt')}")
    for path in meta or []:
        print(f"  {path}")
    if failed_visuals:
        print(f"Image generation failed for scene(s) {', '.join(map(str, failed_visuals))}; "
              "they use title cards. Rerun later to retry (successful images are cached).")


if __name__ == "__main__":
    main()
