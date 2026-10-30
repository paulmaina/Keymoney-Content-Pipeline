"""Tests for stock media (Pexels / Pixabay). Every request is faked: no network, no API keys."""
import json
from types import SimpleNamespace

import pytest

import ai_visuals as av
import make_video as mv
import script_check as sc
import stock
import youtube_meta as ym


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(stock, "STOCK_DIR", tmp_path / "stock")
    monkeypatch.setattr(av, "ENV_FILE", tmp_path / "no.env")
    for k in ("PEXELS_API_KEY", "PIXABAY_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def pixabay_video(id_, tags, w=1920, h=1080, duration=20):
    return {"id": id_, "pageURL": f"https://pixabay.com/videos/id-{id_}/", "tags": tags, "duration": duration,
            "user": "Ana", "user_id": 7, "videos": {
                "large": {"url": f"https://cdn.pixabay.com/{id_}_large.mp4", "width": w * 2, "height": h * 2},
                "medium": {"url": f"https://cdn.pixabay.com/{id_}_medium.mp4", "width": w, "height": h},
                "tiny": {"url": f"https://cdn.pixabay.com/{id_}_tiny.mp4", "width": w // 3, "height": h // 3}}}


def pixabay_photo(id_, tags, w=4000, h=3000):
    return {"id": id_, "pageURL": f"https://pixabay.com/photos/id-{id_}/", "tags": tags, "imageWidth": w,
            "imageHeight": h, "largeImageURL": f"https://cdn.pixabay.com/{id_}_1280.jpg", "user": "Ben", "user_id": 9}


def pexels_video(id_, slug, w=1080, h=1920, duration=20):
    return {"id": id_, "url": f"https://www.pexels.com/video/{slug}-{id_}/", "width": w, "height": h,
            "duration": duration, "user": {"name": "Cleo", "url": "https://www.pexels.com/@cleo"},
            "video_files": [{"quality": "hd", "width": w, "height": h, "link": f"https://videos.pexels.com/{id_}.mp4"},
                            {"quality": "uhd", "width": w * 2, "height": h * 2, "link": f"https://videos.pexels.com/{id_}_4k.mp4"}]}


class FakeWeb:
    """Answers search URLs from canned results per (provider, kind), records them, and can fail."""

    def __init__(self, results, fail=None, headers=None):
        self.results, self.fail, self.headers, self.urls = results, fail or {}, headers or {}, []

    def __call__(self, url, headers):
        self.urls.append((url, headers))
        provider = "pexels" if "pexels" in url else "pixabay"
        kind = "video" if "video" in url else "photo"
        if provider in self.fail:
            raise self.fail[provider]
        hits = self.results.get((provider, kind), [])
        body = {"hits": hits} if provider == "pixabay" else {("videos" if kind == "video" else "photos"): hits}
        return body, self.headers.get(provider, {})

    def calls(self, provider):
        return [u for u, _ in self.urls if provider in u]


def searcher(web, keys=("pixabay",), clock=lambda: 1_000.0):
    limits = stock.RateLimits(clock=clock, sleep=lambda s: None)
    return stock.Searcher({k: f"KEY-{k}" for k in keys}, limits=limits, get=web, clock=clock)


SCENE = {"text": "So villagers stretch huge mesh nets across the hills to catch the fog.", "visual": None,
         "description": "# Visuals: villagers stretching a black fog net on a misty hillside"}


def run(scenes, web, keys=("pixabay",), media="mixed", providers=None, declared=None, seconds=None):
    s = searcher(web, keys)
    plan = stock.fill_stock(scenes, ["horizontal", "vertical"], media, "auto", providers,
                            seconds or [8.0] * len(scenes), declared, searcher=s, get=lambda url: b"media " + url.encode())
    return plan, s


# ---------- keys and modes ----------

def test_keys_come_from_env_and_follow_the_stock_choice(monkeypatch):
    monkeypatch.setenv("PIXABAY_API_KEY", "abc")
    assert stock.keys() == {"pixabay": "abc"}
    monkeypatch.setenv("PEXELS_API_KEY", "xyz")
    assert list(stock.keys()) == ["pexels", "pixabay"] and stock.keys("pixabay") == {"pixabay": "abc"}


def test_no_keys_means_straight_to_ai_images(capsys):
    plan = stock.fill_stock([dict(SCENE)], ["horizontal"], "mixed", "auto", None, [5.0])
    assert "no PEXELS_API_KEY or PIXABAY_API_KEY" in capsys.readouterr().out and plan[0][0] == "mixed"


def test_scene_modes():
    declared = {"Smart Village Boy": "a boy"}
    assert stock.scene_mode({"text": "x", "visual": "a.png"}, "mixed", {})[0] == "own"
    assert stock.scene_mode({"text": "x", "visual": None, "media": "ai"}, "stock", {})[0] == "ai"
    assert stock.scene_mode({"text": "x", "visual": None, "media": "stock"}, "ai", {})[0] == "stock"
    boy = {"text": "Welcome to Smart Village Boy.", "visual": None}
    assert stock.scene_mode(boy, "mixed", declared)[0] == "ai"  # a recurring character stays AI
    assert stock.scene_mode(boy, "stock", declared)[0] == "stock"
    assert stock.scene_mode({"text": "x", "visual": None}, "ai", {})[0] == "ai"


def test_media_lines_and_options(tmp_path):
    p = tmp_path / "s.txt"
    p.write_text("# media: stock\nFog rolls in.\n\n# MEDIA: AI\nThe boy waves.\n\nNo line here.\n", encoding="utf-8")
    assert [s.get("media") for s in mv.parse_script(p)] == ["stock", "ai", None]
    assert mv.parse_comments(p) == []  # media lines aren't reported as skipped comments
    a = mv.parse_args(["s.txt"])
    assert (a.media, a.stock, a.generate_visuals) == ("mixed", "auto", True)  # the plain command: AI images and stock
    a = mv.parse_args(["s.txt", "--media", "stock", "--stock", "pixabay"])
    assert (a.media, a.stock, a.generate_visuals) == ("stock", "pixabay", True)


# ---------- searching ----------

def test_only_real_footage_and_photos_are_asked_for():
    url, _ = stock.request("pixabay", "video", "fog net", "K")
    assert "video_type=film" in url and "/videos/" in url
    url, _ = stock.request("pixabay", "photo", "fog net", "K")
    assert "image_type=photo" in url
    url, headers = stock.request("pexels", "video", "fog net", "K")
    assert url.startswith("https://api.pexels.com/videos/search") and headers == {"Authorization": "K"}
    assert len(stock.request("pixabay", "photo", "x" * 300, "K")[0]) < 300  # Pixabay's 100-character limit


def test_searches_are_cached_for_24_hours():
    web = FakeWeb({("pixabay", "video"): [pixabay_video(1, "fog, net")]})
    now = [1_000.0]
    s = searcher(web, clock=lambda: now[0])
    s.search("pixabay", "video", "fog net")
    s.search("pixabay", "video", "Fog Net")
    assert len(web.urls) == 1
    now[0] += stock.SEARCH_TTL + 1
    s.search("pixabay", "video", "fog net")
    assert len(web.urls) == 2


def test_rate_limit_headers_are_tracked_and_a_short_window_is_waited_out():
    now, slept = [1_000.0], []
    limits = stock.RateLimits(clock=lambda: now[0], sleep=slept.append)
    limits.update("pixabay", {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "30"})
    assert limits.state["pixabay"] == {"remaining": 0, "reset_at": 1_030.0}
    assert limits.wait_or_block("pixabay") and slept == [31.0]
    limits.update("pexels", {"X-Ratelimit-Remaining": "0", "X-Ratelimit-Reset": "1900000000"})  # Pexels: UNIX time
    assert not limits.wait_or_block("pexels")  # a long block: use the other provider instead
    assert json.loads(limits.path.read_text())["pexels"]["remaining"] == 0  # remembered for the next run


def test_a_failing_or_limited_provider_is_skipped_and_the_other_used():
    web = FakeWeb({("pixabay", "video"): [pixabay_video(1, "fog, net, hillside, villagers, mist")]},
                  fail={"pexels": stock.StockError("HTTP 429", rate_limited=True)})
    sc_ = [dict(SCENE)]
    plan, s = run(sc_, web, keys=("pexels", "pixabay"), media="stock")
    assert sc_[0]["stock"]["horizontal"]["provider"] == "pixabay"
    assert len(web.calls("pexels")) == 1 and "pexels" in s.down  # tried once, then off for the run
    assert s.limits.state["pexels"]["remaining"] == 0


def test_parsers_read_both_providers():
    v = stock.pexels_video(pexels_video(5, "woman-carrying-clay-pots"))
    assert (v.text, v.width, v.height, v.creator) == ("woman carrying clay pots", 1080, 1920, "Cleo")
    p = stock.pixabay_photo(pixabay_photo(3, "fog, hill"))
    assert (p.width, p.height) == (1280, 960)  # largeImageURL is capped at 1280 px
    assert p.creator_url == "https://pixabay.com/users/Ben-9/"


# ---------- scoring and choosing ----------

def cand(provider="pixabay", kind="video", w=1920, h=1080, duration=20, relevance=8, id_="1"):
    return stock.Candidate(provider, kind, id_, "u", "c", "cu", "fog net", w, h, duration, [(w, h, "url")],
                           relevance=relevance)


def test_bonuses_for_video_resolution_orientation_and_length():
    wide, tall = cand(w=1920, h=1080), cand(w=1080, h=1920, id_="2")
    assert stock.pick([wide, tall], "vertical", 10, 7) is tall and stock.pick([wide, tall], "horizontal", 10, 7) is wide
    short = cand(duration=3, id_="3")
    assert stock.pick([short, cand(duration=12, id_="4")], "horizontal", 10, 7).id == "4"
    photo = cand(kind="photo", relevance=9, id_="5")
    assert stock.pick([photo, cand(relevance=8, id_="6")], "horizontal", 5, 7).id == "6"  # video +1 beats +1 relevance
    assert stock.pick([cand(relevance=6)], "horizontal", 5, 7) is None  # below the mixed threshold


def test_pexels_wins_a_tie():
    assert stock.pick([cand("pixabay"), cand("pexels")], "horizontal", 10, 7).provider == "pexels"


def test_both_providers_are_scored_together_with_the_small_model():
    web = FakeWeb({("pixabay", "video"): [pixabay_video(1, "sheep, meadow")],
                   ("pexels", "video"): [pexels_video(2, "fog-collection-net-on-a-hill", w=1920, h=1080)]})
    asked = []

    class Model:
        def text(self, messages, max_tokens=160, model=None, temperature=0.7):
            asked.append(model)
            if "search queries" in messages[0]["content"]:
                return '{"queries": ["fog net hillside", "fog collector"]}'
            listing = messages[1]["content"]
            return json.dumps({"scores": [9 if "fog" in line else 1 for line in listing.split("\n") if line[:1].isdigit()]})
    sc_ = [dict(SCENE)]
    run(sc_, web, keys=("pexels", "pixabay"), providers=Model())
    assert set(asked) == {stock.SCORER_MODEL}
    st = sc_[0]["stock"]["horizontal"]
    assert (st["provider"], st["relevance"], st["query"]) == ("pexels", 9.0, "fog net hillside")
    assert sc_[0]["stock_queries"] == ["fog net hillside", "fog collector"]


def test_without_the_model_keyword_overlap_scores():
    c = stock.Candidate("pixabay", "video", "1", "u", "c", "cu", "fog, net, hillside, villagers, mist", 1920, 1080)
    assert stock.overlap(SCENE, c) >= 7
    c.text = "sheep, meadow, sunrise"
    assert stock.overlap(SCENE, c) == 0


def test_no_people_names_in_queries():
    class Model:
        def text(self, *a, **k):
            return '{"queries": ["Sonam Wangchuk ice stupa", "ice cone"]}'
    q = stock.model_queries({"text": "Engineer Sonam Wangchuk froze water.", "description": ""}, Model(), ["Sonam Wangchuk"])
    assert q == ["ice stupa", "ice cone"]


def test_photos_are_searched_only_when_videos_fall_short():
    web = FakeWeb({("pixabay", "video"): [pixabay_video(1, "fog, net, hillside, villagers, mist", w=1080, h=1920)]})
    sc_ = [dict(SCENE)]
    run(sc_, web)
    assert all("/videos/" in u for u in web.calls("pixabay"))
    import shutil
    shutil.rmtree(stock.STOCK_DIR / "search")  # a fresh search, not the cached one
    web = FakeWeb({("pixabay", "photo"): [pixabay_photo(3, "fog, net, hillside, villagers, mist")]})
    sc_ = [dict(SCENE)]
    run(sc_, web)
    assert sc_[0]["stock"]["horizontal"]["kind"] == "photo" and sc_[0]["src"].suffix == ".jpg"


def test_no_match_falls_back_to_ai():
    web = FakeWeb({("pixabay", "video"): [pixabay_video(1, "sheep, meadow")]})
    sc_ = [dict(SCENE)]
    plan, _ = run(sc_, web)
    assert plan[0] == ("ai", "no good stock match") and "stock" not in sc_[0] and sc_[0].get("src") is None


def test_downloads_skip_4k_and_are_cached(tmp_path):
    c = stock.pixabay_video(pixabay_video(1, "fog"))
    got = []
    path = stock.download(c, "horizontal", get=lambda url: got.append(url) or b"mp4")
    assert path.name == "pixabay_video_1_1920x1080.mp4" and got == ["https://cdn.pixabay.com/1_medium.mp4"]
    stock.download(c, "horizontal", get=lambda url: pytest.fail("downloaded twice"))


# ---------- the plan, media.json and credits ----------

def test_fill_sets_both_formats_and_ai_skips_those_scenes(tmp_path):
    web = FakeWeb({("pixabay", "video"): [pixabay_video(1, "fog, net, hillside, villagers, mist"),
                                          pixabay_video(2, "fog, net, hillside, villagers, mist", w=1080, h=1920)]})
    scenes = [dict(SCENE), {"text": "Welcome to Smart Village Boy.", "visual": None}]
    plan, _ = run(scenes, web, declared={"Smart Village Boy": "a boy"})
    s = scenes[0]
    assert s["srcs"]["horizontal"].name.startswith("pixabay_video_1_") and s["srcs"]["vertical"].name.startswith("pixabay_video_2_")
    assert s["stock"]["vertical"]["creator"] == "Ana" and plan[1][0] == "ai"

    media = stock.write_media_json(scenes, plan, tmp_path / "m.json")
    rows = json.loads(media.read_text(encoding="utf-8"))["scenes"]
    assert rows[0]["choice"] == "stock" and rows[0]["stock"]["horizontal"]["page_url"].endswith("id-1/")
    assert rows[1] == {"scene": 2, "choice": "card", "reason": "names Smart Village Boy (AI keeps them consistent)",
                       "narration": "Welcome to Smart Village Boy."}


def test_credits_and_ai_line_in_the_description():
    scenes = [{"visual": None, "src": "a.mp4", "stock": {"horizontal": {"provider": "pexels", "kind": "video", "id": "2",
               "creator": "Cleo", "page_url": "https://www.pexels.com/video/x-2/"},
               "vertical": {"provider": "pexels", "kind": "video", "id": "2", "creator": "Cleo",
                            "page_url": "https://www.pexels.com/video/x-2/"}}},
              {"visual": None, "src": av.GEN_DIR / "x.jpg", "ai": True}]
    text = stock.credits(scenes)
    assert text.startswith("Stock footage and photos from Pexels (https://www.pexels.com):")
    assert text.count("Video by Cleo on Pexels") == 1
    assert ym.ai_line(scenes).startswith("Some visuals in this video are AI-generated and the rest are stock")
    assert ym.ai_line(scenes[:1]).startswith("The narration is an AI voice; this video has no AI-generated")
    d = ym.description({"hook": "Hook.", "summary": "Sum.", "hashtags": ["#A"]}, [], scenes, "Smart Village Boy")
    assert "Video by Cleo on Pexels: https://www.pexels.com/video/x-2/" in d


def test_check_lists_stock_scenes_and_providers(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PIXABAY_API_KEY", "abc")
    p = tmp_path / "s.txt"
    p.write_text("# character: Ama = a girl\n\nFog rolls over the hills.\n\nAma laughs.\n\n# media: ai\nA pot.\n",
                 encoding="utf-8")
    args = mv.parse_args([str(p), "--check", "--output", str(tmp_path / "story")])
    sc.report(p, args)
    out = capsys.readouterr().out
    assert "Stock search (pixabay, --media mixed): 1" in out
    assert "scene 2: AI image, names Ama" in out and "scene 3: AI image, # media line" in out


def test_stock_clips_play_as_video_in_remotion(tmp_path, monkeypatch):
    import remotion_render as rr
    made = []
    monkeypatch.setattr(mv, "run", lambda cmd, cwd=None: made.append(cmd) or "")
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"x")
    scenes = [{"src": clip, "visual": None, "stock": {"horizontal": {}}, "srcs": {"horizontal": clip}}]
    a = rr.prepare_assets(scenes, tmp_path / "public", SimpleNamespace(no_parallax=True), "horizontal", {}, [150])
    assert a == [{"kind": "video", "src": "scene_000_horizontal.mp4"}]
    cmd = " ".join(map(str, made[0]))
    assert "-stream_loop -1" in cmd and "-t 5.000" in cmd and "crop=1920:1080" in cmd and "-an" in cmd


def test_ai_images_are_made_only_for_scenes_stock_did_not_fill(tmp_path, monkeypatch):
    monkeypatch.setattr(av, "GEN_DIR", tmp_path / "generated")
    calls = []
    monkeypatch.setattr(av, "cf_run", lambda model, payload, creds, timeout=120:
                        calls.append(payload) or ({"response": "p"} if "messages" in payload else
                                                  {"image": __import__("test_ai_visuals").jpeg_b64()}))
    scenes = [{"text": "Fog.", "visual": None, "stock": {"horizontal": {}}, "src": tmp_path / "s.mp4"},
              {"text": "A pot.", "visual": None}]
    av.generate_scene_visuals(scenes, tmp_path / "p.json", providers=av.Providers("final", creds=("a", "t")),
                              formats=("horizontal",))
    assert scenes[0]["src"] == tmp_path / "s.mp4" and scenes[1]["src"].parent == av.GEN_DIR
    assert sum("messages" in c for c in calls) == 1  # no prompt written for the stock scene
