"""Where a video's files go: one folder per video, split into what you upload and everything else.

  Exports/<name>/Upload/    only what goes to YouTube
      <name>.mp4                  the full horizontal video
      <name>_Short_Part1.mp4 ...  the vertical Shorts series (<name>_Short.mp4 when it fits in one)
      thumbnail.jpg
      youtube.md                  titles, descriptions, chapters and tags, with a section per Short
  Exports/<name>/Working/   everything else
      prompts.json, media.json, image_checks.json, <name>.srt and each Short's .srt, log.txt,
      kaggle/ (job files of the Kaggle runs), render/ (temporary files, removed unless --keep-work),
      <name>_DRAFT.mp4 (--draft), preview.mp4 (--preview-scenes), thumbnail_ideas/ and style_tests/ (brainstorming)

<name> is the script's file name, or -o: a plain name ("Promo") goes under Exports/, a path ("D:\\Videos\\Promo") is used
as the folder. The shared caches (generated/ images, upscaled images, stock media and voice lines) stay where they are,
so a new folder never regenerates anything.
"""
import sys
from datetime import datetime
from pathlib import Path

EXPORTS = Path(__file__).resolve().parent / "Exports"


class Outputs:
    def __init__(self, folder, name=None):
        self.folder = Path(folder)
        self.name = name or self.folder.name
        self.upload = self.folder / "Upload"
        self.working = self.folder / "Working"

    @classmethod
    def for_script(cls, output, script):
        if not output:
            return cls(EXPORTS / Path(script).stem)
        output = Path(output)
        return cls(output if output.parent != Path(".") or output.is_absolute() else EXPORTS / output)

    def make(self):
        self.upload.mkdir(parents=True, exist_ok=True)
        self.working.mkdir(parents=True, exist_ok=True)
        return self

    # ---------- Upload/ ----------

    def video(self, fmt):
        """The full-length video of a format: the horizontal one, or the vertical one when it's a single Short."""
        return self.upload / (f"{self.name}.mp4" if fmt == "horizontal" else f"{self.name}_Short.mp4")

    def short(self, k):
        return self.upload / f"{self.name}_Short_Part{k}.mp4"

    @property
    def thumbnail(self):
        return self.upload / "thumbnail.jpg"

    @property
    def youtube(self):
        return self.upload / "youtube.md"

    # ---------- Working/ ----------

    @property
    def prompts(self):
        return self.working / "prompts.json"

    @property
    def media(self):
        return self.working / "media.json"

    @property
    def checks(self):
        return self.working / "image_checks.json"

    @property
    def sound_effects(self):
        return self.working / "sound_effects.json"

    @property
    def srt(self):
        return self.working / f"{self.name}.srt"

    def short_srt(self, k):
        return self.working / f"{self.name}_Short_Part{k}.srt"

    @property
    def render(self):
        return self.working / "render"

    def kaggle(self, feature):
        return self.working / "kaggle" / feature

    @property
    def log(self):
        return self.working / "log.txt"


class PreviewOutputs(Outputs):
    """--preview-scenes: the one video goes to Working/preview.mp4."""

    def video(self, fmt):
        return self.working / "preview.mp4"

    @property
    def sound_effects(self):  # the full video's record stays as it was
        return self.working / "preview_sound_effects.json"


class DraftOutputs(Outputs):
    """--draft: the one draft video goes to Working/<name>_DRAFT.mp4, never to Upload/."""

    def video(self, fmt):
        return self.working / f"{self.name}_DRAFT.mp4"

    @property
    def sound_effects(self):  # the final video's record stays as it was
        return self.working / "draft_sound_effects.json"


class Tee:
    """Copies everything printed to Working/log.txt as well as the console, for the length of a `with` block."""

    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = open(self.path, "a", encoding="utf-8")
        self.file.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} {' '.join(sys.argv)}\n")
        self.console = sys.stdout
        sys.stdout = self
        return self

    def write(self, text):
        self.console.write(text)
        self.file.write(text)
        return len(text)

    def flush(self):
        self.console.flush()
        self.file.flush()

    def __getattr__(self, name):  # encoding, isatty, ... from the real console
        return getattr(self.console, name)

    def __exit__(self, *exc):
        sys.stdout = self.console
        self.file.close()
        return False
