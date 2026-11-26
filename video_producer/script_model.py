"""Canonical structured script schema, parser, formatter, and JSON plan helpers.

Bridges the human-editable plain-text format in SCRIPT_GUIDE.md with a
typed production plan (ScriptDocument / SceneSpec) used by script generation,
validation (--check), fact-checking, rendering, and metadata stages.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

LEADING_TAG = re.compile(r"^\[(?:(video)|visual:\s*([^\]]+?))\s*\]\s*", re.I)
CHARACTER_LINE = re.compile(r"^#\s*character\s*:\s*(.+?)\s*(?:=|:| - | – | — )\s*(.+)$", re.I)
DESCRIPTION_LINE = re.compile(r"^#.*?\b(?:visuals?|shot|description)\s*:\s*\S", re.I)
MEDIA_LINE = re.compile(r"^#\s*media\s*:\s*(ai|stock)\s*$", re.I)
CHANNEL_LINE = re.compile(r"^#\s*channel\s*:\s*(.*?)\s*$", re.I)
LOCATION_LINE = re.compile(r"^#\s*(?:location|place)\s*:\s*(.+?)\s*$", re.I)
SECTION_LINE = re.compile(r"^#\s*([A-Z][A-Z0-9&'/, ]*?[A-Z0-9])\s*(?:$|[-–—:])")
NOT_SECTIONS = {"NOTE", "TODO", "FIXME", "TIP", "CHANNEL"}

LABEL_PREFIX = re.compile(r"^(?:visuals?|shot|description)\s*:\s*", re.I)


@dataclass
class CharacterSpec:
    """A recurring invented character declared in the script header."""

    name: str
    description: str


@dataclass
class SceneSpec:
    """One scene (one paragraph) in a structured script."""

    number: int
    text: str
    visual_description: str = ""
    visual_file: str | None = None
    is_video_motion: bool = False
    media: str | None = None
    section: str | None = None
    location: str | None = None
    comments: list[str] = field(default_factory=list)

    def to_legacy_dict(self) -> dict:
        """Convert to the scene dictionary shape expected by make_video.py and renderers."""
        d = {
            "number": self.number,
            "text": self.text,
            "visual": self.visual_file,
            "video": self.is_video_motion,
            "description": self.visual_description,
        }
        if self.media:
            d["media"] = self.media
        if self.section is not None:
            d["section"] = self.section
        return d


@dataclass
class ScriptDocument:
    """Complete structured representation of a video script."""

    title: str
    channel: str = "smart_village_boy"
    characters: list[CharacterSpec] = field(default_factory=list)
    scenes: list[SceneSpec] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    source_path: str | None = None

    @property
    def character_sheet(self) -> dict[str, str]:
        return {c.name: c.description for c in self.characters}

    def to_legacy_scenes(self) -> list[dict]:
        return [s.to_legacy_dict() for s in self.scenes]


def _extract_visual_detail(raw_comment: str) -> str:
    """Strip leading '#' and optional 'SECTION - ' prefix when normalizing a Visuals description."""
    body = raw_comment.lstrip("#").strip()
    return body


def parse_script_document(path: Path | str, default_channel: str = "smart_village_boy") -> ScriptDocument:
    """Parse a SCRIPT_GUIDE.md .txt script into a structured ScriptDocument."""
    p = Path(path)
    raw = p.read_text(encoding="utf-8-sig")
    blocks = re.split(r"\n\s*\n", raw)

    title = ""
    channel = ""
    characters: list[CharacterSpec] = []
    seen_chars: set[str] = set()
    notes: list[str] = []
    scenes: list[SceneSpec] = []
    current_section: str | None = None

    for block_idx, block in enumerate(blocks):
        lines_in_block = [ln.strip() for ln in block.strip().splitlines() if ln.strip()]
        if not lines_in_block:
            continue

        visual_file: str | None = None
        is_video = False
        media: str | None = None
        location: str | None = None
        described: list[str] = []
        spoken_lines: list[str] = []
        block_comments: list[str] = []
        block_section: str | None = None

        for line in lines_in_block:
            if line.startswith("#"):
                if (m := CHANNEL_LINE.match(line)) and m.group(1):
                    channel = m.group(1).strip()
                    continue
                if (m := CHARACTER_LINE.match(line)):
                    cname, cdesc = m.group(1).strip(), m.group(2).strip()
                    if cname not in seen_chars:
                        characters.append(CharacterSpec(name=cname, description=cdesc))
                        seen_chars.add(cname)
                    continue
                if (m := MEDIA_LINE.match(line)):
                    media = m.group(1).lower()
                    continue
                if (m := LOCATION_LINE.match(line)):
                    location = m.group(1).strip()
                    continue
                if ((sm := SECTION_LINE.match(line)) and len(sm.group(1)) >= 3
                        and sm.group(1).split()[0] not in NOT_SECTIONS):
                    block_section = " ".join(sm.group(1).split())
                    current_section = block_section
                if DESCRIPTION_LINE.match(line) and not CHARACTER_LINE.match(line):
                    described.append(_extract_visual_detail(line))
                else:
                    body = line.lstrip("#").strip()
                    if block_idx == 0 and not title and not spoken_lines and not body.lower().startswith("note:"):
                        title = body
                    elif body:
                        block_comments.append(body)
                continue

            cur = line
            while (m := LEADING_TAG.match(cur)):
                if m.group(1):
                    is_video = True
                else:
                    visual_file = m.group(2).strip()
                cur = cur[m.end():]
            if cur:
                spoken_lines.append(cur)

        if spoken_lines:
            scenes.append(
                SceneSpec(
                    number=len(scenes) + 1,
                    text=" ".join(spoken_lines),
                    visual_description="; ".join(described),
                    visual_file=visual_file,
                    is_video_motion=is_video,
                    media=media,
                    section=block_section or current_section,
                    location=location,
                    comments=block_comments,
                )
            )
        else:
            notes.extend(block_comments)

    if not title:
        title = p.stem.replace("_", " ").replace("-", " ").title()
    return ScriptDocument(
        title=title,
        channel=channel or default_channel,
        characters=characters,
        scenes=scenes,
        notes=notes,
        source_path=str(p),
    )


def _clean_visual_for_heading(desc: str, section: str | None) -> str:
    """Format a Visuals description cleanly with an optional chapter prefix."""
    text = desc.strip()
    if section:
        # Avoid duplicating the heading if the description already starts with it.
        pat = re.compile(rf"^{re.escape(section)}\s*[-–—:]\s*", re.I)
        text = pat.sub("", text).strip()
        text = LABEL_PREFIX.sub("", text).strip()
        return f"# {section} - Visuals: {text}" if text else f"# {section}"
    text = LABEL_PREFIX.sub("", text).strip()
    return f"# Visuals: {text}" if text else ""


def format_script_txt(doc: ScriptDocument) -> str:
    """Serialize a ScriptDocument into canonical SCRIPT_GUIDE.md plain-text format."""
    header = [f"# {doc.title.strip()}"]
    if doc.channel:
        header.append(f"# channel: {doc.channel.strip()}")
    for note in doc.notes:
        clean = note.lstrip("#").strip()
        if clean:
            header.append(clean if clean.lower().startswith("note:") else f"# note: {clean}")
            if not header[-1].startswith("#"):
                header[-1] = "# " + header[-1]
    for c in doc.characters:
        header.append(f"# character: {c.name.strip()} = {c.description.strip()}")

    blocks = ["\n".join(header)]
    prev_section: str | None = None

    for scene in doc.scenes:
        lines: list[str] = []
        emit_section = scene.section if (scene.section and scene.section != prev_section) else None
        if scene.section:
            prev_section = scene.section

        if scene.visual_description or emit_section:
            vis_line = _clean_visual_for_heading(scene.visual_description, emit_section)
            if vis_line:
                lines.append(vis_line)
        if scene.media in ("ai", "stock"):
            lines.append(f"# media: {scene.media}")
        if scene.location:
            lines.append(f"# location: {scene.location}")

        tags = []
        if scene.visual_file:
            tags.append(f"[visual: {scene.visual_file}]")
        if scene.is_video_motion:
            tags.append("[video]")
        prefix = (" ".join(tags) + " ") if tags else ""
        lines.append(f"{prefix}{scene.text.strip()}")
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks) + "\n"


def export_script_json(doc: ScriptDocument, path: Path | str | None = None) -> dict:
    """Convert a ScriptDocument to a JSON-serializable dict (and optionally save to `path`)."""
    data = {
        "title": doc.title,
        "channel": doc.channel,
        "notes": doc.notes,
        "characters": [asdict(c) for c in doc.characters],
        "scenes": [asdict(s) for s in doc.scenes],
    }
    if path is not None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return data


def load_script_json(path: Path | str) -> ScriptDocument:
    """Load a ScriptDocument from a JSON production plan written by export_script_json()."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return ScriptDocument(
        title=data.get("title") or Path(path).stem,
        channel=data.get("channel") or "smart_village_boy",
        notes=list(data.get("notes") or []),
        characters=[CharacterSpec(**c) for c in data.get("characters") or []],
        scenes=[SceneSpec(**s) for s in data.get("scenes") or []],
        source_path=str(path),
    )
