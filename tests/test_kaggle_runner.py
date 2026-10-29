"""Tests for the automated Kaggle workflow. The Kaggle CLI is faked: no real API calls, no GPU."""
import io
import json
import subprocess
import zipfile

import pytest

import kaggle_runner as kr
import make_video as mv
import video_job as vj

TOKEN = "KGAT_secret_token_123"
USER = "kimani"


def job(scenes=(1,)):
    return {"version": 1, "name": "demo", "scenes": [
        {"scene": n, "narration": f"Scene {n}.", "task": "video", "image": f"scene_{n:03}.jpg",
         "image_prompt": "p", "motion_prompt": "m", "seconds": 5.0, "seed": n} for n in scenes]}


def clips_zip(scenes):
    """What the notebook leaves in /kaggle/working/clips.zip."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        items = [{"scene": n, "narration": f"Scene {n}.", "kind": "video", "file": f"clips/scene_{n:03}.mp4",
                  "model": "Wan2.2-TI2V-5B", "width": 1280, "height": 704, "seconds": 5.04} for n in scenes]
        z.writestr("manifest.json", json.dumps({"items": items}))
        for n in scenes:
            z.writestr(f"clips/scene_{n:03}.mp4", b"mp4 bytes")
    return buf.getvalue()


class FakeCLI:
    """Answers `kaggle ...` commands like the real CLI prints them, and records every call."""

    def __init__(self, statuses=("RUNNING", "COMPLETE"), outputs=((1,),), datasets=(), log="", version=3):
        self.calls = []
        self.statuses = list(statuses)
        self.outputs = list(outputs)  # clip scene numbers each run's output contains
        self.datasets = list(datasets)
        self.log = log or json.dumps([
            {"stream_name": "stdout", "time": 72.0, "data": "Clips: 0 done already, 1 to make\n"},
            {"stream_name": "stdout", "time": 2254.4, "data": "  scene 1 clip by Wan2.2-TI2V-5B: 36 min\n"}])
        self.version = version
        self.uploaded = []  # file names in the dataset folder at each upload

    def run(self, *args, check=True, timeout=None):
        args = tuple(map(str, args))
        self.calls.append(args)
        cmd = args[:2]
        if cmd == ("config", "view"):
            return f"Configuration values from C:\\Users\\x\\.kaggle\n- username: {USER}\n- path: None\n"
        if cmd == ("datasets", "list"):
            return json.dumps([{"ref": r, "title": r} for r in self.datasets])
        if cmd in (("datasets", "create"), ("datasets", "version")):
            folder = args[args.index("-p") + 1]
            from pathlib import Path
            self.uploaded.append(sorted(p.name for p in Path(folder).iterdir()))
            return "Dataset version is being created."
        if cmd == ("datasets", "status"):
            return "ready"
        if cmd == ("kernels", "push"):
            return (f"Kernel version {self.version} successfully pushed.  Please check progress at "
                    f"https://www.kaggle.com/code/{USER}/demo-wan-video")
        if cmd == ("kernels", "status"):
            status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            msg = '\nFailure message: "Your notebook hit an error"' if status == "ERROR" else ""
            return f'{args[2]} has status "KernelWorkerStatus.{status}"{msg}'
        if cmd == ("kernels", "logs"):
            return self.log
        if cmd == ("kernels", "output"):
            from pathlib import Path
            dest = Path(args[args.index("-p") + 1])
            scenes = self.outputs.pop(0) if self.outputs else ()
            if scenes:
                (dest / "clips.zip").write_bytes(clips_zip(scenes))
            (dest / "demo-wan-video.log").write_text(self.log, encoding="utf-8")
            return ""
        raise AssertionError(f"unexpected kaggle command {args}")

    def did(self, *prefix):
        return [c for c in self.calls if c[:len(prefix)] == prefix]

    def follow_logs(self, kernel):
        self.calls.append(("kernels", "logs", kernel, "-f"))
        return kr.LogFollower(FakeProc(kr.log_lines(self.log)))


class FakeProc:
    """Stands in for the `kaggle kernels logs -f` process."""

    def __init__(self, lines, running=True):
        self.stdout = iter(l + "\n" for l in lines)
        self.running, self.terminated = running, False

    def poll(self):
        return None if self.running and not self.terminated else 0

    def terminate(self):
        self.terminated = True


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """A demo export in tmp_path/Exports, with export_job faked so no images or prompts are made."""
    base = tmp_path / "Exports" / "demo"
    base.parent.mkdir()
    exports = []

    def fake_export(scenes, base_, providers, style="", regenerate=False, video_size="960x544", characters=None):
        d = base_.with_name(base_.name + "_video_job")
        d.mkdir(exist_ok=True)
        (d / "job.json").write_text(json.dumps(job(range(1, len(scenes) + 1))), encoding="utf-8")
        for n in range(1, len(scenes) + 1):
            (d / f"scene_{n:03}.jpg").write_bytes(b"jpg")
        exports.append(d)
    monkeypatch.setattr(vj, "export_job", fake_export)
    return base, exports


def scenes(n=1):
    return [{"text": f"Scene {i}.", "visual": None, "video": True} for i in range(1, n + 1)]


def go(base, cli, **kw):
    return kr.run(scenes(kw.pop("n", 1)), base, providers=None, cli=cli, sleep=lambda s: None,
                  clock=iter(range(0, 10**6, 60)).__next__, **kw)


# ---------- credentials ----------

def test_missing_token_explains_how_to_make_one(monkeypatch, tmp_path):
    monkeypatch.delenv("KAGGLE_API_TOKEN", raising=False)
    monkeypatch.setattr(kr.ai_visuals, "ENV_FILE", tmp_path / "none.env")
    with pytest.raises(SystemExit) as e:
        kr.api_token()
    assert "API Tokens" in str(e.value) and "Generate New Token" in str(e.value)


def test_token_goes_in_env_only_and_is_scrubbed(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw["env"]
        return subprocess.CompletedProcess(cmd, 0, stdout=f"echo {TOKEN}", stderr="")
    monkeypatch.setattr(kr.subprocess, "run", fake_run)
    out = kr.KaggleCLI(TOKEN).run("config", "view")
    assert seen["env"]["KAGGLE_API_TOKEN"] == TOKEN
    assert TOKEN not in " ".join(seen["cmd"]) and TOKEN not in out and "***" in out


def test_rejected_token_stops_with_setup_help(monkeypatch):
    monkeypatch.setattr(kr.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 1, stdout="", stderr=f"401 Client Error: Unauthorized ({TOKEN})"))
    with pytest.raises(SystemExit) as e:
        kr.KaggleCLI(TOKEN).run("config", "view")
    assert "API Tokens" in str(e.value) and TOKEN not in str(e.value)


# ---------- dataset ----------

def test_reuses_hand_uploaded_demo_job_dataset():
    assert kr.pick_dataset(USER, "demo", [f"{USER}/other", f"{USER}/demo-job"]) == (f"{USER}/demo-job", True, [])
    assert kr.pick_dataset(USER, "demo", [f"{USER}/demo_job"])[:2] == (f"{USER}/demo_job", True)


def test_prefers_tool_made_dataset_and_reports_duplicates():
    ref, exists, others = kr.pick_dataset(USER, "demo", [f"{USER}/demo-job", f"{USER}/demo-video-job"])
    assert (ref, exists, others) == (f"{USER}/demo-video-job", True, [f"{USER}/demo-job"])


def test_new_dataset_when_none_exists():
    assert kr.pick_dataset(USER, "My Video", []) == (f"{USER}/my-video-video-job", False, [])


def test_existing_dataset_gets_a_new_version_not_a_duplicate(setup):
    base, _ = setup
    cli = FakeCLI(datasets=[f"{USER}/demo-job"])
    go(base, cli)
    assert cli.did("datasets", "version") and not cli.did("datasets", "create")
    assert "-d" in cli.did("datasets", "version")[0]  # old versions deleted
    meta = json.loads((base.parent / "demo_video_job" / "dataset-metadata.json").read_text(encoding="utf-8"))
    assert meta["id"] == f"{USER}/demo-job"


def test_new_dataset_is_private(setup):
    base, _ = setup
    cli = FakeCLI()
    go(base, cli)
    create = cli.did("datasets", "create")[0]
    assert "-u" not in create and "--public" not in create


# ---------- kernel ----------

def test_kernel_metadata_private_gpu_internet_with_dataset():
    meta = kr.kernel_metadata(USER, "demo", f"{USER}/demo-job")
    assert meta["id"] == f"{USER}/demo-wan-video" and meta["title"] == "demo-wan-video"
    assert (meta["is_private"], meta["enable_gpu"], meta["enable_internet"]) == (True, True, True)
    assert meta["machine_shape"] == "NvidiaTeslaT4" and meta["dataset_sources"] == [f"{USER}/demo-job"]
    assert meta["kernel_type"] == "notebook" and meta["code_file"] == "wan_video.ipynb"


def test_push_uses_t4x2_timeout_and_returns_versioned_ref(setup):
    base, _ = setup
    cli = FakeCLI()
    go(base, cli, hours=2)
    push = cli.did("kernels", "push")[0]
    assert push[push.index("--accelerator") + 1] == "NvidiaTeslaT4" and push[push.index("-t") + 1] == "7200"
    folder = base.parent / "demo_kaggle" / "kernel"
    assert (folder / "wan_video.ipynb").exists() and (folder / "kernel-metadata.json").exists()
    assert all(c[2] == f"{USER}/demo-wan-video/3" for c in cli.did("kernels", "status"))


def test_push_error_text_is_a_failure(tmp_path):
    class BadPush(FakeCLI):
        def run(self, *args, check=True, timeout=None):
            if args[:2] == ("kernels", "push"):
                return "The following are not valid dataset sources and could not be added to the kernel: ['x']"
            return super().run(*args, check=check)
    with pytest.raises(kr.KaggleError):
        kr.push_kernel(BadPush(), kr.kernel_metadata(USER, "demo", "x"), tmp_path / "k", 3600)


# ---------- polling ----------

def test_poll_prints_progress_every_minute_until_done(capsys):
    cli = FakeCLI(statuses=["QUEUED", "RUNNING", "RUNNING", "COMPLETE"])
    sleeps = []
    status, msg, seconds = kr.poll(cli, "k/v/1", interval=60, sleep=sleeps.append,
                                   clock=iter(range(0, 10**6, 60)).__next__)
    out = capsys.readouterr().out
    assert status == "COMPLETE" and sleeps == [60, 60, 60] and seconds == 240
    assert "queued" in out and "scene 1 clip by Wan2.2-TI2V-5B" in out and "complete" in out


def test_log_follower_keeps_latest_progress_including_step_bars():
    lines = ["Clips: 0 done already, 1 to make",
             "Loading Wan2.2-TI2V-5B...",
             " 10%|#         | 3/30 [04:10<37:30, 83.4s/it]\r 13%|#3        | 4/30 [05:33<36:07, 83.4s/it]",
             "pip noise that isn't progress"]
    f = kr.LogFollower(FakeProc(lines))
    f.thread.join(timeout=5)
    assert f.latest.startswith("13%") and "4/30" in f.latest


def test_log_follower_scrubs_the_token():
    f = kr.LogFollower(FakeProc([f"Loading with {TOKEN}"]), kr.KaggleCLI(TOKEN).scrub)
    f.thread.join(timeout=5)
    assert TOKEN not in f.latest and "***" in f.latest


def test_follower_starts_while_running_and_stops_at_the_end():
    cli = FakeCLI(statuses=["QUEUED", "RUNNING", "COMPLETE"])
    kr.poll(cli, "k/v/1", sleep=lambda s: None, clock=iter(range(0, 10**6, 60)).__next__)
    follows = cli.did("kernels", "logs")
    assert len(follows) == 1 and follows[0][-1] == "-f"


def test_gpu_time_comes_from_kaggle_log_timestamps(tmp_path):
    (tmp_path / "demo-wan-video.log").write_text(json.dumps(
        [{"stream_name": "stdout", "time": 7.2, "data": "a"}, {"stream_name": "stdout", "time": 2254.4, "data": "b"}]))
    assert kr.session_minutes(tmp_path) == pytest.approx(37.57, abs=0.01)
    (tmp_path / "demo-wan-video.log").write_text("plain text log without times")
    assert kr.session_minutes(tmp_path) is None


def test_clip_summary_says_which_model_and_why_it_fell_back(tmp_path, capsys):
    (tmp_path / "manifest.json").write_text(json.dumps({"items": [
        {"scene": 1, "kind": "video", "model": "Wan2.1-T2V-1.3B", "width": 832, "height": 480, "seconds": 5.06,
         "minutes": 39.9, "peak_gpu_gb": {"cuda:0": 9.1, "cuda:1": 0.0},
         "fallback_reason": "Wan2.2-TI2V-5B failed on scene 1: OutOfMemoryError: CUDA out of memory"}]}))
    kr.print_clip_summary(tmp_path)
    out = capsys.readouterr().out
    assert "Wan2.1-T2V-1.3B 832x480" in out and "39.9 min" in out and "'cuda:0': 9.1" in out
    assert "FALLBACK because Wan2.2-TI2V-5B failed on scene 1: OutOfMemoryError" in out


def test_status_parsing():
    cli = FakeCLI(statuses=["ERROR"])
    assert kr.kernel_status(cli, "k") == ("ERROR", "Your notebook hit an error")


def test_log_lines_reads_json_and_plain_logs():
    assert kr.log_lines('[{"data": "a\\n"}, {"data": "b\\n"}]') == ["a", "b"]
    assert kr.log_lines("one\n\ntwo") == ["one", "two"]


# ---------- end to end, failure and resume ----------

def test_successful_run_returns_clips_for_import(setup, capsys):
    base, exports = setup
    collected = go(base, FakeCLI())
    assert collected == base.parent / "demo_kaggle" / "collected"
    assert (collected / "clips" / "scene_001.mp4").exists()
    assert "GPU time used: about 38 min (session length from Kaggle's log" in capsys.readouterr().out
    sc = [{"text": "Scene 1.", "visual": None}]
    assert vj.import_clips(collected, sc, base.parent / "w") == (1, 0)  # exactly like --clips


def test_failed_run_shows_log_tail_and_keeps_finished_clips(setup, capsys):
    base, _ = setup
    cli = FakeCLI(statuses=["RUNNING", "ERROR"], outputs=[(1,)],
                  log="Loading Wan2.2-TI2V-5B\nscene 1 clip done\ntorch.cuda.OutOfMemoryError: CUDA out of memory\n")
    assert go(base, cli, n=2) is None
    out = capsys.readouterr().out
    assert "last 3 lines of the Kaggle log" in out and "OutOfMemoryError" in out
    assert "--kaggle-resume" in out and "scene(s) 2 still missing" in out
    assert (base.parent / "demo_kaggle" / "collected" / "clips" / "scene_001.mp4").exists()


def test_resume_sends_finished_clips_back_and_skips_export(setup):
    base, exports = setup
    go(base, FakeCLI(statuses=["RUNNING", "ERROR"], outputs=[(1,)]), n=2)  # scene 1 done, scene 2 missing
    cli = FakeCLI(statuses=["ERROR", "RUNNING", "COMPLETE"], outputs=[(), (2,)], datasets=[f"{USER}/demo-video-job"],
                  version=4)
    collected = go(base, cli, n=2, resume=True)
    assert len(exports) == 1  # the job wasn't exported again
    assert "prev_clips__scene_001.mp4" in cli.uploaded[0] and "prev_manifest.json" in cli.uploaded[0]
    assert cli.did("datasets", "version") and not cli.did("datasets", "create")
    assert sorted(p.name for p in (collected / "clips").iterdir()) == ["scene_001.mp4", "scene_002.mp4"]


def test_resume_waits_for_a_run_that_is_still_going(setup):
    base, _ = setup
    go(base, FakeCLI(statuses=["RUNNING", "ERROR"], outputs=[()]))
    cli = FakeCLI(statuses=["RUNNING", "RUNNING", "COMPLETE"], outputs=[(1,)])
    assert go(base, cli, resume=True) is not None
    assert not cli.did("kernels", "push") and not cli.did("datasets", "version")


def test_resume_with_everything_downloaded_does_not_run_again(setup):
    base, _ = setup
    go(base, FakeCLI())
    cli = FakeCLI(statuses=["COMPLETE"], outputs=[(1,)])
    assert go(base, cli, resume=True) is not None and not cli.did("kernels", "push")


def test_new_run_refuses_while_previous_one_is_running(setup):
    base, _ = setup
    go(base, FakeCLI(statuses=["RUNNING", "ERROR"], outputs=[()]))
    with pytest.raises(SystemExit) as e:
        go(base, FakeCLI(statuses=["RUNNING"]))
    assert "--kaggle-resume" in str(e.value)


def test_default_time_limit_scales_with_clips_and_caps_at_12h(tmp_path):
    (tmp_path / "job.json").write_text(json.dumps(job([1, 2])), encoding="utf-8")
    assert kr.default_hours(tmp_path) == pytest.approx(3.75)
    (tmp_path / "job.json").write_text(json.dumps(job(range(1, 30))), encoding="utf-8")
    assert kr.default_hours(tmp_path) == 12.0


class ReachedImages(Exception):
    pass


@pytest.mark.parametrize("script, kaggle_called", [
    ("Just narration.\n\nMore narration.\n", False),
    ("[video] A scene that moves.\n\nA still scene.\n", True),
])
def test_one_command_uses_kaggle_only_for_video_scenes(tmp_path, monkeypatch, capsys, script, kaggle_called):
    p = tmp_path / "s.txt"
    p.write_text(script, encoding="utf-8")
    called = []
    monkeypatch.setattr(mv, "check_ffmpeg", lambda: None)
    monkeypatch.setattr(mv.ai_visuals, "Providers", lambda *a, **k: object())
    monkeypatch.setattr(kr, "run", lambda *a, **k: called.append(True) or (tmp_path / "collected"))
    monkeypatch.setattr(vj, "import_clips", lambda *a, **k: (0, 0))

    def stop(*a, **k):
        raise ReachedImages
    monkeypatch.setattr(mv.ai_visuals, "generate_scene_visuals", stop)
    monkeypatch.setattr("sys.argv", ["make_video.py", str(p), "--kaggle-video", "-o", str(tmp_path / "out")])
    with pytest.raises(ReachedImages):  # either way it goes on to make the still images
        mv.main()
    assert bool(called) is kaggle_called
    out = capsys.readouterr().out
    assert ("nothing goes to Kaggle" in out) is (not kaggle_called)


def test_cli_flags():
    a = mv.parse_args(["s.txt", "--kaggle-resume"])
    assert a.kaggle_video and a.kaggle_resume and a.generate_visuals and a.kaggle_size == "960x544"
    assert mv.parse_args(["s.txt", "--kaggle-video", "--kaggle-size", "1280x704"]).kaggle_size == "1280x704"
    with pytest.raises(SystemExit):
        mv.parse_args(["s.txt", "--kaggle-size", "1920x1080"])
    with pytest.raises(SystemExit):
        mv.parse_args(["s.txt", "--kaggle-video", "--clips", "c.zip"])
