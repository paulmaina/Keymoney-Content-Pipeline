"""Research helper for the research-topic skill: find top YouTube videos on a topic and pull their transcripts.

  .venv\\Scripts\\python research.py "water storage village hacks"
  .venv\\Scripts\\python research.py "water storage village hacks" --max 20 --sort views
  .venv\\Scripts\\python research.py "topic" --ids "https://youtu.be/abc123def45,xyz987uvw65" --no-search

Writes Research/_raw/<topic-slug>/videos.json (title, channel, views, length, age and transcript status for each
video) and one <video id>.txt transcript per video, then prints a summary to build the report from.
Research/_raw/ is gitignored: transcripts are other creators' words, for analysis only, never for reuse.

No API key: search reads YouTube's results page and transcripts come from youtube-transcript-api. A video without
captions is recorded as such. If YouTube blocks this IP, fetching stops (retrying makes blocks last longer),
what was fetched is kept, and the rest is marked "blocked"; rerun later and saved transcripts are reused.
If search fails, find video links another way (e.g. a web search) and pass them with --ids.
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "Research" / "_raw"
SEARCH_URL = "https://www.youtube.com/results?search_query={query}"
OEMBED_URL = "https://www.youtube.com/oembed?url={url}&format=json"
QUERY_VARIANTS = ["{} ideas", "{} diy", "how to {}", "{} tips", "cheap {}", "{} explained"]
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/130.0 Safari/537.36",
           "Accept-Language": "en-US,en;q=0.9", "Cookie": "CONSENT=YES+1"}
LANGS = ["en", "en-US", "en-GB", "en-IN"]
FETCH_PAUSE = 1.5  # seconds between transcript requests, to stay under YouTube's rate limits
HOOK_WORDS = 45  # opening words of each transcript printed for hook analysis
ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
STOPWORDS = set("""a an and are as at be by for from how i in is it of on or that the this to with you your my
what why when who these those we our can will do does not no vs""".split())


def slug(topic):
    return re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:80] or "topic"


def video_id(text):
    """The 11-character id from a video id, watch/share/shorts/embed URL, or None."""
    text = text.strip()
    if ID.match(text):
        return text
    u = urllib.parse.urlparse(text if "//" in text else "https://" + text)
    if u.hostname and u.hostname.endswith("youtu.be"):
        cand = u.path.strip("/").split("/")[0]
    elif "v" in urllib.parse.parse_qs(u.query):
        cand = urllib.parse.parse_qs(u.query)["v"][0]
    else:
        m = re.search(r"/(?:shorts|embed|live|v)/([^/?#]+)", u.path)
        cand = m.group(1) if m else ""
    return cand if ID.match(cand) else None


def parse_views(text):
    """'1,234,567 views', '1.2M views', 'No views' -> int (None if unknown)."""
    if not text:
        return None
    t = text.replace(",", "").strip().lower()
    if t.startswith("no "):
        return 0
    m = re.match(r"([\d.]+)\s*([kmb])?", t)
    if not m:
        return None
    return int(float(m.group(1)) * {"k": 1e3, "m": 1e6, "b": 1e9}.get(m.group(2), 1))


def text_of(node):
    if not isinstance(node, dict):
        return ""
    return node.get("simpleText") or "".join(r.get("text", "") for r in node.get("runs", []))


def find_renderers(node, key="videoRenderer"):
    """Every `key` object anywhere in YouTube's page data, in page order."""
    if isinstance(node, dict):
        if key in node:
            yield node[key]
        for v in node.values():
            yield from find_renderers(v, key)
    elif isinstance(node, list):
        for v in node:
            yield from find_renderers(v, key)


def parse_results(html):
    """Videos from a YouTube results page (Shorts and ads are skipped)."""
    m = re.search(r"var ytInitialData\s*=\s*(\{.*?\});\s*</script>", html, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return []
    videos, seen = [], set()
    for r in find_renderers(data):
        vid = r.get("videoId")
        if not vid or vid in seen:
            continue
        seen.add(vid)
        views_text = text_of(r.get("viewCountText"))
        videos.append({"id": vid, "url": f"https://www.youtube.com/watch?v={vid}",
                       "title": text_of(r.get("title")), "channel": text_of(r.get("ownerText")),
                       "views": parse_views(views_text), "views_text": views_text,
                       "length": text_of(r.get("lengthText")), "published": text_of(r.get("publishedTimeText"))})
    return videos


def search_page(query, timeout=30):
    req = urllib.request.Request(SEARCH_URL.format(query=urllib.parse.quote_plus(query)), headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return parse_results(r.read().decode("utf-8", "replace"))


def search(topic, want, page=search_page):
    """Up to `want` videos. One results page often holds fewer than 10 regular videos (the rest are Shorts and
    shelves), so close variants of the query are searched too until there are enough."""
    videos, seen = [], set()
    for n, query in enumerate([topic, *(v.format(topic) for v in QUERY_VARIANTS)]):
        if len(videos) >= want:
            break
        try:
            found = page(query)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if n == 0:
                raise
            print(f"  search for '{query}' failed ({getattr(e, 'reason', e)}); keeping what was found")
            break
        for v in found:
            if v["id"] not in seen:
                seen.add(v["id"])
                videos.append(dict(v, query=query))
    return videos


def fill_titles(videos, timeout=15):
    """Title and channel for videos added with --ids, from YouTube's oEmbed endpoint (no key)."""
    for v in videos:
        if v["title"]:
            continue
        url = OEMBED_URL.format(url=urllib.parse.quote(v["url"], safe=""))
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout) as r:
                data = json.loads(r.read())
        except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError):
            continue  # private, removed or offline: the id alone still works for transcripts
        v.update(title=data.get("title", ""), channel=data.get("author_name", ""))


def load_api():
    try:
        import youtube_transcript_api as yta
    except ImportError:
        return None
    return yta


def fetch_one(api, yta, vid):
    """(status, language, text). Raises yta.RequestBlocked when YouTube blocks this IP."""
    try:
        t = api.fetch(vid, languages=LANGS)
    except yta.NoTranscriptFound:
        try:  # no English captions: take whatever language there is
            t = next(iter(api.list(vid))).fetch()
        except StopIteration:
            return "no captions", None, ""
    except yta.TranscriptsDisabled:
        return "no captions", None, ""
    except (yta.VideoUnavailable, yta.VideoUnplayable, yta.AgeRestricted, yta.InvalidVideoId) as e:
        return f"unavailable ({e.__class__.__name__})", None, ""
    text = " ".join(" ".join(s.text.split()) for s in t.snippets if s.text.strip())
    text = " ".join(re.sub(r"\[[^\]]{1,20}\]|>>", " ", text).split())  # [music], [applause], speaker marks
    return "ok" + (" (auto captions)" if t.is_generated else ""), t.language_code, text


def fetch_transcripts(videos, out_dir, refresh=False, api=None, yta=None, pause=FETCH_PAUSE):
    """Fill each video's transcript status, language and word count, saving <id>.txt. Returns True if blocked."""
    yta = yta or load_api()
    if yta is None:
        for v in videos:
            v["transcript"] = "skipped (youtube-transcript-api not installed)"
        print("youtube-transcript-api isn't installed, so only titles and stats were collected.\n"
              "Install it with: .venv\\Scripts\\python -m pip install youtube-transcript-api")
        return False
    api = api or yta.YouTubeTranscriptApi()
    blocked = False
    for n, v in enumerate(videos):
        path = out_dir / f"{v['id']}.txt"
        if path.exists() and not refresh:
            text = path.read_text(encoding="utf-8")
            v.update(transcript="ok (saved earlier)", words=len(text.split()))
            continue
        if blocked:
            v["transcript"] = "blocked"
            continue
        try:
            status, lang, text = fetch_one(api, yta, v["id"])
        except yta.RequestBlocked:
            blocked = True
            v["transcript"] = "blocked"
            print(f"YouTube is blocking transcript requests from this IP (after {n} video(s)); stopping here.\n"
                  "Keep going with what was fetched, try again in an hour or two (saved transcripts are reused),\n"
                  "or from another network. Don't retry in a loop: that makes the block last longer.")
            continue
        except yta.CouldNotRetrieveTranscript as e:
            status, lang, text = f"error ({e.__class__.__name__})", None, ""
        except Exception as e:  # network trouble shows up as several exception types
            status, lang, text = f"error ({e.__class__.__name__})", None, ""
        v.update(transcript=status, language=lang, words=len(text.split()))
        if text:
            path.write_text(text, encoding="utf-8")
        if pause and n + 1 < len(videos):
            time.sleep(pause)
    return blocked


def words(text):
    return [w for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in STOPWORDS and len(w) > 1]


def title_patterns(titles):
    """Counts of common title devices and the most used words and phrases."""
    n = len(titles) or 1
    pats = {
        "a number": sum(bool(re.search(r"\d", t)) for t in titles),
        "a question": sum("?" in t for t in titles),
        '"how to"': sum("how to" in t.lower() for t in titles),
        "an ALL-CAPS word": sum(bool(re.search(r"\b[A-Z]{3,}\b", t)) for t in titles),
        "emoji or symbols": sum(bool(re.search(r"[^\w\s,.:;!?'\"()&|/-]", t)) for t in titles),
        "a | or - separator": sum(bool(re.search(r"\s[|–—-]\s", t)) for t in titles),
    }
    toks = [words(t) for t in titles]
    common = Counter(w for t in toks for w in set(t)).most_common(12)
    pairs = Counter(p for t in toks for p in set(zip(t, t[1:]))).most_common(8)
    return {"count": len(titles), "avg_chars": round(sum(map(len, titles)) / n),
            "devices": {k: v for k, v in pats.items() if v},
            "words": [(w, c) for w, c in common if c > 1],
            "phrases": [(" ".join(p), c) for p, c in pairs if c > 1]}


def summary(topic, videos, out_dir, blocked):
    ok = [v for v in videos if str(v.get("transcript", "")).startswith("ok")]
    print(f"\nTopic: {topic}\n{len(videos)} videos, {len(ok)} with transcripts. Saved in {out_dir}")
    print(f"{'#':>2} {'views':>11} {'length':>7} {'transcript':<22} title / channel")
    for i, v in enumerate(videos, 1):
        views = f"{v['views']:,}" if v.get("views") is not None else "?"
        print(f"{i:>2} {views:>11} {v.get('length') or '?':>7} {str(v.get('transcript'))[:22]:<22} "
              f"{v['title'][:70]} / {v.get('channel') or '?'}")
    pats = title_patterns([v["title"] for v in videos if v.get("title")])
    print(f"\nTitles: average {pats['avg_chars']} characters")
    for k, c in pats["devices"].items():
        print(f"  {c}/{pats['count']} use {k}")
    if pats["words"]:
        print("  common words: " + ", ".join(f"{w} ({c})" for w, c in pats["words"]))
    if pats["phrases"]:
        print("  common phrases: " + ", ".join(f"{p} ({c})" for p, c in pats["phrases"]))
    if ok:
        print(f"\nOpening ~{HOOK_WORDS} words of each transcript (for hook analysis; paraphrase, never quote in scripts):")
        for i, v in enumerate(videos, 1):
            path = out_dir / f"{v['id']}.txt"
            if v in ok and path.exists():
                print(f"{i:>2}: {' '.join(path.read_text(encoding='utf-8').split()[:HOOK_WORDS])}...")
    if blocked:
        print("\nNote: transcripts stopped because YouTube blocked requests; the report should say which videos "
              "were analysed from titles only.")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("topic")
    ap.add_argument("--max", type=int, default=15, help="videos to keep from the search (default 15, 10-20 is best)")
    ap.add_argument("--sort", choices=["relevance", "views"], default="relevance",
                    help="order of the search results kept (default: YouTube's relevance order)")
    ap.add_argument("--ids", default="", help="comma-separated video links or ids to add (e.g. from a web search)")
    ap.add_argument("--no-search", action="store_true", help="only use --ids")
    ap.add_argument("--no-transcripts", action="store_true", help="titles and stats only")
    ap.add_argument("--refresh", action="store_true", help="fetch transcripts again even if saved")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    out_dir = RAW_DIR / slug(args.topic)
    out_dir.mkdir(parents=True, exist_ok=True)
    videos = []
    if not args.no_search:
        try:
            videos = search(args.topic, args.max)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            print(f"YouTube search failed ({getattr(e, 'reason', e)}). Find videos another way and pass them with --ids.")
        if not videos and not args.ids:
            print("No videos found in the search page (YouTube may have changed it). Pass links with --ids.")
        if args.sort == "views":
            videos.sort(key=lambda v: v["views"] or 0, reverse=True)
        videos = videos[:args.max]
    known = {v["id"] for v in videos}
    for item in filter(None, (s.strip() for s in args.ids.split(","))):
        vid = video_id(item)
        if not vid:
            print(f"Not a YouTube video link or id, skipped: {item}")
        elif vid not in known:
            known.add(vid)
            videos.append({"id": vid, "url": f"https://www.youtube.com/watch?v={vid}", "title": "", "channel": "",
                           "views": None, "views_text": "", "length": "", "published": ""})
    if not videos:
        sys.exit(1)
    fill_titles(videos)
    blocked = False if args.no_transcripts else fetch_transcripts(videos, out_dir, args.refresh)
    (out_dir / "videos.json").write_text(json.dumps({"topic": args.topic, "fetched": time.strftime("%Y-%m-%d"),
                                                     "blocked": blocked, "videos": videos}, indent=2,
                                                    ensure_ascii=False), encoding="utf-8")
    summary(args.topic, videos, out_dir, blocked)


if __name__ == "__main__":
    main()
