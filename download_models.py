"""Download the Kokoro offline voice model into models/ (needed for --engine kokoro).

Usage:
  .venv\\Scripts\\python download_models.py
"""
import sys
import urllib.request
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent / "models"
RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
MODEL_FILES = ["kokoro-v1.0.onnx", "voices-v1.0.bin"]


def download(url, dest):
    part = dest.with_name(dest.name + ".part")

    def progress(blocks, block_size, total):
        if total > 0:
            pct = min(blocks * block_size / total, 1) * 100
            print(f"\r  {dest.name}: {pct:5.1f}% of {total / 1e6:.0f} MB", end="", flush=True)

    urllib.request.urlretrieve(url, part, progress)
    part.replace(dest)
    print()


def main():
    MODELS_DIR.mkdir(exist_ok=True)
    for name in MODEL_FILES:
        dest = MODELS_DIR / name
        if dest.exists() and dest.stat().st_size > 0:
            print(f"  {name}: already present")
            continue
        try:
            download(f"{RELEASE}/{name}", dest)
        except OSError as e:
            sys.exit(f"\nCould not download {name}: {e}")
    print(f"Models ready in {MODELS_DIR}")


if __name__ == "__main__":
    main()
