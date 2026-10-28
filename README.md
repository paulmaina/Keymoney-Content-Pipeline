# AI Video Editor

## Installed tools
| Tool | Purpose |
|---|---|
| FFmpeg 9.0.2 (winget `Gyan.FFmpeg`) | Encoding, cutting, mixing. Intel Quick Sync (`h264_qsv`) available |
| Shotcut 26.9 (winget `Meltytech.Shotcut`) | GUI timeline editor for manual editing |
| `.venv` (Python 3.14) | edge-tts, kokoro-onnx, faster-whisper, moviepy, pydub |
| `models/` | Kokoro offline voice model (~340 MB) |

## Script to video
Write a `.txt` in `Scripts/`. Each paragraph is one scene. `[visual: file.mp4]` picks footage
from `Raw Foorage/`. Lines starting with `#` are comments. Scenes with no visual use clips from the footage folder, or a text card if the folder is empty.

```
.venv\Scripts\python make_video.py Scripts\demo.txt
.venv\Scripts\python make_video.py Scripts\demo.txt --engine kokoro --voice am_michael   # offline
.venv\Scripts\python make_video.py Scripts\demo.txt --vertical --music "Raw Foorage\song.mp3"
.venv\Scripts\python make_video.py --help
```
Output goes to `Exports/<name>.mp4`, plus a `.srt` caption file you can import into Shotcut.

Voices: `.venv\Scripts\edge-tts --list-voices` (online, e.g. `en-US-GuyNeural`, `en-GB-SoniaNeural`).
Kokoro (offline): `af_heart`, `af_bella`, `am_michael`, `bf_emma`, `bm_george`, ...
