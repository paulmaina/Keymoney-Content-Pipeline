"""--renderer hyperframes: HTML/GSAP motion composition rendered via HyperFrames CLI.

Builds a self-contained HyperFrames project (index.html + compositions/overlays.html
+ compositions/captions.html + assets/) per video / Short part, runs `hyperframes check`
and `hyperframes render`, and muxes narration, branding-clip audio, ducked music, and
CC0 ambient SFX with FFmpeg (`make_video.mux`) so audio parity matches Remotion.
"""
from __future__ import annotations

import html
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import make_video as mv
import remotion_render as rr

HYPERFRAMES_VERSION = "0.9.26"
GSAP_CDN = "https://cdn.jsdelivr.net/npm/gsap@3.15.0/dist/gsap.min.js"
FPS = mv.FPS
INTRO_SEC = rr.INTRO / FPS
OUTRO_SEC = rr.OUTRO / FPS
TRANSITION_SEC = rr.TRANSITION / FPS


def _cached_hyperframes_bin() -> Path | None:
    """Find a locally installed or npx-cached hyperframes.mjs binary if present."""
    env_bin = os.environ.get("HYPERFRAMES_BIN", "").strip()
    if env_bin and Path(env_bin).is_file():
        return Path(env_bin)
    local_mjs = mv.ROOT / "node_modules" / "hyperframes" / "bin" / "hyperframes.mjs"
    if local_mjs.is_file():
        return local_mjs
    local_app = Path(os.environ.get("LOCALAPPDATA", "")) if os.name == "nt" else Path.home() / ".npm"
    npx_cache = local_app / "npm-cache" / "_npx"
    if npx_cache.is_dir():
        matches = sorted(npx_cache.glob("*/node_modules/hyperframes/bin/hyperframes.mjs"))
        if matches:
            return matches[-1]
    return None


def _node_exe() -> Path | None:
    bin_dir = rr.node_bin()
    if bin_dir:
        exe = bin_dir / ("node.exe" if os.name == "nt" else "node")
        if exe.exists():
            return exe
    found = shutil.which("node")
    return Path(found) if found else None


def available() -> tuple[bool, str | None]:
    """(True, None) when HyperFrames can render, else (False, reason)."""
    if not _node_exe():
        return False, "Node.js isn't installed (winget install OpenJS.NodeJS.LTS)"
    if _cached_hyperframes_bin() or rr.node_bin():
        return True, None
    return False, "hyperframes CLI and npx are not available"


def run_hf(*args: str, cwd: Path, capture: bool = False) -> str:
    """Run the HyperFrames CLI using cached hyperframes.mjs when available, else npx.cmd."""
    node = _node_exe()
    if not node:
        sys.exit("HyperFrames failed: Node.js not found on PATH.")
    bin_dir = node.parent
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    cached = _cached_hyperframes_bin()
    if cached:
        cmd = [str(node), str(cached), *map(str, args)]
    else:
        npx = bin_dir / ("npx.cmd" if os.name == "nt" else "npx")
        cmd = [str(npx), "--yes", f"hyperframes@{HYPERFRAMES_VERSION}", *map(str, args)]
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=capture, text=True)
    if r.returncode != 0:
        detail = (r.stderr or r.stdout or "")[-3000:]
        sys.exit(f"HyperFrames failed: {' '.join(args[:3])}\n{detail}")
    return r.stdout or ""


# ---------- deterministic timeline & HTML builders (unit-tested) ----------

def lead_seconds(args, fmt: str) -> float:
    """Seconds before the narration starts in a video of this format (its intro clip/card length)."""
    intro = rr.branding_ends(args, fmt)[0]
    return intro / FPS


def scene_spans(durations: list[float], intro_sec: float, outro_sec: float) -> tuple[float, list[tuple[float, float]], float]:
    """Compute (lead_in_sec, [(start_sec, duration_sec), ...], total_sec) on the HyperFrames timeline."""
    lead = round(intro_sec, 4)
    spans: list[tuple[float, float]] = []
    t = lead
    for d in durations:
        dur = max(0.1, round(float(d), 4))
        spans.append((round(t, 4), dur))
        t += dur
    total = round(t + outro_sec, 4)
    return lead, spans, total


def _motion_keyframes(move_name: str, strong: bool, camera: str) -> tuple[dict, dict]:
    """Map a move name from remotion_render.CAMERA to safe GSAP from/to transform values."""
    amp = 1.45 if strong else (1.15 if camera == "bold" else 1.0)
    base_scale = 1.0 + 0.08 * amp
    table = {
        "zoom-in": ({"scale": 1.0, "xPercent": 0, "yPercent": 0}, {"scale": round(base_scale, 3), "xPercent": 0, "yPercent": 0}),
        "zoom-out": ({"scale": round(base_scale, 3), "xPercent": 0, "yPercent": 0}, {"scale": 1.0, "xPercent": 0, "yPercent": 0}),
        "pan-left": ({"scale": round(base_scale, 3), "xPercent": round(2.5 * amp, 2), "yPercent": 0}, {"scale": round(base_scale, 3), "xPercent": round(-2.5 * amp, 2), "yPercent": 0}),
        "pan-right": ({"scale": round(base_scale, 3), "xPercent": round(-2.5 * amp, 2), "yPercent": 0}, {"scale": round(base_scale, 3), "xPercent": round(2.5 * amp, 2), "yPercent": 0}),
        "tilt-up": ({"scale": round(base_scale, 3), "xPercent": 0, "yPercent": round(-2.2 * amp, 2)}, {"scale": round(base_scale, 3), "xPercent": 0, "yPercent": round(2.2 * amp, 2)}),
        "tilt-down": ({"scale": round(base_scale, 3), "xPercent": 0, "yPercent": round(2.2 * amp, 2)}, {"scale": round(base_scale, 3), "xPercent": 0, "yPercent": round(-2.2 * amp, 2)}),
        "push-left": ({"scale": 1.02, "xPercent": round(1.5 * amp, 2), "yPercent": 0}, {"scale": round(base_scale + 0.03, 3), "xPercent": round(-2.0 * amp, 2), "yPercent": 0}),
        "push-right": ({"scale": 1.02, "xPercent": round(-1.5 * amp, 2), "yPercent": 0}, {"scale": round(base_scale + 0.03, 3), "xPercent": round(2.0 * amp, 2), "yPercent": 0}),
        "push-up": ({"scale": 1.02, "xPercent": 0, "yPercent": round(1.8 * amp, 2)}, {"scale": round(base_scale + 0.03, 3), "xPercent": 0, "yPercent": round(-1.8 * amp, 2)}),
        "drift": ({"scale": round(base_scale, 3), "xPercent": round(-2.0 * amp, 2), "yPercent": round(1.5 * amp, 2)}, {"scale": round(base_scale + 0.03, 3), "xPercent": round(2.0 * amp, 2), "yPercent": round(-1.5 * amp, 2)}),
    }
    return table.get(move_name, table["zoom-in"])


def _brand_colors(profile) -> dict[str, str]:
    brand = getattr(profile, "brand", None) or {}
    return {
        "night": brand.get("night", "#0E2A3B"),
        "nightLight": brand.get("nightLight", "#1C4A63"),
        "yellow": brand.get("yellow", "#FFD23F"),
        "red": brand.get("red", "#E63946"),
    }


def render_captions_html(lines: list[dict], w: int, h: int, style: str, brand: dict[str, str], fps: int = FPS) -> str:
    """Generate HyperFrames sub-composition `compositions/captions.html`."""
    accent = brand["yellow"]
    font_px = 68 if (style == "bold" and w < h) else (48 if style == "bold" else (38 if w >= h else 44))
    if style == "minimal":
        font_px = 30 if w >= h else 34
    side_pad = 90 if w < h else 140
    box_w = w - 2 * side_pad

    divs: list[str] = []
    tweens: list[str] = []
    for idx, line in enumerate(lines):
        start_s = round(line["start"] / fps, 3)
        end_s = round(line["end"] / fps, 3)
        dur_s = max(0.12, round(end_s - start_s, 3))
        top_px = round(line.get("y", 0.85) * h - font_px * 0.7)
        spans = []
        for wd in line.get("words", []):
            txt = html.escape(wd["text"])
            cls = "em" if wd.get("key") else "w"
            spans.append(f'<span class="{cls}">{txt}</span>')
        cid = f"cg{idx}"
        divs.append(
            f'    <div id="{cid}" class="cg clip" data-start="{start_s}" data-duration="{dur_s}" '
            f'data-track-index="0" style="top:{top_px}px"><div id="{cid}-in" class="cg-in">{" ".join(spans)}</div></div>'
        )
        in_dur = min(0.14, round(dur_s * 0.35, 3))
        out_dur = min(0.08, round(dur_s * 0.25, 3))
        out_at = round(max(start_s + in_dur, end_s - out_dur), 3)
        if style == "bold":
            tweens.append(
                f'    tl.fromTo("#{cid}-in", {{ y: 18, scale: 0.94, opacity: 0 }}, '
                f'{{ y: 0, scale: 1, opacity: 1, duration: {in_dur}, ease: "back.out(2)" }}, {start_s});'
            )
        else:
            tweens.append(
                f'    tl.fromTo("#{cid}-in", {{ y: 8, opacity: 0 }}, '
                f'{{ y: 0, opacity: 1, duration: {in_dur}, ease: "power2.out" }}, {start_s});'
            )
        if out_at > start_s + in_dur:
            tweens.append(
                f'    tl.to("#{cid}-in", {{ opacity: 0, duration: {out_dur}, ease: "power2.in" }}, {out_at});'
            )

    text_transform = "uppercase" if style == "bold" else "none"
    bg_pill = "background: rgba(10, 14, 22, 0.72); padding: 10px 22px; border-radius: 14px;" if style == "clean" else ""
    return f"""<template>
  <style>
    @font-face {{ font-family: "ProducerFont"; src: url("assets/font.ttf"); }}
    #root {{ position: absolute; inset: 0; }}
    .cg {{ position: absolute; left: {side_pad}px; width: {box_w}px; text-align: center; overflow: visible; }}
    .cg-in {{ display: inline-block; font-family: "ProducerFont", "Inter", sans-serif; font-weight: 800; font-size: {font_px}px;
      line-height: 1.15; color: #FFFFFF; text-transform: {text_transform};
      text-shadow: 0 3px 14px rgba(0,0,0,0.88), 0 0 4px rgba(0,0,0,0.95); {bg_pill} }}
    .cg-in .em {{ color: {accent}; }}
  </style>
  <div id="root" data-composition-id="captions" data-width="{w}" data-height="{h}">
{chr(10).join(divs)}
  </div>
  <script>
    const tl = gsap.timeline({{ paused: true }});
{chr(10).join(tweens)}
    window.__timelines["captions"] = tl;
  </script>
</template>
"""


def render_overlays_html(
    w: int,
    h: int,
    total_sec: float,
    channel_name: str,
    tagline: str,
    brand: dict[str, str],
    places_list: list[dict],
    intro_sec: float,
    outro_sec: float,
    has_intro_clip: bool,
    has_outro_clip: bool,
    part: tuple[int, int] | None = None,
    fps: int = FPS,
) -> str:
    """Generate HyperFrames sub-composition `compositions/overlays.html` (intro, place badges, outro)."""
    els: list[str] = []
    tweens: list[str] = []

    if intro_sec > 0 and not has_intro_clip:
        title_sz = 64 if w >= h else 58
        sub_sz = 26 if w >= h else 24
        part_badge = (
            f'<div class="part-pill">PART {part[0]} OF {part[1]}</div>'
            if part else ""
        )
        els.append(
            f'    <div id="ov-intro" class="clip full-card" data-start="0" data-duration="{round(intro_sec, 3)}" data-track-index="0">\n'
            f'      <div id="ov-intro-in" class="card-inner">\n'
            f'        {part_badge}\n'
            f'        <div class="brand-title">{html.escape(channel_name)}</div>\n'
            f'        <div class="brand-tag">{html.escape(tagline)}</div>\n'
            f'      </div>\n'
            f'    </div>'
        )
        tweens.append('    tl.fromTo("#ov-intro-in", { y: 24, opacity: 0 }, { y: 0, opacity: 1, duration: 0.45, ease: "power3.out" }, 0.05);')
        if intro_sec > 0.55:
            tweens.append(f'    tl.to("#ov-intro-in", {{ opacity: 0, duration: 0.35, ease: "power2.in" }}, {round(intro_sec - 0.38, 3)});')
    elif intro_sec > 0 and has_intro_clip and part:
        els.append(
            f'    <div id="ov-part" class="clip part-banner" data-start="0.15" data-duration="{round(max(0.5, intro_sec - 0.25), 3)}" data-track-index="0">\n'
            f'      <div class="part-pill">PART {part[0]} OF {part[1]}</div>\n'
            f'    </div>'
        )

    for idx, pl in enumerate(places_list):
        start_s = round(pl["frame"] / fps, 3)
        dur_s = round(rr.MAP_SECONDS if pl.get("showMap") else rr.LABEL_SECONDS, 3)
        if start_s >= total_sec:
            continue
        dur_s = min(dur_s, max(0.3, round(total_sec - start_s, 3)))
        pid = f"ov-place-{idx}"
        els.append(
            f'    <div id="{pid}" class="clip place-badge" data-start="{start_s}" data-duration="{dur_s}" data-track-index="1">\n'
            f'      <span id="{pid}-in">{html.escape(pl["label"])}</span>\n'
            f'    </div>'
        )
        tweens.append(f'    tl.fromTo("#{pid}-in", {{ x: -20, opacity: 0 }}, {{ x: 0, opacity: 1, duration: 0.28, ease: "power2.out" }}, {start_s});')
        if dur_s > 0.5:
            tweens.append(f'    tl.to("#{pid}-in", {{ opacity: 0, duration: 0.25, ease: "power2.in" }}, {round(start_s + dur_s - 0.28, 3)});')

    if outro_sec > 0 and not has_outro_clip:
        outro_start = round(max(0.0, total_sec - outro_sec), 3)
        next_part = part[0] + 1 if part and part[0] < part[1] else None
        cta = f"Watch Part {next_part}" if next_part else "Thanks for watching"
        sub = "Swipe up for the next part" if next_part else f"Subscribe to {channel_name}"
        els.append(
            f'    <div id="ov-outro" class="clip full-card" data-start="{outro_start}" data-duration="{round(outro_sec, 3)}" data-track-index="0">\n'
            f'      <div id="ov-outro-in" class="card-inner">\n'
            f'        <div class="brand-title">{html.escape(cta)}</div>\n'
            f'        <div class="outro-btn">{html.escape(sub)}</div>\n'
            f'      </div>\n'
            f'    </div>'
        )
        tweens.append(f'    tl.fromTo("#ov-outro-in", {{ scale: 0.94, opacity: 0 }}, {{ scale: 1, opacity: 1, duration: 0.4, ease: "power3.out" }}, {outro_start});')

    title_sz = 64 if w >= h else 54
    sub_sz = 26 if w >= h else 24
    place_top = 48 if w >= h else 160
    place_left = 48 if w >= h else 44
    return f"""<template>
  <style>
    @font-face {{ font-family: "ProducerFont"; src: url("assets/font.ttf"); }}
    #root {{ position: absolute; inset: 0; font-family: "ProducerFont", "Inter", sans-serif; }}
    .full-card {{ position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
      background: radial-gradient(circle at 50% 40%, {brand["nightLight"]} 0%, {brand["night"]} 78%); }}
    .card-inner {{ text-align: center; padding: 40px 64px; max-width: 88%; }}
    .brand-title {{ font-size: {title_sz}px; font-weight: 800; color: #FFFFFF; letter-spacing: -0.01em; margin-bottom: 16px; }}
    .brand-tag {{ font-size: {sub_sz}px; font-weight: 600; color: {brand["yellow"]}; }}
    .outro-btn {{ display: inline-block; margin-top: 14px; padding: 14px 32px; border-radius: 999px;
      background: {brand["red"]}; color: #FFFFFF; font-size: {sub_sz}px; font-weight: 800; }}
    .part-banner {{ position: absolute; top: 88px; left: 0; width: 100%; text-align: center; }}
    .part-pill {{ display: inline-block; padding: 8px 20px; border-radius: 999px; margin-bottom: 18px;
      background: {brand["red"]}; color: #FFFFFF; font-size: 22px; font-weight: 800; letter-spacing: 0.08em; }}
    .place-badge {{ position: absolute; left: {place_left}px; top: {place_top}px; }}
    .place-badge span {{ display: inline-block; padding: 10px 18px; border-left: 6px solid {brand["red"]};
      background: rgba(14, 13, 11, 0.80); color: #F1ECE2; font-size: 24px; font-weight: 700;
      letter-spacing: 0.08em; text-transform: uppercase; }}
  </style>
  <div id="root" data-composition-id="overlays" data-width="{w}" data-height="{h}">
{chr(10).join(els)}
  </div>
  <script>
    const tl = gsap.timeline({{ paused: true }});
{chr(10).join(tweens)}
    window.__timelines["overlays"] = tl;
  </script>
</template>
"""


def render_index_html(
    w: int,
    h: int,
    total_sec: float,
    brand: dict[str, str],
    shot_items: list[dict],
    intro_clip: str | None = None,
    intro_sec: float = 0.0,
    outro_clip: str | None = None,
    outro_sec: float = 0.0,
    camera: str = "bold",
) -> str:
    """Generate the root `index.html` composition for a HyperFrames project."""
    elements: list[str] = []
    tweens: list[str] = []
    z = 2

    if intro_clip and intro_sec > 0:
        elements.append(
            f'      <div id="intro-wrap" class="clip frame" data-start="0" data-duration="{round(intro_sec, 3)}" data-track-index="1" style="z-index:{z}">\n'
            f'        <video id="intro-vid" class="media" src="assets/{html.escape(intro_clip)}" muted playsinline></video>\n'
            f'      </div>'
        )
        z += 1

    for idx, item in enumerate(shot_items):
        sid = f"s{idx}"
        start_s = round(item["start"], 4)
        dur_s = round(item["duration"], 4)
        if item["kind"] == "video":
            elements.append(
                f'      <div id="{sid}-wrap" class="clip frame" data-start="{start_s}" data-duration="{dur_s}" data-track-index="1" style="z-index:{z}">\n'
                f'        <video id="{sid}" class="media" src="assets/{html.escape(item["src"])}" muted playsinline></video>\n'
                f'      </div>'
            )
            tweens.append(
                f'      tl.fromTo("#{sid}", {{ scale: 1.0 }}, {{ scale: 1.06, duration: {dur_s}, ease: "none" }}, {start_s});'
            )
        elif item["kind"] == "card":
            txt = html.escape(item.get("text") or "")
            elements.append(
                f'      <div id="{sid}-wrap" class="clip frame card-scene" data-start="{start_s}" data-duration="{dur_s}" data-track-index="1" style="z-index:{z}">\n'
                f'        <div id="{sid}" class="card-text">{txt}</div>\n'
                f'      </div>'
            )
            tweens.append(
                f'      tl.fromTo("#{sid}", {{ opacity: 0, y: 14 }}, {{ opacity: 1, y: 0, duration: 0.35, ease: "power2.out" }}, {start_s});'
            )
        else:
            elements.append(
                f'      <div id="{sid}-wrap" class="clip frame" data-start="{start_s}" data-duration="{dur_s}" data-track-index="1" style="z-index:{z}">\n'
                f'        <img id="{sid}" class="media" src="assets/{html.escape(item["src"])}" alt="" />\n'
                f'      </div>'
            )
            kf_from, kf_to = _motion_keyframes(item.get("move", "zoom-in"), bool(item.get("strong")), camera)
            tweens.append(
                f'      tl.fromTo("#{sid}", {json.dumps(kf_from)}, {json.dumps({**kf_to, "duration": dur_s, "ease": "none"})}, {start_s});'
            )
        z += 1

    if outro_clip and outro_sec > 0:
        outro_start = round(max(0.0, total_sec - outro_sec), 3)
        elements.append(
            f'      <div id="outro-wrap" class="clip frame" data-start="{outro_start}" data-duration="{round(outro_sec, 3)}" data-track-index="1" style="z-index:{z}">\n'
            f'        <video id="outro-vid" class="media" src="assets/{html.escape(outro_clip)}" muted playsinline></video>\n'
            f'      </div>'
        )

    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="{w}, height={h}" />
    <script src="{GSAP_CDN}"></script>
    <style>
      @font-face {{ font-family: "ProducerFont"; src: url("assets/font.ttf"); }}
      * {{ margin: 0; padding: 0; box-sizing: border-box; }}
      html, body {{ width: {w}px; height: {h}px; overflow: hidden; background: {brand["night"]}; }}
      #root {{ position: relative; width: 100%; height: 100%; background: {brand["night"]}; overflow: hidden; }}
      .frame {{ position: absolute; inset: 0; overflow: hidden; }}
      .frame .media {{ position: absolute; left: 0; top: 0; width: 100%; height: 100%; object-fit: cover; }}
      .card-scene {{ display: flex; align-items: center; justify-content: center; padding: 10%;
        background: radial-gradient(circle at 50% 45%, {brand["nightLight"]} 0%, {brand["night"]} 78%); }}
      .card-text {{ font-family: "ProducerFont", "Inter", sans-serif; font-weight: 700; font-size: {46 if w >= h else 42}px;
        line-height: 1.3; color: #F5F7FA; text-align: center; }}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-duration="{round(total_sec, 3)}" data-width="{w}" data-height="{h}">
{chr(10).join(elements)}
      <div id="overlays" data-composition-id="overlays" data-composition-src="compositions/overlays.html"
        data-start="0" data-duration="{round(total_sec, 3)}" data-track-index="2" data-track-kind="graphics"
        data-width="{w}" data-height="{h}" style="z-index:40"></div>
      <div id="captions" data-composition-id="captions" data-composition-src="compositions/captions.html"
        data-start="0" data-duration="{round(total_sec, 3)}" data-track-index="4" data-track-kind="captions"
        data-width="{w}" data-height="{h}" style="z-index:50"></div>
    </div>
    <script>
      const tl = gsap.timeline({{ paused: true }});
{chr(10).join(tweens)}
      window.__timelines["main"] = tl;
    </script>
  </body>
</html>
"""


def write_project(
    proj_dir: Path,
    *,
    name: str,
    fmt: str,
    scenes: list[dict],
    assets_list: list[dict],
    moves: list[list[str]],
    spans: list[tuple[float, float]],
    total_sec: float,
    caption_lines_list: list[dict],
    places_list: list[dict],
    font_path: Path,
    args,
    intro_sec: float,
    outro_sec: float,
    intro_clip: str | None = None,
    outro_clip: str | None = None,
    part: tuple[int, int] | None = None,
) -> Path:
    """Scaffold a complete HyperFrames project directory ready for `check` and `render`."""
    w, h = mv.FORMATS[fmt]["size"]
    proj_dir.mkdir(parents=True, exist_ok=True)
    comp_dir = proj_dir / "compositions"
    assets_dir = proj_dir / "assets"
    comp_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(font_path, assets_dir / "font.ttf")

    profile = getattr(args, "profile", None)
    brand = _brand_colors(profile)
    camera = getattr(args, "camera", "bold")

    shot_items: list[dict] = []
    for i, (s, asset, mv_names, (st, dur)) in enumerate(zip(scenes, assets_list, moves, spans)):
        if asset["kind"] == "video":
            shot_items.append({"kind": "video", "src": asset["src"], "start": st, "duration": dur})
        elif asset["kind"] == "card":
            shot_items.append({"kind": "card", "text": s["text"], "start": st, "duration": dur})
        else:
            shots = asset.get("shots") or [asset]
            each = round(dur / len(shots), 4)
            for j, sh in enumerate(shots):
                sh_start = round(st + j * each, 4)
                sh_dur = round(dur - j * each, 4) if j + 1 == len(shots) else each
                shot_items.append(
                    {
                        "kind": "image",
                        "src": sh["src"],
                        "start": sh_start,
                        "duration": sh_dur,
                        "move": mv_names[min(j, len(mv_names) - 1)],
                        "strong": bool(s.get("video")),
                    }
                )

    (proj_dir / "hyperframes.json").write_text(
        json.dumps(
            {
                "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
                "paths": {"blocks": "compositions", "components": "compositions/components", "assets": "assets"},
                "authoringSkill": "hyperframes",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (proj_dir / "package.json").write_text(
        json.dumps(
            {
                "name": name,
                "private": True,
                "type": "module",
                "scripts": {
                    "dev": f"npx --yes hyperframes@{HYPERFRAMES_VERSION} preview",
                    "check": f"npx --yes hyperframes@{HYPERFRAMES_VERSION} check",
                    "render": f"npx --yes hyperframes@{HYPERFRAMES_VERSION} render",
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (proj_dir / "meta.json").write_text(json.dumps({"id": name, "name": name}, indent=2), encoding="utf-8")

    (comp_dir / "captions.html").write_text(
        render_captions_html(caption_lines_list, w, h, rr.caption_style(fmt, args), brand, fps=FPS),
        encoding="utf-8",
    )
    (comp_dir / "overlays.html").write_text(
        render_overlays_html(
            w,
            h,
            total_sec,
            getattr(args, "channel", "Smart Village Boy"),
            profile.tagline if profile else rr.TAGLINE,
            brand,
            places_list,
            intro_sec,
            outro_sec,
            bool(intro_clip),
            bool(outro_clip),
            part=part,
            fps=FPS,
        ),
        encoding="utf-8",
    )
    (proj_dir / "index.html").write_text(
        render_index_html(
            w,
            h,
            total_sec,
            brand,
            shot_items,
            intro_clip=intro_clip,
            intro_sec=intro_sec,
            outro_clip=outro_clip,
            outro_sec=outro_sec,
            camera=camera,
        ),
        encoding="utf-8",
    )
    return proj_dir


def render_all(scenes, work, narration, chunks, font_path, args, formats, out, script, parts=None, checkpoints=None):
    """Render every requested format using HyperFrames, returning (output paths, lead_in_sec, total_sec)."""
    done = checkpoints or rr._NoCheckpoints()
    ok, why = available()
    if not ok:
        sys.exit(f"HyperFrames renderer isn't available ({why}).")

    fps = FPS
    hwork = work / "hyperframes"
    hwork.mkdir(parents=True, exist_ok=True)
    durations = [s["dur"] for s in scenes]
    cuts = rr.plan_cuts(scenes, formats, out, parts)
    starts = [sum(durations[:i]) for i in range(len(scenes) + 1)]
    camera = getattr(args, "camera", "bold")

    # Prepare assets per format cleanly into shared staging, then link/copy into each cut's project assets/
    state: dict = {"parallax": False}
    assets_by_fmt: dict = {}
    moves_by_fmt: dict = {}
    stage_assets = hwork / "_assets"
    stage_assets.mkdir(parents=True, exist_ok=True)

    for fmt in formats:
        intro_f, outro_f = rr.branding_ends(args, fmt)[:2]
        _, lengths, _, _ = rr.timeline(durations, fps, intro_f, outro_f)
        assets_by_fmt[fmt] = rr.prepare_assets(scenes, stage_assets, args, fmt, state, lengths)
        counts = [len(a.get("shots") or [a]) for a in assets_by_fmt[fmt]]
        moves_by_fmt[fmt] = rr.plan_shot_moves(counts, script.stem, set(), camera)

    # Prepare custom channel branding clips once per (src, fmt)
    clips: dict = {}
    for fmt, _, _, part, _ in cuts:
        c_intro, c_outro, intro_src, outro_src = rr.branding_ends(args, fmt, part)
        for src, n in ((intro_src, c_intro), (outro_src, c_outro)):
            if src and (src, fmt) not in clips:
                clips[(src, fmt)] = rr.prepare_clip(src, fmt, n, stage_assets, f"branding_{Path(src).stem}")

    outs: list[Path] = []
    wrote_sfx = False
    first_lead, first_total = 0.0, sum(durations)

    for cut in cuts:
        fmt, a, b, part, video = cut
        name = f"{fmt}_part{part[0]}" if part else fmt
        sub = scenes[a:b]
        c_intro, c_outro, intro_src, outro_src = rr.branding_ends(args, fmt, part)
        intro_sec = c_intro / fps
        outro_sec = c_outro / fps
        lead_sec, spans, total_sec = scene_spans(durations[a:b], intro_sec, outro_sec)
        if (a, b) == (0, len(scenes)) and fmt == formats[0]:
            first_lead, first_total = lead_sec, total_sec

        lead_frames = round(lead_sec * fps)
        bounds_frames = [round(st * fps) for st, _ in spans]
        places_ = rr.place_props(sub, script, bounds_frames, fps, fmt, first=a)
        lines = rr.caption_layouts(
            rr.shift_chunks(chunks, starts[a], starts[b]) if chunks else chunks,
            sub,
            script,
            [fmt],
            args,
            fps,
            lead_frames,
            bounds_frames,
            {fmt: rr.map_blocks(places_, fmt)},
            first=a,
        )[fmt]

        proj_dir = hwork / f"proj_{name}"
        shutil.rmtree(proj_dir, ignore_errors=True)
        proj_assets = proj_dir / "assets"
        proj_assets.mkdir(parents=True, exist_ok=True)
        # Copy only needed visual assets for this cut
        sub_assets = assets_by_fmt[fmt][a:b]
        for asset in sub_assets:
            for item in asset.get("shots") or [asset]:
                if item.get("src") and (stage_assets / item["src"]).exists():
                    shutil.copy(stage_assets / item["src"], proj_assets / item["src"])

        intro_clip = clips[(intro_src, fmt)][0] if intro_src else None
        outro_clip = clips[(outro_src, fmt)][0] if outro_src else None
        for clip_name in (intro_clip, outro_clip):
            if clip_name and (stage_assets / clip_name).exists():
                shutil.copy(stage_assets / clip_name, proj_assets / clip_name)

        write_project(
            proj_dir,
            name=f"{out.name}_{name}",
            fmt=fmt,
            scenes=sub,
            assets_list=sub_assets,
            moves=moves_by_fmt[fmt][a:b],
            spans=spans,
            total_sec=total_sec,
            caption_lines_list=lines,
            places_list=places_,
            font_path=font_path,
            args=args,
            intro_sec=intro_sec,
            outro_sec=outro_sec,
            intro_clip=intro_clip,
            outro_clip=outro_clip,
            part=part,
        )

        voice = narration if (a, b) == (0, len(scenes)) else rr.cut_audio(narration, starts[a], starts[b], hwork / f"narration_{name}.wav")
        sounds = [
            (clips[(src, fmt)][1], at)
            for src, at in ((intro_src, 0.0), (outro_src, max(0.0, total_sec - outro_sec)))
            if src and clips[(src, fmt)][1]
        ]
        audio = rr.timeline_audio(voice, lead_sec, total_sec, hwork, f"timeline_{name}.wav", sounds)

        ambience = None
        if getattr(args, "sound_effects", False):
            import sfx
            cats = [sfx.choose(s) for s in sub]
            ambience = sfx.ambience_track(sfx.beds(cats, spans), total_sec, hwork / f"ambience_{name}.wav", args.sfx_volume)
            if (a, b) == (0, len(scenes)) and not wrote_sfx:
                sfx.record(sub, cats, out.sound_effects)
                wrote_sfx = True

        silent = hwork / f"{name}_silent.mp4"
        label = f"{fmt} part {part[0]} of {part[1]} (scenes {a + 1}-{b})" if part else fmt
        if done.get(f"hf_render_{name}") and silent.exists():
            print(f"Rendering {label} (HyperFrames): from the last run; mixing audio...")
        else:
            print(f"Rendering {label} with HyperFrames...")
            t0 = time.time()
            run_hf("render", "--fps", str(fps), "--quality", "looks", "--output", str(silent), cwd=proj_dir)
            done.save(f"hf_render_{name}")
            print(f"  {label}: {time.time() - t0:.0f} s; mixing audio...")

        outs.append(
            mv.mux(
                silent,
                audio,
                args,
                video,
                cwd=hwork,
                video_codec=["-c:v", "copy"],
                ambience=ambience,
                music_start=intro_sec if intro_src else 0.0,
            )
        )

    return outs, first_lead, first_total
