# AI Video Editor

Turn a text script into narrated, captioned videos ready for social media. One run makes both:

| File | Size | For |
|---|---|---|
| `Exports/<name>_horizontal.mp4` | 1920x1080 | YouTube, Facebook |
| `Exports/<name>_vertical.mp4` | 1080x1920 | Instagram Reels, TikTok, YouTube Shorts |
| `Exports/<name>.srt` | | Caption file (same timings for both videos) |

The voice over and captions are generated once and shared by both videos.

## Setup
1. Install **FFmpeg** and make sure `ffmpeg` and `ffprobe` are on your PATH
   (Windows: `winget install Gyan.FFmpeg`, macOS: `brew install ffmpeg`, Linux: `apt install ffmpeg`).
2. Create the Python environment and install the packages:
   ```
   python -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt
   ```
3. Optional, for the offline voice (`--engine kokoro`): download the Kokoro model (~340 MB, once):
   ```
   .venv\Scripts\python download_models.py
   ```
   The first run also downloads the Whisper caption model (~140 MB) automatically.

4. Optional, for AI-generated scene images (`--generate-visuals`): see [AI-generated visuals](#ai-generated-visuals).

For hand editing, [Shotcut](https://shotcut.org) (`winget install Meltytech.Shotcut`) works well with the exported MP4s and `.srt`.

## Writing a script
Save a `.txt` file in `Scripts/`. The recommended format is **one paragraph per scene**, separated by blank lines,
with 2–3 spoken sentences each and 15–20 paragraphs in all:

```
# character: Ama = a 10-year-old girl with short braids, a yellow dress and red sandals
# character: Grandpa Kofi = an old man with a white beard, a blue kaftan and a walking stick

[video] Ama runs across the root bridge. The roots creak under her feet.

Grandpa Kofi watches from the riverbank. He smiles and waves his stick.

[visual: market.jpg] The market is busy today. Everyone has come to see the bridge.
```

- **`[video]`** at the start of a paragraph, on the same line as the narration or on its own line, makes that scene an AI video clip
  (see [AI video clips](#ai-video-clips-kaggle)). Aim for 4–5 action scenes.
- **`[visual: file]`** at the start of a paragraph uses your own footage or image, from `Raw Footage/` or next to the script.
  A scene with `[visual:]` is never animated. Missing files are reported before any audio is made.
- **Tags are never spoken or captioned.** They're recognised only at the start of a line, in any upper or lower case, and both can be combined:
  `[video] [visual: x.png] Text`. A bracket anywhere else stays in the narration, and `--check` flags it.
- **`# character: Name = description`** lines (also `Name: description` or `Name - description`) declare a character sheet
  for consistent AI images (see [Character consistency](#character-consistency)). They aren't spoken.
- **`#` description lines** inside a paragraph, labelled `Visuals:`, `Visual:`, `Shot:` or `Description:`
  (e.g. `# HACK 1 NIGERIA - Visuals: hands placing a small pot inside a larger one`), say what that scene should show.
  They're never spoken or captioned, but they're the main input to the scene's **image prompt** and, for `[video]` scenes,
  its **motion prompt**. If a description is added or edited, that scene's saved prompt is written again.
- **Every other line starting with `#` is a comment**, wherever it is (above, inside or between paragraphs, indented or not).
  Comments are never spoken, captioned or used in AI prompts. `--check` lists the comments it skipped.
- Without `--generate-visuals`, scenes with no `[visual:]` use the files in `Raw Footage/` in alphabetical order, or a text card if it's empty.
- Short clips loop to fill their scene, and still images get a slow zoom.
- Captions use your script's spelling (names, brands), with timings taken from the narration.

### Check a script before rendering
```
.venv\Scripts\python make_video.py Scripts\my_video.txt --check
```
`--check` reports scene count, words and estimated seconds per scene (from the voice engine and `--rate`), which scenes are video,
which use your own files, and every `#` comment line it skipped. It flags:
- a `# character:` line without `=`, `:` or ` - ` between name and description (it would be skipped as a comment);
- paragraphs over 60 words, or with more than 3 sentences once they pass 40 words;
- missing `[visual:]` files;
- more than 6 video scenes;
- scenes with both `[video]` and `[visual:]`;
- stray `[tags]` that would be spoken.

It also estimates Cloudflare neurons and Kaggle GPU time, counting saved prompts and cached images as free. It uses no network,
audio or FFmpeg, and changes nothing.

### One command for everything
```
.venv\Scripts\python make_video.py Scripts\my_video.txt --kaggle-video
```
`[video]` scenes become Kaggle clips, every other scene without `[visual:]` gets an AI image, and both final videos are rendered
locally. With no `[video]` scenes it skips Kaggle and just makes the images. Run `--check` first: it lists which scenes will be video,
image or your own file.

### Your defaults (`make_video.toml`)
Options you always use go in `make_video.toml` in the project folder, written like the command-line option without the dashes:
```toml
[defaults]
style = "3D animated film style, colourful, expressive characters"
voice = "en-US-EmmaMultilingualNeural"
rate = "+5%"
```
Alternatively, put `VIDEO_STYLE=...` in `.env`, which overrides `style` in the file. Options typed on the command line always win
(`--style ""` turns the style off for one run). Unknown keys are reported, not silently ignored.

## Making the video
```
.venv\Scripts\python make_video.py Scripts\demo.txt
```

| Option | What it does |
|---|---|
| `--format horizontal` / `vertical` / `both` | Which videos to render (default `both`) |
| `-o Exports\promo` | Base name for outputs, giving `promo_horizontal.mp4`, `promo_vertical.mp4` and `promo.srt` |
| `--engine kokoro` | Offline voice instead of Microsoft's online voices |
| `--voice en-US-GuyNeural` | Choose a voice (defaults: `en-US-AriaNeural` online, `af_heart` offline) |
| `--rate +10%` | Speak faster (or `-10%` slower) |
| `--music "Raw Footage\song.mp3"` | Background music. It drops while the voice speaks and fades out over the last 2 seconds |
| `--music-volume 0.2` | Music level between sentences |
| `--font path\to\font.ttf` | Font for captions and title cards (default: bundled Noto Sans Bold) |
| `--generate-visuals` | AI images for scenes without a `[visual:]` line (see below) |
| `--style "cinematic, warm tones"` | Added to every AI image prompt |
| `--prompts-only` | Write the AI image prompts for review, then stop (no images, audio or video) |
| `--regenerate` | Ignore cached AI images and saved prompts for this run (your edited prompts are rewritten) |
| `--new-images` | Keep the saved prompts but draw every image again; the new ones replace the cached ones |
| `--image-provider auto` | Where AI images come from: `auto` (default), `cloudflare`, `pollinations` or `kaggle` (see below) |
| `--image-model @cf/...` | A different Workers AI image model (default `@cf/black-forest-labs/flux-1-schnell`) |
| `--text-model @cf/...` | A different Workers AI model for writing prompts (default `@cf/meta/llama-3.1-8b-instruct-fp8`) |
| `--video-scenes "3,7,12"` | Scenes to make into AI video clips, the same as a `[video]` line |
| `--export-video-job` | Write `Exports/<name>_video_job.zip` for the Kaggle notebook, then stop |
| `--clips clips.zip` | Use the clips (and any images) downloaded from the Kaggle notebook |
| `--clip-fill pingpong` | How a short clip fills a longer scene: `pingpong` (default), `slowmo` or `freeze` |
| `--kaggle-video` | Do the whole Kaggle step automatically: export, upload, run on a private GPU kernel, download, render |
| `--kaggle-resume` | Continue an interrupted `--kaggle-video` run without redoing finished clips |
| `--kaggle-hours 3` | Hard time limit for each Kaggle run (default about 45 min plus 1 h per clip, at most 12) |
| `--check` | Check the script and estimate length, neurons and GPU time, then stop |
| `--no-captions` | Don't burn in captions (the `.srt` is still useful for YouTube's caption upload) |
| `--whisper-model small` | More accurate caption timing, but slower |
| `--caption-words 4` | Fewer words per caption line |

`--vertical` still works as a deprecated alias for `--format vertical`.

Examples:
```
.venv\Scripts\python make_video.py Scripts\demo.txt --format vertical --music "Raw Footage\song.mp3"
.venv\Scripts\python make_video.py Scripts\demo.txt --engine kokoro --voice am_michael -o Exports\demo_offline
```

Voices: run `.venv\Scripts\edge-tts --list-voices` for the online ones (e.g. `en-US-AndrewNeural`, `en-GB-SoniaNeural`).
Offline Kokoro voices include `af_heart`, `af_bella`, `am_michael`, `bf_emma` and `bm_george`.

## AI-generated visuals
`--generate-visuals` draws an image for every scene that has no `[visual:]` line. Scenes with a `[visual:]` line always keep your file.
With this option, `Raw Footage/` is **not** used to fill the other scenes.

`--image-provider` picks where images come from:

| Provider | What it uses | Needs |
|---|---|---|
| `auto` (default) | Cloudflare first. If Cloudflare's daily allowance runs out, it stops calling Cloudflare and uses Pollinations for the rest of the run. If both fail, the scene gets a title card | Cloudflare credentials and/or a Pollinations key |
| `cloudflare` | FLUX.1 schnell on [Cloudflare Workers AI](https://developers.cloudflare.com/workers-ai/) | Cloudflare credentials |
| `pollinations` | FLUX.1 schnell on [Pollinations](https://pollinations.ai) | `POLLINATIONS_API_KEY` |
| `kaggle` | SDXL in the Kaggle notebook. Nothing is generated online: export a job, run the notebook, and import with `--clips` | A Kaggle account |

All providers make square 1024x1024 images and share the `generated/` cache. A cached image from any provider is reused, and the log shows
which provider made each image (also recorded in `prompts.json`). Prompts are written by Cloudflare's text model whenever Cloudflare credentials
are set, whatever the image provider. Without them, or after the daily allowance runs out, a simple prompt is built from the scene text.

**Pollinations setup:** create a free secret key (it starts with `sk_`) at [enter.pollinations.ai](https://enter.pollinations.ai) and add
`POLLINATIONS_API_KEY=...` to `.env`. Its FLUX model costs 0.002 "pollen" per image from the free allowance. The current API requires a key. Its
docs no longer mention the old `nologo` option or a watermark, so check the first few images it makes.

```
.venv\Scripts\python make_video.py Scripts\my_video.txt --generate-visuals --style "cinematic documentary, natural light"
```

### One-time Cloudflare setup
1. Create a free account at [dash.cloudflare.com](https://dash.cloudflare.com).
2. Go to **AI > Workers AI** and select **Use REST API**.
3. Copy the **Account ID**.
4. Select **Create a Workers AI API Token**, then create and copy it. The token needs Workers AI Read and Edit permissions; the template sets both.
5. Copy `.env.example` to `.env` and paste in both values. `.env` is gitignored, so don't commit it or share it.
   You can set `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` as environment variables instead.

### How it works
1. **Prompts.** A small text model (`@cf/meta/llama-3.1-8b-instruct-fp8`) turns each scene's narration into an image prompt
   describing the subject, setting, lighting, camera angle and mood. It is told to leave out text, logos, watermarks and real people's names.
   The previous scene's prompt is passed along so visuals flow from one scene to the next. If the text model fails, a simple prompt is built from the scene text.
2. **Images.** FLUX.1 schnell (`@cf/black-forest-labs/flux-1-schnell`) draws each image. Its Workers AI API has no width or height setting,
   so it makes **one square image per scene**. The normal crop fits it to both 16:9 and 9:16, and the prompt asks for a centred subject so
   it survives both crops. Expect some loss at the edges, especially in the vertical video.
3. **Cache.** Images are saved in `generated/`, named by a hash of the prompt, size and model. Reruns reuse them and don't spend your quota.
   `--regenerate` forces new prompts and images.
4. **Failures.** Network errors and rate limits are retried 3 times. A scene that still fails gets a title card, and the run carries on;
   failed scenes are listed at the end. Rerun later to fill them in, since images that did succeed are cached.

### Reviewing and editing prompts
The prompt writer sees each scene's `Visuals:` description line (which decides what the image shows), its narration, the style,
the character sheet and the previous scene's prompt. Plain `#` comments are never sent. To steer a shot, edit its description
line in the script, or its prompt in `prompts.json`. Review the prompts before spending image quota:
```
.venv\Scripts\python make_video.py Scripts\my_video.txt --prompts-only
```
This writes the prompts and stops, using only the text model. Check how people, places and clothing are described. Remove anything a
single still image can't show, such as "time-lapse", "fast cuts" or animations, and anything that would render as text. Then run
again with `--generate-visuals` and the same `-o`.

Prompts are saved to `Exports/<name>_prompts.json`:
```json
{"scenes": [{"scene": 4, "narration": "In northern Nigeria, heat can...", "prompt": "A busy market in a dry Sahel village..."}]}
```
Edit any `"prompt"` and rerun the same command. Your edited prompts are reused instead of being rewritten, and only scenes whose prompt changed get new images.
Prompts are matched to scenes by their narration, so if you change a scene's narration, that scene gets a fresh prompt. `--style` is added when the
image is made, not stored in the file, so changing the style regenerates the images but keeps your prompts.

### Character consistency
Image models draw each scene separately, so the same character can look different from scene to scene. To keep them consistent:
1. **Declare them** with `# character: Name = description` lines in the script: age, build, hair, clothing and colours.
2. Every image prompt gets the **character sheet**, and the prompt writer is told to describe those characters exactly as listed.
3. The prompt writer also lists the recurring characters it drew. A character described for the first time is **added to the sheet**
   and used in every later scene. Existing entries never change, and your declarations always win.
4. When a scene's narration names a character, using any distinctive word of the name ("Kofi" or "Grandpa" for "Grandpa Kofi"), their
   description is **added to that scene's image prompt** if the prompt doesn't already contain it. Only the description is added,
   never the name, so real people's names stay out of images.

The sheet is saved under `"characters"` in `Exports/<name>_prompts.json`; edit it there like the prompts. Image models still vary a little,
so keep descriptions short, concrete and identical across scenes: "yellow dress, red sandals", not "brightly dressed".

### Style
The style (from `--style`, `make_video.toml` or `VIDEO_STYLE`) is added to every image prompt, and it's also given to the prompt writer.
So with an animated style, prompts are written for animation rather than as photographs.

### Free allowance
Workers AI's free plan includes **10,000 neurons per day**. Cloudflare can change this, so check the
[pricing page](https://developers.cloudflare.com/workers-ai/platform/pricing/).
At the published rates, a 1024x1024 FLUX.1 schnell image at 4 steps costs **57.6 neurons**, and each prompt from the 8B text model about **5–7 neurons**.
That makes a 20-scene video roughly 1,300 neurons the first time, and almost nothing on cached reruns. Each run prints its estimated usage.
Past the free allowance, Cloudflare refuses requests until the next day. With `--image-provider auto` the run switches to Pollinations if you have a
key; otherwise those scenes get title cards.

AI images of realistic scenes may need to be labelled as synthetic content on some platforms, for example YouTube's "altered or synthetic content" setting.

## AI video clips (Kaggle)
Selected scenes can become short AI video clips, made with **Wan 2.2** on a free Kaggle GPU (2x T4). The clip starts from the scene's image,
so it matches the stills around it. Everything else stays the same.

### One command (recommended)
```
.venv\Scripts\python make_video.py Scripts\my_video.txt --kaggle-video --style "..."
```
This uses the official Kaggle CLI, so you never open the Kaggle website:
1. Exports the job, as `--export-video-job` does.
2. Creates a **private** dataset with the job files, or adds a new version to the one already made for this job. A dataset you uploaded by hand
   as `<name>_job` / `<name>-job` is reused, so no duplicates are left. Old versions are deleted.
3. Pushes `kaggle/wan_video.ipynb` as a **private** kernel (`<name>-wan-video`) with **GPU T4 x2** and **internet** on, and the dataset attached.
4. Checks the run **every minute**, printing its status, the elapsed time and the notebook's latest progress line,
   streamed live with `kaggle kernels logs -f` (e.g. "encoded 1 prompt(s) on cuda:1…", "scene 1 clip by Wan2.2-TI2V-5B…", or a FALLBACK notice).
5. Downloads the outputs, merges them into `Exports/<name>_kaggle/collected/`, and imports them exactly like `--clips`.
6. Renders the horizontal and vertical videos, and prints the GPU time used.

**Setup, once:** on Kaggle, go to **Settings > API Tokens > Generate New Token** and add `KAGGLE_API_TOKEN=...` to `.env`. This is the new-style
token, not the legacy `kaggle.json`. The token is only given to the Kaggle CLI through its environment; it's never printed, logged or written to a file.
Kaggle may ask you to verify your phone number before kernels can use GPUs and the internet.

**Saving GPU hours:** Kaggle batch runs stop by themselves when the notebook ends, and every run also gets a hard time limit (`--kaggle-hours`),
so a stuck run can't use up the ~30 free GPU hours a week. The GPU time printed at the end comes from the session's own log timestamps,
which is what Kaggle counts, so it stays right even if your PC slept while watching. The same summary lists which model made each clip, its size,
its time and its peak GPU memory.
Pressing Ctrl+C stops *watching*, not the run; the CLI has no command to cancel a run.

**Failures and resuming:** if a run fails or hits its time limit, the end of the Kaggle log is printed, and any clips that finished are kept in
`Exports/<name>_kaggle/collected/`. Run the same command with `--kaggle-resume`:
- If the previous run is still going, it waits for it.
- Otherwise it uploads the finished clips with the job, so the notebook skips them, and runs only what's missing.
- If every clip is already there, it goes straight to rendering.

You can also render with the clips you have: `--clips Exports\<name>_kaggle\collected`.

### Step by step (manual)
1. **Mark scenes.** Add a `[video]` line to each scene you want animated, or pass `--video-scenes "3,7,12"`. Pick a few key moments,
   since each clip takes a long time to make on a T4 (see timing below).
2. **Export the job:**
   ```
   .venv\Scripts\python make_video.py Scripts\my_video.txt --export-video-job --style "..."
   ```
   This makes (or reuses from the cache) each marked scene's image, writes a **motion prompt** for it (e.g. "slow push-in, leaves moving in the wind"),
   and creates `Exports/my_video_video_job.zip`. To change a motion, edit `"motion_prompt"` in `Exports/my_video_video_job/job.json` and export again;
   your edits are kept, as with `prompts.json`. Use the same `--style` you'll render with, so the images match.
3. **Upload the zip to Kaggle as a dataset:** [kaggle.com](https://www.kaggle.com), then **Create > New Dataset**, upload the zip, and name it, e.g. `my-video-job`.
   Kaggle unpacks it. Upload a new version of the dataset whenever you re-export.
4. **Open the notebook:** **Create > New Notebook**, then **File > Import Notebook** and choose `kaggle/wan_video.ipynb`.
5. **Settings** (right-hand panel): **Accelerator: GPU T4 x2**, **Internet: On**, and **Add Input** the dataset from step 3.
   Kaggle may ask you to verify your phone number before it enables GPUs and Internet.
6. **Run All.** The first run downloads the models (about 30–40 GB). The notebook prints the time per clip and the estimated time remaining.
7. **Download `clips.zip`** from the notebook's **Output** tab (`/kaggle/working/clips.zip`).
8. **Stop the session** (power icon, top right). Kaggle gives about **30 free GPU hours a week**, and an idle session still uses them.
9. **Render:**
   ```
   .venv\Scripts\python make_video.py Scripts\my_video.txt --generate-visuals --clips clips.zip --style "..."
   ```
   Clips are matched to scenes by their narration (falling back to scene number), using the notebook's `manifest.json`. Scenes without a clip
   keep their still image with the slow zoom. Cached images mean this spends no image quota.

If a Kaggle session times out, start it again and Run All. Clips already made are skipped, and `clips.zip` is rebuilt after every clip.

### Models, sizes and timing
- **Wan 2.2 TI2V-5B**, image to video: **960x544** by default (`--kaggle-size 1280x704` for full 720p), 121 frames at 24 fps (about 5 s),
  30 steps (the model card uses 50). T4s have no bf16, so it runs in fp16, with the VAE in fp32 and VAE tiling on. To fit a 15 GB T4:
  - The 11 GB text encoder encodes every clip's prompt once on the second T4, then it's freed.
  - The video model uses diffusers' **leaf-level group offloading** with CUDA streams: weights stay in CPU RAM, and only the layers
    being computed are on the GPU.
- **Fallback: Wan 2.1 T2V-1.3B**, text to video, 832x480, 81 frames at 16 fps. It's used only if TI2V-5B can't load, runs out of memory,
  or returns broken (NaN or blank) frames. Because it's text-only, the clip **won't match the scene image**. The notebook log, the manifest and
  `make_video.py` all say which model made each clip and, for a fallback, why.
- **Measured on Kaggle T4 x2** (one clip, October 2026):

  | Setup | Result | Peak GPU memory | Time for the clip |
  |---|---|---|---|
  | TI2V-5B at 1280x704, split across both GPUs / whole-model CPU offload | Out of memory | GPU full | – |
  | Wan 2.1 1.3B fallback at 832x480 | Clip made (text-only) | – | 39.9 min |
  | **TI2V-5B at 960x544, group offloading** | **Clip made from the image** | **6.0 GB** (12.5 GB on the second T4 while encoding) | **36.0 min** |

  The whole 960x544 session, including installs, model downloads and encoding, took about 38 minutes of GPU time. 1280x704 with group
  offloading hasn't been tested; it has about 1.7x the pixels, so expect roughly an hour per clip.
- Clips are made one at a time on the first T4. Running one worker per GPU would need two pinned ~10 GB copies of the model in CPU RAM,
  plus working memory, and Kaggle has about 29 GB.

### How clips fill a scene
Clips are about 5 seconds, and scenes are usually longer. The clip plays once at normal speed, then `--clip-fill` decides the rest:

| `--clip-fill` | The rest of the scene |
|---|---|
| `pingpong` (default) | Plays backwards, then forwards, and so on, so there's never a jump back to the first frame |
| `slowmo` | One slow-motion backwards pass stretched to fit, up to 3x slower; longer scenes alternate direction at 3x |
| `freeze` | Holds the last frame |

Clips are upscaled with Lanczos to 1920x1080. For the vertical video, the centre of the 1280x704 clip is cropped to 9:16, which
leaves about 396x704 pixels of source, so vertical clips look softer than horizontal ones.

### Kaggle images (`--image-provider kaggle`)
To spend no online image quota at all, export with `--export-video-job --image-provider kaggle`. The job then also lists every scene that needs a still
image and has none cached. The notebook makes them with **SDXL** (fp16, with the fp16-fix VAE) before the clips. FLUX schnell isn't used on Kaggle:
even with CPU offload it needs about 34 GB of RAM in fp16. Importing with `--clips` copies the images into the `generated/` cache, so later runs reuse them.
SDXL reads only the first 77 tokens of each prompt, so put the most important details first.

## Tests
```
.venv\Scripts\python -m pip install pytest
.venv\Scripts\python -m pytest
```
The tests cover script parsing (including `[video]`/`[visual:]` tags and character lines), `--check`, `make_video.toml` defaults, the character sheet,
caption timing and alignment, AI image providers and their fallbacks, video-job export, clip import, clip-fill timing and the Kaggle runner.
All Cloudflare and Pollinations calls are mocked, so they don't need FFmpeg, a GPU or internet. They also check that `kaggle/wan_video.ipynb`
matches `kaggle/wan_video.py`. After editing the `.py`, run `.venv\Scripts\python kaggle\build_notebook.py`.

## Credits
The bundled font, `assets/fonts/NotoSans-Bold.ttf`, is licensed under the SIL Open Font License (`assets/fonts/OFL.txt`).
