"""Channel profiles: everything that makes a video look and sound like one channel.

  channels/<id>.toml            the channel's name, intro and outro text and colours, image style, voice, speed,
                                caption style, music and YouTube text style
  channels/<id>/branding/       your own intro and outro clips, used in place of the generated ones; README.txt there
                                lists the exact names, and the *_TEMPLATE.mp4 files are the generated versions to edit

A render picks its channel from a "# channel: <id>" line in the script, else --channel, else default-channel in
make_video.toml. Its videos go to Exports/<id>/<script name>/, so channels never mix.

Precedence of settings: make_video.toml < the channel's file < the command line.

  .venv\\Scripts\\python channels.py list
  .venv\\Scripts\\python channels.py new "Kitchen Science"      a new profile, its branding folder, README and templates
  .venv\\Scripts\\python channels.py templates smart_village_boy  (re)write a channel's README and template clips
"""
import argparse
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHANNELS_DIR = ROOT / "channels"
DEFAULT = "smart_village_boy"
CHANNEL_LINE = re.compile(r"^#\s*channel\s*:\s*(.*?)\s*$", re.I)
PROFILE_KEYS = {"name", "tagline", "colors", "youtube-style"}  # every other key in a profile is a make_video option
# the generated intro and outro's colours: profile key -> the Remotion theme's (remotion/src/theme.ts) name
COLORS = {"accent": ("yellow", "#FFD23F"), "button": ("red", "#E63946"), "background": ("night", "#0E2A3B"),
          "background-light": ("nightLight", "#1C4A63")}
FPS = 30
SIZES = {"horizontal": (1920, 1080), "vertical": (1080, 1920)}
# (kind, format) -> the file name in channels/<id>/branding/. "next" is the outro of a Short part that isn't the last
CLIPS = {("intro", "horizontal"): "intro_horizontal.mp4", ("intro", "vertical"): "intro_vertical.mp4",
         ("outro", "horizontal"): "outro_horizontal.mp4", ("outro", "vertical"): "outro_vertical.mp4",
         ("next", "vertical"): "outro_next_part_vertical.mp4"}
SUGGESTED = {"intro": (2, 4), "outro": (3, 5), "next": (3, 5)}  # seconds
MIN_CLIP = 1.0  # seconds: anything shorter can't carry the crossfade into the first scene


def slug(name):
    """'Smart Village Boy' -> 'smart_village_boy' (the id is also the file and Exports folder name)."""
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")


def script_channel(path):
    """The channel named by a '# channel: <id>' line in the script, or None."""
    try:
        lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return None
    for line in lines:
        if (m := CHANNEL_LINE.match(line.strip())) and m.group(1):
            return m.group(1)
    return None


def rel(path):
    """A path as shown to the user: relative to the project when it's inside it."""
    try:
        return Path(path).relative_to(ROOT)
    except ValueError:
        return Path(path)


def available():
    return sorted(p.stem for p in CHANNELS_DIR.glob("*.toml"))


class Channel:
    def __init__(self, cid, data, path=None):
        self.id = cid
        self.path = path
        self.name = data.get("name") or cid.replace("_", " ").title()
        self.tagline = data.get("tagline", "")
        self.youtube_style = data.get("youtube-style", "")
        colors = data.get("colors", {})
        self.brand = {theme: colors.get(key, default) for key, (theme, default) in COLORS.items()}
        self.options = {k: v for k, v in data.items() if k not in PROFILE_KEYS}

    @property
    def branding(self):
        return CHANNELS_DIR / self.id / "branding"

    def clip(self, kind, fmt):
        """Your clip for this part of the video, or None for the generated one. *_TEMPLATE.mp4 files never count."""
        name = CLIPS.get((kind, fmt))
        p = self.branding / name if name else None
        return p if p and p.is_file() else None


def load(name):
    """The channel profile for an id or a display name ("Smart Village Boy" works too)."""
    cid = slug(name)
    path = CHANNELS_DIR / f"{cid}.toml"
    if not path.exists():
        known = ", ".join(available()) or "none yet"
        sys.exit(f'Channel "{name}" not found: there is no {rel(path)}. Channels: {known}.\n'
                 f'Make one with: .venv\\Scripts\\python channels.py new "{name}"')
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        sys.exit(f"{path.name}: {e}")
    return Channel(cid, data, path)


# ---------- branding clips ----------

def probe(path):
    """{seconds, width, height, fps, audio} of a clip, or None when ffprobe can't read it."""
    import json
    import subprocess
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration:stream=codec_type,width,height,r_frame_rate", "-of", "json", str(path)],
                           capture_output=True, text=True)
    except OSError:
        return None
    if r.returncode != 0:
        return None
    try:
        data = json.loads(r.stdout)
        streams = data.get("streams", [])
        video = next(s for s in streams if s.get("codec_type") == "video")
        num, _, den = video.get("r_frame_rate", "0/1").partition("/")
        return {"seconds": float(data["format"]["duration"]), "width": int(video["width"]),
                "height": int(video["height"]), "fps": float(num) / float(den or 1) if float(den or 1) else 0.0,
                "audio": any(s.get("codec_type") == "audio" for s in streams)}
    except (StopIteration, KeyError, ValueError, ZeroDivisionError):
        return None


class Branding:
    """A render's intro and outro for each format: your clip from the channel's branding folder, or the generated one
    (`generated` = {kind: frames}) for any clip that's missing, unreadable or under a second."""

    def __init__(self, channel, generated):
        self.channel = channel
        self.generated = generated
        self._info = {}

    def info(self, kind, fmt):
        """(clip path, probe info) when your clip is used, else None."""
        key = (kind, fmt)
        if key not in self._info:
            p = self.channel.clip(kind, fmt) if self.channel else None
            info = probe(p) if p else None
            self._info[key] = (p, info) if info and info["seconds"] >= MIN_CLIP else None
        return self._info[key]

    def clip(self, kind, fmt):
        return (self.info(kind, fmt) or (None,))[0]

    def frames(self, kind, fmt):
        used = self.info(kind, fmt)
        if used:
            return round(used[1]["seconds"] * FPS)
        return self.generated["outro" if kind == "next" else kind]

    def outro_kind(self, part):
        """The outro of a video: "next" for a Short part with another after it ("Watch Part N"), else "outro"."""
        return "next" if part and part[0] < part[1] else "outro"

    def lines(self, formats):
        """--check: one line per intro and outro, with where it comes from and its length."""
        out = []
        for (kind, fmt), name in CLIPS.items():
            if fmt not in formats:
                continue
            label = {"intro": "intro", "outro": "outro", "next": "outro of every Short part but the last"}[kind]
            secs = self.frames(kind, fmt) / FPS
            used = self.info(kind, fmt)
            if used:
                info = used[1]
                lo, hi = SUGGESTED[kind]
                notes = [f"{info['width']}x{info['height']}"]
                if (info["width"], info["height"]) != SIZES[fmt]:
                    notes.append(f"fitted to {SIZES[fmt][0]}x{SIZES[fmt][1]} with padding, never stretched")
                if abs(info["fps"] - FPS) > 0.5:
                    notes.append(f"{info['fps']:.3g} fps, converted to {FPS}")
                notes.append("its own sound" if info["audio"] else "silent")
                warn = f"  ! suggested {lo}-{hi} s" if not lo <= info["seconds"] <= hi else ""
                out.append(f"{fmt} {label}: your clip {name}, {info['seconds']:.1f} s ({', '.join(notes)}){warn}")
            else:
                p = self.channel.clip(kind, fmt) if self.channel else None
                why = (f" ({name} is unreadable or under {MIN_CLIP:.0f} s, so it isn't used)" if p else "")
                what = "generated \"Watch Part N\" card" if kind == "next" else f"generated {kind}"
                out.append(f"{fmt} {label}: {what}, {secs:.1f} s{why}")
        return out


# ---------- the branding folder: README and templates ----------

README = """Branding clips for {name}
{rule}

Put your own intro and outro clips in this folder with exactly these names. Each one you add is used in
place of the generated version; any that's missing uses the generated one. Remove a file to go back to
the generated version. The Remotion renderer uses them (the default); --no-branding leaves out every intro
and outro.

  File                            Size (W x H)   Suggested length   Used for
  intro_horizontal.mp4            1920 x 1080    2-4 s              the start of the full video
  intro_vertical.mp4              1080 x 1920    2-4 s              the start of every Short
  outro_horizontal.mp4            1920 x 1080    3-5 s              the end of the full video
  outro_vertical.mp4              1080 x 1920    3-5 s              the end of a single Short, and of the last part
  outro_next_part_vertical.mp4    1080 x 1920    3-5 s              optional: the end of every Short part that has
                                                                    another after it (instead of "Watch Part 2");
                                                                    a single clip, so it can't show the part number

Format
  - MP4 (H.264), 30 fps. Other sizes and frame rates work: the clip is resized and padded with black bars
    to fit the frame, never stretched or cropped, and converted to 30 fps. Clips under 1 second are ignored.
  - The last half second of an intro crossfades into the first scene, and the first half second of an
    outro crossfades out of the last scene, so keep text and logos clear of those moments.
  - In a Shorts series, the "PART 1 OF 4" badge is still drawn over your vertical intro, near the middle.
  - Keep the bottom fifth and the right edge of vertical clips free of anything important: YouTube's
    buttons and title cover them.

Audio
  - The clip's own sound is kept (a silent clip is fine).
  - With --music, the music fades in after your intro ends, so it never plays over the intro's sound,
    and it ducks under the outro's sound.
  - Keep the clip's sound at speaking level or a little lower: about -16 LUFS, peaks under -1 dB.
  - Only use music, voices and footage you have the right to use.

Templates
  The *_TEMPLATE.mp4 files are the generated intro and outro exported for you to edit (silent). Files
  ending in _TEMPLATE are never used in a render: edit one, then save it under the exact name above.
  Make them again (after changing the channel's name, tagline or colours) with:
    .venv\\Scripts\\python channels.py templates {id}

--check lists which intro and outro each video will use, and their lengths.
"""


def write_readme(channel):
    channel.branding.mkdir(parents=True, exist_ok=True)
    title = f"Branding clips for {channel.name}"
    path = channel.branding / "README.txt"
    path.write_text(README.format(name=channel.name, id=channel.id, rule="=" * len(title)), encoding="utf-8")
    return path


def template_name(kind, fmt):
    return CLIPS[(kind, fmt)].replace(".mp4", "_TEMPLATE.mp4")


def export_templates(channel):
    """Render the generated intro and outro of each format (and the "Watch Part 2" card) into the branding folder as
    *_TEMPLATE.mp4 (needs Node.js for Remotion). Returns the files written."""
    import remotion_render
    ok, why = remotion_render.available()
    if not ok:
        print(f"Templates skipped: the Remotion renderer isn't available ({why}).")
        return []
    channel.branding.mkdir(parents=True, exist_ok=True)
    return remotion_render.render_branding_templates(channel, {key: channel.branding / template_name(*key)
                                                               for key in CLIPS})


PROFILE = '''# {name}: everything that makes a video look and sound like this channel.
# Any make_video option can go here too, written without the leading dashes. These settings win over
# make_video.toml; options typed on the command line win over both.
name = "{name}"
tagline = "{tagline}"   # under the name in the generated intro

# images
style = "{style}"

# voice
engine = "kokoro"      # "kokoro" offline, "edge" Microsoft voices online, "chatterbox" on a free Kaggle GPU
voice = "am_michael"   # kokoro: am_michael, af_heart, ...; edge: en-US-EmmaMultilingualNeural, ...
rate = "+0%"           # speed: "+15%" = 1.15x (every engine)

# captions: "auto" (bold for vertical, clean for horizontal), "bold", "clean" or "minimal"
caption-style = "auto"

# music: a file you have the right to use, ducked under the voice
# music = "Raw Footage/{id}_theme.mp3"
music-volume = 0.2

# how the YouTube titles and descriptions should sound (added to the writer's instructions)
youtube-style = ""

# the generated intro and outro's colours (your own clips in channels/{id}/branding/ replace them).
# Keep this table last: in TOML every line after [colors] belongs to it.
[colors]
accent = "#FFD23F"            # the channel name's last word, the "Watch Part" card
button = "#E63946"            # the subscribe button and the "Part 1 of 4" badge
background = "#0E2A3B"
background-light = "#1C4A63"
'''


def new_channel(name, templates=True):
    cid = slug(name)
    if not cid:
        sys.exit("A channel needs a name with letters or digits.")
    path = CHANNELS_DIR / f"{cid}.toml"
    if path.exists():
        sys.exit(f"{rel(path)} already exists.")
    CHANNELS_DIR.mkdir(exist_ok=True)
    path.write_text(PROFILE.format(name=name.replace('"', "'"), id=cid, tagline="", style=""), encoding="utf-8")
    channel = load(cid)
    print(f"Wrote {rel(path)}; edit its name, tagline, colours, style, voice and YouTube style.")
    print(f"Wrote {rel(write_readme(channel))}")
    for p in export_templates(channel) if templates else []:
        print(f"Wrote {rel(p)}")
    print(f'Use it with "# channel: {cid}" at the top of a script, or --channel {cid}.')
    return channel


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="the channels and which branding clips each has")
    new = sub.add_parser("new", help="a new channel profile, its branding folder, README.txt and template clips")
    new.add_argument("name")
    new.add_argument("--no-templates", action="store_true", help="skip rendering the template clips")
    tpl = sub.add_parser("templates", help="(re)write a channel's branding README.txt and *_TEMPLATE.mp4 clips")
    tpl.add_argument("channel")
    args = ap.parse_args(argv)
    if args.command == "new":
        new_channel(args.name, templates=not args.no_templates)
    elif args.command == "templates":
        channel = load(args.channel)
        print(f"Wrote {rel(write_readme(channel))}")
        for p in export_templates(channel):
            print(f"Wrote {rel(p)}")
    else:
        for cid in available():
            channel = load(cid)
            have = [name for key, name in CLIPS.items() if channel.clip(*key)]
            print(f"{cid}: {channel.name}; own clips: {', '.join(have) or 'none (generated intro and outro)'}")


if __name__ == "__main__":
    main()
