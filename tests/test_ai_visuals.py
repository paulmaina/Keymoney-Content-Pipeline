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


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """Never read the real .env or call Pollinations for real."""
    monkeypatch.setattr(av, "ENV_FILE", tmp_path / "no.env")
    for name in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "POLLINATIONS_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    def no_network(*a, **k):
        raise AssertionError("unexpected Pollinations call")
    monkeypatch.setattr(av, "pollinations_run", no_network)


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


def test_new_images_keeps_saved_prompts_but_draws_again(api, tmp_path):
    prompts = tmp_path / "p.json"
    first = scenes("One.", "Two.")
    run(first, prompts)
    api.calls.clear()
    again = scenes("One.", "Two.")
    av.generate_scene_visuals(again, prompts, providers=av.Providers("cloudflare", creds=CREDS, new_images=True))
    assert api.text_calls() == []  # saved prompts reused
    assert len(api.image_calls()) == 2  # cache skipped: both drawn again
    # Workers AI rejects a seed for FLUX schnell; it varies the picture by itself (checked for real, Oct 2026)
    assert all("seed" not in p for p in api.image_calls())
    assert [s["src"] for s in again] == [s["src"] for s in first]  # the new images replace the cached ones
    api.calls.clear()
    run(scenes("One.", "Two."), prompts)  # a normal run afterwards reuses the new images
    assert api.image_calls() == []


def test_new_images_uses_a_random_pollinations_seed(api, tmp_path, monkeypatch):
    seeds = []
    monkeypatch.setattr(av, "pollinations_run",
                        lambda prompt, key, seed, timeout=180: seeds.append(seed) or base64.b64decode(jpeg_b64()))
    for new in (False, False, True):
        av.Providers("pollinations", creds=CREDS, poll_key="sk_x", new_images=new).image("a pot", regenerate=True)
    assert seeds[0] == seeds[1] == av.stable_seed("a pot") and seeds[2] != seeds[0]


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


def test_prompt_writer_is_never_told_about_shot_notes(api, tmp_path):
    run(scenes("Put one clay pot inside a bigger one."), tmp_path / "p.json")
    request = api.text_calls()[0]["messages"]
    assert "shot note" not in json.dumps(request).lower()


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


def test_auth_error_stops_cloudflare_mode_without_leaking_secrets(monkeypatch):
    def reject(*a, **k):
        raise av.CloudflareError("HTTP 403: Authentication error", auth=True, status=403)
    monkeypatch.setattr(av, "cf_run", reject)
    with pytest.raises(SystemExit) as e:
        av.Providers("cloudflare", creds=CREDS).image("a pot")
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


# ---------- provider fallback ----------

class FakePollinations:
    def __init__(self, fail=None):
        self.prompts, self.fail = [], fail

    def __call__(self, prompt, key, seed, timeout=180):
        self.prompts.append(prompt)
        if self.fail:
            raise self.fail
        return base64.b64decode(jpeg_b64())


def quota_error():
    return av.ProviderError("HTTP 429: 4006: you have used up your daily free allocation of 10,000 neurons",
                            quota=True, status=429)


def test_quota_exhausted_switches_to_pollinations_for_rest_of_run(api, tmp_path, monkeypatch):
    def cf(model, payload, creds, timeout=120):
        if model == av.TEXT_MODEL:  # a distinct prompt per scene, so nothing comes from the cache
            return {"response": "prompt for " + payload["messages"][1]["content"][-8:]}
        api.calls.append((model, payload))
        raise quota_error()
    monkeypatch.setattr(av, "cf_run", cf)
    poll = FakePollinations()
    monkeypatch.setattr(av, "pollinations_run", poll)
    p = av.Providers("auto", creds=CREDS, poll_key="sk_test")
    sc = scenes("One.", "Two.", "Three.")
    assert av.generate_scene_visuals(sc, tmp_path / "p.json", providers=p) == []
    assert len(api.calls) == 1  # Cloudflare tried once, then never again this run
    assert len(poll.prompts) == 3 and all(s["src"].exists() for s in sc)
    saved = json.loads((tmp_path / "p.json").read_text(encoding="utf-8"))["scenes"]
    assert {s["image_provider"] for s in saved} == {"pollinations"}


def test_server_error_does_not_stop_cloudflare(api, tmp_path, monkeypatch):
    errors = iter([av.ProviderError("HTTP 500", retryable=True, status=500)] * av.ATTEMPTS)

    def cf(model, payload, creds, timeout=120):
        if model == av.TEXT_MODEL:
            return {"response": "p"}
        api.calls.append(model)
        if (e := next(errors, None)):
            raise e
        return {"image": jpeg_b64()}
    monkeypatch.setattr(av, "cf_run", cf)
    sc = scenes("One.", "Two.")
    assert av.generate_scene_visuals(sc, tmp_path / "p.json", providers=av.Providers("auto", creds=CREDS)) == [1]
    assert sc[1]["src"].exists()  # scene 2 still went to Cloudflare


def test_both_providers_failing_falls_back_to_title_cards(api, tmp_path, monkeypatch):
    def cf(model, payload, creds, timeout=120):
        if model == av.TEXT_MODEL:
            return {"response": "p"}
        raise quota_error()
    monkeypatch.setattr(av, "cf_run", cf)
    poll = FakePollinations(fail=av.ProviderError("HTTP 402", quota=True, status=402))
    monkeypatch.setattr(av, "pollinations_run", poll)
    sc = scenes("One.", "Two.")
    failed = av.generate_scene_visuals(sc, tmp_path / "p.json",
                                       providers=av.Providers("auto", creds=CREDS, poll_key="sk_test"))
    assert failed == [1, 2] and all(s["src"] is None for s in sc)
    assert len(poll.prompts) == 1  # Pollinations also stopped after its credit ran out


def test_cached_image_from_any_provider_is_reused(api, tmp_path):
    prompt = av.full_prompt("A pot", "")
    av.save_image(base64.b64decode(jpeg_b64()), av.image_path(prompt, av.KAGGLE_CACHE_ID))
    path, name, cached = av.Providers("cloudflare", creds=CREDS).image(prompt)
    assert (name, cached) == ("kaggle", True) and api.image_calls() == []


def test_kaggle_provider_never_calls_online_image_apis(api, tmp_path):
    sc = scenes("One.")
    assert av.generate_scene_visuals(sc, tmp_path / "p.json",
                                     providers=av.Providers("kaggle", creds=CREDS)) == [1]
    assert api.image_calls() == [] and len(api.text_calls()) == 1


def test_missing_all_credentials_in_auto_mode_explains_both(monkeypatch):
    with pytest.raises(SystemExit) as e:
        av.Providers("auto")
    assert "Cloudflare" in str(e.value) and "POLLINATIONS_API_KEY" in str(e.value)


def test_neuron_estimate_uses_reported_tokens(api, tmp_path, monkeypatch):
    def cf(model, payload, creds, timeout=120):
        if model == av.TEXT_MODEL:
            return {"response": "p", "usage": {"prompt_tokens": 1_000, "completion_tokens": 100}}
        return {"image": jpeg_b64()}
    monkeypatch.setattr(av, "cf_run", cf)
    p = av.Providers("cloudflare", creds=CREDS)
    av.generate_scene_visuals(scenes("One."), tmp_path / "p.json", providers=p)
    rate_in, rate_out = av.TEXT_NEURONS[av.TEXT_MODEL]
    assert p.neurons == pytest.approx((1_000 * rate_in + 100 * rate_out) / 1e6 + av.FLUX_NEURONS)
    assert av.FLUX_NEURONS == pytest.approx(57.6)


def test_pollinations_errors_never_leak_key(monkeypatch):
    monkeypatch.undo()  # use the real pollinations_run, with urlopen mocked
    def fail(req, timeout=None):
        assert "sk_SECRET" not in req.full_url  # the key goes in a header only
        raise urllib.error.HTTPError(req.full_url, 402, "Payment Required", {}, io.BytesIO(b""))
    monkeypatch.setattr(urllib.request, "urlopen", fail)
    with pytest.raises(av.ProviderError) as e:
        av.pollinations_run("a pot", "sk_SECRET", 1)
    assert e.value.quota and "sk_SECRET" not in str(e.value)


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


def test_text_returns_json_text_when_the_reply_arrives_parsed(monkeypatch):
    # llama-3.3-70b's JSON replies come back as an object in "response", not a string
    monkeypatch.setattr(av, "cf_call", lambda model, payload, creds: {"response": {"titles": ["Fog Nets in Chile"]}})
    p = av.Providers("cloudflare", creds=CREDS, poll_key="")
    assert json.loads(p.text([{"role": "user", "content": "x"}])) == {"titles": ["Fog Nets in Chile"]}
