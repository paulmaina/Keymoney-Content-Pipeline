"""Tests for AI scene visuals. Every Cloudflare call is mocked; nothing touches the network."""
import base64
import io
import json
import urllib.error
import urllib.request

import pytest
from PIL import Image

import ai_visuals as av
import make_video as mv

ACCOUNT, TOKEN = "acct-SECRET-456", "tok-SECRET-123"
CREDS = (ACCOUNT, TOKEN)


def jpeg_b64():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 100, 50)).save(buf, "JPEG")
    return base64.b64encode(buf.getvalue()).decode()


class FakeAPI:
    """Stands in for av.cf_run and records every request."""

    def __init__(self):
        self.calls = []
        self.fail_text = self.fail_images = False

    def __call__(self, model, payload, creds, timeout=120):
        self.calls.append((model, payload))
        if model == av.TEXT_MODEL:
            if self.fail_text:
                raise av.CloudflareError("HTTP 500", retryable=True)
            return {"response": f"Prompt #{len(self.text_calls())}"}
        if self.fail_images:
            raise av.CloudflareError("HTTP 429", retryable=True)
        return {"image": jpeg_b64()}

    def text_calls(self):
        return [p for m, p in self.calls if m == av.TEXT_MODEL]

    def image_calls(self):
        return [p for m, p in self.calls if m != av.TEXT_MODEL]


@pytest.fixture
def api(monkeypatch, tmp_path):
    fake = FakeAPI()
    monkeypatch.setattr(av, "cf_run", fake)
    monkeypatch.setattr(av, "GEN_DIR", tmp_path / "generated")
    monkeypatch.setattr(av.time, "sleep", lambda s: None)
    return fake


def scenes(*texts):
    return [{"text": t, "visual": None, "src": None} for t in texts]


def run(sc, prompts, **kw):
    return av.generate_scene_visuals(sc, prompts, creds=CREDS, **kw)


# ---------- caching ----------

def test_cache_miss_then_hit(api, tmp_path):
    prompts = tmp_path / "p.json"
    first = scenes("A clay pot in the sun.", "Fog over hills.")
    assert run(first, prompts) == []
    assert len(api.image_calls()) == 2 and all(s["src"].exists() for s in first)

    api.calls.clear()
    second = scenes("A clay pot in the sun.", "Fog over hills.")
    run(second, prompts)
    assert api.calls == []  # prompts reused from the file, images from the cache
    assert [s["src"] for s in second] == [s["src"] for s in first]


def test_cache_key_depends_on_prompt_model_and_size():
    base = av.image_path("a pot", "m1")
    assert base == av.image_path("a pot", "m1")
    assert len({base, av.image_path("a jar", "m1"), av.image_path("a pot", "m2"),
                av.image_path("a pot", "m1", size="portrait")}) == 4


def test_regenerate_ignores_cache_and_saved_prompts(api, tmp_path):
    prompts = tmp_path / "p.json"
    run(scenes("One.", "Two."), prompts)
    api.calls.clear()
    run(scenes("One.", "Two."), prompts, regenerate=True)
    assert len(api.text_calls()) == 2 and len(api.image_calls()) == 2


# ---------- prompts.json ----------

def test_saved_prompts_are_reused_so_edits_stick(api, tmp_path):
    prompts = tmp_path / "p.json"
    prompts.write_text(json.dumps({"scenes": [
        {"scene": 1, "narration": "Old narration.", "prompt": "stale"},
        {"scene": 2, "narration": "Fog over hills.", "prompt": "My edited prompt of misty hills"},
    ]}), encoding="utf-8")
    sc = scenes("Fog over hills.", "A brand new scene.")
    run(sc, prompts)
    assert len(api.text_calls()) == 1  # only the new scene needed a prompt written
    assert api.image_calls()[0]["prompt"].startswith("My edited prompt of misty hills")
    saved = json.loads(prompts.read_text(encoding="utf-8"))["scenes"]
    assert [(s["narration"], s["prompt"]) for s in saved] == [
        ("Fog over hills.", "My edited prompt of misty hills"), ("A brand new scene.", "Prompt #1")]


def test_unreadable_prompts_file_is_ignored(api, tmp_path):
    prompts = tmp_path / "p.json"
    prompts.write_text("{ not json", encoding="utf-8")
    run(scenes("One."), prompts)
    assert len(api.text_calls()) == 1


def test_previous_prompt_is_context_and_style_is_appended(api, tmp_path):
    run(scenes("First.", "Second."), tmp_path / "p.json", style="cinematic, warm tones")
    second_request = api.text_calls()[1]["messages"][1]["content"]
    assert "Prompt #1" in second_request
    img = api.image_calls()[0]
    assert img["prompt"].startswith("Prompt #1, cinematic, warm tones, centered composition")
    assert img["steps"] == av.IMAGE_STEPS and len(img["prompt"]) <= av.MAX_PROMPT


def test_text_model_failure_falls_back_to_template(api, tmp_path):
    api.fail_text = True
    run(scenes("Villagers stretch mesh nets."), tmp_path / "p.json")
    assert len(api.text_calls()) == av.ATTEMPTS
    template = av.fallback_prompt("Villagers stretch mesh nets.").rstrip(".")
    assert api.image_calls()[0]["prompt"].startswith(template + ", centered composition")


def test_shot_notes_are_sent_to_prompt_writer_and_saved(api, tmp_path):
    sc = scenes("Put one clay pot inside a bigger one.")
    sc[0]["notes"] = "HACK 1 - Visuals: hands placing a small pot inside a larger one"
    prompts = tmp_path / "p.json"
    run(sc, prompts)
    assert "Shot notes: HACK 1 - Visuals: hands placing" in api.text_calls()[0]["messages"][1]["content"]
    assert json.loads(prompts.read_text(encoding="utf-8"))["scenes"][0]["notes"].startswith("HACK 1")


def test_prompts_only_writes_prompts_but_no_images(api, tmp_path):
    prompts = tmp_path / "p.json"
    sc = scenes("One.", "Two.")
    assert run(sc, prompts, prompts_only=True) == []
    assert len(api.text_calls()) == 2 and api.image_calls() == []
    assert all(s["src"] is None for s in sc) and prompts.exists()


# ---------- failures ----------

def test_failed_image_falls_back_to_title_card(api, tmp_path):
    api.fail_images = True
    sc = scenes("One.", "Two.")
    assert run(sc, tmp_path / "p.json") == [1, 2]
    assert all(s["src"] is None for s in sc)  # None -> make_video renders a title card
    assert len(api.image_calls()) == 2 * av.ATTEMPTS


def test_bad_image_payload_is_a_failure_not_a_crash(api, tmp_path, monkeypatch):
    monkeypatch.setattr(av, "cf_run", lambda m, p, c: {"response": "x"} if m == av.TEXT_MODEL
                        else {"image": base64.b64encode(b"not an image").decode()})
    sc = scenes("One.")
    assert run(sc, tmp_path / "p.json") == [1] and sc[0]["src"] is None


def test_auth_error_stops_without_leaking_secrets(monkeypatch):
    def reject(*a, **k):
        raise av.CloudflareError("HTTP 403: Authentication error", auth=True)
    monkeypatch.setattr(av, "cf_run", reject)
    with pytest.raises(SystemExit) as e:
        av.cf_call(av.IMAGE_MODEL, {"prompt": "x"}, CREDS)
    assert "Workers AI" in str(e.value) and TOKEN not in str(e.value) and ACCOUNT not in str(e.value)


@pytest.mark.parametrize("error, retryable", [
    (urllib.error.HTTPError(f"https://x/{ACCOUNT}", 429, "Too Many Requests", {},
                            io.BytesIO(b'{"errors": [{"message": "rate limited"}]}')), True),
    (urllib.error.HTTPError(f"https://x/{ACCOUNT}", 400, "Bad Request", {}, io.BytesIO(b"")), False),
    (urllib.error.URLError(f"cannot reach {ACCOUNT}"), True),
])
def test_http_errors_are_classified_and_never_leak_credentials(monkeypatch, error, retryable):
    def fail(*a, **k):
        raise error
    monkeypatch.setattr(urllib.request, "urlopen", fail)
    with pytest.raises(av.CloudflareError) as e:
        av.cf_run(av.IMAGE_MODEL, {"prompt": "x"}, ("acct", TOKEN))
    assert e.value.retryable is retryable and TOKEN not in str(e.value)


def test_missing_credentials_explain_setup(monkeypatch, tmp_path):
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.setattr(av, "ENV_FILE", tmp_path / "missing.env")
    with pytest.raises(SystemExit) as e:
        av.credentials()
    assert "CLOUDFLARE_API_TOKEN" in str(e.value) and "Workers AI" in str(e.value)


def test_credentials_load_from_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    env = tmp_path / ".env"
    env.write_text(f"CLOUDFLARE_ACCOUNT_ID={ACCOUNT}\nCLOUDFLARE_API_TOKEN={TOKEN}\n", encoding="utf-8")
    monkeypatch.setattr(av, "ENV_FILE", env)
    assert av.credentials() == CREDS


# ---------- integration with make_video ----------

def test_explicit_visuals_are_never_sent_for_generation(api, tmp_path, monkeypatch):
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "stock.png").write_bytes(b"x")
    monkeypatch.setattr(mv, "FOOTAGE_DIRS", [pool])
    (tmp_path / "mine.png").write_bytes(b"x")
    script = tmp_path / "s.txt"
    script.write_text("[visual: mine.png]\nMy own footage scene.\n\nGenerate this one.\n", encoding="utf-8")

    sc = mv.parse_script(script)
    mv.assign_visuals(sc, tmp_path, use_pool=False)
    assert sc[0]["src"] == tmp_path / "mine.png" and sc[1]["src"] is None  # pool not used

    run(sc, tmp_path / "p.json")
    sent = json.dumps(api.calls)
    assert "My own footage scene" not in sent and len(api.image_calls()) == 1
    assert sc[0]["src"] == tmp_path / "mine.png" and sc[1]["src"].parent == av.GEN_DIR


def test_no_generation_needed_means_no_credentials_needed(monkeypatch, tmp_path):
    monkeypatch.setattr(av, "credentials", lambda: pytest.fail("credentials requested"))
    sc = [{"text": "Mine.", "visual": "mine.png", "src": tmp_path}]
    assert av.generate_scene_visuals(sc, tmp_path / "p.json") == []


def test_cli_options():
    a = mv.parse_args(["s.txt"])
    assert not a.generate_visuals and a.image_model == av.IMAGE_MODEL and a.style == ""
    a = mv.parse_args(["s.txt", "--generate-visuals", "--style", "warm", "--regenerate",
                       "--image-model", "@cf/x/y"])
    assert (a.generate_visuals, a.style, a.regenerate, a.image_model) == (True, "warm", True, "@cf/x/y")
    a = mv.parse_args(["s.txt", "--prompts-only"])
    assert a.prompts_only and a.generate_visuals
