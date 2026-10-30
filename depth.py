"""Depth masks for the Remotion renderer's subtle 2.5D parallax.

Depth Anything V2 Small (Apache-2.0, ~99 MB ONNX, downloaded once into models/) estimates which parts of an image are
near the camera. The near part is saved as a soft alpha mask (<image>_fg.png next to a copy of the image) so Remotion can
move it a little faster than the rest. Parallax is only used where it looks good: a clear foreground that is neither a
speck nor most of the frame, standing well in front of the background. Everything runs on the CPU (~1 s per image).
"""
import urllib.request
from pathlib import Path

from download_models import MODELS_DIR

MODEL_URL = "https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/main/onnx/model.onnx"
MODEL_FILE = MODELS_DIR / "depth-anything-v2-small.onnx"
INPUT = 518  # the model's native size (a multiple of its 14-pixel patches)
MEAN, STD = (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)
# a foreground worth separating: 6-60% of the frame, clearly nearer than the rest
FG_AREA = (0.06, 0.60)
MIN_SEPARATION = 0.20  # mean depth gap between foreground and background, on a 0-1 scale
MAX_TOP_EDGE, MAX_SIDE_EDGE = 0.15, 0.35  # share of the top / each side edge the foreground may touch
MASK_VERSION = "v2"  # part of the cached mask's name; bump when the mask recipe changes
PLATE_SIZE = 640  # the background behind the subject is filled in at this size, then blended back at full size

_session = None


def ensure_model():
    if MODEL_FILE.exists() and MODEL_FILE.stat().st_size > 0:
        return MODEL_FILE
    MODELS_DIR.mkdir(exist_ok=True)
    print("  downloading the depth model for parallax (99 MB, once)...")
    part = MODEL_FILE.with_suffix(".part")
    urllib.request.urlretrieve(MODEL_URL, part)
    part.replace(MODEL_FILE)
    return MODEL_FILE


def session():
    global _session
    if _session is None:
        import onnxruntime as ort
        _session = ort.InferenceSession(str(ensure_model()), providers=["CPUExecutionProvider"])
    return _session


def estimate(img):
    """Relative depth of a PIL image as a float array in 0-1 (1 = nearest), at the model's input size."""
    import numpy as np
    x = np.asarray(img.convert("RGB").resize((INPUT, INPUT)), np.float32) / 255
    x = ((x - MEAN) / STD).transpose(2, 0, 1)[None].astype(np.float32)
    sess = session()
    d = sess.run(None, {sess.get_inputs()[0].name: x})[0][0]
    lo, hi = np.percentile(d, 2), np.percentile(d, 98)
    return np.clip((d - lo) / max(hi - lo, 1e-6), 0, 1)


def split(depth):
    """(soft foreground mask 0-1, area, separation) from a 0-1 depth map. The threshold sits between the near and
    far halves (Otsu), and the edge is feathered so the moving layer has no hard outline."""
    import numpy as np
    hist, edges = np.histogram(depth, bins=64, range=(0, 1))
    p = hist / hist.sum()
    centers = (edges[:-1] + edges[1:]) / 2
    w = np.cumsum(p)
    mu = np.cumsum(p * centers)
    between = (mu[-1] * w - mu) ** 2 / np.maximum(w * (1 - w), 1e-9)
    t = centers[int(np.argmax(between))]
    near = depth > t
    area = float(near.mean())
    separation = float(depth[near].mean() - depth[~near].mean()) if 0 < area < 1 else 0.0
    soft = np.clip((depth - t) / 0.08 + 0.5, 0, 1)  # ramps over a narrow depth band around the threshold
    edges = {"top": float(near[0].mean()), "left": float(near[:, 0].mean()), "right": float(near[:, -1].mean())}
    return soft, area, separation, edges


def looks_good(area, separation, edges):
    """A subject standing in front of a backdrop: it may rest on the bottom edge, but a 'foreground' spread along the
    top or sides is a wall, a floor seen from above or an aerial view, and moving it would look like a wobble."""
    return (FG_AREA[0] <= area <= FG_AREA[1] and separation >= MIN_SEPARATION
            and edges["top"] <= MAX_TOP_EDGE and max(edges["left"], edges["right"]) <= MAX_SIDE_EDGE)


def foreground_mask(image):
    """The image's foreground mask (an L-mode PNG at the image's size, cached next to it as
    <stem>.<version>_fg.png) if the image suits parallax, else None."""
    from PIL import Image, ImageFilter
    import numpy as np
    image = Path(image)
    cached = image.with_name(f"{image.stem}.{MASK_VERSION}_fg.png")
    verdict = image.with_name(f"{image.stem}.{MASK_VERSION}_flat")  # marker: checked, no parallax
    if verdict.exists():
        return None
    if not cached.exists():
        with Image.open(image) as img:
            size = img.size
            soft, *stats = split(estimate(img))
        if not looks_good(*stats):
            verdict.touch()
            return None
        mask = Image.fromarray((soft * 255).astype(np.uint8)).resize(size, Image.BILINEAR)
        mask.filter(ImageFilter.GaussianBlur(max(size) / 200)).save(cached)
    return cached


def background_plate(image, mask):
    """The picture with its foreground filled in from the surroundings (OpenCV inpainting), cached as
    <stem>.<version>_bg.jpg. Strong parallax moves the subject over this plate instead of over a copy of itself, so
    the area it uncovers shows background, never a stretched or doubled edge."""
    import cv2
    import numpy as np
    image = Path(image)
    out = image.with_name(f"{image.stem}.{MASK_VERSION}_bg.jpg")
    if out.exists():
        return out
    img = cv2.imread(str(image))
    m = cv2.imread(str(mask), cv2.IMREAD_GRAYSCALE)
    if img is None or m is None:
        return None
    h, w = img.shape[:2]
    scale = PLATE_SIZE / max(h, w)
    small = cv2.resize(img, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    hole = (cv2.resize(m, small.shape[1::-1]) > 60).astype(np.uint8) * 255
    hole = cv2.dilate(hole, np.ones((17, 17), np.uint8))  # a margin round the subject: its own halo goes too
    filled = cv2.GaussianBlur(cv2.inpaint(small, hole, 9, cv2.INPAINT_TELEA), (0, 0), 2)
    big = cv2.resize(filled, (w, h), interpolation=cv2.INTER_CUBIC).astype(np.float32)
    alpha = cv2.GaussianBlur(cv2.resize(hole, (w, h)).astype(np.float32) / 255, (0, 0), max(w, h) / 150)[..., None]
    plate = img.astype(np.float32) * (1 - alpha) + big * alpha
    cv2.imwrite(str(out), plate.astype(np.uint8), [cv2.IMWRITE_JPEG_QUALITY, 90])
    return out
