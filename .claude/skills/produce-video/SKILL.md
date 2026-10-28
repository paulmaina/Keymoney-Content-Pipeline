---
name: produce-video
description: Turn a script in Scripts/ into finished narrated, captioned horizontal and vertical videos with make_video.py. Use when the user asks to make, render, produce or re-render a video from a script, convert a production document into a voiceover script, pick a voice or speed, or prepare visuals for scenes.
---

# Produce a video from a script

The pipeline is `make_video.py` in the project root (see README.md for every option).
It makes the voice over, captions and both video formats. Each scene's picture comes from:
1. its `[visual: file]` line (always wins), else
2. with `--generate-visuals`: an AI image from Cloudflare Workers AI (FLUX.1 schnell), else
3. without it: the next file in `Raw Footage/`, else a text card.

## 1. Get a narration-only script
`make_video.py` reads every non-`#` line aloud, and paragraphs (blank-line separated) are scenes.
If the user's script is a production document (headings, "Narration:", "Visuals:",
"On-screen text:", sources, title options), do not run it directly. Write
`Scripts/<name>_voiceover.txt` next to it and leave the original untouched:
- Keep only the spoken narration, without surrounding quotes.
- Split long sections into scenes of 1-3 sentences, one per planned shot.
- Put each scene's visual idea above it as a `# ...` comment, inside the scene's paragraph (no blank line
  between). These are shot notes: they tell the user what footage it needs, and `--generate-visuals`
  passes them to the prompt writer. Name the place in each note (e.g. "HACK 1 NIGERIA - Visuals: ...").
- Write numbers the way they should be spoken where TTS may stumble (e.g. "40 to 60 watt").
- Check it parses cleanly:
  `.venv\Scripts\python -c "import make_video as m, pathlib; s=m.parse_script(pathlib.Path('Scripts/<file>')); print(len(s), 'scenes', sum(len(x['text'].split()) for x in s), 'words')"`
  At about 150 words per minute (1.0x), estimate the length and tell the user if it's far from their target.

## 2. Visuals
- Missing `[visual:]` files stop the run before any audio is made, so fix those first.
- On-screen text callouts aren't supported; suggest adding them in Shotcut afterwards.

### Own footage (no `--generate-visuals`)
- List `Raw Footage/`. Scenes without `[visual: file]` take files from it in alphabetical order,
  which usually puts the wrong clip in a scene, so prefer explicit `[visual: ...]` lines.
- The demo's sample files live in `assets/demo/`, not `Raw Footage/`.

### AI images (`--generate-visuals`)
Needs `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` in `.env` (gitignored) or the environment.
Check they're present by length only; never print, log or ask the user to paste them into chat.
Free plan: 10,000 neurons/day; a 20-scene video is roughly 1,500 the first time, near zero when cached.

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
   prompts and new images, so keep it the same between runs. `--regenerate` discards both.
- `--style` is appended to every image prompt at render time (not stored in the JSON). Warn if it says
  "traditional dress": that pushes every person into costume, whatever the scene.
- Images are square (FLUX.1 schnell has no size option) and cropped to both formats, so subjects should be centred.
- Failed scenes fall back to text cards and are listed at the end; rerun to retry them.

## 3. Render
Run from the project root (not from Scripts/), in the background, since 20 scenes take several minutes:
```
.venv\Scripts\python make_video.py Scripts\<file>.txt --voice en-US-AndrewMultilingualNeural --rate +20%
```
- User preference: 1.2x speed (`--rate +20%`) and a very natural voice. The Multilingual edge voices
  sound most natural: Andrew (warm, documentary), Brian, Ava, Emma, en-AU-William.
  They need internet; `--engine kokoro --voice am_michael` works offline.
- `--format vertical` or `horizontal` renders only one; `--music <file>` adds ducked background music.

## 4. Check before handing over
- `ffprobe` both outputs in `Exports/`: 1920x1080 and 1080x1920, each with an audio stream.
- Read `Exports/<name>.srt` for caption problems, and grab a frame or two with
  `ffmpeg -ss <t> -i <mp4> -frames:v 1 <scratch png>` to look at the result.
- With AI images, look at frames from several scenes in both formats: check the crop kept the subject and
  that people are depicted respectfully.
- Tell the user the output paths, the duration, and which scenes still use text cards or fallback footage.
