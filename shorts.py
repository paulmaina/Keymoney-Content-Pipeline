"""The vertical video as a numbered series of YouTube Shorts.

The horizontal video uses the whole script, however long. The vertical one is the same script cut into parts at scene
boundaries, each at most LIMIT seconds including the intro and outro, so every part fits Shorts (3 minutes or less).
Cuts go at chapter headings (# HOOK, # HACK 2 ...) wherever that's possible, so each part feels complete; among the
ways to do that, the fewest parts, then the most even lengths. Parts are saved as Upload/<name>_Short_Part1.mp4, ...;
a script that fits in one part makes a single Upload/<name>_Short.mp4 (see outputs.py).
"""
import make_video as mv

LIMIT = 170.0  # seconds per part, intro and outro included: 2:50, safely under Shorts' 3 minutes


def overhead(branding, fmt="vertical"):
    """Seconds a cut adds to its narration: the Remotion intro and outro (overlapping by a crossfade each), or half a
    crossfade without them. The ffmpeg renderer adds nothing. `branding` may be the render's channels.Branding, whose
    own clips can be longer or shorter than the generated intro and outro; a vertical part counts the longer of its two
    possible outros (the last part's, or "Watch Part N")."""
    import remotion_render as rr
    if branding is None:
        return 0.0
    if branding is True:
        return (rr.INTRO + rr.OUTRO - rr.TRANSITION) / mv.FPS
    if branding:
        outro = branding.frames("outro", fmt)
        if fmt == "vertical":
            outro = max(outro, branding.frames("next", fmt))
        return (branding.frames("intro", fmt) + outro - rr.TRANSITION) / mv.FPS
    return rr.TRANSITION // 2 / mv.FPS


def part_seconds(durations, branding=True, fmt="vertical"):
    return sum(durations) + overhead(branding, fmt)


def branding_of(args):
    """The channel's intro and outro with Remotion (its channels.Branding, or True for the generated ones), False for
    Remotion without them, None for the ffmpeg renderer."""
    if getattr(args, "renderer", "remotion") != "remotion":
        return None
    return False if getattr(args, "no_branding", False) else (getattr(args, "branding", None) or True)


def chapter_starts(sections):
    """Scene indexes where a new chapter begins ('HACK 1' and 'HACK 1 KENYA' are the same chapter)."""
    from youtube_meta import section_key
    keys = [section_key(s) if s else None for s in sections]
    return {i for i in range(1, len(keys)) if keys[i] and keys[i] != keys[i - 1]}


def split(durations, sections=(), limit=None, branding=True):
    """[(first scene, end scene)] (end exclusive) covering every scene. Each part is at most `limit` seconds (a single
    scene longer than that becomes a part of its own). Fewest cuts away from a chapter heading first, then the fewest
    parts, then the most even lengths."""
    n = len(durations)
    if not n:
        return []
    limit = LIMIT if limit is None else limit
    starts = chapter_starts(list(sections)) if len(sections) == n else set()
    best = [None] * (n + 1)  # best[b] = ((off-chapter cuts, parts, sum of squared lengths), start of the last part)
    best[0] = ((0, 0, 0.0), None)
    for b in range(1, n + 1):
        for a in range(b - 1, -1, -1):
            secs = part_seconds(durations[a:b], branding)
            if secs > limit and b - a > 1:
                break  # starting earlier only makes it longer
            if best[a] is None:
                continue
            off, parts, sq = best[a][0]
            cost = (off + (0 if a == 0 or a in starts else 1), parts + 1, sq + secs * secs)
            if best[b] is None or cost < best[b][0]:
                best[b] = (cost, a)
    out, b = [], n
    while b:
        a = best[b][1]
        out.append((a, b))
        b = a
    return out[::-1]


def plan(scenes, script, args, durations=None):
    """The vertical parts for this script: from the real narration lengths after narrate(), else `durations`."""
    sections = mv.parse_sections(script)
    durations = durations if durations is not None else [s["dur"] for s in scenes]
    return split(durations, sections, branding=branding_of(args))


def describe(parts, durations, sections, branding):
    """One line per part for --check and the render log: scene range, chapters and length."""
    from youtube_meta import chapter_title, section_key
    lines = []
    for k, (a, b) in enumerate(parts, 1):
        names = []
        for s in sections[a:b] if len(sections) == len(durations) else []:
            if s and (not names or section_key(s) != section_key(names[-1])):
                names.append(s)
        chaps = ", ".join(chapter_title(s) for s in names)
        secs = part_seconds(durations[a:b], branding)
        scene_range = f"scene {a + 1}" if b - a == 1 else f"scenes {a + 1}-{b}"
        warn = "  ! one scene alone is over the limit" if secs > LIMIT else ""
        lines.append(f"Part {k}: {scene_range}, {secs // 60:.0f}:{secs % 60:02.0f}{f' ({chaps})' if chaps else ''}{warn}")
    return lines
