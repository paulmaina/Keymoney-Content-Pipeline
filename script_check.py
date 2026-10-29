"""make_video.py --check: look a script over before spending anything.

Reports scene count, words and estimated seconds per scene, [video] (stronger move) and [visual:] scenes,
the '#' comment lines it skipped, and flags overlong paragraphs, missing [visual:] files and leftover [tags] that
would be spoken. Estimates Cloudflare neurons (counting cached prompts and images as free), Kaggle GPU time for
--upscale and the render time. No network, audio or ffmpeg is used.
"""
import re

import ai_visuals
import make_video as mv

WPM = {"edge": 135, "kokoro": 160}  # measured on the demo: edge ~134 wpm, kokoro ~160 wpm at +0%
# the 2-3 sentence format: long paragraphs hold one image too long. Short fragments ("No fridge.") are fine,
# so more than 3 sentences only counts once the paragraph is also long-ish.
MAX_SENTENCES, MAX_WORDS, SENTENCE_WORDS = 3, 60, 40
TARGET_SCENES = (15, 20)
PREVIOUS_PROMPT_CHARS = 350  # a typical image prompt, passed as context to the next one
PROMPT_OUT_TOKENS, CHECK_OUT_TOKENS = 90, 60
# --upscale on Kaggle T4, measured on Script2 (Oct 2026): a 1.9 min session for 38 images at 1536x864 (2x),
# ~0.6 min of install and weights plus 2.0 s per image; rounded up a little
UPSCALE_SETUP_MIN, UPSCALE_SEC_PER_IMAGE = 1.0, 2.0
KAGGLE_WEEKLY_HOURS = 30


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


def check_neurons(model):
    """One --check-images call: the checker's instructions, the image and a short JSON reply."""
    return text_neurons(model, len(ai_visuals.QC_SYSTEM) + 300 + ai_visuals.QC_IMAGE_TOKENS * 4, CHECK_OUT_TOKENS)


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
    if not TARGET_SCENES[0] <= len(rows) <= TARGET_SCENES[1]:
        notes.append(f"{len(rows)} scenes; your format aims for {TARGET_SCENES[0]}-{TARGET_SCENES[1]}")
    if n_video and not 4 <= n_video <= 5:
        notes.append(f"{n_video} [video] scene(s); your format aims for 4-5")

    # Cloudflare: prompts and images not already saved/cached cost neurons
    base = mv.output_base(args.output, script)
    prompts_path = base.with_name(base.name + "_prompts.json")
    saved = ai_visuals.load_prompt_records(prompts_path)
    sheet = ai_visuals.build_sheet(mv.parse_characters(script), ai_visuals.load_characters(prompts_path))
    cloudflare_images = args.image_provider in ("auto", "cloudflare")
    model_id = ai_visuals.resolve_model(args.image_model)
    model = ai_visuals.model_info(model_id)
    formats = list(mv.FORMATS) if args.format == "both" else [args.format]
    sizes = formats if model.sized and args.image_provider != "pollinations" else ["square"]
    cache_ids = [model_id, ai_visuals.POLLINATIONS_CACHE_ID]
    fresh = ai_visuals.style_changed(prompts_path, args.style)
    if fresh:
        saved = {}
        notes.append(f"the style changed since {prompts_path.name} was written, so every prompt is written again")
    est = {"prompts_new": 0, "prompts_saved": 0, "prompts_redescribed": 0, "images_new": 0, "images_cached": 0,
           "neurons_prompts": 0.0, "neurons_images": 0.0, "sizes": sizes, "model": model_id,
           "per_image": [model.neurons(*ai_visuals.dims(z)) for z in sizes], "upscale_todo": 0, "checks": 0,
           "neurons_checks": 0.0}
    redo = getattr(args, "redo", set())
    sheet_chars = len(ai_visuals.sheet_text(sheet))
    for s, row in zip(scenes, rows):
        row["description"] = s["description"]
        row["kind"] = "own file" if s["visual"] else "image"
        if s["visual"]:
            continue
        prompt = ai_visuals.saved_prompt(saved, s)
        if s["text"] in saved and not prompt:
            est["prompts_redescribed"] += 1  # saved, but written before this description line existed or changed
        if prompt:
            est["prompts_saved"] += 1
            shot = ai_visuals.enforce_characters(prompt, s["text"], sheet, s["description"])
            for z in sizes:
                full = ai_visuals.full_prompt(shot, args.style, z)
                hit = next((p for m in cache_ids if (p := ai_visuals.image_path(full, m, z)).exists()), None)
                if hit and not args.new_images and row["scene"] - 1 not in redo:
                    est["images_cached"] += 1
                    if args.check_images and not ai_visuals.load_verdict(hit):
                        est["checks"] += 1  # cached, but never checked
                    if args.upscale and not upscale_cached(hit):
                        est["upscale_todo"] += 1
                else:
                    est["images_new"] += 1
                    est["neurons_images"] += model.neurons(*ai_visuals.dims(z)) if cloudflare_images else 0
        else:
            est["prompts_new"] += 1
            est["images_new"] += len(sizes)
            if cloudflare_images:
                est["neurons_images"] += sum(est["per_image"])
            chars = (len(ai_visuals.PROMPT_SYSTEM) + len(s["text"]) + len(s["description"]) + len(args.style)
                     + sheet_chars + PREVIOUS_PROMPT_CHARS)
            est["neurons_prompts"] += text_neurons(args.text_model, chars, PROMPT_OUT_TOKENS)
    if args.check_images:  # one check per new image; failures cost a redraw and a check each, not counted here
        est["checks"] += est["images_new"]
        est["neurons_checks"] = est["checks"] * check_neurons(args.vision_model)
    est["neurons_total"] = est["neurons_prompts"] + est["neurons_images"] + est["neurons_checks"]
    if args.upscale:  # new images are always under 2048 px, so each one goes to Kaggle once
        est["upscale_todo"] += est["images_new"]
    upscale_min = (UPSCALE_SETUP_MIN + est["upscale_todo"] * UPSCALE_SEC_PER_IMAGE / 60) if est["upscale_todo"] else 0
    return {"script": script, "rows": rows, "flags": flags, "notes": notes, "sheet": sheet, "comments": comments,
            "declared": mv.parse_characters(script), "estimates": est, "upscale_minutes": upscale_min,
            "total_seconds": sum(r["seconds"] for r in rows), "words": sum(r["words"] for r in rows),
            "prompts_path": prompts_path}


def upscale_cached(image):
    import upscale
    return upscale.long_side(image) >= upscale.MIN_SIDE or upscale.cached_path(image).exists()


def report(script, args):
    a = analyse(script, args)
    rows, est = a["rows"], a["estimates"]
    print(f"Script: {script.name}")
    print(f"{len(rows)} scenes, {a['words']} words, about {seconds_label(a['total_seconds'])} "
          f"({args.engine} voice at {args.rate}, ~{WPM.get(args.engine, WPM['edge'])} words/min at +0%)")
    print(f"Style: {args.style or '(none)'}")
    print()
    print("Each scene becomes: image = AI image, own file = your [visual:]; move = strong for [video] scenes")
    print(f"{'#':>3} {'words':>5} {'~sec':>5}  {'becomes':<9} {'move':<7} {'described':<9} narration / own file")
    for r in rows:
        if r["visual"]:
            what = f"[visual: {r['visual']}]" + ("" if r["visual_found"] else " MISSING")
        else:
            what = r["text"] if len(r["text"]) <= 50 else r["text"][:47] + "..."
        print(f"{r['scene']:>3} {r['words']:>5} {r['seconds']:>5.1f}  {r['kind']:<9} "
              f"{'strong' if r['video'] else 'normal':<7} {'yes' if r['description'] else '-':<9} {what}")
    strong = [r["scene"] for r in rows if r["video"]]
    images = [r["scene"] for r in rows if r["kind"] == "image"]
    own = [r["scene"] for r in rows if r["kind"] == "own file"]
    print(f"\nStrong move ([video]): {', '.join(map(str, strong)) or 'none'}")
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
    shapes = " + ".join("x".join(map(str, ai_visuals.dims(z))) for z in est["sizes"])
    each = " + ".join(f"{n:.0f}" for n in est["per_image"])
    print(f"Cloudflare (with --generate-visuals; cached prompts and images are free; "
          f"{est['model'].rsplit('/', 1)[-1]} at {shapes}, ~{each} neurons per scene):")
    print(f"  image prompts: {est['prompts_new']} new (~{est['neurons_prompts']:.0f} neurons), {est['prompts_saved']} saved")
    if est["prompts_redescribed"]:
        print(f"                 ({est['prompts_redescribed']} of the new ones replace saved prompts that were written "
              "without this scene's current description line)")
    if args.image_provider in ("auto", "cloudflare"):
        print(f"  images:        {est['images_new']} new (~{est['neurons_images']:.0f} neurons), {est['images_cached']} cached")
    else:
        print(f"  images:        {est['images_new']} new from {args.image_provider} (no Cloudflare neurons), "
              f"{est['images_cached']} cached")
    if args.check_images:
        print(f"  quality checks: {est['checks']} (~{est['neurons_checks']:.0f} neurons; each failed image adds a redraw "
              f"and a check, up to {ai_visuals.QC_RETRIES} times)")
    print(f"  total about {est['neurons_total']:,.0f} of {ai_visuals.FREE_NEURONS_PER_DAY:,} free neurons a day")
    if est["neurons_total"] > ai_visuals.FREE_NEURONS_PER_DAY:
        print("  ! that's more than the free allowance: --image-model flux-2-klein-4b, or render one --format")
    print()
    if args.upscale:
        if est["upscale_todo"]:
            print(f"Kaggle (--upscale): {est['upscale_todo']} image(s) to upscale, about {a['upscale_minutes']:.0f} min "
                  f"of the ~{KAGGLE_WEEKLY_HOURS} free GPU hours a week")
        else:
            print("Kaggle (--upscale): every image is already upscaled (cached), so no GPU time needed")
    render_estimate(a["total_seconds"], args)
    return a


def render_estimate(narration_seconds, args):
    """Local render time for the chosen renderer (Remotion: from the speed measured on this PC)."""
    import remotion_render as rr
    formats = list(mv.FORMATS) if args.format == "both" else [args.format]
    if args.renderer != "remotion":
        print(f"Render ({args.renderer}): a few minutes for {' + '.join(formats)}")
        return
    frames = round((narration_seconds + (0 if args.no_branding else (rr.INTRO + rr.OUTRO) / mv.FPS)) * mv.FPS)
    secs, measured = rr.estimate(frames, formats)
    print(f"Render (remotion): ~{rr.minutes(secs)} for {' + '.join(formats)} at ~{frames} frames each "
          f"({'measured on this PC' if measured else 'first-run guess'}), plus voice and captions")
