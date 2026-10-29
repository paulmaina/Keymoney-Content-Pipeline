---
name: produce-video
description: Turn a script in Scripts/ into finished narrated, captioned horizontal and vertical videos with make_video.py. Use when the user asks to make, render, produce or re-render a video from a script, convert a production document into a voiceover script, pick a voice or speed, prepare visuals for scenes (own footage, AI images, choosing the image model, sharpening images with --upscale on Kaggle), or get YouTube titles, description, chapters, tags and a thumbnail for a render.
---

# Produce a video from a script

The pipeline is `make_video.py` in the project root (see README.md for every option).
It makes the voice over, captions and both video formats, animated by Remotion by default (section 3). Each scene's picture comes from:
1. its `[visual: file]` line (always wins), else
2. with `--generate-visuals`: AI images, a real 16:9 one for the horizontal video and a 9:16 one for the vertical
   (cached in `generated/`; section 2), else
3. without it: the next file in `Raw Footage/`, else a text card.
`--upscale` then sharpens any of those images under 2048 px on a free Kaggle GPU (section 2).

## 1. Get a script in the user's format
The user's format is one paragraph per scene, separated by blank lines, with 2-3 spoken sentences each and 15-20 paragraphs:
- `[video]` at the start of 4-5 action paragraphs: those scenes get a stronger camera move. It can be on the narration's line
  (`[video] Ama runs...`) or on its own line. (AI video clips were removed; `[video]` never means a clip now.)
- Optional `[visual: filename]` at the start of a paragraph, for the user's own media.
- `# character: Name = description` lines declare recurring characters (age, origin, build, hair, clothing, colours), kept identical
  in every scene that names them. Declare real people the script names too (Script2 declares Sonam Wangchuk), so they're drawn
  as described instead of guessed or swapped for another character.
- `#` lines labelled `Visuals:` / `Visual:` / `Shot:` / `Description:` inside a paragraph are that scene's visual
  description. They're never spoken, but they decide the scene's image prompt, and characters they name are drawn in it.
  Editing one makes that scene's prompt be written again.
- `# location: Nakuru, Kenya` sets the scene's place label and map pin (`# location: none` stops one). Without it, places are
  found in the narration (see section 3), so only add it when a label is missing or wrong.
- Every other line starting with `#` is a plain comment: never spoken, captioned or used in prompts. `--check` lists them.
- Watch for visual descriptions written *as narration* (e.g. "At sunrise on a dusty hilltop, a boy in a red cap…"):
  they get read aloud. Move them to a `# Visuals:` line and keep only the spoken words in the paragraph.
Tags are recognised only at the start of a line and are never spoken or captioned. A bracket anywhere else is spoken.

If the user's script is a production document (headings, "Narration:", "Visuals:", "On-screen text:", sources, title options),
don't run it directly. Write `Scripts/<name>_voiceover.txt` in that format and leave the original untouched:
- Keep only the spoken narration, without quotes. Give each planned shot its own paragraph of 2-3 sentences.
- Put the shot idea on a `# … - Visuals: …` line inside the paragraph, naming the place and the people in it (e.g.
  "# HACK 1 NIGERIA - Visuals: hands placing a small pot inside a larger one"). It drives that scene's image prompt.
- Add `# character:` lines for anyone who recurs, and `[video]` on the 4-5 most action-heavy paragraphs.
- Write numbers the way they should be spoken where TTS may stumble (e.g. "40 to 60 watt").

Then **always run `--check` first** and show the user its report, with the options you'll render with:
`.venv\Scripts\python make_video.py Scripts\<file>.txt --check --generate-visuals [--new-images] [--upscale]`
It lists scenes with words and estimated seconds, which get an AI image, a strong move or an own file, the skipped `#` comments,
and the Cloudflare neuron, Kaggle GPU-time and render-time estimates. It flags paragraphs that are too long, missing `[visual:]`
files and stray `[tags]`, and warns when the neurons exceed the free 10,000 a day. It needs no network or FFmpeg. Fix the flags first.

**Before a full render of a new or changed script, suggest running the fact-check skill** (it writes
`Research/<script>_factcheck.md` and never edits the script without asking). If the user declines, go ahead.

The normal command is `make_video.py Scripts\<file>.txt --generate-visuals`: FLUX.2 [klein] 4B images for every scene,
animated locally by the Remotion renderer (section 3). Add `--upscale` when the user wants sharper images.
Defaults live in `make_video.toml` ([defaults] table, option names without dashes), currently the photorealistic documentary
style, the Emma Multilingual voice and +5%. `VIDEO_STYLE` in `.env` overrides style (it's present but empty, so the toml
style applies); command-line options override both. So don't repeat those options unless changing them.

## 2. Visuals
- Missing `[visual:]` files stop the run before any audio is made, so fix those first.
- On-screen text callouts aren't supported; suggest adding them in Shotcut afterwards.

### Own footage (no `--generate-visuals`)
- List `Raw Footage/`. Scenes without `[visual: file]` take files from it in alphabetical order,
  which usually puts the wrong clip in a scene, so prefer explicit `[visual: ...]` lines.
- The demo's sample files live in `assets/demo/`, not `Raw Footage/`.

### AI images (`--generate-visuals`)
Keys live in `.env` (gitignored): `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`, optional `POLLINATIONS_API_KEY`,
and `KAGGLE_API_TOKEN` for `--upscale`. Check they're present by length only; never print, log or ask the user to paste them.

`--image-model` (short name or `@cf/...` ID; README "Image models" has the table):
- `flux-2-klein-4b` (default): realistic, takes a size, ~132 neurons per 1536x864 image by the published rate. Two images a scene
  (16:9 + 9:16), so a 19-scene video is ~5,000 neurons of images plus ~700 for prompts. The only realistic model that fits a
  whole video in the free 10,000 a day.
- `flux-2-klein-9b` (~1,400 an image), `flux-2-dev` (~3,800), `phoenix-1.0` (~2,900), `lucid-origin` (~3,500): each needs several
  days' allowance per video, so only on request, for a handful of scenes, or with `--format` to halve it.
- `flux-1-schnell`: 57.6 neurons, square only (cropped to each format), plastic illustrated look. The cheap fallback.
**Measured caveat (29 Oct 2026):** Script2's first klein run hit Cloudflare's daily limit after 26 images, when the published
rates put the day at only ~4,000 neurons. Either the account had used more that UTC day, or klein bills above its listed rate.
Until the user confirms in the Cloudflare dashboard, plan on at most ~25 klein images a day, check `--check` first, and render
one `--format` if the budget is tight. The allowance resets at 00:00 UTC.

`--image-provider`:
- `auto` (default): Cloudflare, switching to Pollinations (FLUX.1 schnell, at the same sizes) for the rest of the run once
  Cloudflare's daily allowance (or a persistent 429) stops it; title cards if both fail. Tell the user which scenes fell back:
  they look less realistic.
- `cloudflare`, `pollinations`: only that one.
Every provider shares the `generated/` cache, and a cached image from any provider is reused (including a Pollinations fallback;
to redraw those with Cloudflare, delete their files from `generated/` — the log lists them — and rerun without `--new-images`).

Prompts use `@cf/meta/llama-3.3-70b-instruct-fp8-fast` (`--text-model`), about 30-50 neurons a scene. The 8B model put the
channel's boy into Sonam Wangchuk's scene and called Alfredo Moser Nigerian, so don't switch back to save neurons.

Always review prompts before spending image quota:
1. `--prompts-only` (plus the same `-o`/`--style` you'll render with) writes
   `Exports/<name>_prompts.json` and stops. It uses only the text model.
2. Read every prompt and fix it in the JSON. Common problems seen so far:
   - Previous-scene context applied too strongly (the same subject or person carried into an unrelated scene).
   - Generic people ("African villager") or vague "traditional clothing": name the region, and describe
     everyday contemporary clothing unless traditional dress is genuinely worn there, then name it
     specifically (e.g. Ladakhi goncha, Khasi jainsem, northern Nigerian kaftan and cap).
   - Brand names, invented characters, or anything that renders as text (subscribe buttons, signs, logos).
     Names in prompts are fine: before the image call every sheet name becomes a role ("a Ladakhi engineer in his late
     fifties", "a village boy") plus its description, so real people's names never reach the image model.
   - Things a single still can't show: "time-lapse", "fast cuts", "montage of five scenes", animations.
   - Mood words that contradict the scene (a "warm" grey fog).
3. Render with `--generate-visuals` and the same `-o`. Saved prompts are matched by narration and reused,
   so edits stick; images are cached in `generated/`. A different `-o` means a new prompts file, new
   prompts and new images, so keep it the same between runs. `--regenerate` discards both (including the user's
   prompt edits). If the user dislikes the images but not the prompts, use `--new-images`: saved prompts are kept, every
   image is drawn again with a new seed, and the new ones replace the cached ones. Re-running the same command alone reuses the
   cached images.
- `--style` (default from `make_video.toml`) is appended to every image prompt and given to the prompt writer, so the photographic
  default gets documentary-photo prompts (shot type, lens, natural light). The style is stored in `prompts.json`: when it changes,
  every prompt and learned character is written again (declared `# character:` lines stay). Warn if it says "traditional
  dress": that pushes every person into costume, whatever the scene.
- Characters: only the sheet entries a scene's narration or `Visuals:` line names are given to the prompt writer and added to
  that scene's image prompt. Role words ("villagers", "farmer", "teacher") don't count as a mention, and roles aren't learned as
  characters. The sheet is saved under `"characters"` in `prompts.json`; when reviewing, check the descriptions are concrete,
  accurate for the person's origin, and identical across scenes.
- A real person is drawn as a representative figure from their description, not their actual likeness; say so if the user asks
  for "the real" person, and remind them about YouTube's synthetic-content label.
- Failed scenes fall back to text cards and are listed at the end; rerun to retry them. If one shape fails, the other is cropped.

### Sharper images (`--upscale`, Real-ESRGAN on a free Kaggle T4)
- Sends every scene image under 2048 px that isn't upscaled yet to Kaggle **in one batch**: one private dataset version and one
  private kernel run (`kaggle/upscale.ipynb`), polled every minute. 2x with RealESRGAN_x2plus (1536x864 -> 3072x1728), 4x if 2x
  would still be under 2048 px.
- Cached in `generated/upscaled/` by a hash of the source image, so each image is upscaled once; reruns skip Kaggle entirely.
- It prints the GPU time from Kaggle's own log; report it to the user. `--check --upscale` estimates it beforehand.
- If Kaggle fails or the token is missing, the run prints the log tail and renders with the original images; rerun to retry.
- After editing `kaggle/upscale.py`, run `.venv\Scripts\python kaggle\build_notebook.py` (a test checks they match).
  Don't run the notebook locally; it expects Kaggle's paths and a GPU.

## 3. Render
Run from the project root (not from Scripts/), in the background, since 20 scenes take several minutes:
```
.venv\Scripts\python make_video.py Scripts\<file>.txt --generate-visuals
```
- The user's preferences (voice, speed, the photorealistic style) come from `make_video.toml`, so they don't need to be typed.
  Other natural Multilingual edge voices: Andrew, Brian, Ava, Emma, en-AU-William.
  They need internet; `--engine kokoro --voice am_michael` works offline.
- `--format vertical` or `horizontal` renders only one (and draws only that image shape); `--music <file>` adds ducked background music.

### Renderer (`--renderer remotion`, the default)
`remotion_render.py` plans the video and `remotion/` (React, Remotion 4.0.535) draws it; FFmpeg mixes the audio as before.
- Needs Node.js (`winget install OpenJS.NodeJS.LTS`); without it the run notes it and falls back to `--renderer ffmpeg`.
  The first run installs `remotion/node_modules` (~250 MB) and the 99 MB depth model into `models/`.
- Image scenes: eased pans, tilts, zooms and pushes, never the same move twice in a row, clamped so edges never show; `[video]`
  scenes move more. Each format uses its own 16:9 or 9:16 image. Subtle 2.5D parallax only where the depth model finds a clear
  subject in front of a backdrop (`--no-parallax` turns it off). Crossfades are centred on narration boundaries.
- Animated captions (spoken word in yellow), a place label when the narration reaches a new place, a world-map card with a pin the
  first time each country comes up, and a 2 s intro plus 3.5 s outro (`--no-branding` drops them). The `.srt` and YouTube
  chapters are shifted to match the intro.
- It prints a render-time estimate from the last measured speed. On this PC (i7-1355U, integrated graphics, 16 GB) Script2 (2:37)
  rendered in 286 s horizontal + 336 s vertical; the whole run took 12 min against 6.5 min with `--renderer ffmpeg`. Run it in the background.
- When writing a script, name places in the narration ("In Ladakh, India, ...", "northern Nigeria") so labels and pins appear.
- Design tweaks go in `remotion/src/` (colours in `theme.ts`); run `npm run typecheck` there afterwards, and preview with
  `npx remotion studio`. `.claude/skills/remotion-*` hold Remotion's own best practices: drive animation with
  `useCurrentFrame()` + `interpolate()`/`spring()`, never CSS transitions.

### YouTube metadata and thumbnail (on by default)
After rendering, `make_video.py` writes `Exports/<name>_youtube.md` and `Exports/<name>_thumbnail.jpg`; `--no-metadata` skips both.
- The `.md` has 3 title options (under 60 characters, curiosity-driven but accurate, with counts), a ready-to-paste description (hook, summary, chapters,
  a line saying whether visuals are AI-generated, hashtags), the chapters, and 10-15 tags.
- Chapters use the real scene timings, grouped by all-caps section headings in the script: `# HOOK`, `# INTRO`,
  `# HACK 1 NIGERIA - Visuals: ...`, `# OUTRO` (a heading applies until the next one; `HACK 1 NIGERIA` and `HACK 1` are one
  chapter). YouTube needs the first at 0:00, at least 3, each 10+ seconds, so short sections merge into the previous one and
  chapters are left out of videos under ~30 s or scripts without headings. When writing a `_voiceover.txt`, start each
  `# ... - Visuals:` line with its section name so chapters work.
- Titles, summary, tags and the thumbnail words come from one call to `@cf/meta/llama-3.3-70b-instruct-fp8-fast`
  (`--metadata-model`), about 65 neurons; it prints an upper estimate first. The 3B and 8B models wrote false titles on
  Script2, so don't switch to them to save neurons. Model titles and tags using words the narration doesn't are dropped, and
  the description always opens with the narration's first lines. Without Cloudflare credentials, if the daily allowance is
  used up, or if the reply isn't usable, they're built from the script (first-line `# Title Case Title`, opening sentences,
  headings, character names); such titles are marked "built from the script". Rerun the metadata the next day if so.
- The thumbnail is 1280x720, under 2 MB, with 2-4 words of bold yellow text in the bundled font, on the most colourful and
  contrasty scene picture (its 16:9 image, upscaled if `--upscale` ran). If every scene is a title card it draws one AI image
  of the opening scene (~132 neurons, cached), else a plain gradient.
- `--channel` sets the channel name (default Smart Village Boy).
- Review the `.md` with the user: titles and description must stay within what the video says (fact-check them like the
  script), and remind them to declare altered or synthetic content in YouTube Studio: with photorealistic images that's always.

## 4. Check before handing over
- `ffprobe` both outputs in `Exports/`: 1920x1080 and 1080x1920, each with an audio stream.
- Read `Exports/<name>.srt` for caption problems, and grab a frame or two with
  `ffmpeg -ss <t> -i <mp4> -frames:v 1 <scratch png>` to look at the result.
- With AI images, look at frames from several scenes in both formats: check the subject is in frame, the right person is in
  each scene, people are depicted respectfully, and there's no stray lettering or warped hands and faces.
- Remotion renders: also check a frame just after each place label appears (label text, map pin on the right country), one
  mid-crossfade, and the intro and outro. In the vertical video, captions sit at ~66% height and the map near the top.
- Look at `Exports/<name>_thumbnail.jpg` and read `Exports/<name>_youtube.md` (titles, chapters, tags).
- Tell the user the output paths, the duration, the neurons and GPU time used, and which scenes used text cards, a Pollinations
  fallback or fallback footage.
