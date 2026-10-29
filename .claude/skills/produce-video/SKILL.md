---
name: produce-video
description: Turn a script in Scripts/ into finished narrated, captioned horizontal and vertical videos with make_video.py. Use when the user asks to make, render, produce or re-render a video from a script, convert a production document into a voiceover script, pick a voice or speed, prepare visuals for scenes (own footage, AI images, AI video clips on Kaggle), export/import a Kaggle video job, or get YouTube titles, description, chapters, tags and a thumbnail for a render.
---

# Produce a video from a script

The pipeline is `make_video.py` in the project root (see README.md for every option).
It makes the voice over, captions and both video formats. Each scene's picture comes from:
1. its `[visual: file]` line (always wins; such scenes are never animated), else
2. with `--clips`: the scene's AI video clip from the Kaggle notebook, if one came back, else
3. with `--generate-visuals`: an AI image (cached in `generated/`; providers in section 2), else
4. without it: the next file in `Raw Footage/`, else a text card.

## 1. Get a script in the user's format
The user's format is one paragraph per scene, separated by blank lines, with 2-3 spoken sentences each and 15-20 paragraphs:
- `[video]` at the start of 4-5 action paragraphs. It can be on the narration's line (`[video] Ama runs...`) or on its own line.
- Optional `[visual: filename]` at the start of a paragraph, for the user's own media. Such scenes are never animated.
- `# character: Name = description` lines declare recurring characters (age, build, hair, clothing, colours), kept identical in
  every scene's image prompt.
- `#` lines labelled `Visuals:` / `Visual:` / `Shot:` / `Description:` inside a paragraph are that scene's visual
  description. They're never spoken, but they decide the scene's image prompt and the motion prompt for `[video]` scenes.
  Editing one makes that scene's prompt be written again.
- Every other line starting with `#` is a plain comment: never spoken, captioned or used in prompts. `--check` lists them.
- Watch for visual descriptions written *as narration* (e.g. "At sunrise on a dusty hilltop, a boy in a red cap…"):
  they get read aloud. Move them to a `# Visuals:` line and keep only the spoken words in the paragraph.
Tags are recognised only at the start of a line and are never spoken or captioned. A bracket anywhere else is spoken.

If the user's script is a production document (headings, "Narration:", "Visuals:", "On-screen text:", sources, title options),
don't run it directly. Write `Scripts/<name>_voiceover.txt` in that format and leave the original untouched:
- Keep only the spoken narration, without quotes. Give each planned shot its own paragraph of 2-3 sentences.
- Put the shot idea on a `# … - Visuals: …` line inside the paragraph, naming the place (e.g.
  "# HACK 1 NIGERIA - Visuals: hands placing a small pot inside a larger one"). It drives that scene's image and motion prompts.
- Add `# character:` lines for anyone who recurs, and `[video]` on the 4-5 most action-heavy paragraphs.
- Write numbers the way they should be spoken where TTS may stumble (e.g. "40 to 60 watt").

Then **always run `--check` first** and show the user its report:
`.venv\Scripts\python make_video.py Scripts\<file>.txt --check`
It lists scenes with words and estimated seconds, video and visual scenes, the skipped `#` comments, and the Cloudflare
neuron and Kaggle GPU-time estimates. It also flags paragraphs that are too long, missing `[visual:]` files, more than 6 video scenes and stray `[tags]`.
It needs no network or FFmpeg. Fix the flags before rendering.

**Before a full render of a new or changed script, suggest running the fact-check skill** (it writes
`Research/<script>_factcheck.md` and never edits the script without asking). Do it before `--export-video-job` or
`--kaggle-video` too: changing a `[video]` scene's narration afterwards breaks the clip-to-scene match. If the user
declines, go ahead.

The one command for everything is `make_video.py Scripts\<file>.txt --kaggle-video`: Kaggle clips for `[video]` scenes (skipped
if there are none), Cloudflare images for the rest, and both videos rendered locally.
Defaults live in `make_video.toml` ([defaults] table, option names without dashes), currently the animated style, the Emma
Multilingual voice and +5%. `VIDEO_STYLE` in `.env` overrides style; command-line options override both. So don't repeat those options unless changing them.

## 2. Visuals
- Missing `[visual:]` files stop the run before any audio is made, so fix those first.
- On-screen text callouts aren't supported; suggest adding them in Shotcut afterwards.

### Own footage (no `--generate-visuals`)
- List `Raw Footage/`. Scenes without `[visual: file]` take files from it in alphabetical order,
  which usually puts the wrong clip in a scene, so prefer explicit `[visual: ...]` lines.
- The demo's sample files live in `assets/demo/`, not `Raw Footage/`.

### AI images (`--generate-visuals`)
Keys live in `.env` (gitignored): `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`, optional `POLLINATIONS_API_KEY`.
Check they're present by length only; never print, log or ask the user to paste them into chat.
`--image-provider`:
- `auto` (default): Cloudflare FLUX, switching to Pollinations FLUX for the rest of the run once Cloudflare's daily
  allowance (or a persistent 429) stops it; title cards if both fail.
- `cloudflare`, `pollinations`: only that one.
- `kaggle`: nothing online. SDXL images come from the Kaggle notebook via `--export-video-job` and then `--clips`.
Every provider shares the `generated/` cache, and a cached image from any provider is reused. The log and `prompts.json`
record which provider made each image.
Prompts use `@cf/meta/llama-3.1-8b-instruct-fp8` (`--text-model` to change), about 5-7 neurons each; a FLUX image is 57.6.
Free plan: 10,000 neurons/day, so a 20-scene video is roughly 1,300 the first time and near zero when cached. Each run prints its estimate.

Always review prompts before spending image quota:
1. `--prompts-only` (plus the same `-o`/`--style` you'll render with) writes
   `Exports/<name>_prompts.json` and stops. It uses only the text model.
2. Read every prompt and fix it in the JSON. Common problems seen so far:
   - Previous-scene context applied too strongly (the same subject repeated across unrelated scenes).
   - Generic people ("African villager") or vague "traditional clothing": name the region, and describe
     everyday contemporary clothing unless traditional dress is genuinely worn there, then name it
     specifically (e.g. Ladakhi goncha, Khasi jainsem, northern Nigerian kaftan and cap).
   - No real people's names, invented characters, brand names, or anything that renders as text
     (subscribe buttons, signs, logos).
   - Things a single still can't show: "time-lapse", "fast cuts", "montage of five scenes", animations.
   - Mood words that contradict the scene (a "warm" grey fog).
3. Render with `--generate-visuals` and the same `-o`. Saved prompts are matched by narration and reused,
   so edits stick; images are cached in `generated/`. A different `-o` means a new prompts file, new
   prompts and new images, so keep it the same between runs. `--regenerate` discards both (including the user's
   prompt edits). If the user dislikes the images but not the prompts, use `--new-images`: saved prompts are kept, every
   image is drawn again, and the new ones replace the cached ones. Re-running the same command alone reuses the cached
   images, and `--generate-visuals` never uses Kaggle (that needs `[video]` scenes plus `--kaggle-video`).
- `--style` (default from `make_video.toml`) is appended to every image prompt at render time, not stored in the JSON. It's also given
  to the prompt writer, so an animated style gets animation prompts. Warn if it says "traditional dress": that pushes every person
  into costume, whatever the scene.
- Characters: the sheet (declared `# character:` lines, plus ones the prompt writer describes for the first time) is passed to every
  prompt. A scene whose narration names a character gets that description added to its image prompt (description only, never
  the name). The sheet is saved under `"characters"` in `prompts.json`; when reviewing, check the descriptions are concrete and
  identical across scenes. Declared lines in the script always win.
- Images are square (FLUX.1 schnell has no size option) and cropped to both formats, so subjects should be centred.
- Failed scenes fall back to text cards and are listed at the end; rerun to retry them.

### AI video clips (Wan 2.2 on a free Kaggle T4 x2)
Only a few key scenes: measured on a T4, a 960x544 clip takes ~36 min plus ~6 min of setup per session (1280x704 is untested,
roughly an hour), and Kaggle gives ~30 GPU hours a week. `--check` estimates the total.
1. Mark scenes with `[video]` at the paragraph start, or `--video-scenes "3,7"`. A scene with `[visual:]` is never animated (it warns).
2. `--export-video-job` (with the same `--style`/`-o` as the final render) makes or reuses each marked scene's image, writes a
   motion prompt per clip, and creates `Exports/<name>_video_job.zip`. Review `"motion_prompt"` in `Exports/<name>_video_job/job.json`:
   it should describe gentle camera and subject motion that fits the image, with no cuts, new objects or text. Edits survive a re-export.
   With `--image-provider kaggle`, scenes without a cached image are listed for the notebook to make with SDXL.
3. Preferred: run it all with one command, in the background, since it takes about an hour per clip:
   `make_video.py Scripts\<file>.txt --kaggle-video [same --style/-o/--voice/--rate as the render]`.
   It needs `KAGGLE_API_TOKEN` in `.env` (Settings > API Tokens > Generate New Token; check presence by length only).
   It creates or versions one private dataset per job (reusing a hand-made `<name>_job`), pushes a private T4 x2 kernel
   with internet, polls every minute, downloads, imports and renders, then prints the GPU time used.
   On failure it prints the end of the Kaggle log and keeps finished clips; rerun with `--kaggle-resume`.
   Never start a second run while one is going (it refuses without `--kaggle-resume`); Ctrl+C doesn't cancel a run.
   Manual route, if the user prefers the website, tell them in order:
   - Upload the zip as a new Kaggle dataset (Create > New Dataset), or as a new version after a re-export.
   - Create > New Notebook, then File > Import Notebook with `kaggle/wan_video.ipynb`.
   - Settings: Accelerator GPU T4 x2, Internet On, Add Input with the dataset.
   - Run All, download `clips.zip` from the Output tab, then **stop the session**.
   - The notebook resumes after a timeout: Run All again.
4. Render with `--generate-visuals --clips <path to clips.zip>`. Clips match scenes by narration, so tell the user not to edit a marked
   scene's narration between export and import. Scenes without a clip keep their image. `--clip-fill pingpong` (default),
   `slowmo` or `freeze` controls how a ~5 s clip fills a longer scene.
- Wan 2.1 1.3B fallback clips (480p, text-only, so they won't match the image) trigger a warning on import; mention them.
- After editing `kaggle/wan_video.py`, run `.venv\Scripts\python kaggle\build_notebook.py` (a test checks they match).
- Don't run the notebook locally; it needs CUDA GPUs.

## 3. Render
Run from the project root (not from Scripts/), in the background, since 20 scenes take several minutes:
```
.venv\Scripts\python make_video.py Scripts\<file>.txt --generate-visuals
```
- The user's preferences (1.2x speed, the Andrew Multilingual voice, the animated style) come from `make_video.toml`, so they
  don't need to be typed. Other natural Multilingual edge voices: Brian, Ava, Emma, en-AU-William.
  They need internet; `--engine kokoro --voice am_michael` works offline.
- `--format vertical` or `horizontal` renders only one; `--music <file>` adds ducked background music.

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
  the description always opens with the narration's first lines. Without Cloudflare credentials, or if the reply isn't
  usable, they're built from the script (first-line `# Title Case Title`, opening sentences, headings, character names);
  such titles are marked "built from the script".
- The thumbnail is 1280x720, under 2 MB, with 2-4 words of bold yellow text in the bundled font, on the most colourful and
  contrasty scene picture (a frame for video scenes). If every scene is a title card it draws one FLUX image of the opening
  scene (57.6 neurons, cached), else a plain gradient.
- `--channel` sets the channel name (default Smart Village Boy).
- Review the `.md` with the user: titles and description must stay within what the video says (fact-check them like the
  script), and remind them to declare altered or synthetic content in YouTube Studio when AI scenes look realistic.

## 4. Check before handing over
- `ffprobe` both outputs in `Exports/`: 1920x1080 and 1080x1920, each with an audio stream.
- Read `Exports/<name>.srt` for caption problems, and grab a frame or two with
  `ffmpeg -ss <t> -i <mp4> -frames:v 1 <scratch png>` to look at the result.
- With AI images, look at frames from several scenes in both formats: check the crop kept the subject and
  that people are depicted respectfully.
- Look at `Exports/<name>_thumbnail.jpg` and read `Exports/<name>_youtube.md` (titles, chapters, tags).
- Tell the user the output paths, the duration, and which scenes still use text cards or fallback footage.
