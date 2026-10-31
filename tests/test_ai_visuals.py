"""Tests for AI scene visuals. Every Cloudflare call is mocked; nothing touches the network."""
import base64
import io
import json
import sys
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
    """One format, so the default sized model draws one image per scene (both formats are tested below)."""
    return av.generate_scene_visuals(sc, prompts, creds=CREDS, **{"formats": ("horizontal",), **kw})


ONE = {"formats": ("horizontal",)}


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
                av.image_path("a pot", "m1", size="horizontal"), av.image_path("a pot", "m1", size="vertical")}) == 5
    # sized keys use the pixel size, so changing SIZES never reuses an image of the old shape
    assert av.image_path("a pot", "m1", "horizontal") == av.image_path("a pot", "m1", "1536x864")


def test_new_images_keeps_saved_prompts_but_draws_again(api, tmp_path):
    prompts = tmp_path / "p.json"
    first = scenes("One.", "Two.")
    run(first, prompts)
    api.calls.clear()
    again = scenes("One.", "Two.")
    av.generate_scene_visuals(again, prompts, providers=av.Providers("final", creds=CREDS, new_images=True), **ONE)
    assert api.text_calls() == []  # saved prompts reused
    assert len(api.image_calls()) == 2  # cache skipped: both drawn again
    seeds = [p["seed"] for p in api.image_calls()]  # FLUX.2 takes a seed: a random one gives a new picture
    assert len(set(seeds)) == 2
    assert [s["src"] for s in again] == [s["src"] for s in first]  # the new images replace the cached ones
    api.calls.clear()
    run(scenes("One.", "Two."), prompts)  # a normal run afterwards reuses the new images
    assert api.image_calls() == []


def test_new_images_uses_a_random_pollinations_seed(api, tmp_path, monkeypatch):
    seeds = []
    monkeypatch.setattr(av, "pollinations_run", lambda prompt, key, seed, timeout=180, size="square":
                        seeds.append(seed) or base64.b64decode(jpeg_b64()))
    for new in (False, False, True):
        av.Providers("draft", creds=CREDS, poll_key="sk_x", new_images=new).image("a pot", regenerate=True)
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
    assert img["prompt"].startswith("Prompt #1, cinematic, warm tones, wide 16:9 frame")
    assert (img["width"], img["height"]) == (1536, 864) and len(img["prompt"]) <= av.MAX_PROMPT
    assert "steps" not in img  # FLUX.2 klein fixes its own 4 steps


def test_text_model_failure_falls_back_to_template(api, tmp_path):
    api.fail_text = True
    run(scenes("Villagers stretch mesh nets."), tmp_path / "p.json")
    assert len(api.text_calls()) == 2  # one retry
    template = av.fallback_prompt("Villagers stretch mesh nets.").rstrip(".")
    assert api.image_calls()[0]["prompt"].startswith(template + ", " + av.COMPOSITION["horizontal"])
    assert json.loads((tmp_path / "p.json").read_text(encoding="utf-8"))["scenes"][0]["stand_in"]  # written next time


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
    assert len(api.image_calls()) == 2 * 2  # each scene: one try and one retry


def test_bad_image_payload_is_a_failure_not_a_crash(api, tmp_path, monkeypatch):
    monkeypatch.setattr(av, "cf_run", lambda m, p, c: {"response": "x"} if m == av.TEXT_MODEL
                        else {"image": base64.b64encode(b"not an image").decode()})
    sc = scenes("One.")
    assert run(sc, tmp_path / "p.json") == [1] and sc[0]["src"] is None


def test_auth_error_stops_cloudflare_without_leaking_secrets(monkeypatch, capsys):
    calls = []

    def reject(*a, **k):
        calls.append(1)
        raise av.CloudflareError("HTTP 403: Authentication error", auth=True, status=403)
    monkeypatch.setattr(av, "cf_run", reject)
    p = av.Providers("final", creds=CREDS, poll_key="")
    for prompt in ("a pot", "a jar"):
        with pytest.raises(av.ProviderError):
            p.image(prompt)
    out = capsys.readouterr().out
    assert len(calls) == 1  # no retry for bad credentials, and never asked again this run
    assert "Workers AI Read + Edit" in out and TOKEN not in out and ACCOUNT not in out


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

    def __call__(self, prompt, key, seed, timeout=180, size="square"):
        self.prompts.append(prompt)
        if self.fail:
            raise self.fail
        return base64.b64decode(jpeg_b64())


def quota_error():
    return av.ProviderError("HTTP 429: 4006: you have used up your daily free allocation of 10,000 neurons",
                            quota=True, status=429)


def test_a_cloudflare_error_after_a_retry_sends_that_scene_to_the_backup(api, tmp_path, monkeypatch):
    def cf(model, payload, creds, timeout=120):
        if model == av.TEXT_MODEL:  # a distinct prompt per scene, so nothing comes from the cache
            return {"response": "prompt for " + payload["messages"][1]["content"][-8:]}
        api.calls.append((model, payload))
        if "Two" in payload["prompt"]:
            return {"image": jpeg_b64()}
        raise quota_error()  # the free plan's allowance message: on Workers Paid, a real error like any other
    monkeypatch.setattr(av, "cf_run", cf)
    poll = FakePollinations()
    monkeypatch.setattr(av, "pollinations_run", poll)
    p = av.Providers("final", creds=CREDS, poll_key="sk_test")
    sc = scenes("One.", "Two.", "Three.")
    assert av.generate_scene_visuals(sc, tmp_path / "p.json", providers=p, **ONE) == []
    assert len(api.calls) == 2 + 1 + 2  # Cloudflare is asked for every scene: a try and one retry each
    assert len(poll.prompts) == 2 and all(s["src"].exists() for s in sc)
    assert p.backup == {1: "Pollinations backup image", 3: "Pollinations backup image"}
    saved = json.loads((tmp_path / "p.json").read_text(encoding="utf-8"))["scenes"]
    assert [s["image_provider"] for s in saved] == ["pollinations", "cloudflare", "pollinations"]


def test_server_error_does_not_stop_cloudflare(api, tmp_path, monkeypatch):
    errors = iter([av.ProviderError("HTTP 500", retryable=True, status=500)] * 2)  # a try and its retry

    def cf(model, payload, creds, timeout=120):
        if model == av.TEXT_MODEL:
            return {"response": "p"}
        api.calls.append(model)
        if (e := next(errors, None)):
            raise e
        return {"image": jpeg_b64()}
    monkeypatch.setattr(av, "cf_run", cf)
    sc = scenes("One.", "Two.")
    assert av.generate_scene_visuals(sc, tmp_path / "p.json", providers=av.Providers("final", creds=CREDS), **ONE) == [1]
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
                                       providers=av.Providers("final", creds=CREDS, poll_key="sk_test"), **ONE)
    assert failed == [1, 2] and all(s["src"] is None for s in sc)
    assert len(poll.prompts) == 1  # Pollinations also stopped after its credit ran out


def test_a_final_render_never_reuses_backup_or_draft_images(api, tmp_path):
    prompt = av.full_prompt("A pot", "")
    av.save_image(base64.b64decode(jpeg_b64()), av.image_path(prompt, av.POLLINATIONS_CACHE_ID))
    draft = av.save_image(base64.b64decode(jpeg_b64()), av.draft_path(prompt))
    path, name, cached = av.Providers("final", creds=CREDS, poll_key="").image(prompt)
    assert (name, cached) == ("cloudflare", False) and len(api.image_calls()) == 1  # Cloudflare asked again
    assert av.Providers("draft", creds=CREDS, poll_key="sk_test").image(prompt) == (draft, "pollinations", True)
    assert draft.parent == av.GEN_DIR / "drafts" and draft != path


def test_a_final_render_needs_cloudflare_and_a_draft_needs_pollinations(monkeypatch):
    with pytest.raises(SystemExit) as e:
        av.Providers("final", poll_key="sk_test")
    assert "CLOUDFLARE_API_TOKEN" in str(e.value)
    with pytest.raises(SystemExit) as e:
        av.Providers("draft", creds=CREDS, poll_key="")
    assert "POLLINATIONS_API_KEY" in str(e.value)


def test_neuron_estimate_uses_reported_tokens(api, tmp_path, monkeypatch):
    def cf(model, payload, creds, timeout=120):
        if model == av.TEXT_MODEL:
            return {"response": "p", "usage": {"prompt_tokens": 1_000, "completion_tokens": 100}}
        return {"image": jpeg_b64()}
    monkeypatch.setattr(av, "cf_run", cf)
    p = av.Providers("final", creds=CREDS)
    av.generate_scene_visuals(scenes("One."), tmp_path / "p.json", providers=p)  # both formats: two images
    rate_in, rate_out = av.TEXT_NEURONS[av.TEXT_MODEL]
    images = av.image_neurons(av.IMAGE_MODEL, "horizontal") + av.image_neurons(av.IMAGE_MODEL, "vertical")
    assert p.neurons == pytest.approx((1_000 * rate_in + 100 * rate_out) / 1e6 + images)


def test_image_costs_follow_the_workers_ai_price_list():
    # FLUX.2 klein 4B: 26.05 neurons per 512x512 output tile; FLUX.1 schnell: 4.80 per tile + 9.60 per step
    assert av.image_neurons(av.IMAGE_MODEL, "horizontal") == pytest.approx(1536 * 864 / 512 ** 2 * 26.05)
    assert av.image_neurons(av.IMAGE_MODEL, "horizontal") == pytest.approx(131.9, abs=0.1)
    assert av.FLUX_NEURONS == pytest.approx(57.6)
    assert av.IMAGE_MODELS["flux-2-klein-9b"].neurons(1024, 1024) == pytest.approx(1363.64)
    # the realistic alternatives cost 10-30x more: a whole video wouldn't fit the free 10,000 a day
    two_per_scene = {n: 2 * 19 * m.neurons(1536, 864) for n, m in av.IMAGE_MODELS.items() if m.sized}
    assert [n for n, c in two_per_scene.items() if c <= av.FREE_NEURONS_PER_DAY] == ["flux-2-klein-4b"]
    assert two_per_scene["flux-2-klein-4b"] < 5_100


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
    assert a.generate_visuals and a.image_model == av.IMAGE_MODEL and a.style == ""  # a full final render
    a = mv.parse_args(["s.txt", "--generate-visuals", "--style", "warm", "--regenerate",
                       "--image-model", "@cf/x/y"])
    assert (a.generate_visuals, a.style, a.regenerate, a.image_model) == (True, "warm", True, "@cf/x/y")
    a = mv.parse_args(["s.txt", "--prompts-only"])
    assert a.prompts_only and a.generate_visuals


def test_text_returns_json_text_when_the_reply_arrives_parsed(monkeypatch):
    # llama-3.3-70b's JSON replies come back as an object in "response", not a string
    monkeypatch.setattr(av, "cf_run", lambda model, payload, creds, timeout=120:
                        {"response": {"titles": ["Fog Nets in Chile"]}})
    p = av.Providers("final", creds=CREDS, poll_key="")
    assert json.loads(p.text([{"role": "user", "content": "x"}])) == {"titles": ["Fog Nets in Chile"]}


# ---------- image models and sizes ----------

def test_sized_model_draws_a_real_16_9_and_9_16_image_per_scene(api, tmp_path):
    sc = scenes("Fog over hills.")
    assert av.generate_scene_visuals(sc, tmp_path / "p.json", providers=av.Providers("final", creds=CREDS)) == []
    sizes = [(p["width"], p["height"]) for p in api.image_calls()]
    assert sizes == [(1536, 864), (864, 1536)]
    prompts = [p["prompt"] for p in api.image_calls()]
    assert prompts[0].endswith(av.COMPOSITION["horizontal"]) and prompts[1].endswith(av.COMPOSITION["vertical"])
    assert set(sc[0]["srcs"]) == {"horizontal", "vertical"} and sc[0]["srcs"]["horizontal"] != sc[0]["srcs"]["vertical"]
    assert sc[0]["src"] == sc[0]["srcs"]["horizontal"] and sc[0]["ai"]
    assert mv.scene_src(sc[0], "vertical") == sc[0]["srcs"]["vertical"]


def test_square_model_draws_one_image_for_both_formats(api, tmp_path):
    sc = scenes("Fog over hills.")
    p = av.Providers("final", image_model="flux-1-schnell", creds=CREDS, new_images=True)
    assert p.image_model == av.SQUARE_MODEL  # short names work
    av.generate_scene_visuals(sc, tmp_path / "p.json", providers=p)
    [img] = api.image_calls()
    # Workers AI rejects a seed for FLUX schnell and only documents prompt + steps (checked for real, Oct 2026)
    assert img["steps"] == 4 and not {"width", "height", "seed"} & set(img)
    assert img["prompt"].endswith(av.COMPOSITION["square"]) and "srcs" not in sc[0]
    assert mv.scene_src(sc[0], "vertical") == sc[0]["src"]


def test_one_failed_shape_falls_back_to_the_other(api, tmp_path, monkeypatch):
    def cf(model, payload, creds, timeout=120):
        if model == av.TEXT_MODEL:
            return {"response": "p"}
        if payload["height"] > payload["width"]:
            raise av.ProviderError("HTTP 400", status=400)
        return {"image": jpeg_b64()}
    monkeypatch.setattr(av, "cf_run", cf)
    sc = scenes("One.")
    assert av.generate_scene_visuals(sc, tmp_path / "p.json", providers=av.Providers("final", creds=CREDS)) == []
    assert sc[0]["srcs"]["vertical"] == sc[0]["srcs"]["horizontal"]  # cropped from the 16:9 image


def test_only_one_format_draws_only_that_shape(api, tmp_path):
    run(scenes("One."), tmp_path / "p.json", formats=("vertical",))
    assert [(p["width"], p["height"]) for p in api.image_calls()] == [(864, 1536)]


class FakeResponse(io.BytesIO):
    def __init__(self, body, ctype):
        super().__init__(body)
        self.headers = {"Content-Type": ctype}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_flux2_requests_are_multipart_forms(monkeypatch):
    seen = {}

    def fake(req, timeout=None):
        seen["ctype"], seen["body"] = req.get_header("Content-type"), req.data.decode()
        return FakeResponse(json.dumps({"success": True, "result": {"image": jpeg_b64()}}).encode(), "application/json")
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    av.cf_run(av.IMAGE_MODEL, {"prompt": "a pot", "width": 1536, "height": 864}, CREDS)
    assert seen["ctype"].startswith("multipart/form-data; boundary=")
    assert 'name="prompt"\r\n\r\na pot\r\n' in seen["body"] and 'name="width"\r\n\r\n1536\r\n' in seen["body"]
    av.cf_run(av.SQUARE_MODEL, {"prompt": "a pot", "steps": 4}, CREDS)
    assert seen["ctype"] == "application/json" and json.loads(seen["body"]) == {"prompt": "a pot", "steps": 4}


def test_a_reply_that_is_the_image_itself_is_accepted(monkeypatch):
    jpeg = base64.b64decode(jpeg_b64())  # Leonardo Phoenix answers with image/jpeg bytes, not JSON
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: FakeResponse(jpeg, "image/jpeg"))
    assert base64.b64decode(av.cf_run("@cf/leonardo/phoenix-1.0", {"prompt": "x"}, CREDS)["image"]) == jpeg


def test_model_names_and_ids():
    assert av.resolve_model("flux-2-klein-4b") == "@cf/black-forest-labs/flux-2-klein-4b" == av.IMAGE_MODEL
    assert av.resolve_model("phoenix-1.0") == "@cf/leonardo/phoenix-1.0"
    assert av.resolve_model("@cf/x/y") == "@cf/x/y" and av.model_info("@cf/x/y") is av.UNKNOWN_MODEL
    assert av.model_info("@cf/leonardo/lucid-origin").steps_key == "num_steps"
    assert not av.model_info(av.SQUARE_MODEL).sized and av.model_info(av.IMAGE_MODEL).form


# ---------- prompts: style changes and characters ----------

def test_a_new_style_rewrites_prompts_and_learned_characters_but_keeps_declared(api, tmp_path):
    prompts = tmp_path / "p.json"
    prompts.write_text(json.dumps({"style": "3D animated film style", "scenes": [
        {"narration": "One.", "prompt": "a cartoon pot"}], "characters": {"Ama": "cartoon girl", "Kofi": "old man"}}))
    sc = scenes("One.")
    run(sc, prompts, style="photorealistic", characters={"Kofi": "old man, white beard"})
    assert len(api.text_calls()) == 1  # written again for the new style
    saved = json.loads(prompts.read_text(encoding="utf-8"))
    assert saved["style"] == "photorealistic" and saved["characters"] == {"Kofi": "old man, white beard"}
    api.calls.clear()
    run(scenes("One."), prompts, style="photorealistic")
    assert api.text_calls() == []  # same style: the saved prompt is reused


def test_only_characters_named_in_the_scene_reach_the_prompt_writer(api, tmp_path):
    # Script2 scene 14: given the whole sheet, the 8B writer drew the channel's boy instead of Sonam Wangchuk
    sheet = {"Smart Village Boy": "twelve years old, red cap", "Sonam Wangchuk": "Ladakhi engineer in his late fifties"}
    sc = [{"text": "Engineer Sonam Wangchuk piped meltwater downhill.", "visual": None, "src": None,
           "description": "Sonam Wangchuk opening a valve on a pipe"}]
    run(sc, tmp_path / "p.json", characters=sheet, prompts_only=True)
    request = api.text_calls()[0]["messages"][1]["content"]
    assert "Character sheet: Sonam Wangchuk = Ladakhi engineer" in request and "red cap" not in request


def test_names_become_roles_in_image_prompts():
    sheet = {"Sonam Wangchuk": "Ladakhi engineer in his late fifties, short greying hair",
             "Smart Village Boy": "twelve years old, red cap", "Mohammed Bah Abba": "a Nigerian teacher in his fifties",
             "Khasi villagers": "local villagers, traditional Khasi clothing"}
    assert av.unname("Sonam Wangchuk opens a valve, sharp focus on Sonam and the ice", sheet) == \
        "a Ladakhi engineer in his late fifties opens a valve, sharp focus on the Ladakhi engineer in his late fifties and the ice"
    assert av.unname("Smart Village Boy waves", sheet) == "a village boy waves"
    assert av.unname("Mohammed Bah Abba, a Nigerian teacher in his fifties, kneels", sheet) == \
        "a Nigerian teacher in his fifties, kneels"
    assert av.unname("Khasi villagers guide roots", sheet) == "Khasi villagers guide roots"  # a group, not a name
    prompt = av.enforce_characters("Sonam Wangchuk at night", "Engineer Sonam Wangchuk piped meltwater.", sheet)
    assert "Sonam" not in prompt and prompt.endswith("with Ladakhi engineer in his late fifties, short greying hair")


def test_roles_and_group_words_are_not_learned_as_characters():
    sheet = {}
    av.learn(sheet, {"Alfredo Moser": "Brazilian mechanic", "Villagers": "mid-40s", "Farmer 1": "mid-40s"},
             "In Brazil, a mechanic named Alfredo Moser. So villagers stretch huge nets.")
    assert sheet == {"Alfredo Moser": "Brazilian mechanic"}
    assert not av.mentioned("Khasi villagers", "Yet villagers around the world")  # "villagers" alone is generic


# ---------- --redo-scenes and --check-images ----------

class FakeVision(FakeAPI):
    """FakeAPI plus a vision model that answers from a list of verdicts (dicts) or raw strings."""

    def __init__(self, verdicts=()):
        super().__init__()
        self.verdicts = list(verdicts)

    def __call__(self, model, payload, creds, timeout=120):
        if model == av.VISION_MODEL:
            self.calls.append((model, payload))
            v = self.verdicts.pop(0) if self.verdicts else {"ok": True, "problems": [], "score": 9}
            return {"response": v if isinstance(v, str) else json.dumps(v),
                    "usage": {"prompt_tokens": 900, "completion_tokens": 40}}
        return super().__call__(model, payload, creds, timeout)

    def checks(self):
        return [p for m, p in self.calls if m == av.VISION_MODEL]

    def image_calls(self):
        return [p for m, p in self.calls if m not in (av.TEXT_MODEL, av.VISION_MODEL)]


@pytest.fixture
def vision(monkeypatch, tmp_path):
    fake = FakeVision()
    monkeypatch.setattr(av, "cf_run", fake)
    monkeypatch.setattr(av, "GEN_DIR", tmp_path / "generated")
    monkeypatch.setattr(av.time, "sleep", lambda s: None)
    return fake


BAD = {"ok": False, "problems": ["six fingers on the left hand"], "score": 3}
WORSE = {"ok": False, "problems": ["extra arm", "text on shirt"], "score": 1}


def test_redo_scenes_redraws_only_those_with_a_new_seed(api, tmp_path):
    prompts = tmp_path / "p.json"
    first = scenes("One.", "Two.", "Three.")
    run(first, prompts)
    api.calls.clear()
    again = scenes("One.", "Two.", "Three.")
    run(again, prompts, redo={1})
    assert api.text_calls() == [] and len(api.image_calls()) == 1  # prompts and the other scenes from the cache
    assert "seed" in api.image_calls()[0]
    assert again[1]["src"] == first[1]["src"]  # the new picture replaces the cached one


def test_redrawing_an_image_drops_its_old_parallax_mask_and_verdict(tmp_path, monkeypatch):
    monkeypatch.setattr(av, "GEN_DIR", tmp_path)
    img = av.save_image(base64.b64decode(jpeg_b64()), tmp_path / "abc.jpg")
    (tmp_path / "abc.v1_fg.png").write_bytes(b"mask")
    av.save_verdict(img, {"ok": True, "problems": [], "score": 9})
    (tmp_path / "abcd.jpg").write_bytes(b"other image")
    av.save_image(base64.b64decode(jpeg_b64()), img)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["abc.jpg", "abcd.jpg"]


def test_check_passes_good_images_and_counts_neurons(vision, tmp_path):
    p = av.Providers("final", creds=CREDS)
    sc = scenes("A farmer at a stone channel.")
    av.generate_scene_visuals(sc, tmp_path / "p.json", providers=p, check=True, **ONE)
    [check] = vision.checks()
    assert check["messages"][0]["content"] == av.QC_SYSTEM
    part = check["messages"][1]["content"][1]
    assert part["type"] == "image_url" and part["image_url"]["url"].startswith("data:image/jpeg;base64,")
    rate_in, rate_out = av.TEXT_NEURONS[av.VISION_MODEL]
    assert p.qc_neurons == pytest.approx((900 * rate_in + 40 * rate_out) / 1e6)
    assert len(vision.image_calls()) == 1 and "quality_flags" not in sc[0]
    assert av.load_verdict(sc[0]["src"])["ok"]


def test_failed_image_is_redrawn_until_it_passes(vision, tmp_path):
    vision.verdicts = [BAD, {"ok": True, "problems": [], "score": 8}]
    sc = scenes("A boy waves.")
    av.generate_scene_visuals(sc, tmp_path / "p.json", providers=av.Providers("final", creds=CREDS), check=True,
                              **ONE)
    assert len(vision.image_calls()) == 2 and len(vision.checks()) == 2
    assert "seed" in vision.image_calls()[1]  # the redraw uses a new seed
    assert "quality_flags" not in sc[0] and av.load_verdict(sc[0]["src"])["score"] == 8
    assert not list(av.GEN_DIR.glob("_try_*"))  # the tries are cleaned up


def test_still_failing_keeps_the_best_try_and_flags_it(vision, tmp_path, capsys, monkeypatch):
    drawn = iter([(10, 10, 10), (20, 20, 20), (30, 30, 30)])

    def colour_jpeg():
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), next(drawn)).save(buf, "JPEG")
        return base64.b64encode(buf.getvalue()).decode()
    monkeypatch.setattr(sys.modules[__name__], "jpeg_b64", colour_jpeg)
    vision.verdicts = [WORSE, BAD, WORSE]  # the second try is the best
    sc = scenes("A boy waves.")
    av.generate_scene_visuals(sc, tmp_path / "p.json", providers=av.Providers("final", creds=CREDS), check=True,
                              **ONE)
    assert len(vision.image_calls()) == 1 + av.QC_RETRIES
    with Image.open(sc[0]["src"]) as img:
        assert abs(img.getpixel((4, 4))[0] - 20) < 6  # the best try was put back in the cache
    assert av.load_verdict(sc[0]["src"]) == BAD
    assert sc[0]["quality_flags"] == ["horizontal: six fingers on the left hand"]
    out = capsys.readouterr().out
    assert "FLAGGED scene 1 horizontal (six fingers on the left hand)" in out and "~" in out
    saved = json.loads((tmp_path / "p.json").read_text(encoding="utf-8"))["scenes"][0]
    assert saved["quality"]["horizontal"] == BAD


def test_cached_checked_images_are_not_checked_again(vision, tmp_path):
    prompts = tmp_path / "p.json"
    run(scenes("One."), prompts, check=True)
    vision.calls.clear()
    run(scenes("One."), prompts, check=True)
    assert vision.calls == []


def test_unreadable_verdict_keeps_the_image_unchecked(vision, tmp_path):
    vision.verdicts = ["I cannot help with that."]
    sc = scenes("One.")
    run(sc, tmp_path / "p.json", check=True)
    assert len(vision.image_calls()) == 1 and "quality_flags" not in sc[0]
    assert av.load_verdict(sc[0]["src"]) is None  # tried again next run


def test_no_check_without_the_option(vision, tmp_path):
    run(scenes("One."), tmp_path / "p.json")
    assert vision.checks() == []


def test_parse_verdict():
    assert av.parse_verdict('Sure! {"ok": true, "problems": [], "score": 9}') == {"ok": True, "problems": [], "score": 9}
    # listing a defect means it didn't pass, whatever "ok" says
    assert av.parse_verdict('{"ok": true, "problems": ["extra finger"], "score": 7}')["ok"] is False
    assert av.parse_verdict('{"ok": false, "problems": "text on sign", "score": "4"}') == \
        {"ok": False, "problems": ["text on sign"], "score": 4}
    assert av.parse_verdict('"ok": false, broken json') == {"ok": False, "problems": [], "score": 0}
    assert av.parse_verdict("no idea") is None and av.parse_verdict(None) is None
    assert av.better({"ok": True, "problems": [], "score": 5}, BAD) and av.better(BAD, WORSE)


def test_prompt_writer_is_asked_for_simple_hand_poses():
    assert "simple, clear pose" in av.PROMPT_SYSTEM and "waving with spread fingers" in av.PROMPT_SYSTEM


def test_redo_and_check_options():
    a = mv.parse_args(["s.txt", "--redo-scenes", "3, 7"])
    assert a.redo == {2, 6} and a.generate_visuals and a.check_images  # checks are on by default
    assert mv.parse_args(["s.txt", "--check-images"]).vision_model == av.VISION_MODEL
    with pytest.raises(SystemExit):
        mv.parse_args(["s.txt", "--redo-scenes", "three"])


def test_repeated_narration_keeps_a_prompt_and_edit_per_scene(api, tmp_path):
    prompts = tmp_path / "p.json"
    # a file from before scene ids: the same line twice, the second with the user's edit
    prompts.write_text(json.dumps({"scenes": [
        {"scene": 1, "narration": "Water is life.", "prompt": "a well at dawn"},
        {"scene": 2, "narration": "Fog over hills.", "prompt": "misty hills"},
        {"scene": 3, "narration": "Water is life.", "prompt": "MY EDIT: a river at dusk"},
    ]}), encoding="utf-8")
    sc = scenes("Water is life.", "Fog over hills.", "Water is life.")
    run(sc, prompts)
    assert api.text_calls() == []  # every saved prompt was found, the repeated line's twice
    assert [c["prompt"].split(",")[0] for c in api.image_calls()] == [
        "a well at dawn", "misty hills", "MY EDIT: a river at dusk"]
    saved = json.loads(prompts.read_text(encoding="utf-8"))["scenes"]
    assert [s["prompt"] for s in saved] == ["a well at dawn", "misty hills", "MY EDIT: a river at dusk"]
    assert saved[0]["id"] != saved[2]["id"] and all(s["id"] for s in saved)  # migrated: ids written
    # reordering keeps each scene's own prompt (the repeated line keeps its order among its copies)
    reordered = scenes("Fog over hills.", "Water is life.", "Water is life.")
    av.assign_ids(reordered)
    records = av.load_prompt_records(prompts)
    assert [av.saved_prompt(records, s) for s in reordered] == ["misty hills", "a well at dawn", "MY EDIT: a river at dusk"]


def test_migration_matches_by_scene_number_when_an_earlier_copy_has_its_own_visual(api, tmp_path):
    prompts = tmp_path / "p.json"
    # before scene ids: scene 1 used its own picture (no prompt record), scene 2 repeats its line with the user's edit
    prompts.write_text(json.dumps({"scenes": [
        {"scene": 2, "narration": "Water is life.", "description": "", "prompt": "MY EDIT: a river at dusk"},
    ]}), encoding="utf-8")
    sc = scenes("Water is life.", "Water is life.")
    sc[0]["visual"] = "own.png"
    run(sc, prompts)
    assert api.text_calls() == []  # the edit was found for scene 2, not given to scene 1 (counting copies in the file would)
    assert api.image_calls()[0]["prompt"].startswith("MY EDIT: a river at dusk")
    saved = json.loads(prompts.read_text(encoding="utf-8"))
    assert [(r["scene"], r["id"], r["prompt"]) for r in saved["scenes"]] == [
        (2, sc[1]["id"], "MY EDIT: a river at dusk")]
    assert sc[1]["id"] != sc[0]["id"] and "unmatched" not in saved


def test_records_that_cant_be_matched_are_kept_with_a_warning(api, tmp_path, capsys):
    prompts = tmp_path / "p.json"
    prompts.write_text(json.dumps({"scenes": [
        {"scene": 5, "narration": "Water is life.", "description": "a well", "prompt": "edited well"},  # 2 copies fit
        {"scene": 9, "narration": "A line the script no longer has.", "prompt": "edited old line"},
    ]}), encoding="utf-8")
    sc = scenes("Water is life.", "Water is life.")
    for s in sc:
        s["description"] = "a well"
    run(sc, prompts)
    assert "couldn't be matched to a scene with confidence" in capsys.readouterr().out
    saved = json.loads(prompts.read_text(encoding="utf-8"))
    assert [r["prompt"] for r in saved["unmatched"]] == ["edited well", "edited old line"]  # never dropped
    run(sc, prompts)  # a later run keeps them, without warning again
    assert "with confidence" not in capsys.readouterr().out
    assert len(json.loads(prompts.read_text(encoding="utf-8"))["unmatched"]) == 2
