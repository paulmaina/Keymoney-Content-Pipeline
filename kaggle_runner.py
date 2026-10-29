"""Run the Kaggle video job without the Kaggle website (make_video.py --kaggle-video / --kaggle-resume).

Uses the official Kaggle CLI (`python -m kaggle`) with a new-style API token from KAGGLE_API_TOKEN
in .env. The token is passed to the CLI through its environment only: it never appears on a command
line, in a file this tool writes, or in printed output.

Flow: export the job -> create or version a private dataset -> push kernel/wan_video.ipynb as a private
GPU (T4 x2) kernel with internet and the dataset attached -> poll every minute -> download the
outputs (also when the run fails) -> merge them into Exports/<name>_kaggle/collected/ for --clips.

Kaggle batch runs stop by themselves when the notebook ends; a hard timeout (--kaggle-hours) caps
each run so a stuck session can't burn the weekly GPU quota. There is no CLI command to cancel a run.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import ai_visuals
import video_job

ROOT = Path(__file__).resolve().parent
NOTEBOOK = ROOT / "kaggle" / "wan_video.ipynb"
ACCELERATOR = "NvidiaTeslaT4"  # GPU T4 x2
POLL_SECONDS = 60
DONE = {"COMPLETE"}
FAILED = {"ERROR", "CANCEL_REQUESTED", "CANCEL_ACKNOWLEDGED"}
LOG_TAIL = 40
TOKEN_SETUP = (
    "1. Sign in at https://www.kaggle.com and open Settings (your profile picture > Settings).\n"
    "2. Under 'API Tokens', select 'Generate New Token' and copy it (it's shown once).\n"
    "3. Add KAGGLE_API_TOKEN=<the token> to .env in this folder (.env is gitignored).\n"
    "Kaggle may also ask you to verify your phone number before kernels can use GPUs and the internet.")


class KaggleError(Exception):
    pass


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "video"


def api_token():
    ai_visuals.load_env()
    token = os.environ.get("KAGGLE_API_TOKEN", "").strip()
    if not token:
        raise SystemExit(f"--kaggle-video needs KAGGLE_API_TOKEN.\n{TOKEN_SETUP}")
    return token


class KaggleCLI:
    """Runs `python -m kaggle ...` with the token in the child's environment, and scrubs it from output."""

    def __init__(self, token):
        self.token = token

    def scrub(self, text):
        return (text or "").replace(self.token, "***") if self.token else text or ""

    def run(self, *args, check=True, timeout=None):
        env = {**os.environ, "KAGGLE_API_TOKEN": self.token, "PYTHONIOENCODING": "utf-8"}
        env.pop("KAGGLE_KEY", None)  # make sure the new-style token is what's used
        try:
            r = subprocess.run([sys.executable, "-m", "kaggle", *map(str, args)], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", env=env, timeout=timeout)
        except subprocess.TimeoutExpired:
            if check:
                raise KaggleError(f"kaggle {' '.join(map(str, args[:2]))} timed out after {timeout}s") from None
            return ""
        out = self.scrub(r.stdout) + self.scrub(r.stderr)
        if r.returncode != 0 and check:
            if re.search(r"401|403|unauthori[sz]ed|invalid.*token|not authenticated|credentials", out, re.I):
                raise SystemExit("Kaggle rejected KAGGLE_API_TOKEN (it may be mistyped, expired or revoked).\n"
                                 f"{TOKEN_SETUP}")
            raise KaggleError(f"kaggle {' '.join(map(str, args[:2]))} failed:\n{out.strip()[-1500:]}")
        return out

    def follow_logs(self, kernel):
        """Stream the running session's log (`kaggle kernels logs -f`) in the background."""
        env = {**os.environ, "KAGGLE_API_TOKEN": self.token, "PYTHONIOENCODING": "utf-8"}
        env.pop("KAGGLE_KEY", None)
        proc = subprocess.Popen([sys.executable, "-m", "kaggle", "kernels", "logs", kernel, "-f"],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace", env=env)
        return LogFollower(proc, self.scrub)


class LogFollower:
    """Keeps the latest progress line from a live log stream; tqdm bars arrive as \\r-separated updates."""

    def __init__(self, proc, scrub=lambda s: s):
        import threading
        self.proc, self.scrub, self.latest = proc, scrub, ""
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        for raw in self.proc.stdout:
            for piece in raw.split("\r"):
                line = self.scrub(piece).strip()
                if PROGRESS.search(line):
                    self.latest = line[:160]

    def alive(self):
        return self.proc.poll() is None

    def stop(self):
        if self.alive():
            self.proc.terminate()


def username(cli):
    out = cli.run("config", "view")
    m = re.search(r"username:\s*(\S+)", out)
    if not m or m.group(1).lower() == "none":
        raise SystemExit(f"Couldn't confirm the Kaggle account for KAGGLE_API_TOKEN.\n{TOKEN_SETUP}")
    return m.group(1)


def my_datasets(cli):
    """Refs (owner/slug) of the account's datasets."""
    out = cli.run("datasets", "list", "--mine", "--page-size", "200", "--format", "json")
    start = out.find("[")
    try:
        return [d["ref"] for d in json.loads(out[start:])] if start >= 0 else []
    except (ValueError, KeyError, TypeError):
        return re.findall(r"^(\S+/\S+)", out, re.M)


def pick_dataset(user, name, existing):
    """Reuse a dataset already made for this job (including a hand-uploaded '<name>_job'), else a new ref.
    Returns (ref, exists, other matching refs)."""
    candidates = [f"{user}/{slugify(name)}-video-job", f"{user}/{slugify(name)}-job",
                  f"{user}/{name.lower()}_job", f"{user}/{name.lower()}-job"]
    lower = {r.lower(): r for r in existing}
    found = list(dict.fromkeys(lower[c.lower()] for c in candidates if c.lower() in lower))
    if found:
        return found[0], True, found[1:]
    return candidates[0], False, []


def upload_dataset(cli, ref, exists, job_dir, title):
    meta = {"title": title, "id": ref, "licenses": [{"name": "CC0-1.0"}]}
    (job_dir / "dataset-metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    if exists:
        print(f"Updating dataset {ref} (new version, old versions deleted)...")
        cli.run("datasets", "version", "-p", job_dir, "-m",
                f"make_video.py job {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC", "-d", "-q")
    else:
        print(f"Creating private dataset {ref}...")
        cli.run("datasets", "create", "-p", job_dir, "-q")


def wait_dataset_ready(cli, ref, sleep=time.sleep, interval=10, limit=600):
    waited = 0
    while True:
        out = cli.run("datasets", "status", ref, check=False).lower()
        if "ready" in out:
            return
        if "error" in out or waited >= limit:
            raise KaggleError(f"dataset {ref} isn't ready ({out.strip()[-200:]})")
        sleep(interval)
        waited += interval


def kernel_metadata(user, name, dataset_ref):
    slug = f"{slugify(name)}-wan-video"
    return {"id": f"{user}/{slug}", "title": slug, "code_file": NOTEBOOK.name, "language": "python",
            "kernel_type": "notebook", "is_private": True, "enable_gpu": True, "enable_internet": True,
            "machine_shape": ACCELERATOR, "dataset_sources": [dataset_ref], "competition_sources": [],
            "kernel_sources": [], "model_sources": []}


def push_kernel(cli, meta, folder, timeout_s):
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    shutil.copy(NOTEBOOK, folder / NOTEBOOK.name)
    (folder / "kernel-metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Pushing private kernel {meta['id']} (GPU T4 x2, internet on, stops after {timeout_s / 3600:.1f} h at most)...")
    out = cli.run("kernels", "push", "-p", folder, "--accelerator", ACCELERATOR, "-t", int(timeout_s))
    # the CLI reports push problems as text, sometimes with exit code 0
    if re.search(r"push error|not valid dataset sources", out, re.I):
        raise KaggleError(f"kernel push failed:\n{out.strip()[-1500:]}")
    m = re.search(r"Kernel version (\d+) successfully pushed", out)
    # status/logs/output for this exact version, never an earlier run's result
    return f"{meta['id']}/{m.group(1)}" if m else meta["id"]


def kernel_status(cli, kernel):
    out = cli.run("kernels", "status", kernel)
    m = re.search(r'has status "([^"]+)"', out)
    status = (m.group(1) if m else "UNKNOWN").split(".")[-1].upper()
    fm = re.search(r'Failure message: "([^"]*)"', out)
    return status, fm.group(1) if fm else ""


def log_lines(text):
    """Kaggle logs are JSON lists of {"data": ...} entries, or plain text."""
    text = (text or "").strip()
    try:
        data = json.loads(text[text.find("["):]) if "[" in text else None
        if isinstance(data, list):
            text = "".join(str(e.get("data", "")) for e in data if isinstance(e, dict))
    except ValueError:
        pass
    return [l for l in text.splitlines() if l.strip()]


# notebook milestones, FALLBACK notices and diffusers' step bars ("12/30 [10:21<15:32"), the last shown with the status
PROGRESS = re.compile(r"clip by|image:|Loading|Clips:|Images:|FALLBACK|failed|remaining|done already|encoded|"
                      r"\d+/\d+ \[\d", re.I)


def poll(cli, kernel, interval=POLL_SECONDS, sleep=time.sleep, clock=time.monotonic):
    """Print status, elapsed time and the latest live log line every `interval` seconds until the run ends.
    Returns (status, message, seconds)."""
    start, last, follower = clock(), None, None
    try:
        while True:
            status, message = kernel_status(cli, kernel)
            elapsed = clock() - start
            if status in DONE | FAILED:
                print(f"  [{elapsed / 60:5.1f} min] {status.lower()}{': ' + message if message else ''}")
                return status, message, elapsed
            if status == "RUNNING" and (follower is None or not follower.alive()):
                note_before = follower.latest if follower else ""
                follower = cli.follow_logs(kernel)  # (re)attach; the server replays the log from the start
                follower.latest = follower.latest or note_before
            note = follower.latest if follower else ""
            line = f"  [{elapsed / 60:5.1f} min] {status.lower()}{' - ' + note if note else ''}"
            if line != last:
                print(line, flush=True)
                last = line
            sleep(interval)
    finally:
        if follower:
            follower.stop()


def download_output(cli, kernel, dest):
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    cli.run("kernels", "output", kernel, "-p", dest, "-o", "-q", check=False)
    return dest


def show_log_tail(run_dir, cli, kernel):
    logs = sorted(run_dir.glob("*.log"))
    text = logs[0].read_text(encoding="utf-8", errors="replace") if logs else cli.run("kernels", "logs", kernel, check=False)
    lines = log_lines(text)
    print(f"--- last {min(LOG_TAIL, len(lines))} lines of the Kaggle log ---")
    for l in lines[-LOG_TAIL:]:
        print("  " + l[:300])
    print("---")


# ---------- collected clips (kept across runs) ----------

def read_manifest(folder):
    p = folder / "manifest.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"items": []}
    except ValueError:
        return {"items": []}


def merge_output(run_dir, collected):
    """Merge one run's outputs (clips.zip or loose files + manifest.json) into `collected`. Returns new item count."""
    src = run_dir
    if (run_dir / "clips.zip").exists():
        src = run_dir / "_unzipped"
        shutil.rmtree(src, ignore_errors=True)
        with zipfile.ZipFile(run_dir / "clips.zip") as z:
            z.extractall(src)
    new = read_manifest(src)
    merged = read_manifest(collected)
    collected.mkdir(parents=True, exist_ok=True)
    added = 0
    for item in new.get("items", []):
        f = src / item["file"]
        if not f.is_file():
            continue
        (collected / item["file"]).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(f, collected / item["file"])
        merged["items"] = [i for i in merged["items"] if i["file"] != item["file"]] + [item]
        added += 1
    (collected / "manifest.json").write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return added


def add_previous(job_dir, collected):
    """Put finished clips/images into the dataset as flat prev_* files; the notebook restores and skips them."""
    for old in job_dir.glob("prev_*"):
        old.unlink()
    manifest = read_manifest(collected)
    items = [i for i in manifest["items"] if (collected / i["file"]).is_file()]
    for i in items:
        shutil.copy(collected / i["file"], job_dir / ("prev_" + i["file"].replace("/", "__")))
    if items:
        (job_dir / "prev_manifest.json").write_text(json.dumps({"items": items}, indent=2), encoding="utf-8")
    return len(items)


def missing_clips(job_dir, collected):
    job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    have = {i["scene"] for i in read_manifest(collected)["items"] if i.get("kind") == "video"}
    return [e["scene"] for e in job["scenes"] if e["task"] == "video" and e["scene"] not in have]


def default_hours(job_dir):
    """Rough cap: model downloads plus ~1.5 hours per clip (offloading is slow, and a fallback may redo a clip),
    within Kaggle's 12-hour session limit."""
    job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    clips = sum(e["task"] == "video" for e in job["scenes"])
    images = sum(not e.get("image") for e in job["scenes"])
    return min(12.0, 0.75 + clips * 1.5 + images * 0.05)


def session_minutes(run_dir):
    """Length of the Kaggle session from its own log timestamps (seconds since start), or None."""
    for log in run_dir.glob("*.log"):
        text = log.read_text(encoding="utf-8", errors="replace")
        try:
            entries = json.loads(text[text.find("["):])
            times = [e["time"] for e in entries if isinstance(e, dict) and isinstance(e.get("time"), (int, float))]
        except (ValueError, TypeError):
            continue
        if times:
            return max(times) / 60
    return None


def print_clip_summary(run_dir):
    """Which model made each clip, how long it took, peak GPU memory, and why any fallback happened."""
    src = run_dir / "_unzipped" if (run_dir / "_unzipped").exists() else run_dir
    for i in sorted(read_manifest(src).get("items", []), key=lambda i: i["scene"]):
        if i.get("kind") != "video":
            continue
        print(f"  scene {i['scene']}: {i.get('model')} {i.get('width')}x{i.get('height')}, {i.get('seconds')}s clip "
              f"in {i.get('minutes')} min, peak GPU memory {i.get('peak_gpu_gb')} GB")
        if i.get("fallback_reason"):
            print(f"    FALLBACK because {i['fallback_reason']}")


# ---------- orchestration ----------

def run(scenes, base, providers, style="", resume=False, hours=None, cli=None, sleep=time.sleep,
        clock=time.monotonic, interval=POLL_SECONDS, video_size="960x544", characters=None):
    """Export, upload, run and collect. Returns the folder to pass to --clips, or None if clips are missing."""
    work = base.with_name(base.name + "_kaggle")
    collected, state_file = work / "collected", work / "state.json"
    job_dir = base.with_name(base.name + "_video_job")
    cli = cli or KaggleCLI(api_token())
    user = username(cli)
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}

    kernel = state.get("kernel")
    try:
        status = kernel_status(cli, kernel)[0] if kernel else None
    except KaggleError:
        status = None  # the kernel was deleted on the website
    if status in ("QUEUED", "RUNNING") and not resume:
        raise SystemExit(f"An earlier Kaggle run ({kernel}) is still {status.lower()}. Add --kaggle-resume to wait "
                         "for it and collect its clips, rather than starting a second run on the same GPU quota.")
    if status in ("QUEUED", "RUNNING"):
        print(f"Kernel {kernel} is still {status.lower()}: waiting for it instead of starting a new run.")
    else:
        if not resume:
            shutil.rmtree(collected, ignore_errors=True)  # a fresh job starts with no clips
        if resume and status in DONE | FAILED:  # an earlier run ended while we weren't watching: keep its clips
            merge_output(download_output(cli, kernel, work / "last_run"), collected)
        if not (resume and (job_dir / "job.json").exists()):
            video_job.export_job(scenes, base, providers, style=style, video_size=video_size, characters=characters)
        todo = missing_clips(job_dir, collected)
        if resume and not todo:
            print("All clips are already downloaded; nothing to run on Kaggle.")
            return collected
        if (n := add_previous(job_dir, collected)):
            print(f"Resuming: {n} finished file(s) go back to Kaggle and won't be redone.")
        ref, exists, others = pick_dataset(user, base.name, my_datasets(cli))
        if others:
            print(f"Note: also found {', '.join(others)} for this job; reusing {ref} only.")
        upload_dataset(cli, ref, exists, job_dir, f"{base.name} video job")
        wait_dataset_ready(cli, ref, sleep=sleep)
        meta = kernel_metadata(user, base.name, ref)
        timeout_s = (hours or default_hours(job_dir)) * 3600
        kernel = push_kernel(cli, meta, work / "kernel", timeout_s)
        state = {"kernel": kernel, "dataset": ref, "pushed": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        work.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        print(f"Polling every {interval // 60 or 1} min. Ctrl+C stops watching, not the run; "
              "continue later with --kaggle-resume.")

    status, message, seconds = poll(cli, kernel, interval=interval, sleep=sleep, clock=clock)
    run_dir = download_output(cli, kernel, work / "last_run")
    added = merge_output(run_dir, collected)
    used = session_minutes(run_dir)
    if used is not None:
        print(f"Downloaded {added} file(s) from this run. GPU time used: about {used:.0f} min "
              "(session length from Kaggle's log, which is what counts against the weekly quota).")
    else:  # how long we watched; a sleeping PC or a late start makes this longer than the session
        print(f"Downloaded {added} file(s) from this run. GPU time used: at most about {seconds / 60:.0f} min "
              "(how long this run was watched; Kaggle's log had no timestamps).")
    print_clip_summary(run_dir)
    todo = missing_clips(job_dir, collected)
    if status in FAILED or todo:
        show_log_tail(run_dir, cli, kernel)
        done = len(read_manifest(collected)["items"])
        print(f"The Kaggle run {'failed' if status in FAILED else 'ended'} with clip(s) for scene(s) "
              f"{', '.join(map(str, todo)) or 'none'} still missing. {done} finished file(s) are kept in {collected}.\n"
              "Continue with the same command plus --kaggle-resume (finished clips aren't redone), "
              f"or render now with --clips \"{collected}\".")
        return None
    return collected
