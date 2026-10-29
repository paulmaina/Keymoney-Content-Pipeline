"""YouTube upload kit, written by make_video.py after a render (--no-metadata skips it):

  Exports/<name>_youtube.md       3 title options, a description with chapters, and 10-15 tags
  Exports/<name>_thumbnail.jpg    1280x720 JPEG under 2 MB with 2-4 words of bold text

Chapters come from the real scene timings, grouped by the script's all-caps section headings
("# HOOK", "# HACK 1 NIGERIA - Visuals: ..."). YouTube only shows chapters when the first starts at 0:00,
there are at least 3 and each lasts at least 10 seconds, so a short section is merged into its neighbour,
and the list is left out when fewer than 3 remain (or the script has no headings).

Titles, the summary, tags and the thumbnail words come from one call to a Workers AI text model (~65 neurons with
the default 70B); the description opens with the narration's own first lines. Without Cloudflare credentials, or when
the reply is unusable, they're built from the script.
The thumbnail uses the most colourful, contrasty scene picture; when every scene is a title card it draws one
with the image model (about 132 neurons with FLUX.2 klein 4B, cached), or uses a plain background.
"""
import io
import json
import re
from collections import Counter
from pathlib import Path

import ai_visuals
import make_video as mv

META_MODEL = ai_visuals.METADATA_MODEL
META_TOKENS = 450  # max reply tokens; the estimate assumes all of them
MAX_NARRATION = 6000  # characters of narration sent to the model
TITLE_MAX = 59  # under 60 characters, so phones don't cut them off
TITLE_MIN = 20
CLAUSE_BREAK = re.compile(r"[,;:(–—|]| (?:that|which|who|where|when|so|because|but) ", re.I)
TITLES = 3
THUMB_CHARS = 28  # thumbnail words, so they fit two big lines
MIN_CHAPTER, MIN_CHAPTERS = 10, 3
TAGS = (10, 15)
TAG_MAX, TAGS_TOTAL = 30, 500  # YouTube allows 500 characters of tags in all
THUMB_SIZE = (1280, 720)
THUMB_MAX_BYTES = 2 * 1024 * 1024
THUMB_FILL, THUMB_STROKE = (255, 214, 0), (0, 0, 0)
ACRONYMS = {"CTA", "AI", "DIY", "FAQ", "USA", "UK", "UAE", "DRC"}
STOPWORDS = set("""a an and are as at be but by can do for from has have here how i if in into is it its just
like more most no not now of on one or our out so than that the their them then there these they this those to
up us was we were what when where which who why will with you your yet very about after all also any been before
being both each even every few get gets got had his her him she he me my new over own same some such too under
until while would could should may might must let lets make makes made welcome hello through around because
one two three four five six seven eight nine ten""".split())

SYSTEM = (
    "You write YouTube metadata for the channel {channel}. You are given a video's chapters and narration. "
    "Use only facts stated in the narration; never invent numbers, places or claims. "
    "Reply with one JSON object and nothing else, with these keys:\n"
    '"titles": 6 different titles, each 35 to 59 characters, in natural grammatical English, that make a '
    "viewer curious with a specific detail the narration states (a place, an object, a result it describes). "
    "Every title must be literally true according to the narration: check who or what each verb applies to. "
    "For example, for a video about a fishing town that dries fish on its roofs, good titles are "
    "'The Town That Dries Its Fish on Rooftops in the Sun' and 'Why Fishermen in Oman Use Their Roofs as Ovens'. "
    "No clickbait: no 'you won't believe', 'shocking', 'secret', 'insane', no exclamation marks, "
    "no promises the video doesn't keep; no emoji, no all-caps sentences;\n"
    '"summary": 2 sentences saying what the video covers;\n'
    '"tags": 12 search phrases people would type, lower case, 1-4 words each;\n'
    '"hashtags": 3 hashtags without spaces;\n'
    '"thumbnail_text": one string of 2 or 3 punchy words (at most 24 characters) for the thumbnail.'
)
CLICKBAIT = re.compile(r"!|won'?t believe|shocking|secret|insane|mind[- ]?blowing|jaw[- ]?dropping|gone wrong|"
                       r"never seen|will blow|what happens next|\bmust see\b", re.I)


# ---------- chapters ----------

def timestamp(t):
    t = int(t)
    h, m, s = t // 3600, t // 60 % 60, t % 60
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def section_key(label):
    """'HACK 1 NIGERIA' and 'HACK 1' are the same section."""
    m = re.match(r"[A-Z&']+(?: \d+)?", label)
    return m.group(0) if m else label


def nice(text):
    return " ".join(w if w in ACRONYMS else w.capitalize() for w in text.split())


def chapter_title(label):
    """'HACK 2 BRAZIL / PHILIPPINES' -> 'Hack 2: Brazil / Philippines'."""
    key = section_key(label)
    rest = label[len(key):].strip(" -–—:/")
    if not rest:
        return nice(key)
    joined = re.match(r"&|AND\b", rest)  # "OUTRO & CTA" continues the name rather than adding a place
    return f"{nice(key)}{' ' if joined else ': '}{nice(rest).replace('And ', 'and ', 1) if joined else nice(rest)}"


def chapters(durations, sections, total=None):
    """[(start seconds, title)] from each scene's duration and section heading, or [] when YouTube
    wouldn't show them. Scenes before the first heading join the first chapter."""
    total = sum(durations) if total is None else total
    groups, t = [], 0.0  # [start, key, labels]
    for dur, label in zip(durations, sections):
        if label is None or (groups and section_key(label) == groups[-1][1]):
            if groups and label:
                groups[-1][2].append(label)
            elif not groups:
                groups.append([t, None, []])
        elif groups and groups[-1][1] is None:  # untitled opening scenes take the first heading
            groups[-1][1:] = [section_key(label), [label]]
        else:
            groups.append([t, section_key(label), [label]])
        t += dur
    if not groups or all(g[1] is None for g in groups):
        return []
    chaps = [[int(g[0]), chapter_title(max(g[2], key=len))] for g in groups]
    chaps[0][0] = 0
    end = int(total)
    while len(chaps) > 1:
        lengths = [b[0] - a[0] for a, b in zip(chaps, chaps[1:])] + [end - chaps[-1][0]]
        short = next((i for i, n in enumerate(lengths) if n < MIN_CHAPTER), None)
        if short is None:
            break
        del chaps[short + 1 if short == 0 else short]  # a short chapter is absorbed by the one before it
    if len(chaps) < MIN_CHAPTERS or end - chaps[-1][0] < MIN_CHAPTER:
        return []
    return [(s, title) for s, title in chaps]


# ---------- text ----------

def shorten(text, limit=TITLE_MAX):
    """Cut to the limit at a word boundary, preferably before a clause ("..., where", " that ", " so ")
    so the result doesn't end on a dangling word."""
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    cut = text[:limit + 1].rsplit(" ", 1)[0] if " " in text[:limit + 1] else text[:limit]
    breaks = [m.start() for m in CLAUSE_BREAK.finditer(cut) if m.start() >= limit * 0.4]
    if breaks:
        cut = cut[:breaks[-1]]
    return cut.rstrip(" ,;:-–—|/&")


def lead(text, min_chars, limit):
    """Opening sentences of `text`, adding sentences until min_chars while staying within limit.
    The first sentence is kept whole even when it's longer (callers shorten if they must)."""
    out = ""
    for s in (s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s):
        if out and (len(out) >= min_chars or len(out) + 1 + len(s) > limit):
            break
        out = f"{out} {s}".strip()
    return out


def script_title(path):
    """The script's first line, when it's a '# ...' title rather than a tag, label or heading."""
    lines = path.read_text(encoding="utf-8-sig").strip().splitlines()
    line = lines[0].strip() if lines else ""
    if not line.startswith("#") or mv.CHARACTER_LINE.match(line) or mv.DESCRIPTION_LINE.match(line):
        return ""
    text = line.lstrip("#").strip()
    if mv.SECTION_LINE.match(line) or re.match(r"^\w+\s*:", text) or len(text) < 12:
        return ""
    return text if is_title_case(text) else ""  # a title is in Title Case, a note isn't


def words_of(text):
    return re.findall(r"[A-Za-z][A-Za-z'-]+", text)


def keywords(texts, n):
    """Search phrases from the narration: repeated two-word phrases, repeated words, the other two-word
    phrases in order, then the remaining words. Phrases never cross punctuation."""
    clauses = [[w.lower() for w in words_of(c)] for t in texts for c in re.split(r"[.,!?;:—–()]", t)]
    pairs = Counter(f"{a} {b}" for c in clauses for a, b in zip(c, c[1:])
                    if a not in STOPWORDS and b not in STOPWORDS and len(a) > 2 and len(b) > 2)
    singles = Counter(w for c in clauses for w in c if w not in STOPWORDS and len(w) > 3)
    ranked = sorted(singles, key=lambda w: (-singles[w], -len(w)))
    out = [p for p, c in pairs.most_common() if c >= 2] + [w for w in ranked if singles[w] >= 2]
    out += list(pairs) + ranked
    return dedupe(out)[:n]


def hashtag(text):
    words = re.findall(r"[A-Za-z0-9]+", text)
    return "#" + "".join(w[:1].upper() + w[1:] for w in words) if words else ""


def fallback(path, scenes, chaps, channel):
    """Metadata built from the script alone (no network)."""
    texts = [s["text"] for s in scenes]
    places = [p.strip() for _, t in chaps if ": " in t for p in t.split(": ", 1)[1].split("/")]
    names = list(mv.parse_characters(path))
    title = script_title(path)
    # a file name like "Script2" is no title, so "<title> | channel" only when the script has one
    titles = [title, *(lead(t, 30, TITLE_MAX) for t in texts[:6]), f"{title} | {channel}" if title else ""]
    tags = [channel.lower(), *(p.lower() for p in places), *(n.lower() for n in names), *keywords(texts, TAGS[1])]
    covered = [t for _, t in chaps if section_key(t.upper()) not in ("HOOK", "INTRO", "OUTRO", "CTA")]
    summary = (f"In this video: {'; '.join(covered)}." if covered
               else " ".join(shorten(lead(t, 1, 160), 160) for t in texts[1:3]))
    big = [w.upper() for w in words_of(title or lead(texts[0], 30, TITLE_MAX)) if w.lower() not in STOPWORDS]
    return {"titles": titles, "hook": shorten(lead(texts[0], 120, 250), 250), "summary": summary, "tags": tags,
            "hashtags": [hashtag(channel), *(hashtag(t) for t in (places + [t for t in tags[1:] if " " in t])[:2])],
            "thumbnail_text": " ".join(big[:3]) if len(big) >= 2 else "WATCH THIS"}


def parse_reply(text):
    """The JSON object in a model reply, or {} if there isn't a usable one."""
    if not text:
        return {}
    start = text.find("{")
    if start < 0:
        return {}
    try:
        data = json.loads(close_json(text[start:]))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def close_json(text):
    """The first JSON object in text, with any brackets or string the model forgot to close added back
    (small models often stop right before the final '}')."""
    stack, in_str, escaped = [], False, False
    for i, ch in enumerate(text):
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if stack:
                stack.pop()
            if not stack:
                return text[:i + 1]
    tail = text + ('"' if in_str else "")
    return re.sub(r"[,\s]+$", "", tail) + "".join(reversed(stack))


def str_list(value):
    if isinstance(value, str):
        value = re.split(r"[,\n]", value)
    return [" ".join(str(v).split()) for v in value if str(v).strip()] if isinstance(value, list) else []


def dedupe(items):
    seen, out = set(), []
    for x in items:
        k = x.lower()
        if x and k not in seen:
            seen.add(k)
            out.append(x)
    return out


SMALL_WORDS = set("a an and as at but by for from in into of on or the to via vs with".split())


def title_case(title):
    """'How clay pots cool food' -> 'How Clay Pots Cool Food'; titles already in Title Case are kept."""
    if is_title_case(title):
        return title
    words = title.split()
    return " ".join(w if i and w.lower() in SMALL_WORDS or any(c.isupper() for c in w[1:]) else w[:1].upper() + w[1:]
                    for i, w in enumerate(words))


def is_title_case(text):
    long_words = [w for w in words_of(text) if len(w) > 3]
    return sum(w[0].isupper() for w in long_words) >= 0.6 * len(long_words)


# title words that carry no claim, so they needn't be in the narration
GENERIC = set("""idea ideas clever simple brilliant genius smart hack hacks trick tricks way ways world village villages
people use uses using turn turns make makes bring brings keep keeps help helps how why what solve solves problem
problems solution solutions everyday daily ancient old free cheap real""".split())


def stem(word):
    w = word.lower().replace("'s", "").strip("'")
    for suffix in ("ness", "ing", "ers", "er", "ed", "es", "s"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            return w[:-len(suffix)]
    return w


def ungrounded(text, narration):
    """Words of text (4+ letters) that aren't in the narration: the same stem ('cools' ~ 'cool'), or one
    extending a 5+ letter narration word by up to 3 letters ('Nigerian' ~ 'Nigeria', 'spoilage' ~ 'spoil'),
    but 'rainwater' doesn't match 'rainforest'. Catches invented places, materials and claims."""
    known = {stem(w) for w in words_of(narration)}

    def found(w):
        s = stem(w)
        return s in known or any(len(k) >= 5 and s.startswith(k) and len(s) - len(k) <= 3 for k in known)
    return [w for w in words_of(text) if len(w) > 3 and w.lower() not in STOPWORDS | GENERIC and not found(w)]


def merge(reply, base, narration=None):
    """Model fields where they're valid, the script-built ones otherwise; enforces every limit.
    With the narration, model titles and tags may only use its words (plus GENERIC title words)."""
    model_titles = [title_case(t.strip("\"'“”").strip()) for t in str_list(reply.get("titles"))]
    model_titles = [t for t in model_titles if not CLICKBAIT.search(t)
                    and (narration is None or not ungrounded(t, narration))]
    candidates = model_titles + base["titles"]
    candidates = [t for t in candidates if len(t) >= TITLE_MIN]
    # whole titles first; a title cut to fit ("... Cities Should") only when there aren't 3 that fit
    titles = dedupe([t for t in candidates if len(t) <= TITLE_MAX] + [shorten(t) for t in candidates])[:TITLES]

    model_tags = [t for t in str_list(reply.get("tags")) if narration is None or not ungrounded(t, narration)]
    tags = [t.strip("#\"' ").lower() for t in model_tags + base["tags"]]
    tags = dedupe([t for t in tags if t and len(t) <= TAG_MAX])
    kept, used = [], 0
    for t in tags:
        cost = len(t) + 1 + (2 if " " in t else 0)  # YouTube counts commas, and quotes around phrases
        if len(kept) < TAGS[1] and used + cost <= TAGS_TOTAL:
            kept.append(t)
            used += cost

    tags_hash = [hashtag(h) for h in str_list(reply.get("hashtags"))]
    hashtags = dedupe([base["hashtags"][0], *tags_hash, *base["hashtags"][1:]])
    hashtags = [h for h in hashtags if 2 < len(h) <= 30][:3]

    def sentence(key, limit):
        v = reply.get(key)
        if not (isinstance(v, str) and 20 <= len(v.strip()) <= limit):
            return base[key]
        v = " ".join(v.split())
        return v if v[-1] in ".!?…\"'”" else v + "."

    options = reply.get("thumbnail_text") or ""
    options = options if isinstance(options, list) else [options]  # some replies give several choices
    # the hook is always the narration's own opening: the models' hooks were vaguer in every test
    return {"titles": titles, "hook": base["hook"], "summary": sentence("summary", 450), "tags": kept,
            "hashtags": hashtags, "thumbnail_text": thumb_text([*options, *model_titles]) or base["thumbnail_text"],
            "from_model": bool(reply),
            "script_titles": [t for t in titles if reply and t not in model_titles and t not in map(shorten, model_titles)]}


def thumb_text(options):
    """The first option that is 2-4 words and fits the thumbnail, or its first 2-3 key words."""
    for option in options:
        words = str(option).replace('"', "").upper().split()
        if 2 <= len(words) <= 4 and len(" ".join(words)) <= THUMB_CHARS:
            return " ".join(words)
    for option in options:
        words = [w.strip(":,.;?") for w in str(option).upper().split()]
        words = [w for w in words if w and w.lower() not in STOPWORDS]
        for n in (3, 2):
            if len(words) >= n and len(" ".join(words[:n])) <= THUMB_CHARS:
                return " ".join(words[:n])
    return ""


def messages(scenes, chaps, channel):
    chapter_list = "\n".join(f"{timestamp(s)} {t}" for s, t in chaps) or "(none)"
    narration = shorten(" ".join(s["text"] for s in scenes), MAX_NARRATION)
    return [{"role": "system", "content": SYSTEM.format(channel=channel)},
            {"role": "user", "content": f"Chapters:\n{chapter_list}\n\nNarration:\n{narration}"}]


def estimate(model, msgs, max_tokens=META_TOKENS):
    rate_in, rate_out = ai_visuals.TEXT_NEURONS.get(model, ai_visuals.TEXT_NEURONS[ai_visuals.TEXT_MODEL])
    return (sum(len(m["content"]) for m in msgs) / 4 * rate_in + max_tokens * rate_out) / 1e6


def ai_line(scenes):
    footage = [d.resolve() for d in mv.FOOTAGE_DIRS]

    def is_ai(s):
        src = s.get("upscaled_from") or s.get("src")  # an upscaled picture is judged by the original
        return bool(s.get("ai")) or bool(src and not s["visual"] and Path(src).resolve().parent not in footage)
    if any(map(is_ai, scenes)):  # one AI image is enough to disclose it
        return "The visuals in this video are AI-generated, and the narration is an AI voice."
    return "The narration is an AI voice; this video has no AI-generated visuals."


def description(meta, chaps, scenes, channel):
    parts = [meta["hook"], meta["summary"]]
    if chaps:
        parts.append("Chapters:\n" + "\n".join(f"{timestamp(s)} {t}" for s, t in chaps))
    parts += [ai_line(scenes), f"Subscribe to {channel} for more.", " ".join(meta["hashtags"])]
    return "\n\n".join(p for p in parts if p)


def markdown(name, meta, desc, chaps, thumb, total, notes):
    lines = [f"# YouTube upload: {name}", "",
             f"Length {timestamp(total)}. Metadata written by "
             f"{'the Workers AI text model' if meta['from_model'] else 'make_video.py from the script (no AI)'}; "
             "check it before uploading.", "", "## Title options", ""]
    lines += [f"{i}. {t} ({len(t)} characters{', built from the script' if t in meta.get('script_titles', ()) else ''})"
              for i, t in enumerate(meta["titles"], 1)]
    lines += ["", "## Description", "", "```text", desc, "```", "", "## Chapters", ""]
    if chaps:
        lines += [f"- {timestamp(s)} {t}" for s, t in chaps]
        lines += ["", "Already in the description above; YouTube reads them from there."]
    else:
        lines += [f"None. {notes.get('chapters', '')}".strip()]
    lines += ["", f"## Tags ({len(meta['tags'])})", "", "```text", ", ".join(meta["tags"]), "```",
              "", "## Thumbnail", "", f"`{thumb.name}` (1280x720): \"{meta['thumbnail_text']}\" on "
              f"{notes.get('thumbnail', 'a scene image')}.", "", "## Before uploading", "",
              "- In YouTube Studio, answer Yes to \"altered or synthetic content\" if any AI scene looks realistic.",
              "- Run the fact-check skill on the script if you haven't, and keep the description's claims "
              "within what the video says.", ""]
    return "\n".join(lines)


# ---------- thumbnail ----------

def striking(img):
    """Colourfulness (Hasler & Süsstrunk) plus contrast, on a small copy."""
    import numpy as np
    a = np.asarray(img.convert("RGB").resize((160, 90)), dtype=np.float32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    rg, yb = r - g, 0.5 * (r + g) - b
    colour = np.hypot(rg.std(), yb.std()) + 0.3 * np.hypot(rg.mean(), yb.mean())
    return float(colour + 0.5 * (0.299 * r + 0.587 * g + 0.114 * b).std())


def scene_picture(s, work, i):
    """A still for the scene: its 16:9 image, else a frame from the middle of its video file."""
    src = mv.scene_src(s, "horizontal")
    if src and Path(src).suffix.lower() in mv.IMAGE_EXT:
        return Path(src)
    if not src:
        return None
    src = Path(src)
    out = work / f"thumb_frame_{i:03}.png"
    try:
        mid = mv.duration(src) / 2
        mv.run(["ffmpeg", "-y", "-ss", f"{mid:.2f}", "-i", src, "-frames:v", "1", out])
    except SystemExit:
        return None
    return out if out.exists() else None


def best_picture(scenes, work):
    """(path, scene number) of the most striking scene picture, or None if every scene is a title card."""
    from PIL import Image
    best = None
    for i, s in enumerate(scenes):
        path = scene_picture(s, work, i)
        if not path:
            continue
        try:
            with Image.open(path) as img:
                score = striking(img)
        except OSError:
            continue
        if best is None or score > best[0]:
            best = (score, path, i + 1)
    return best[1:] if best else None


def thumb_lines(words, font_path, size):
    from PIL import ImageFont
    font = ImageFont.truetype(str(font_path), size)
    if len(words) <= 2 or font.getlength(" ".join(words)) <= THUMB_SIZE[0] * 0.9:
        return [" ".join(words)], font
    cut = min(range(1, len(words)), key=lambda k: abs(font.getlength(" ".join(words[:k]))
                                                      - font.getlength(" ".join(words[k:]))))
    return [" ".join(words[:cut]), " ".join(words[cut:])], font


def make_thumbnail(picture, text, font_path, out):
    """1280x720 JPEG with bold outlined text over a darkened lower band; kept under 2 MB."""
    from PIL import Image, ImageDraw, ImageEnhance, ImageOps
    w, h = THUMB_SIZE
    if picture:
        with Image.open(picture) as src:
            img = ImageOps.fit(src.convert("RGB"), THUMB_SIZE, Image.LANCZOS)
        img = ImageEnhance.Contrast(ImageEnhance.Color(img).enhance(1.2)).enhance(1.1)
    else:
        img = Image.linear_gradient("L").resize(THUMB_SIZE).convert("RGB")
        img = ImageOps.colorize(img.convert("L"), (24, 40, 70), (230, 120, 30))
    band = Image.linear_gradient("L").resize((w, h // 2))  # transparent at the top, dark at the bottom
    shade = Image.new("RGB", (w, h // 2), (0, 0, 0))
    img.paste(shade, (0, h - h // 2), band.point(lambda v: int(v * 0.75)))

    words = text.upper().split()
    draw = ImageDraw.Draw(img)
    for size in range(220, 40, -6):
        lines, font = thumb_lines(words, font_path, size)
        block = "\n".join(lines)
        stroke = max(4, size // 14)
        box = draw.multiline_textbbox((0, 0), block, font=font, spacing=size // 8, stroke_width=stroke, align="center")
        if box[2] - box[0] <= w * 0.92 and box[3] - box[1] <= h * 0.46:
            break
    draw.multiline_text((w / 2, h - 36), block, font=font, fill=THUMB_FILL, anchor="md", align="center",
                        spacing=size // 8, stroke_width=stroke, stroke_fill=THUMB_STROKE)
    for quality in (92, 85, 75, 65, 50):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality, optimize=True)
        if buf.tell() < THUMB_MAX_BYTES:
            break
    out.write_bytes(buf.getvalue())
    return out


def thumb_size(providers):
    return "horizontal" if providers.model.sized else "square"


def generated_picture(scenes, providers, style):
    """An AI image for the thumbnail when no scene has a picture. Returns a path or None."""
    if not providers or not providers.cf_ok:
        return None
    s = scenes[0]
    size = thumb_size(providers)
    shot = ai_visuals.fallback_prompt(s["text"], s.get("description", ""))
    prompt = ai_visuals.full_prompt(shot, style, size)
    try:
        path, _, _ = providers.image(prompt, size=size)
    except ai_visuals.ProviderError as e:
        print(f"  thumbnail image failed ({e}); using a plain background")
        return None
    return path


# ---------- entry point ----------

def write(scenes, script, base, args, work, total, lead_in=0.0):
    """Write <base>_youtube.md and <base>_thumbnail.jpg. Returns both paths. total is the video's length and
    lead_in the seconds before the narration starts (the Remotion intro), which the first chapter covers."""
    channel = args.channel
    sections = mv.parse_sections(script)
    if len(sections) != len(scenes):
        sections = [None] * len(scenes)
    chaps = chapters([s["dur"] + (lead_in if i == 0 else 0) for i, s in enumerate(scenes)], sections, total)
    notes = {}
    if not chaps:
        notes["chapters"] = ("The script has no section headings (# HOOK, # HACK 1 ...)." if not any(sections) else
                             f"YouTube needs at least {MIN_CHAPTERS} chapters of {MIN_CHAPTER}+ seconds, "
                             f"and this {timestamp(total)} video's sections don't make that.")

    base_meta = fallback(script, scenes, chaps, channel)
    picture = best_picture(scenes, work)
    creds = ai_visuals.credentials(required=False)
    providers = None
    reply = {}
    if creds:
        providers = ai_visuals.Providers("cloudflare", args.image_model, args.metadata_model, creds=creds, poll_key="")
        msgs = messages(scenes, chaps, channel)
        cost = providers.model.neurons(*ai_visuals.dims(thumb_size(providers)))
        extra = "" if picture else f" + ~{cost:.0f} for a thumbnail image if not cached"
        print(f"YouTube metadata: one {args.metadata_model} call, ~{estimate(args.metadata_model, msgs):.0f} "
              f"neurons (estimate){extra}")
        reply = parse_reply(providers.text(msgs, max_tokens=META_TOKENS))
        if not reply:
            print("  the model's reply wasn't usable JSON; using titles and tags built from the script")
    else:
        print("YouTube metadata: no Cloudflare credentials, so titles and tags are built from the script (0 neurons)")
    meta = merge(reply, base_meta, " ".join([channel, *(s["text"] for s in scenes)]))

    if picture:
        notes["thumbnail"] = f"scene {picture[1]}'s picture (the most colourful and contrasty)"
        picture = picture[0]
    elif (picture := generated_picture(scenes, providers, args.style)):
        notes["thumbnail"] = "an AI image of the opening scene"
    else:
        notes["thumbnail"] = "a plain gradient (every scene is a title card)"
    thumb = make_thumbnail(picture, meta["thumbnail_text"], args.font.resolve(),
                           base.with_name(base.name + "_thumbnail.jpg"))
    desc = description(meta, chaps, scenes, channel)
    md = base.with_name(base.name + "_youtube.md")
    md.write_text(markdown(base.name, meta, desc, chaps, thumb, total, notes), encoding="utf-8")
    if providers:
        providers.report()
    return md, thumb
