---
name: research-topic
description: Research a video topic for the Smart Village Boy channel by finding 10-20 top YouTube videos on it, reading their transcripts with research.py, and writing Research/<topic>.md covering what they cover, what they miss, their hooks and titles, and 5 topic ideas with the gap each fills. Use when the user asks to research a topic, find video ideas, check what other channels have made, or look for a content gap before writing a script.
---

# Research a topic

Goal: a short report that tells the user what already exists on YouTube for a topic and where Smart Village Boy
can do something different. Other creators' videos are research only: describe and analyse them in your own words,
and never copy their wording, structure, jokes or hooks into a report idea or a script.

## 1. Collect videos and transcripts
Run from the project root:
```
.venv\Scripts\python research.py "water storage village hacks"
```
- `--max 15` (default) keeps 15 videos; aim for 10-20. `--sort views` keeps the most viewed instead of YouTube's
  relevance order. One results page often has fewer than 10 regular videos, so it also searches a few close
  variants of the query ("... ideas", "... diy", "how to ...").
- It saves `Research/_raw/<topic-slug>/videos.json` (title, channel, views, length, age, transcript status) and one
  `<video id>.txt` transcript each, then prints a table, title patterns and each transcript's opening words.
  `Research/_raw/` is gitignored: never commit or publish those transcripts.
- If `youtube-transcript-api` is missing, it says so and collects titles only. Install it into the venv with
  `.venv\Scripts\python -m pip install youtube-transcript-api` (it's in requirements.txt) after asking the user.
- **Search fails or finds too few videos**: use web search (e.g. `site:youtube.com <topic>`) to find video links,
  then `--ids "url1,url2,..."` (add `--no-search` to use only those). Titles and channels are filled in from
  YouTube's oEmbed endpoint.
- **"no captions"**: normal for some videos. Analyse those from title, length and views only.
- **"blocked"**: YouTube is blocking this IP. Don't rerun in a loop (it extends the block). Write the report with
  what you have and say which videos were analysed from titles only; rerunning later reuses saved transcripts.

## 2. Read and analyse
Read `videos.json` and the transcripts. Transcripts are long auto captions, so skim: opening, structure, the main
claims or hacks, and the ending. Note for each video: the angle, the hacks or ideas shown, setting (country,
rural/urban, budget), format (talking head, build, list, story), and how it opens.
Then look across them:
- **Covered**: the ideas, places and formats most videos repeat.
- **Missed**: angles nobody takes. For this channel, look especially for village and low-cost solutions from
  specific countries (Africa, Asia, Latin America), the people and history behind a hack, how and why it works,
  safety caveats the videos skip, and children's or community perspectives.
- **Hooks**: what kind of opening each uses (a shocking number, a problem, a promise, a story, a challenge).
  Describe the technique, not the words.
- **Titles**: patterns (numbers, questions, "how to", ALL-CAPS words, money claims), typical length, and which
  patterns go with high views. Use research.py's title stats.
Treat view counts as rough signals: a video's age and channel size matter.

## 3. Write Research/<topic-slug>.md
Use the same slug research.py printed (e.g. `Research/water-storage-village-hacks.md`). Keep it to one or two screens:

```
# Research: <topic>
<date> · <n> videos (<k> with transcripts) · data in Research/_raw/<slug>/

## Videos looked at
| # | Title | Channel | Views | Length | Angle (your words) |

## What they cover
## What they miss
## Hooks and titles
(hook techniques with counts; title patterns; what the most-viewed videos have in common)

## 5 topic ideas for Smart Village Boy
1. **Working title** (under 60 characters, written fresh)
   Gap it fills: ...
   Why it suits the channel: ...
   Facts to check before scripting: ...
```
- Each idea names the specific gap from "What they miss" that it fills.
- Working titles and hooks must be original. Don't reuse a phrase from a researched title or transcript;
  if an idea is close to one video, say how it differs.
- Flag facts from transcripts as unverified: they're claims to check (suggest the fact-check skill once a script exists).
- No quotes from transcripts beyond a few words when needed to describe a technique, and attribute them.

Finish by telling the user the report path, the strongest idea, and anything that limited the research
(blocked transcripts, few results).
