# %% [markdown]
# # Chatterbox narration for make_video.py --engine chatterbox
#
# `make_video.py --engine chatterbox` pushes this notebook to a private Kaggle kernel on its own, with a private dataset
# holding `job.json` (every narration line not generated yet, each with its own settings) and any reference voice clip.
# You don't need to open it on the website.
#
# [Chatterbox](https://github.com/resemble-ai/chatterbox) (Resemble AI, MIT licence) runs on one T4. Each line is split
# into sentences, generated with a fixed seed, and joined with a short pause, then saved as `<key>.wav` in
# `/kaggle/working` straight away, so a run that stops part-way keeps what it made. `manifest.json` lists each line's
# length and GPU seconds. Chatterbox adds Resemble's imperceptible Perth watermark to everything it generates.
#
# Chatterbox's own package pins torch 2.6 and numpy < 2, which would replace Kaggle's CUDA build, so it's installed
# without its pins and only the libraries it actually imports are added.

# %%
import subprocess
import sys

subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "--no-deps", "chatterbox-tts==0.1.7"])
subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "conformer==0.3.2", "diffusers==0.29.0",
                       "resemble-perth", "s3tokenizer", "omegaconf", "pyloudnorm", "librosa==0.11.0",
                       "transformers==5.2.0", "safetensors==0.5.3", "soundfile"])

# %%
# Find the job.
import glob
import hashlib
import json
import os
import re
import time

import numpy as np
import soundfile as sf
import torch

OUT = "/kaggle/working"
PAUSE = 0.18  # seconds of silence between sentences

jobs = sorted(glob.glob("/kaggle/input/**/job.json", recursive=True))
if not jobs:
    raise SystemExit("No job.json under /kaggle/input: attach the dataset make_video.py --engine chatterbox uploaded.")
JOB_DIR = os.path.dirname(jobs[0])
job = json.load(open(jobs[0], encoding="utf-8"))
device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"Lines: {len(job['lines'])} to speak on {device}"
      f"{' (' + torch.cuda.get_device_name(0) + ')' if device == 'cuda' else ' (no GPU: this will be slow)'}")

# %%
# Load Chatterbox (the weights come from Hugging Face, ResembleAI/chatterbox).
from chatterbox.tts import ChatterboxTTS

t0 = time.time()
print("Loading Chatterbox...")
model = ChatterboxTTS.from_pretrained(device=device)
print(f"Loading done in {time.time() - t0:.0f}s, {model.sr} Hz")


def sentences(text):
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p.strip()]
    out = []
    for p in parts:  # very short fragments ("No fridge.") sound better with their neighbour
        if out and len(out[-1]) < 24:
            out[-1] = f"{out[-1]} {p}"
        else:
            out.append(p)
    return out


def speak(line):
    ref = line.get("reference")
    ref = os.path.join(JOB_DIR, ref) if ref else None
    seed = int(hashlib.sha256(line["key"].encode()).hexdigest()[:8], 16)
    pieces = []
    for n, sentence in enumerate(sentences(line["text"])):
        torch.manual_seed(seed + n)
        wav = model.generate(sentence, audio_prompt_path=ref, exaggeration=line["exaggeration"],
                             cfg_weight=line["cfg_weight"], temperature=line.get("temperature", 0.8))
        pieces += [wav.squeeze(0).cpu().numpy(), np.zeros(int(PAUSE * model.sr), dtype=np.float32)]
    return np.concatenate(pieces[:-1]) if pieces else np.zeros(1, dtype=np.float32)


# %%
# Speak every line; manifest.json is rewritten after each, so a timeout keeps what's done.
done = []
start = time.time()
for n, line in enumerate(job["lines"], 1):
    try:
        t = time.time()
        audio = speak(line)
        if device == "cuda":
            torch.cuda.synchronize()
        sf.write(f"{OUT}/{line['key']}.wav", audio, model.sr)
        secs = time.time() - t
        done.append({"key": line["key"], "seconds": round(len(audio) / model.sr, 2), "gpu_seconds": round(secs, 1)})
        print(f"  line {n}/{len(job['lines'])}: {len(audio) / model.sr:.1f}s of speech in {secs:.1f}s")
    except Exception as e:  # one bad line shouldn't lose the rest
        print(f"  line {n}/{len(job['lines'])} failed: {e.__class__.__name__}: {e}")
    json.dump({"lines": done, "sample_rate": model.sr}, open(f"{OUT}/manifest.json", "w"), indent=1)

print(f"Lines: {len(done)} of {len(job['lines'])} done in {(time.time() - start) / 60:.1f} min")
