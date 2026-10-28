"""AI-generated scene images via Cloudflare Workers AI (used by make_video.py --generate-visuals).

For each scene without a [visual: ...] line, a text model turns the narration into an image
prompt, then FLUX.1 schnell draws it. Prompts are saved to Exports/<name>_prompts.json for
review and editing; images are cached in generated/ so reruns don't spend the free quota.

FLUX.1 schnell on Workers AI only documents `prompt` and `steps` (no width/height), so one
square image is made per scene and the normal crop fits it to 16:9 and 9:16.
"""
import base64
import hashlib
import io
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
GEN_DIR = ROOT / "generated"
API = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
IMAGE_MODEL = "@cf/black-forest-labs/flux-1-schnell"
TEXT_MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
IMAGE_SIZE = "square"  # part of the cache key, in case sized output becomes available
IMAGE_STEPS = 4
MAX_PROMPT = 2048  # FLUX.1 schnell prompt limit
ATTEMPTS = 3
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
    "If shot notes are given, follow them; they describe the shot the director planned. "
    "Keep the main subject centred. Reply with the prompt only, no preamble or quotes."
)


class CloudflareError(Exception):
    def __init__(self, message, retryable=False, auth=False):
        super().__init__(message)
        self.retryable = retryable
        self.auth = auth


def credentials():
    """(account_id, api_token) from the environment, or from .env if present."""
    if ENV_FILE.exists():
        from dotenv import load_dotenv
        load_dotenv(ENV_FILE, override=False)
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    missing = [n for n, v in (("CLOUDFLARE_ACCOUNT_ID", account), ("CLOUDFLARE_API_TOKEN", token)) if not v]
    if missing:
        raise SystemExit(
            f"--generate-visuals needs {' and '.join(missing)}.\n"
            "1. Sign in at https://dash.cloudflare.com (a free account is enough).\n"
            "2. Open AI > Workers AI and select 'Use REST API'.\n"
            "3. Copy the Account ID, then 'Create a Workers AI API Token' (it needs Workers AI Read + Edit).\n"
            "4. Copy .env.example to .env and paste both values in, or set them as environment variables.")
    return account, token


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
        detail = ""
        try:
            detail = "; ".join(err.get("message", "") for err in json.loads(e.read()).get("errors", []))
        except (ValueError, AttributeError):
            pass
        raise CloudflareError(f"HTTP {e.code}{': ' + detail if detail else ''}",
                              retryable=e.code == 429 or e.code >= 500, auth=e.code in (401, 403)) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise CloudflareError(f"network error ({getattr(e, 'reason', e)})", retryable=True) from None
    if not body.get("success", False):
        msgs = "; ".join(err.get("message", "") for err in body.get("errors", [])) or "unknown error"
        raise CloudflareError(msgs)
    return body["result"]


def cf_call(model, payload, creds):
    """cf_run with retries and backoff on network errors, rate limits and server errors."""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return cf_run(model, payload, creds)
        except CloudflareError as e:
            if e.auth:
                raise SystemExit(f"Cloudflare rejected the credentials ({e}).\n"
                                 "Check CLOUDFLARE_ACCOUNT_ID, and that the API token has "
                                 "Workers AI Read + Edit permission.") from None
            if not e.retryable or attempt == ATTEMPTS:
                raise
            wait = 2 ** attempt
            print(f"    {e}; retrying in {wait}s...")
            time.sleep(wait)


def fallback_prompt(text):
    return f"Documentary photograph illustrating this scene: {text[:400]}"


def write_prompt(text, previous, creds, notes=""):
    context = f"Previous scene's image prompt, keep visual continuity: {previous}\n\n" if previous else ""
    if notes:
        context += f"Shot notes: {notes}\n\n"
    try:
        result = cf_call(TEXT_MODEL, {
            "messages": [{"role": "system", "content": PROMPT_SYSTEM},
                         {"role": "user", "content": f"{context}Narration for this scene: {text}"}],
            "max_tokens": 160, "temperature": 0.7}, creds)
        prompt = str(result.get("response") or "").strip().strip('"').strip()
        if prompt:
            return prompt
    except CloudflareError as e:
        print(f"    prompt writer failed ({e}); using a simple prompt")
    return fallback_prompt(text)


def full_prompt(prompt, style):
    return ", ".join(p for p in (prompt.rstrip(". "), style, COMPOSITION) if p)[:MAX_PROMPT]


def image_path(prompt, model, size=IMAGE_SIZE):
    key = hashlib.sha256(f"{model}|{size}|{prompt}".encode()).hexdigest()[:20]
    return GEN_DIR / f"{key}.jpg"


def generate_image(prompt, model, creds, regenerate=False):
    """Return (path, from_cache). Raises CloudflareError if generation fails."""
    path = image_path(prompt, model)
    if path.exists() and not regenerate:
        return path, True
    result = cf_call(model, {"prompt": prompt, "steps": IMAGE_STEPS}, creds)
    try:
        data = base64.b64decode(result["image"])
        from PIL import Image
        Image.open(io.BytesIO(data)).verify()
    except Exception as e:  # missing field, bad base64 or not an image
        raise CloudflareError(f"unexpected image response ({e.__class__.__name__})") from None
    GEN_DIR.mkdir(exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(path)
    return path, False


def load_prompts(path):
    """Saved prompts keyed by narration, so edits survive reordering the script."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {s["narration"]: s["prompt"] for s in data["scenes"] if s.get("prompt")}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def generate_scene_visuals(scenes, prompts_path, model=IMAGE_MODEL, style="", regenerate=False, creds=None,
                           prompts_only=False):
    """Fill s["src"] for every scene without a [visual: ...] line. Scenes whose image can't be
    generated keep src None (title card). Returns the 1-based numbers of failed scenes.
    With prompts_only, stop after writing the prompts file."""
    targets = [(i, s) for i, s in enumerate(scenes) if not s["visual"]]
    if not targets:
        return []
    creds = creds or credentials()
    saved = {} if regenerate else load_prompts(prompts_path)
    print(f"Generating visuals for {len(targets)} scene(s) with {model}"
          f"{' (ignoring cache and saved prompts)' if regenerate else ''}...")

    records, previous = [], None
    for i, s in targets:
        notes = s.get("notes", "")
        prompt = saved.get(s["text"]) or write_prompt(s["text"], previous, creds, notes)
        records.append({"scene": i + 1, "notes": notes, "narration": s["text"], "prompt": prompt})
        previous = prompt
    prompts_path.parent.mkdir(parents=True, exist_ok=True)
    prompts_path.write_text(json.dumps({"image_model": model, "style": style, "scenes": records},
                                       indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  prompts saved to {prompts_path} (edit them and rerun to change the images)")
    if prompts_only:
        return []

    made = reused = 0
    failed = []
    for (i, s), rec in zip(targets, records):
        try:
            s["src"], cached = generate_image(full_prompt(rec["prompt"], style), model, creds, regenerate)
        except CloudflareError as e:
            print(f"  scene {i + 1}: FAILED ({e}); using a title card")
            s["src"] = None
            failed.append(i + 1)
            continue
        reused += cached
        made += not cached
        print(f"  scene {i + 1}: {'cached' if cached else 'generated'} {s['src'].name}")
    print(f"  images: {made} generated, {reused} reused from cache, {len(failed)} failed")
    return failed
