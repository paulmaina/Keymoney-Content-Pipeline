"""Sharper scene images from Real-ESRGAN on a free Kaggle GPU (kaggle/upscale.ipynb), on every final render.

Every scene image (Cloudflare, the Pollinations backup or a stock photo) under MIN_SIDE px on its long side that isn't upscaled yet goes to Kaggle in one batch
(one private dataset, one kernel run). The notebook enlarges each 2x (4x for images under 1024 px) and the
results are cached in generated/upscaled/<hash of the source image>.jpg, so each image is upscaled only once:
a rerun with the same images doesn't touch Kaggle at all.

If the run fails, Kaggle can't be reached, KAGGLE_API_TOKEN is missing or the weekly GPU hours are used up, the video
is rendered with the original images and a warning says so (also repeated at the end of the run).
"""
import hashlib
import json
import re
import shutil
import time
from pathlib import Path

import ai_visuals
import kaggle_runner
import make_video as mv

UPSCALED_DIR = ai_visuals.GEN_DIR / "upscaled"
WORK = UPSCALED_DIR / "_kaggle"
NOTEBOOK = mv.ROOT / "kaggle" / "upscale.ipynb"
SLUG = "ai-video-upscale"
MIN_SIDE = 2048
JOB_VERSION = 1
# hard cap per run, generous: Script2's 38 images took a 1.9 min session on a T4 (Oct 2026)
SETUP_HOURS, HOURS_PER_IMAGE, MAX_HOURS = 0.25, 0.01, 2.0


def key(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:20]


def cached_path(path):
    return UPSCALED_DIR / f"{key(path)}.jpg"


def long_side(path):
    from PIL import Image
    with Image.open(path) as img:
        return max(img.size)


def scene_images(scenes, formats):
    """The distinct image files the render will use (videos and title cards aren't upscaled)."""
    found = []
    for s in scenes:
        for f in formats:
            for src in (mv.scene_src(s, f), mv.scene_src2(s, f)):
                if src and Path(src).suffix.lower() in mv.IMAGE_EXT and Path(src) not in found:
                    found.append(Path(src))
    return found


def export_job(paths, job_dir):
    shutil.rmtree(job_dir, ignore_errors=True)
    job_dir.mkdir(parents=True)
    items = []
    for p in paths:
        k = key(p)
        shutil.copy(p, job_dir / f"{k}{p.suffix.lower()}")
        items.append({"key": k, "file": f"{k}{p.suffix.lower()}"})
    (job_dir / "job.json").write_text(json.dumps({"version": JOB_VERSION, "min_side": MIN_SIDE, "images": items},
                                                 indent=2), encoding="utf-8")
    return items


def collect(run_dir, wanted):
    """Copy the notebook's upscaled images into the cache, checking each opens and is big enough. Returns
    (number cached, the manifest's per-image entries)."""
    from PIL import Image
    try:
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0, []
    UPSCALED_DIR.mkdir(parents=True, exist_ok=True)
    done = 0
    for item in manifest.get("images", []):
        f = run_dir / f"{item.get('key')}.jpg"
        if item.get("key") not in wanted or not f.is_file():
            continue
        try:
            with Image.open(f) as img:
                img.verify()
            if long_side(f) < MIN_SIDE:
                continue
        except OSError:
            continue
        shutil.copy(f, UPSCALED_DIR / f.name)
        done += 1
    return done, manifest.get("images", [])


def apply(scenes):
    """Point every scene at its upscaled images where they exist (s["upscaled_from"] keeps the original, so the
    YouTube notes still know whose picture it was). Returns how many files were swapped."""
    swapped = set()

    def better(src):
        if src and Path(src).suffix.lower() in mv.IMAGE_EXT and (up := cached_path(src)).exists():
            swapped.add(up)
            return up
        return src
    for s in scenes:
        if (new := better(s.get("src"))) != s.get("src"):
            s["upscaled_from"], s["src"] = s["src"], new
        if s.get("srcs"):
            s["srcs"] = {f: better(p) for f, p in s["srcs"].items()}
        if s.get("src2"):
            s["src2"] = better(s["src2"])
        if s.get("srcs2"):
            s["srcs2"] = {f: better(p) for f, p in s["srcs2"].items()}
    return len(swapped)


def hours_cap(n):
    return min(MAX_HOURS, SETUP_HOURS + n * HOURS_PER_IMAGE)


def warn(text, notes=None):
    """A warning that stands out in the log; `notes` collects it for the end-of-run summary."""
    print(f"  WARNING: {text}")
    if notes is not None:
        notes.append(text)


def failure(e):
    """Why Kaggle didn't upscale, in a line."""
    text = str(e).strip()
    if re.search(r"quota|exceed|weekly|no (more )?gpu|limit reached", text, re.I):
        return "Kaggle's weekly GPU hours look used up (they reset each week)"
    return text.splitlines()[0][:300] if text else e.__class__.__name__


def upscale_scenes(scenes, formats, cli=None, sleep=time.sleep, clock=time.monotonic,
                   interval=kaggle_runner.POLL_SECONDS, work=None, notes=None):
    """Upscale every small scene image not in the cache in one Kaggle run, then use the upscaled images.
    `work` holds the Kaggle job files (the video's Working/kaggle/upscale); the upscaled images always go to the shared
    cache. Never stops the render: when Kaggle can't help, the originals are used and a warning is printed (and added to
    `notes`). Returns the GPU minutes used (None if Kaggle wasn't needed or didn't report it)."""
    work = Path(work) if work else WORK
    images = scene_images(scenes, formats)
    small = [p for p in images if long_side(p) < MIN_SIDE]
    todo = [p for p in small if not cached_path(p).exists()]
    print(f"Upscale: {len(images)} scene image(s), {len(small)} under {MIN_SIDE} px, "
          f"{len(small) - len(todo)} already upscaled (cached)")
    used = None
    if todo:
        try:
            cli = cli or kaggle_runner.KaggleCLI(kaggle_runner.api_token("Upscaling"))
            job_dir = work / "job"
            items = export_job(todo, job_dir)
            print(f"Sending {len(todo)} image(s) to Kaggle for Real-ESRGAN in one batch...")
            status, run_dir, used, watched = kaggle_runner.run_notebook(
                cli, NOTEBOOK, SLUG, job_dir, work, hours_cap(len(todo)), sleep=sleep, clock=clock, interval=interval)
            got, entries = collect(run_dir, {i["key"] for i in items})
            gpu = (f"about {used:.1f} min (session length from Kaggle's log, which is what counts against the "
                   f"~30 free GPU hours a week)" if used is not None else
                   f"at most {watched:.0f} min (how long the run was watched; Kaggle's log had no timestamps)")
            print(f"  upscaled {got} of {len(todo)} image(s); GPU time used: {gpu}")
            if (secs := [e["seconds"] for e in entries if isinstance(e.get("seconds"), (int, float))]):
                print(f"  {sum(secs) / len(secs):.1f} s per image on the GPU, {sum(secs) / 60:.1f} min in total")
            if got < len(todo):
                warn(f"{len(todo) - got} of {len(todo)} image(s) weren't upscaled (Kaggle status {status.lower()}); "
                     "they're rendered from the originals. The next render retries only those.", notes)
        except (kaggle_runner.KaggleError, OSError, SystemExit) as e:  # SystemExit: no or a rejected KAGGLE_API_TOKEN
            warn(f"upscaling skipped: {failure(e)}. Rendering with the original images; the next render tries "
                 "again.", notes)
    n = apply(scenes)
    if n:
        print(f"  rendering with {n} upscaled image(s) from {UPSCALED_DIR}")
    return used
