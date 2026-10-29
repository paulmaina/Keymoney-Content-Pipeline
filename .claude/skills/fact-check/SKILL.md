---
name: fact-check
description: Fact-check a video script in Scripts/ by listing every factual claim (places, people, dates, numbers, how a hack works, safety or health claims), checking each against 1-2 independent web sources, and writing Research/<script>_factcheck.md with confirmed / unclear / unsupported verdicts, links and softer wording. Use when the user asks to fact-check, verify or source a script, or before a full render of a new script. Never edits the script without asking.
---

# Fact-check a script

Output: `Research/<script name>_factcheck.md` (e.g. `Scripts\Script2.txt` -> `Research/Script2_factcheck.md`).
**Never edit the script.** Suggest wording in the report and ask before changing anything in `Scripts\`.

## 1. Extract the claims
Read the script. Only the spoken text counts as claims (paragraphs, not `#` lines), but read `# Visuals:` lines too:
a visual can state a fact (a place, a person, a kind of clothing). If the script came from a production document with
a sources list (e.g. Script1.txt), note those sources as leads, not proof.

List every checkable claim, one per row, quoting the script's words and the scene (paragraph) number:
- **Places**: countries, regions, towns, landscapes ("one of the driest places on Earth").
- **People**: names, roles, nationality, what they did ("a teacher named Mohammed Bah Abba").
- **Dates and history**: years, "centuries old", firsts, who invented or popularised what.
- **Numbers**: amounts, sizes, durations, costs, counts ("hundreds of litres a day", "40 to 60 watt").
- **How a hack works**: the mechanism ("the water bends the light"), and what it achieves ("stays fresh for weeks").
- **Safety or health**: anything a viewer might copy (bleach in water, drinking collected water, building, electricity,
  food storage). These need the strongest sources.
Split compound sentences into separate claims. Skip opinions and calls to action ("which hack would you use?"),
but do check implied facts inside them ("not one of them needed a big budget").

## 2. Check each claim
Use web search for 1-2 **independent** sources per claim; two sources from the same organisation, or one copying
the other, count as one. Prefer, in order: peer-reviewed or university sources, government and UN/NGO
technical pages, established news outlets and encyclopaedias with citations, the inventor's or project's own site
(fine for what they did, weak for how well it works). Avoid content farms, AI-written listicles and YouTube videos as proof.
Open the pages rather than relying on search snippets; check numbers and dates exactly.

Verdicts:
- **Confirmed**: two independent good sources agree (one is enough for a basic, uncontested fact like a capital city).
- **Unclear**: sources disagree, are weak, or support only part of it (e.g. the number is a best case,
  the date is approximate, "invented" should be "popularised").
- **Unsupported**: no good source found, or sources contradict it.
Safety and health claims are at best Unclear unless an authoritative source (e.g. WHO, a health ministry,
a university extension service) backs them.

## 3. Write the report
```
# Fact-check: <script name>
<date> · <n> claims: <a> confirmed, <b> unclear, <c> unsupported

## Needs attention
(unclear and unsupported claims first, each with the suggested rewrite)

## Checklist
| # | Scene | Claim (script's words) | Type | Verdict | Sources | Notes / suggested wording |
```
- Sources as markdown links with the site name, e.g. `[Rolex Awards](https://...)`. Every verdict needs at least one link,
  except Unsupported, where you say what you searched for.
- For every Unclear or Unsupported claim, suggest softer wording that keeps the line speakable and about the same
  length ("can last for weeks" -> "can last much longer"; "invented" -> "popularised"; "hundreds of thousands of homes"
  -> "many thousands of homes"; add "about", "up to", "in some places"). Suggest a safety caveat where viewers could copy
  something risky.
- End with a short summary for the user: how many claims need changes, the most important ones, and ask whether to
  apply the suggested wording to the script. Only edit the script after they say yes, and then rerun
  `make_video.py <script> --check`.

Fact-check before a full render: changing narration after `--export-video-job` breaks the clip-to-scene match, and
after rendering means rendering again.
