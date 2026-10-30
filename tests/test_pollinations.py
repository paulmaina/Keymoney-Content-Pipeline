"""Cloudflare first, Pollinations as the automatic fallback for images and, now, for writing prompts; the daily usage
record and --check's fallback estimate. No network: Cloudflare and Pollinations are faked."""
import io
import json
from types import SimpleNamespace

import pytest

import ai_visuals as av
import make_video as mv
import script_check
from test_ai_visuals import CREDS, jpeg_b64, scenes

DETAILED = ("Medium shot, 35mm lens, of a teacher in a white kaftan kneeling in a shaded schoolyard in northern "
            "Nigeria, lowering a small clay pot into a larger one, soft afternoon light, dusty ground, realistic.")
ALLOWANCE = av.ProviderError("HTTP 429: you have used up your daily free allocation of 10,000 neurons", quota=True,
                             status=429)


@pytest.fixture
def cloud(monkeypatch, tmp_path):
    """Cloudflare that is out of allowance, and a Pollinations that answers text and images."""
    calls = {"cf": 0, "poll_text": [], "poll_image": 0}

    def cf_run(model, payload, creds, timeout=120):
        calls["cf"] += 1
        raise ALLOWANCE

    def poll_text(messages, key, max_tokens=220, temperature=0.7, timeout=90):
        calls["poll_text"].append(messages)
        return DETAILED

    def poll_image(prompt, key, seed, timeout=180, size="square"):
        calls["poll_image"] += 1
        import base64
        return base64.b64decode(jpeg_b64())
    monkeypatch.setattr(av, "cf_run", cf_run)
    monkeypatch.setattr(av, "pollinations_text_run", poll_text)
    monkeypatch.setattr(av, "pollinations_run", poll_image)
    monkeypatch.setattr(av, "GEN_DIR", tmp_path / "generated")
    monkeypatch.setattr(av.time, "sleep", lambda s: None)
    return calls


def test_prompts_are_written_by_pollinations_when_cloudflare_is_out(cloud, tmp_path):
    prompts = tmp_path / "p.json"
    sc = scenes("A teacher sets one clay pot inside another.")
    av.generate_scene_visuals(sc, prompts, creds=CREDS, formats=("horizontal",),
                              providers=av.Providers("auto", creds=CREDS, poll_key="sk_test"))
    rec = json.loads(prompts.read_text(encoding="utf-8"))["scenes"][0]
    assert rec["prompt"] == DETAILED and not rec.get("stand_in")  # a real prompt, kept for next time
    assert rec["image_provider"] == "pollinations" and cloud["poll_image"] == 1
    assert cloud["cf"] == 1  # Cloudflare is asked once, then left alone for the rest of the run
    system = cloud["poll_text"][0][0]["content"]
    assert system == av.PROMPT_SYSTEM  # the same detailed instructions as Cloudflare's prompt writer gets


def test_without_a_pollinations_key_prompts_are_stand_ins(cloud, tmp_path):
    p = av.Providers("cloudflare", creds=CREDS, poll_key="")
    assert p.text([{"role": "user", "content": "hi"}]) is None and not p.text_ok
    assert cloud["poll_text"] == []


def test_the_quality_check_never_goes_to_pollinations(cloud):
    p = av.Providers("auto", creds=CREDS, poll_key="sk_test")
    assert p.text([{"role": "user", "content": "check"}], model=p.vision_model) is None
    assert cloud["poll_text"] == []
    assert p.text([{"role": "user", "content": "write"}]) == DETAILED  # but text jobs do


def test_pollinations_text_request_keeps_the_key_in_the_header(monkeypatch):
    seen = {}

    class Reply(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout=None):
        seen.update(url=req.full_url, headers=dict(req.header_items()), body=json.loads(req.data))
        return Reply(json.dumps({"choices": [{"message": {"content": " A wide shot. "}}]}).encode())
    monkeypatch.setattr(av.urllib.request, "urlopen", urlopen)
    assert av.pollinations_text_run([{"role": "user", "content": "x"}], "sk_secret", 120, 0.5) == " A wide shot. "
    assert seen["url"] == av.POLLINATIONS_TEXT_API and "sk_secret" not in seen["url"]
    assert seen["headers"]["Authorization"] == "Bearer sk_secret"
    assert seen["body"]["max_tokens"] == 120 and "model" not in seen["body"]  # the service's default model


def test_usage_record_counts_today_and_knows_when_cloudflare_refused(cloud):
    av.record_usage(2500)
    assert av.neurons_left() == 7500
    p = av.Providers("auto", creds=CREDS, poll_key="sk_test")
    p.text([{"role": "user", "content": "x"}])  # hits the daily allowance
    assert av.usage_today()["refused"] and av.neurons_left() == 0


def test_a_brief_rate_limit_is_not_the_daily_allowance(monkeypatch):
    p = av.Providers("auto", creds=CREDS, poll_key="sk_test")
    p._disable_cloudflare(av.ProviderError("HTTP 429: too many requests", status=429))
    assert not av.usage_today()["refused"]


def test_yesterdays_usage_does_not_count(monkeypatch):
    av.usage_path().write_text(json.dumps({"day": "2026-01-01", "neurons": 9999, "refused": True}), encoding="utf-8")
    assert av.neurons_left() == av.FREE_NEURONS_PER_DAY


def check(tmp_path, monkeypatch, *extra, key="sk_test"):
    monkeypatch.setattr(av, "pollinations_key", lambda: key)
    p = tmp_path / "s.txt"
    p.write_text("\n\n".join(f"# Visuals: scene {n}\nFarmers dig small pits and fill them with compost. Rain soaks "
                             f"in number {n}." for n in range(1, 7)), encoding="utf-8")
    args = mv.parse_args([str(p), "-o", str(tmp_path / "o"), "--generate-visuals", *extra])
    return script_check.analyse(p, args)


def test_check_says_which_scenes_will_fall_back_to_pollinations(tmp_path, monkeypatch):
    monkeypatch.setattr(av, "neurons_left", lambda: 1000.0)
    fb = check(tmp_path, monkeypatch, "--no-two-shots")["estimates"]["fallback"]
    assert fb["to"] == "Pollinations" and fb["scenes"] == [4, 5, 6]  # ~310 neurons a scene: three fit in 1,000
    assert check(tmp_path, monkeypatch, "--no-two-shots", key=None)["estimates"]["fallback"]["to"].startswith("title cards")
    monkeypatch.setattr(av, "neurons_left", lambda: 10_000.0)
    assert check(tmp_path, monkeypatch, "--no-two-shots")["estimates"]["fallback"]["scenes"] == []


def test_check_prints_the_fallback_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(av, "neurons_left", lambda: 0.0)
    monkeypatch.setattr(av, "usage_today", lambda: {"day": "x", "neurons": 0, "refused": True})
    monkeypatch.setattr(av, "pollinations_key", lambda: "sk_test")
    p = tmp_path / "s.txt"
    p.write_text("# Visuals: a road\nOne scene here.\n", encoding="utf-8")
    script_check.report(p, mv.parse_args([str(p), "-o", str(tmp_path / "o"), "--generate-visuals"]))
    out = capsys.readouterr().out
    assert "Cloudflare refused earlier today" in out and "fall back to Pollinations: 1" in out


def test_image_provider_is_auto_in_the_project_defaults(monkeypatch):
    monkeypatch.setattr(mv, "CONFIG_FILE", mv.ROOT / "make_video.toml")
    assert mv.parse_args(["s.txt"]).image_provider == "auto"
    assert mv.parse_args(["s.txt", "--image-provider", "pollinations"]).image_provider == "pollinations"


def test_scene_list_joins_runs():
    assert script_check.scene_list([3, 4, 5, 9, 11, 12]) == "3-5, 9, 11-12"


def test_a_key_without_text_access_is_never_used_as_a_prompt(monkeypatch, capsys):
    refusal = ("The API key used for this request isn't allowed to use openai/gpt-5.4-nano. Please [allow it for this "
               "key](<https://enter.pollinations.ai/grant?id=x&category=text>), then try again.")

    class Reply(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(av.urllib.request, "urlopen", lambda req, timeout=None: Reply(
        json.dumps({"choices": [{"message": {"content": refusal}}]}).encode()))
    with pytest.raises(av.ProviderError) as e:
        av.pollinations_text_run([{"role": "user", "content": "x"}], "sk_test")
    assert e.value.auth
    p = av.Providers("pollinations", creds=None, poll_key="sk_test")
    p.cf_ok = False  # Cloudflare out (whatever credentials the environment holds)
    assert p.text([{"role": "user", "content": "x"}]) is None and not p.poll_text and p.poll_key  # images still allowed
    assert "allow text for the key" in capsys.readouterr().out
