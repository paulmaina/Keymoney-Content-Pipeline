"""Stage 3: Structured factual claim extraction and fact-check report generation.

Matches `.claude/skills/fact-check/SKILL.md`: extracts verifiable claims
(places, people, dates, numbers, mechanisms, safety/health) from spoken lines
without ever modifying the script file, and writes `Research/<script>_factcheck.md`.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from pathlib import Path

import places
from video_producer.script_model import ScriptDocument, parse_script_document

RESEARCH_DIR = Path(__file__).resolve().parent.parent / "Research"

SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
NUMBER_RE = re.compile(
    r"\b(?:\d[\d,./-]*|hundreds|thousands|millions|billions|dozens|centuries|decades|years|litres|liters|kilometres|kilometers|watt|watts|percent)\b",
    re.I,
)
DATE_RE = re.compile(r"\b(?:1\d{3}|20\d{2}s?|centuries|decades|ancient|century|19\d0s)\b", re.I)
PERSON_RE = re.compile(
    r"\b(?:named|called|engineer|farmer|teacher|mechanic|scientist|doctor|inventor)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\b"
)
MECHANISM_RE = re.compile(
    r"\b(?:evaporat\w*|gravity|filters?|condens\w*|absorb\w*|seep\w*|reflect\w*|bends?|insulat\w*|traps?|stores?|freez\w*)\b",
    re.I,
)
SAFETY_RE = re.compile(
    r"\b(?:drink\w*|bleach|untreated|bacteria|disease|mosquito\w*|electric\w*|fire|smoke|boil\w*|safe|health|spoil\w*|rot\w*)\b",
    re.I,
)


@dataclass
class Claim:
    number: int
    scene: int
    text: str
    claim_type: str
    verdict: str = "Needs verification"
    sources: str = ""
    notes: str = ""


def _classify_sentence(sentence: str, visual_desc: str = "") -> str | None:
    if SAFETY_RE.search(sentence):
        return "Safety / health"
    if PERSON_RE.search(sentence):
        return "Person"
    if DATE_RE.search(sentence):
        return "Date / history"
    if NUMBER_RE.search(sentence):
        return "Number / quantity"
    if MECHANISM_RE.search(sentence):
        return "How it works"
    if places.detect(sentence, visual_desc):
        return "Place"
    return None


def extract_claims(script: Path | str | ScriptDocument) -> list[Claim]:
    """Extract checkable factual claims from a script without modifying it."""
    doc = script if isinstance(script, ScriptDocument) else parse_script_document(script)
    claims: list[Claim] = []
    for scene in doc.scenes:
        for sent in SENTENCE_SPLIT.split(scene.text.strip()):
            sent = sent.strip()
            if not sent:
                continue
            ctype = _classify_sentence(sent, scene.visual_description)
            if ctype:
                claims.append(
                    Claim(
                        number=len(claims) + 1,
                        scene=scene.number,
                        text=sent,
                        claim_type=ctype,
                        notes="Verify against 1-2 independent authoritative sources (government, UN/NGO, academic, or primary project docs).",
                    )
                )
    return claims


def _enrich_with_llm(claims: list[Claim], providers) -> None:
    """Optionally ask a text model to suggest cautious wording for risky/quantitative claims."""
    if not claims or not providers or not getattr(providers, "text_ok", False):
        return
    payload = [{"id": c.number, "type": c.claim_type, "claim": c.text} for c in claims[:25]]
    reply = providers.text(
        [
            {
                "role": "system",
                "content": (
                    "You review factual claims in a documentary script before web verification. "
                    "Reply with JSON list [{\"id\": int, \"note\": \"what specific number/date/safety detail to check "
                    "and a softer speakable fallback phrasing if unconfirmed\"}]. Do not invent URLs."
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        max_tokens=900,
        temperature=0.3,
    )
    if not reply:
        return
    start, end = reply.find("["), reply.rfind("]")
    if start == -1 or end <= start:
        return
    try:
        items = json.loads(reply[start : end + 1])
    except ValueError:
        return
    by_id = {c.number: c for c in claims}
    for item in items:
        if isinstance(item, dict) and item.get("id") in by_id and item.get("note"):
            by_id[item["id"]].notes = str(item["note"]).strip()


def format_fact_check_report(script_name: str, claims: list[Claim]) -> str:
    today = dt.date.today().isoformat()
    confirmed = sum(1 for c in claims if c.verdict.lower() == "confirmed")
    unclear = sum(1 for c in claims if c.verdict.lower() == "unclear")
    unsupported = sum(1 for c in claims if c.verdict.lower() == "unsupported")
    pending = len(claims) - confirmed - unclear - unsupported

    lines = [
        f"# Fact-check: {script_name}",
        f"{today} · {len(claims)} claims: {confirmed} confirmed, {unclear} unclear, {unsupported} unsupported, {pending} pending verification",
        "",
        "## Needs attention",
    ]
    attention = [c for c in claims if c.verdict.lower() != "confirmed"]
    if not attention:
        lines.append("All extracted claims are confirmed.")
    else:
        for c in attention:
            lines.append(f"- **Scene {c.scene} ({c.claim_type})**: \"{c.text}\" — {c.notes}")

    lines += [
        "",
        "## Checklist",
        "| # | Scene | Claim (script's words) | Type | Verdict | Sources | Notes / suggested wording |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in claims:
        claim_col = c.text.replace("|", "/")
        notes_col = c.notes.replace("|", "/")
        src_col = c.sources.replace("|", "/") if c.sources else "Pending source check"
        lines.append(f"| {c.number} | {c.scene} | {claim_col} | {c.claim_type} | {c.verdict} | {src_col} | {notes_col} |")
    lines.append("")
    return "\n".join(lines)


def run_fact_check(
    script_path: Path | str,
    *,
    out_path: Path | str | None = None,
    providers=None,
    use_llm: bool = False,
) -> tuple[Path, list[Claim]]:
    """Extract claims from `script_path` and write `Research/<name>_factcheck.md` without editing the script."""
    script = Path(script_path)
    claims = extract_claims(script)
    if use_llm and providers is not None:
        _enrich_with_llm(claims, providers)
    target = Path(out_path) if out_path else (RESEARCH_DIR / f"{script.stem}_factcheck.md")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(format_fact_check_report(script.stem, claims), encoding="utf-8")
    return target, claims
