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
Save a `.txt` file in `Scripts/`. Each paragraph (separated by a blank line) is one scene.

```
# Lines starting with # are notes and aren't read aloud.
Welcome to the channel. Today we're looking at three easy recipes.

[visual: kitchen.mp4]
First up, a five-minute breakfast.

[visual: pancakes.jpg]
These pancakes need only four ingredients.
```

- `[visual: file]` picks the footage or image for that scene, from `Raw Footage/` or next to the script.
  Missing files are reported straight away, before any audio is generated.
- Scenes without a visual use the files in `Raw Footage/` in alphabetical order. If that folder is empty, they get a text card.
- Short clips loop to fill their scene, and still images get a slow zoom.
- Captions use your script's spelling (names, brands), with timings taken from the narration.

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
| `--regenerate` | Ignore cached AI images and saved prompts for this run |
| `--image-model @cf/...` | A different Workers AI image model (default `@cf/black-forest-labs/flux-1-schnell`) |
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
`--generate-visuals` draws an image for every scene that has no `[visual:]` line, using
[Cloudflare Workers AI](https://developers.cloudflare.com/workers-ai/). Scenes with a `[visual:]` line always keep your file.
With this option, `Raw Footage/` is **not** used to fill the other scenes.

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
1. **Prompts.** A text model (`@cf/meta/llama-3.3-70b-instruct-fp8-fast`) turns each scene's narration into an image prompt
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
`#` comment lines inside a scene's paragraph are passed to the prompt writer as shot notes, so describe the shot you want there.
Review the prompts before spending image quota:
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

### Free allowance
Workers AI's free plan includes **10,000 neurons per day**. Cloudflare can change this, so check the
[pricing page](https://developers.cloudflare.com/workers-ai/platform/pricing/).
At the published rates, a FLUX.1 schnell image at the default 4 steps costs roughly 40–60 neurons, depending on its resolution.
Each prompt from the text model costs roughly 25 neurons. That makes a 20-scene video roughly 1,500 neurons the first time, and almost nothing on cached reruns.
Past the free allowance, requests fail with a rate-limit error until the next day, and those scenes get title cards.

AI images of realistic scenes may need to be labelled as synthetic content on some platforms, for example YouTube's "altered or synthetic content" setting.

## Tests
```
.venv\Scripts\python -m pip install pytest
.venv\Scripts\python -m pytest
```
The tests cover script parsing, caption timing and alignment, and AI visuals with all Cloudflare calls mocked. They don't need FFmpeg or internet.

## Credits
The bundled font, `assets/fonts/NotoSans-Bold.ttf`, is licensed under the SIL Open Font License (`assets/fonts/OFL.txt`).
