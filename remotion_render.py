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
# Places.tsx's vertical layout (shares of the frame, or px): the map card under the Shorts top bar, the label under it
MAP_TOP, MAP_WIDTH, MAP_ASPECT, LABEL_GAP_PX, LABEL_PX = 0.13, 0.8, 0.52, 34, 68
MAP_SECONDS, LABEL_SECONDS = 4.2, 3.6
MAP_MIN_WIDTH = 0.4  # a map moved below a face shrinks to fit above the captions, but not below this
MAP_FLOOR = 0.63  # where a moved map or label must end: above the lowest caption slot, even two bold lines
MAP_FACE_MIN, MAP_FACE_MARGIN = 0.05, 0.03  # smaller background faces may be covered
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


def map_spot(fmt, faces, show_map):
    """(top, map width) as shares of the frame for one place's map and label in the vertical video, or None to keep
    them under the top bar. When that would cover a face they move down below it: the map shrinks to fit above the
    captions and its label goes inside it. If there's no room for a useful map they stay put."""
    if fmt != "vertical" or not faces:
        return None
    w, h = mv.FORMATS[fmt]["size"]
    top_rest = (LABEL_GAP_PX / h if show_map else 0) + LABEL_PX / h  # under the top bar the label sits below the map
    rest = 0 if show_map else LABEL_PX / h  # moved, it sits inside the map

    def end(top, width, below=rest):
        return top + (width * w * MAP_ASPECT / h if show_map else 0) + below

    def overlap(top, width, below=rest):
        return sum(max(0.0, min(end(top, width, below), b1) - max(top, b0)) for b0, b1 in faces)
    if not overlap(MAP_TOP, MAP_WIDTH, top_rest):
        return None
    top = max(b1 for b0, b1 in faces if b0 < end(MAP_TOP, MAP_WIDTH, top_rest) and b1 > MAP_TOP)
    width = min(MAP_WIDTH, (MAP_FLOOR - top) * h / (w * MAP_ASPECT)) if show_map else MAP_WIDTH
    if end(top, width) > MAP_FLOOR + 1e-9 or (show_map and width < MAP_MIN_WIDTH):
        return None
    if overlap(top, width):  # it would land on another face lower down
        return None
    return round(top, 4), round(width, 4)


def place_props(scenes, script, bounds, fps, fmt=None, first=0):
    """Place labels and map pins. In the vertical video, a map or label that would cover a face (in any scene it
    spans) is moved down below it; those get "top" and "mapWidth", and "until" (its last frame) for the captions.
    `scenes` may be a vertical part starting at script scene `first`; it gets its own map pins."""
    import captions
    import places
    found = list(places.scene_places(scenes, script, first))
    faces = (captions.scene_faces(scenes, fmt, min_height=MAP_FACE_MIN, margin=MAP_FACE_MARGIN)
             if fmt == "vertical" else None)
    out = []
    for n, (i, place, show_map, visited) in enumerate(found):
        frame = bounds[i] + round(LABEL_DELAY * fps)
        p = {"frame": frame, "label": place.label, "country": place.country, "lat": place.lat, "lon": place.lon,
             "showMap": show_map, "visited": visited}
        if faces:
            nxt = bounds[found[n + 1][0]] + round(LABEL_DELAY * fps) if n + 1 < len(found) else float("inf")
            until = min(frame + round((MAP_SECONDS if show_map else LABEL_SECONDS) * fps), nxt)
            spanned = [b for j, b0 in enumerate(bounds) if b0 < until and (j + 1 == len(bounds) or bounds[j + 1] > frame)
                       for b in faces[j]]
            spot = map_spot(fmt, spanned, show_map)
            if spot:
                p.update(top=spot[0], mapWidth=spot[1], until=until)
        out.append(p)
    return out


def map_blocks(places_, fmt):
    """(start, end, y0, y1) of every map or label moved down, for the captions to stay clear of."""
    w, h = mv.FORMATS[fmt]["size"]
    out = []
    for p in places_:
        if "top" in p:
            height = p["mapWidth"] * w * MAP_ASPECT if p["showMap"] else LABEL_PX  # a moved map holds its label
            out.append((p["frame"], p["until"], p["top"], p["top"] + height / h))
    return out


# ---------- assets ----------

def stock_clip(src, out, fmt, frames, fps=mv.FPS):
    """A stock video cover-cropped to the format and looped or cut to the scene's sequence length, silent."""
    w, h = mv.FORMATS[fmt]["size"]
    mv.run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", src, "-t", f"{frames / fps:.3f}", "-vf",
            f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h},setsar=1,fps={fps}",
            "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", out])
    return out


def prepare_assets(scenes, public, args, fmt, state, lengths=None):
    """Copy each scene's picture for one format (and its parallax mask) into the public dir. Returns per-scene asset
    dicts. Files both formats share are copied once; `state` carries that and the parallax switch between formats.
    Stock videos play as video, prepared to each scene's length (`lengths`, in frames)."""
    from PIL import Image
    public.mkdir(parents=True, exist_ok=True)
    copied = state.setdefault("copied", {})
    assets = []
    for i, s in enumerate(scenes):
        src = mv.scene_src(s, fmt)
        if src and s.get("stock") and Path(src).suffix.lower() in mv.VIDEO_EXT and lengths:
            clip = stock_clip(src, public / f"scene_{i:03}_{fmt}.mp4", fmt, lengths[i])
            assets.append({"kind": "video", "src": clip.name})
            continue
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


def build_props(fmt, scenes, assets, moves, lengths, bounds, total, lines, places_, args, fps=mv.FPS, part=None):
    """Props for one video. part = (k, n) for part k of n of the vertical series: "Part k of N" in the intro, and
    "Watch Part k+1" instead of the subscribe card in every outro but the last."""
    w, h = mv.FORMATS[fmt]["size"]
    props_scenes = []
    for s, a, name, length in zip(scenes, assets, moves, lengths):
        aspect = (a.get("width") or 1) / (a.get("height") or 1)
        strong = bool(s.get("video")) and a["kind"] == "image"
        props_scenes.append({**a, "frames": length, "text": s["text"] if a["kind"] == "card" else None,
                             "move": move_props(name, strong, aspect, w, h)})
    return {"width": w, "height": h, "fps": fps, "durationInFrames": total, "transitionFrames": TRANSITION,
            "introFrames": 0 if args.no_branding else INTRO, "outroFrames": 0 if args.no_branding else OUTRO,
            "font": "font.ttf", "channel": args.channel, "tagline": TAGLINE, "scenes": props_scenes,
            "captions": lines, "captionStyle": caption_style(fmt, args), "places": places_,
            "part": {"index": part[0], "count": part[1]} if part else None}


def caption_style(fmt, args):
    import captions
    return captions.style_for(fmt, getattr(args, "caption_style", "auto"))


def caption_layouts(chunks, scenes, script, formats, args, fps, lead, bounds, blocks=None, first=0):
    """{format: caption lines} in each format's style, kept off the faces in that format's pictures and off any map
    moved down below a face (`blocks`: {format: map_blocks}). `scenes` may be a part starting at scene `first`."""
    import captions
    import places
    if not chunks:
        return {f: [] for f in formats}
    names = list(mv.parse_characters(script))
    places_by_scene = {}
    for i, place, _, _ in places.scene_places(scenes, script, first):
        places_by_scene.setdefault(i, []).append(place.label)
    out = {}
    for fmt in formats:
        style = caption_style(fmt, args)
        boxes = dict(enumerate(captions.scene_faces(scenes, fmt)))
        out[fmt] = captions.layout(chunks, scenes, fmt, style, fps, lead, bounds, names, places_by_scene, boxes,
                                   hold=CAPTION_HOLD, blocks=(blocks or {}).get(fmt, ()))
        moved = sum(l["y"] != captions.POSITIONS[fmt][style][0] for l in out[fmt])
        print(f"  {fmt} captions: {style}, {len(out[fmt])} lines, {sum(w['key'] for l in out[fmt] for w in l['words'])} "
              f"key words, {moved} line(s) moved off a face")
    return out


def shift_chunks(chunks, t0, t1):
    """The caption chunks spoken between t0 and t1 seconds of the narration, re-timed to start at t0."""
    out = []
    for chunk in chunks or []:
        words = [mv.Word(w.text, w.start - t0, w.end - t0) for w in chunk if t0 <= w.start < t1]
        if words:
            out.append(words)
    return out


def cut_audio(narration, t0, t1, out):
    mv.run(["ffmpeg", "-y", "-ss", f"{t0:.3f}", "-t", f"{t1 - t0:.3f}", "-i", narration, out])
    return out


def plan_cuts(scenes, formats, base, parts):
    """[(format, first scene, end scene, (k, n) or None, output path)]: the whole script for the horizontal video, and
    the vertical one as parts when it needs more than one."""
    import shorts
    cuts = []
    for fmt in formats:
        if fmt == "vertical" and parts and len(parts) > 1:
            cuts += [(fmt, a, b, (k, len(parts)), shorts.part_path(base, k)) for k, (a, b) in enumerate(parts, 1)]
        else:
            cuts.append((fmt, 0, len(scenes), None, base.with_name(f"{base.name}_{fmt}.mp4")))
    return cuts


def render_all(scenes, work, narration, chunks, font_path, args, formats, base, script, parts=None):
    """Render every format with Remotion: the horizontal video from the whole script, and the vertical one as the
    `parts` [(first, end)] of a Shorts series when there are several. Returns (output paths, seconds before the
    narration starts, total seconds) for the full-length video."""
    ensure_installed()
    fps = mv.FPS
    intro, outro = (0, 0) if args.no_branding else (INTRO, OUTRO)
    durations = [s["dur"] for s in scenes]
    lead, lengths, bounds, total = timeline(durations, fps, intro, outro)
    rwork = work / "remotion"
    public = rwork / "public"
    rwork.mkdir(parents=True, exist_ok=True)
    public.mkdir(parents=True, exist_ok=True)
    shutil.copy(font_path, public / "font.ttf")
    print("Preparing Remotion assets...")
    state, assets, moves = {}, {}, {}
    for fmt in formats:
        assets[fmt] = prepare_assets(scenes, public, args, fmt, state, lengths)
        moves[fmt] = plan_moves(scenes, script.stem, {i for i, a in enumerate(assets[fmt]) if a.get("mask")})
    parallax = " / ".join(f"{sum(bool(a.get('mask')) for a in assets[f])} {f}" for f in formats)
    cuts = plan_cuts(scenes, formats, base, parts)
    frames = {c: timeline(durations[c[1]:c[2]], fps, intro, outro)[3] for c in cuts}
    n_parts = sum(c[3] is not None for c in cuts)
    print(f"  {len(scenes)} scenes, parallax on {parallax}"
          f"{f'; the vertical video is a series of {n_parts} Shorts' if n_parts else ''}")
    secs = sum(frames[c] * load_speed()[0][c[0]] for c in cuts)
    print(f"Remotion render: {sum(frames.values())} frames in all; estimated ~{minutes(secs)} "
          f"({'measured on this PC' if load_speed()[1] else 'first-run guess; the next estimate uses your real speed'}, "
          f"{concurrency()} parallel frames)")

    print("Bundling the Remotion project...")
    bundle = rwork / "bundle"
    npx("remotion", "bundle", "src/index.ts", f"--public-dir={public}", f"--out-dir={bundle}", "--log=error", capture=True)

    starts = [sum(durations[:i]) for i in range(len(scenes) + 1)]  # where each scene starts in the narration
    outs = []
    for cut in cuts:
        fmt, a, b, part, out = cut
        name = f"{fmt}_part{part[0]}" if part else fmt
        sub = scenes[a:b]
        c_lead, c_lengths, c_bounds, c_total = timeline(durations[a:b], fps, intro, outro)
        places_ = place_props(sub, script, c_bounds, fps, fmt, first=a)
        lines = caption_layouts(shift_chunks(chunks, starts[a], starts[b]) if chunks else chunks, sub, script, [fmt],
                                args, fps, c_lead, c_bounds, {fmt: map_blocks(places_, fmt)}, first=a)[fmt]
        voice = narration if (a, b) == (0, len(scenes)) else cut_audio(narration, starts[a], starts[b],
                                                                       rwork / f"narration_{name}.wav")
        audio = timeline_audio(voice, c_lead / fps, c_total / fps, rwork, f"timeline_{name}.wav")
        props = rwork / f"props_{name}.json"
        props.write_text(json.dumps(build_props(fmt, sub, assets[fmt][a:b], moves[fmt][a:b], c_lengths, c_bounds,
                                                c_total, lines, places_, args, part=part),
                                    ensure_ascii=False), encoding="utf-8")
        silent = rwork / f"{name}_silent.mp4"
        w, h = mv.FORMATS[fmt]["size"]
        label = f"{fmt} part {part[0]} of {part[1]} (scenes {a + 1}-{b})" if part else fmt
        print(f"Rendering {label} ({w}x{h}) with Remotion...")
        t0 = time.time()
        npx("remotion", "render", bundle, "Main", silent, f"--props={props}", "--codec=h264", "--crf=18",
            "--muted", f"--concurrency={concurrency()}", "--gl=angle", "--log=warn")
        save_speed(fmt, (time.time() - t0) / c_total)
        print(f"  {label}: {time.time() - t0:.0f} s; mixing the voice{' and music' if args.music else ''}...")
        outs.append(mv.mux(silent, audio, args, out, cwd=rwork, video_codec=["-c:v", "copy"]))
    return outs, lead / fps, total / fps


def timeline_audio(narration, lead, total, work, name="narration_timeline.wav"):
    """The narration placed on the final timeline: silence for the intro, then the voice, padded to the outro's end."""
    out = work / name
    ms = round(lead * 1000)
    mv.run(["ffmpeg", "-y", "-i", narration, "-af", f"adelay={ms}|{ms},apad", "-t", f"{total:.3f}", out])
    return out
