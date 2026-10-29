"""make_video.py --check: look a script over before spending anything.

Reports scene count, words and estimated seconds per scene, video and [visual:] scenes, the '#' comment
lines it skipped, and flags
overlong paragraphs, missing [visual:] files, too many video scenes and leftover [tags] that would be
spoken. Estimates Cloudflare neurons (counting cached prompts and images as free) and Kaggle GPU time.
No network, audio or ffmpeg is used.
"""
import json
import re
from pathlib import Path

import ai_visuals
import make_video as mv
import video_job

WPM = {"edge": 135, "kokoro": 160}  # measured on the demo: edge ~134 wpm, kokoro ~160 wpm at +0%
# the 2-3 sentence format: long paragraphs hold one image too long. Short fragments ("No fridge.") are fine,
# so more than 3 sentences only counts once the paragraph is also long-ish.
MAX_SENTENCES, MAX_WORDS, SENTENCE_WORDS = 3, 60, 40
MAX_VIDEO_SCENES = 6
TARGET_SCENES = (15, 20)
# measured on Kaggle T4 x2 (Oct 2026): ~6 min to install and load, 36 min per 960x544 clip
KAGGLE_SETUP_MIN = 6
KAGGLE_CLIP_MIN = {"960x544": 36, "1280x704": 61}  # 1280x704 is an estimate (1.7x the pixels), not measured
KAGGLE_WEEKLY_HOURS = 30
PREVIOUS_PROMPT_CHARS = 350  # a typical image prompt, passed as context to the next one
PROMPT_OUT_TOKENS, MOTION_OUT_TOKENS = 90, 40


def sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def speed(rate):
    try:
        return 1 + int(str(rate).strip().rstrip("%")) / 100
    except ValueError:
        return 1.0


def text_neurons(model, chars_in, tokens_out):
    rate_in, rate_out = ai_visuals.TEXT_NEURONS.get(model, ai_visuals.TEXT_NEURONS[ai_visuals.TEXT_MODEL])
    return (chars_in / 4 * rate_in + tokens_out * rate_out) / 1e6


def seconds_label(s):
    return f"{int(s // 60)}:{int(round(s % 60)):02}"


def analyse(script, args):
    """Everything --check reports, as a dict (printed by report())."""
    scenes = mv.parse_script(script)
    flags, notes = [], []
    comments = mv.parse_comments(script)
    for n, text in comments:
        if re.match(r"#\s*character\s*:", text, re.I):
            flags.append(f"line {n}: '{text[:50]}' looks like a character line but has no '=', ':' or ' - ' "
                         "between name and description, so it's skipped as a comment")
    for i, s in enumerate(scenes):
        if s["video"] and s["visual"]:
            flags.append(f"scene {i + 1}: has both [video] and [visual: {s['visual']}]; it keeps the file and won't be animated")
            s["video"] = False
    try:
        extra = video_job.parse_scene_numbers(args.video_scenes)
    except SystemExit as e:
        extra = []
        flags.append(str(e))
    for n in extra:
        if 1 <= n <= len(scenes):
            if scenes[n - 1]["visual"]:
                flags.append(f"--video-scenes {n}: that scene has a [visual:] file, so it won't be animated")
            else:
                scenes[n - 1]["video"] = True
        else:
            flags.append(f"--video-scenes {n}: there is no scene {n}")

    per_word = 60 / (WPM.get(args.engine, WPM["edge"]) * speed(args.rate))
    rows = []
    for i, s in enumerate(scenes):
        words = len(s["text"].split())
        n_sent = len(sentences(s["text"]))
        row = {"scene": i + 1, "words": words, "sentences": n_sent, "seconds": words * per_word + mv.PAUSE,
               "video": s["video"], "visual": s["visual"], "visual_found": None, "text": s["text"]}
        if s["visual"]:
            row["visual_found"] = mv.resolve_visual(s["visual"], script.parent) is not None
            if not row["visual_found"]:
                flags.append(f"scene {i + 1}: [visual: {s['visual']}] not found in \"Raw Footage\" or next to the script")
        if words > MAX_WORDS or (n_sent > MAX_SENTENCES and words > SENTENCE_WORDS):
            flags.append(f"scene {i + 1}: {n_sent} sentences / {words} words; aim for 2-3 sentences "
                         f"(about {MAX_WORDS} words at most)")
        for tag in re.findall(r"\[[^\]]*\]", s["text"]):
            flags.append(f"scene {i + 1}: '{tag}' isn't a tag the tool knows, so it would be spoken and captioned "
                         "(tags go at the start of a line: [video], [visual: file])")
        rows.append(row)

    n_video = sum(r["video"] for r in rows)
    if n_video > MAX_VIDEO_SCENES:
        flags.append(f"{n_video} scenes are marked [video]; keep it to {MAX_VIDEO_SCENES} at most "
                     f"(each costs ~{KAGGLE_CLIP_MIN['960x544']} min of Kaggle GPU time)")
    if not TARGET_SCENES[0] <= len(rows) <= TARGET_SCENES[1]:
        notes.append(f"{len(rows)} scenes; your format aims for {TARGET_SCENES[0]}-{TARGET_SCENES[1]}")
    if n_video and not 4 <= n_video <= 5:
        notes.append(f"{n_video} video scene(s); your format aims for 4-5")

    # Cloudflare: prompts and images not already saved/cached cost neurons
    base = mv.output_base(args.output, script)
    prompts_path = base.with_name(base.name + "_prompts.json")
    saved = ai_visuals.load_prompt_records(prompts_path)
    sheet = ai_visuals.build_sheet(mv.parse_characters(script), ai_visuals.load_characters(prompts_path))
    job_json = base.with_name(base.name + "_video_job") / "job.json"
    saved_motion = video_job.load_motion_prompts(job_json)
    cloudflare_images = args.image_provider in ("auto", "cloudflare")
    cache_ids = [args.image_model, ai_visuals.POLLINATIONS_CACHE_ID, ai_visuals.KAGGLE_CACHE_ID]
    est = {"prompts_new": 0, "prompts_saved": 0, "prompts_redescribed": 0, "images_new": 0, "images_cached": 0,
           "motion_new": 0, "motion_saved": 0, "neurons_prompts": 0.0, "neurons_images": 0.0, "neurons_motion": 0.0}
    sheet_chars = len(ai_visuals.sheet_text(sheet))
    for s, row in zip(scenes, rows):
        row["description"] = s["description"]
        row["kind"] = "own file" if s["visual"] else "video" if s["video"] else "image"
        if s["visual"]:
            continue
        prompt = ai_visuals.saved_prompt(saved, s)
        if s["text"] in saved and not prompt:
            est["prompts_redescribed"] += 1  # saved, but written before this description line existed or changed
        if prompt:
            est["prompts_saved"] += 1
            full = ai_visuals.full_prompt(ai_visuals.enforce_characters(prompt, s["text"], sheet), args.style)
            if any(ai_visuals.image_path(full, m).exists() for m in cache_ids):
                est["images_cached"] += 1
            else:
                est["images_new"] += 1
        else:
            est["prompts_new"] += 1
            est["images_new"] += 1
            chars = (len(ai_visuals.PROMPT_SYSTEM) + len(s["text"]) + len(s["description"]) + len(args.style)
                     + sheet_chars + PREVIOUS_PROMPT_CHARS)
            est["neurons_prompts"] += text_neurons(args.text_model, chars, PROMPT_OUT_TOKENS)
        if s["video"]:
            if video_job.saved_motion(saved_motion, s):
                est["motion_saved"] += 1
            else:
                est["motion_new"] += 1
                chars = (len(video_job.MOTION_SYSTEM) + len(prompt or "") + len(s["description"])
                         + PREVIOUS_PROMPT_CHARS)
                est["neurons_motion"] += text_neurons(args.text_model, chars, MOTION_OUT_TOKENS)
    if cloudflare_images:
        est["neurons_images"] = est["images_new"] * ai_visuals.FLUX_NEURONS
    est["neurons_total"] = est["neurons_prompts"] + est["neurons_images"] + est["neurons_motion"]

    clip_min = KAGGLE_CLIP_MIN.get(args.kaggle_size, KAGGLE_CLIP_MIN["960x544"])
    kaggle = {"clips": n_video, "size": args.kaggle_size, "clip_minutes": clip_min,
              "minutes": KAGGLE_SETUP_MIN + n_video * clip_min if n_video else 0,
              "measured": args.kaggle_size == "960x544"}
    return {"script": script, "rows": rows, "flags": flags, "notes": notes, "sheet": sheet, "comments": comments,
            "declared": mv.parse_characters(script), "estimates": est, "kaggle": kaggle,
            "total_seconds": sum(r["seconds"] for r in rows), "words": sum(r["words"] for r in rows),
            "prompts_path": prompts_path}


def report(script, args):
    a = analyse(script, args)
    rows, est, kg = a["rows"], a["estimates"], a["kaggle"]
    print(f"Script: {script.name}")
    print(f"{len(rows)} scenes, {a['words']} words, about {seconds_label(a['total_seconds'])} "
          f"({args.engine} voice at {args.rate}, ~{WPM.get(args.engine, WPM['edge'])} words/min at +0%)")
    print(f"Style: {args.style or '(none)'}")
    print()
    print("Each scene becomes: video = Kaggle clip (with --kaggle-video), image = Cloudflare image, own file = your [visual:]")
    print(f"{'#':>3} {'words':>5} {'~sec':>5}  {'becomes':<9} {'described':<9} narration / own file")
    for r in rows:
        if r["visual"]:
            what = f"[visual: {r['visual']}]" + ("" if r["visual_found"] else " MISSING")
        else:
            what = r["text"] if len(r["text"]) <= 50 else r["text"][:47] + "..."
        print(f"{r['scene']:>3} {r['words']:>5} {r['seconds']:>5.1f}  {r['kind']:<9} "
              f"{'yes' if r['description'] else '-':<9} {what}")
    videos = [r["scene"] for r in rows if r["kind"] == "video"]
    images = [r["scene"] for r in rows if r["kind"] == "image"]
    own = [r["scene"] for r in rows if r["kind"] == "own file"]
    print(f"\nVideo (Kaggle):    {', '.join(map(str, videos)) or 'none'}")
    print(f"Image (Cloudflare): {', '.join(map(str, images)) or 'none'}")
    if own:
        print(f"Own file:          {', '.join(map(str, own))}")
    described = [r for r in rows if r["description"]]
    if described:
        print(f"\nVisual descriptions used in prompts ({len(described)} of {len(rows)} scenes):")
        for r in described:
            d = r["description"]
            print(f"  {r['scene']:>3}: {d if len(d) <= 95 else d[:92] + '...'}")
    print()
    if a["flags"]:
        print(f"Flags ({len(a['flags'])}):")
        for f in a["flags"]:
            print(f"  ! {f}")
    else:
        print("Flags: none")
    for n in a["notes"]:
        print(f"  note: {n}")
    if a["comments"]:
        print(f"Comments skipped ({len(a['comments'])}; never spoken, captioned or used in prompts):")
        for n, text in a["comments"]:
            print(f"  line {n:>3}: {text if len(text) <= 90 else text[:87] + '...'}")
    if a["declared"]:
        print(f"Characters declared in the script: {', '.join(a['declared'])}")
    learned = [k for k in a["sheet"] if k not in a["declared"]]
    if learned:
        print(f"Characters learned in {a['prompts_path'].name}: {', '.join(learned)}")
    print()
    print("Cloudflare (with --generate-visuals; cached prompts and images are free):")
    print(f"  image prompts: {est['prompts_new']} new (~{est['neurons_prompts']:.0f} neurons), {est['prompts_saved']} saved")
    if est["prompts_redescribed"]:
        print(f"                 ({est['prompts_redescribed']} of the new ones replace saved prompts that were written "
              "without this scene's current description line)")
    if args.image_provider in ("auto", "cloudflare"):
        print(f"  images:        {est['images_new']} new (~{est['neurons_images']:.0f} neurons), {est['images_cached']} cached")
    else:
        print(f"  images:        {est['images_new']} new from {args.image_provider} (no Cloudflare neurons), "
              f"{est['images_cached']} cached")
    if kg["clips"]:
        print(f"  motion prompts: {est['motion_new']} new (~{est['neurons_motion']:.0f} neurons), {est['motion_saved']} saved")
    print(f"  total about {est['neurons_total']:,.0f} of {ai_visuals.FREE_NEURONS_PER_DAY:,} free neurons a day")
    print()
    if kg["clips"]:
        basis = "measured" if kg["measured"] else "estimated, not measured"
        print(f"Kaggle (with --kaggle-video): {kg['clips']} clip(s) at {kg['size']}, ~{KAGGLE_SETUP_MIN} min setup + "
              f"{kg['clips']} x ~{kg['clip_minutes']} min ({basis}) = about {kg['minutes'] / 60:.1f} h of the "
              f"~{KAGGLE_WEEKLY_HOURS} free GPU hours a week")
        if kg["minutes"] > 12 * 60:
            print("  that's longer than Kaggle's 12-hour session limit: the run will stop part-way, then use "
                  "--kaggle-resume to finish")
    else:
        print("Kaggle: no [video] scenes, so no GPU time needed")
    return a
