"""Brainstorming with Pollinations: pictures to choose from, never used in a render and no Cloudflare neurons.

  --thumbnail-ideas N   N thumbnail options in Working/thumbnail_ideas/: the opening scene drawn N different ways, each
                        with the thumbnail words over it (the words are built from the script, as youtube.md's are
                        without the text model)
  --style-test "a | b"  one scene (--style-test-scene, default the first without a [visual:] file) drawn once in each
                        style into Working/style_tests/, to pick a style before a render

Prompts come from Pollinations' text model (a template if it can't answer) and the images from Pollinations, into the
draft cache (generated/drafts/). Both stop before any voice or video.
"""
import re
import shutil

import ai_visuals
import make_video as mv

# one camera idea per thumbnail option, so the options differ in more than the seed
THUMB_SHOTS = (
    "close-up of the main person's face showing a strong, clear emotion",
    "wide dramatic shot of the whole setting, with the key object large in the foreground",
    "the key object held up to the camera in both hands, sharp, with the setting softly blurred behind",
    "low-angle hero shot of the main person against a bright sky",
    "the moment of discovery, seen over the main person's shoulder",
    "bright, high-contrast close-up of the key detail",
)


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "style"


def providers_for(args):
    """Pollinations for images and prompts: brainstorming spends no Cloudflare neurons."""
    p = ai_visuals.Providers("draft", args.image_model, args.text_model, creds=("", ""), cap=args.max_daily_neurons)
    p.cf_ok = False
    return p


def base_prompt(scene, saved, providers, style, sheet):
    """The scene's saved prompt when Cloudflare wrote one for it, else a new one from Pollinations for this style."""
    prompt = ai_visuals.saved_prompt(saved, scene)
    if not prompt:
        prompt, _ = ai_visuals.write_prompt(scene["text"], None, providers, style, sheet, scene.get("description", ""))
    return ai_visuals.enforce_characters(prompt, scene["text"], sheet, scene.get("description", ""))


def thumbnail_ideas(args, script, scenes, font_path, out, providers):
    import youtube_meta
    folder = out.working / "thumbnail_ideas"
    folder.mkdir(parents=True, exist_ok=True)
    words = youtube_meta.fallback(script, scenes, [], args.channel)["thumbnail_text"]
    sheet = ai_visuals.build_sheet(mv.parse_characters(script), ai_visuals.load_characters(out.prompts))
    first = next((s for s in scenes if not s["visual"]), scenes[0])
    shot = base_prompt(first, ai_visuals.load_prompt_records(out.prompts), providers, args.style, sheet)
    size = "horizontal"
    print(f"Thumbnail ideas: {args.thumbnail_ideas} option(s) with Pollinations, words \"{words}\", into {folder}")
    made = []
    for k in range(args.thumbnail_ideas):
        idea = THUMB_SHOTS[k % len(THUMB_SHOTS)]
        prompt = ai_visuals.full_prompt(f"{idea}: {shot}", args.style, size)
        try:  # past the list of shots, the same shots come again with new seeds
            path, _, _ = providers.image(prompt, size=size, fresh=k >= len(THUMB_SHOTS))
        except ai_visuals.ProviderError as e:
            print(f"  idea {k + 1}: failed ({e})")
            continue
        made.append(youtube_meta.make_thumbnail(path, words, font_path, folder / f"idea_{k + 1}.jpg"))
        print(f"  idea {k + 1}: {made[-1].name} ({idea})")
    print(f"{len(made)} thumbnail idea(s) in {folder}. They're for choosing: the final render still makes "
          "Upload/thumbnail.jpg itself.")
    return made


def style_test(args, script, scenes, out, providers):
    styles = [s.strip() for s in args.style_test.split("|") if s.strip()]
    if not styles:
        raise SystemExit('--style-test needs styles separated by "|", like "watercolour | 3D animated film"')
    n = args.style_test_scene or next((i + 1 for i, s in enumerate(scenes) if not s["visual"]), 1)
    if not 1 <= n <= len(scenes):
        raise SystemExit(f"--style-test-scene: there is no scene {n} (the script has {len(scenes)})")
    scene = scenes[n - 1]
    folder = out.working / "style_tests"
    folder.mkdir(parents=True, exist_ok=True)
    size = "vertical" if args.format == "vertical" else "horizontal"
    sheet = ai_visuals.build_sheet(mv.parse_characters(script), ai_visuals.load_characters(out.prompts))
    print(f"Style test: scene {n} in {len(styles)} style(s) with Pollinations, into {folder}")
    made = []
    for k, style in enumerate(styles, 1):
        shot = base_prompt(scene, {}, providers, style, sheet)  # a prompt written for each style
        try:
            path, _, _ = providers.image(ai_visuals.full_prompt(shot, style, size), size=size)
        except ai_visuals.ProviderError as e:
            print(f"  {style}: failed ({e})")
            continue
        made.append(folder / f"scene{n}_{k}_{slug(style)}{path.suffix}")
        shutil.copy(path, made[-1])
        print(f"  {made[-1].name}: {style}")
    print(f"{len(made)} style test(s) in {folder}. Put the one you like in make_video.toml as style = \"...\".")
    return made


def run(args, script, scenes, font_path, out):
    """--thumbnail-ideas and/or --style-test, then stop."""
    providers = providers_for(args)
    ai_visuals.assign_ids(scenes)  # saved prompts are found by scene id
    if args.thumbnail_ideas:
        thumbnail_ideas(args, script, scenes, font_path, out, providers)
    if args.style_test:
        style_test(args, script, scenes, out, providers)
    if providers.counts["pollinations_text"]:
        print(f"  Pollinations text model: {providers.counts['pollinations_text']} prompt(s); no Cloudflare neurons")
