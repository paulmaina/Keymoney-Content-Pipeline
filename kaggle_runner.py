"""Run a notebook on a free Kaggle GPU without the Kaggle website (used by make_video.py --upscale).

Uses the official Kaggle CLI (`python -m kaggle`) with a new-style API token from KAGGLE_API_TOKEN
in .env. The token is passed to the CLI through its environment only: it never appears on a command
line, in a file this tool writes, or in printed output.

Flow (run_notebook): create or version a private dataset from a job folder -> push the notebook as a
private GPU (T4 x2) kernel with internet and the dataset attached -> poll every minute -> download the
outputs (also when the run fails).

Kaggle batch runs stop by themselves when the notebook ends; a hard timeout caps each run so a stuck
session can't burn the weekly GPU quota. There is no CLI command to cancel a run.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

import ai_visuals

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
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "job"


def api_token(feature="--upscale"):
    ai_visuals.load_env()
    token = os.environ.get("KAGGLE_API_TOKEN", "").strip()
    if not token:
        raise SystemExit(f"{feature} needs KAGGLE_API_TOKEN.\n{TOKEN_SETUP}")
    return token


class KaggleCLI:
    """Runs `python -m kaggle ...` with the token in the child's environment, and scrubs it from output."""

    def __init__(self, token):
        self.token = token

    def scrub(self, text):
        return (text or "").replace(self.token, "***") if self.token else text or ""

    def env(self):
        env = {**os.environ, "KAGGLE_API_TOKEN": self.token, "PYTHONIOENCODING": "utf-8"}
        env.pop("KAGGLE_KEY", None)  # make sure the new-style token is what's used
        return env

    def run(self, *args, check=True, timeout=None):
        try:
            r = subprocess.run([sys.executable, "-m", "kaggle", *map(str, args)], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", env=self.env(), timeout=timeout)
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
        proc = subprocess.Popen([sys.executable, "-m", "kaggle", "kernels", "logs", kernel, "-f"],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace", env=self.env())
        return LogFollower(proc, self.scrub)


# the notebooks' milestone lines ("image 3/38: ...", "Loading", "Images:"), shown with the status
PROGRESS = re.compile(r"image \d+/\d+|Loading|Images:|failed|remaining|done", re.I)


class LogFollower:
    """Keeps the latest progress line from a live log stream; progress bars arrive as \\r-separated updates."""

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


def upload_dataset(cli, ref, exists, job_dir, title):
    meta = {"title": title, "id": ref, "licenses": [{"name": "CC0-1.0"}]}
    (job_dir / "dataset-metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    if exists:
        print(f"Updating private dataset {ref} (new version, old versions deleted)...")
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


def kernel_metadata(user, slug, notebook, dataset_ref):
    return {"id": f"{user}/{slug}", "title": slug, "code_file": notebook.name, "language": "python",
            "kernel_type": "notebook", "is_private": True, "enable_gpu": True, "enable_internet": True,
            "machine_shape": ACCELERATOR, "dataset_sources": [dataset_ref], "competition_sources": [],
            "kernel_sources": [], "model_sources": []}


def push_kernel(cli, meta, folder, timeout_s, notebook):
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    shutil.copy(notebook, folder / notebook.name)
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


def run_notebook(cli, notebook, slug, job_dir, work, hours, sleep=time.sleep, clock=time.monotonic,
                 interval=POLL_SECONDS):
    """Upload job_dir as the private dataset <user>/<slug>-job (a new version if it exists), run the notebook on it
    as the private kernel <user>/<slug>, and download its outputs into work/last_run.
    Returns (status, output folder, GPU minutes from Kaggle's log or None, minutes watched)."""
    user = username(cli)
    ref = f"{user}/{slug}-job"
    upload_dataset(cli, ref, ref.lower() in {r.lower() for r in my_datasets(cli)}, job_dir, f"{slug} job")
    wait_dataset_ready(cli, ref, sleep=sleep)
    meta = kernel_metadata(user, slug, notebook, ref)
    kernel = push_kernel(cli, meta, work / "kernel", hours * 3600, notebook)
    print(f"Polling every {interval // 60 or 1} min. Ctrl+C stops watching, not the run.")
    status, message, seconds = poll(cli, kernel, interval=interval, sleep=sleep, clock=clock)
    run_dir = download_output(cli, kernel, work / "last_run")
    if status in FAILED:
        show_log_tail(run_dir, cli, kernel)
    return status, run_dir, session_minutes(run_dir), seconds / 60
