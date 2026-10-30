"""Stock footage and photos from Pexels and Pixabay (make_video.py --generate-visuals with --media stock or mixed).

For each scene a small Workers AI model writes 2-3 search queries from its Visuals line and narration (a keyword
fallback is used without Cloudflare). Videos are searched first, then photos, on every provider with a key in .env
(PEXELS_API_KEY, PIXABAY_API_KEY; --stock picks one). Only real footage and photographs are asked for: Pixabay
with video_type=film and image_type=photo, and Pexels only has photos and footage.

All results are scored together: the same small model rates how well each result's description and tags match the
scene (0-10; keyword overlap without Cloudflare), plus bonuses for video, for enough resolution and length, and for
the format's orientation. The best result wins for each format, Pexels on a tie. A scene whose best result is below
the threshold (or whose providers fail or are rate-limited) falls back to the Cloudflare AI image.

  --media mixed  (default) stock only for a good match (relevance 7+); scenes that name a '# character:' use AI
                 images, so a recurring character looks the same throughout
  --media stock  stock wherever anything relevant (4+) is found, AI only when nothing is
  --media ai     never stock
  "# media: ai" / "# media: stock" in a paragraph forces that scene.

Searches are cached for 24 hours (Pixabay's terms ask for it) and downloads for good, in generated/stock/. Rate limits
are read from the response headers and remembered between runs. Each scene's choice, provider, creator and link go
to Exports/<name>_media.json, and the YouTube description credits them.
"""
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

import ai_visuals

STOCK_DIR = ai_visuals.GEN_DIR / "stock"
SEARCH_TTL = 24 * 3600
PROVIDERS = ("pexels", "pixabay")  # order = preference on a tie
KEY_NAMES = {"pexels": "PEXELS_API_KEY", "pixabay": "PIXABAY_API_KEY"}
SCORER_MODEL = "@cf/meta/llama-3.1-8b-instruct-fp8-fast"  # small and cheap: ~10 neurons a scene for queries + scores
THRESHOLD = {"mixed": 7, "stock": 4}
PER_PAGE = 15
MAX_SCORED = 14  # candidates sent to the scorer per round, best by keyword overlap first
TIMEOUT = 60
FRAMES = {"horizontal": (1920, 1080), "vertical": (1080, 1920)}
MAX_SIDE = 2112  # no 4K downloads (50-180 MB each): the videos are 1080p, so a portrait clip beats cropping 4K
USER_AGENT = "ai-video-editor (stock search for a narrated video)"

QUERY_SYSTEM = (
    "You write stock-footage search queries. Given a video scene's visual description and narration, reply with "
    "JSON only: {\"queries\": [\"...\", \"...\", \"...\"]}: three English queries of 2-4 words, most specific first, "
    "made of concrete things a camera can film (objects, places, actions, landscapes). Never use people's names, "
    "brand names or abstract ideas. The last query should be broad enough to find something.")
SCORE_SYSTEM = (
    "You pick stock footage for a documentary scene. Given the scene and a numbered list of stock results (each "
    "with its description or tags), rate how well each result could illustrate the scene, from 0 (unrelated) to 10 "
    "(shows exactly what the scene describes, in a fitting place). Wrong country or region, wrong era, staged studio "
    "shots or text overlays lower the score. Reply with JSON only: {\"scores\": [n, n, ...]} in list order.")
STOP = set("""a an the and or of to in on at by for with from into onto over under is are was were be been being it its
this that these those as so yet but not no one two three five some any each every very just only then than there their
they them he she his her him we our you your who whom which what when where while how why all most more many much
can could will would should may might must do does did done has have had near beside behind front same scene shot
visuals visual description close view wide medium hand hands""".split())


class StockError(Exception):
    def __init__(self, message, rate_limited=False):
        super().__init__(message)
        self.rate_limited = rate_limited


@dataclass
class Candidate:
    provider: str
    kind: str  # "video" or "photo"
    id: str
    page_url: str
    creator: str
    creator_url: str
    text: str  # description or tags, for scoring
    width: int
    height: int
    duration: float = 0.0
    files: list = field(default_factory=list)  # [(width, height, url)], best first is not assumed
    query: str = ""
    relevance: float = 0.0

    @property
    def key(self):
        return f"{self.provider}:{self.kind}:{self.id}"


def keys(choice="auto"):
    """{provider: key} for the providers to use: those with a key in .env, limited to --stock's choice."""
    ai_visuals.load_env()
    found = {p: os.environ.get(KEY_NAMES[p], "").strip() for p in PROVIDERS}
    return {p: k for p, k in found.items() if k and choice in ("auto", p)}


# ---------- rate limits ----------

class RateLimits:
    """Requests left per provider, from the response headers, kept in generated/stock/ratelimits.json."""

    def __init__(self, path=None, clock=time.time, sleep=time.sleep):
        self.path = path or STOCK_DIR / "ratelimits.json"
        self.clock, self.sleep = clock, sleep
        try:
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.state = {}

    def update(self, provider, headers):
        h = {k.lower(): v for k, v in (headers or {}).items()}
        try:
            remaining = int(h["x-ratelimit-remaining"])
            reset = float(h["x-ratelimit-reset"])
        except (KeyError, ValueError):
            return
        # Pexels sends a UNIX time (monthly period), Pixabay the seconds left in its 60 s window
        reset_at = reset if reset > 10 ** 9 else self.clock() + reset
        self.state[provider] = {"remaining": remaining, "reset_at": reset_at}
        self.save()

    def exhausted(self, provider, reset_in=None):
        self.state[provider] = {"remaining": 0, "reset_at": self.clock() + (reset_in or 3600)}
        self.save()

    def wait_or_block(self, provider):
        """Wait out a short window (Pixabay's minute); returns False if the provider is blocked for longer."""
        s = self.state.get(provider)
        if not s or s["remaining"] > 0:
            return True
        left = s["reset_at"] - self.clock()
        if left <= 0:
            return True
        if left <= 65:
            print(f"    {provider}: rate limit reached, waiting {left:.0f} s")
            self.sleep(left + 1)
            return True
        return False

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.state), encoding="utf-8")
        except OSError:
            pass


# ---------- search ----------

def http_get(url, headers=None):
    """(parsed JSON, response headers). Errors never include the API key."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read()), dict(r.headers)
    except urllib.error.HTTPError as e:
        raise StockError(f"HTTP {e.code}", rate_limited=e.code == 429) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as e:
        raise StockError(f"network error ({getattr(e, 'reason', e.__class__.__name__)})") from None


def request(provider, kind, query, key):
    q = query[:100]
    if provider == "pixabay":
        base = "https://pixabay.com/api/videos/" if kind == "video" else "https://pixabay.com/api/"
        params = {"key": key, "q": q, "per_page": PER_PAGE, "safesearch": "true"}
        params.update({"video_type": "film"} if kind == "video" else {"image_type": "photo"})
        return base + "?" + urllib.parse.urlencode(params), {}
    base = "https://api.pexels.com/videos/search" if kind == "video" else "https://api.pexels.com/v1/search"
    return base + "?" + urllib.parse.urlencode({"query": q, "per_page": PER_PAGE}), {"Authorization": key}


class Searcher:
    """Cached, rate-limited searches across the providers that have keys."""

    def __init__(self, provider_keys, limits=None, get=http_get, clock=time.time):
        self.keys = dict(provider_keys)
        self.limits = limits or RateLimits(clock=clock)
        self.get, self.clock = get, clock
        self.calls = 0
        self.down = {}  # provider -> why it's off for this run

    def cache_file(self, provider, kind, query):
        h = hashlib.sha256(f"{provider}|{kind}|{query.lower()}".encode()).hexdigest()[:20]
        return STOCK_DIR / "search" / f"{h}.json"

    def search(self, provider, kind, query):
        """Raw results (cached 24 h), or [] if the provider is off. Disables a provider that fails or is limited."""
        if provider in self.down or provider not in self.keys:
            return []
        f = self.cache_file(provider, kind, query)
        try:
            cached = json.loads(f.read_text(encoding="utf-8"))
            if self.clock() - cached["time"] < SEARCH_TTL:
                return cached["results"]
        except (OSError, ValueError, KeyError):
            pass
        if not self.limits.wait_or_block(provider):
            self.down[provider] = "rate limit reached"
            print(f"    {provider}: rate limit reached until later; using the other provider or AI images")
            return []
        url, headers = request(provider, kind, query, self.keys[provider])
        try:
            data, resp_headers = self.get(url, headers)
        except StockError as e:
            if e.rate_limited:
                self.limits.exhausted(provider, 60 if provider == "pixabay" else 3600)
            self.down[provider] = str(e)
            print(f"    {provider} search failed ({e}); not using it again this run")
            return []
        self.calls += 1
        self.limits.update(provider, resp_headers)
        results = data.get("hits" if provider == "pixabay" else ("videos" if kind == "video" else "photos")) or []
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"time": self.clock(), "results": results}), encoding="utf-8")
        return results

    def candidates(self, kind, query):
        out = []
        for provider in self.keys:
            for raw in self.search(provider, kind, query):
                try:
                    c = PARSERS[provider, kind](raw)
                except (KeyError, TypeError, ValueError):
                    continue
                c.query = query
                out.append(c)
        return out


def slug_words(url):
    """Pexels pages are named after their content: /video/woman-carrying-clay-pots-856322/."""
    m = re.search(r"/(?:video|photo)/([a-z0-9-]+?)-?\d*/?$", url or "")
    return m.group(1).replace("-", " ") if m else ""


def pexels_video(r):
    files = [(f["width"], f["height"], f["link"]) for f in r["video_files"] if f.get("width") and f.get("link")]
    return Candidate("pexels", "video", str(r["id"]), r["url"], r["user"]["name"], r["user"]["url"], slug_words(r["url"]),
                     r["width"], r["height"], float(r.get("duration") or 0), files)


def pexels_photo(r):
    url = r["src"]["original"] + "?auto=compress&cs=tinysrgb&w=2400"
    w, h = r["width"], r["height"]
    if w > 2400:
        w, h = 2400, round(h * 2400 / w)
    return Candidate("pexels", "photo", str(r["id"]), r["url"], r["photographer"], r["photographer_url"],
                     r.get("alt") or slug_words(r["url"]), w, h, 0.0, [(w, h, url)])


def pixabay_video(r):
    files = [(v["width"], v["height"], v["url"]) for v in r["videos"].values() if v.get("url") and v.get("width")]
    big = max(files)
    return Candidate("pixabay", "video", str(r["id"]), r["pageURL"], r["user"], f"https://pixabay.com/users/{r['user']}-{r['user_id']}/",
                     r.get("tags", ""), big[0], big[1], float(r.get("duration") or 0), files)


def pixabay_photo(r):
    w, h = r["imageWidth"], r["imageHeight"]
    scale = min(1.0, 1280 / max(w, h))  # largeImageURL is at most 1280 px; full size needs approved API access
    w, h = round(w * scale), round(h * scale)
    return Candidate("pixabay", "photo", str(r["id"]), r["pageURL"], r["user"],
                     f"https://pixabay.com/users/{r['user']}-{r['user_id']}/", r.get("tags", ""), w, h, 0.0,
                     [(w, h, r["largeImageURL"])])


PARSERS = {("pexels", "video"): pexels_video, ("pexels", "photo"): pexels_photo,
           ("pixabay", "video"): pixabay_video, ("pixabay", "photo"): pixabay_photo}


# ---------- queries and scoring ----------

def words(text):
    return [w for w in re.findall(r"[a-z]+", (text or "").lower()) if w not in STOP and len(w) > 2]


def scene_text(scene):
    desc = re.sub(r"^.*?\b(?:visuals?|shot|description)\s*:\s*", "", scene.get("description", ""), flags=re.I)
    return desc, scene["text"]


PHRASE_BREAK = re.compile(r"[,;.]|(?:in|at|on|with|from|into|onto|over|under|beside|between|behind|near|of|while|"
                          r"as|and|looking|that|which)", re.I)


def keyword_queries(scene, names=()):
    """2-3 queries without a model: the description's phrases with the most content words (e.g. "busy open air
    market", "pile wilting tomatoes"), then the narration's first content words."""
    desc, narration = scene_text(scene)
    skip = {w.lower() for n in names for w in n.split()}
    phrases = [[w for w in words(p) if w not in skip] for p in PHRASE_BREAK.split(desc)]
    phrases = sorted((p for p in phrases if p), key=len, reverse=True)
    queries = [" ".join(p[-4:]) for p in phrases[:2]]
    queries.append(" ".join([w for w in words(narration) if w not in skip][:3]))
    return [q for q in dict.fromkeys(q.strip() for q in queries) if q][:3]


def model_queries(scene, providers, names=()):
    desc, narration = scene_text(scene)
    reply = providers.text([{"role": "system", "content": QUERY_SYSTEM},
                            {"role": "user", "content": f"Visual description: {desc or '(none)'}\nNarration: {narration}"}],
                           max_tokens=80, model=SCORER_MODEL, temperature=0.2) if providers else None
    try:
        data = json.loads(reply[reply.find("{"):reply.rfind("}") + 1])
        qs = [str(q).strip() for q in data["queries"] if str(q).strip()]
    except (TypeError, ValueError, KeyError, AttributeError):
        return keyword_queries(scene, names)
    skip = {w.lower() for n in names for w in n.split()}
    qs = [" ".join(w for w in q.split() if w.lower() not in skip) for q in qs]  # no names in searches
    return [q for q in dict.fromkeys(qs) if q][:3] or keyword_queries(scene, names)


def overlap(scene, c):
    """0-10 from shared words between the scene and the result's description or tags."""
    desc, narration = scene_text(scene)
    want = {w.rstrip("s") for w in words(desc) + words(narration)}
    have = {w.rstrip("s") for w in words(c.text)}
    if not want or not have:
        return 0.0
    return round(min(10.0, 10 * len(want & have) / min(len(have), 5)), 1)


def score(scene, cands, providers):
    """Set c.relevance (0-10) for each candidate: the small model when it answers, else keyword overlap."""
    if not cands:
        return
    desc, narration = scene_text(scene)
    listing = "\n".join(f"{i + 1}. [{c.kind}] {c.text[:160]}" for i, c in enumerate(cands))
    reply = providers.text([{"role": "system", "content": SCORE_SYSTEM},
                            {"role": "user", "content": f"Scene: {desc or narration}\nNarration: {narration}\n\n"
                                                        f"Results:\n{listing}"}],
                           max_tokens=12 + 4 * len(cands), model=SCORER_MODEL, temperature=0.1) if providers else None
    try:
        got = json.loads(reply[reply.find("{"):reply.rfind("}") + 1])["scores"]
        if len(got) != len(cands):
            raise ValueError
        for c, s in zip(cands, got):
            c.relevance = max(0.0, min(10.0, float(s)))
    except (TypeError, ValueError, KeyError, AttributeError):
        for c in cands:
            c.relevance = overlap(scene, c)


def best_file(c, fmt):
    """The smallest file that's sharp for the format's frame, else the biggest one up to 1080p-class."""
    fw, fh = FRAMES[fmt]
    files = sorted(c.files, key=lambda f: f[0] * f[1])
    files = [f for f in files if max(f[0], f[1]) <= MAX_SIDE] or files[:1]
    sharp = [f for f in files if cover_scale(f[0], f[1], fw, fh) <= 1.0]
    return sharp[0] if sharp else files[-1]


def cover_scale(w, h, fw, fh):
    return max(fw / w, fh / h)


def fit(c, fmt, seconds):
    """Relevance plus bonuses for this format. Returns (total, notes)."""
    fw, fh = FRAMES[fmt]
    w, h, _ = best_file(c, fmt)
    scale = cover_scale(w, h, fw, fh)
    bonus = {"video": 1.0 if c.kind == "video" else 0.0,
             "sharp": 1.0 if scale <= 1.0 else 0.5 if scale <= 1.5 else 0.0,
             "orientation": 1.0 if (w >= h) == (fw >= fh) else 0.0,
             "length": (1.0 if c.duration >= seconds else 0.5 if c.duration >= seconds * 0.6 else 0.0)
             if c.kind == "video" else 0.0}
    return c.relevance + sum(bonus.values()), bonus


def pick(cands, fmt, seconds, threshold):
    """The best candidate for a format, or None if nothing reaches the threshold. Pexels wins a tie."""
    good = [c for c in cands if c.relevance >= threshold]
    if not good:
        return None
    return max(good, key=lambda c: (round(fit(c, fmt, seconds)[0], 3), c.provider == "pexels", c.relevance))


def prefilter(scene, cands):
    seen, out = set(), []
    for c in sorted(cands, key=lambda c: -overlap(scene, c)):
        if c.key not in seen:
            seen.add(c.key)
            out.append(c)
    return out[:MAX_SCORED]


def find(scene, formats, seconds, threshold, searcher, providers, names=()):
    """{format: Candidate} for one scene: videos first, photos only if no video is good enough for every format."""
    queries = model_queries(scene, providers, names)
    chosen, pool = {}, []
    for kind in ("video", "photo"):
        cands = prefilter(scene, [c for q in queries for c in searcher.candidates(kind, q)])
        score(scene, cands, providers)
        pool += cands
        chosen = {f: pick(pool, f, seconds, threshold) for f in formats}
        if all(c and c.kind == "video" for c in chosen.values()):
            break
    return {f: c for f, c in chosen.items() if c}, queries


# ---------- downloads ----------

def download(c, fmt, get=None):
    """Download the chosen file once into generated/stock/. Returns the path."""
    w, h, url = best_file(c, fmt)
    ext = ".mp4" if c.kind == "video" else ".jpg"
    path = STOCK_DIR / f"{c.provider}_{c.kind}_{c.id}_{w}x{h}{ext}"
    if path.exists() and path.stat().st_size > 0:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    if get:
        tmp.write_bytes(get(url))
    else:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=300) as r, open(tmp, "wb") as out:
                while chunk := r.read(1 << 20):
                    out.write(chunk)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            tmp.unlink(missing_ok=True)
            raise StockError(f"download failed ({getattr(e, 'reason', e)})") from None
    tmp.replace(path)
    return path


# ---------- per scene ----------

def scene_mode(scene, media, declared):
    """'stock' (low threshold), 'mixed' (high threshold) or 'ai' for one scene, with the reason."""
    forced = scene.get("media")
    if scene.get("visual"):
        return "own", "your [visual:] file"
    if forced:
        return forced, "# media line"
    if media == "ai":
        return "ai", "--media ai"
    if media == "mixed" and (names := ai_visuals.in_scene(declared or {}, scene["text"], scene.get("description", ""))):
        return "ai", f"names {', '.join(names)} (AI keeps them consistent)"
    return media, f"--media {media}"


def fill_stock(scenes, formats, media, stock_choice, providers, seconds, declared=None, searcher=None, get=None):
    """Give scenes a stock video or photo where one fits (sets src/srcs and s["stock"]). Scenes left without one get
    AI images afterwards. Returns {scene index: (mode, reason)} for every scene."""
    plan = {i: scene_mode(s, media, declared) for i, s in enumerate(scenes)}
    wanted = [i for i, (m, _) in plan.items() if m in THRESHOLD]
    if not wanted:
        return plan
    found_keys = searcher.keys if searcher else keys(stock_choice)
    if not found_keys:
        print("Stock media: no PEXELS_API_KEY or PIXABAY_API_KEY in .env, so every scene gets an AI image.")
        return plan
    searcher = searcher or Searcher(found_keys)
    names = list(declared or {})
    print(f"Searching stock media for {len(wanted)} scene(s) on {' + '.join(searcher.keys)} "
          f"(threshold {THRESHOLD['mixed']}/10 for mixed, {THRESHOLD['stock']}/10 for stock)...")
    for i in wanted:
        s, mode = scenes[i], plan[i][0]
        chosen, queries = find(s, formats, seconds[i], THRESHOLD[mode], searcher, providers, names)
        if not chosen:
            print(f"  scene {i + 1}: no good stock match for {queries}; AI image")
            plan[i] = ("ai", "no good stock match")
            s["stock_queries"] = queries
            continue
        paths = {}
        for f in formats:
            c = chosen.get(f) or next(iter(chosen.values()))  # a format without its own match crops the other's
            try:
                paths[f] = download(c, f, get)
            except StockError as e:
                print(f"  scene {i + 1}: {c.provider} {e}")
        if not paths:
            plan[i] = ("ai", "download failed")
            continue
        first = chosen.get(formats[0]) or next(iter(chosen.values()))
        s["srcs"] = {f: paths.get(f) or next(iter(paths.values())) for f in formats}
        s["src"] = s["srcs"][formats[0]]
        s["stock"] = {f: credit(chosen.get(f) or first, f, seconds[i]) for f in formats}
        s["stock_queries"] = queries
        what = "; ".join(f"{f} {c.provider} {c.kind} {c.relevance:.0f}/10 \"{c.text[:40]}\""
                         for f, c in chosen.items())
        print(f"  scene {i + 1}: {what}")
    print(f"  stock: {sum(bool(s.get('stock')) for s in scenes)} scene(s), {searcher.calls} search request(s) "
          f"(the rest from the 24-hour cache)")
    return plan


def credit(c, fmt, seconds):
    total, bonus = fit(c, fmt, seconds)
    return {"provider": c.provider, "kind": c.kind, "id": c.id, "creator": c.creator, "creator_url": c.creator_url,
            "page_url": c.page_url, "description": c.text, "query": c.query, "relevance": c.relevance,
            "score": round(total, 2), "width": c.width, "height": c.height, "duration": c.duration}


def write_media_json(scenes, plan, path):
    """Exports/<name>_media.json: what each scene shows and where it came from."""
    rows = []
    for i, s in enumerate(scenes):
        mode, reason = plan.get(i, ("ai", ""))
        if s.get("stock"):
            choice = "stock"
        elif s.get("visual"):
            choice = "own"
        elif s.get("ai"):
            choice = "ai"
        else:
            choice = "card" if not s.get("src") else "footage"
        row = {"scene": i + 1, "choice": choice, "reason": reason if choice != "stock" else f"best match ({mode})",
               "narration": s["text"]}
        if s.get("stock"):
            row["stock"] = s["stock"]
        if s.get("stock_queries"):
            row["queries"] = s["stock_queries"]
        if s.get("srcs") or s.get("src"):
            row["files"] = {f: str(p) for f, p in (s.get("srcs") or {"all": s["src"]}).items()}
        rows.append(row)
    path.write_text(json.dumps({"scenes": rows}, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def credits(scenes):
    """Lines for the YouTube description, one per stock item, with the providers' required links."""
    seen, lines, used = set(), [], []
    for s in scenes:
        for c in (s.get("stock") or {}).values():
            if (c["provider"], c["id"]) in seen:
                continue
            seen.add((c["provider"], c["id"]))
            used.append(c["provider"])
            what = "Video" if c["kind"] == "video" else "Photo"
            lines.append(f"{what} by {c['creator']} on {c['provider'].capitalize()}: {c['page_url']}")
    if not lines:
        return ""
    sites = " and ".join(f"{p.capitalize()} ({'https://www.pexels.com' if p == 'pexels' else 'https://pixabay.com'})"
                         for p in PROVIDERS if p in used)
    return f"Stock footage and photos from {sites}:\n" + "\n".join(lines)
