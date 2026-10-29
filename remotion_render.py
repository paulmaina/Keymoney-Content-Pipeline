"""--renderer remotion: polished motion from still images, rendered locally with Remotion (remotion/).

Python decides everything (timing, camera moves, parallax masks, captions, places) and writes props.json; the React
composition in remotion/src only draws it. Remotion renders silent video; the voice and music are mixed with ffmpeg
exactly as the ffmpeg renderer does, so both renderers sound the same.

  - Image scenes get a slow pan or zoom with eased motion, never the same move twice in a row, and a subtle 2.5D
    parallax when the depth model finds a clear foreground (see depth.py). [video] scenes get a stronger move.
    Each format uses its own 16:9 or 9:16 image when the image model drew one. Scenes crossfade into each other,
    centred on the narration boundary.
  - Captions pop in line by line with the spoken word in yellow; a label names each new place, and a world map drops a
    pin the first time each country comes up (see places.py).
  - A 2-second branded intro and a 3.5-second outro (--no-branding skips them).
"""
import json
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

import make_video as mv

PROJECT = mv.ROOT / "remotion"
SPEED_FILE = PROJECT / ".render_speed.json"  # seconds per frame measured on this PC, for the estimate
FALLBACK_SPEED = {"horizontal": 0.12, "vertical": 0.12}  # first-run guess for a 4-core laptop CPU
TRANSITION = 16  # frames of crossfade between scenes (even, so it centres on the cut)
INTRO, OUTRO = 60, 105  # frames
TAGLINE = "Clever ideas from the world's villages"
LABEL_DELAY = 0.3  # seconds after a scene starts before its place label
CAPTION_HOLD = 0.5  # seconds a caption line stays after its last word when there's a gap

# camera moves: (scale, x %, y %) at the start and the end. Offsets are clamped so the picture always covers the frame.
MOVES = {
    "zoom-in": ((1.0, 0, 0), (1.10, 0, 0)),
    "zoom-out": ((1.10, 0, 0), (1.0, 0, 0)),
    "pan-left": ((1.10, 4, 0), (1.10, -4, 0)),
    "pan-right": ((1.10, -4, 0), (1.10, 4, 0)),
    "tilt-up": ((1.10, 0, -3.5), (1.10, 0, 3.5)),
    "tilt-down": ((1.10, 0, 3.5), (1.10, 0, -3.5)),
    "push-left": ((1.02, 1.5, 0), (1.13, -2.5, 0)),
    "push-right": ((1.02, -1.5, 0), (1.13, 2.5, 0)),
}
PARALLAX_MOVES = ("zoom-in", "push-left", "push-right", "zoom-out")  # depth reads best when the camera moves in or out
STRONG = 1.9  # [video] scenes: this much more zoom and travel


def node_bin():
    """Folder holding node.exe / npx, or None. Checks PATH, then the default Windows install."""
    found = shutil.which("npx") or shutil.which("npx.cmd")
    if not found and os.name == "nt":
        mv.refresh_windows_path()
        found = shutil.which("npx") or shutil.which("npx.cmd")
        if not found and (Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs" / "npx.cmd").exists():
            found = str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs" / "npx.cmd")
    return Path(found).parent if found else None


def available():
    """(True, None) when Remotion can render, else (False, why)."""
    if not (PROJECT / "package.json").exists():
        return False, f"{PROJECT} is missing"
    if not node_bin():
        return False, "Node.js isn't installed (winget install OpenJS.NodeJS.LTS)"
    return True, None


def npx(*args, cwd=PROJECT, capture=False):
    bin_dir = node_bin()
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    cmd = [str(bin_dir / ("npx.cmd" if os.name == "nt" else "npx")), *map(str, args)]
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=capture, text=True)
    if r.returncode != 0:
        sys.exit(f"Remotion failed: {' '.join(cmd[1:4])} ...\n{(r.stderr or '')[-3000:]}")
    return r.stdout


def ensure_installed():
    if (PROJECT / "node_modules" / "remotion").exists():
        return
    print("First Remotion run: installing its packages into remotion/node_modules (about 250 MB, once)...")
    bin_dir = node_bin()
    npm = bin_dir / ("npm.cmd" if os.name == "nt" else "npm")
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    r = subprocess.run([str(npm), "ci", "--no-audit", "--no-fund"], cwd=PROJECT, env=env)
    if r.returncode != 0:
        sys.exit("npm ci failed in remotion/; check your internet connection and run it there by hand.")


# ---------- planning (pure, tested) ----------

def safe_offset(scale, img_aspect, frame_w, frame_h, axis):
    """Largest offset (% of the frame) on an axis that still keeps the cover-scaled picture over the whole frame."""
    if img_aspect > frame_w / frame_h:
        w, h = frame_h * img_aspect, frame_h
    else:
        w, h = frame_w, frame_w / img_aspect
    size, frame = (w, frame_w) if axis == "x" else (h, frame_h)
    return max((size * scale - frame) / 2 / frame * 100, 0)


def plan_moves(scenes, seed, has_mask=()):
    """A move name per scene: varied, never the same twice in a row, pushes for scenes with parallax."""
    rng = random.Random(seed)
    names, prev = [], None
    for i, _ in enumerate(scenes):
        pool = [m for m in (PARALLAX_MOVES if i in has_mask else MOVES) if m != prev]
        # avoid repeating the move from two scenes back too, when there's a choice
        if len(names) >= 2 and len(pool) > 1:
            pool = [m for m in pool if m != names[-2]] or pool
        prev = rng.choice(pool)
        names.append(prev)
    return names


def move_props(name, strong, img_aspect, frame_w, frame_h):
    """The move's start and end, stronger for [video] scenes, with offsets clamped to what the picture can cover."""
    a, b = MOVES[name]
    k = STRONG if strong else 1.0

    def point(p):
        scale = 1 + (p[0] - 1) * k
        x = p[1] * k
        y = p[2] * k
        lim_x = safe_offset(scale, img_aspect, frame_w, frame_h, "x")
        lim_y = safe_offset(scale, img_aspect, frame_w, frame_h, "y")
        return {"scale": round(scale, 4), "x": round(max(-lim_x, min(lim_x, x)), 3),
                "y": round(max(-lim_y, min(lim_y, y)), 3)}
    return {"name": name + (" (strong)" if strong else ""), "from": point(a), "to": point(b)}


def timeline(durations, fps, intro, outro, transition=TRANSITION):
    """Frame layout of the whole video. Returns (lead_in frames: where the narration starts, scene sequence
    lengths, scene start frames on the final timeline, total frames). Each crossfade is centred on its narration
    boundary, so every scene appears exactly when its words start."""
    half = transition // 2
    lead = intro - half if intro else half
    bounds, t = [], 0.0
    for d in durations:
        bounds.append(lead + round(t * fps))
        t += d
    end = lead + round(t * fps)
    edges = bounds + [end]
    lengths = [edges[i + 1] - edges[i] + transition for i in range(len(durations))]
    if not outro:
        lengths[-1] -= half  # the last scene ends the video with its words instead of fading into the outro
    total = end + (outro - half if outro else 0)
    return lead, lengths, bounds, total


def caption_lines(chunks, fps, lead):
    """Caption lines on the final timeline, in frames, each held a moment after its last word unless the next starts."""
    lines = []
    for i, chunk in enumerate(chunks):
        start = lead + round(chunk[0].start * fps)
        end = lead + round((chunk[-1].end + CAPTION_HOLD) * fps)
        if i + 1 < len(chunks):
            end = min(end, lead + round(chunks[i + 1][0].start * fps))
        words = [{"text": w.text, "start": lead + round(w.start * fps), "end": lead + round(w.end * fps)} for w in chunk]
        lines.append({"start": start, "end": max(end, start + 1), "words": words})
    return lines


def place_props(scenes, script, bounds, fps):
    import places
    out = []
    for i, place, show_map, visited in places.scene_places(scenes, script):
        out.append({"frame": bounds[i] + round(LABEL_DELAY * fps), "label": place.label, "country": place.country,
                    "lat": place.lat, "lon": place.lon, "showMap": show_map, "visited": visited})
    return out


# ---------- assets ----------

def prepare_assets(scenes, public, args, fmt, state):
    """Copy each scene's picture for one format (and its parallax mask) into the public dir. Returns per-scene asset
    dicts. Files both formats share are copied once; `state` carries that and the parallax switch between formats."""
    from PIL import Image
    public.mkdir(parents=True, exist_ok=True)
    copied = state.setdefault("copied", {})
    assets = []
    for i, s in enumerate(scenes):
        src = mv.scene_src(s, fmt)
        if src and Path(src).suffix.lower() in mv.VIDEO_EXT:  # own footage: a still frame keeps the motion consistent
            if src not in copied:
                frame = public / f"scene_{i:03}.png"
                mv.run(["ffmpeg", "-y", "-ss", f"{min(1.0, mv.duration(src) / 2):.2f}", "-i", src, "-frames:v", "1",
                        frame])
                copied[src] = {"file": frame.name, "image": frame}
            src = copied[src]["image"]
        if not src:
            assets.append({"kind": "card"})
            continue
        if src not in copied:
            dest = public / f"scene_{i:03}_{fmt}{Path(src).suffix.lower()}"
            shutil.copy(src, dest)
            copied[src] = {"file": dest.name, "image": src}
        entry = copied[src]
        with Image.open(public / entry["file"]) as img:
            asset = {"kind": "image", "src": entry["file"], "width": img.width, "height": img.height}
        if "mask" not in entry and not args.no_parallax and state.get("parallax", True):
            import depth
            try:
                mask = depth.foreground_mask(entry["image"])
            except Exception as e:  # no model download, odd image: plain motion is fine
                print(f"  parallax skipped for scene {i + 1} ({e.__class__.__name__}: {e})")
                state["parallax"] = False
                mask = None
            entry["mask"] = None
            if mask:
                entry["mask"] = Path(entry["file"]).stem + "_fg.png"
                shutil.copy(mask, public / entry["mask"])
        if entry.get("mask"):
            asset["mask"] = entry["mask"]
        assets.append(asset)
    return assets


# ---------- estimate ----------

def load_speed():
    try:
        return {**FALLBACK_SPEED, **json.loads(SPEED_FILE.read_text())}, True
    except (OSError, ValueError):
        return dict(FALLBACK_SPEED), False


def save_speed(fmt, seconds_per_frame):
    speed, _ = load_speed()
    speed[fmt] = round(seconds_per_frame, 4)
    try:
        SPEED_FILE.write_text(json.dumps(speed))
    except OSError:
        pass


def estimate(frames, formats):
    speed, measured = load_speed()
    secs = sum(frames * speed[f] for f in formats)
    return secs, measured


def minutes(secs):
    return f"{secs / 60:.0f} min" if secs >= 90 else f"{secs:.0f} s"


# ---------- render ----------

def concurrency():
    return max(1, (os.cpu_count() or 4) // 2)


def build_props(fmt, scenes, assets, moves, lengths, bounds, total, lines, places_, args, fps=mv.FPS):
    w, h = mv.FORMATS[fmt]["size"]
    props_scenes = []
    for s, a, name, length in zip(scenes, assets, moves, lengths):
        aspect = (a.get("width") or 1) / (a.get("height") or 1)
        strong = bool(s.get("video"))
        props_scenes.append({**a, "frames": length, "text": s["text"] if a["kind"] == "card" else None,
                             "move": move_props(name, strong, aspect, w, h)})
    return {"width": w, "height": h, "fps": fps, "durationInFrames": total, "transitionFrames": TRANSITION,
            "introFrames": 0 if args.no_branding else INTRO, "outroFrames": 0 if args.no_branding else OUTRO,
            "font": "font.ttf", "channel": args.channel, "tagline": TAGLINE, "scenes": props_scenes,
            "captions": lines, "places": places_}


def render_all(scenes, work, narration, chunks, font_path, args, formats, base, script):
    """Render every format with Remotion. Returns (output paths, seconds before the narration starts, total seconds)."""
    ensure_installed()
    fps = mv.FPS
    intro, outro = (0, 0) if args.no_branding else (INTRO, OUTRO)
    lead, lengths, bounds, total = timeline([s["dur"] for s in scenes], fps, intro, outro)
    rwork = work / "remotion"
    public = rwork / "public"
    rwork.mkdir(parents=True, exist_ok=True)
    public.mkdir(parents=True, exist_ok=True)
    shutil.copy(font_path, public / "font.ttf")
    print("Preparing Remotion assets...")
    state, assets, moves = {}, {}, {}
    for fmt in formats:
        assets[fmt] = prepare_assets(scenes, public, args, fmt, state)
        moves[fmt] = plan_moves(scenes, script.stem, {i for i, a in enumerate(assets[fmt]) if a.get("mask")})
    lines = caption_lines(chunks, fps, lead) if chunks else []
    places_ = place_props(scenes, script, bounds, fps)
    parallax = " / ".join(f"{sum(bool(a.get('mask')) for a in assets[f])} {f}" for f in formats)
    print(f"  {len(scenes)} scenes, parallax on {parallax}, {len(places_)} place label(s), "
          f"{sum(p['showMap'] for p in places_)} map pin(s)")
    secs, measured = estimate(total, formats)
    print(f"Remotion render: {total} frames per format; estimated ~{minutes(secs)} for {' + '.join(formats)} "
          f"({'measured on this PC' if measured else 'first-run guess; the next estimate uses your real speed'}, "
          f"{concurrency()} parallel frames)")

    print("Bundling the Remotion project...")
    bundle = rwork / "bundle"
    npx("remotion", "bundle", "src/index.ts", f"--public-dir={public}", f"--out-dir={bundle}", "--log=error", capture=True)

    full_audio = timeline_audio(narration, lead / fps, total / fps, rwork)
    outs = []
    for fmt in formats:
        props = rwork / f"props_{fmt}.json"
        props.write_text(json.dumps(build_props(fmt, scenes, assets[fmt], moves[fmt], lengths, bounds, total, lines,
                                                places_, args),
                                    ensure_ascii=False), encoding="utf-8")
        silent = rwork / f"{fmt}_silent.mp4"
        w, h = mv.FORMATS[fmt]["size"]
        print(f"Rendering {fmt} ({w}x{h}) with Remotion...")
        t0 = time.time()
        npx("remotion", "render", bundle, "Main", silent, f"--props={props}", "--codec=h264", "--crf=18",
            "--muted", f"--concurrency={concurrency()}", "--gl=angle", "--log=warn")
        save_speed(fmt, (time.time() - t0) / total)
        out = base.with_name(f"{base.name}_{fmt}.mp4")
        print(f"  {fmt}: {time.time() - t0:.0f} s; mixing the voice{' and music' if args.music else ''}...")
        outs.append(mv.mux(silent, full_audio, args, out, cwd=rwork, video_codec=["-c:v", "copy"]))
    return outs, lead / fps, total / fps


def timeline_audio(narration, lead, total, work):
    """The narration placed on the final timeline: silence for the intro, then the voice, padded to the outro's end."""
    out = work / "narration_timeline.wav"
    ms = round(lead * 1000)
    mv.run(["ffmpeg", "-y", "-i", narration, "-af", f"adelay={ms}|{ms},apad", "-t", f"{total:.3f}", out])
    return out
