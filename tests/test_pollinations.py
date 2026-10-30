"""Cloudflare makes every final prompt and image; Pollinations only drafts, stands in as the emergency backup and
brainstorms. The daily spending cap (Nairobi day), the usage record and --check's cost and cap lines. No network:
Cloudflare and Pollinations are faked."""
import base64
import io
import json
from datetime import datetime, timezone

import pytest

import ai_visuals as av
import make_video as mv
import script_check
from test_ai_visuals import CREDS, jpeg_b64, scenes

DETAILED = ("Medium shot, 35mm lens, of a teacher in a white kaftan kneeling in a shaded schoolyard in northern "
            "Nigeria, lowering a small clay pot into a larger one, soft afternoon light, dusty ground, realistic.")
OUTAGE = av.ProviderError("HTTP 503: service unavailable", retryable=True, status=503)


@pytest.fixture
def cloud(monkeypatch, tmp_path):
    """A Cloudflare that answers (or fails, with calls["down"]) and a Pollinations that answers text and images."""
    calls = {"cf_text": 0, "cf_image": 0, "poll_text": [], "poll_image": 0, "down": False}

    def cf_run(model, payload, creds, timeout=120):
        kind = "cf_image" if "prompt" in payload else "cf_text"
        calls[kind] += 1
        if calls["down"]:
            raise OUTAGE
        return {"image": jpeg_b64()} if kind == "cf_image" else {"response": f"Cloudflare prompt {calls[kind]}"}

    def poll_text(messages, key, max_tokens=220, temperature=0.7, timeout=90):
        calls["poll_text"].append(messages)
        return DETAILED

    def poll_image(prompt, key, seed, timeout=180, size="square"):
        calls["poll_image"] += 1
        return base64.b64decode(jpeg_b64())
    monkeypatch.setattr(av, "cf_run", cf_run)
    monkeypatch.setattr(av, "pollinations_text_run", poll_text)
    monkeypatch.setattr(av, "pollinations_run", poll_image)
    monkeypatch.setattr(av, "GEN_DIR", tmp_path / "generated")
    monkeypatch.setattr(av.time, "sleep", lambda s: None)
    return calls


def final(**kw):
    return av.Providers("final", creds=CREDS, poll_key="sk_test", **kw)


# ---------- final render: Cloudflare, with Pollinations as the emergency backup ----------

def test_a_final_render_is_all_cloudflare_past_the_free_allowance(cloud, tmp_path):
    av.record_usage(12_000)  # the free 10,000 are used up: Workers Paid keeps going
    p = final()
    sc = scenes("A teacher sets one clay pot inside another.", "Water seeps through the clay.")
    av.generate_scene_visuals(sc, tmp_path / "p.json", providers=p, formats=("horizontal",))
    assert (cloud["cf_text"], cloud["cf_image"], cloud["poll_image"], cloud["poll_text"]) == (2, 2, 0, [])
    assert p.backup == {}


def test_prompts_from_the_backup_are_stand_ins_and_listed(cloud, tmp_path):
    cloud["down"] = True
    prompts = tmp_path / "p.json"
    p = final()
    av.generate_scene_visuals(scenes("A teacher sets one clay pot inside another."), prompts, providers=p,
                              formats=("horizontal",))
    rec = json.loads(prompts.read_text(encoding="utf-8"))["scenes"][0]
    assert rec["prompt"] == DETAILED and rec["stand_in"]  # Cloudflare writes it again on the next final render
    assert rec["image_provider"] == "pollinations" and cloud["poll_image"] == 1
    assert (cloud["cf_text"], cloud["cf_image"]) == (2, 2)  # one retry each, then the backup
    assert p.backup == {1: "Pollinations backup image"}
    assert cloud["poll_text"][0][0]["content"] == av.PROMPT_SYSTEM  # the same instructions Cloudflare gets


def test_without_a_pollinations_key_prompts_are_simple_stand_ins(cloud):
    cloud["down"] = True
    p = av.Providers("final", creds=CREDS, poll_key="")
    assert p.text([{"role": "user", "content": "hi"}]) is None and p.last_writer is None
    assert cloud["poll_text"] == []


def test_the_quality_check_never_goes_to_pollinations(cloud):
    cloud["down"] = True
    p = final()
    assert p.text([{"role": "user", "content": "check"}], model=p.vision_model) is None
    assert cloud["poll_text"] == []
    assert p.text([{"role": "user", "content": "write"}]) == DETAILED and p.last_writer == "pollinations"


def test_the_backup_lines_and_redo_command(monkeypatch, capsys):
    p = type("P", (), {"backup": {3: "Pollinations backup image", 7: "prompt not written by Cloudflare"}})()
    argv = ["Scripts\\My Script.txt", "--redo-scenes", "1", "--format", "horizontal"]
    assert mv.redo_command([3, 7, 9], argv) == ('.venv\\Scripts\\python make_video.py "Scripts\\My Script.txt" '
                                                '--format horizontal --redo-scenes "3,7,9"')
    monkeypatch.setattr(mv.sys, "argv", ["make_video.py", "Scripts\\Script2.txt"])
    mv.backup_report(p, [9])
    out = capsys.readouterr().out
    assert "WARNING: 3 scene(s) aren't Cloudflare's work" in out
    assert "scene 3: Pollinations backup image" in out and "scene 9: a title card" in out
    assert 'make_video.py Scripts\\Script2.txt --redo-scenes "3,7,9"' in out
    mv.backup_report(type("P", (), {"backup": {}})(), [])
    assert capsys.readouterr().out == ""  # all Cloudflare: nothing to say


# ---------- the daily cap ----------

def test_the_cap_stops_new_cloudflare_images_and_warns(cloud, tmp_path, capsys):
    av.record_usage(60_000 - 200)  # room for one 132-neuron image, not two
    p = final()
    sc = scenes("One.", "Two.", "Three.")
    av.generate_scene_visuals(sc, tmp_path / "p.json", providers=p, formats=("horizontal",))
    out = capsys.readouterr().out
    assert "WARNING: the daily Cloudflare cap is reached" in out and "max-daily-neurons = 60,000" in out
    assert cloud["cf_image"] == 1 and cloud["poll_image"] == 2 and p.capped and not p.cf_ok
    assert set(p.backup) == {2, 3} and av.spent_today() <= 60_000
    assert all(s["src"].exists() for s in sc)


def test_no_cap_with_zero(cloud):
    av.record_usage(1_000_000)
    p = final(cap=0)
    assert p.within_cap(500) and p.image("a pot")[1] == "cloudflare"


def test_usage_counts_the_free_day_in_utc_and_the_cap_day_in_nairobi(monkeypatch):
    class LateEvening(datetime):  # 22:30 UTC is already the next day in Nairobi (UTC+3)
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 30, 22, 30, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(av, "datetime", LateEvening)
    assert (av.utc_day(), av.nairobi_day()) == ("2026-10-30", "2026-10-31")
    av.usage_path().write_text(json.dumps({"free_day": "2026-10-30", "free_used": 9_000,
                                           "cap_day": "2026-10-30", "spent": 40_000}), encoding="utf-8")
    assert av.free_left() == 1_000  # same UTC day: the free allowance is still counting
    assert av.spent_today() == 0  # a new Nairobi day: the cap starts again
    av.record_usage(500)
    assert av.usage_today() | {} == {"free_day": "2026-10-30", "free_used": 9_500, "cap_day": "2026-10-31", "spent": 500}


def test_dollars_beyond_the_free_allowance():
    assert av.dollars(10_000, free=10_000) == 0
    assert av.dollars(60_000, free=10_000) == pytest.approx(0.55)  # the default cap, from a fresh day
    av.record_usage(15_000)
    assert av.free_left() == 0 and av.dollars(1_000) == pytest.approx(0.011)


def test_an_old_usage_file_is_ignored():
    av.usage_path().write_text(json.dumps({"day": "2026-01-01", "neurons": 9999, "refused": True}), encoding="utf-8")
    assert av.free_left() == av.FREE_NEURONS_PER_DAY and av.spent_today() == 0


# ---------- drafts ----------

def test_a_draft_draws_with_pollinations_into_its_own_cache(cloud, tmp_path):
    prompts = tmp_path / "p.json"
    p = av.Providers("draft", creds=CREDS, poll_key="sk_test")
    sc = scenes("A teacher sets one clay pot inside another.")
    av.generate_scene_visuals(sc, prompts, providers=p, formats=("horizontal",))
    assert cloud["cf_image"] == 0 and cloud["poll_image"] == 1
    assert sc[0]["src"].parent == av.GEN_DIR / "drafts" and sc[0]["srcs"]["horizontal"] == sc[0]["src"]
    rec = json.loads(prompts.read_text(encoding="utf-8"))["scenes"][0]
    assert rec["prompt"] == "Cloudflare prompt 1" and not rec.get("stand_in")  # the final render reuses it
    assert p.backup == {}  # a draft lists nothing to redo
    av.generate_scene_visuals(sc, prompts, providers=final(), formats=("horizontal",))
    assert cloud["cf_image"] == 1 and sc[0]["src"].parent == av.GEN_DIR  # the final render never takes the draft


def test_draft_options(monkeypatch):
    monkeypatch.setattr(mv, "CONFIG_FILE", mv.ROOT / "make_video.toml")
    a = mv.parse_args(["s.txt"])
    assert not a.draft and not a.draft_images and a.max_daily_neurons == 60_000
    assert mv.parse_args(["s.txt", "--draft"]).draft
    assert mv.parse_args(["s.txt", "--preview-scenes", "2"]).draft  # a preview is always a draft
    assert not mv.parse_args(["s.txt", "--no-draft"]).draft
    with pytest.raises(SystemExit):
        mv.parse_args(["s.txt", "--image-provider", "pollinations"])  # gone


# ---------- --check ----------

def check(tmp_path, monkeypatch, *extra, key="sk_test"):
    monkeypatch.setattr(av, "pollinations_key", lambda: key)
    p = tmp_path / "s.txt"
    p.write_text("\n\n".join(f"# Visuals: scene {n}\nFarmers dig small pits and fill them with compost. Rain soaks "
                             f"in number {n}." for n in range(1, 7)), encoding="utf-8")
    args = mv.parse_args([str(p), "-o", str(tmp_path / "o"), "--no-two-shots", "--no-metadata", "--no-check-images",
                          *extra])
    return script_check.analyse(p, args)


def test_check_says_which_scenes_would_go_past_the_cap(tmp_path, monkeypatch):
    av.record_usage(59_000)
    cp = check(tmp_path, monkeypatch)["estimates"]["cap"]
    assert cp["to"] == "the Pollinations backup" and cp["scenes"] == [4, 5, 6] and not cp["fits"]  # ~310 a scene
    assert check(tmp_path, monkeypatch, key=None)["estimates"]["cap"]["to"].startswith("title cards")
    assert check(tmp_path, monkeypatch, "--max-daily-neurons", "0")["estimates"]["cap"]["fits"]


def test_check_estimates_the_cost_beyond_the_free_neurons(tmp_path, monkeypatch):
    est = check(tmp_path, monkeypatch)["estimates"]
    cp = est["cap"]
    assert cp["fits"] and cp["scenes"] == [] and cp["free_left"] == av.FREE_NEURONS_PER_DAY and cp["dollars"] == 0
    av.record_usage(10_000)
    cp = check(tmp_path, monkeypatch)["estimates"]["cap"]
    assert cp["billable"] == pytest.approx(est["neurons_total"])
    assert cp["dollars"] == pytest.approx(est["neurons_total"] * av.NEURON_PRICE)


def test_check_counts_no_image_neurons_for_a_draft(tmp_path, monkeypatch):
    est = check(tmp_path, monkeypatch, "--draft")["estimates"]
    assert est["draft"] and est["neurons_images"] == 0 and est["images_new"] == 6  # one format
    assert est["neurons_checks"] == 0 and est["upscale_todo"] == 0 and est["neurons_prompts"] > 0


def test_check_prints_the_cost_and_cap_lines(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(av, "pollinations_key", lambda: "sk_test")
    av.record_usage(59_990)
    p = tmp_path / "s.txt"
    p.write_text("# Visuals: a road\nOne scene here.\n", encoding="utf-8")
    script_check.report(p, mv.parse_args([str(p), "-o", str(tmp_path / "o")]))
    out = capsys.readouterr().out
    assert "cost: ~" in out and "beyond the 0 free ones left today" in out and "$0.011 per 1,000" in out
    assert "of max-daily-neurons = 60,000 (Nairobi day): DOES NOT FIT" in out
    assert "would use the Pollinations backup: 1" in out


def test_scene_list_joins_runs():
    assert script_check.scene_list([3, 4, 5, 9, 11, 12]) == "3-5, 9, 11-12"


# ---------- Pollinations' text model ----------

class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_pollinations_text_request_keeps_the_key_in_the_header(monkeypatch):
    seen = {}

    def urlopen(req, timeout=None):
        seen.update(url=req.full_url, headers=dict(req.header_items()), body=json.loads(req.data))
        return Reply(json.dumps({"choices": [{"message": {"content": " A wide shot. "}}]}).encode())
    monkeypatch.setattr(av.urllib.request, "urlopen", urlopen)
    assert av.pollinations_text_run([{"role": "user", "content": "x"}], "sk_secret", 120, 0.5) == " A wide shot. "
    assert seen["url"] == av.POLLINATIONS_TEXT_API and "sk_secret" not in seen["url"]
    assert seen["headers"]["Authorization"] == "Bearer sk_secret"
    assert seen["body"]["max_tokens"] == 120 and "model" not in seen["body"]  # the service's default model


def test_a_key_without_text_access_is_never_used_as_a_prompt(monkeypatch, capsys):
    refusal = ("The API key used for this request isn't allowed to use openai/gpt-5.4-nano. Please [allow it for this "
               "key](<https://enter.pollinations.ai/grant?id=x&category=text>), then try again.")
    monkeypatch.setattr(av.urllib.request, "urlopen", lambda req, timeout=None: Reply(
        json.dumps({"choices": [{"message": {"content": refusal}}]}).encode()))
    with pytest.raises(av.ProviderError) as e:
        av.pollinations_text_run([{"role": "user", "content": "x"}], "sk_test")
    assert e.value.auth
    p = av.Providers("draft", creds=CREDS, poll_key="sk_test")
    p.cf_ok = False  # Cloudflare can't write the prompt
    assert p.text([{"role": "user", "content": "x"}]) is None and not p.poll_text and p.poll_key  # images still allowed
    assert "allow text for the key" in capsys.readouterr().out
