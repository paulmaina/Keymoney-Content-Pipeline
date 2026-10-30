# %% [markdown]
# # Real-ESRGAN upscaling for make_video.py (every final render)
#
# Every final render of `make_video.py` pushes this notebook to a private Kaggle kernel on its own, with a private dataset
# holding `job.json` and the scene images that are under 2048 px. You don't need to open it on the website.
#
# Each image is enlarged **2x** with **RealESRGAN_x2plus** (4x with RealESRGAN_x4plus if 2x would still be under
# 2048 px), in fp16 on one T4, in 512 px tiles with a 16 px overlap so any size fits in memory. The results are saved
# as `<key>.jpg` (quality 95) in `/kaggle/working`, with `manifest.json` listing each one's size and GPU seconds.
#
# Models are loaded with [spandrel](https://github.com/chaiNNer-org/spandrel), so the old `basicsr` package (which
# breaks on current torchvision) isn't needed. Weights: Real-ESRGAN (BSD-3-Clause) from its GitHub releases.

# %%
# Install the model loader. Kaggle's preinstalled PyTorch (with CUDA) is used as-is.
import subprocess
import sys

subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "spandrel==0.4.1"])

# %%
# Find the job and load the images list.
import glob
import json
import os
import time
import urllib.request

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image
from spandrel import ModelLoader

OUT = "/kaggle/working"
WEIGHTS = {
    2: "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth",
    4: "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
}
TILE, OVERLAP = 512, 16

jobs = sorted(glob.glob("/kaggle/input/**/job.json", recursive=True))
if not jobs:
    raise SystemExit("No job.json under /kaggle/input: attach the dataset make_video.py uploaded.")
JOB_DIR = os.path.dirname(jobs[0])
job = json.load(open(jobs[0], encoding="utf-8"))
MIN_SIDE = job.get("min_side", 2048)
device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"Images: {len(job['images'])} to upscale to at least {MIN_SIDE} px on {device}"
      f"{' (' + torch.cuda.get_device_name(0) + ')' if device == 'cuda' else ' (no GPU: this will be slow)'}")

# %%
# Load models on first use, and upscale in overlapping tiles.
models = {}


def model(scale):
    if scale not in models:
        path = f"/tmp/{os.path.basename(WEIGHTS[scale])}"
        if not os.path.exists(path):
            print(f"Loading RealESRGAN x{scale} weights...")
            urllib.request.urlretrieve(WEIGHTS[scale], path)
        m = ModelLoader().load_from_file(path)
        assert m.scale == scale, f"expected a x{scale} model, got x{m.scale}"
        m = m.to(device).eval()
        if device == "cuda" and m.supports_half:
            m = m.half()
        models[scale] = m
    return models[scale]


@torch.inference_mode()
def upscale(img, scale):
    m = model(scale)
    x = torch.from_numpy(np.asarray(img.convert("RGB"))).permute(2, 0, 1).unsqueeze(0).to(device)
    x = x.half() if device == "cuda" and m.supports_half else x.float()
    x = x / 255
    _, _, h, w = x.shape
    out = torch.zeros((1, 3, h * scale, w * scale), dtype=x.dtype, device=device)
    for y0 in range(0, h, TILE):
        for x0 in range(0, w, TILE):
            y1, x1 = min(y0 + TILE, h), min(x0 + TILE, w)
            py0, px0, py1, px1 = max(y0 - OVERLAP, 0), max(x0 - OVERLAP, 0), min(y1 + OVERLAP, h), min(x1 + OVERLAP, w)
            tile = x[:, :, py0:py1, px0:px1]
            th, tw = tile.shape[2:]
            tile = F.pad(tile, (0, tw % 2, 0, th % 2), mode="reflect")  # x2plus needs even sides
            r = m(tile)[:, :, :th * scale, :tw * scale]
            oy, ox = (y0 - py0) * scale, (x0 - px0) * scale
            out[:, :, y0 * scale:y1 * scale, x0 * scale:x1 * scale] = \
                r[:, :, oy:oy + (y1 - y0) * scale, ox:ox + (x1 - x0) * scale]
    a = out.clamp(0, 1).mul(255).round().byte().squeeze(0).permute(1, 2, 0).cpu().numpy()
    return Image.fromarray(a)


# %%
# Upscale every image; manifest.json is rewritten after each one, so a timeout keeps what's done.
done = []
start = time.time()
for n, item in enumerate(job["images"], 1):
    try:
        img = Image.open(os.path.join(JOB_DIR, item["file"]))
        scale = 2 if max(img.size) * 2 >= MIN_SIDE else 4
        t = time.time()
        big = upscale(img, scale)
        if device == "cuda":
            torch.cuda.synchronize()
        secs = time.time() - t
        big.save(f"{OUT}/{item['key']}.jpg", quality=95)
        done.append({"key": item["key"], "scale": scale, "width": big.width, "height": big.height,
                     "seconds": round(secs, 2)})
        print(f"  image {n}/{len(job['images'])}: {img.width}x{img.height} -> {big.width}x{big.height} "
              f"(x{scale}) in {secs:.1f}s")
    except Exception as e:  # one bad image shouldn't lose the rest
        print(f"  image {n}/{len(job['images'])} failed: {e.__class__.__name__}: {e}")
    json.dump({"images": done}, open(f"{OUT}/manifest.json", "w"), indent=1)

print(f"Images: {len(done)} of {len(job['images'])} done in {(time.time() - start) / 60:.1f} min")
