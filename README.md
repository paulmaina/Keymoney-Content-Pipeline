# AI Video Editor

Turn a text script into narrated, captioned videos ready for social media, for one or more [channels](#channels), each with
its own look, voice and intro and outro. Each video gets its own folder, `Exports/<channel>/<name>/`, named after the script
(or `-o`), so channels never mix. `Upload/` holds only what goes to YouTube:

| `Exports/<channel>/<name>/Upload/` | Size | For |
|---|---|---|
| `<name>.mp4` | 1920x1080 | The full video for YouTube |
| `<name>_Short_Part1.mp4`, `_Short_Part2.mp4`, ... | 1080x1920 | YouTube Shorts (also Reels, TikTok): the same script as a numbered series of parts under 2:50 each ([Shorts series](#vertical-shorts-series)); a script that fits in one is a single `<name>_Short.mp4` |
| `thumbnail.jpg` | 1280x720 | YouTube thumbnail with 2–4 words of bold text |
| `youtube.md` | | Titles, descriptions, chapters and tags, with a section per Short ([YouTube metadata](#youtube-metadata-and-thumbnail)) |

`Exports/<channel>/<name>/Working/` holds everything else: `prompts.json` (image prompts and the character sheet), `media.json` (stock
picks and credits), `image_checks.json` (`--check-images` verdicts), an `.srt` for every video (`<name>.srt`, `<name>_Short.srt`
or `<name>_Short_PartN.srt`, each timed after that video's own intro), `log.txt` (everything the run printed), `manifest.json`
(the files the last final render put in `Upload/`), `old_outputs/` (generated files an earlier render left in `Upload/` that
the latest one didn't make, such as old Short parts; the run lists them), `kaggle/` (job files of the Kaggle runs) and
`render/` (temporary files and the render's checkpoints, removed after a successful render unless `--keep-work`), plus `<name>_DRAFT.mp4` ([drafts](#drafts-and-brainstorming)), `preview.mp4`, `thumbnail_ideas/` and
`style_tests/` when you make them. The images, upscaled images, stock media and voice lines stay in the shared caches in
`generated/`, so a new folder never regenerates anything. A run ends with `FINAL render: ready to upload from <Upload folder>`,
or `DRAFT render: <Working folder>`.

**Early checks and resuming.** Before a run spends any neurons or Kaggle time, it checks what later steps need (FFmpeg, the
image services' keys when a scene needs a picture made, the voice engine and the Kokoro model, Remotion's packages) and stops
with a clear message if something is missing; `--visuals-only` skips the voice and video checks. A final render keeps
checkpoints in `Working/render/` (the narration, captions, Remotion's prepared pictures and bundle, each video's render),
keyed to the script, the pictures, the settings, the resolved channel (tagline, colours, branding clips by their contents,
voice, captions, music) and the code, so a run that fails part-way resumes from its last finished
stage. `--fresh` starts over.

The plain command is a full **final render**: Cloudflare writes every image prompt and draws every image (stock footage where it
fits better), a vision model checks each image, Kaggle sharpens them, and the voice, captions, both videos and the YouTube notes
follow. Pollinations only makes drafts, brainstorms, and stands in for a scene Cloudflare can't draw
([Cloudflare and Pollinations](#cloudflare-and-pollinations)).

The voice over and captions are generated once and shared by both videos. By default the videos are rendered with
[Remotion](#motion-with-remotion-default): eased pans and zooms with a subtle 2.5D parallax, crossfades, animated captions,
place labels, a world map with a pin for each new country, and the channel's intro and outro (generated, or your own clips).
Everything runs locally and free.

## Setup
1. Install **FFmpeg** and make sure `ffmpeg` and `ffprobe` are on your PATH
   (Windows: `winget install Gyan.FFmpeg`, macOS: `brew install ffmpeg`, Linux: `apt install ffmpeg`).
2. Create the Python environment and install the packages:
   ```
   python -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt
   ```
3. For the Kokoro voice (Smart Village Boy's voice, and `--engine kokoro`): download the Kokoro model (~340 MB, once):
   ```
   .venv\Scripts\python download_models.py
   ```
   The first run also downloads the Whisper caption model (~140 MB) automatically.

4. For the AI scene images (on by default), set up Cloudflare (on the Workers Paid plan) and, for drafts and the backup, a
   Pollinations key: see [AI-generated visuals](#ai-generated-visuals).

5. For the default Remotion renderer, install **Node.js** (`winget install OpenJS.NodeJS.LTS`, then open a new terminal).
   The first render installs Remotion's packages into `remotion/node_modules` (~250 MB) and the depth model for parallax
   into `models/` (99 MB), once each. Without Node.js, videos are rendered with plain FFmpeg instead.

For hand editing, [Shotcut](https://shotcut.org) (`winget install Meltytech.Shotcut`) works well with the exported MP4s and `.srt`.

## Writing a script
[SCRIPT_GUIDE.md](SCRIPT_GUIDE.md) is the full standard for writing scripts: the general format and writing rules, a short
style section per channel, length and cost, and a fact-check checklist, with `Scripts/TEMPLATE.txt` as a worked example that a
test keeps passing `--check`.

Save a `.txt` file in `Scripts/`. The recommended format is **one paragraph per scene**, separated by blank lines,
with 2–3 spoken sentences each and 15–20 paragraphs in all:

```
# character: Ama = a 10-year-old girl with short braids, a yellow dress and red sandals
# character: Grandpa Kofi = an old man with a white beard, a blue kaftan and a walking stick

[video] Ama runs across the root bridge. The roots creak under her feet.

Grandpa Kofi watches from the riverbank. He smiles and waves his stick.

[visual: market.jpg] The market is busy today. Everyone has come to see the bridge.
```

- **`[video]`** at the start of a paragraph, on the same line as the narration or on its own line, marks an action scene: its
  picture gets a stronger camera move (more zoom and travel). Aim for 4–5 action scenes.
- **`[visual: file]`** at the start of a paragraph uses your own footage or image, from `Raw Footage/` or next to the script.
  Missing files are reported before any audio is made.
- **Tags are never spoken or captioned.** They're recognised only at the start of a line, in any upper or lower case, and both can be combined:
  `[video] [visual: x.png] Text`. A bracket anywhere else stays in the narration, and `--check` flags it.
- **`# channel: <id>`** in the header picks the [channel](#channels) (its settings, intro and outro, and `Exports/<id>/`).
  It wins over `--channel` and `default-channel`.
- **`# character: Name = description`** lines (also `Name: description` or `Name - description`) declare a character sheet
  for consistent AI images (see [Character consistency](#character-consistency)). They aren't spoken.
- **`#` description lines** inside a paragraph, labelled `Visuals:`, `Visual:`, `Shot:` or `Description:`
  (e.g. `# HACK 1 NIGERIA - Visuals: hands placing a small pot inside a larger one`), say what that scene should show.
  They're never spoken or captioned, but they're the main input to the scene's **image prompt**. If a description is added or
  edited, that scene's saved prompt is written again.
- **Every other line starting with `#` is a comment**, wherever it is (above, inside or between paragraphs, indented or not).
  Comments are never spoken, captioned or used in AI prompts. `--check` lists the comments it skipped.
- With `--no-generate-visuals`, scenes with no `[visual:]` use the files in `Raw Footage/` in alphabetical order, or a text card if it's empty.
- Your own video clips loop to fill their scene with `--renderer ffmpeg`; the Remotion renderer moves a still frame from them.
- Captions use your script's spelling (names, brands), with timings taken from the narration.

### Check a script before rendering
```
.venv\Scripts\python make_video.py Scripts\my_video.txt --check
```
`--check` reports scene count, words and estimated seconds per scene (from the voice engine and `--rate`), the channel, each
intro and outro (your clip with its length, or the generated one), which scenes get an AI image, a stronger move (`[video]`) or your own file, and every `#` comment line it skipped. It flags:
- a `# character:` line without `=`, `:` or ` - ` between name and description (it would be skipped as a comment);
- paragraphs over 60 words, or with more than 3 sentences once they pass 40 words;
- missing `[visual:]` files;
- stray `[tags]` that would be spoken.

It also estimates the Cloudflare neurons (counting saved prompts and cached images as free), what they cost beyond the free 10,000
a day, whether the run fits under your daily cap (and which scenes would go past it), the Kaggle GPU time for upscaling, and the
render time:
```
  total about 42,729 neurons
  cost: ~32,729 neurons beyond the 10,000 free ones left today (of 10,000, UTC day), about $0.36 ($0.011 per 1,000)
  daily cap: ~0 spent today + this run = ~42,729 of max-daily-neurons = 60,000 (Nairobi day): fits
```
It uses no network, audio or FFmpeg, and changes nothing. Add the options you'll render with (`--new-images`, `--draft`, ...) to
see their cost; a draft costs only its prompts.

### Your defaults (`make_video.toml`)
Options you always use go in `make_video.toml` in the project folder, written like the command-line option without the dashes.
Each channel's look and sound live in its own file ([Channels](#channels)), which wins over this one:
```toml
default-channel = "smart_village_boy"   # unless the script has "# channel: <id>" or you pass --channel
[defaults]
generate-visuals = true     # stock or AI images for every scene without [visual:] (also the built-in default)
check-images = true         # vision check for anatomy errors and stray text (~40 neurons an image; built-in default)
max-daily-neurons = 60000   # daily Cloudflare cap, Nairobi time (~$0.55 a day beyond the free 10,000); 0 = no cap
draft-images = false        # true = every run is a quick Pollinations draft (--draft)
media = "mixed"             # stock where it matches well, else AI images
```
`VIDEO_STYLE=...` in `.env` overrides `style` in this file, but not a channel's style. Options typed on the command line always
win over both files (`--style ""` turns the style off for one run). On/off options set in the file have a `--no-` form for one run:
`--no-generate-visuals`, `--no-check-images`, `--no-draft`. Unknown keys are reported, not silently ignored (an old
`upscale` or `image-provider` line just gets that note). Upscaling has no switch: every final render does it.
With these defaults, the whole command is `.venv\Scripts\python make_video.py Scripts\my_video.txt`.
[Commands.txt](Commands.txt) is a plain-text runbook of setup, the workflow, every command and every default with the setting
that changes it, limits, troubleshooting, and a step-by-step guide to new channels and branding clips.

## Channels
Every video belongs to a channel. A channel is a settings file, `channels/<id>.toml`, and a branding folder,
`channels/<id>/branding/`, and its videos go to `Exports/<id>/<name>/`. Smart Village Boy (`smart_village_boy`) is the default.

**The settings file** holds the channel's name and tagline (in the generated intro), the intro and outro colours, the image
`style`, the voice (`engine`, `voice`, the Chatterbox settings), the speed (`rate`), `caption-style`, `music` and
`music-volume`, and `youtube-style` (added to the YouTube writer's instructions). Any other make_video option can go there too.
Settings layer as `make_video.toml` < the channel's file < the command line. Smart Village Boy's:
```toml
name = "Smart Village Boy"
tagline = "Clever ideas from the world's villages"
style = "photorealistic documentary photography, natural lighting, realistic people and places, respectful depiction of local cultures"
engine = "kokoro"
voice = "am_michael"
rate = "+20%"              # Kokoro speed 1.2; every engine follows rate
caption-style = "auto"
music-volume = 0.2         # music = "..." when there is one
youtube-style = "warm, curious and respectful: celebrate the villagers' ingenuity and the place, never pity or hype"

[colors]                   # keep this table last: in TOML every line after it belongs to it
accent = "#FFD23F"
button = "#E63946"
background = "#0E2A3B"
background-light = "#1C4A63"
```

**Choosing the channel:** a `# channel: <id>` line in the script's header wins; else `--channel <id>` (the display name
works too); else `default-channel` in `make_video.toml`. An unknown channel stops the run with the list of channels.

**Your own intro and outro clips.** Put any of these in `channels/<id>/branding/` and it replaces the generated one; any that's
missing stays generated (the folder's `README.txt` has the details):

| File | Size | Suggested | Used for |
|---|---|---|---|
| `intro_horizontal.mp4` | 1920x1080 | 2–4 s | the full video's start |
| `intro_vertical.mp4` | 1080x1920 | 2–4 s | every Short's start (the "PART 1 OF N" badge is drawn over it) |
| `outro_horizontal.mp4` | 1920x1080 | 3–5 s | the full video's end |
| `outro_vertical.mp4` | 1080x1920 | 3–5 s | a single Short's end, and the last part's |
| `outro_next_part_vertical.mp4` | 1080x1920 | 3–5 s | optional: every other part's end, instead of "Watch Part N" |

MP4 at 30 fps is best; other sizes and frame rates are resized and padded with black bars (never stretched or cropped) and
converted to 30 fps by FFmpeg before Remotion plays them, and clips under 1 s are ignored. A clip lasts as long as its longer
part: when its sound runs on after its picture, the last picture is held, and the prepared clip's frame count is checked
before anything renders. The intro and outro crossfade with the
first and last scene as the generated ones do. Each clip keeps its own sound; music fades in after your intro and ducks under
the voice and your outro's sound. The clips' real lengths drive the timeline, the YouTube chapters, the Shorts captions and the
Shorts split (each part still stays under 2:50), and `--check` lists every intro and outro with its length. Files ending in
`_TEMPLATE.mp4` are never used: they're the generated intro and outro exported for you to edit (silent), made by:
```
.venv\Scripts\python channels.py templates smart_village_boy   # README.txt and the *_TEMPLATE.mp4 files again
.venv\Scripts\python channels.py new "Travel Diaries"          # a new channel: its file, branding folder, README and templates
.venv\Scripts\python channels.py list                          # the channels and their own clips
```
Only the Remotion renderer adds intros and outros; `--no-branding` and `--renderer ffmpeg` leave them out. Give each new channel a
style section in SCRIPT_GUIDE.md too.

## Making the video
```
.venv\Scripts\python make_video.py Scripts\demo.txt
```

| Option | What it does |
|---|---|
| `--format horizontal` / `vertical` / `both` | Which videos to render (default `both`) |
| `--renderer ffmpeg` | Plain FFmpeg rendering: one slow zoom per image, burned-in captions, no intro. Much faster (default `remotion`) |
| `--no-branding` | Remotion: leave out the channel intro and outro |
| `--depth strong` | Remotion 2.5D parallax: `strong` (default), `subtle` or `off` (`--no-parallax` is the same as `off`); see [Stills that feel alive](#stills-that-feel-alive) |
| `--no-overlays` | Remotion: no animated dust, smoke, mist, rain, heat shimmer, firelight, leaves, ripples or light rays |
| `--camera calm` | Remotion: slow moves and crossfades only (default `bold`: faster pushes, pans and tilts, whip-pan or zoom between chapters) |
| `--no-sound-effects` | No ambience under the narration (`--sfx-volume 0.5` sets its level) |
| `--no-two-shots` | One picture per scene (default: scenes over 6 s get a second shot, about doubling the image neurons) |
| `--draft` | A quick draft with Pollinations pictures, one format, to `Working/<name>_DRAFT.mp4` with a DRAFT label (see [Drafts](#drafts-and-brainstorming)); `draft-images = true` in `make_video.toml` makes it the default |
| `--preview-scenes "1,6,8"` | A draft of only those scenes, in one format, to `Working/preview.mp4`, to compare quickly |
| `--thumbnail-ideas 4` | Brainstorm: 4 thumbnail options from Pollinations in `Working/thumbnail_ideas/`, then stop |
| `--style-test "a \| b"` | Brainstorm: one scene (`--style-test-scene N`) drawn in each style by Pollinations, in `Working/style_tests/`, then stop |
| `--caption-style bold` | Remotion captions: `bold`, `clean` or `minimal` for both formats (default `auto`: bold vertical, clean horizontal) |
| `-o Promo` | The video's folder and name (default: the script's name): `Exports\<channel>\Promo\Upload\Promo.mp4` and so on. A path (`-o D:\Videos\Promo`) is used as the folder |
| `--channel travel_diaries` | The [channel](#channels) for this run, unless the script has a `# channel:` line (default: `default-channel` in `make_video.toml`) |
| `--engine kokoro` | Offline Kokoro voice (Smart Village Boy's, with `voice = "am_michael"`) |
| `--engine chatterbox` | Chatterbox voice made on a free Kaggle GPU (see [Chatterbox voice](#chatterbox-voice-on-kaggle---engine-chatterbox)); `--chatterbox-voice`, `--chatterbox-emotion`, `--chatterbox-cfg` |
| `--voice en-US-GuyNeural` | Choose a voice (defaults: `en-US-AriaNeural` online, `af_heart` offline) |
| `--rate +10%` | Speak faster (or `-10%` slower), for every engine: Edge's own rate, Kokoro's speed (`+15%` = 1.15), a pitch-kept tempo change for Chatterbox |
| `--music "Raw Footage\song.mp3"` | Background music. It drops while the voice speaks and fades out over the last 2 seconds |
| `--music-volume 0.2` | Music level between sentences |
| `--font path\to\font.ttf` | Font for captions and title cards (default: bundled Noto Sans Bold) |
| `--no-generate-visuals` | No AI images or stock for scenes without a `[visual:]` line (on by default; see below) |
| `--style "cinematic, warm tones"` | Added to every AI image prompt |
| `--prompts-only` | Write the AI image prompts for review, then stop (no images, audio or video) |
| `--visuals-only` | Make and cache the prompts and images (with checks, stock and upscaling), then stop before the voice and video. For scripts that don't fit under the daily cap: run it once a day, then render |
| `--regenerate` | Ignore cached AI images and saved prompts for this run (your edited prompts are rewritten) |
| `--new-images` | Keep the saved prompts but draw every image again; the new ones replace the cached ones |
| `--redo-scenes "3,7"` | Draw only those scenes' images again with Cloudflare and a new seed; everything else stays cached. A run that used the Pollinations backup prints this command for those scenes |
| `--no-check-images` | Skip the vision check (on by default: it looks at every AI image for extra or missing limbs, fingers or faces and stray text, and redraws failures up to 2 times; see [Quality check](#quality-check---check-images)) |
| `--max-daily-neurons 60000` | The daily Cloudflare cap, counted per day in Nairobi time; 0 = no cap (see [Cloudflare and Pollinations](#cloudflare-and-pollinations)) |
| `--media mixed` | `mixed` (default) uses Pexels/Pixabay footage or photos where one matches well and AI images elsewhere; `stock` uses stock wherever anything relevant exists; `ai` never uses stock (see [Stock footage](#stock-footage-and-photos-pexels-and-pixabay)) |
| `--stock auto` | Stock provider: `auto` (every one with a key), `pexels` or `pixabay` |
| `--image-model flux-2-klein-4b` | The Workers AI image model, by short name or `@cf/...` ID (see [Image models](#image-models)) |
| `--text-model @cf/...` | A different Workers AI model for writing prompts (default `@cf/meta/llama-3.3-70b-instruct-fp8-fast`) |
| `--check` | Check the script and estimate length, neurons and their cost, the daily cap, GPU time and render time, then stop |
| `--no-captions` | Don't burn in captions (the `.srt` is still useful for YouTube's caption upload) |
| `--whisper-model small` | More accurate caption timing, but slower |
| `--caption-words 4` | Fewer words per caption line |
| `--no-metadata` | Don't write `Upload/youtube.md` and `Upload/thumbnail.jpg` after rendering |
| `--metadata-model @cf/...` | Workers AI model for the YouTube titles, description and tags (default `@cf/meta/llama-3.3-70b-instruct-fp8-fast`, ~65 neurons) |

`--vertical` still works as a deprecated alias for `--format vertical`. Every final render also sharpens its images on Kaggle
([Upscaling](#upscaling-on-kaggle-every-final-render)); there's no option for it.

Examples:
```
.venv\Scripts\python make_video.py Scripts\demo.txt --format vertical --music "Raw Footage\song.mp3"
.venv\Scripts\python make_video.py Scripts\demo.txt --engine kokoro --voice am_michael -o demo_offline
```

Voices: run `.venv\Scripts\edge-tts --list-voices` for the online ones (e.g. `en-US-AndrewNeural`, `en-GB-SoniaNeural`).
Offline Kokoro voices include `af_heart`, `af_bella`, `am_michael`, `bf_emma` and `bm_george`. `Exports/voice_samples/` has
`kokoro-am_michael-speed1.00.wav`, `1.10`, `1.15` and `1.20` to compare speeds (`rate = "+0%"`, `"+10%"`, `"+15%"`, `"+20%"`).

### Motion with Remotion (default)
`remotion_render.py` plans every frame in Python and hands it to the small React project in `remotion/`, which Remotion renders in
headless Chrome on your PC. The voice and music are then mixed by FFmpeg exactly as with `--renderer ffmpeg`.
- **Image scenes** get a slow pan, tilt, zoom or push with eased motion, varied through the video and never the same move twice
  in a row. Offsets are limited so the picture's edge never shows. `[video]` scenes get a stronger move. Each format uses its own
  16:9 or 9:16 image when the image model drew one.
- **2.5D parallax**: the [Depth Anything V2 Small](https://huggingface.co/depth-anything/Depth-Anything-V2-Small) model (Apache-2.0, CPU,
  ~1 s per image) finds what's near the camera, and that layer drifts a little more than the background. It's used only when an
  image has a clear subject standing in front of a backdrop (in Script2: the market vendor, the two farmers, the globe's pins), not on
  walls, aerial views or close-ups, where it would wobble. Masks are cached next to the images.
- **Crossfades** between scenes, centred on the moment the narration moves on.
- **Captions** have their own style per format (`--caption-style`, or `caption-style` in the channel's file):

  | Style | Look | Default for |
  |---|---|---|
  | `bold` | Big bold capitals, 2–4 words a line, each word popping in as it's spoken, with a thick outline and shadow | vertical |
  | `clean` | One or two calm lines that fade in, the spoken word in yellow | horizontal |
  | `minimal` | A small plain line at the bottom, no highlight | |

  **Key words** (numbers, places, names, and named hacks such as "the zeer", "Liter of Light" or "an ice stupa") are coloured and a
  little bigger: yellow in `bold`, light blue in the others. Vertical captions sit in the lower third, above YouTube Shorts' title and
  description and left of its button column, and below the map and place labels. Each line is placed so it **doesn't cover a face**:
  OpenCV's YuNet face detector (MIT, 230 KB, downloaded once into `models/`) finds the faces in the scene's picture, and a line that
  would cover one moves up or down. Faces are cached next to each image. Captions use the narration's word timings, so they stay in
  sync with the voice. The `.srt` is still written (shifted to match the intro); `--renderer ffmpeg` burns in a plain line.
- **Place labels** ("Northern Nigeria", "Ladakh, India") when the narration reaches a new place, and a **world map** that flies to
  each new country and drops a pin, with a dotted line from the last one. Places come from the narration only: country names,
  directions ("northern Nigeria"), known regions (Ladakh, Meghalaya, Atacama, ...), or a name the `# Visuals:` line places
  ("Tharu, Nepal"). Add `# location: Nakuru, Kenya` to a scene to set it, or `# location: none` to stop a label.
  In the vertical video, a map or label that would cover a face (the vendor in a market, say) moves down below it, the map
  shrinking to fit above the captions with its label inside it, and the captions take their lowest slot while it shows. Small faces in a background
  crowd may still be covered; if there's no room under the face for a useful map, it stays at the top.
- **Branding**: a 2-second intro (rising sun, channel name, tagline) and a 3.5-second "Thanks for watching / Subscribe" outro,
  in the channel's colours, or the channel's own clips ([Channels](#channels)). `--no-branding` leaves them out. YouTube
  chapters account for the intro.

**Render time.** Before rendering, the run prints an estimate from the speed it measured last time on your PC (`--check` shows it
too). On an Intel i7-1355U laptop with integrated graphics and 16 GB RAM, Script2 (2:37, 19 scenes) took 286 s to render
horizontally and 336 s vertically, about 15 frames a second with 6 in parallel. The whole run was 12 minutes, against 6.5 minutes with
`--renderer ffmpeg`. `--format vertical` or `horizontal` saves about half the render time when you only need one.

### Stills that feel alive
Every setting below is on by default and has a key in `make_video.toml`. Compare quickly with
`--preview-scenes "1,6,8,14"`, a draft of only those scenes (horizontal, or `--format vertical`, Pollinations pictures) in
`Exports/<channel>/<name>/Working/preview.mp4`.
- **Depth parallax** (`depth = "strong"`): on every picture where the depth model finds a clear foreground (6–60% of the frame,
  well in front of the rest), the subject grows and drifts over the backdrop, as if the camera moved through the scene. The part
  of the backdrop behind the subject is filled in from its surroundings (OpenCV inpainting, cached next to the image as
  `*_bg.jpg`), so what the subject uncovers is background, never a stretched copy of its own edge. `subtle` is the old, gentler
  version; `off` skips the depth model.
- **Animated overlays** (`overlays = true`): up to two per picture, chosen from its Visuals line and image prompt: dust, smoke,
  mist, rain, heat shimmer, firelight flicker, drifting leaves, water ripples or light rays. They're drawn in code inside
  Remotion (`remotion/src/Overlays.tsx`), so there's nothing to download and no neurons, and they're kept faint. Stock clips
  and title cards get none.
- **Bolder camera** (`camera = "bold"`): bigger moves on a faster ease (quick start, gentle settle), including tilts, a push up
  and a diagonal drift, never the same move twice in a row (also between a scene's two shots). Where a new chapter starts
  (`# HACK 2 ...`), the cut is a whip-pan or a zoom-through, alternating; every other cut stays a crossfade. `calm` is the old style.
- **Ambient sound** (`sound-effects = true`, `sfx-volume = 0.5`): a quiet bed of wind, water, birds, fire, a market crowd or
  rain under each scene, matched from its Visuals line, prompt and narration, faded between scenes and ducked under the voice
  like the music. Only CC0 sounds are used: five recordings from Wikimedia Commons (licences checked through the Commons API)
  and a fire crackle generated in code. `assets/sfx/sources.json` records each one's source page, author and licence, and each
  render writes `Working/sound_effects.json` (which sound plays under which scene). `python sfx.py build <folder>` rebuilds the
  40-second loops from the downloads.
- **Two shots per scene** (`two-shots = true`): for scenes longer than 6 seconds, the text model rewrites the image prompt as
  the same moment from another angle or distance (a close-up of the hands or object, or a wide shot of the setting), the image
  model draws it, and the scene cuts to it half way through. The second prompt is saved as `prompt2` in `prompts.json` (edit it
  like the first). It costs about 380 more neurons a scene (its prompt, two images and their checks), so a typical script costs
  about twice as much; `--check` lists the second shots and their neurons, and `--no-two-shots` turns them off.

### Vertical Shorts series
The horizontal video uses the whole script, with no length limit (plan 8–10 minutes or more). The vertical video is the same
script split into parts of **under 2:50 each, including the intro and outro**, so each fits YouTube Shorts:
- **Where it cuts:** only between scenes, and at chapter headings (`# HACK 2 ...`) wherever possible. Among those splits it picks
  the fewest parts, then the most even lengths. A chapter longer than a Short is cut inside, evenly.
- **Files:** `Upload/<name>_Short_Part1.mp4`, `_Short_Part2.mp4` and so on. A script that fits in one part makes a single
  `Upload/<name>_Short.mp4` with the normal intro and outro.
- **Each part:** the intro shows "PART 1 OF N"; every part but the last ends with "Watch Part 2" (with a swipe-up arrow) instead
  of the subscribe card; the last ends with the normal subscribe outro. Each part has its own captions, place labels and map
  pins, and its own slice of the narration.
- **`--check`** lists the main video's length and every part's scene range, chapters and length, before anything is made.
- **`youtube.md`** gets a section per part: Shorts titles ending "(Part 2/5)", a description without chapters that links to the
  full video (replace `FULL_VIDEO_LINK` after uploading it) and tags. Each part's notes are one more text-model call (~95
  neurons, upper estimate).
- **Captions:** each part also gets its own `Working/<name>_Short_PartN.srt`, with only its words, timed from 0:00 plus its intro,
  for uploading with that Short (the captions are burned in too).
- With `--renderer ffmpeg` the parts are cut from the full vertical video, without the part badge or intro. Its scenes are
  encoded to whole frames on one shared timeline, so every cut lands on a scene's first picture.

Preview or tweak the design with `npx remotion studio` in `remotion/` (the colours are in `remotion/src/theme.ts`). Remotion is free
for individuals and small companies; see [remotion.dev/license](https://www.remotion.dev/license).

### YouTube metadata and thumbnail
After rendering, two upload helpers are written into `Upload/` next to the videos (`--no-metadata` skips them):
- **`youtube.md`**: 3 curiosity-driven title options under 60 characters (no clickbait), a ready-to-paste description (hook, short summary,
  chapters, a line saying whether the visuals are AI-generated, hashtags), the chapters on their own, and 10–15 tags.
- **`thumbnail.jpg`**: 1280x720, under 2 MB, with 2–4 words of bold text in the bundled font, on the most colourful
  and contrasty scene picture (its 16:9 image). If every scene is a title card it draws one AI image (about 132 neurons, cached), or
  uses a plain gradient.

**Chapters** come from the real scene timings, grouped by all-caps section headings in the script. A heading applies to its
paragraph and the following ones until the next heading, and `HACK 1 NIGERIA` and `HACK 1` count as the same chapter:
```
# HOOK - Visuals: clay pot in desert sun
No electricity. No fridge. No running water.

# HACK 1 NIGERIA - Visuals: Sahel village market in harsh sun
In northern Nigeria, heat can spoil vegetables in just a few days.
```
gives `0:00 Hook`, then `Hack 1: Nigeria` at its real start time. YouTube shows chapters only when the first is at 0:00, there are
at least 3, and each lasts at least 10 seconds, so shorter sections merge into the one before. Scripts without headings, and
videos under about 30 seconds, get no chapters (the `.md` says why).

**Titles, summary, tags and thumbnail words** come from one call to `@cf/meta/llama-3.3-70b-instruct-fp8-fast`, about
65 neurons (the run prints an upper estimate first). On Script2 the cheaper 3B and 8B models kept writing titles the narration
contradicts, such as "collect rainwater" for fog nets, so the 70B is the default; `--metadata-model` changes it. The model is
told to use only facts from the narration, and its titles and tags are dropped if they use words the narration doesn't
(apart from generic ones like "clever" or "hacks"), as are clickbait phrases and exclamation marks. The description always
opens with the narration's own first lines. Without Cloudflare credentials, or if the reply isn't usable, everything is built
from the script instead: a Title Case `# title` on the first line, the opening sentences, the headings and character names.
Titles that came from the script are marked in the `.md`. Always read them before uploading.

## Research and fact-checking
Two Claude Code skills in `.claude/skills/` help before you write and before you render:
- **research-topic**: give it a topic ("water storage village hacks"). It runs `research.py` to find 10–20 top YouTube videos
  and their transcripts (no API key, using `youtube-transcript-api`), then writes `Research/<topic>.md`: what the videos cover
  and miss, common hooks and title patterns, and 5 topic ideas for the channel with the gap each fills. Transcripts are saved in
  `Research/_raw/` (gitignored) for analysis only; the report never copies their wording.
  ```
  .venv\Scripts\python research.py "water storage village hacks" --max 15
  .venv\Scripts\python research.py "topic" --ids "https://youtu.be/...,https://youtu.be/..." --no-search
  ```
  Videos without captions are noted. If YouTube blocks your IP it stops fetching and keeps what it has; rerun later
  (saved transcripts are reused).
- **fact-check**: give it a script in `Scripts\`. It lists every factual claim (places, people, dates, numbers, how a hack works,
  safety or health claims), checks each against 1–2 independent web sources, and writes `Research/<script>_factcheck.md` with
  confirmed / unclear / unsupported verdicts, links, and softer wording for anything not confirmed. It never edits the script
  without asking. Run it before a full render.

## AI-generated visuals
Every scene that has no `[visual:]` line gets an AI image (or stock, see `--media`), on by default. Scenes with a `[visual:]` line
always keep your file, and `Raw Footage/` is **not** used to fill the other scenes (`--no-generate-visuals` turns all of this off).

```
.venv\Scripts\python make_video.py Scripts\my_video.txt
```

### Cloudflare and Pollinations
| Who | Does what | Needs |
|---|---|---|
| [Cloudflare Workers AI](https://developers.cloudflare.com/workers-ai/) | **Every final image prompt and image** (`--image-model`), the quality check, stock scoring and the YouTube notes | `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`; the Workers Paid plan |
| [Pollinations](https://pollinations.ai) | Only: drafts (`--draft`, `--preview-scenes`), the emergency backup in a final render, and brainstorming (`--thumbnail-ideas`, `--style-test`) | `POLLINATIONS_API_KEY` |

**Paying past the free allowance.** On the Workers Paid plan, the first 10,000 neurons a day are free (they reset at 00:00 UTC,
03:00 in Nairobi) and the rest cost $0.011 per 1,000. A final render keeps using Cloudflare after the free amount; it never switches
to Pollinations just because the free neurons are used up.

**The daily cap.** `max-daily-neurons = 60000` in `make_video.toml` (about $0.55 a day beyond the free 10,000) caps what this PC
spends per day in **Nairobi time** (midnight to midnight, EAT has no daylight saving). Every Cloudflare call is counted in
`generated/cloudflare_usage.json` (neurons per UTC day for the free allowance, per Nairobi day for the cap; use of the same account
elsewhere isn't seen). Before each image or prompt, the run checks that it stays within the cap; when it wouldn't, it prints a boxed
`WARNING: the daily Cloudflare cap is reached ...`, makes no more Cloudflare images, prompts or checks that day, and the remaining
scenes use the backup. `0` turns the cap off; `--max-daily-neurons 80000` raises it for one run.

**The emergency backup.** In a final render, Pollinations draws a scene only when its Cloudflare call still fails after **one
retry** (any error but bad credentials, which stop Cloudflare for the run with a warning), or once the cap is reached; if the prompt
writer failed too, Pollinations' text model writes that prompt (`gen.pollinations.ai/v1/chat/completions`, with the same detailed
instructions). The quality check never goes to Pollinations. At the end of the run, and so in `Working/log.txt`:
```
WARNING: 2 scene(s) aren't Cloudflare's work in this final render:
  scene 15: Pollinations backup image
  scene 16: prompt not written by Cloudflare
Redraw them with Cloudflare (once it works again, or tomorrow under the daily cap) with:
  .venv\Scripts\python make_video.py Scripts\MyVideo.txt --redo-scenes "15,16"
```
Backup pictures are never reused by a later final render (it asks Cloudflare again), and prompts Cloudflare didn't write are
marked `"stand_in"` in `prompts.json` and written by Cloudflare next time. Without a Pollinations key, those scenes get title cards.

**Pollinations setup:** create a free secret key (it starts with `sk_`) at [enter.pollinations.ai](https://enter.pollinations.ai) and add
`POLLINATIONS_API_KEY=...` to `.env`. For prompts, the key must also be **allowed to use text models**: on
enter.pollinations.ai, open the key and allow text (a key without it answers with a refusal, which the run detects and reports,
then keeps using Pollinations for images only). Its FLUX model costs 0.002 "pollen" per image from the free allowance. The current API requires a key. Its
docs no longer mention the old `nologo` option or a watermark, so check the first few images it makes.

### Drafts and brainstorming
```
.venv\Scripts\python make_video.py Scripts\my_video.txt --draft --engine edge
```
A **draft** shows the whole video cheaply before you pay for the final images: Pollinations draws every picture, into a cache of
its own (`generated/drafts/`) that a final render never reads, and it renders one format (horizontal, or `--format vertical`) to
`Working/<name>_DRAFT.mp4` with a small red **DRAFT** label in the top-right corner of every frame. It never writes to `Upload/`, and
it skips the image check, Kaggle upscaling, the Shorts split, the caption file and the YouTube notes, and leaves `media.json` and
`image_checks.json` alone. Its prompts still come from Cloudflare while it's within the cap (Pollinations' text model writes them
otherwise, marked as stand-ins), so the final render reuses the same prompts. `draft-images = true` in `make_video.toml` makes every
run a draft (`--no-draft` for one final run). `--preview-scenes "1,6,8"` is always a draft, of just those scenes.

**Brainstorming** with Pollinations only (no Cloudflare neurons), stopping before any voice or video:
- `--thumbnail-ideas 4`: four thumbnail options in `Working/thumbnail_ideas/` (`idea_1.jpg` ...): the opening scene drawn as a
  face close-up, a wide dramatic shot, the key object held up, a low-angle hero shot and so on, with the thumbnail words over each.
  The final render still makes its own `Upload/thumbnail.jpg`.
- `--style-test "watercolour | 3D animated film | photorealistic documentary photography"`: one scene (the first without a
  `[visual:]` file, or `--style-test-scene 5`) drawn once in each style, with a prompt written for each, into
  `Working/style_tests/scene5_1_watercolour.jpg` and so on. Put the one you like in the channel's file as `style = "..."`.

### Image models
Workers AI's text-to-image models (pricing page checked October 2026). Costs are for one 1536x864 image, from the published
per-tile and per-step rates; a video needs two per scene (16:9 and 9:16), so ~38 images for Script2. The realism notes for klein 4B
and schnell come from renders here; the others are from the model descriptions, since one video of them would cost days of allowance.

| `--image-model` | Sizes | Neurons per image | Realism | A 19-scene video |
|---|---|---|---|---|
| **`flux-2-klein-4b`** (default) | any, 256–1920 px | **~132** (26.05 per 512x512 tile, 4 fixed steps) | Real skin, fabric, light and materials; reads as a photograph | **~5,000** |
| `flux-2-klein-9b` | any, 256–1920 px | ~1,410 (1,363.64 for the first megapixel) | Sharper detail than 4B | ~54,000 (7 images a day) |
| `flux-2-dev` | any, 256–1920 px | ~3,800 at 20 steps (37.5 per tile per step) | The most photographic | ~144,000 |
| `phoenix-1.0` (Leonardo) | any, up to 2048 px | ~2,930 at 25 steps | Strong prompt adherence, polished | ~111,000 |
| `lucid-origin` (Leonardo) | any, up to 2500 px | ~3,460 at 20 steps | Polished, often stylised | ~131,000 |
| `flux-1-schnell` | square 1024 only | 57.6 | Plastic skin, illustrated feel; cropped to each format | ~1,100 (19 squares) |

**FLUX.2 [klein] 4B is the recommendation**: it's the only realistic model whose full video fits easily under the daily cap
(about 5,000 neurons for the images, ~750 for the prompts and ~70 for the metadata on Script2), and it takes a real width and
height. The others cost 10-30 times as much per video. Older SD-based models (`stable-diffusion-xl-*`, `dreamshaper-8-lcm`) are still
listed by Workers AI, but they're marked beta and are older Stable Diffusion models; they weren't tried here.

**Real 16:9 and 9:16 images.** Models that take a size draw **1536x864** for the horizontal video and **864x1536** for the vertical one,
each with a composition note for its shape, so the vertical video is no longer a narrow crop of a square. The renderers scale them to
1080p, after Kaggle sharpens them. FLUX.1 schnell's Workers AI API has no size setting, so it still makes one square per
scene, cropped to both formats. `--format horizontal` or `vertical` draws only that shape and halves the image cost.

### One-time Cloudflare setup
1. Create a free account at [dash.cloudflare.com](https://dash.cloudflare.com).
2. Go to **AI > Workers AI** and select **Use REST API**.
3. Copy the **Account ID**.
4. Select **Create a Workers AI API Token**, then create and copy it. The token needs Workers AI Read and Edit permissions; the template sets both.
5. Copy `.env.example` to `.env` and paste in both values. `.env` is gitignored, so don't commit it or share it.
   You can set `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` as environment variables instead.

### How it works
1. **Prompts.** `@cf/meta/llama-3.3-70b-instruct-fp8-fast` turns each scene's narration into an image prompt describing the shot,
   lens, light, setting and mood (about 40–50 neurons a scene). On Script2 the cheaper 8B model put the channel's boy into Sonam
   Wangchuk's scene and called Alfredo Moser Nigerian; the 70B didn't. It is told to leave out text, logos and watermarks, and to
   show only the people this scene calls for. The previous scene's prompt is passed along for continuity of place and light. If the
   text model fails, a simple prompt is built from the scene text.
2. **No names in images.** Before a prompt goes to the image model, every character's name is swapped for their role ("a Ladakhi
   engineer in his late fifties", "a village boy") and their sheet description is added. Image models don't know who people are,
   may letter a name into the picture, and real people's names shouldn't be sent to them. A real person is drawn as a representative
   figure, not as their likeness.
3. **Images.** The image model draws each scene, once per format for a sized model (see [Image models](#image-models)).
   `--new-images` sends a fresh random seed.
4. **Cache.** Images are saved in `generated/`, named by a hash of the prompt, size and model. Reruns reuse them and don't spend your quota.
   `--regenerate` forces new prompts and images, `--new-images` new images for every scene, and `--redo-scenes "3,7"` new images
   for just those scenes (both formats, new seed, same prompt). A redrawn image drops its old parallax mask and quality verdict.
5. **Hands.** The prompt writer is told to give every person one simple, clear pose (hands resting, holding one object, at their
   sides) and say what each hand does, and to avoid poses image models get wrong: spread-finger waves, pointing, interlocked
   hands, hands near faces. A description that asks for a wave or a point gets its simplest version. This applies to prompts
   written from now on; remove a scene's entry from `prompts.json` (or use `--regenerate`) to rewrite an older one.
6. **Failures.** A Cloudflare error gets one retry, then that scene goes to the Pollinations backup (see
   [Cloudflare and Pollinations](#cloudflare-and-pollinations)). If one shape fails, the other is cropped to fit. A scene that still
   fails gets a title card, and the run carries on; backup and failed scenes are listed at the end with the `--redo-scenes` command
   that redraws them. Images that did succeed are cached.

### Quality check (`--check-images`)
On by default (`--no-check-images` skips it; drafts never check). After each image is drawn, `@cf/meta/llama-4-scout-17b-16e-instruct` (natively multimodal, no licence step; `--vision-model` to
change) looks at it, shrunk to 1024 px, with the shot it was meant to show, and answers in JSON: does any person or animal have an
extra or missing arm, leg, hand or finger, a fused or floating limb, a distorted or extra face, and is there any lettering, logo or
watermark? A failed image is redrawn with a new seed, up to **2 times**; then the best try (a pass, else the highest score) is kept,
and the log ends with a `FLAGGED` line for each image that still failed, so you can look at it or `--redo-scenes` it.
- **Cost:** about **40 neurons a check** at the published rates (the run prints the real total from the reported tokens), so ~1,500
  for a 19-scene video's 38 images, plus a redraw (~132) and a check for each failure. `--check` estimates it.
- **Verdicts are cached** next to each image (`generated/<image>.qc.json`) and saved in `prompts.json` under `"quality"`, so a
  rerun doesn't pay to check the same picture twice. If the checker is unavailable (past the daily cap) or its reply can't be read,
  the image is kept unchecked and checked on the next run.
- Vision models miss things and sometimes see problems that aren't there, so still look at the flagged scenes and a few others.

### Reviewing and editing prompts
The prompt writer sees each scene's `Visuals:` description line (which decides what the image shows), its narration, the style,
the characters this scene names and the previous scene's prompt. Plain `#` comments are never sent. To steer a shot, edit its description
line in the script, or its prompt in `prompts.json`. Review the prompts before spending image quota:
```
.venv\Scripts\python make_video.py Scripts\my_video.txt --prompts-only
```
This writes the prompts and stops, using only the text model. Check how people, places and clothing are described. Remove anything a
single still image can't show, such as "time-lapse", "fast cuts" or animations, and anything that would render as text. Then run
the normal command with the same `-o`.

Prompts are saved to `Exports/<channel>/<name>/Working/prompts.json`:
```json
{"style": "photorealistic ...", "scenes": [{"scene": 4, "narration": "In northern Nigeria, heat can...", "prompt": "Medium shot, 50mm lens..."}]}
```
Edit any `"prompt"` and rerun the same command. Your edited prompts are reused instead of being rewritten, and only scenes whose prompt changed get new images.
Prompts are matched to scenes by their narration, so if you change a scene's narration, that scene gets a fresh prompt. Prompts are
written for a style (a photograph is described differently from an animation), so when the style changes, every prompt and every
learned character description is written again; your `# character:` lines are kept.

### Character consistency
Image models draw each scene separately, so the same character can look different from scene to scene. To keep them consistent:
1. **Declare them** with `# character: Name = description` lines in the script: age, origin, build, hair, clothing and colours.
   Declare **invented characters only** ([SCRIPT_GUIDE.md](SCRIPT_GUIDE.md)): describe a real person by role, age and clothing in
   the `Visuals:` lines instead ("an elderly farmer in a long robe and a knitted cap"), the same words each time.
2. When a scene's narration or `Visuals:` line names a character, using any distinctive word of the name ("Kofi" or "Grandpa" for
   "Grandpa Kofi"; role words like "villagers" or "farmer" don't count), the prompt writer gets their sheet entry and is told to
   describe them exactly as listed. Characters the scene doesn't name aren't shown to it.
3. The prompt writer also lists the recurring characters it drew. A character named in the scene's text and described for the first
   time is **added to the sheet**; roles ("Villagers", "Farmer 1") aren't. Existing entries never change, and your declarations win.
4. That scene's image prompt gets the description, with the name swapped for a role.

The sheet is saved under `"characters"` in `Working/prompts.json`; edit it there like the prompts. Image models still vary a little,
so keep descriptions short, concrete and identical across scenes: "yellow dress, red sandals", not "brightly dressed".

### Style
The style (from `--style`, the channel's file, `make_video.toml` or `VIDEO_STYLE`) is added to every image prompt, and it's also given to the prompt writer.
The default is photorealistic documentary photography, so prompts are written as real photographs (shot type, lens, natural light,
real textures); with an animated style they're written for animation instead.

### Cost
Workers AI includes **10,000 free neurons per day**; on the Workers Paid plan the rest cost **$0.011 per 1,000**. Cloudflare can
change this, so check the [pricing page](https://developers.cloudflare.com/workers-ai/platform/pricing/).
At the published rates, a 55-scene, 8-minute script with second shots and checks costs about **42,700 neurons** (about **$0.36**
beyond the free 10,000), or 21,900 (about $0.13) with `--no-two-shots`. Cached reruns cost almost nothing. Each run prints its
usage and today's total against the cap, and `--check` shows the neurons, the dollars and the cap beforehand.

Realistic AI images need to be labelled as synthetic content on YouTube ("altered or synthetic content"), especially when they show
a real person or place. The YouTube notes say so.

## Chatterbox voice on Kaggle (`--engine chatterbox`)
```
.venv\Scripts\python make_video.py Scripts\my_video.txt --engine chatterbox
```
[Chatterbox](https://github.com/resemble-ai/chatterbox) (Resemble AI, MIT licence) is an expressive open voice model. It needs a GPU,
so every narration line goes to a free Kaggle T4 **in one batch** (`kaggle/chatterbox.ipynb`, the same private dataset and kernel
runner as the upscaling), the audio comes back, and the render finishes locally as usual. The GPU time is printed at the end.

- **Voice:** Chatterbox's built-in voice by default. To speak in another voice, set `chatterbox-voice` to a 6–15 second clip of
  clean speech. **Only use a voice you have the right to use: your own, or one whose speaker has given you permission.**
- **Emotion:** `chatterbox-emotion` from 0.25 (flat) to 2.0 (theatrical), default 0.5. Above about 0.7, a lower `chatterbox-cfg`
  (about 0.3, default 0.5) keeps the pace natural. `--rate` still applies (as a tempo change that keeps the pitch).
- **Cache and resume:** each line is cached in `generated/voice/`, keyed by its text and every setting that changes the sound (the
  reference clip's contents, emotion, cfg, model version), so a rerun only sends new or edited lines. If a run stops part-way, the
  lines it finished are kept and the next run sends only the rest.
- **Using it:** set `engine = "chatterbox"` in a channel's file (with the "natural" settings, emotion 0.5 and cfg 0.5), or
  `--engine chatterbox` for one render. Smart Village Boy uses Kokoro now; its file keeps the Chatterbox settings for a switch back.
- **Fallback:** if Kaggle fails, `KAGGLE_API_TOKEN` is missing or any line is missing, the whole video uses the Edge voice (Emma,
  or the channel's voice when it's an Edge voice), so a video never mixes two voices.
- Chatterbox adds Resemble's imperceptible Perth watermark to its audio. The YouTube notes already say the narration is an AI voice.
- **Samples:** `.venv\Scripts\python chatterbox_tts.py` speaks one line at three settings (natural 0.5/0.5, calm 0.35/0.6,
  lively 0.8/0.3) in one Kaggle run, plus Edge Emma, into `Exports/voice_samples/`. Add `--voice <clip>` to try a reference voice.
  Measured October 2026: the three 8-second samples took a **2.4-minute** GPU session (35 s to load the model). The lively
  setting added an "Uh" before the first word, so listen for fillers when you raise the emotion or lower the cfg.

```toml
engine = "chatterbox"
chatterbox-voice = "voices/my_voice.wav"   # optional; a voice you have the right to use
chatterbox-emotion = 0.5
chatterbox-cfg = 0.5
```

## Stock footage and photos (Pexels and Pixabay)
Scenes can use real stock footage or photos instead of an AI image. Add free keys to `.env` as
`PEXELS_API_KEY` and/or `PIXABAY_API_KEY` ([pexels.com/api](https://www.pexels.com/api/), [pixabay.com/api/docs](https://pixabay.com/api/docs/));
whichever exist are used, and with neither the run goes straight to AI images.

1. **Queries.** For each scene, `@cf/meta/llama-3.1-8b-instruct-fp8-fast` writes 2–3 short search queries from its `Visuals:` line and
   narration (no people's names). Without Cloudflare, they're built from the description's key phrases.
2. **Search.** Videos first, then photos only if no video fits every format. Only real footage and photographs are asked for
   (Pixabay `video_type=film`, `image_type=photo`; Pexels has no illustrations).
3. **Score.** Every result from both providers is scored together. The same small model rates how well each result's description
   (Pexels) or tags (Pixabay) match the scene, 0–10; without Cloudflare, shared keywords are counted instead. Each result then gets
   up to +4: +1 for video, +1 if it's sharp at 1080p for that format, +1 for the format's orientation, and +1 if a clip is at least as
   long as the narration. The best result wins **for each format**, so the vertical video can use a portrait clip. Pexels wins a tie.
4. **Fallback.** A provider that fails or hits its rate limit is skipped for the rest of the run and the other is used; a scene
   with no good match gets the Cloudflare AI image.

| `--media` | Stock is used when |
|---|---|
| `mixed` (default) | the best result scores 7+ out of 10. Scenes that name a `# character:` keep AI images, so recurring characters look the same |
| `stock` | anything scores 4+; AI only when nothing relevant is found |
| `ai` | never |

`# media: stock` or `# media: ai` inside a paragraph forces that scene. Both options can go in `make_video.toml`.

- **In the video:** stock photos get the same camera moves and parallax as AI images, and stock clips play as moving video,
  cover-cropped to each format and looped if they're shorter than the scene. Downloads are 1080p-class files (no 4K), and Pixabay
  photos are at most 1280 px, so they'd look soft in the vertical video: the Kaggle upscaling sharpens them.
- **Caching and limits:** searches are cached for 24 hours (Pixabay's terms ask for it) and downloads for good, in `generated/stock/`.
  Pixabay allows 100 requests a minute, Pexels 200 an hour; the limits are read from the response headers and remembered, a short
  Pixabay wait is sat out, and a long block switches to the other provider. A 19-scene video needs about 3–6 searches per provider per scene the first time.
- **Credits:** `Working/media.json` lists each scene's choice (stock, AI, your own file, title card), the provider, the
  creator, the link, the query and the scores. The YouTube description credits every stock clip and photo ("Video by … on Pixabay:
  link") with a link to Pexels and Pixabay, as both ask, and its AI line says which visuals are AI-generated.
- `--check` shows which scenes would search for stock, which stay AI and why, and what the last run chose.
- **Cost:** about 10 neurons a scene for the queries and the scoring.

## Upscaling on Kaggle (every final render)
There's no option: every final render sends its new scene images to Kaggle (drafts don't). Every scene image, whether from
Cloudflare, the Pollinations backup or a stock photo, under 2048 px on its long side goes to a free Kaggle GPU **in one batch**, is enlarged with **Real-ESRGAN**, and
the sharper images are used in the render (and the thumbnail). Images are enlarged 2x with RealESRGAN_x2plus (1536x864 becomes
3072x1728), or 4x with RealESRGAN_x4plus if 2x would still be under 2048 px.

- **Cached once:** results are saved in `generated/upscaled/`, named by a hash of the source image, so each image is upscaled only
  once. A rerun with the same images doesn't touch Kaggle at all; after `--new-images` only the new ones go.
- **How it runs:** the official Kaggle CLI uploads the images and `job.json` as the **private** dataset `<you>/ai-video-upscale-job`
  (a new version each time, old versions deleted), pushes `kaggle/upscale.ipynb` as the **private** kernel `<you>/ai-video-upscale`
  with GPU T4 and internet on, checks it every minute with its latest progress line, and downloads the results. You never open the
  website. The notebook works in 512 px tiles in fp16, so any size fits on a T4. It loads the models with
  [spandrel](https://github.com/chaiNNer-org/spandrel) instead of the `basicsr` package, which breaks on current torchvision.
- **GPU time** is printed at the end from the session's own log timestamps, which is what Kaggle counts against the ~30 free GPU hours
  a week. Each run has a hard time limit (15 min plus ~36 s per image, at most 2 hours), so a stuck run can't use up the quota.
  **Measured on Script2** (October 2026): 38 images at 1536x864, 2x on one T4, took **2.0 s each** and a **1.9-minute
  session** in all (about 0.6 min of install and model download), plus a few minutes queued and uploading. That's about 1% of a
  week's free GPU hours.
- **If it fails** (no or a rejected token, Kaggle down, a timeout, the weekly GPU hours used up), the render never stops: it uses
  the original images and prints `WARNING: upscaling skipped: ...`, repeated at the end of the run. The next render retries; only
  the missing images are sent. Ctrl+C stops *watching*, not the run.
- **Setup, once:** on Kaggle, go to **Settings > API Tokens > Generate New Token** and add `KAGGLE_API_TOKEN=...` to `.env`. This is the
  new-style token, not the legacy `kaggle.json`. It's only given to the Kaggle CLI through its environment; it's never printed, logged
  or written to a file. Kaggle may ask you to verify your phone number before kernels can use GPUs and the internet.

## Tests
```
.venv\Scripts\python -m pip install pytest
.venv\Scripts\python -m pytest
```
The tests cover script parsing (including `[video]`/`[visual:]` tags and character lines), `--check`, `make_video.toml` defaults, channel
profiles (choosing the channel, the settings layers, per-channel outputs, branding clips, their padding, sound and lengths, templates), the character sheet
(names swapped for roles, only the scene's own characters), caption timing and alignment, AI image providers, models, sizes and their fallbacks,
the Kaggle runner, upscaling and `--engine chatterbox` (one batch, the caches, resuming, the fallbacks), the daily cap, drafts and
brainstorming, stock media
(search caching, rate limits, scoring, the per-format choice, credits), caption styles (key words, line cuts, faces), YouTube metadata (chapters, title and tag limits,
the thumbnail), `research.py` (search parsing, missing captions, IP blocks) and the Remotion renderer's planning (timeline and crossfade
maths, camera moves that never show an edge, per-format images, caption timing, place detection, when parallax is used). In `remotion/`,
`npm run typecheck` checks the React code.
All Cloudflare, Pollinations, Pexels, Pixabay, Kaggle, YouTube and transcript calls are mocked, so they don't need FFmpeg, a GPU or internet. They also check that
`kaggle/upscale.ipynb` and `kaggle/chatterbox.ipynb` match their `.py` sources. After editing the `.py`, run `.venv\Scripts\python kaggle\build_notebook.py`.

**CI:** GitHub Actions (`.github/workflows/ci.yml`) runs the tests on Windows, a smoke render and the Remotion type check on every pull
request and on `main`, with no secrets. The smoke render (`tests/test_smoke_render.py`, about a minute; locally with
`SMOKE_RENDER=1`) makes a tiny final render with the real FFmpeg and Remotion, fully offline (synthetic narration, local
pictures, branding clips with their own sound, music) and checks both orientations, their lengths, each video's captions, the
intro's sound and the Upload manifest. Changes reach `main` through a pull request whose checks pass.

## Credits
The bundled font, `assets/fonts/NotoSans-Bold.ttf`, is licensed under the SIL Open Font License (`assets/fonts/OFL.txt`).
Map shapes come from [world-atlas](https://github.com/topojson/world-atlas) (Natural Earth, public domain). The parallax depth model is
Depth Anything V2 Small (Apache-2.0). The ambient sounds in `assets/sfx/` are CC0: wind ("Howling wind" by Tvabutzku1234), water
("stream-river-water-up-close" by jackthemurray), birds ("Birds chirping in a garden", recorded in Abuja, by Akum20), market
("Market activities taking place record" by Aishatu98) and rain ("Light Rain Distant Thunder" by kvgarlic), all from Wikimedia
Commons (links in `assets/sfx/sources.json`); the fire crackle is generated by `sfx.py`. Upscaling uses Real-ESRGAN's weights (BSD-3-Clause) through spandrel (MIT). `.claude/skills/remotion-best-practices` is Remotion's own agent skill ([remotion-dev/skills](https://github.com/remotion-dev/skills)).
