"""Tests for --engine chatterbox: one Kaggle batch, per-line cache, resume and the Edge fallback. Kaggle is faked."""
import json
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

import chatterbox_tts as cb
import make_video as mv
from test_kaggle_runner import FakeCLI


@pytest.fixture(autouse=True)
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "VOICE_DIR", tmp_path / "voice")
    monkeypatch.setattr(cb, "WORK", tmp_path / "voice" / "_kaggle")


def notebook(skip=()):
    """What kaggle/chatterbox.ipynb leaves in /kaggle/working: a WAV per line (minus `skip` keys) and a manifest."""
    def output(dest):
        job = json.loads((cb.WORK / "job" / "job.json").read_text(encoding="utf-8"))
        done = []
        for line in job["lines"]:
            if line["key"] in skip:
                continue
            sf.write(dest / f"{line['key']}.wav", np.zeros(24000, dtype=np.float32), 24000)
            done.append({"key": line["key"], "seconds": 1.0, "gpu_seconds": 1.4})
        (dest / "manifest.json").write_text(json.dumps({"lines": done}), encoding="utf-8")
    return output


def settings(**kw):
    return cb.Settings.from_args(SimpleNamespace(**{"chatterbox_voice": None, "chatterbox_emotion": 0.5,
                                                    "chatterbox_cfg": 0.5, **kw}))


def run(lines, cli):
    return cb.synthesize(lines, cli=cli, sleep=lambda s: None, clock=iter(range(0, 10**6, 60)).__next__)


def test_every_new_line_goes_in_one_batch_and_is_cached(capsys):
    st = settings()
    cli = FakeCLI(output=notebook())
    paths, used = run([("Line one.", st), ("Line two.", st), ("Line one.", st)], cli)
    assert len(cli.did("kernels", "push")) == 1 and sorted(paths) == [0, 1, 2]
    job = json.loads((cb.WORK / "job" / "job.json").read_text(encoding="utf-8"))
    assert [l["text"] for l in job["lines"]] == ["Line one.", "Line two."]  # a repeated line is made once
    assert paths[0] == paths[2] and used == pytest.approx(251.4 / 60)
    assert "GPU time used: about 4.2 min" in capsys.readouterr().out
    cli = FakeCLI(output=notebook())
    assert sorted(run([("Line one.", st), ("Line two.", st)], cli)[0]) == [0, 1] and cli.calls == []  # all cached


def test_changing_any_setting_or_the_reference_makes_a_new_line(tmp_path):
    ref = tmp_path / "me.wav"
    sf.write(ref, np.zeros(16000, dtype=np.float32), 16000)
    base = cb.line_key("Hello.", settings())
    assert base != cb.line_key("Hello.", settings(chatterbox_emotion=0.8))
    assert base != cb.line_key("Hello.", settings(chatterbox_cfg=0.3))
    with_ref = cb.line_key("Hello.", settings(chatterbox_voice=str(ref)))
    assert with_ref != base
    sf.write(ref, np.ones(16000, dtype=np.float32) * 0.1, 16000)  # same file name, different voice
    assert cb.line_key("Hello.", settings(chatterbox_voice=str(ref))) != with_ref


def test_reference_clip_is_sent_once_and_lines_point_to_it(tmp_path):
    ref = tmp_path / "me.wav"
    sf.write(ref, np.zeros(16000, dtype=np.float32), 16000)
    st = settings(chatterbox_voice=str(ref), chatterbox_emotion=0.8, chatterbox_cfg=0.3)
    cli = FakeCLI(output=notebook())
    run([("One.", st), ("Two.", st)], cli)
    files = cli.uploaded[0]
    assert sum(f.startswith("ref_") for f in files) == 1
    job = json.loads((cb.WORK / "job" / "job.json").read_text(encoding="utf-8"))
    assert {l["reference"] for l in job["lines"]} == {next(f for f in files if f.startswith("ref_"))}
    assert job["lines"][0]["exaggeration"] == 0.8 and job["lines"][0]["cfg_weight"] == 0.3


def test_a_stopped_run_resumes_with_only_the_missing_lines():
    st = settings()
    lines = [("One.", st), ("Two.", st), ("Three.", st)]
    missing = cb.line_key("Three.", st)
    paths, _ = run(lines, FakeCLI(statuses=["RUNNING", "ERROR"], output=notebook(skip={missing})))
    assert sorted(paths) == [0, 1]
    cli = FakeCLI(output=notebook())
    paths, _ = run(lines, cli)
    job = json.loads((cb.WORK / "job" / "job.json").read_text(encoding="utf-8"))
    assert [l["text"] for l in job["lines"]] == ["Three."] and sorted(paths) == [0, 1, 2]


def test_broken_audio_is_not_cached(tmp_path):
    st = settings()
    key = cb.line_key("One.", st)

    def output(dest):
        (dest / f"{key}.wav").write_bytes(b"not audio")
    paths, _ = run([("One.", st)], FakeCLI(output=output))
    assert paths == {} and not cb.cached(key).exists()


def test_any_missing_line_falls_back_to_emma_for_the_whole_video(monkeypatch, capsys):
    monkeypatch.setattr(cb, "synthesize", lambda lines, **kw: ({0: "a.wav"}, 1.0))
    args = SimpleNamespace(engine="chatterbox", voice=None, chatterbox_voice=None, chatterbox_emotion=0.5,
                           chatterbox_cfg=0.5)
    assert mv.chatterbox_or_fallback([{"text": "One."}, {"text": "Two."}], args) is None
    assert (args.engine, args.voice) == ("edge", "en-US-EmmaMultilingualNeural")
    assert "never two voices" in capsys.readouterr().out


def test_kaggle_failure_falls_back_too(monkeypatch):
    import kaggle_runner

    def fail(lines, **kw):
        raise kaggle_runner.KaggleError("kernel push failed")
    monkeypatch.setattr(cb, "synthesize", fail)
    args = SimpleNamespace(engine="chatterbox", voice="en-US-AndrewMultilingualNeural", chatterbox_voice=None,
                           chatterbox_emotion=0.5, chatterbox_cfg=0.5)
    assert mv.chatterbox_or_fallback([{"text": "One."}], args) is None
    assert (args.engine, args.voice) == ("edge", "en-US-AndrewMultilingualNeural")  # an Edge voice in the toml stays


def test_rate_is_applied_to_chatterbox_audio(tmp_path, monkeypatch):
    wavs = {}
    for i in range(2):
        wavs[i] = tmp_path / f"{i}.wav"
        sf.write(wavs[i], np.zeros(24000, dtype=np.float32), 24000)
    monkeypatch.setattr(mv, "chatterbox_or_fallback", lambda scenes, args: wavs)
    cmds = []
    monkeypatch.setattr(mv, "run", lambda cmd, cwd=None: cmds.append(list(map(str, cmd))) or "")
    monkeypatch.setattr(mv, "duration", lambda p: 1.4)
    args = SimpleNamespace(engine="chatterbox", rate="+5%", voice=None)
    mv.narrate([{"text": "A."}, {"text": "B."}], tmp_path, args)
    pad = [c for c in cmds if "-af" in c]
    assert pad[0][pad[0].index("-i") + 1] == str(wavs[0])
    assert pad[0][pad[0].index("-af") + 1].startswith("atempo=1.050,apad=")


def test_options_and_config(tmp_path, monkeypatch):
    a = mv.parse_args(["s.txt", "--engine", "chatterbox", "--chatterbox-emotion", "0.8", "--chatterbox-cfg", "0.3"])
    assert (a.engine, a.chatterbox_emotion, a.chatterbox_cfg, a.chatterbox_voice) == ("chatterbox", 0.8, 0.3, None)
    cfg = tmp_path / "make_video.toml"
    cfg.write_text('[defaults]\nengine = "chatterbox"\nchatterbox-voice = "voices/me.wav"\nchatterbox-emotion = 0.6\n')
    monkeypatch.setattr(mv, "CONFIG_FILE", cfg)
    a = mv.parse_args(["s.txt"])
    assert a.engine == "chatterbox" and a.chatterbox_voice.name == "me.wav" and a.chatterbox_emotion == 0.6
    with pytest.raises(SystemExit):
        cb.Settings.from_args(a)  # the clip doesn't exist


def test_time_cap_grows_with_the_batch():
    assert cb.hours_cap(1) == pytest.approx(0.32) and cb.hours_cap(10_000) == cb.MAX_HOURS


def test_no_kaggle_token_falls_back_instead_of_stopping(monkeypatch, capsys):
    import kaggle_runner
    monkeypatch.delenv("KAGGLE_API_TOKEN", raising=False)
    monkeypatch.setattr(kaggle_runner.ai_visuals, "load_env", lambda: None)
    args = SimpleNamespace(engine="chatterbox", voice="en-US-EmmaMultilingualNeural", chatterbox_voice=None,
                           chatterbox_emotion=0.5, chatterbox_cfg=0.5)
    assert mv.chatterbox_or_fallback([{"text": "A line nobody has cached."}], args) is None
    out = capsys.readouterr().out
    assert args.engine == "edge" and "needs KAGGLE_API_TOKEN" in out and "Emma" in out


def test_the_smart_village_boy_voice_is_kokoro_michael(monkeypatch):
    import channels
    monkeypatch.setattr(mv, "CONFIG_FILE", mv.ROOT / "make_video.toml")
    monkeypatch.setattr(channels, "CHANNELS_DIR", mv.ROOT / "channels")
    a = mv.parse_args(["s.txt"])
    assert a.channel_id == "smart_village_boy" and a.channel == "Smart Village Boy"
    import tomllib
    rate = tomllib.loads((mv.ROOT / "channels" / "smart_village_boy.toml").read_text(encoding="utf-8"))["rate"]
    assert (a.engine, a.voice, a.rate) == ("kokoro", "am_michael", rate)  # the speed is the user's to tune
    assert (a.chatterbox_emotion, a.chatterbox_cfg, a.chatterbox_voice) == (0.5, 0.5, None)  # if switched to chatterbox
