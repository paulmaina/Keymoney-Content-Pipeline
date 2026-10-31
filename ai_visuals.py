"""AI-generated scene images (used by make_video.py --generate-visuals).

For each scene without a [visual: ...] line, a Workers AI text model turns the narration
into an image prompt, then an image provider draws it (Providers):

  final render  Cloudflare writes every prompt and draws every image (--image-model, default FLUX.2 [klein] 4B),
                and keeps billing past the free 10,000 neurons a day (Workers Paid) up to max-daily-neurons, counted
                per day in Nairobi time. Pollinations is only the emergency backup: for a scene whose Cloudflare call
                still fails after one retry, or once the daily cap is reached. Those scenes are listed at the end of
                the run with the --redo-scenes command that redraws them with Cloudflare.
  draft         (--draft, --preview-scenes, --thumbnail-ideas, --style-test) Pollinations draws the images into
                generated/drafts/, a cache a final render never reads. Prompts still come from Cloudflare while it's
                within the cap, so the final render reuses them.

Models that take a width and height (all but FLUX.1 schnell) draw a real 16:9 image for the horizontal
video and a real 9:16 one for the vertical video, so nothing important is cropped away. FLUX.1 schnell
only makes squares, which the renderers crop to each format.

Prompts are saved to Exports/<name>/Working/prompts.json for review and editing (rewritten when the style changes); a
prompt Cloudflare didn't write is marked "stand_in" and written again by Cloudflare on the next final render.
Final images are cached in generated/ (keyed by prompt + size + model), so reruns don't spend neurons.
"""
import base64
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, NamedTuple, Optional

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
GEN_DIR = ROOT / "generated"
API = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
POLLINATIONS_API = "https://gen.pollinations.ai/image/{prompt}"
# writes the image prompts and the YouTube metadata. On Script2 the 8B model put the channel's boy into
# Sonam Wangchuk's scene and called Alfredo Moser Nigerian, and the 3B / 8B wrote titles the narration
# contradicts; the 70B was accurate. About 50 neurons per scene prompt and ~65 per video's metadata.
TEXT_MODEL = METADATA_MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
POLLINATIONS_MODEL = "flux"  # alias for black-forest-labs/flux.1-schnell
POLLINATIONS_CACHE_ID = "pollinations:flux"
# when Cloudflare's text model is out of allowance, prompts are written by Pollinations' text model (same key)
POLLINATIONS_TEXT_API = "https://gen.pollinations.ai/v1/chat/completions"
POLLINATIONS_TEXT_MODEL = None  # None = the service's default, a small low-cost model
NOT_ALLOWED = re.compile(r"isn.?t allowed to use|allow it for this key|pollinations\.ai/grant", re.I)
USAGE_FILE_NAME = "cloudflare_usage.json"  # in GEN_DIR: neurons this PC spent today (UTC day and Nairobi day)
MODES = ("final", "draft")
MAX_DAILY_NEURONS = 60_000  # max-daily-neurons default: about $0.55 a day beyond the free 10,000
NEURON_PRICE = 0.011 / 1000  # US$ per neuron beyond the free allowance (Workers Paid plan, Oct 2026)
NAIROBI = timezone(timedelta(hours=3), "EAT")  # the cap's day; East Africa Time has no daylight saving
CF_RETRY_WAIT = (2, 10)  # seconds before Cloudflare's one retry: after an error, after a rate limit
IMAGE_PX = 1024  # square images
# real 16:9 and 9:16 images for models that take a size: multiples of 16, upscaled to 1080p by the renderer
# (sharpened to 3072 px on Kaggle first)
SIZES = {"horizontal": (1536, 864), "vertical": (864, 1536)}
MAX_PROMPT = 2048  # FLUX.1 schnell prompt limit
ATTEMPTS = 3
FREE_NEURONS_PER_DAY = 10_000
# Workers AI pricing page: neurons per million input / output tokens
TEXT_NEURONS = {
    "@cf/meta/llama-3.1-8b-instruct-fp8": (13_778, 26_128),
    "@cf/meta/llama-3.1-8b-instruct-fp8-fast": (4_119, 34_868),
    "@cf/meta/llama-3.2-3b-instruct": (4_625, 30_475),
    "@cf/meta/llama-3.3-70b-instruct-fp8-fast": (26_668, 204_805),
    "@cf/meta/llama-4-scout-17b-16e-instruct": (24_545, 77_273),
    "@cf/meta/llama-3.2-11b-vision-instruct": (4_410, 61_493),
}
# --check-images: a vision model looks at every image for broken anatomy and stray text. Llama 4 Scout is natively
# multimodal and needs no licence step (Llama 3.2 Vision does). The image goes in at QC_SIDE px: about 40 neurons a check.
VISION_MODEL = "@cf/meta/llama-4-scout-17b-16e-instruct"
QC_SIDE = 1024
QC_RETRIES = 2  # redraws after a failed check; then the best of the tries is kept and flagged
QC_IMAGE_TOKENS = 1_200  # guess for the image's share of the input, when the reply has no usage
QC_SYSTEM = (
    "You check AI-generated photographs for a documentary video before they are used. Look carefully at every person "
    "and animal, one at a time. Reply with JSON only: "
    '{"ok": true or false, "problems": ["short description", ...], "score": 0-10}. '
    "Set ok to false if you see any of these: an extra or missing arm, leg, hand or finger (count the fingers on every "
    "visible hand); fused, twisted or floating limbs; a hand or arm that belongs to no one; a duplicated, melted or "
    "distorted face; an extra head; or any letters, words, numbers, logos or watermarks anywhere in the image. "
    "Tiny background figures too small to judge don't count, and neither do style, lighting or composition. "
    "score is the image's overall soundness: 10 means no defects at all. If ok is true, problems is empty."
)


def tiles(w, h):
    return w * h / 512 ** 2


class ImageModel(NamedTuple):
    name: str  # short name accepted by --image-model
    sized: bool  # takes width/height, so each format gets its own image
    form: bool  # FLUX.2 takes multipart/form-data, the others JSON
    steps: Optional[int]  # sent with each request; None where the model fixes it
    steps_key: str
    max_side: int
    neurons: Callable[[int, int], float]  # cost of one w x h image, from the Workers AI pricing page (Oct 2026)
    note: str


IMAGE_MODELS = {m.name: m for m in (
    ImageModel("flux-2-klein-4b", True, True, None, "", 1920, lambda w, h: tiles(w, h) * 26.05,
               "realistic people, light and materials; 4 fixed steps; the only realistic model that fits the "
               "free allowance (~132 neurons for 1536x864)"),
    ImageModel("flux-1-schnell", False, False, 4, "steps", 1024, lambda w, h: tiles(w, h) * 4.80 + 4 * 9.60,
               "cheapest (58 neurons), square only, plastic-looking skin and an illustrated feel"),
    ImageModel("flux-2-klein-9b", True, True, None, "", 1920,
               lambda w, h: 1363.64 + 181.82 * max(0.0, w * h / 1024 ** 2 - 1),
               "sharper detail than klein 4B, but ~1,370 neurons an image (7 a day)"),
    ImageModel("flux-2-dev", True, True, 20, "steps", 1920, lambda w, h: tiles(w, h) * 37.50 * 20,
               "the most photographic, but ~3,800 neurons an image at 20 steps"),
    ImageModel("lucid-origin", True, False, 20, "num_steps", 2500, lambda w, h: tiles(w, h) * 636 + 20 * 12,
               "Leonardo; polished, often stylised; ~3,450 neurons an image"),
    ImageModel("phoenix-1.0", True, False, 25, "num_steps", 2048, lambda w, h: tiles(w, h) * 530 + 25 * 10,
               "Leonardo; strong prompt adherence; ~2,930 neurons an image"),
)}
MODEL_IDS = {"flux-1-schnell": "@cf/black-forest-labs/flux-1-schnell", "lucid-origin": "@cf/leonardo/lucid-origin",
             "phoenix-1.0": "@cf/leonardo/phoenix-1.0"}
MODEL_IDS.update({n: f"@cf/black-forest-labs/{n}" for n in ("flux-2-klein-4b", "flux-2-klein-9b", "flux-2-dev")})
IMAGE_MODEL = MODEL_IDS["flux-2-klein-4b"]
SQUARE_MODEL = MODEL_IDS["flux-1-schnell"]
UNKNOWN_MODEL = ImageModel("other", False, False, None, "", 1024, lambda w, h: 0.0, "not in the price list")


def resolve_model(name):
    """Full Workers AI ID for a short name ('flux-2-klein-4b') or an ID ('@cf/...')."""
    return MODEL_IDS.get(name, name)


def model_info(model_id):
    return IMAGE_MODELS.get(model_id.rsplit("/", 1)[-1], UNKNOWN_MODEL) if model_id.startswith("@cf/") else UNKNOWN_MODEL


def dims(size):
    return SIZES[size] if size in SIZES else (IMAGE_PX, IMAGE_PX)


def image_neurons(model_id, size="square"):
    return model_info(model_id).neurons(*dims(size))


FLUX_NEURONS = image_neurons(SQUARE_MODEL)  # 57.6 for a 1024x1024, 4-step FLUX.1 schnell image
# the renderers' pans and zooms trim the edges, so keep the subject away from them
COMPOSITION = {
    "square": "centered composition, main subject in the middle of the frame, no text, no watermark",
    "horizontal": "wide 16:9 frame, main subject near the centre with room around it, no text, no watermark",
    "vertical": "tall 9:16 portrait frame, main subject in the middle third, no text, no watermark",
}

PROMPT_SYSTEM = (
    "You write prompts for an AI image generator that illustrates scenes of a narrated video. "
    "Given the narration for one scene, reply with a single image prompt of at most 60 words "
    "describing one concrete shot: subject, setting, lighting, camera angle and mood. "
    "Never include text, letters, signs, captions, logos or watermarks in the image. "
    "Never name real people; describe them by role, age, origin and clothing (e.g. 'a Nigerian schoolteacher in "
    "his forties'), using the character sheet's description when it lists them. "
    "Only show the people the visual description or narration of this scene calls for: the previous scene's prompt "
    "is for continuity of place and light, so never carry its people into a scene about someone else. "
    "Depict people respectfully and accurately for the place and present day: everyday contemporary "
    "clothing suited to the region and work, traditional dress only where it would genuinely be worn, "
    "described specifically rather than as generic 'tribal' or 'ethnic' costume; no poverty clichés or caricature. "
    "If the script gives a visual description for the scene, it decides what the image shows: follow it closely "
    "and use the narration only for context (place, people, time of day). "
    "If a visual style is given, write the prompt for that style and don't contradict it (no 'photograph' for an animated style). "
    "For a photographic style, write it as a real documentary photograph: the shot type and lens (e.g. 'wide shot, "
    "35mm lens'), natural light, real skin texture, fabric and materials, believable everyday detail, nothing fantastical. "
    "If a character sheet is given, describe every listed character who appears exactly as the sheet does, "
    "so they look the same in every scene. "
    "Image models often draw broken hands and limbs, so for every person give one simple, clear pose: hands resting, "
    "holding one object with both hands, or at their sides, and say what each hand does (e.g. 'both hands on the rim of "
    "the pot'). Avoid poses that tangle limbs: waving with spread fingers, pointing, interlocked or overlapping hands, "
    "hands near the face, crowds of hands, or several people touching the same thing. If the description asks for such a "
    "pose, choose the simplest version of it (a raised open palm for a wave, a hand resting on the globe for pointing). "
    "Keep the main subject centred. Reply with the prompt only, no preamble or quotes. "
    "Then, on a new last line starting with 'CHARACTERS:', list each recurring person or animal in the shot as "
    "'name or role = short visual description (age, build, hair, clothing, colours)', separated by ';', "
    "copying sheet descriptions word for word; write 'CHARACTERS: none' if there are none."
)
MAX_SHEET = 8  # character sheet entries passed to each prompt


class ProviderError(Exception):
    def __init__(self, message, retryable=False, auth=False, quota=False, status=None):
        super().__init__(message)
        self.retryable = retryable
        self.auth = auth
        self.quota = quota
        self.status = status


CloudflareError = ProviderError  # earlier name, kept for callers and tests


def load_env():
    if ENV_FILE.exists():
        from dotenv import load_dotenv
        load_dotenv(ENV_FILE, override=False)


CF_SETUP = (
    "1. Sign in at https://dash.cloudflare.com (a free account is enough).\n"
    "2. Open AI > Workers AI and select 'Use REST API'.\n"
    "3. Copy the Account ID, then 'Create a Workers AI API Token' (it needs Workers AI Read + Edit).\n"
    "4. Copy .env.example to .env and paste both values in, or set them as environment variables.")
POLLINATIONS_SETUP = (
    "Create a free key at https://enter.pollinations.ai (a secret key starting with sk_),\n"
    "then add POLLINATIONS_API_KEY=... to .env.")


def credentials(required=True):
    """(account_id, api_token) from the environment or .env. Returns None if missing and not required."""
    load_env()
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    missing = [n for n, v in (("CLOUDFLARE_ACCOUNT_ID", account), ("CLOUDFLARE_API_TOKEN", token)) if not v]
    if not missing:
        return account, token
    if required:
        raise SystemExit(f"Cloudflare image generation needs {' and '.join(missing)}.\n{CF_SETUP}")
    return None


def pollinations_key():
    load_env()
    return os.environ.get("POLLINATIONS_API_KEY", "").strip() or None


def _error_detail(e):
    try:
        return "; ".join(err.get("message", "") for err in json.loads(e.read()).get("errors", []))
    except (ValueError, AttributeError, TypeError):
        return ""


def multipart(fields):
    """(body, content type) of a multipart/form-data request with plain text fields."""
    boundary = "----ai-video-editor-" + secrets.token_hex(12)
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n' for k, v in fields.items()]
    return ("".join(parts) + f"--{boundary}--\r\n").encode(), f"multipart/form-data; boundary={boundary}"


def cf_run(model, payload, creds, timeout=120):
    """POST to Workers AI and return the `result` object. FLUX.2 models get the payload as form fields, and a
    reply that is the image itself (Phoenix) comes back as {"image": base64}. Error messages never include
    the account ID or token."""
    account, token = creds
    if model_info(model).form:
        data, ctype = multipart(payload)
    else:
        data, ctype = json.dumps(payload).encode(), "application/json"
    req = urllib.request.Request(API.format(account=account, model=model), data=data, method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            if r.headers.get("Content-Type", "").startswith("image/"):
                return {"image": base64.b64encode(raw).decode()}
            body = json.loads(raw)
    except urllib.error.HTTPError as e:
        detail = _error_detail(e)
        # the free plan answers 429 with a message about the daily neuron allocation once it's used up (Workers Paid
        # bills instead, so on the paid plan this is a real error and the scene goes to the backup)
        quota = e.code == 429 and any(k in detail.lower() for k in ("neuron", "daily", "allocation", "4006"))
        raise ProviderError(f"HTTP {e.code}{': ' + detail if detail else ''}", quota=quota,
                            retryable=(e.code == 429 or e.code >= 500) and not quota,
                            auth=e.code in (401, 403), status=e.code) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise ProviderError(f"network error ({getattr(e, 'reason', e)})", retryable=True) from None
    if not body.get("success", False):
        msgs = "; ".join(err.get("message", "") for err in body.get("errors", [])) or "unknown error"
        raise ProviderError(msgs)
    return body["result"]


def with_retries(call, *args):
    """Retry `call` with backoff on network errors, rate limits and server errors."""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return call(*args)
        except ProviderError as e:
            if not e.retryable or attempt == ATTEMPTS:
                raise
            wait = 2 ** attempt
            print(f"    {e}; retrying in {wait}s...")
            time.sleep(wait)


def pollinations_run(prompt, key, seed, timeout=180, size="square"):
    """GET an image from Pollinations and return its bytes. The key is sent as a header only."""
    w, h = dims(size)
    query = urllib.parse.urlencode({"model": POLLINATIONS_MODEL, "width": w, "height": h, "seed": seed})
    req = urllib.request.Request(
        POLLINATIONS_API.format(prompt=urllib.parse.quote(prompt, safe="")) + "?" + query,
        headers={"Authorization": f"Bearer {key}", "User-Agent": "ai-video-editor"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if not r.headers.get("Content-Type", "").startswith("image/"):
                raise ProviderError(f"unexpected response type {r.headers.get('Content-Type')}")
            return r.read()
    except urllib.error.HTTPError as e:
        raise ProviderError(f"HTTP {e.code}", quota=e.code == 402, auth=e.code in (401, 403),
                            retryable=e.code == 429 or e.code >= 500, status=e.code) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise ProviderError(f"network error ({getattr(e, 'reason', e)})", retryable=True) from None


def pollinations_text_run(messages, key, max_tokens=220, temperature=0.7, timeout=90):
    """POST a chat completion to Pollinations and return the reply text. The key is sent as a header only."""
    body = {"messages": messages, "max_tokens": max_tokens, "temperature": temperature}
    if POLLINATIONS_TEXT_MODEL:
        body["model"] = POLLINATIONS_TEXT_MODEL
    req = urllib.request.Request(POLLINATIONS_TEXT_API, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                          "User-Agent": "ai-video-editor"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise ProviderError(f"HTTP {e.code}", quota=e.code == 402, auth=e.code in (401, 403),
                            retryable=e.code == 429 or e.code >= 500, status=e.code) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise ProviderError(f"network error ({getattr(e, 'reason', e)})", retryable=True) from None
    except ValueError:
        raise ProviderError("unreadable text response") from None
    try:
        reply = str(data["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError):
        raise ProviderError("unexpected text response (no choices)") from None
    if NOT_ALLOWED.search(reply):  # a refusal arrives as an ordinary reply (HTTP 200): never use it as a prompt
        raise ProviderError("this key isn't allowed to use Pollinations' text models (allow text for the key at "
                            "enter.pollinations.ai)", auth=True)
    return reply


def utc_day():
    """The free allowance's day: it resets at 00:00 UTC (03:00 in Nairobi)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def nairobi_day():
    """The daily cap's day (max-daily-neurons): it resets at midnight in Nairobi."""
    return datetime.now(NAIROBI).strftime("%Y-%m-%d")


def usage_path():
    return GEN_DIR / USAGE_FILE_NAME


def usage_today():
    """Neurons this PC has spent today: "free_used" in the UTC day the free allowance counts, "spent" in the Nairobi
    day the cap counts. Other use of the same account isn't seen."""
    u = {"free_day": utc_day(), "free_used": 0.0, "cap_day": nairobi_day(), "spent": 0.0}
    try:
        data = json.loads(usage_path().read_text(encoding="utf-8"))
        if data.get("free_day") == u["free_day"]:
            u["free_used"] = float(data.get("free_used", 0))
        if data.get("cap_day") == u["cap_day"]:
            u["spent"] = float(data.get("spent", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return u


def record_usage(neurons=0.0):
    u = usage_today()
    u["free_used"] += neurons
    u["spent"] += neurons
    try:
        usage_path().parent.mkdir(parents=True, exist_ok=True)
        usage_path().write_text(json.dumps(u), encoding="utf-8")
    except OSError:
        pass


def free_left():
    """Free neurons this PC hasn't used yet today (UTC day); beyond them, Workers Paid bills."""
    return max(0.0, FREE_NEURONS_PER_DAY - usage_today()["free_used"])


def spent_today():
    """Neurons spent today in Nairobi time, which max-daily-neurons caps."""
    return usage_today()["spent"]


def dollars(neurons, free=None):
    """US$ for `neurons` more today: whatever doesn't fit in the free neurons left (`free`, default today's)."""
    return max(0.0, neurons - (free_left() if free is None else free)) * NEURON_PRICE


def check_image(data):
    try:
        from PIL import Image
        Image.open(io.BytesIO(data)).verify()
    except Exception as e:  # not an image
        raise ProviderError(f"unexpected image response ({e.__class__.__name__})") from None
    return data


def stable_seed(text):
    return int(hashlib.sha256(text.encode()).hexdigest()[:8], 16) % 2**31


def random_seed():
    import secrets
    return secrets.randbelow(2**31 - 1) + 1


def image_path(prompt, model, size="square"):
    if size in SIZES:  # the pixel size, so a change of SIZES never reuses an image of the old shape
        size = "x".join(map(str, SIZES[size]))
    key = hashlib.sha256(f"{model}|{size}|{prompt}".encode()).hexdigest()[:20]
    return GEN_DIR / f"{key}.jpg"


def draft_path(prompt, size="square"):
    """A draft image's place: generated/drafts/, a cache of its own that a final render never reads."""
    return GEN_DIR / "drafts" / image_path(prompt, "draft|" + POLLINATIONS_CACHE_ID, size).name


def save_image(data, path):
    """Write an image into the cache. Files made from the image it replaces (its parallax mask, its quality
    verdict) are deleted, so a redrawn picture never keeps the old one's."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_bytes(data)
    for old in path.parent.glob(path.stem + ".*"):
        if old not in (path, tmp):
            old.unlink()
    tmp.replace(path)
    return path


# ---------- quality check ----------

def qc_path(image):
    return Path(image).with_suffix(".qc.json")


def load_verdict(image):
    try:
        v = json.loads(qc_path(image).read_text(encoding="utf-8"))
        return v if isinstance(v, dict) and "ok" in v else None
    except (OSError, ValueError):
        return None


def save_verdict(image, verdict):
    qc_path(image).write_text(json.dumps(verdict, ensure_ascii=False), encoding="utf-8")


def write_check_report(scenes, path):
    """The video's Working/image_checks.json: each scene's quality-check verdict per format. The verdicts themselves stay
    cached next to the images in generated/, so a new video folder never pays for a check twice."""
    rows = []
    for i, s in enumerate(scenes, 1):
        for fmt, image in (s.get("srcs") or {}).items():
            if (v := load_verdict(image)) is not None:
                rows.append({"scene": i, "format": fmt, "image": str(image), **v})
    Path(path).write_text(json.dumps({"checks": rows}, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def parse_verdict(reply):
    """{"ok", "problems", "score"} from the vision model's reply, or None if it can't be read."""
    if not reply:
        return None
    text = reply[reply.find("{"):reply.rfind("}") + 1]
    try:
        data = json.loads(text)
    except ValueError:
        m = re.search(r'"ok"\s*:\s*(true|false)', reply, re.I)
        if not m:
            return None
        data = {"ok": m.group(1).lower() == "true"}
    if not isinstance(data, dict) or not isinstance(data.get("ok"), bool):
        return None
    problems = data.get("problems") or []
    problems = [str(p).strip() for p in (problems if isinstance(problems, list) else [problems]) if str(p).strip()]
    try:
        score = max(0, min(10, int(round(float(data.get("score", 10 if data["ok"] else 0))))))
    except (TypeError, ValueError):
        score = 10 if data["ok"] else 0
    if data["ok"] and problems:  # a model that lists defects hasn't passed the image, whatever it says
        data["ok"] = False
    return {"ok": data["ok"], "problems": problems, "score": score}


def better(a, b):
    """True if verdict a beats verdict b: a pass beats a fail, then the higher score."""
    return (a["ok"], a["score"], -len(a["problems"])) > (b["ok"], b["score"], -len(b["problems"]))


def data_uri(image, side=QC_SIDE):
    from PIL import Image
    with Image.open(image) as img:
        img = img.convert("RGB")
        img.thumbnail((side, side))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


class Providers:
    """The image providers and the prompt-writing text model for one run, with usage counts and the daily cap.

    mode "final": Cloudflare writes the prompts and draws the images. A call that still fails after one retry, or the
    daily cap (`cap`, max-daily-neurons; 0 = none), sends that work to the Pollinations backup, and the scene is
    recorded in self.backup for the --redo-scenes line at the end of the run.
    mode "draft": Pollinations draws every image into generated/drafts/; Cloudflare still writes the prompts while it
    can, and Pollinations' text model writes them when it can't."""

    def __init__(self, mode="final", image_model=IMAGE_MODEL, text_model=TEXT_MODEL, creds=None, poll_key=None,
                 new_images=False, vision_model=VISION_MODEL, cap=MAX_DAILY_NEURONS):
        if mode not in MODES:
            raise ValueError(f"unknown provider mode {mode}")
        image_model = resolve_model(image_model)
        self.mode, self.image_model, self.text_model = mode, image_model, text_model
        self.model = model_info(image_model)
        # --new-images: skip the image cache and use a random seed, so the same prompt gives a different picture
        self.new_images = new_images
        self.creds = creds or credentials(required=mode == "final")
        self.poll_key = poll_key if poll_key is not None else pollinations_key()
        if mode == "draft" and not self.poll_key:
            raise SystemExit("Draft images (--draft, --preview-scenes, --thumbnail-ideas, --style-test) come from "
                             f"Pollinations and need POLLINATIONS_API_KEY.\n{POLLINATIONS_SETUP}")
        if mode == "final" and poll_key is None and not self.poll_key:
            print("  (no POLLINATIONS_API_KEY: a scene Cloudflare can't draw, or one past the daily cap, gets a title card)")
        self.cf_ok = bool(self.creds)  # Cloudflare is called: credentials, not rejected, under the cap
        self.poll_text = bool(self.poll_key)  # Pollinations' text model writes prompts Cloudflare can't
        self.cap = cap or 0
        self.capped = False
        self._logged = 0.0  # neurons already written to the usage record
        self.vision_model = vision_model
        self.neurons = self.qc_neurons = 0.0
        self.counts = Counter()
        self.last_writer = None  # who answered the latest text(): "cloudflare", "pollinations" or None
        self.backup = {}  # final render: scene number -> why it isn't all Cloudflare's work

    @property
    def text_ok(self):
        """Some text model can write prompts: Cloudflare's, or Pollinations' as the backup."""
        return self.cf_ok or self.poll_text

    def save_usage(self):
        record_usage(self.neurons - self._logged)
        self._logged = self.neurons

    def spent(self):
        """Neurons spent today in Nairobi time, this run's included."""
        return spent_today() + self.neurons - self._logged

    def within_cap(self, cost=0.0):
        """True if `cost` more neurons stay within max-daily-neurons. The first time they don't, Cloudflare is left
        alone for the rest of the run, with a warning."""
        if self.capped:
            return False
        if not self.cap or self.spent() + cost <= self.cap:
            return True
        self.capped, self.cf_ok = True, False
        self.save_usage()
        nxt = ("the Pollinations backup draws the remaining scenes (listed at the end of the run)"
               if self.mode == "final" and self.poll_key else
               "Pollinations writes the remaining draft prompts" if self.mode == "draft" else
               "the remaining scenes get title cards (no POLLINATIONS_API_KEY)")
        bar = "=" * 78
        print(f"\n{bar}\nWARNING: the daily Cloudflare cap is reached: ~{self.spent():,.0f} neurons spent today "
              f"(Nairobi time) of max-daily-neurons = {self.cap:,}.\nNo more Cloudflare images or prompts this run; "
              f"{nxt}.\nThe cap resets at midnight Nairobi time; raise max-daily-neurons in make_video.toml to allow "
              f"more.\n{bar}")
        return False

    def _cf(self, model, payload):
        """A Cloudflare call with one retry. An error that survives the retry (or bad credentials) is raised."""
        try:
            return cf_run(model, payload, self.creds)
        except ProviderError as e:
            if e.auth:
                raise
            wait = CF_RETRY_WAIT[1] if e.status == 429 else CF_RETRY_WAIT[0]
            print(f"    Cloudflare: {e}; retrying once in {wait}s...")
            time.sleep(wait)
        return cf_run(model, payload, self.creds)

    def _cloudflare_failed(self, e, what):
        if e.auth:  # every later call would fail the same way
            self.cf_ok = False
            print(f"  WARNING: Cloudflare rejected the credentials ({e}); not calling it again this run. Check "
                  "CLOUDFLARE_ACCOUNT_ID, and that the API token has Workers AI Read + Edit permission.")
        else:
            print(f"    Cloudflare {what} failed after a retry ({e})")

    def _pollinations_text(self, messages, max_tokens, temperature):
        try:
            reply = with_retries(pollinations_text_run, messages, self.poll_key, max_tokens, temperature)
        except ProviderError as e:
            if e.quota or e.auth:
                self.poll_text = False
                print(f"  Pollinations text {'credit used up' if e.quota else 'refused'} ({e}); "
                      "simple prompts from here on (Pollinations images still work).")
            else:
                print(f"    Pollinations prompt writer failed ({e}); using a simple prompt")
            return None
        self.counts["pollinations_text"] += 1
        return reply.strip().strip('"').strip() or None

    def text(self, messages, max_tokens=160, model=None, temperature=0.7):
        """Run the prompt-writing model (or `model`, e.g. the vision checker) on Cloudflare. When Cloudflare can't (an
        error after its retry, the daily cap, no credentials), text jobs go to Pollinations' text model instead; the
        vision check never does. Returns None if no model can answer; self.last_writer says which one did."""
        vision = model is not None and model == self.vision_model
        model = model or self.text_model
        self.last_writer = None
        if self.cf_ok and self.within_cap():
            try:
                result = self._cf(model, {"messages": messages, "max_tokens": max_tokens, "temperature": temperature})
            except ProviderError as e:
                self._cloudflare_failed(e, "quality check" if vision else "text model")
            else:
                return self._cloudflare_reply(result, messages, model)
        if vision or not self.poll_text:
            return None
        reply = self._pollinations_text(messages, max_tokens, temperature)
        if reply:
            self.last_writer = "pollinations"
        return reply

    def _cloudflare_reply(self, result, messages, model):
        usage = result.get("usage") or {}
        response = result.get("response")
        if isinstance(response, (dict, list)):  # some models' JSON replies arrive already parsed
            response = json.dumps(response, ensure_ascii=False)
        chars = sum(len(c) if isinstance(c, str) else sum(len(p.get("text", "")) for p in c)
                    for c in (m["content"] for m in messages))
        images = sum(not isinstance(m["content"], str) and any(p.get("type") == "image_url" for p in m["content"])
                     for m in messages)
        tin = usage.get("prompt_tokens") or chars / 4 + images * QC_IMAGE_TOKENS
        tout = usage.get("completion_tokens") or len(str(response or "")) / 4
        rate_in, rate_out = TEXT_NEURONS.get(model, TEXT_NEURONS[TEXT_MODEL])
        cost = (tin * rate_in + tout * rate_out) / 1e6
        self.neurons += cost
        if model == self.vision_model:
            self.qc_neurons += cost
        self.counts["qc" if model == self.vision_model else "text"] += 1
        self.save_usage()
        reply = str(response or "").strip().strip('"').strip() or None
        if reply:
            self.last_writer = "cloudflare"
        return reply

    def inspect(self, image, shot=""):
        """Ask the vision model whether an image has broken anatomy or stray text. Returns a verdict, or None when it
        can't be checked (no Cloudflare, the cap reached, unreadable reply)."""
        content = [{"type": "text", "text": f"The picture was meant to show: {shot[:500]}\nCheck it." if shot
                    else "Check this picture."},
                   {"type": "image_url", "image_url": {"url": data_uri(image)}}]
        reply = self.text([{"role": "system", "content": QC_SYSTEM}, {"role": "user", "content": content}],
                          max_tokens=160, model=self.vision_model, temperature=0.1)
        return parse_verdict(reply)

    def sizes(self, formats):
        """The image shapes to draw: one per video format for a sized model (and for drafts), else one square."""
        sized = self.model.sized or self.mode == "draft"
        return [f for f in formats if f in SIZES] if sized else ["square"]

    def cached(self, prompt, size="square"):
        """The cached image for a prompt: Cloudflare's for a final render, the draft's for a draft. Neither stands in
        for the other, and the backup's images are never reused, so the next final render asks Cloudflare again."""
        if self.mode == "draft":
            p, name = draft_path(prompt, size), "pollinations"
        else:
            p, name = image_path(prompt, self.image_model, size), "cloudflare"
        return (p, name) if p.exists() else None

    def _cloudflare(self, prompt, size, fresh):
        payload = {"prompt": prompt}
        if self.model.sized:
            payload["width"], payload["height"] = (min(v, self.model.max_side) for v in dims(size))
        if self.model.steps:
            payload[self.model.steps_key] = self.model.steps
        if fresh and self.model.sized:  # FLUX.1 schnell rejects a seed and varies by itself
            payload["seed"] = random_seed()
        result = self._cf(self.image_model, payload)
        try:
            data = base64.b64decode(result["image"])
        except (KeyError, TypeError, ValueError):
            raise ProviderError("unexpected image response (no image field)") from None
        self.neurons += self.model.neurons(*dims(size))
        self.save_usage()
        return check_image(data)

    def _pollinations(self, prompt, size, fresh):
        seed = random_seed() if fresh else stable_seed(prompt)
        return check_image(with_retries(pollinations_run, prompt, self.poll_key, seed, 180, size))

    def image(self, prompt, regenerate=False, size="square", fresh=False):
        """Return (path, provider, from_cache). A final render asks Cloudflare (one retry), then the Pollinations backup;
        a draft asks Pollinations. `fresh` (or --new-images) skips the cache and draws with a new random seed,
        replacing the cached image. Raises ProviderError when no provider can make it."""
        fresh = fresh or self.new_images
        if not (regenerate or fresh) and (hit := self.cached(prompt, size)):
            return hit[0], hit[1], True
        errors = []
        if self.mode == "final":
            if self.cf_ok and self.within_cap(self.model.neurons(*dims(size))):
                try:
                    data = self._cloudflare(prompt, size, fresh)
                except ProviderError as e:
                    self._cloudflare_failed(e, "image")
                    errors.append(f"cloudflare: {e}")
                else:
                    self.counts["cloudflare"] += 1
                    return save_image(data, image_path(prompt, self.image_model, size)), "cloudflare", False
            else:
                errors.append("cloudflare: " + ("the daily cap is reached" if self.capped else "not available"))
        if self.poll_key:
            target = draft_path(prompt, size) if self.mode == "draft" else image_path(prompt, POLLINATIONS_CACHE_ID, size)
            try:
                data = self._pollinations(prompt, size, fresh)
            except ProviderError as e:
                errors.append(f"pollinations: {e}")
                if e.quota or e.auth:
                    self.poll_key = None
                    print(f"  Pollinations {'credit used up' if e.quota else 'rejected the key'} ({e}); "
                          "not calling it again this run.")
            else:
                self.counts["pollinations"] += 1
                return save_image(data, target), "pollinations", False
        raise ProviderError("; ".join(errors) or "no image provider available")

    def report(self):
        self.save_usage()
        if self.neurons:
            qc = f", of which ~{self.qc_neurons:.0f} for {self.counts['qc']} quality check(s)" if self.counts["qc"] else ""
            u = usage_today()
            billed = max(0.0, u["free_used"] - FREE_NEURONS_PER_DAY)
            cap = f"of the {self.cap:,} cap" if self.cap else "(no daily cap)"
            print(f"  Cloudflare usage this run: ~{self.neurons:.0f} neurons{qc} (estimate from the published rates)")
            print(f"  today: ~{u['spent']:,.0f} neurons {cap} (Nairobi day); ~{billed:,.0f} beyond the free "
                  f"{FREE_NEURONS_PER_DAY:,} so far, about ${billed * NEURON_PRICE:.2f} (UTC day)")
        if self.counts["pollinations_text"]:
            print(f"  Pollinations text model: {self.counts['pollinations_text']} prompt(s) written while Cloudflare "
                  "couldn't")


def fallback_prompt(text, description=""):
    return f"{description}. Scene illustrating: {text[:300]}" if description else f"Scene illustrating: {text[:400]}"


SECOND_SYSTEM = (
    "You rewrite an image prompt for a documentary's second shot of the same moment. Keep exactly the same people "
    "(age, clothing, skin, hair), place, objects, time of day and light. Change only the camera: if the first shot is "
    "wide or medium, make it a close-up of the key action, hands or object; if it is a close-up, make it a wide shot "
    "that shows the setting. Keep people in simple, natural poses with relaxed, clearly visible hands. No text, "
    "logos, split screens or before-and-after. Reply with the new prompt only, in one paragraph under 90 words.")
CLOSE_WORDS = re.compile(r"\b(close[- ]?up|close view|detail|macro|extreme close)\b", re.I)


def fallback_second(prompt):
    """A second shot without the text model: the other distance, same everything else."""
    if CLOSE_WORDS.search(prompt):
        return f"Wide establishing shot showing the whole setting of this moment: {prompt}"
    return f"Close-up detail of the key action and objects in this moment: {prompt}"


def write_second_prompt(prompt, scene, providers):
    """The prompt for a scene's second shot (--two-shots): ~25 neurons with the 70B text model."""
    reply = providers.text([{"role": "system", "content": SECOND_SYSTEM},
                            {"role": "user", "content": f"First shot's prompt: {prompt}\n\n"
                                                        f"Narration: {scene['text']}"}], max_tokens=160)
    reply = (reply or "").strip().strip('"')
    return reply if len(reply) > 40 else fallback_second(prompt)


def split_characters(reply):
    """Split the prompt writer's reply into (prompt, {name: description}) from its CHARACTERS: line."""
    m = re.search(r"^\s*\**CHARACTERS?\**\s*:(.*)$", reply, re.I | re.M)
    if not m:
        return reply.strip(), {}
    found = {}
    for part in m.group(1).split(";"):
        name, sep, desc = part.partition("=")
        if sep and name.strip() and desc.strip() and name.strip().lower() != "none":
            found[name.strip().strip("*").strip()] = desc.strip().rstrip(".")
    return reply[:m.start()].strip().strip('"').strip(), found


def sheet_text(sheet):
    return "; ".join(f"{name} = {desc}" for name, desc in list(sheet.items())[-MAX_SHEET:])


GENERIC_NAME_WORDS = {"the", "and", "with", "old", "young", "little", "big", "mr", "mrs", "miss", "man", "woman",
                      "boy", "girl", "village", "villager", "villagers", "farmer", "farmers", "people", "vendor",
                      "teacher", "mechanic", "engineer", "local", "locals", "children", "family"}


def mentioned(name, text):
    """True if the text uses any distinctive word of the character's name ("Kofi" or "Grandpa" for
    "Grandpa Kofi"), as a whole word; a name of generic words only ("The Old Man") must appear whole."""
    words = [w for w in re.findall(r"\w+", name.lower()) if len(w) > 2 and w not in GENERIC_NAME_WORDS]
    if not words:
        return bool(re.search(rf"\b{re.escape(name)}\b", text, re.I))
    return any(re.search(rf"\b{re.escape(w)}\b", text, re.I) for w in words)


def in_scene(sheet, *texts):
    """The sheet entries this scene's narration or visual description names. Only these reach the prompt writer:
    given the whole sheet, it put the channel's boy into Sonam Wangchuk's scene."""
    text = " ".join(texts)
    return {name: desc for name, desc in sheet.items() if mentioned(name, text)}


PERSON_WORDS = {"boy", "girl", "man", "woman"}


def role(name, desc):
    """What an image prompt calls a character instead of their name: "a village boy" for "Smart Village Boy",
    else the first phrase of the description ("a Ladakhi engineer in his late fifties")."""
    generic = [w.lower() for w in re.findall(r"\w+", name) if w.lower() in GENERIC_NAME_WORDS - {"the", "and", "with"}]
    phrase = " ".join(generic) if generic and generic[-1] in PERSON_WORDS else re.split(r"[,(;]", desc)[0].strip()
    if re.match(r"(a|an|the)\b", phrase, re.I):
        return phrase
    return f"{'an' if phrase[:1].lower() in 'aeiou' else 'a'} {phrase}"


def is_proper_name(name):
    return all(w[:1].isupper() for w in name.split())


def unname(prompt, sheet):
    """Replace sheet names ("Sonam Wangchuk", "Smart Village Boy"; not roles like "Khasi villagers") in a prompt with
    roles: image models can't know them and may letter them into the picture, and real people's names never go to
    the image model. A later or partial mention ("Sonam") becomes "the ...", and a role the prompt already gives
    right after the name isn't repeated."""
    for name, desc in sheet.items():
        if not is_proper_name(name):
            continue
        words = [w for w in re.findall(r"\w+", name) if len(w) > 2 and w.lower() not in GENERIC_NAME_WORDS]
        options = sorted({name, *words}, key=len, reverse=True)
        pattern = r"(?<!\w)(?:(?:a|an|the)\s+)?(?:" + "|".join(map(re.escape, options)) + r")(?!\w)(,\s*(?:a|an)\s)?"
        who = role(name, desc)
        bare = re.sub(r"^(a|an|the)\s+", "", who, flags=re.I)
        first = [True]

        def swap(m):
            if m.group(1):  # "Mohammed Bah Abba, a Nigerian teacher": the prompt describes him already
                first[0] = False
                return m.group(1).lstrip(", ")
            out = who if first[0] else f"the {bare}"
            first[0] = False
            return out
        prompt = re.sub(pattern, swap, prompt)
    return prompt


def enforce_characters(prompt, narration, sheet, description=""):
    """Make sure every sheet character named in the narration or description is described in the prompt
    (description only, never the name, so real people's names stay out of images)."""
    prompt = unname(prompt, sheet)
    for name, desc in in_scene(sheet, narration, description).items():
        if desc.lower()[:40] not in prompt.lower():
            prompt = f"{prompt.rstrip('. ')}, with {desc}"
    return prompt


def write_prompt(text, previous, providers, style="", sheet=None, description=""):
    """Returns (prompt, characters the writer saw in this shot)."""
    context = f"Previous scene's image prompt, keep visual continuity: {previous}\n\n" if previous else ""
    if style:
        context += f"Visual style of the whole video: {style}\n\n"
    if (cast := in_scene(sheet or {}, text, description)):
        context += f"Character sheet: {sheet_text(cast)}\n\n"
    if description:
        context += f"Visual description from the script (this is what the image must show): {description}\n\n"
    reply = providers.text([{"role": "system", "content": PROMPT_SYSTEM},
                            {"role": "user", "content": f"{context}Narration for this scene: {text}"}], max_tokens=220)
    if not reply:
        return fallback_prompt(text, description), {}
    prompt, found = split_characters(reply)
    return prompt or fallback_prompt(text, description), found


def full_prompt(prompt, style, size="square"):
    return ", ".join(p for p in (prompt.rstrip(". "), style, COMPOSITION[size]) if p)[:MAX_PROMPT]


def load_prompt_file(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def scene_id(text, n=1):
    """A scene's stable id: its narration's hash and which occurrence of that narration it is (1, 2, ...). It survives
    reordering the script, and two scenes with the same narration get their own ids, prompts and edits."""
    return f"{hashlib.sha1(text.encode('utf-8')).hexdigest()[:12]}-{n}"


def assign_ids(scenes):
    """Set s["id"] on every scene of a script (all of them, so a scene's occurrence number never depends on which
    scenes a run draws)."""
    seen = {}
    for s in scenes:
        seen[s["text"]] = seen.get(s["text"], 0) + 1
        s["id"] = scene_id(s["text"], seen[s["text"]])
    return scenes


def migrate_records(records, scenes):
    """Match prompt records saved before ids existed to the script's scenes (the full list, with ids from assign_ids)
    and give each its scene's id. A record matches the scene with its stored scene number when that scene has the same
    narration; failing that, the one scene not yet matched with the same narration and visual description. Anything
    else can't be matched with confidence and is returned in `unmatched`, never dropped. Every other field, including
    the user's edited prompts, is kept as it is. Returns (records with ids, unmatched records)."""
    records = [r for r in records if isinstance(r, dict)]
    old = [r for r in records if not r.get("id")]
    taken = {r["id"] for r in records if r.get("id")}
    pending = []
    for r in old:  # first by stored scene number and narration
        n = r.get("scene")
        s = scenes[n - 1] if isinstance(n, int) and 1 <= n <= len(scenes) else None
        if s and s["text"] == r.get("narration") and s["id"] not in taken:
            r["id"] = s["id"]
            taken.add(s["id"])
        else:
            pending.append(r)
    unmatched = []
    for r in pending:  # then by narration and visual description, when exactly one scene fits
        fits = [s for s in scenes if s["id"] not in taken and s["text"] == r.get("narration")
                and s.get("description", "") == r.get("description", "")]
        if len(fits) == 1:
            r["id"] = fits[0]["id"]
            taken.add(r["id"])
        else:
            unmatched.append(r)
    return [r for r in records if r.get("id")], unmatched


def load_prompt_records(path, scenes=None):
    """Saved prompt records keyed by scene id (see scene_id), so edits survive reordering the script and repeated
    narration keeps one record per scene. Records from before ids are matched to `scenes` (the script's full scene
    list) as they're read; without scenes, only records that already have an id count."""
    try:
        records = list(load_prompt_file(path)["scenes"])
    except (KeyError, TypeError, AttributeError):
        return {}
    if scenes is not None:
        records, _ = migrate_records(records, assign_ids(scenes))
    return {s["id"]: s for s in records if isinstance(s, dict) and s.get("prompt") and s.get("id")}


def load_prompts(path):
    return {n: r["prompt"] for n, r in load_prompt_records(path).items()}


def saved_prompt(records, scene):
    """The saved prompt for a scene (by its id), if it was written for the same narration and visual description.
    A new or edited description line means the prompt is written again."""
    rec = records.get(scene.get("id") or scene_id(scene["text"]))
    if rec and rec.get("narration", scene["text"]) != scene["text"]:
        rec = None
    if rec and rec.get("description", "") == scene.get("description", "") and not rec.get("stand_in"):
        return rec["prompt"]
    return None


def style_changed(path, style):
    """True when the saved prompts were written for a different --style (a file without one counts as the same)."""
    saved = load_prompt_file(path).get("style")
    return saved is not None and saved != style


def load_characters(path):
    """The character sheet saved in prompts.json (editable, like the prompts)."""
    sheet = load_prompt_file(path).get("characters") or {}
    return {str(k): str(v) for k, v in sheet.items()} if isinstance(sheet, dict) else {}


def build_sheet(declared, saved):
    """Character sheet: saved/learned entries, overridden by '# character:' lines in the script."""
    sheet = dict(saved)
    for name, desc in (declared or {}).items():
        for old in [k for k in sheet if k.lower() == name.lower()]:
            del sheet[old]
        sheet[name] = desc
    return sheet


def learn(sheet, found, text=None):
    """Add characters the prompt writer described for the first time; existing entries never change.
    With text, only names written there as a name are learned ("Alfredo Moser", not "Villagers" or "Farmer 1"),
    since a role learned as a character gets pasted into every scene that uses the word."""
    known = {k.lower() for k in sheet}
    for name, desc in found.items():
        if text is not None and not re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text):
            continue
        if name.lower() not in known:
            sheet[name] = desc
            known.add(name.lower())


def draw(providers, prompt, size, shot, regenerate=False, fresh=False, check=False, label=""):
    """One image, with the optional quality check: a failed image is redrawn with a new seed up to QC_RETRIES times,
    and the best try is kept. Returns (path, provider, cached, verdict or None)."""
    path, name, cached = providers.image(prompt, regenerate, size, fresh)
    if not check:
        return path, name, cached, None
    verdict = load_verdict(path)  # a cached image checked on an earlier run isn't paid for again
    if verdict is None and providers.cf_ok:
        verdict = providers.inspect(path, shot)
        if verdict:
            save_verdict(path, verdict)
    if verdict is None or verdict["ok"]:
        return path, name, cached, verdict
    tries = [(verdict, GEN_DIR / f"_try_{path.stem}_0{path.suffix}")]
    shutil.copy(path, tries[0][1])
    for n in range(1, QC_RETRIES + 1):
        print(f"    {label}: {', '.join(verdict['problems']) or 'failed the check'}; redrawing ({n}/{QC_RETRIES})")
        try:
            path, name, cached = providers.image(prompt, True, size, fresh=True)
        except ProviderError as e:
            print(f"    {label}: redraw failed ({e})")
            break
        verdict = providers.inspect(path, shot)
        if verdict is None:  # can't check any more: keep the best checked try
            break
        tries.append((verdict, GEN_DIR / f"_try_{path.stem}_{n}{path.suffix}"))
        shutil.copy(path, tries[-1][1])
        if verdict["ok"]:
            break
    best, best_file = tries[0]
    for v, f in tries[1:]:
        if better(v, best):
            best, best_file = v, f
    if verdict is None or best is not verdict:
        shutil.copy(best_file, path)  # the latest draw lost: put the best one back in the cache
        for old in path.parent.glob(path.stem + ".*"):  # its mask (if any) belonged to the latest draw
            if old != path:
                old.unlink()
    save_verdict(path, best)
    for _, f in tries:
        f.unlink(missing_ok=True)
    return path, name, False, best


def generate_scene_visuals(scenes, prompts_path, model=IMAGE_MODEL, style="", regenerate=False, creds=None,
                           prompts_only=False, text_model=TEXT_MODEL, providers=None,
                           characters=None, formats=("horizontal", "vertical"), redo=(), check=False, only=None,
                           second_shots=None):
    """Fill s["src"] (and s["image_prompt"]) for every scene without a [visual: ...] line. A model that takes a
    size draws one image per format, in s["srcs"] ({format: path}); s["src"] is the first format's.
    `characters` is the script's declared character sheet; recurring characters the prompt writer
    describes are added to it, and every image prompt that names one gets its description.
    `redo` holds 0-based scene indices to draw again with a new seed (--redo-scenes); every other scene keeps its cached
    images. With `check`, each image is looked over by the vision model and redrawn if it fails (see draw()); scenes that
    still fail are listed in the log and in s["quality_flags"].
    Scenes whose image can't be made keep src None (title card). Returns the 1-based numbers of
    failed scenes. With prompts_only, stop after writing the prompts file. `only` (0-based indices) limits the work to
    those scenes (--preview-scenes); the other scenes' saved prompts are kept in the file. Scenes in `second_shots`
    (0-based) also get a second image of the same moment from another angle (s["srcs2"]), prompt saved as "prompt2".
    In a final render, scenes the Pollinations backup helped with are recorded in providers.backup."""
    targets = [(i, s) for i, s in enumerate(scenes) if not s["visual"] and not s.get("stock")
               and (only is None or i in only)]
    if not targets:
        return []
    providers = providers or Providers("final", model, text_model, creds)
    final = providers.mode == "final"
    fresh = regenerate or style_changed(prompts_path, style)
    if fresh and not regenerate:
        print(f"  the style changed since {prompts_path.name} was written: writing new prompts and character "
              "descriptions for it (your '# character:' lines are kept)")
    assign_ids(scenes)
    file = {} if fresh else load_prompt_file(prompts_path)
    matched, unmatched = migrate_records(list(file.get("scenes") or []), scenes)
    unmatched = [r for r in file.get("unmatched") or [] if isinstance(r, dict)] + unmatched
    if len(unmatched) > len(file.get("unmatched") or []):
        print(f"  WARNING: {len(unmatched) - len(file.get('unmatched') or [])} saved prompt(s) in {prompts_path.name} "
              "from before scene ids couldn't be matched to a scene with confidence; they're kept under \"unmatched\" "
              "in the file (copy any edit you want back into its scene's prompt)")
    saved = {r["id"]: r for r in matched if r.get("prompt")}
    # scenes of this script not drawn this run (stock, own files, outside a preview) keep their saved records;
    # records for scenes the script no longer has are dropped
    others = {s["id"] for s in scenes} - {s["id"] for _, s in targets}
    kept = [r for r in matched if r.get("id") in others]
    numbers = {s["id"]: n for n, s in enumerate(scenes, 1)}
    for r in kept:  # a kept record follows its scene when the script is reordered
        r["scene"] = numbers[r["id"]]
    sheet = build_sheet(characters, {} if fresh else load_characters(prompts_path))
    sizes = providers.sizes(formats)
    shapes = " + ".join("x".join(map(str, dims(z))) for z in sizes)
    print(f"Writing image prompts for {len(targets)} scene(s) (images: "
          f"{'Cloudflare' if final else 'Pollinations, draft'}, "
          f"{providers.image_model.rsplit('/', 1)[-1]} at {shapes})"
          f"{' (ignoring cache and saved prompts)' if regenerate else ''}...")

    records, previous = [], None
    for i, s in targets:
        description = s.get("description", "")
        prompt = saved_prompt(saved, s)
        rec = {"scene": i + 1, "id": s["id"], "description": description, "narration": s["text"]}
        if prompt:
            old = saved[s["id"]]
            if old.get("prompt2") and old.get("prompt2_for") == prompt:  # a second shot written for this same prompt
                rec.update(prompt2=old["prompt2"], prompt2_for=prompt)
        else:
            prompt, found = write_prompt(s["text"], previous, providers, style, sheet, description)
            learn(sheet, found, f"{s['text']} {description}")
            if providers.last_writer != "cloudflare" or prompt == fallback_prompt(s["text"], description):
                rec["stand_in"] = True  # not Cloudflare's: the next final render writes it again
                if final and not prompts_only:
                    providers.backup.setdefault(i + 1, "prompt not written by Cloudflare")
        rec["prompt"] = prompt
        if second_shots and i in second_shots and "prompt2" not in rec and not prompts_only:
            prompt2 = write_second_prompt(prompt, s, providers) if providers.text_ok else fallback_second(prompt)
            ours = providers.text_ok and providers.last_writer == "cloudflare"
            rec.update(prompt2=prompt2, prompt2_for=prompt if ours else None)  # None: written again next time
            if final and not ours:
                providers.backup.setdefault(i + 1, "second-shot prompt not written by Cloudflare")
        records.append(rec)
        previous = prompt
    shots = []  # after the loop, so later-learned characters reach earlier scenes too
    for (i, s), rec in zip(targets, records):
        shots.append(enforce_characters(rec["prompt"], s["text"], sheet, s.get("description", "")))
        s["image_prompt"] = full_prompt(shots[-1], style)

    def save(records):
        prompts_path.parent.mkdir(parents=True, exist_ok=True)
        every = sorted(kept + records, key=lambda r: r.get("scene", 0))
        data = {"image_model": providers.image_model, "style": style, "characters": sheet, "scenes": every}
        if unmatched:
            data["unmatched"] = unmatched
        prompts_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    save(records)
    print(f"  prompts saved to {prompts_path} (edit them and rerun to change the images)")
    if prompts_only:
        providers.report()
        return []

    failed, flagged = [], []
    made = Counter()
    for (i, s), rec, shot in zip(targets, records, shots):
        paths, errors, new = {}, [], False
        for size in sizes:
            label = f"scene {i + 1}" + ("" if size == "square" else f" {size}")
            try:
                paths[size], name, cached, verdict = draw(providers, full_prompt(shot, style, size), size, shot,
                                                          regenerate, i in redo, check, label)
            except ProviderError as e:
                errors.append(str(e))
                continue
            rec["image_provider"] = name
            made["cached" if cached else name] += 1
            new |= not cached
            if final and name == "pollinations":
                providers.backup[i + 1] = "Pollinations backup image"
            if verdict:
                rec.setdefault("quality", {})[size] = verdict
                if not verdict["ok"]:
                    s.setdefault("quality_flags", []).append(f"{size}: {', '.join(verdict['problems']) or 'failed'}")
                    flagged.append(f"{label} ({', '.join(verdict['problems']) or 'failed the check'})")
        if not paths:
            print(f"  scene {i + 1}: FAILED ({'; '.join(errors)}); using a title card")
            s["src"] = None
            failed.append(i + 1)
            continue
        if errors:
            print(f"  scene {i + 1}: one shape failed ({errors[0]}); the other is cropped to fit")
        first = next(iter(paths.values()))
        s["src"] = paths.get(sizes[0], first)
        if sizes != ["square"]:
            s["srcs"] = {f: paths.get(f, first) for f in formats}
        s["ai"] = True
        print(f"  scene {i + 1}: {'generated' if new else 'cached'} by {rec['image_provider']}: "
              f"{', '.join(p.name for p in dict.fromkeys(paths.values()))}")
        if rec.get("prompt2"):
            shot2 = enforce_characters(rec["prompt2"], s["text"], sheet, s.get("description", ""))
            second = {}
            for size in sizes:
                label = f"scene {i + 1} second shot" + ("" if size == "square" else f" {size}")
                try:
                    second[size], name, cached, verdict = draw(providers, full_prompt(shot2, style, size), size, shot2,
                                                               regenerate, i in redo, check, label)
                except ProviderError as e:
                    print(f"  {label}: failed ({e}); the scene keeps one shot")
                    continue
                made["cached" if cached else name] += 1
                if final and name == "pollinations":
                    providers.backup[i + 1] = "Pollinations backup image"
                if verdict:
                    rec.setdefault("quality2", {})[size] = verdict
            if second:
                first2 = next(iter(second.values()))
                s["src2"] = second.get(sizes[0], first2)
                if sizes != ["square"]:
                    s["srcs2"] = {f: second.get(f, first2) for f in formats}
                print(f"  scene {i + 1}: second shot: {', '.join(p.name for p in dict.fromkeys(second.values()))}")
    save(records)
    summary = ", ".join(f"{n} {k}" for k, n in made.items()) or "none"
    print(f"  images: {summary}; {len(failed)} failed")
    if check:
        print(f"  quality check ({providers.vision_model.rsplit('/', 1)[-1]}): {providers.counts['qc']} check(s), "
              f"~{providers.qc_neurons:.0f} neurons; "
              + (f"{len(flagged)} image(s) still flagged after {QC_RETRIES} redraws, best try kept:" if flagged
                 else "every image passed"))
        for f in flagged:
            print(f"    FLAGGED {f}")
    providers.report()
    return failed
