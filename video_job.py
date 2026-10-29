"""AI video clips made on a free Kaggle GPU (used by make_video.py).

  --export-video-job  writes Exports/<name>_video_job/ (job.json + scene images) and zips it for
                      upload as a Kaggle dataset; kaggle/wan_video.ipynb turns it into clips.
  --clips             imports the notebook's clips.zip: clips are matched to scenes, and any
                      images it made are added to the generated/ cache so they're reused.

Clips (about 5 s) are usually shorter than a scene's narration; clip_fill_plan() decides how the
rest of the scene is filled (--clip-fill pingpong / slowmo / freeze).
"""
import json
import shutil
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import ai_visuals

JOB_VERSION = 1
CLIP_SECONDS = 5.0
FILL_MODES = ("pingpong", "slowmo", "freeze")
MAX_SLOWMO = 3.0  # slow motion beyond 3x looks like a slideshow; alternate direction instead

MOTION_SYSTEM = (
    "You direct short AI video clips that animate a still image. Given the image prompt (and the script's visual "
    "description, if any, whose action the motion should show), reply with "
    "one sentence of at most 25 words describing gentle camera movement and subject motion for a "
    "5-second clip, e.g. 'slow push-in, leaves moving in the wind, steam rising from the pot'. "
    "Keep it physically plausible and subtle; no cuts, no new objects, no text. Reply with the sentence only."
)
FALLBACK_MOTION = "slow gentle push-in, subtle natural movement in the scene"


# ---------- marking scenes ----------

def apply_video_scenes(scenes, numbers):
    """Mark 1-based scene numbers for video; scenes with a [visual:] line are never animated."""
    for n in numbers:
        if not 1 <= n <= len(scenes):
            sys.exit(f"--video-scenes: there is no scene {n} (the script has {len(scenes)})")
        scenes[n - 1]["video"] = True
    for i, s in enumerate(scenes):
        if s.get("video") and s["visual"]:
            print(f"Warning: scene {i + 1} is marked for video but has [visual: {s['visual']}]; "
                  "it keeps that file and won't be animated.")
            s["video"] = False


def parse_scene_numbers(text):
    try:
        return sorted({int(x) for x in text.replace(" ", "").split(",") if x})
    except ValueError:
        sys.exit(f'--video-scenes expects numbers like "3,7,12", got "{text}"')


# ---------- export ----------

def load_motion_prompts(job_json):
    """Saved motion prompt records keyed by narration, so edits survive a re-export."""
    try:
        data = json.loads(job_json.read_text(encoding="utf-8"))
        return {s["narration"]: s for s in data["scenes"] if s.get("motion_prompt")}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def saved_motion(records, scene):
    """A saved motion prompt, if it was written for the same narration and visual description."""
    rec = records.get(scene["text"])
    if rec and rec.get("description", "") == scene.get("description", ""):
        return rec["motion_prompt"]
    return None


def write_motion_prompt(image_prompt, providers, description=""):
    shot = f"Visual description from the script (the motion should act it out): {description}\n\n" if description else ""
    return providers.text([{"role": "system", "content": MOTION_SYSTEM},
                           {"role": "user", "content": f"{shot}Image prompt: {image_prompt}"}], max_tokens=80) \
        or FALLBACK_MOTION


VIDEO_SIZES = {"960x544": (960, 544), "1280x704": (1280, 704)}  # Wan 2.2 TI2V-5B sizes that suit a T4


def export_job(scenes, base, providers, style="", regenerate=False, video_size="960x544", characters=None):
    """Write <base>_video_job/ and <base>_video_job.zip. Returns the zip path."""
    video = [i for i, s in enumerate(scenes) if s.get("video") and not s["visual"]]
    kaggle_images = providers.mode == "kaggle"
    if not video and not kaggle_images:
        sys.exit("No scenes are marked for video. Add a [video] line to a scene or use --video-scenes.")

    prompts_path = base.with_name(base.name + "_prompts.json")
    # images for the video scenes come from the usual provider/cache so clips match the stills;
    # with --image-provider kaggle nothing is generated here and the notebook makes them
    failed = ai_visuals.generate_scene_visuals(
        scenes, prompts_path, style=style, regenerate=regenerate, providers=providers, characters=characters,
        only=set() if kaggle_images else set(video))

    job_dir = base.with_name(base.name + "_video_job")
    saved = {} if regenerate else load_motion_prompts(job_dir / "job.json")
    shutil.rmtree(job_dir, ignore_errors=True)
    job_dir.mkdir(parents=True)

    entries = []
    for i, s in enumerate(scenes):
        if s["visual"]:
            continue
        is_video = i in video
        src = s.get("src")
        if src is None and (hit := providers.cached(s["image_prompt"])):
            src = hit[0]
        if not is_video and (src or not kaggle_images):
            continue  # already have this image, or it's made locally at render time
        image = None
        if src:
            image = f"scene_{i + 1:03}{src.suffix}"
            shutil.copy(src, job_dir / image)
        description = s.get("description", "")
        entry = {"scene": i + 1, "narration": s["text"], "description": description,
                 "task": "video" if is_video else "image", "image": image, "image_prompt": s["image_prompt"],
                 "seed": ai_visuals.stable_seed(s["text"])}
        if is_video:
            entry["motion_prompt"] = (saved_motion(saved, s)
                                      or write_motion_prompt(s["image_prompt"], providers, description))
            entry["seconds"] = CLIP_SECONDS
        entries.append(entry)

    job = {"version": JOB_VERSION, "name": base.name, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "image_cache_id": ai_visuals.KAGGLE_CACHE_ID, "video_size": list(VIDEO_SIZES[video_size]),
           "scenes": entries}
    (job_dir / "job.json").write_text(json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8")

    zip_path = base.with_name(base.name + "_video_job.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(job_dir.iterdir()):
            z.write(f, f.name)
    n_video = sum(e["task"] == "video" for e in entries)
    n_image = sum(e["task"] == "image" or not e["image"] for e in entries)
    print(f"Video job: {n_video} clip(s), {n_image} image(s) for the notebook to make -> {zip_path}")
    print(f"  edit motion prompts in {job_dir / 'job.json'}, then re-export to rebuild the zip")
    if failed:
        print(f"  no image for scene(s) {', '.join(map(str, failed))}: the notebook will make one with SDXL first")
    providers.report()
    return zip_path


# ---------- import ----------

def find_manifest(path, work):
    """Folder or zip -> (folder, manifest dict)."""
    path = Path(path)
    if not path.exists():
        sys.exit(f"--clips: not found: {path}")
    if path.suffix.lower() == ".zip":
        shutil.rmtree(work, ignore_errors=True)
        with zipfile.ZipFile(path) as z:
            z.extractall(work)
        path = work
    manifests = sorted(path.rglob("manifest.json"), key=lambda p: len(p.parts))
    if not manifests:
        sys.exit(f"--clips: no manifest.json in {path}")
    return manifests[0].parent, json.loads(manifests[0].read_text(encoding="utf-8"))


def match_scene(item, scenes):
    """Index of the scene an item belongs to: by narration, else by scene number."""
    for i, s in enumerate(scenes):
        if item.get("narration") and s["text"] == item["narration"]:
            return i
    n = item.get("scene")
    if isinstance(n, int) and 1 <= n <= len(scenes) and not item.get("narration"):
        return n - 1
    return None


def import_clips(path, scenes, work):
    """Attach clips to scenes (s["clip"], s["clip_info"]) and copy notebook images into the cache.
    Returns (number of clips attached, number of images imported)."""
    folder, manifest = find_manifest(path, work)
    clips = images = 0
    for item in manifest.get("items", []):
        f = folder / item.get("file", "")
        if not f.is_file():
            print(f"  --clips: {item.get('file')} listed in manifest.json but missing; skipped")
            continue
        if item.get("kind") == "image":
            if item.get("prompt"):
                from PIL import Image
                dest = ai_visuals.image_path(item["prompt"], ai_visuals.KAGGLE_CACHE_ID)
                ai_visuals.GEN_DIR.mkdir(exist_ok=True)
                Image.open(f).convert("RGB").save(dest, quality=95)
                images += 1
            continue
        i = match_scene(item, scenes)
        if i is None:
            print(f"  --clips: no scene matches clip {item.get('file')} (scene {item.get('scene')}); "
                  "was the narration changed after export?")
            continue
        if scenes[i]["visual"]:
            print(f"  --clips: scene {i + 1} has a [visual:] line; ignoring its clip")
            continue
        scenes[i]["clip"], scenes[i]["clip_info"] = f, item
        clips += 1
        print(f"  --clips: scene {i + 1} clip by {item.get('model')} ({item.get('width')}x{item.get('height')})"
              + (f", FALLBACK because {item['fallback_reason']}" if item.get("fallback_reason") else ""))
        h = item.get("height") or 0
        if h and h < 540:
            print(f"  --clips: scene {i + 1} clip is {item.get('width')}x{h}; it will look soft upscaled to 1080p")
    return clips, images


# ---------- filling a scene with a short clip ----------

@dataclass(frozen=True)
class Segment:
    kind: str  # "forward", "reverse" or "hold" (freeze on the last frame)
    stretch: float  # playback slowdown: 1.0 = real time, 2.0 = half speed
    seconds: float  # output duration of this segment


def clip_fill_plan(clip_len, dur, mode="pingpong", max_slow=MAX_SLOWMO):
    """How to fill `dur` seconds from a clip of `clip_len` seconds. The clip always plays once at
    normal speed first; the remainder never jumps back to the first frame."""
    if mode not in FILL_MODES:
        raise ValueError(f"unknown clip fill mode {mode}")
    if clip_len <= 0:
        raise ValueError("clip has no length")
    if clip_len >= dur:
        return [Segment("forward", 1.0, dur)]
    plan = [Segment("forward", 1.0, clip_len)]
    left = dur - clip_len
    if mode == "freeze":
        return plan + [Segment("hold", 1.0, left)]
    stretch = 1.0
    if mode == "slowmo":
        stretch = min(max(left / clip_len, 1.0), max_slow)  # one slow reverse pass if it fits
    kind = "reverse"
    while left > 1e-6:
        seconds = min(clip_len * stretch, left)
        plan.append(Segment(kind, stretch, round(seconds, 6)))
        left -= seconds
        kind = "forward" if kind == "reverse" else "reverse"
    return plan


def clip_filter(plan, fit):
    """ffmpeg filter graph for input 0 -> [v] following a clip_fill_plan."""
    moving = [s for s in plan if s.kind != "hold"]
    labels = [f"s{k}" for k in range(len(moving))]
    graph = [f"[0:v]{fit},setpts=PTS-STARTPTS,split={len(moving)}" + "".join(f"[{l}]" for l in labels)
             if len(moving) > 1 else f"[0:v]{fit},setpts=PTS-STARTPTS[s0]"]
    for k, seg in enumerate(moving):
        rev = "reverse," if seg.kind == "reverse" else ""
        graph.append(f"[s{k}]{rev}setpts={seg.stretch:.4f}*(PTS-STARTPTS),trim=duration={seg.seconds:.3f}[g{k}]")
    tail = []
    hold = sum(s.seconds for s in plan if s.kind == "hold")
    if hold:
        tail.append(f"tpad=stop_mode=clone:stop_duration={hold:.3f}")
    # blend frames when slowed so slow motion looks smooth rather than stepped
    tail.append("framerate=fps=30" if any(s.stretch > 1 for s in moving) else "fps=30")
    concat = "".join(f"[g{k}]" for k in range(len(moving))) + f"concat=n={len(moving)}:v=1:a=0"
    graph.append(concat + "," + ",".join(tail) + "[v]")
    return ";".join(graph)
