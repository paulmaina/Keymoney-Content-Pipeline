# %% [markdown]
# # AI video clips for make_video.py (Wan 2.2 on Kaggle)
#
# Turns the job exported by `make_video.py --export-video-job` into short video clips,
# plus any still images the job asks for. Built for Kaggle's free **GPU T4 x2**.
#
# **Before "Run All", in the right-hand panel (Session options / Settings):**
# 1. **Accelerator: GPU T4 x2.**
# 2. **Internet: On.** It's needed to download the models, about 30-40 GB on the first run, cached for the session.
#    Kaggle may ask you to verify your phone number before it allows Internet and GPUs.
# 3. **Add Input**: attach the dataset you made by uploading `<name>_video_job.zip`.
#
# Then **Run All**. When it finishes, download `clips.zip` from the **Output** tab (`/kaggle/working/clips.zip`)
# and **stop the session** (power icon, top right) so you don't use up the ~30 free GPU hours a week.
#
# The run can be resumed: clips and images already in `/kaggle/working/` are skipped, so if a session times out,
# start it again and Run All. `clips.zip` is rebuilt after every clip, so it's always downloadable.
#
# **Models**
# - **Wan 2.2 TI2V-5B** (image to video), starting from each scene's image, 121 frames at 24 fps (~5 s).
#   The default size is **960x544**; the job can ask for 1280x704 (`make_video.py --kaggle-size 1280x704`).
#   T4s have no bf16, so it runs in fp16. The VAE stays in fp32, as the diffusers docs recommend, with tiling.
# - **Fallback: Wan 2.1 T2V-1.3B** at 832x480, 81 frames at 16 fps (~5 s). This is text-to-video only, so it
#   won't match the scene image. It's used only if TI2V-5B can't load, runs out of memory, or returns broken frames,
#   and the log and manifest say why.
# - **SDXL** (fp16, with the fp16-fix VAE) for scenes that need a still image made here.
#
# **How TI2V-5B fits a 15 GB T4.** Loading it whole doesn't fit: the 720p attempt ran out of memory both when split
# across the GPUs and with whole-model CPU offload.
# 1. The 11 GB text encoder runs once, on the second T4, to encode every clip's prompt, and is then freed.
# 2. The 10 GB video model uses **leaf-level group offloading** with CUDA streams. Its weights stay in CPU RAM,
#    and only the layers being computed are on the GPU, with the next ones copied ahead.
# 3. The fp32 VAE has tiling, so decoding works in pieces.
# Each clip's peak GPU memory is printed and recorded in the manifest.
#
# **Why not one clip per GPU?** Each worker would need its own ~10 GB copy of the model pinned in CPU RAM for
# offloading, plus working memory, and Kaggle gives about 29 GB. So clips are made one at a time on the first T4.
#
# **Timing:** the Wan 2.1 fallback took 40 minutes for one 480p clip on a T4. TI2V-5B with offloading is untested
# here, so the notebook prints the real time per clip and an estimate for the rest.

# %%
# Install pinned packages. Kaggle's preinstalled PyTorch (with CUDA) is used as-is.
import subprocess
import sys

PINS = ["diffusers==0.42.1", "transformers==5.21.0", "accelerate==1.15.0", "ftfy==6.3.1",
        "sentencepiece==0.2.2", "imageio==2.38.1", "imageio-ffmpeg==0.7.0"]
subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *PINS])

# %%
# Settings: edit these to trade quality for speed.
import os

os.environ.setdefault("HF_HOME", "/tmp/hf")  # model downloads go to temporary disk, not the 20 GB output folder

TI2V_MODEL = "Wan-AI/Wan2.2-TI2V-5B-Diffusers"
DEFAULT_SIZE = (960, 544)  # used when the job doesn't set video_size; 1280x704 is the model's full 720p size
FPS = 24
STEPS, GUIDANCE = 30, 5.0  # the model card uses 50 steps; 30 is ~40% faster with a small quality cost

FALLBACK_MODEL = "Wan-AI/Wan2.1-T2V-1.3B-Diffusers"
FB_WIDTH, FB_HEIGHT, FB_FPS, FB_STEPS, FB_SHIFT = 832, 480, 16, 30, 3.0  # 480p; low flow shift suits low resolution

SDXL_MODEL = "stabilityai/stable-diffusion-xl-base-1.0"
SDXL_VAE = "madebyollin/sdxl-vae-fp16-fix"  # the stock SDXL VAE produces NaNs in fp16
SDXL_STEPS, SDXL_SIZE = 30, 1024

NEGATIVE = ("text, watermark, logo, subtitles, static, still picture, blurred details, worst quality, low quality, "
            "JPEG artifacts, deformed, disfigured, extra fingers, poorly drawn hands, poorly drawn faces, "
            "messy background, walking backwards")

OUT = "/kaggle/working"
CLIPS_DIR, IMAGES_DIR = f"{OUT}/clips", f"{OUT}/images"
MANIFEST, ZIP_PATH = f"{OUT}/manifest.json", f"{OUT}/clips.zip"

# %%
# Find the job and check the GPUs.
import glob
import json

import torch

jobs = sorted(glob.glob("/kaggle/input/**/job.json", recursive=True))
if not jobs:  # the dataset kept the zip packed: unpack it into a scratch folder
    import zipfile as _zip
    for z in glob.glob("/kaggle/input/**/*_video_job.zip", recursive=True):
        _zip.ZipFile(z).extractall("/tmp/job")
    jobs = sorted(glob.glob("/tmp/job/**/job.json", recursive=True))
if not jobs:
    raise SystemExit("No job.json under /kaggle/input. Use 'Add Input' to attach the dataset made from "
                     "<name>_video_job.zip, then Run All again.")
JOB_DIR = os.path.dirname(jobs[0])
job = json.load(open(jobs[0], encoding="utf-8"))
print(f"Job '{job.get('name')}' from {JOB_DIR}: "
      f"{sum(e['task'] == 'video' for e in job['scenes'])} clip(s), "
      f"{sum(not e.get('image') for e in job['scenes'])} image(s) to make")

if not torch.cuda.is_available():
    raise SystemExit("No GPU. Set Accelerator to 'GPU T4 x2' in the session settings, then Run All.")
GPUS = torch.cuda.device_count()
for g in range(GPUS):
    print(f"cuda:{g} {torch.cuda.get_device_name(g)} "
          f"{torch.cuda.get_device_properties(g).total_memory / 2**30:.0f} GB")
print("torch", torch.__version__)

WIDTH, HEIGHT = job.get("video_size") or DEFAULT_SIZE
if WIDTH % 32 or HEIGHT % 32:
    raise SystemExit(f"video_size {WIDTH}x{HEIGHT} must be multiples of 32 for Wan 2.2 TI2V-5B")
print(f"Clip size: {WIDTH}x{HEIGHT}")

os.makedirs(CLIPS_DIR, exist_ok=True)
os.makedirs(IMAGES_DIR, exist_ok=True)

# Resuming (make_video.py --kaggle-resume): clips and images from earlier runs come back as flat
# prev_* files in the dataset. Restore them so they're skipped below.
import shutil

prev = os.path.join(JOB_DIR, "prev_manifest.json")
if os.path.exists(prev) and not os.path.exists(f"{OUT}/manifest.json"):
    restored = json.load(open(prev, encoding="utf-8"))
    for item in restored["items"]:
        shutil.copy(os.path.join(JOB_DIR, "prev_" + item["file"].replace("/", "__")), f"{OUT}/{item['file']}")
    json.dump({"job": job.get("name"), "items": restored["items"]}, open(f"{OUT}/manifest.json", "w"), indent=2)
    print(f"Resuming: restored {len(restored['items'])} finished file(s) from earlier runs")

# %%
# Helpers: manifest, zip, timing.
import gc
import time
import zipfile

import numpy as np
from PIL import Image

manifest = json.load(open(MANIFEST)) if os.path.exists(MANIFEST) else {"job": job.get("name"), "items": []}


def record(item):
    """Add or replace a manifest entry and rebuild clips.zip, so progress survives a timeout."""
    manifest["items"] = [i for i in manifest["items"] if i["file"] != item["file"]] + [item]
    json.dump(manifest, open(MANIFEST, "w"), indent=2)
    with zipfile.ZipFile(ZIP_PATH, "w") as z:
        z.write(MANIFEST, "manifest.json")
        for i in manifest["items"]:
            z.write(f"{OUT}/{i['file']}", i["file"])


def free_gpu():
    gc.collect()
    torch.cuda.empty_cache()


def frames_for(seconds, fps):
    """Wan needs 4k+1 frames."""
    return int(round(seconds * fps / 4)) * 4 + 1


def fit_image(path, w, h):
    """Centre-crop to w:h, then resize; the scene images are square."""
    im = Image.open(path).convert("RGB")
    scale = max(w / im.width, h / im.height)
    im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    left, top = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((left, top, left + w, top + h))


def looks_broken(frames):
    """fp16 overflow shows up as NaNs or flat black/grey frames."""
    arr = np.asarray(frames, dtype=np.float32)
    return not np.isfinite(arr).all() or arr.std() < 0.01


class Timer:
    def __init__(self, total):
        self.total, self.done, self.spent = total, 0, 0.0

    def tick(self, seconds, label):
        self.done += 1
        self.spent += seconds
        left = (self.total - self.done) * self.spent / self.done
        print(f"  {label}: {seconds / 60:.1f} min; {self.total - self.done} left, ~{left / 60:.0f} min remaining")


# %%
# Phase 1: still images (SDXL) for scenes that don't have one yet.
image_tasks = [e for e in job["scenes"] if not e.get("image")]
todo = [e for e in image_tasks if not os.path.exists(f"{IMAGES_DIR}/scene_{e['scene']:03}.png")]
print(f"Images: {len(image_tasks) - len(todo)} done already, {len(todo)} to make")
if todo:
    from diffusers import AutoencoderKL, StableDiffusionXLPipeline

    vae = AutoencoderKL.from_pretrained(SDXL_VAE, torch_dtype=torch.float16)
    sdxl = StableDiffusionXLPipeline.from_pretrained(SDXL_MODEL, vae=vae, torch_dtype=torch.float16,
                                                     variant="fp16", use_safetensors=True).to("cuda:0")
    timer = Timer(len(todo))
    for e in todo:
        start = time.time()
        name = f"images/scene_{e['scene']:03}.png"
        # SDXL's text encoders read the first 77 tokens, so the start of the prompt matters most
        image = sdxl(prompt=e["image_prompt"], negative_prompt="text, watermark, logo, blurry, deformed",
                     width=SDXL_SIZE, height=SDXL_SIZE, num_inference_steps=SDXL_STEPS,
                     generator=torch.Generator("cpu").manual_seed(e["seed"])).images[0]
        image.save(f"{OUT}/{name}.part.png")
        os.replace(f"{OUT}/{name}.part.png", f"{OUT}/{name}")
        record({"scene": e["scene"], "narration": e["narration"], "kind": "image", "file": name,
                "model": SDXL_MODEL, "prompt": e["image_prompt"], "width": SDXL_SIZE, "height": SDXL_SIZE})
        timer.tick(time.time() - start, f"scene {e['scene']} image")
    del sdxl, vae
    free_gpu()


def scene_image(e):
    if e.get("image"):
        return f"{JOB_DIR}/{e['image']}"
    return f"{IMAGES_DIR}/scene_{e['scene']:03}.png"


# %%
# Phase 2: video clips. Wan 2.2 TI2V-5B with group offloading first; if it fails to load, runs out of memory
# or returns broken frames, the clip is retried with the Wan 2.1 fallback and the reason is logged.
from diffusers import AutoencoderKLWan, UniPCMultistepScheduler, WanImageToVideoPipeline, WanPipeline
from diffusers.utils import export_to_video

MAIN, FALLBACK = "Wan2.2-TI2V-5B", "Wan2.1-T2V-1.3B"
GPU0 = torch.device("cuda:0")
TEXT_GPU = "cuda:1" if GPUS > 1 else "cuda:0"


def gpu_peaks():
    return {f"cuda:{g}": round(torch.cuda.max_memory_allocated(g) / 2**30, 2) for g in range(GPUS)}


def reset_peaks():
    for g in range(GPUS):
        torch.cuda.reset_peak_memory_stats(g)


def clip_prompt(e):
    return f"{e['motion_prompt']}. {e['image_prompt']}"


def encode_all(pipe, entries):
    """Run the 11 GB text encoder once for every clip on the second T4, then free it."""
    pipe.text_encoder.to(TEXT_GPU)
    embeds = {}
    with torch.no_grad():
        for e in entries:
            pe, ne = pipe.encode_prompt(prompt=clip_prompt(e), negative_prompt=NEGATIVE,
                                        do_classifier_free_guidance=True, device=torch.device(TEXT_GPU))
            embeds[e["scene"]] = (pe.to("cpu"), ne.to("cpu"))
    print(f"  encoded {len(embeds)} prompt(s) on {TEXT_GPU}, peak {gpu_peaks()} GB; freeing the text encoder")
    # drop it straight from the GPU: moving 11 GB back to CPU RAM would crowd out the pinned weights
    # that group offloading with streams keeps there
    pipe.register_modules(text_encoder=None)
    free_gpu()
    return embeds


def load_main(entries):
    vae = AutoencoderKLWan.from_pretrained(TI2V_MODEL, subfolder="vae", torch_dtype=torch.float32)
    pipe = WanImageToVideoPipeline.from_pretrained(TI2V_MODEL, vae=vae, torch_dtype=torch.float16)
    embeds = encode_all(pipe, entries)
    # leaf-level group offloading: weights stay in CPU RAM; only the layers being computed are on the GPU,
    # and a CUDA stream copies the next ones ahead
    pipe.transformer.enable_group_offload(onload_device=GPU0, offload_device=torch.device("cpu"),
                                          offload_type="leaf_level", use_stream=True)
    pipe.to(GPU0)  # moves the VAE; group-offloaded modules manage themselves (as in the diffusers Wan docs)
    pipe.vae.enable_tiling()
    if hasattr(pipe.vae, "enable_slicing"):
        pipe.vae.enable_slicing()
    return pipe, embeds


def load_fallback():
    vae = AutoencoderKLWan.from_pretrained(FALLBACK_MODEL, subfolder="vae", torch_dtype=torch.float32)
    pipe = WanPipeline.from_pretrained(FALLBACK_MODEL, vae=vae, torch_dtype=torch.float16)
    pipe.scheduler = UniPCMultistepScheduler.from_config(pipe.scheduler.config, flow_shift=FB_SHIFT)
    pipe.enable_model_cpu_offload()
    pipe.vae.enable_tiling()
    return pipe, None


def make_clip(pipe, embeds, model, e):
    gen = torch.Generator("cpu").manual_seed(e["seed"])
    if model == MAIN:
        w, h, fps = WIDTH, HEIGHT, FPS
        pe, ne = embeds[e["scene"]]
        frames = pipe(image=fit_image(scene_image(e), w, h), prompt_embeds=pe.to(GPU0),
                      negative_prompt_embeds=ne.to(GPU0), height=h, width=w,
                      num_frames=frames_for(e.get("seconds", 5), fps), num_inference_steps=STEPS,
                      guidance_scale=GUIDANCE, generator=gen).frames[0]
    else:  # text-to-video fallback: describe the scene image and the motion together
        w, h, fps = FB_WIDTH, FB_HEIGHT, FB_FPS
        frames = pipe(prompt=clip_prompt(e), negative_prompt=NEGATIVE, height=h, width=w,
                      num_frames=frames_for(e.get("seconds", 5), fps), num_inference_steps=FB_STEPS,
                      guidance_scale=GUIDANCE, generator=gen).frames[0]
    if looks_broken(frames):
        raise FloatingPointError("broken frames (NaN or blank), probably fp16 overflow")
    return frames, w, h, fps


video_tasks = [e for e in job["scenes"] if e["task"] == "video"]
todo = [e for e in video_tasks if not os.path.exists(f"{CLIPS_DIR}/scene_{e['scene']:03}.mp4")]
print(f"Clips: {len(video_tasks) - len(todo)} done already, {len(todo)} to make")
models = [MAIN, FALLBACK]
fallback_reasons = []  # why the main model was given up, recorded on every clip the fallback makes
pipe = embeds = current = None
timer = Timer(len(todo))
for e in todo:
    name = f"clips/scene_{e['scene']:03}.mp4"
    start = time.time()
    while models:
        model = models[0]
        try:
            if current != model:
                pipe = embeds = None
                free_gpu()
                reset_peaks()
                print(f"Loading {model}...")
                pipe, embeds = load_main(todo) if model == MAIN else load_fallback()
                current = model
            reset_peaks()
            frames, w, h, fps = make_clip(pipe, embeds, model, e)
            break
        except (torch.cuda.OutOfMemoryError, FloatingPointError, RuntimeError, ValueError, OSError,
                AttributeError, TypeError) as err:
            reason = f"{model} failed on scene {e['scene']}: {type(err).__name__}: {str(err)[:240]}"
            print(f"FALLBACK: {reason} (peak GPU memory {gpu_peaks()} GB)")
            fallback_reasons.append(reason)
            models.pop(0)
            pipe = embeds = current = None
            free_gpu()
    else:
        raise SystemExit("Every model failed; see the FALLBACK lines above. Clips made so far are in clips.zip.")
    peaks = gpu_peaks()
    export_to_video(frames, f"{OUT}/{name}.part.mp4", fps=fps)
    os.replace(f"{OUT}/{name}.part.mp4", f"{OUT}/{name}")
    seconds = round(len(frames) / fps, 2)
    record({"scene": e["scene"], "narration": e["narration"], "kind": "video", "file": name,
            "model": model, "width": w, "height": h, "frames": len(frames), "fps": fps, "seconds": seconds,
            "seed": e["seed"], "minutes": round((time.time() - start) / 60, 1), "peak_gpu_gb": peaks,
            "fallback_reason": "; ".join(fallback_reasons) if model != MAIN else None})
    why = f", fallback because {fallback_reasons[-1]}" if model != MAIN and fallback_reasons else ""
    timer.tick(time.time() - start, f"scene {e['scene']} clip by {model} ({w}x{h}, {seconds}s, "
                                    f"peak GPU memory {peaks} GB{why})")

# %%
# Summary. Download clips.zip from the Output tab, then stop the session.
for i in sorted(manifest["items"], key=lambda i: (i["kind"], i["scene"])):
    detail = f"{i['model']} {i['width']}x{i['height']}"
    if i["kind"] == "video":
        detail += f", {i['seconds']}s in {i.get('minutes')} min, peak GPU memory {i.get('peak_gpu_gb')} GB"
        if i.get("fallback_reason"):
            detail += f"\n      FALLBACK because {i['fallback_reason']}"
    print(f"scene {i['scene']:>3} {i['kind']:<5} {detail}")
print(f"\n{ZIP_PATH} ({os.path.getsize(ZIP_PATH) / 2**20:.1f} MB): download it from the Output tab, "
      "then run make_video.py with --clips clips.zip. Remember to stop the session.")
