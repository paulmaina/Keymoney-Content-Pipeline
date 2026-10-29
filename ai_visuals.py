"""AI-generated scene images (used by make_video.py --generate-visuals).

For each scene without a [visual: ...] line, a Workers AI text model turns the narration
into an image prompt, then an image provider draws it:

  cloudflare    a Workers AI image model (--image-model, default FLUX.2 [klein] 4B; free daily allowance)
  pollinations  FLUX.1 schnell on Pollinations (needs a free key in POLLINATIONS_API_KEY)
  auto          Cloudflare, switching to Pollinations for the rest of the run once Cloudflare's
                daily allowance runs out; scenes that still fail get a title card

Models that take a width and height (all but FLUX.1 schnell) draw a real 16:9 image for the horizontal
video and a real 9:16 one for the vertical video, so nothing important is cropped away. FLUX.1 schnell
only makes squares, which the renderers crop to each format.

Prompts are saved to Exports/<name>_prompts.json for review and editing (rewritten when the style changes).
Images are cached in generated/ (keyed by prompt + size + provider model) and any provider's cached image
is reused, so reruns don't spend quota.
"""
import base64
import hashlib
import io
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
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
PROVIDERS = ("auto", "cloudflare", "pollinations")
IMAGE_PX = 1024  # square images
# real 16:9 and 9:16 images for models that take a size: multiples of 16, upscaled to 1080p by the renderer
# (or sharpened to 3072 px with --upscale)
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
}


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

    @property
    def stops_provider(self):
        """Quota used up, bad credentials or still rate-limited after retries: stop calling it this run."""
        return self.quota or self.auth or self.status == 429


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
        # the free plan answers 429 with a message about the daily neuron allocation once it's used up
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


def cf_call(model, payload, creds):
    return with_retries(cf_run, model, payload, creds)


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


def save_image(data, path):
    GEN_DIR.mkdir(exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(path)
    return path


class Providers:
    """Image providers and the prompt-writing text model for one run, with fallback and usage counts."""

    def __init__(self, mode="auto", image_model=IMAGE_MODEL, text_model=TEXT_MODEL, creds=None, poll_key=None,
                 new_images=False):
        if mode not in PROVIDERS:
            raise ValueError(f"unknown image provider {mode}")
        image_model = resolve_model(image_model)
        self.mode, self.image_model, self.text_model = mode, image_model, text_model
        self.model = model_info(image_model)
        # --new-images: skip the image cache and use a random seed, so the same prompt gives a different picture
        self.new_images = new_images
        self.creds = creds or credentials(required=mode == "cloudflare")
        self.poll_key = poll_key if poll_key is not None else pollinations_key()
        if mode == "pollinations" and not self.poll_key:
            raise SystemExit(f"--image-provider pollinations needs POLLINATIONS_API_KEY.\n{POLLINATIONS_SETUP}")
        if mode == "auto" and not (self.creds or self.poll_key):
            raise SystemExit("AI images need Cloudflare credentials or a Pollinations key.\n"
                             f"Cloudflare:\n{CF_SETUP}\nPollinations: {POLLINATIONS_SETUP}")
        if mode == "auto" and not self.poll_key:
            print("  (no POLLINATIONS_API_KEY: if Cloudflare's daily allowance runs out, remaining scenes get title cards)")
        self.cf_ok = bool(self.creds)  # the text model always uses Cloudflare when it's available
        self.neurons = 0.0
        self.counts = Counter()

    def _disable_cloudflare(self, e):
        if not self.cf_ok:
            return
        self.cf_ok = False
        if e.auth and self.mode == "cloudflare":
            raise SystemExit(f"Cloudflare rejected the credentials ({e}).\n"
                             "Check CLOUDFLARE_ACCOUNT_ID, and that the API token has Workers AI Read + Edit permission.")
        why = "rejected the credentials" if e.auth else "daily allowance or rate limit reached"
        nxt = "Pollinations" if self.mode == "auto" and self.poll_key else "title cards / template prompts"
        print(f"  Cloudflare {why} ({e}); not calling it again this run, using {nxt}.")

    def text(self, messages, max_tokens=160):
        """Run the prompt-writing model. Returns None if it's unavailable or fails."""
        if not self.cf_ok:
            return None
        try:
            result = cf_call(self.text_model, {"messages": messages, "max_tokens": max_tokens,
                                               "temperature": 0.7}, self.creds)
        except ProviderError as e:
            if e.stops_provider:
                self._disable_cloudflare(e)
            print(f"    prompt writer failed ({e}); using a simple prompt")
            return None
        usage = result.get("usage") or {}
        response = result.get("response")
        if isinstance(response, (dict, list)):  # some models' JSON replies arrive already parsed
            response = json.dumps(response, ensure_ascii=False)
        tin = usage.get("prompt_tokens") or sum(len(m["content"]) for m in messages) / 4
        tout = usage.get("completion_tokens") or len(str(response or "")) / 4
        rate_in, rate_out = TEXT_NEURONS.get(self.text_model, TEXT_NEURONS[TEXT_MODEL])
        self.neurons += (tin * rate_in + tout * rate_out) / 1e6
        self.counts["text"] += 1
        return str(response or "").strip().strip('"').strip() or None

    def sizes(self, formats):
        """The image shapes to draw: one per video format for a sized model, else one square for all."""
        return [f for f in formats if f in SIZES] if self.model.sized and self.mode != "pollinations" else ["square"]

    def cache_ids(self):
        return [(self.image_model, "cloudflare"), (POLLINATIONS_CACHE_ID, "pollinations")]

    def cached(self, prompt, size="square"):
        for model, name in self.cache_ids():
            p = image_path(prompt, model, size)
            if p.exists():
                return p, name
        return None

    def _cloudflare(self, prompt, size):
        payload = {"prompt": prompt}
        if self.model.sized:
            payload["width"], payload["height"] = (min(v, self.model.max_side) for v in dims(size))
        if self.model.steps:
            payload[self.model.steps_key] = self.model.steps
        if self.new_images and self.model.sized:  # FLUX.1 schnell rejects a seed and varies by itself
            payload["seed"] = random_seed()
        result = cf_call(self.image_model, payload, self.creds)
        try:
            data = base64.b64decode(result["image"])
        except (KeyError, TypeError, ValueError):
            raise ProviderError("unexpected image response (no image field)") from None
        self.neurons += self.model.neurons(*dims(size))
        return check_image(data)

    def _pollinations(self, prompt, size):
        seed = random_seed() if self.new_images else stable_seed(prompt)
        return check_image(with_retries(pollinations_run, prompt, self.poll_key, seed, 180, size))

    def image(self, prompt, regenerate=False, size="square"):
        """Return (path, provider, from_cache). Raises ProviderError when no provider can make it."""
        if not (regenerate or self.new_images) and (hit := self.cached(prompt, size)):
            return hit[0], hit[1], True
        attempts = []
        if self.mode in ("auto", "cloudflare") and self.cf_ok:
            attempts.append(("cloudflare", self.image_model, self._cloudflare))
        if self.mode in ("auto", "pollinations") and self.poll_key:
            attempts.append(("pollinations", POLLINATIONS_CACHE_ID, self._pollinations))
        errors = []
        for name, cache_id, make in attempts:
            try:
                path = save_image(make(prompt, size), image_path(prompt, cache_id, size))
            except ProviderError as e:
                errors.append(f"{name}: {e}")
                if name == "cloudflare" and e.stops_provider:
                    self._disable_cloudflare(e)
                elif name == "pollinations" and (e.quota or e.auth):
                    self.poll_key = None
                    print(f"  Pollinations {'credit used up' if e.quota else 'rejected the key'} ({e}); "
                          "not calling it again this run.")
                continue
            self.counts[name] += 1
            return path, name, False
        raise ProviderError("; ".join(errors) or "no image provider available")

    def report(self):
        if self.neurons:
            print(f"  Cloudflare usage this run: ~{self.neurons:.0f} neurons "
                  f"(free allowance {FREE_NEURONS_PER_DAY:,}/day; estimate from the published rates)")


def fallback_prompt(text, description=""):
    return f"{description}. Scene illustrating: {text[:300]}" if description else f"Scene illustrating: {text[:400]}"


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


def load_prompt_records(path):
    """Saved prompt records keyed by narration, so edits survive reordering the script."""
    try:
        return {s["narration"]: s for s in load_prompt_file(path)["scenes"] if s.get("prompt")}
    except (KeyError, TypeError, AttributeError):
        return {}


def load_prompts(path):
    return {n: r["prompt"] for n, r in load_prompt_records(path).items()}


def saved_prompt(records, scene):
    """The saved prompt for a scene, if it was written for the same narration and visual description.
    A new or edited description line means the prompt is written again."""
    rec = records.get(scene["text"])
    if rec and rec.get("description", "") == scene.get("description", ""):
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


def generate_scene_visuals(scenes, prompts_path, model=IMAGE_MODEL, style="", regenerate=False, creds=None,
                           prompts_only=False, provider="auto", text_model=TEXT_MODEL, providers=None,
                           characters=None, formats=("horizontal", "vertical")):
    """Fill s["src"] (and s["image_prompt"]) for every scene without a [visual: ...] line. A model that takes a
    size draws one image per format, in s["srcs"] ({format: path}); s["src"] is the first format's.
    `characters` is the script's declared character sheet; recurring characters the prompt writer
    describes are added to it, and every image prompt that names one gets its description.
    Scenes whose image can't be made keep src None (title card). Returns the 1-based numbers of
    failed scenes. With prompts_only, stop after writing the prompts file."""
    targets = [(i, s) for i, s in enumerate(scenes) if not s["visual"]]
    if not targets:
        return []
    providers = providers or Providers(provider, model, text_model, creds)
    fresh = regenerate or style_changed(prompts_path, style)
    if fresh and not regenerate:
        print(f"  the style changed since {prompts_path.name} was written: writing new prompts and character "
              "descriptions for it (your '# character:' lines are kept)")
    saved = {} if fresh else load_prompt_records(prompts_path)
    sheet = build_sheet(characters, {} if fresh else load_characters(prompts_path))
    sizes = providers.sizes(formats)
    shapes = " + ".join("x".join(map(str, dims(z))) for z in sizes)
    print(f"Writing image prompts for {len(targets)} scene(s) (images: {provider}, "
          f"{providers.image_model.rsplit('/', 1)[-1]} at {shapes})"
          f"{' (ignoring cache and saved prompts)' if regenerate else ''}...")

    records, previous = [], None
    for i, s in targets:
        description = s.get("description", "")
        prompt = saved_prompt(saved, s)
        if not prompt:
            prompt, found = write_prompt(s["text"], previous, providers, style, sheet, description)
            learn(sheet, found, f"{s['text']} {description}")
        records.append({"scene": i + 1, "description": description, "narration": s["text"], "prompt": prompt})
        previous = prompt
    shots = []  # after the loop, so later-learned characters reach earlier scenes too
    for (i, s), rec in zip(targets, records):
        shots.append(enforce_characters(rec["prompt"], s["text"], sheet, s.get("description", "")))
        s["image_prompt"] = full_prompt(shots[-1], style)

    def save(records):
        prompts_path.parent.mkdir(parents=True, exist_ok=True)
        prompts_path.write_text(json.dumps({"image_model": providers.image_model, "style": style, "characters": sheet,
                                            "scenes": records}, indent=2, ensure_ascii=False), encoding="utf-8")

    save(records)
    print(f"  prompts saved to {prompts_path} (edit them and rerun to change the images)")
    if prompts_only:
        providers.report()
        return []

    failed = []
    made = Counter()
    for (i, s), rec, shot in zip(targets, records, shots):
        paths, errors, new = {}, [], False
        for size in sizes:
            try:
                paths[size], name, cached = providers.image(full_prompt(shot, style, size), regenerate, size)
            except ProviderError as e:
                errors.append(str(e))
                continue
            rec["image_provider"] = name
            made["cached" if cached else name] += 1
            new |= not cached
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
    save(records)
    summary = ", ".join(f"{n} {k}" for k, n in made.items()) or "none"
    print(f"  images: {summary}; {len(failed)} failed")
    providers.report()
    return failed
