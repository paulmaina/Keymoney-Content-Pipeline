"""The paragraph-per-scene script format, --check, config defaults and the character sheet.
No network, ffmpeg or GPU: every Cloudflare call is faked."""
import json

import pytest

import ai_visuals as av
import make_video as mv
import script_check as sc

FORMAT = """# character: Ama = a 10-year-old girl with short braids, yellow dress and red sandals
# character: Grandpa Kofi - an old man with a white beard, blue kaftan and walking stick

[video] Ama runs across the root bridge. The roots creak under her feet.

Grandpa Kofi watches from the riverbank. He smiles and waves his stick.

[visual: market.jpg] The market is busy today. Everyone has come to see the bridge.

[VIDEO]
Ama and Grandpa Kofi cross together. The bridge holds them both.
"""


def write(tmp_path, text, name="story.txt"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# ---------- 1. parsing the format ----------

def test_tags_at_paragraph_start_are_parsed_and_never_spoken(tmp_path):
    scenes = mv.parse_script(write(tmp_path, FORMAT))
    assert [s["text"] for s in scenes] == [
        "Ama runs across the root bridge. The roots creak under her feet.",
        "Grandpa Kofi watches from the riverbank. He smiles and waves his stick.",
        "The market is busy today. Everyone has come to see the bridge.",
        "Ama and Grandpa Kofi cross together. The bridge holds them both.",
    ]
    assert [s["video"] for s in scenes] == [True, False, False, True]
    assert [s["visual"] for s in scenes] == [None, None, "market.jpg", None]


def test_tags_never_reach_captions(tmp_path):
    scenes = mv.parse_script(write(tmp_path, FORMAT))
    caption_words = mv.script_tokens(" ".join(s["text"] for s in scenes))  # what captions are spelled from
    assert not any("[" in w or "]" in w or w.lower() in ("video", "visual:") for w in caption_words)


def test_both_tags_on_one_line_in_either_order(tmp_path):
    for line in ("[video] [visual: a.png] Text here.", "[visual: a.png][video] Text here."):
        s = mv.parse_script(write(tmp_path, line + "\n"))[0]
        assert (s["text"], s["visual"], s["video"]) == ("Text here.", "a.png", True)


def test_character_lines_are_a_sheet_not_narration_or_comments(tmp_path):
    p = write(tmp_path, FORMAT)
    assert mv.parse_characters(p) == {
        "Ama": "a 10-year-old girl with short braids, yellow dress and red sandals",
        "Grandpa Kofi": "an old man with a white beard, blue kaftan and walking stick"}
    assert all("braids" not in s["text"] for s in mv.parse_script(p))
    assert mv.parse_comments(p) == []  # sheet lines aren't reported as skipped comments


COMMENTED = """# SCENE NOTES: SECRET-COMMENT-1 close-up of the bridge
# character: Ama = a girl in a yellow dress

[video] Ama runs across the bridge.
# SECRET-COMMENT-2 between narration lines
The roots creak.

   # SECRET-COMMENT-3 indented, on its own
Grandpa watches.
"""


def test_comments_are_never_spoken_captioned_or_used_in_prompts(tmp_path, monkeypatch):
    p = write(tmp_path, COMMENTED)
    scenes = mv.parse_script(p)
    spoken = " ".join(s["text"] for s in scenes)  # narration, which is also what captions are spelled from
    assert spoken == "Ama runs across the bridge. The roots creak. Grandpa watches."
    assert not any("SECRET" in w for w in mv.script_tokens(spoken))

    fake = FakeText(["a girl running", "an old man"])
    monkeypatch.setattr(av, "cf_run", fake)
    prompts = tmp_path / "p.json"
    for s in scenes:
        s.setdefault("visual", None)
    av.generate_scene_visuals(scenes, prompts, prompts_only=True, characters=mv.parse_characters(p),
                              providers=av.Providers("cloudflare", creds=("a", "t")))
    sent = " ".join(fake.requests) + prompts.read_text(encoding="utf-8")
    assert "SECRET" not in sent and "SECRET" not in " ".join(s["image_prompt"] for s in scenes)
    assert "a girl in a yellow dress" in scenes[0]["image_prompt"]  # the character line still works


DESCRIBED = """# HACK 1 NIGERIA - Visuals: hands placing a small clay pot inside a larger one
[video] Put one clay pot inside a bigger one, and fill the gap with wet sand.

# HACK 1 - Visuals: fresh tomatoes inside the cool pot
The inner pot stays cool. Tomatoes last for weeks.
"""


def test_visuals_lines_reach_image_prompts_but_are_never_spoken(tmp_path, monkeypatch):
    p = write(tmp_path, DESCRIBED)
    scenes = mv.parse_script(p)
    assert all("Visuals" not in s["text"] for s in scenes)  # not spoken or captioned
    assert scenes[0]["description"] == "HACK 1 NIGERIA - Visuals: hands placing a small clay pot inside a larger one"

    fake = FakeText(["hands placing a clay pot", "tomatoes in a pot"])
    monkeypatch.setattr(av, "cf_run", fake)
    prompts = tmp_path / "p.json"
    providers = av.Providers("cloudflare", creds=("a", "t"))
    av.generate_scene_visuals(scenes, prompts, prompts_only=True, providers=providers)
    assert "this is what the image must show): HACK 1 NIGERIA - Visuals: hands placing" in fake.requests[0]
    assert "Visuals: fresh tomatoes" in fake.requests[1]
    saved = json.loads(prompts.read_text(encoding="utf-8"))["scenes"]
    assert saved[0]["description"].startswith("HACK 1 NIGERIA")


def test_changed_description_rewrites_only_that_prompt(tmp_path, monkeypatch):
    p = write(tmp_path, DESCRIBED)
    prompts = tmp_path / "p.json"
    prompts.write_text(json.dumps({"scenes": [
        {"narration": "Put one clay pot inside a bigger one, and fill the gap with wet sand.", "prompt": "OLD 1",
         "description": "HACK 1 NIGERIA - Visuals: hands placing a small clay pot inside a larger one"},
        {"narration": "The inner pot stays cool. Tomatoes last for weeks.", "prompt": "OLD 2"}]}))  # no description
    fake = FakeText(["NEW 2"])
    monkeypatch.setattr(av, "cf_run", fake)
    scenes = mv.parse_script(p)
    av.generate_scene_visuals(scenes, prompts, prompts_only=True, providers=av.Providers("cloudflare", creds=("a", "t")))
    saved = [s["prompt"] for s in json.loads(prompts.read_text(encoding="utf-8"))["scenes"]]
    assert saved == ["OLD 1", "NEW 2"] and len(fake.requests) == 1


def test_check_shows_what_each_scene_becomes(tmp_path, capsys):
    (tmp_path / "mine.png").write_bytes(b"x")
    p = write(tmp_path, DESCRIBED + "\n[visual: mine.png] My own photo.\n")
    args = mv.parse_args([str(p), "--check", "--output", str(tmp_path / "Exports" / "story")])
    a = sc.report(p, args)
    assert [r["kind"] for r in a["rows"]] == ["image", "image", "own file"]
    out = capsys.readouterr().out
    assert "Strong move ([video]): 1" in out and "Image (Cloudflare): 1, 2" in out and "Own file:          3" in out
    assert "Visual descriptions used in prompts (2 of 3 scenes):" in out


def test_check_lists_skipped_comments_and_flags_broken_character_lines(tmp_path, capsys):
    p = write(tmp_path, COMMENTED + "\n# character: Kofi has no separator\n")
    args = mv.parse_args([str(p), "--check", "--output", str(tmp_path / "Exports" / "story")])
    a = sc.report(p, args)
    assert [n for n, _ in a["comments"]] == [1, 5, 8, 11]
    out = capsys.readouterr().out
    assert "Comments skipped (4; never spoken, captioned or used in prompts):" in out
    assert "line   1: # SCENE NOTES: SECRET-COMMENT-1" in out and "line   8: # SECRET-COMMENT-3" in out
    assert any("line 11:" in f and "looks like a character line" in f for f in a["flags"])


def test_tag_in_the_middle_of_a_line_is_left_alone(tmp_path):
    s = mv.parse_script(write(tmp_path, "She said [video] twice.\n"))[0]
    assert s["text"] == "She said [video] twice." and not s["video"]  # --check flags this


# ---------- 2. --check ----------

def check(tmp_path, text, *extra):
    p = write(tmp_path, text)
    args = mv.parse_args([str(p), "--check", "--output", str(tmp_path / "Exports" / "story"), *extra])
    return sc.analyse(p, args)


def test_check_counts_words_seconds_and_video_scenes(tmp_path):
    a = check(tmp_path, FORMAT, "--rate", "+20%")
    assert [r["words"] for r in a["rows"]] == [12, 12, 12, 11]
    per_word = 60 / (sc.WPM["edge"] * 1.2)
    assert a["rows"][0]["seconds"] == pytest.approx(12 * per_word + mv.PAUSE)
    assert [r["video"] for r in a["rows"]] == [True, False, False, True]


def test_check_flags_missing_visual_overlong_and_unknown_tags(tmp_path):
    long = " ".join(["This sentence keeps going and going for the narration."] * 7)
    a = check(tmp_path, f"[visual: nope.mp4] Short one.\n\n{long}\n\nUse the [vidoe] tag.\n")
    flags = "\n".join(a["flags"])
    assert "scene 1: [visual: nope.mp4] not found" in flags
    assert "scene 2: 7 sentences / 63 words" in flags
    assert "scene 3: '[vidoe]' isn't a tag" in flags


def test_short_fragments_are_not_flagged(tmp_path):
    a = check(tmp_path, "No power. No fridge. No water. Yet they keep food cold all year.\n")
    assert a["flags"] == []


def test_video_tag_on_an_own_file_is_just_a_stronger_move(tmp_path):
    (tmp_path / "mine.png").write_bytes(b"x")
    a = check(tmp_path, "[video] [visual: mine.png] My own picture.\n")
    assert a["flags"] == [] and a["rows"][0]["video"] and a["rows"][0]["kind"] == "own file"


def test_check_counts_cached_prompts_and_images_as_free(tmp_path, monkeypatch):
    monkeypatch.setattr(av, "GEN_DIR", tmp_path / "generated")
    text = "Ama runs. The roots creak.\n\nGrandpa Kofi waves.\n"
    prompts = tmp_path / "Exports" / "story_prompts.json"
    prompts.parent.mkdir(parents=True)
    prompts.write_text(json.dumps({"scenes": [{"narration": "Ama runs. The roots creak.", "prompt": "a girl running"}]}))
    full = av.full_prompt("a girl running", "", "horizontal")
    av.save_image(b"jpg", av.image_path(full, av.IMAGE_MODEL, "horizontal"))
    est = check(tmp_path, text, "--style", "")["estimates"]
    # scene 1: 16:9 cached, 9:16 new; scene 2: a new prompt and both shapes
    assert (est["prompts_saved"], est["prompts_new"], est["images_cached"], est["images_new"]) == (1, 1, 1, 3)
    assert est["neurons_images"] == pytest.approx(3 * av.image_neurons(av.IMAGE_MODEL, "horizontal"))
    assert 20 < est["neurons_prompts"] < 120  # one new prompt with the 70B model
    est = check(tmp_path, text, "--style", "", "--new-images")["estimates"]
    assert (est["images_cached"], est["images_new"]) == (0, 4)


def test_check_estimates_upscaling_on_kaggle(tmp_path, monkeypatch):
    monkeypatch.setattr(av, "GEN_DIR", tmp_path / "generated")
    a = check(tmp_path, "One scene.\n\nTwo scene.\n", "--upscale")
    assert a["estimates"]["upscale_todo"] == 4  # 2 scenes x 2 shapes, all new
    assert a["upscale_minutes"] == pytest.approx(sc.UPSCALE_SETUP_MIN + 4 * sc.UPSCALE_SEC_PER_IMAGE / 60)
    assert check(tmp_path, "One scene.\n")["upscale_minutes"] == 0  # not asked for


def test_pollinations_images_cost_no_cloudflare_neurons(tmp_path):
    est = check(tmp_path, "One scene.\n", "--image-provider", "pollinations")["estimates"]
    assert est["images_new"] == 1 and est["neurons_images"] == 0


def test_check_needs_no_ffmpeg_or_network(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(mv, "check_ffmpeg", lambda: pytest.fail("ffmpeg checked"))
    monkeypatch.setattr(av, "cf_run", lambda *a, **k: pytest.fail("network used"))
    p = write(tmp_path, FORMAT)
    monkeypatch.setattr("sys.argv", ["make_video.py", str(p), "--check"])
    mv.main()
    out = capsys.readouterr().out
    assert "4 scenes" in out and "Strong move ([video]): 1, 4" in out and "Kaggle" not in out


# ---------- 5. config defaults ----------

def test_style_from_config_file(tmp_path, monkeypatch):
    cfg = tmp_path / "make_video.toml"
    cfg.write_text('[defaults]\nstyle = "3D animated film style"\nrate = "+20%"\nimage-model = "flux-1-schnell"\n'
                   'upscale = true\n')
    monkeypatch.setattr(mv, "CONFIG_FILE", cfg)
    a = mv.parse_args(["s.txt"])
    assert (a.style, a.rate, a.image_model, a.upscale) == ("3D animated film style", "+20%", "flux-1-schnell", True)


def test_env_style_beats_config_and_cli_beats_both(tmp_path, monkeypatch):
    cfg = tmp_path / "make_video.toml"
    cfg.write_text('[defaults]\nstyle = "from config"\n')
    monkeypatch.setattr(mv, "CONFIG_FILE", cfg)
    monkeypatch.setenv("VIDEO_STYLE", "from env")
    assert mv.parse_args(["s.txt"]).style == "from env"
    assert mv.parse_args(["s.txt", "--style", "from cli"]).style == "from cli"


def test_config_unknown_keys_are_reported_and_paths_converted(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "make_video.toml"
    cfg.write_text('[defaults]\nmusik = "x.mp3"\nmusic = "song.mp3"\n')
    monkeypatch.setattr(mv, "CONFIG_FILE", cfg)
    a = mv.parse_args(["s.txt"])
    assert a.music.name == "song.mp3" and "unknown option 'musik'" in capsys.readouterr().out


def test_no_config_means_builtin_defaults():
    a = mv.parse_args(["s.txt"])
    assert (a.style, a.rate, a.voice) == ("", "+0%", None)


# ---------- 4. character consistency ----------

class FakeText:
    """Fake Workers AI: records requests; answers with a prompt plus a CHARACTERS line."""

    def __init__(self, replies):
        self.replies, self.requests = list(replies), []

    def __call__(self, model, payload, creds, timeout=120):
        if model == av.TEXT_MODEL:
            self.requests.append(payload["messages"][1]["content"])
            return {"response": self.replies.pop(0)}
        raise AssertionError("no images in these tests")


def run_prompts(tmp_path, monkeypatch, texts, replies, characters=None, style=""):
    fake = FakeText(replies)
    monkeypatch.setattr(av, "cf_run", fake)
    scenes = [{"text": t, "visual": None} for t in texts]
    prompts = tmp_path / "p.json"
    av.generate_scene_visuals(scenes, prompts, style=style, prompts_only=True, characters=characters,
                              providers=av.Providers("cloudflare", creds=("a", "t")))
    return scenes, fake, json.loads(prompts.read_text(encoding="utf-8"))


def test_learned_character_is_passed_to_later_prompts_and_saved(tmp_path, monkeypatch):
    scenes, fake, saved = run_prompts(tmp_path, monkeypatch,
        ["Ama finds a seed.", "Ama plants the seed."],
        ["A girl kneels in a field\nCHARACTERS: Ama = 10-year-old girl, short braids, yellow dress",
         "A girl planting a seed\nCHARACTERS: Ama = a different description"])
    assert "Character sheet: Ama = 10-year-old girl, short braids, yellow dress" in fake.requests[1]
    assert saved["characters"] == {"Ama": "10-year-old girl, short braids, yellow dress"}  # first one sticks
    assert saved["scenes"][0]["prompt"] == "A girl kneels in a field"  # CHARACTERS line not in the prompt
    assert "short braids, yellow dress" in scenes[1]["image_prompt"]


def test_declared_characters_win_and_are_added_when_named(tmp_path, monkeypatch):
    declared = {"Grandpa Kofi": "old man, white beard, blue kaftan, walking stick"}
    scenes, fake, saved = run_prompts(tmp_path, monkeypatch,
        ["Grandpa Kofi tells a story.", "The river is quiet."],
        ["An old man by a fire\nCHARACTERS: Grandpa Kofi = man in a red coat",
         "A calm river at dusk\nCHARACTERS: none"], characters=declared)
    assert saved["characters"] == declared
    assert "white beard, blue kaftan" in scenes[0]["image_prompt"]
    assert "Kofi" not in scenes[0]["image_prompt"]  # descriptions only, never names, go into images
    assert "white beard" not in scenes[1]["image_prompt"]  # not mentioned in scene 2


def test_style_and_sheet_reach_the_prompt_writer(tmp_path, monkeypatch):
    scenes, fake, _ = run_prompts(tmp_path, monkeypatch, ["Ama laughs."], ["A laughing girl"],
                                  characters={"Ama": "girl, braids"}, style="3D animated film style")
    assert "Visual style of the whole video: 3D animated film style" in fake.requests[0]
    assert "Character sheet: Ama = girl, braids" in fake.requests[0]
    assert scenes[0]["image_prompt"].startswith("A laughing girl, with girl, braids, 3D animated film style")


def test_edited_sheet_in_prompts_json_is_reused(tmp_path, monkeypatch):
    p = tmp_path / "p.json"
    p.write_text(json.dumps({"characters": {"Ama": "girl in a green dress"},
                             "scenes": [{"narration": "Ama sings.", "prompt": "a girl singing"}]}))
    monkeypatch.setattr(av, "cf_run", lambda *a, **k: pytest.fail("saved prompt should be reused"))
    scenes = [{"text": "Ama sings.", "visual": None}]
    av.generate_scene_visuals(scenes, p, prompts_only=True, providers=av.Providers("cloudflare", creds=("a", "t")))
    assert scenes[0]["image_prompt"].startswith("a girl singing, with girl in a green dress")


def test_split_characters_tolerates_messy_replies():
    assert av.split_characters('"A boy"\n**Characters:** Tomas = boy, red cap; none = x; broken') == \
        ("A boy", {"Tomas": "boy, red cap"})
    assert av.split_characters("Just a prompt") == ("Just a prompt", {})


def test_mentioned_matches_any_distinctive_word_as_a_whole_word():
    assert av.mentioned("Grandpa Kofi", "and grandpa kofi smiled")
    assert av.mentioned("Grandpa Kofi", "Kofi smiled") and av.mentioned("Grandpa Kofi", "Grandpa laughed")
    assert not av.mentioned("Ama", "the drama begins")
    assert not av.mentioned("The Old Man", "an old tree")  # generic words alone don't count


def test_check_counts_redo_scenes_as_new_and_estimates_quality_checks(tmp_path, monkeypatch):
    monkeypatch.setattr(av, "GEN_DIR", tmp_path / "generated")
    text = "Ama runs.\n\nKofi waves.\n"
    prompts = tmp_path / "Exports" / "story_prompts.json"
    prompts.parent.mkdir(parents=True)
    prompts.write_text(json.dumps({"scenes": [{"narration": "Ama runs.", "prompt": "a girl running"},
                                              {"narration": "Kofi waves.", "prompt": "an old man"}]}))
    for shot in ("a girl running", "an old man"):
        for z in ("horizontal", "vertical"):
            av.save_image(b"jpg", av.image_path(av.full_prompt(shot, "", z), av.IMAGE_MODEL, z))
    av.save_verdict(av.image_path(av.full_prompt("a girl running", "", "horizontal"), av.IMAGE_MODEL, "horizontal"),
                    {"ok": True, "problems": [], "score": 9})
    est = check(tmp_path, text, "--style", "")["estimates"]
    assert (est["images_cached"], est["images_new"], est["checks"]) == (4, 0, 0)
    est = check(tmp_path, text, "--style", "", "--redo-scenes", "2", "--check-images")["estimates"]
    assert (est["images_cached"], est["images_new"]) == (2, 2)
    assert est["checks"] == 1 + 2  # scene 1's unchecked vertical image, and scene 2's two new ones
    assert est["neurons_checks"] == pytest.approx(3 * sc.check_neurons(av.VISION_MODEL))
    assert 25 < sc.check_neurons(av.VISION_MODEL) < 60
