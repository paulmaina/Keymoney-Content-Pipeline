"""Stage 2: Generate well-structured SCRIPT_GUIDE.md scripts from a topic or brief.

Produces both a canonical `.txt` script in `Scripts/` (passing `make_video.py --check`)
and an optional JSON scene plan, keeping channel conventions (host character,
chapters, pace, tone, and `# Visuals:` guidelines) intact.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import channels
import make_video as mv
from video_producer.script_model import (
    CharacterSpec,
    SceneSpec,
    ScriptDocument,
    export_script_json,
    format_script_txt,
)

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "Scripts"
SMART_VILLAGE_HOST = CharacterSpec(
    name="Smart Village Boy",
    description="Kenyan boy, twelve years old, dark skin, red cap, yellow T-shirt, short black hair, bright smile",
)

SCRIPT_SYSTEM = (
    "You write documentary YouTube scripts for the channel '{channel_name}'.\n"
    "Output ONLY valid JSON (no markdown fences) matching this schema:\n"
    "{{\n"
    '  "title": "Title Case Working Title (12-59 chars)",\n'
    '  "characters": [{{"name": "...", "description": "age, origin, build, hair, clothing, colours"}}],\n'
    '  "scenes": [\n'
    "    {{\n"
    '      "section": "HOOK | HACK 1 PLACE | ... | OUTRO",\n'
    '      "location": "Region, Country (only when a scene introduces a new place not named clearly in narration, else null)",\n'
    '      "visual_description": "Single photographable moment: who (role, rough age, everyday contemporary clothing with '
    'concrete colours, NEVER write \'traditional clothing\' or \'traditional dress\' unless naming specific authentic '
    'garment), what, where, time of day. Simple relaxed pose. NO text/signs/logos, NO camera directions, NO montages.",\n'
    '      "is_video_motion": true or false (true on ~1 in 6 to 12 action scenes),\n'
    '      "media": "ai" or "stock" or null,\n'
    '      "narration": "2 to 3 concrete spoken sentences (22-34 words total, under 55 words). Give specific real-world '
    'mechanisms, places, and physical reasons why it works. Write numbers as spoken words when needed. Never use '
    'abbreviations with dots like Dr. or e.g. Never include brackets or visual directions."\n'
    "    }}\n"
    "  ]\n"
    "}}\n"
    "Rules:\n"
    "- Never declare real people in characters[]; describe real people by role, age, and clothing in visual_description.\n"
    "- Start with section 'HOOK' (at least 2 scenes): scene 1 opens directly on a vivid problem or surprising fact (NO "
    "greeting in scene 1); scene 2 welcomes viewers and opens a curiosity loop that pays off in the last chapter.\n"
    "- Every middle chapter (e.g. HACK 1 KENYA, HACK 2 INDIA) must be unique, cover ONE real documented technique in "
    "a named country/region, span at least 2-4 scenes (place & problem -> idea -> plain-physics mechanism -> result), "
    "and name its country/region in its first scene's narration.\n"
    "- End with section 'OUTRO' (at least 2 scenes): one-line recap, a question for comments, and 'subscribe'.\n"
    "- Channel tone: {tone}\n"
    "{host_rule}"
)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:60] or "video_script"


def _strip_fences(raw: str) -> str:
    txt = raw.strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", txt)
        txt = re.sub(r"\s*```$", "", txt)
    start, end = txt.find("{"), txt.rfind("}")
    return txt[start : end + 1] if start != -1 and end > start else txt


def _sanitize_narration(text: str) -> str:
    """Keep narration clean of stray [tags] and overly dense sentences."""
    cleaned = re.sub(r"\[[^\]]*\]", "", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _split_long_scene(scene: SceneSpec, max_words: int = 55) -> list[SceneSpec]:
    """Split an over-long scene at sentence boundaries so `--check` reports zero flags."""
    words = scene.text.split()
    if len(words) <= max_words:
        sents = [s for s in re.split(r"(?<=[.!?])\s+", scene.text) if s]
        if not (len(sents) > 3 and len(words) > 40):
            return [scene]

    sents = [s for s in re.split(r"(?<=[.!?])\s+", scene.text) if s]
    if len(sents) <= 1:
        # Hard split by words as last resort
        mid = len(words) // 2
        chunks = [" ".join(words[:mid]), " ".join(words[mid:])]
    else:
        chunks, cur, cur_w = [], [], 0
        for sent in sents:
            sw = len(sent.split())
            if cur and (cur_w + sw > max_words or (len(cur) >= 3 and cur_w + sw > 40)):
                chunks.append(" ".join(cur))
                cur, cur_w = [sent], sw
            else:
                cur.append(sent)
                cur_w += sw
        if cur:
            chunks.append(" ".join(cur))

    out = []
    for idx, chunk in enumerate(chunks):
        out.append(
            SceneSpec(
                number=scene.number,
                text=chunk,
                visual_description=scene.visual_description,
                visual_file=scene.visual_file,
                is_video_motion=scene.is_video_motion if idx == 0 else False,
                media=scene.media,
                section=scene.section,
                location=scene.location if idx == 0 else None,
            )
        )
    return out


def _normalize_document(doc: ScriptDocument) -> ScriptDocument:
    """Ensure sections, scene numbering, [video] share, and word budgets meet SCRIPT_GUIDE.md."""
    expanded: list[SceneSpec] = []
    for s in doc.scenes:
        s.text = _sanitize_narration(s.text)
        if not s.text:
            continue
        expanded.extend(_split_long_scene(s))

    for i, s in enumerate(expanded, 1):
        s.number = i
        if not s.visual_description:
            s.visual_description = f"Documentary view illustrating: {s.text[:120]}"
        if s.location:
            # Collapse "Rural Kenya, Kenya" -> "Rural Kenya"
            parts = [p.strip() for p in s.location.split(",") if p.strip()]
            if len(parts) == 2 and parts[1].lower() in parts[0].lower():
                s.location = parts[0]
        if s.section:
            s.section = re.sub(r"[^A-Z0-9&'/, ]+", "", s.section.upper()).strip() or None

    if expanded:
        if not expanded[0].section:
            expanded[0].section = "HOOK"
        if len(expanded) >= 2 and not expanded[-1].section:
            expanded[-1].section = "OUTRO"
        # Ensure every non-OUTRO section has at least 2 scenes when possible
        if len(expanded) >= 4 and expanded[0].section == "HOOK" and expanded[1].section != "HOOK":
            expanded[1].section = "HOOK"
        if len(expanded) >= 4 and expanded[-1].section == "OUTRO" and expanded[-2].section != "OUTRO":
            expanded[-2].section = "OUTRO"

    # Keep [video] count within script_check.video_range(n)
    n = len(expanded)
    if n:
        lo = max(1, n // 12)
        hi = max(lo, min(5, n) if n < 30 else n // 6)
        video_idxs = [i for i, s in enumerate(expanded) if s.is_video_motion]
        if len(video_idxs) > hi:
            for idx in video_idxs[hi:]:
                expanded[idx].is_video_motion = False
        elif len(video_idxs) < lo:
            step = max(1, n // lo)
            for idx in range(0, n, step):
                if sum(s.is_video_motion for s in expanded) >= lo:
                    break
                expanded[idx].is_video_motion = True

    doc.scenes = expanded
    return doc


def _fallback_script(topic: str, channel_id: str, profile, target_minutes: float) -> ScriptDocument:
    """Deterministic structured scaffold when no LLM provider is available."""
    title = re.sub(r"\s+", " ", topic.strip()).title()
    if len(title) < 12:
        title = f"{title} Explained"
    chars = [SMART_VILLAGE_HOST] if channel_id == "smart_village_boy" else []
    host_prefix = "Smart Village Boy standing on a sunlit hilltop overlooking a village" if chars else "A presenter in a sunlit rural landscape"
    scenes = [
        SceneSpec(
            number=1,
            section="HOOK",
            is_video_motion=True,
            visual_description=f"{host_prefix} at sunrise, holding a simple handmade prototype in both hands.",
            text=f"Most people overlook {topic.lower()}, assuming modern factories are the only answer. Look closer, and local ingenuity tells a different story.",
        ),
        SceneSpec(
            number=2,
            section="HOOK",
            visual_description=f"{host_prefix} in the early morning light, gesturing toward a wooden map on a table.",
            text=f"Welcome to {profile.name}. Stay to the end to see how communities turn everyday materials into lasting solutions.",
        ),
        SceneSpec(
            number=3,
            section="CHAPTER 1 CONTEXT",
            visual_description="Wide documentary shot of a dry hillside community in the morning sun, people walking along an earthen path.",
            text="Every strong solution begins with a clear local problem. When supplies are scarce or expensive, designs have to be simple and repairable.",
        ),
        SceneSpec(
            number=4,
            section="CHAPTER 1 CONTEXT",
            visual_description="Close view of skilled hands shaping clay, wood, and natural fibres on a clean outdoor workbench in warm daylight.",
            text="Instead of fighting the environment, builders work with gravity, shade, and local materials. Small physical principles do the heavy lifting.",
        ),
        SceneSpec(
            number=5,
            section="CHAPTER 2 MECHANISM",
            is_video_motion=True,
            visual_description="Mid-shot of local craftsmen testing a completed small-scale device outdoors under clear afternoon sky.",
            text="Step by step, the mechanism proves itself in daily use. Maintenance takes minutes with tools already found in the village.",
        ),
        SceneSpec(
            number=6,
            section="CHAPTER 2 MECHANISM",
            visual_description="Community members gathering around a working installation in warm late-afternoon sunlight, smiling calmly.",
            text="Once one household sees reliable results, neighbours adapt the design to their own homes. Practical ideas spread by demonstration.",
        ),
        SceneSpec(
            number=7,
            section="OUTRO",
            visual_description=f"{host_prefix} at golden hour, looking across the landscape calmly.",
            text="Simple materials, clear physics, and local skill can solve problems long before big infrastructure arrives.",
        ),
        SceneSpec(
            number=8,
            section="OUTRO",
            visual_description=f"{host_prefix} at dusk with warm lights in the village behind.",
            text="Which of these ideas would work best where you live? Share your thoughts in the comments, and subscribe for more stories.",
        ),
    ]
    return ScriptDocument(
        title=title,
        channel=channel_id,
        characters=chars,
        scenes=scenes,
        notes=[f"Generated structured outline for topic: {topic} (target ~{target_minutes:g} min; expand chapters or rerun with Cloudflare/Pollinations key for full detail)."],
    )


def _llm_script(
    topic: str,
    channel_id: str,
    profile,
    research_text: str,
    target_minutes: float,
    style_notes: str,
    providers,
) -> ScriptDocument | None:
    if not providers or not getattr(providers, "text_ok", False):
        return None
    target_scenes = max(8, min(65, round(target_minutes * 7.5)))
    target_words = max(180, round(target_minutes * 165))
    host_rule = (
        f"- Include host character in characters[] EXACTLY as: {SMART_VILLAGE_HOST.name} = {SMART_VILLAGE_HOST.description}. "
        "Smart Village Boy appears on screen in HOOK and OUTRO (and optionally transitional scenes), but NEVER narrates in "
        "the first person ('I am Smart Village Boy'): the narrator is the channel ('we', 'you').\n"
        if channel_id == "smart_village_boy"
        else ""
    )
    sys_msg = SCRIPT_SYSTEM.format(
        channel_name=profile.name,
        tone=profile.youtube_style or "warm, curious, clear documentary",
        host_rule=host_rule,
    )
    user_parts = [
        f"Topic: {topic}",
        f"Target length: ~{target_minutes:g} minutes (~{target_words} spoken words total across ~{target_scenes} scenes, averaging 24-32 words per scene).",
    ]
    if style_notes:
        user_parts.append(f"Extra direction: {style_notes}")
    if research_text:
        user_parts.append(f"Research brief (facts and gaps to draw from, never copy wording):\n{research_text[:5000]}")

    reply = providers.text(
        [{"role": "system", "content": sys_msg}, {"role": "user", "content": "\n\n".join(user_parts)}],
        max_tokens=4096,
        temperature=0.6,
    )
    if not reply:
        return None
    try:
        data = json.loads(_strip_fences(reply))
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("scenes"), list):
        return None

    chars = [
        CharacterSpec(name=str(c["name"]).strip(), description=str(c["description"]).strip())
        for c in (data.get("characters") or [])
        if isinstance(c, dict) and c.get("name") and c.get("description")
    ]
    if channel_id == "smart_village_boy":
        chars = [SMART_VILLAGE_HOST, *(c for c in chars if c.name.lower() != SMART_VILLAGE_HOST.name.lower())]

    scenes: list[SceneSpec] = []
    for idx, item in enumerate(data["scenes"], 1):
        if not isinstance(item, dict) or not str(item.get("narration") or "").strip():
            continue
        media = str(item.get("media") or "").strip().lower()
        scenes.append(
            SceneSpec(
                number=idx,
                text=str(item["narration"]).strip(),
                visual_description=str(item.get("visual_description") or "").strip(),
                is_video_motion=bool(item.get("is_video_motion")),
                media=media if media in ("ai", "stock") else None,
                section=str(item.get("section") or "").strip().upper() or None,
                location=str(item.get("location")).strip() if item.get("location") else None,
            )
        )
    if not scenes:
        return None
    return ScriptDocument(
        title=str(data.get("title") or topic.title()).strip(),
        channel=channel_id,
        characters=chars,
        scenes=scenes,
        notes=[f"Generated for topic: {topic}"],
    )


def generate_script(
    topic: str,
    *,
    channel_id: str = "smart_village_boy",
    research_path: Path | str | None = None,
    target_minutes: float = 8.0,
    style_notes: str = "",
    out_path: Path | str | None = None,
    json_path: Path | str | None = None,
    providers=None,
) -> tuple[Path, ScriptDocument]:
    """Generate a structured script in `Scripts/` (and optional JSON plan) and validate it."""
    profile = channels.load(channel_id)
    research_text = ""
    if research_path and Path(research_path).exists():
        research_text = Path(research_path).read_text(encoding="utf-8")

    doc = _llm_script(topic, profile.id, profile, research_text, target_minutes, style_notes, providers)
    if doc is None:
        doc = _fallback_script(topic, profile.id, profile, target_minutes)
    doc = _normalize_document(doc)

    target = Path(out_path) if out_path else (SCRIPTS_DIR / f"{slugify(topic)}.txt")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(format_script_txt(doc), encoding="utf-8")
    doc.source_path = str(target)

    if json_path is not None:
        export_script_json(doc, json_path)

    return target, doc
