"""--engine chatterbox: narration from Chatterbox (Resemble AI, MIT) on a free Kaggle GPU, via kaggle/chatterbox.ipynb.

Every scene's line that isn't cached yet goes to Kaggle in one batch (one private dataset version, one kernel run).
Each line is cached in generated/voice/<key>.wav, keyed by its text and every setting that changes the sound (the
reference clip's contents, emotion, cfg, model version), so reruns only send new or edited lines. If a run stops
part-way, the lines it finished are kept and the next run sends only the rest. If Kaggle fails or a line is still
missing, the whole video falls back to the Edge voice (Emma), so it never mixes two voices.

Settings (make_video.toml or the command line):
  chatterbox-voice    a reference clip (WAV/MP3, 6-15 s of clean speech) to speak in that voice; default: Chatterbox's
                      own built-in voice. Only use a voice you have the right to use: your own, or with the speaker's
                      written permission.
  chatterbox-emotion  0.25 (flat) to 2.0 (theatrical); default 0.5. Above ~0.7, a lower chatterbox-cfg (~0.3) keeps
  chatterbox-cfg      the pace natural; default 0.5.
"""
import hashlib
import json
import shutil
import time
from pathlib import Path

import ai_visuals
import kaggle_runner
import make_video as mv

VOICE_DIR = ai_visuals.GEN_DIR / "voice"
WORK = VOICE_DIR / "_kaggle"
NOTEBOOK = mv.ROOT / "kaggle" / "chatterbox.ipynb"
SLUG = "ai-video-chatterbox"
MODEL_VERSION = "chatterbox-tts==0.1.7"  # part of each line's cache key; matches the notebook's pin
DEFAULT_EMOTION, DEFAULT_CFG, TEMPERATURE = 0.5, 0.5, 0.8
FALLBACK_VOICE = "en-US-EmmaMultilingualNeural"
# hard cap per run: the model install and download, then generation (a T4 speaks roughly in real time; generous)
SETUP_HOURS, HOURS_PER_LINE, MAX_HOURS = 0.3, 0.02, 3.0


class Settings(dict):
    """exaggeration, cfg_weight, temperature and the reference clip (a Path or None)."""

    @classmethod
    def from_args(cls, args, emotion=None, cfg=None, voice=None):
        ref = voice if voice is not None else getattr(args, "chatterbox_voice", None)
        ref = Path(ref).resolve() if ref else None
        if ref and not ref.exists():
            raise SystemExit(f"chatterbox-voice: {ref} not found")
        return cls(exaggeration=float(emotion if emotion is not None else getattr(args, "chatterbox_emotion", DEFAULT_EMOTION)),
                   cfg_weight=float(cfg if cfg is not None else getattr(args, "chatterbox_cfg", DEFAULT_CFG)),
                   temperature=TEMPERATURE, reference=ref)

    def describe(self):
        voice = self["reference"].name if self["reference"] else "built-in voice"
        return f"{voice}, emotion {self['exaggeration']:g}, cfg {self['cfg_weight']:g}"


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16] if path else "builtin"


def line_key(text, settings):
    parts = [MODEL_VERSION, file_hash(settings["reference"]), f"{settings['exaggeration']:.3f}",
             f"{settings['cfg_weight']:.3f}", f"{settings['temperature']:.2f}", text.strip()]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:20]


def cached(key):
    return VOICE_DIR / f"{key}.wav"


def valid_wav(path):
    try:
        import soundfile as sf
        info = sf.info(str(path))
        return info.frames > info.samplerate * 0.2
    except Exception:  # soundfile raises its own error types for a broken file
        return False


def export_job(lines, job_dir):
    """job.json with one entry per (key, text, settings), plus each reference clip once."""
    shutil.rmtree(job_dir, ignore_errors=True)
    job_dir.mkdir(parents=True)
    refs, entries = {}, []
    for key, text, st in lines:
        ref_name = None
        if st["reference"]:
            h = file_hash(st["reference"])
            ref_name = refs.setdefault(h, f"ref_{h}{st['reference'].suffix.lower()}")
            if not (job_dir / ref_name).exists():
                shutil.copy(st["reference"], job_dir / ref_name)
        entries.append({"key": key, "text": text, "exaggeration": st["exaggeration"], "cfg_weight": st["cfg_weight"],
                        "temperature": st["temperature"], "reference": ref_name})
    (job_dir / "job.json").write_text(json.dumps({"version": 1, "model": MODEL_VERSION, "lines": entries}, indent=2,
                                                 ensure_ascii=False), encoding="utf-8")


def collect(run_dir, wanted):
    """Copy finished lines into the cache. Returns (number cached, the manifest's entries)."""
    try:
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {"lines": []}
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    got = 0
    for key in wanted:
        f = run_dir / f"{key}.wav"
        if f.is_file() and valid_wav(f):
            shutil.copy(f, cached(key))
            got += 1
    return got, manifest.get("lines", [])


def hours_cap(n):
    return min(MAX_HOURS, SETUP_HOURS + n * HOURS_PER_LINE)


def synthesize(lines, cli=None, sleep=time.sleep, clock=time.monotonic, interval=kaggle_runner.POLL_SECONDS, work=None):
    """lines: [(text, Settings)]. Generates every line that isn't cached in one Kaggle run. `work` holds the Kaggle job
    files (the video's Working/kaggle/chatterbox); the audio always goes to the shared voice cache.
    Returns ({index: wav path} for the lines that exist now, GPU minutes or None)."""
    work = Path(work) if work else WORK
    keyed = [(line_key(t, st), t, st) for t, st in lines]
    todo = list({k: (k, t, st) for k, t, st in keyed if not cached(k).exists()}.values())
    print(f"Chatterbox: {len(keyed)} line(s), {len(keyed) - sum(not cached(k).exists() for k, _, _ in keyed)} cached, "
          f"{len(todo)} to generate on Kaggle")
    used = None
    if todo:
        cli = cli or kaggle_runner.KaggleCLI(kaggle_runner.api_token("--engine chatterbox"))
        job_dir = work / "job"
        export_job(todo, job_dir)
        status, run_dir, used, watched = kaggle_runner.run_notebook(
            cli, NOTEBOOK, SLUG, job_dir, work, hours_cap(len(todo)), sleep=sleep, clock=clock, interval=interval)
        got, entries = collect(run_dir, [k for k, _, _ in todo])
        gpu = (f"about {used:.1f} min (session length from Kaggle's log)" if used is not None else
               f"at most {watched:.0f} min (how long the run was watched)")
        speech = sum(e.get("seconds", 0) for e in entries)
        print(f"  {got} of {len(todo)} line(s) generated ({speech:.0f} s of speech); GPU time used: {gpu}")
        if got < len(todo):
            print(f"  {len(todo) - got} line(s) are missing (Kaggle status {status.lower()}); finished ones are cached, "
                  "so a rerun only sends the rest.")
    return {i: cached(k) for i, (k, _, _) in enumerate(keyed) if cached(k).exists()}, used


def narrate_lines(scenes, args, **kw):
    """{scene index: wav} from Chatterbox for every scene, or None when any line is missing (the caller then uses
    the Edge fallback for the whole video)."""
    st = Settings.from_args(args)
    if st["reference"]:
        print(f"  reference voice: {st['reference'].name}. Only use a voice you have the right to use "
              "(your own, or with the speaker's permission).")
    try:
        paths, _ = synthesize([(s["text"], st) for s in scenes], **kw)
    except (kaggle_runner.KaggleError, OSError) as e:
        print(f"  Chatterbox on Kaggle failed ({e}).")
        return None
    except SystemExit as e:  # no KAGGLE_API_TOKEN, or Kaggle rejected it: the default voice still renders
        print(f"  Chatterbox can't run: {str(e).splitlines()[0]} (see the README's Kaggle setup).")
        return None
    if len(paths) < len(scenes):
        return None
    return paths


def samples(text, variants, out_dir, **kw):
    """One line spoken with several settings, in one Kaggle run, copied to out_dir. Returns the files."""
    paths, used = synthesize([(text, st) for _, st in variants], **kw)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for i, (name, st) in enumerate(variants):
        if i in paths:
            dest = out_dir / f"chatterbox_{name}.wav"
            shutil.copy(paths[i], dest)
            files.append((dest, st))
    return files, used


SAMPLE_TEXT = ("No electricity. No fridge. No running water. Yet villagers around the world keep food cold, "
               "light their homes and pull water from the air.")
SAMPLES = [("natural", 0.5, 0.5), ("calm", 0.35, 0.6), ("lively", 0.8, 0.3)]


def main():
    """Make comparison samples: Chatterbox at three settings (one Kaggle run) and the Edge voice, same line."""
    import argparse
    import asyncio
    ap = argparse.ArgumentParser(description=main.__doc__)
    ap.add_argument("--text", default=SAMPLE_TEXT)
    ap.add_argument("--voice", type=Path, help="a reference clip you have the right to use (default: built-in voice)")
    ap.add_argument("--out", type=Path, default=mv.ROOT / "Exports" / "voice_samples")
    a = ap.parse_args()
    variants = [(name, Settings.from_args(None, emotion=e, cfg=c, voice=a.voice or "")) for name, e, c in SAMPLES]
    files, used = samples(a.text, variants, a.out)
    import edge_tts
    emma = a.out / "edge_emma.mp3"
    asyncio.run(edge_tts.Communicate(a.text, FALLBACK_VOICE, rate="+5%").save(str(emma)))
    for f, st in files:
        print(f"  {f}  ({st.describe()})")
    print(f"  {emma}  (Edge Emma at +5%, your current voice)")
    if used is not None:
        print(f"GPU time: about {used:.1f} min")


if __name__ == "__main__":
    main()
