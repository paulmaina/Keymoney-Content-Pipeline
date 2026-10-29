"""AI-generated scene images (used by make_video.py --generate-visuals).

For each scene without a [visual: ...] line, a small Workers AI text model turns the narration
into an image prompt, then an image provider draws it:

  cloudflare    FLUX.1 schnell on Cloudflare Workers AI (free daily allowance)
  pollinations  FLUX.1 schnell on Pollinations (needs a free key in POLLINATIONS_API_KEY)
  kaggle        SDXL in the Kaggle notebook; images come back through make_video.py --clips
  auto          Cloudflare, switching to Pollinations for the rest of the run once Cloudflare's
                daily allowance runs out; scenes that still fail get a title card

Prompts are saved to Exports/<name>_prompts.json for review and editing. Images are cached in
generated/ (keyed by prompt + size + provider model) and any provider's cached image is reused,
so reruns don't spend quota.

FLUX.1 schnell on Workers AI only documents `prompt` and `steps` (no width/height), so every
provider makes one square 1024x1024 image per scene and the normal crop fits it to 16:9 and 9:16.
"""
import base64
import hashlib
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
GEN_DIR = ROOT / "generated"
API = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
POLLINATIONS_API = "https://gen.pollinations.ai/image/{prompt}"
IMAGE_MODEL = "@cf/black-forest-labs/flux-1-schnell"
TEXT_MODEL = "@cf/meta/llama-3.1-8b-instruct-fp8"
POLLINATIONS_MODEL = "flux"  # alias for black-forest-labs/flux.1-schnell
POLLINATIONS_CACHE_ID = "pollinations:flux"
KAGGLE_CACHE_ID = "kaggle:sdxl"
PROVIDERS = ("auto", "cloudflare", "pollinations", "kaggle")
IMAGE_SIZE = "square"  # part of the cache key, in case sized output becomes available
IMAGE_PX = 1024
IMAGE_STEPS = 4
MAX_PROMPT = 2048  # FLUX.1 schnell prompt limit
ATTEMPTS = 3
FREE_NEURONS_PER_DAY = 10_000
# Workers AI pricing page: neurons per million input / output tokens, and FLUX per tile and step
TEXT_NEURONS = {
    "@cf/meta/llama-3.1-8b-instruct-fp8": (13_778, 26_128),
    "@cf/meta/llama-3.1-8b-instruct-fp8-fast": (4_119, 34_868),
    "@cf/meta/llama-3.2-3b-instruct": (4_625, 30_475),
    "@cf/meta/llama-3.3-70b-instruct-fp8-fast": (26_668, 204_805),
}
FLUX_NEURONS = (IMAGE_PX // 512) ** 2 * 4.80 + IMAGE_STEPS * 9.60  # 57.6 for a 1024x1024, 4-step image
# square images are cropped to both 16:9 and 9:16, so keep the subject in the middle
COMPOSITION = "centered composition, main subject in the middle of the frame, no text, no watermark"

PROMPT_SYSTEM = (
    "You write prompts for an AI image generator that illustrates scenes of a narrated video. "
    "Given the narration for one scene, reply with a single image prompt of at most 60 words "
    "describing one concrete shot: subject, setting, lighting, camera angle and mood. "
    "Never include text, letters, signs, captions, logos or watermarks in the image. "
    "Never name real people; describe them generically (e.g. 'a Nigerian teacher'). "
    "Depict people respectfully and accurately for the place and present day: everyday contemporary "
    "clothing suited to the region and work, traditional dress only where it would genuinely be worn, "
    "described specifically rather than as generic 'tribal' or 'ethnic' costume; no poverty clichés or caricature. "
    "If the script gives a visual description for the scene, it decides what the image shows: follow it closely "
    "and use the narration only for context (place, people, time of day). "
    "If a visual style is given, write the prompt for that style and don't contradict it (no 'photograph' for an animated style). "
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


def cf_run(model, payload, creds, timeout=120):
    """POST to Workers AI and return the `result` object. Error messages never include
    the account ID or token."""
    account, token = creds
    req = urllib.request.Request(
        API.format(account=account, model=model), data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read())
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


def pollinations_run(prompt, key, seed, timeout=180):
    """GET an image from Pollinations and return its bytes. The key is sent as a header only."""
    query = urllib.parse.urlencode({"model": POLLINATIONS_MODEL, "width": IMAGE_PX, "height": IMAGE_PX,
                                    "seed": seed})
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


def image_path(prompt, model, size=IMAGE_SIZE):
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
        self.mode, self.image_model, self.text_model = mode, image_model, text_model
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
        tin = usage.get("prompt_tokens") or sum(len(m["content"]) for m in messages) / 4
        tout = usage.get("completion_tokens") or len(str(result.get("response") or "")) / 4
        rate_in, rate_out = TEXT_NEURONS.get(self.text_model, TEXT_NEURONS[TEXT_MODEL])
        self.neurons += (tin * rate_in + tout * rate_out) / 1e6
        self.counts["text"] += 1
        return str(result.get("response") or "").strip().strip('"').strip() or None

    def cache_ids(self):
        return [(self.image_model, "cloudflare"), (POLLINATIONS_CACHE_ID, "pollinations"), (KAGGLE_CACHE_ID, "kaggle")]

    def cached(self, prompt):
        for model, name in self.cache_ids():
            p = image_path(prompt, model)
            if p.exists():
                return p, name
        return None

    def _cloudflare(self, prompt):
        # no seed: Workers AI rejects it for FLUX.1 schnell ("Additional ... properties '/seed' not allowed")
        result = cf_call(self.image_model, {"prompt": prompt, "steps": IMAGE_STEPS}, self.creds)
        try:
            data = base64.b64decode(result["image"])
        except (KeyError, TypeError, ValueError):
            raise ProviderError("unexpected image response (no image field)") from None
        self.neurons += FLUX_NEURONS
        return check_image(data)

    def _pollinations(self, prompt):
        seed = random_seed() if self.new_images else stable_seed(prompt)
        return check_image(with_retries(pollinations_run, prompt, self.poll_key, seed))

    def image(self, prompt, regenerate=False):
        """Return (path, provider, from_cache). Raises ProviderError when no provider can make it."""
        if not (regenerate or self.new_images) and (hit := self.cached(prompt)):
            return hit[0], hit[1], True
        attempts = []
        if self.mode in ("auto", "cloudflare") and self.cf_ok:
            attempts.append(("cloudflare", self.image_model, self._cloudflare))
        if self.mode in ("auto", "pollinations") and self.poll_key:
            attempts.append(("pollinations", POLLINATIONS_CACHE_ID, self._pollinations))
        if self.mode == "kaggle":
            raise ProviderError("not made yet: export with --export-video-job --image-provider kaggle, "
                                "run the Kaggle notebook, then import with --clips")
        errors = []
        for name, cache_id, make in attempts:
            try:
                path = save_image(make(prompt), image_path(prompt, cache_id))
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


GENERIC_NAME_WORDS = {"the", "and", "with", "old", "young", "little", "big", "mr", "mrs", "miss", "man", "woman"}


def mentioned(name, text):
    """True if the narration uses any distinctive word of the character's name ("Kofi" or "Grandpa" for
    "Grandpa Kofi"), as a whole word."""
    words = [w for w in re.findall(r"\w+", name.lower()) if len(w) > 2 and w not in GENERIC_NAME_WORDS]
    return any(re.search(rf"\b{re.escape(w)}\b", text, re.I) for w in words)


def enforce_characters(prompt, narration, sheet):
    """Make sure every sheet character named in the narration is described in the prompt (description only,
    never the name, so real people's names stay out of images)."""
    for name, desc in sheet.items():
        if mentioned(name, narration) and desc.lower()[:40] not in prompt.lower():
            prompt = f"{prompt.rstrip('. ')}, with {desc}"
    return prompt


def write_prompt(text, previous, providers, style="", sheet=None, description=""):
    """Returns (prompt, characters the writer saw in this shot)."""
    context = f"Previous scene's image prompt, keep visual continuity: {previous}\n\n" if previous else ""
    if style:
        context += f"Visual style of the whole video: {style}\n\n"
    if sheet:
        context += f"Character sheet: {sheet_text(sheet)}\n\n"
    if description:
        context += f"Visual description from the script (this is what the image must show): {description}\n\n"
    reply = providers.text([{"role": "system", "content": PROMPT_SYSTEM},
                            {"role": "user", "content": f"{context}Narration for this scene: {text}"}], max_tokens=220)
    if not reply:
        return fallback_prompt(text, description), {}
    prompt, found = split_characters(reply)
    return prompt or fallback_prompt(text, description), found


def full_prompt(prompt, style):
    return ", ".join(p for p in (prompt.rstrip(". "), style, COMPOSITION) if p)[:MAX_PROMPT]


def load_prompt_records(path):
    """Saved prompt records keyed by narration, so edits survive reordering the script."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {s["narration"]: s for s in data["scenes"] if s.get("prompt")}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
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


def load_characters(path):
    """The character sheet saved in prompts.json (editable, like the prompts)."""
    try:
        sheet = json.loads(path.read_text(encoding="utf-8")).get("characters") or {}
        return {str(k): str(v) for k, v in sheet.items()} if isinstance(sheet, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def build_sheet(declared, saved):
    """Character sheet: saved/learned entries, overridden by '# character:' lines in the script."""
    sheet = dict(saved)
    for name, desc in (declared or {}).items():
        for old in [k for k in sheet if k.lower() == name.lower()]:
            del sheet[old]
        sheet[name] = desc
    return sheet


def learn(sheet, found):
    """Add characters the prompt writer described for the first time; existing entries never change."""
    known = {k.lower() for k in sheet}
    for name, desc in found.items():
        if name.lower() not in known:
            sheet[name] = desc
            known.add(name.lower())


def generate_scene_visuals(scenes, prompts_path, model=IMAGE_MODEL, style="", regenerate=False, creds=None,
                           prompts_only=False, provider="auto", text_model=TEXT_MODEL, only=None, providers=None,
                           characters=None):
    """Fill s["src"] (and s["image_prompt"]) for every scene without a [visual: ...] line.
    `only` limits image generation to those scene indices (prompts are still written for all).
    `characters` is the script's declared character sheet; recurring characters the prompt writer
    describes are added to it, and every image prompt that names one gets its description.
    Scenes whose image can't be made keep src None (title card). Returns the 1-based numbers of
    failed scenes. With prompts_only, stop after writing the prompts file."""
    targets = [(i, s) for i, s in enumerate(scenes) if not s["visual"]]
    if not targets:
        return []
    providers = providers or Providers(provider, model, text_model, creds)
    saved = {} if regenerate else load_prompt_records(prompts_path)
    sheet = build_sheet(characters, {} if regenerate else load_characters(prompts_path))
    print(f"Writing image prompts for {len(targets)} scene(s) (images: {provider})"
          f"{' (ignoring cache and saved prompts)' if regenerate else ''}...")

    records, previous = [], None
    for i, s in targets:
        description = s.get("description", "")
        prompt = saved_prompt(saved, s)
        if not prompt:
            prompt, found = write_prompt(s["text"], previous, providers, style, sheet, description)
            learn(sheet, found)
        records.append({"scene": i + 1, "description": description, "narration": s["text"], "prompt": prompt})
        previous = prompt
    for (i, s), rec in zip(targets, records):  # after the loop, so later-learned characters reach earlier scenes too
        s["image_prompt"] = full_prompt(enforce_characters(rec["prompt"], s["text"], sheet), style)

    def save(records):
        prompts_path.parent.mkdir(parents=True, exist_ok=True)
        prompts_path.write_text(json.dumps({"image_model": model, "style": style, "characters": sheet,
                                            "scenes": records}, indent=2, ensure_ascii=False), encoding="utf-8")

    save(records)
    print(f"  prompts saved to {prompts_path} (edit them and rerun to change the images)")
    if prompts_only:
        providers.report()
        return []

    failed = []
    made = Counter()
    for (i, s), rec in zip(targets, records):
        if only is not None and i not in only:
            continue
        try:
            s["src"], name, cached = providers.image(s["image_prompt"], regenerate)
        except ProviderError as e:
            print(f"  scene {i + 1}: FAILED ({e}); using a title card")
            s["src"] = None
            failed.append(i + 1)
            continue
        rec["image_provider"] = name
        made["cached" if cached else name] += 1
        print(f"  scene {i + 1}: {'cached' if cached else 'generated'} by {name}: {s['src'].name}")
    save(records)
    summary = ", ".join(f"{n} {k}" for k, n in made.items()) or "none"
    print(f"  images: {summary}; {len(failed)} failed")
    providers.report()
    return failed
