"""Tests for running a notebook on Kaggle (used by --upscale). The Kaggle CLI is faked: no real API calls, no GPU."""
import json
import subprocess
from pathlib import Path

import pytest

import kaggle_runner as kr

TOKEN = "KGAT_secret_token_123"
USER = "kimani"
NOTEBOOK = Path(__file__).resolve().parent.parent / "kaggle" / "upscale.ipynb"


class FakeCLI:
    """Answers `kaggle ...` commands like the real CLI prints them, and records every call.
    `output` is called with the download folder to fake what the notebook left in /kaggle/working."""

    def __init__(self, statuses=("RUNNING", "COMPLETE"), datasets=(), log="", version=3, output=None):
        self.calls = []
        self.statuses = list(statuses)
        self.datasets = list(datasets)
        self.log = log or json.dumps([
            {"stream_name": "stdout", "time": 12.0, "data": "Images: 2 to upscale to at least 2048 px on cuda\n"},
            {"stream_name": "stdout", "time": 251.4, "data": "  image 2/2: 1536x864 -> 3072x1728 (x2) in 2.1s\n"}])
        self.version = version
        self.output = output
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
            folder = Path(args[args.index("-p") + 1])
            self.uploaded.append(sorted(p.name for p in folder.iterdir()))
            return "Dataset version is being created."
        if cmd == ("datasets", "status"):
            return "ready"
        if cmd == ("kernels", "push"):
            return (f"Kernel version {self.version} successfully pushed.  Please check progress at "
                    f"https://www.kaggle.com/code/{USER}/ai-video-upscale")
        if cmd == ("kernels", "status"):
            status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            msg = '\nFailure message: "Your notebook hit an error"' if status == "ERROR" else ""
            return f'{args[2]} has status "KernelWorkerStatus.{status}"{msg}'
        if cmd == ("kernels", "logs"):
            return self.log
        if cmd == ("kernels", "output"):
            dest = Path(args[args.index("-p") + 1])
            (dest / "ai-video-upscale.log").write_text(self.log, encoding="utf-8")
            if self.output:
                self.output(dest)
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


def clock():
    return iter(range(0, 10**6, 60)).__next__


def run_job(tmp_path, cli, hours=0.5):
    job = tmp_path / "job"
    job.mkdir(exist_ok=True)
    (job / "job.json").write_text("{}", encoding="utf-8")
    return kr.run_notebook(cli, NOTEBOOK, "ai-video-upscale", job, tmp_path / "work", hours,
                           sleep=lambda s: None, clock=clock())


# ---------- credentials ----------

def test_missing_token_explains_how_to_make_one(monkeypatch, tmp_path):
    monkeypatch.delenv("KAGGLE_API_TOKEN", raising=False)
    monkeypatch.setattr(kr.ai_visuals, "ENV_FILE", tmp_path / "none.env")
    with pytest.raises(SystemExit) as e:
        kr.api_token()
    assert "--upscale needs KAGGLE_API_TOKEN" in str(e.value) and "Generate New Token" in str(e.value)


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


# ---------- dataset and kernel ----------

def test_first_run_creates_a_private_dataset(tmp_path):
    cli = FakeCLI()
    run_job(tmp_path, cli)
    create = cli.did("datasets", "create")[0]
    assert "-u" not in create and "--public" not in create and not cli.did("datasets", "version")
    meta = json.loads((tmp_path / "job" / "dataset-metadata.json").read_text(encoding="utf-8"))
    assert meta["id"] == f"{USER}/ai-video-upscale-job"
    assert cli.uploaded == [["dataset-metadata.json", "job.json"]]


def test_later_runs_version_the_same_dataset(tmp_path):
    cli = FakeCLI(datasets=[f"{USER}/ai-video-upscale-job"])
    run_job(tmp_path, cli)
    assert cli.did("datasets", "version") and not cli.did("datasets", "create")
    assert "-d" in cli.did("datasets", "version")[0]  # old versions deleted


def test_kernel_metadata_private_gpu_internet_with_dataset():
    meta = kr.kernel_metadata(USER, "ai-video-upscale", NOTEBOOK, f"{USER}/ai-video-upscale-job")
    assert meta["id"] == f"{USER}/ai-video-upscale" and meta["code_file"] == "upscale.ipynb"
    assert (meta["is_private"], meta["enable_gpu"], meta["enable_internet"]) == (True, True, True)
    assert meta["machine_shape"] == "NvidiaTeslaT4" and meta["dataset_sources"] == [f"{USER}/ai-video-upscale-job"]


def test_push_uses_t4_timeout_and_polls_the_pushed_version(tmp_path):
    cli = FakeCLI()
    status, run_dir, used, watched = run_job(tmp_path, cli, hours=0.5)
    push = cli.did("kernels", "push")[0]
    assert push[push.index("--accelerator") + 1] == "NvidiaTeslaT4" and push[push.index("-t") + 1] == "1800"
    assert (tmp_path / "work" / "kernel" / "upscale.ipynb").exists()
    assert all(c[2] == f"{USER}/ai-video-upscale/3" for c in cli.did("kernels", "status"))
    assert status == "COMPLETE" and run_dir == tmp_path / "work" / "last_run"
    assert used == pytest.approx(251.4 / 60)


def test_push_error_text_is_a_failure(tmp_path):
    class BadPush(FakeCLI):
        def run(self, *args, check=True, timeout=None):
            if args[:2] == ("kernels", "push"):
                return "The following are not valid dataset sources and could not be added to the kernel: ['x']"
            return super().run(*args, check=check)
    meta = kr.kernel_metadata(USER, "ai-video-upscale", NOTEBOOK, "x")
    with pytest.raises(kr.KaggleError):
        kr.push_kernel(BadPush(), meta, tmp_path / "k", 3600, NOTEBOOK)


def test_failed_run_shows_the_log_tail(tmp_path, capsys):
    status, *_ = run_job(tmp_path, FakeCLI(statuses=["RUNNING", "ERROR"]))
    assert status == "ERROR" and "last 2 lines of the Kaggle log" in capsys.readouterr().out


# ---------- polling ----------

def test_poll_prints_progress_every_minute_until_done(capsys):
    cli = FakeCLI(statuses=["QUEUED", "RUNNING", "RUNNING", "COMPLETE"])
    sleeps = []
    status, msg, seconds = kr.poll(cli, "k/v/1", interval=60, sleep=sleeps.append, clock=clock())
    out = capsys.readouterr().out
    assert status == "COMPLETE" and sleeps == [60, 60, 60] and seconds == 240
    assert "queued" in out and "image 2/2: 1536x864 -> 3072x1728" in out and "complete" in out


def test_log_follower_keeps_latest_progress():
    lines = ["Images: 2 to upscale", "Loading RealESRGAN x2 weights...", "pip noise that isn't progress",
             "  image 1/2: 1536x864 -> 3072x1728 (x2) in 2.0s\r  image 2/2: 864x1536 -> 1728x3072 (x2) in 2.1s"]
    f = kr.LogFollower(FakeProc(lines))
    f.thread.join(timeout=5)
    assert f.latest.startswith("image 2/2")


def test_log_follower_scrubs_the_token():
    f = kr.LogFollower(FakeProc([f"Loading with {TOKEN}"]), kr.KaggleCLI(TOKEN).scrub)
    f.thread.join(timeout=5)
    assert TOKEN not in f.latest and "***" in f.latest


def test_follower_starts_while_running_and_stops_at_the_end():
    cli = FakeCLI(statuses=["QUEUED", "RUNNING", "COMPLETE"])
    kr.poll(cli, "k/v/1", sleep=lambda s: None, clock=clock())
    follows = cli.did("kernels", "logs")
    assert len(follows) == 1 and follows[0][-1] == "-f"


def test_gpu_time_comes_from_kaggle_log_timestamps(tmp_path):
    (tmp_path / "x.log").write_text(json.dumps(
        [{"stream_name": "stdout", "time": 7.2, "data": "a"}, {"stream_name": "stdout", "time": 2254.4, "data": "b"}]))
    assert kr.session_minutes(tmp_path) == pytest.approx(37.57, abs=0.01)
    (tmp_path / "x.log").write_text("plain text log without times")
    assert kr.session_minutes(tmp_path) is None


def test_status_parsing():
    cli = FakeCLI(statuses=["ERROR"])
    assert kr.kernel_status(cli, "k") == ("ERROR", "Your notebook hit an error")


def test_log_lines_reads_json_and_plain_logs():
    assert kr.log_lines('[{"data": "a\\n"}, {"data": "b\\n"}]') == ["a", "b"]
    assert kr.log_lines("one\n\ntwo") == ["one", "two"]


def test_notebook_is_built_from_its_source():
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_notebook", NOTEBOOK.parent / "build_notebook.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    cells = build.cells_from_py((NOTEBOOK.parent / "upscale.py").read_text(encoding="utf-8"))
    nb = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    assert [c["source"] for c in nb["cells"]] == [c["source"] for c in cells]  # rebuild after editing upscale.py
    assert nb["metadata"]["kaggle"]["isGpuEnabled"]
