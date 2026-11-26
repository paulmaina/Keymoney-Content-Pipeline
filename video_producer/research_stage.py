"""Stage 1: Topic research & structured brief synthesis.

Wraps the existing YouTube search/transcript collector in research.py and
writes Research/<topic-slug>.md so the script-writing stage has concrete
competitor coverage, missed angles, hook patterns, and unverified leads.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import research as yt_research

RESEARCH_DIR = Path(__file__).resolve().parent.parent / "Research"

SYNTHESIS_SYSTEM = (
    "You are a documentary YouTube researcher for the channel {channel}. "
    "Given metadata and opening excerpts from existing YouTube videos on a topic, write concise markdown sections:\n"
    "## What they cover\n"
    "## What they miss\n"
    "## Hooks and titles\n"
    "## 5 topic ideas for {channel}\n"
    "Never copy wording or hooks from other creators. Focus on specific places, communities, practical low-cost "
    "mechanisms, human context, and safety/accuracy gaps. Keep each idea title under 60 characters and include: "
    "'Gap it fills:', 'Why it suits the channel:', and 'Facts to check before scripting:'."
)


def _deterministic_sections(topic: str, videos: list[dict], stats: dict, channel_name: str) -> str:
    """Build a structured research brief without an LLM when offline or --no-llm is used."""
    devices = ", ".join(f"{k} ({v})" for k, v in stats.get("devices", {}).items()) or "none detected"
    words = ", ".join(f"{w} ({c})" for w, c in stats.get("words", [])[:8]) or "n/a"
    phrases = ", ".join(f'"{p}" ({c})' for p, c in stats.get("phrases", [])[:5]) or "n/a"
    with_tx = [v for v in videos if str(v.get("transcript", "")).startswith("ok")]
    return (
        "## What they cover\n"
        f"- Collected {len(videos)} video(s) for \"{topic}\" ({len(with_tx)} with transcripts).\n"
        "- Review `videos.json` and individual transcripts in `Research/_raw/` for recurring examples and formats.\n\n"
        "## What they miss\n"
        "- Local community context, real people/origin details, plain-language explanation of *why* the mechanism works, "
        "and explicit safety or reliability caveats.\n\n"
        "## Hooks and titles\n"
        f"- Average title length: {stats.get('avg_chars', 0)} chars; devices: {devices}.\n"
        f"- Common title words: {words}; recurring phrases: {phrases}.\n\n"
        f"## 5 topic ideas for {channel_name}\n"
        f"1. **{topic.title()[:55]}: Five Real-World Fixes**\n"
        "   - Gap it fills: Connects five distinct places with clear mechanisms and verifiable sources.\n"
        "   - Why it suits the channel: Fits chapter-based storytelling and clean Shorts splits.\n"
        "   - Facts to check before scripting: Exact locations, quantities, dates, and safety caveats.\n"
    )


def _clean_cell(value: object) -> str:
    return " ".join(str(value or "").split()).replace("|", "/")


def _llm_sections(topic: str, videos: list[dict], stats: dict, channel_name: str, providers, raw_dir: Path | None = None) -> str | None:
    if not providers or not getattr(providers, "text_ok", False):
        return None
    digest_rows = []
    for i, v in enumerate(videos[:15], 1):
        excerpt = ""
        if raw_dir and str(v.get("transcript", "")).startswith("ok"):
            tx_file = raw_dir / f"{v['id']}.txt"
            if tx_file.exists():
                words = tx_file.read_text(encoding="utf-8", errors="ignore").split()[:45]
                if words:
                    excerpt = f" | opening: \"{' '.join(words)}\""
        digest_rows.append(
            f"{i}. {_clean_cell(v.get('title'))} | {_clean_cell(v.get('channel'))} | "
            f"{_clean_cell(v.get('views_text'))} | {_clean_cell(v.get('length'))} | "
            f"status={_clean_cell(v.get('transcript'))}{excerpt}"
        )
    user = (
        f"Topic: {topic}\n"
        f"Title stats: avg_chars={stats.get('avg_chars')}, devices={stats.get('devices')}, "
        f"words={stats.get('words', [])[:10]}\n\n"
        "Videos:\n" + "\n".join(digest_rows)
    )
    reply = providers.text(
        [
            {"role": "system", "content": SYNTHESIS_SYSTEM.format(channel=channel_name)},
            {"role": "user", "content": user},
        ],
        max_tokens=1100,
        temperature=0.5,
    )
    return reply.strip() if reply and "## " in reply else None


def format_research_report(
    topic: str,
    slug: str,
    videos: list[dict],
    stats: dict,
    body_sections: str,
    blocked: bool = False,
) -> str:
    today = dt.date.today().isoformat()
    ok_count = sum(1 for v in videos if str(v.get("transcript", "")).startswith("ok"))
    lines = [
        f"# Research: {topic}",
        f"{today} · {len(videos)} videos ({ok_count} with transcripts) · data in Research/_raw/{slug}/",
        "",
    ]
    if blocked:
        lines += ["> Note: YouTube rate-limited transcript fetching mid-run; missing transcripts are marked in `videos.json`.", ""]
    lines += [
        "## Videos looked at",
        "| # | Title | Channel | Views | Length | Transcript |",
        "|---|---|---|---|---|---|",
    ]
    for i, v in enumerate(videos, 1):
        title = _clean_cell(v.get("title") or "(untitled)")
        chan = _clean_cell(v.get("channel") or "?")
        views = _clean_cell(v.get("views_text") or "?")
        length = _clean_cell(v.get("length") or "?")
        tx = _clean_cell(v.get("transcript") or "?")
        lines.append(f"| {i} | {title} | {chan} | {views} | {length} | {tx} |")
    lines += ["", body_sections.strip(), ""]
    return "\n".join(lines)


def run_research(
    topic: str,
    *,
    max_videos: int = 15,
    sort: str = "relevance",
    ids: str = "",
    no_search: bool = False,
    refresh: bool = False,
    channel_name: str = "Smart Village Boy",
    providers=None,
    synthesize: bool = True,
    raw_dir: Path | None = None,
    out_dir: Path | None = None,
) -> dict:
    """Collect YouTube research data and write `Research/<slug>.md`. Returns summary metadata."""
    slug = yt_research.slug(topic)
    base_raw = (raw_dir or yt_research.RAW_DIR) / slug
    base_raw.mkdir(parents=True, exist_ok=True)
    report_dir = out_dir or RESEARCH_DIR
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{slug}.md"

    videos = [] if no_search else yt_research.search(topic, max_videos)
    seen = {v["id"] for v in videos}
    if ids:
        for raw_id in ids.split(","):
            if not raw_id.strip():
                continue
            vid = yt_research.video_id(raw_id)
            if vid and vid not in seen:
                seen.add(vid)
                videos.append(
                    {
                        "id": vid,
                        "url": f"https://www.youtube.com/watch?v={vid}",
                        "title": "",
                        "channel": "",
                        "views": None,
                        "views_text": "",
                        "length": "",
                        "published": "",
                        "query": "added by --ids",
                    }
                )
    if not videos:
        raise SystemExit(f"No YouTube videos found for '{topic}'. Try --ids with --no-search.")

    yt_research.fill_titles(videos)
    if sort == "views":
        videos.sort(key=lambda v: v.get("views") if v.get("views") is not None else -1, reverse=True)
    videos = videos[:max_videos]

    blocked = yt_research.fetch_transcripts(videos, base_raw, refresh=refresh)
    (base_raw / "videos.json").write_text(json.dumps(videos, indent=2, ensure_ascii=False), encoding="utf-8")

    stats = yt_research.title_patterns([v["title"] for v in videos if v.get("title")])
    body = (_llm_sections(topic, videos, stats, channel_name, providers, raw_dir=base_raw) if synthesize else None) or _deterministic_sections(
        topic, videos, stats, channel_name
    )
    report_path.write_text(format_research_report(topic, slug, videos, stats, body, blocked=blocked), encoding="utf-8")
    return {
        "topic": topic,
        "slug": slug,
        "raw_dir": base_raw,
        "report_path": report_path,
        "video_count": len(videos),
        "blocked": blocked,
    }
