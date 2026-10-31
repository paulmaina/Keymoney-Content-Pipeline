"""Resumable final renders: the stages a render finished, so a run that fails part-way picks up from the last good stage.

Working/render/ keeps each stage's files (the narration, the captions, Remotion's prepared pictures and bundle, each
video's silent render) and checkpoints.json lists the finished stages under a key: a hash of the script, the scenes'
pictures, the branding clips, music and font, the settings and the code. A run with the same key reuses them; any change
(an edited line, a new image, another voice or speed, a code update) starts the render over, and so does --fresh. A
successful final render removes Working/render/ unless --keep-work.
"""
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# options that don't change what a render makes (or only steer earlier steps whose results are in the scene pictures)
VOLATILE = {"fresh", "keep_work", "check", "redo_scenes", "redo", "regenerate", "new_images", "visuals_only",
            "prompts_only", "kaggle_work", "max_daily_neurons", "thumbnail_ideas", "style_test", "style_test_scene",
            "text_model", "vision_model", "check_images", "no_metadata", "metadata_model"}


def stat(path):
    """A file's identity for the key: path, size and modification time (or None when it doesn't exist)."""
    try:
        st = Path(path).stat()
        return [str(path), st.st_size, st.st_mtime_ns]
    except (OSError, TypeError):
        return None


def digest(path):
    """A file's identity by its contents: path and SHA-256 (or None when it doesn't exist)."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        return [str(path), h.hexdigest()]
    except (OSError, TypeError):
        return None


def code_version():
    """A hash of the editor's own code: the Python modules and the Remotion project's sources."""
    h = hashlib.sha256()
    for p in sorted([*ROOT.glob("*.py"), *(ROOT / "remotion" / "src").glob("*")]):
        if p.is_file():
            h.update(p.name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def settings(args):
    """The options that change what a render makes, as plain values."""
    out = {}
    for k, v in sorted(vars(args).items()):
        if k in VOLATILE:
            continue
        if isinstance(v, Path):  # music, font, a reference voice: by contents
            out[k] = digest(v) or str(v)
        elif isinstance(v, (str, int, float, bool, type(None))):
            out[k] = v
        elif isinstance(v, (set, list, tuple)) and all(isinstance(x, (str, int, float)) for x in v):
            out[k] = sorted(v)
    return out


def channel_settings(args):
    """The resolved channel profile, as the render uses it: name, tagline, intro and outro colours, YouTube style, every
    setting in its file (voice, speed, captions, music, ...), and each branding clip by its contents."""
    import channels
    profile = getattr(args, "profile", None)
    if profile is None:
        return None
    return {"id": profile.id, "name": profile.name, "tagline": profile.tagline, "brand": profile.brand,
            "youtube_style": profile.youtube_style, "options": profile.options,
            "clips": {name: digest(profile.clip(*key)) for key, name in channels.CLIPS.items()}}


def render_key(script, scenes, args):
    """The key a render's checkpoints are kept under (see the module docstring)."""
    pictures = []
    for s in scenes:
        files = [s.get("src"), s.get("src2"), *(s.get("srcs") or {}).values(), *(s.get("srcs2") or {}).values()]
        pictures.append([stat(f) for f in files if f] + [bool(s.get("stock")), bool(s.get("video"))])
    data = {"script": Path(script).read_text(encoding="utf-8-sig"), "pictures": pictures,
            "channel": channel_settings(args), "settings": settings(args), "code": code_version()}
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode("utf-8")).hexdigest()


class Checkpoints:
    """The finished stages of one final render, in work/checkpoints.json. With a different key, or fresh, the work
    folder is emptied and the render starts over."""

    def __init__(self, work, key, fresh=False):
        self.work = Path(work)
        self.file = self.work / "checkpoints.json"
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if fresh or data.get("key") != key:
            if data.get("stages") and not fresh:
                print("  the script, pictures, settings or code changed since the last unfinished render: starting over")
            shutil.rmtree(self.work, ignore_errors=True)
            data = {"key": key, "stages": {}}
        self.work.mkdir(parents=True, exist_ok=True)
        self.data = data
        self.write()
        if data["stages"]:
            print(f"Resuming the last render (--fresh starts over): {', '.join(data['stages'])} already done")

    def get(self, stage):
        return self.data["stages"].get(stage)

    def save(self, stage, value=True):
        self.data["stages"][stage] = value
        self.write()

    def write(self):
        self.file.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
