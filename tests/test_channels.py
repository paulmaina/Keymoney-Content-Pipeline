"""Channel profiles (channels/<id>.toml), choosing a channel, per-channel outputs and the channel's own intro and outro
clips. No network; ffmpeg and ffprobe are mocked."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import channels
import make_video as mv
import outputs
import remotion_render as rr
import script_check
import shorts
import youtube_meta as ym

ROOT = Path(__file__).resolve().parent.parent


def profile(cid, text):
    (channels.CHANNELS_DIR / f"{cid}.toml").write_text(text, encoding="utf-8")


def clip_files(cid, *names):
    folder = channels.CHANNELS_DIR / cid / "branding"
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / name).write_bytes(b"clip")
    return folder


def fake_probe(seconds=3.0, audio=True, size=(1920, 1080), fps=30.0):
    return lambda path: {"seconds": seconds, "width": size[0], "height": size[1], "fps": fps, "audio": audio}


# ---------- choosing a channel ----------

def test_the_script_line_beats_channel_which_beats_default_channel(tmp_path, monkeypatch):
    profile("cooking", 'name = "Kitchen Science"\nengine = "edge"\n')
    profile("travel", 'name = "Slow Travel"\n')
    cfg = tmp_path / "make_video.toml"
    cfg.write_text('default-channel = "travel"\n[defaults]\nmedia = "ai"\n', encoding="utf-8")
    monkeypatch.setattr(mv, "CONFIG_FILE", cfg)
    plain = tmp_path / "plain.txt"
    plain.write_text("# A Title For The Video\n\nHello there.\n", encoding="utf-8")
    tagged = tmp_path / "tagged.txt"
    tagged.write_text("# channel: cooking\n# A Title For The Video\n\nHello there.\n", encoding="utf-8")
    assert mv.parse_args([str(plain)]).channel_id == "travel"  # default-channel
    assert mv.parse_args([str(plain), "--channel", "smart_village_boy"]).channel_id == "smart_village_boy"
    a = mv.parse_args([str(tagged), "--channel", "travel"])  # the script's line wins over both
    assert (a.channel_id, a.channel, a.engine, a.media) == ("cooking", "Kitchen Science", "edge", "ai")


def test_a_display_name_works_and_an_unknown_channel_lists_the_known_ones(capsys):
    assert mv.parse_args(["s.txt", "--channel", "Smart Village Boy"]).channel_id == "smart_village_boy"
    with pytest.raises(SystemExit) as e:
        mv.parse_args(["s.txt", "--channel", "nope"])
    assert "smart_village_boy" in str(e.value) and "channels.py new" in str(e.value)


def test_profile_settings_sit_between_make_video_toml_and_the_command_line(tmp_path, monkeypatch):
    cfg = tmp_path / "make_video.toml"
    cfg.write_text('[defaults]\nengine = "edge"\nrate = "+5%"\ncamera = "calm"\n', encoding="utf-8")
    monkeypatch.setattr(mv, "CONFIG_FILE", cfg)
    profile("smart_village_boy", 'name = "Smart Village Boy"\nengine = "kokoro"\nvoice = "am_michael"\nrate = "+20%"\n'
                                 'caption-style = "bold"\nmusic = "song.mp3"\n[colors]\naccent = "#123456"\n')
    a = mv.parse_args(["s.txt", "--rate", "+10%"])
    assert (a.engine, a.voice, a.rate, a.caption_style, a.camera) == ("kokoro", "am_michael", "+10%", "bold", "calm")
    assert a.music == Path("song.mp3")  # paths are paths, as in make_video.toml
    assert a.profile.brand["yellow"] == "#123456" and a.profile.brand["red"] == "#E63946"  # unset colours keep defaults


def test_the_channel_line_is_never_spoken_a_comment_or_a_chapter(tmp_path):
    p = tmp_path / "s.txt"
    p.write_text("# channel: smart_village_boy\n# A Title For The Video\n\n# HOOK\nHello there.\n", encoding="utf-8")
    assert [s["text"] for s in mv.parse_script(p)] == ["Hello there."]
    assert [n for n, _ in mv.parse_comments(p)] == [2, 4]  # the title and the heading, not the channel line
    assert mv.parse_sections(p) == ["HOOK"]
    (tmp_path / "caps.txt").write_text("# CHANNEL: smart_village_boy\n\nHello.\n", encoding="utf-8")
    assert mv.parse_sections(tmp_path / "caps.txt") == [None]
    assert channels.script_channel(tmp_path / "caps.txt") == "smart_village_boy"
    assert channels.script_channel(tmp_path / "missing.txt") is None
    first = tmp_path / "first.txt"
    first.write_text("# channel: smart_village_boy\n# Five Village Water Hacks\n\nHello.\n", encoding="utf-8")
    assert ym.script_title(first) == "Five Village Water Hacks"  # the title can follow the channel line


def test_outputs_go_under_the_channel(tmp_path):
    script = tmp_path / "Promo.txt"
    out = outputs.Outputs.for_script(None, script, "smart_village_boy")
    assert out.folder == outputs.EXPORTS / "smart_village_boy" / "Promo" and out.name == "Promo"
    assert outputs.Outputs.for_script(Path("Launch"), script, "travel").folder == outputs.EXPORTS / "travel" / "Launch"
    assert outputs.Outputs.for_script(tmp_path / "x" / "Final", script, "travel").folder == tmp_path / "x" / "Final"


def test_the_real_smart_village_boy_profile(monkeypatch):
    monkeypatch.setattr(channels, "CHANNELS_DIR", ROOT / "channels")
    svb = channels.load("smart_village_boy")
    assert svb.name == "Smart Village Boy" and svb.tagline == rr.TAGLINE
    assert {k: svb.options[k] for k in ("engine", "voice")} == {"engine": "kokoro", "voice": "am_michael"}
    assert {"style", "rate", "caption-style", "music-volume"} <= set(svb.options) and svb.youtube_style
    assert set(svb.brand) == {"yellow", "red", "night", "nightLight"}
    readme = (svb.branding / "README.txt").read_text(encoding="utf-8")
    assert readme == channels.README.format(name=svb.name, id=svb.id, rule="=" * len(f"Branding clips for {svb.name}"))


# ---------- branding clips ----------

def test_own_clips_replace_the_generated_ones_and_templates_never_count(monkeypatch):
    clip_files("smart_village_boy", "intro_horizontal.mp4", "outro_vertical_TEMPLATE.mp4", "intro_vertical_TEMPLATE.mp4")
    monkeypatch.setattr(channels, "probe", fake_probe(3.0))
    br = channels.Branding(channels.load("smart_village_boy"), {"intro": rr.INTRO, "outro": rr.OUTRO})
    assert br.clip("intro", "horizontal").name == "intro_horizontal.mp4" and br.frames("intro", "horizontal") == 90
    assert br.clip("intro", "vertical") is None and br.frames("intro", "vertical") == rr.INTRO
    assert br.clip("outro", "vertical") is None and br.frames("next", "vertical") == rr.OUTRO
    assert br.outro_kind((1, 3)) == "next" and br.outro_kind((3, 3)) == br.outro_kind(None) == "outro"


def test_a_clip_under_a_second_or_unreadable_falls_back_to_the_generated_one(monkeypatch):
    clip_files("smart_village_boy", "intro_horizontal.mp4", "outro_horizontal.mp4")
    monkeypatch.setattr(channels, "probe", lambda p: None if p.name.startswith("outro") else fake_probe(0.5)(p))
    br = channels.Branding(channels.load("smart_village_boy"), {"intro": rr.INTRO, "outro": rr.OUTRO})
    assert br.clip("intro", "horizontal") is None and br.frames("intro", "horizontal") == rr.INTRO
    assert br.clip("outro", "horizontal") is None
    assert any("unreadable or under 1 s" in line for line in br.lines(["horizontal"]))


def test_shorts_count_the_clips_real_length(monkeypatch):
    clip_files("smart_village_boy", "intro_vertical.mp4", "outro_next_part_vertical.mp4")
    monkeypatch.setattr(channels, "probe", lambda p: fake_probe(4.0 if "intro" in p.name else 5.0)(p))
    br = channels.Branding(channels.load("smart_village_boy"), {"intro": rr.INTRO, "outro": rr.OUTRO})
    assert shorts.overhead(br, "vertical") == pytest.approx((120 + 150 - rr.TRANSITION) / mv.FPS)
    assert shorts.overhead(br, "horizontal") == pytest.approx((rr.INTRO + rr.OUTRO - rr.TRANSITION) / mv.FPS)
    assert shorts.overhead(True) == shorts.overhead(channels.Branding(None, {"intro": rr.INTRO, "outro": rr.OUTRO}))
    assert shorts.branding_of(SimpleNamespace(renderer="remotion", no_branding=False, branding=br)) is br


def test_check_lists_each_intro_and_outro_with_its_length(tmp_path, monkeypatch, capsys):
    clip_files("smart_village_boy", "intro_horizontal.mp4", "outro_vertical.mp4")
    monkeypatch.setattr(channels, "probe", lambda p: fake_probe(6.0, audio=False, size=(1280, 720), fps=25)(p)
                        if "intro" in p.name else fake_probe(4.0)(p))
    p = tmp_path / "story.txt"
    p.write_text("# A Story About Water\n\n# HOOK\nThe river is dry.\n", encoding="utf-8")
    script_check.report(p, mv.parse_args([str(p), "--check", "--no-generate-visuals"]))
    out = capsys.readouterr().out
    assert "Channel: Smart Village Boy (smart_village_boy)" in out
    assert ("horizontal intro: your clip intro_horizontal.mp4, 6.0 s (1280x720, fitted to 1920x1080 with padding, never "
            "stretched, 25 fps, converted to 30, silent)  ! suggested 2-4 s") in out
    assert "vertical outro: your clip outro_vertical.mp4, 4.0 s (1080x1920" not in out  # a 1920x1080 probe here
    assert "vertical outro: your clip outro_vertical.mp4, 4.0 s" in out
    assert 'vertical outro of every Short part but the last: generated "Watch Part N" card, 3.5 s' in out
    assert "horizontal outro: generated outro, 3.5 s" in out
    assert "Main video (horizontal): about 0:" in out


def test_check_says_when_there_is_no_intro_or_outro(tmp_path, capsys):
    p = tmp_path / "story.txt"
    p.write_text("# A Story About Water\n\nThe river is dry.\n", encoding="utf-8")
    script_check.report(p, mv.parse_args([str(p), "--check", "--no-generate-visuals", "--no-branding"]))
    assert "Intro and outro: none (--no-branding)" in capsys.readouterr().out


# ---------- rendering the clips ----------

def test_clips_are_fitted_with_padding_and_keep_their_sound(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(mv, "run", lambda cmd, cwd=None: calls.append([str(c) for c in cmd]) or "")
    monkeypatch.setattr(channels, "probe", fake_probe(3.0, audio=True))
    public = tmp_path / "public"
    public.mkdir()
    name, audio = rr.prepare_clip(tmp_path / "intro_vertical.mp4", "vertical", 90, public, "branding_intro_vertical")
    video_cmd, audio_cmd = calls
    vf = video_cmd[video_cmd.index("-vf") + 1]
    assert "force_original_aspect_ratio=decrease" in vf and "pad=1080:1920" in vf and "crop" not in vf
    assert video_cmd[video_cmd.index("-frames:v") + 1] == "90" and "-an" in video_cmd
    assert name == "branding_intro_vertical.mp4" and audio == tmp_path / "branding_intro_vertical.wav"
    assert audio_cmd[audio_cmd.index("-t") + 1] == "3.000" and "-vn" in audio_cmd
    calls.clear()
    monkeypatch.setattr(channels, "probe", fake_probe(3.0, audio=False))
    assert rr.prepare_clip(tmp_path / "x.mp4", "horizontal", 90, public, "b")[1] is None and len(calls) == 1


def test_music_fades_in_after_the_intro_clip():
    plain = mv.background_filter(30.0, 0.2)
    assert "adelay" not in plain and "afade=t=in" not in plain  # unchanged without a clip
    late = mv.background_filter(30.0, 0.2, music_start=3.0)
    assert "atrim=0:27.000" in late and f"afade=t=in:st=0:d={mv.MUSIC_FADE_IN}" in late and "adelay=3000|3000" in late
    assert "afade=t=out:st=25.000" in late  # still ends with the video


def test_the_clips_sound_is_mixed_onto_the_timeline(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(mv, "run", lambda cmd, cwd=None: calls.append([str(c) for c in cmd]) or "")
    rr.timeline_audio(tmp_path / "n.wav", 2.5, 40.0, tmp_path, "t.wav", [(tmp_path / "i.wav", 0.0), (tmp_path / "o.wav", 36.0)])
    graph = calls[0][calls[0].index("-filter_complex") + 1]
    assert "adelay=2500|2500" in graph and "adelay=36000|36000" in graph and "amix=inputs=3" in graph
    calls.clear()
    rr.timeline_audio(tmp_path / "n.wav", 2.5, 40.0, tmp_path, "t.wav")
    assert "-filter_complex" not in calls[0]  # no clips: the plain delay, as before


def test_render_all_uses_each_formats_clips_and_the_next_part_outro(tmp_path, monkeypatch):
    import captions
    clip_files("smart_village_boy", "intro_vertical.mp4", "outro_next_part_vertical.mp4", "outro_horizontal.mp4")
    monkeypatch.setattr(channels, "probe", lambda p: fake_probe(4.0 if "intro" in p.name else 3.0, audio=True)(p))
    p = tmp_path / "s.txt"
    p.write_text("# HOOK\nNo fridge here.\n\n# HACK 1\nSand holds water.\n\n# HACK 2\nTunnels carry water.\n",
                 encoding="utf-8")
    scenes = mv.parse_script(p)
    for s, d in zip(scenes, [10.0, 12.0, 11.0]):
        s["dur"] = d
    props, mixes, prepared = {}, [], []
    monkeypatch.setattr(rr, "ensure_installed", lambda: None)
    monkeypatch.setattr(rr, "prepare_assets", lambda sc, *a, **k: [{"kind": "card"} for _ in sc])
    monkeypatch.setattr(rr, "save_speed", lambda *a: None)
    monkeypatch.setattr(captions, "scene_faces", lambda sc, fmt, **kw: [[] for _ in sc])
    real_prepare = rr.prepare_clip
    monkeypatch.setattr(rr, "prepare_clip", lambda src, fmt, n, public, stem: prepared.append((src.name, fmt, n))
                        or real_prepare(src, fmt, n, public, stem))
    bundled = []

    def npx(*a, **k):
        if a[:2] == ("remotion", "bundle"):
            bundled.append(len(prepared))  # the bundle copies public/, so every clip must be in it by then
        if a[:2] == ("remotion", "render"):
            prop = Path(next(x for x in a if str(x).startswith("--props=")).split("=", 1)[1])
            props[Path(a[4]).name] = json.loads(prop.read_text(encoding="utf-8"))
    monkeypatch.setattr(rr, "npx", npx)
    monkeypatch.setattr(mv, "run", lambda cmd, cwd=None: "")
    monkeypatch.setattr(mv, "mux", lambda video, audio, args, out, **k: mixes.append((out.name, k["music_start"])) or out)
    args = mv.parse_args([str(p), "--no-parallax"])
    font = tmp_path / "f.ttf"
    font.write_bytes(b"x")
    outs, lead, total = rr.render_all(scenes, tmp_path / "work", tmp_path / "narration.wav", None, font, args,
                                      ["horizontal", "vertical"], outputs.Outputs(tmp_path / "vid").make(), p,
                                      parts=[(0, 2), (2, 3)])
    full, one, two = props["horizontal_silent.mp4"], props["vertical_part1_silent.mp4"], props["vertical_part2_silent.mp4"]
    assert (full["introFrames"], full["introClip"]) == (rr.INTRO, None)  # no horizontal intro clip: the drawn one
    assert (full["outroFrames"], full["outroClip"]) == (90, "branding_outro_horizontal.mp4")
    assert (one["introFrames"], one["introClip"]) == (120, "branding_intro_vertical.mp4")
    assert (one["outroFrames"], one["outroClip"]) == (90, "branding_outro_next_part_vertical.mp4")  # "Watch Part 2"
    assert (two["outroFrames"], two["outroClip"]) == (rr.OUTRO, None)  # the last part: no outro_vertical.mp4
    assert full["brand"]["yellow"] == "#FFD23F" and full["channel"] == "Smart Village Boy"
    assert sorted(prepared) == [("intro_vertical.mp4", "vertical", 120), ("outro_horizontal.mp4", "horizontal", 90),
                                ("outro_next_part_vertical.mp4", "vertical", 90)]  # each clip prepared once
    assert bundled == [3]
    assert mixes == [("vid.mp4", 0.0), ("vid_Short_Part1.mp4", 4.0), ("vid_Short_Part2.mp4", 4.0)]
    assert lead == pytest.approx((rr.INTRO - rr.TRANSITION // 2) / mv.FPS)  # the horizontal video's
    assert rr.lead_seconds(args, "vertical") == pytest.approx((120 - rr.TRANSITION // 2) / mv.FPS)


# ---------- voice speed and YouTube text ----------

def test_kokoro_speed_follows_the_rate(tmp_path, monkeypatch):
    speeds = []
    monkeypatch.setattr(mv, "tts_kokoro", lambda text, out, voice, speed: speeds.append((voice, speed)))
    monkeypatch.setattr(mv, "run", lambda cmd, cwd=None: "")
    monkeypatch.setattr(mv, "duration", lambda p: 2.0)
    args = SimpleNamespace(engine="kokoro", voice="am_michael", rate="+15%")
    mv.narrate([{"text": "One."}, {"text": "Two."}], tmp_path, args)
    assert speeds == [("am_michael", pytest.approx(1.15))] * 2
    assert mv.rate_speed("-5%") == pytest.approx(0.95) and mv.rate_speed("7.5%") == pytest.approx(1.075)
    # --engine edge on a Kokoro channel (and the other way round) uses that engine's own default voice
    assert mv.engine_voice(SimpleNamespace(engine="edge", voice="am_michael")) == mv.EDGE_VOICE
    assert mv.engine_voice(SimpleNamespace(engine="kokoro", voice="en-US-AndrewNeural")) == mv.KOKORO_VOICE
    assert mv.engine_voice(SimpleNamespace(engine="edge", voice="en-US-AndrewNeural")) == "en-US-AndrewNeural"
    assert mv.engine_voice(SimpleNamespace(engine="kokoro", voice=None)) == "af_heart"
    with pytest.raises(SystemExit):
        mv.rate_speed("fast")


def test_the_youtube_style_reaches_the_writer():
    scenes = [{"text": "Sand holds water."}]
    msgs = ym.messages(scenes, [], "Kitchen Science", "playful and practical")
    assert "playful and practical" in msgs[0]["content"] and "Kitchen Science" in msgs[0]["content"]
    assert "style" not in ym.messages(scenes, [], "Kitchen Science")[0]["content"].split("\n")[-1]
    assert ym.youtube_style(SimpleNamespace()) == ""


# ---------- making a channel ----------

def test_new_channel_writes_a_profile_and_the_branding_readme(capsys):
    ch = channels.new_channel("Kitchen Science", templates=False)
    assert (ch.id, ch.name) == ("kitchen_science", "Kitchen Science")
    assert {"engine", "voice", "rate", "style", "caption-style", "music-volume"} <= set(ch.options)
    assert "colors" not in ch.options and ch.brand["red"] == "#E63946"  # [colors] is a table of its own
    readme = (ch.branding / "README.txt").read_text(encoding="utf-8")
    for name in channels.CLIPS.values():
        assert name in readme
    assert "_TEMPLATE" in readme and "30 fps" in readme and "2-4 s" in readme and "3-5 s" in readme
    assert mv.parse_args(["s.txt", "--channel", "kitchen_science"]).channel == "Kitchen Science"
    with pytest.raises(SystemExit):
        channels.new_channel("Kitchen Science", templates=False)


def test_templates_render_each_generated_part_alone(tmp_path, monkeypatch):
    rendered = {}
    ch = channels.new_channel("Kitchen Science", templates=False)
    monkeypatch.setattr(rr, "available", lambda: (True, None))
    monkeypatch.setattr(rr, "ensure_installed", lambda: None)

    def npx(*a, **k):
        if a[:2] == ("remotion", "render"):
            prop = Path(next(x for x in a if str(x).startswith("--props=")).split("=", 1)[1])
            rendered[Path(a[4]).name] = json.loads(prop.read_text(encoding="utf-8"))
    monkeypatch.setattr(rr, "npx", npx)
    written = channels.export_templates(ch)
    assert sorted(p.name for p in written) == sorted(n.replace(".mp4", "_TEMPLATE.mp4") for n in channels.CLIPS.values())
    intro, nxt = rendered["intro_vertical_TEMPLATE.mp4"], rendered["outro_next_part_vertical_TEMPLATE.mp4"]
    assert (intro["introFrames"], intro["outroFrames"], intro["durationInFrames"]) == (rr.INTRO, 0, rr.INTRO)
    assert (nxt["introFrames"], nxt["outroFrames"], nxt["part"]) == (0, rr.OUTRO, {"index": 1, "count": 2})
    assert rendered["outro_horizontal_TEMPLATE.mp4"]["part"] is None and intro["channel"] == "Kitchen Science"
    # and a render never picks a template up
    for p in written:
        p.write_bytes(b"x")
    assert all(ch.clip(*key) is None for key in channels.CLIPS)
