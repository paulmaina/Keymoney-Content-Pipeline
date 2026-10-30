"""Caption styles for the Remotion renderer (--caption-style): which words are key, how lines are cut, where they sit.

  bold     big words, 2-4 a line, each popping in as it's spoken, thick outline and shadow (vertical default)
  clean    one or two calmer lines, the spoken word highlighted (horizontal default)
  minimal  a small plain line at the bottom, no highlight or animation

Key words (numbers, places, names and named hacks such as "the zeer" or "Liter of Light") get another colour and are a
little bigger. Each line's height is chosen so it never covers a face (OpenCV's YuNet face detector on the scene's picture,
cached next to it) or a place label: vertical lines stay in the lower third, clear of YouTube Shorts' title and buttons.
"""
import json
import re
from pathlib import Path

import make_video as mv

STYLES = ("bold", "clean", "minimal")
DEFAULT = {"vertical": "bold", "horizontal": "clean"}
WORDS_PER_LINE = {"bold": 3, "clean": 7, "minimal": 8}  # chunk_words may run one over, so bold is 2-4
# caption centres to try, as a share of the height, best first. Vertical: the lower third, above Shorts' title and
# description (bottom ~22%) and below the map and place label (top ~45%). Horizontal: the bottom, else a little higher.
POSITIONS = {"vertical": {"bold": (0.64, 0.57, 0.71), "clean": (0.66, 0.58, 0.72), "minimal": (0.74, 0.66)},
             "horizontal": {"bold": (0.82, 0.70), "clean": (0.86, 0.75), "minimal": (0.90, 0.80)}}
LINE_HEIGHT = {"bold": 0.075, "clean": 0.06, "minimal": 0.04}  # of the frame height, per line, for the face check
FACE_MARGIN = 0.06  # faces drift with the camera move and the zoom; keep this much extra room (share of the frame)
NUMBER_WORDS = set("""one two three four five six seven eight nine ten eleven twelve twenty thirty forty fifty hundred
hundreds thousand thousands million millions dozen dozens half double triple""".split())
NAMING = re.compile(r"\b(?:called|named|known as|call it)\s+((?:an?|the)\s+)?([^,.;:!?]+)", re.I)
SMALL = {"a", "an", "the", "of", "or", "and", "in", "on", "to", "it", "its"}


def plain(word):
    return re.sub(r"[^\w'-]", "", word).strip("'-")


def key_words(text, names=(), places=()):
    """Indices of the key words in a scene's narration (split on spaces, as the captions are)."""
    tokens = text.split()
    keys = set()
    place_words = {w for p in places for w in re.findall(r"[A-Z][\w'-]*", p)}
    name_words = {w for n in names for w in n.split() if w[:1].isupper()}
    sentence_start = True
    for i, tok in enumerate(tokens):
        w = plain(tok)
        if not w:
            continue
        if re.search(r"\d", w) or w.lower() in NUMBER_WORDS:
            keys.add(i)
        elif w in place_words or w in name_words:
            keys.add(i)
        elif w[:1].isupper() and not sentence_start and w not in ("I",):  # a proper noun mid-sentence
            keys.add(i)
        sentence_start = tok.rstrip("\"')”’").endswith((".", "!", "?"))
    for m in NAMING.finditer(text):  # "a movement called Liter of Light", "people call it the zeer"
        start = len(text[:m.start(2)].split())
        span = m.group(2).split()
        proper = bool(span) and span[0][:1].isupper()
        if proper:  # a proper name runs while its words are capitalised ("Liter of Light")
            n = 0
            while n < len(span) and (span[n][:1].isupper() or (plain(span[n]).lower() in ("of", "and")
                                                              and n + 1 < len(span) and span[n + 1][:1].isupper())):
                n += 1
            span = span[:n]
        for j, tok in enumerate(span[:4] if proper else span[:3]):
            if proper or plain(tok).lower() not in SMALL:
                keys.add(start + j)
    return keys


def mark_keys(chunks, scenes, names=(), places_by_scene=None):
    """Words of the caption chunks (make_video.Word) as dicts with a "key" flag. Captions follow the script's tokens in
    order, so they're matched back to each scene's narration word by word."""
    flat = [w for c in chunks for w in c]
    keyed, k = [], 0
    for i, s in enumerate(scenes):
        tokens = s["text"].split()
        keys = key_words(s["text"], names, (places_by_scene or {}).get(i, ()))
        for j, tok in enumerate(tokens):
            keyed.append((plain(tok).lower(), j in keys))
    out, pos = [], 0
    for w in flat:
        parts = [plain(p).lower() for p in w.text.split()]
        is_key = False
        for p in parts:
            for look in range(pos, min(pos + 4, len(keyed))):  # Whisper may have dropped or merged a word
                if keyed[look][0] == p:
                    is_key |= keyed[look][1]
                    pos = look + 1
                    break
        out.append(is_key)
    return out


# ---------- faces ----------

YUNET_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
             "face_detection_yunet_2023mar.onnx")  # MIT licence, 230 KB
YUNET = mv.ROOT / "models" / "face_detection_yunet_2023mar.onnx"
FACES_VERSION = "yunet2"  # part of the cache name, so a better detector never reuses old results


def yunet():
    if not YUNET.exists():
        import urllib.request
        YUNET.parent.mkdir(exist_ok=True)
        tmp = YUNET.with_suffix(".part")
        urllib.request.urlretrieve(YUNET_URL, tmp)
        tmp.replace(YUNET)
    return YUNET


def detect_faces(img):
    """(x, y, w, h) in pixels: OpenCV's YuNet detector, or its old Haar cascade if the model can't be fetched."""
    import cv2
    h, w = img.shape[:2]
    try:
        det = cv2.FaceDetectorYN.create(str(yunet()), "", (w, h), score_threshold=0.8)  # 0.7 let a clay jug through (0.78); real faces score 0.85+
        _, found = det.detect(img)
        return [tuple(int(v) for v in f[:4]) for f in (found if found is not None else [])]
    except (OSError, AttributeError, cv2.error):
        gray = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
        det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        return [tuple(b) for b in det.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(w // 30, w // 30))]


def faces(image):
    """Face boxes as (x, y, w, h) shares of the image, cached in <image>.<version>.faces.json (the cache goes when the
    image is redrawn). [] if OpenCV isn't installed."""
    image = Path(image)
    cache = image.with_name(f"{image.stem}.{FACES_VERSION}.faces.json")
    try:
        return [tuple(b) for b in json.loads(cache.read_text(encoding="utf-8"))]
    except (OSError, ValueError):
        pass
    try:
        import cv2
    except ImportError:
        return []
    img = cv2.imread(str(image))
    if img is None:
        return []
    h, w = img.shape[:2]
    scale = min(1.0, 1280 / max(w, h))
    if scale < 1:
        img = cv2.resize(img, (round(w * scale), round(h * scale)))
    sh, sw = img.shape[:2]
    found = [(round(x / sw, 4), round(y / sh, 4), round(bw / sw, 4), round(bh / sh, 4)) for x, y, bw, bh in detect_faces(img)]
    try:
        cache.write_text(json.dumps(found), encoding="utf-8")
    except OSError:
        pass
    return found


def frame_faces(image, fmt, min_height=0.0, margin=FACE_MARGIN):
    """Face boxes on the video frame (y0, y1 as shares of its height), for the picture cover-cropped to the format.
    Faces shorter than min_height (share of the frame) are left out."""
    from PIL import Image
    fw, fh = mv.FORMATS[fmt]["size"]
    with Image.open(image) as img:
        iw, ih = img.size
    scale = max(fw / iw, fh / ih)
    off_y = (ih * scale - fh) / 2
    off_x = (iw * scale - fw) / 2
    boxes = []
    for x, y, w, h in faces(image):
        x0, x1 = (x * iw * scale - off_x) / fw, ((x + w) * iw * scale - off_x) / fw
        y0, y1 = (y * ih * scale - off_y) / fh, ((y + h) * ih * scale - off_y) / fh
        if x1 > 0 and x0 < 1 and y1 > 0 and y0 < 1 and y1 - y0 >= min_height:  # visible in this crop
            boxes.append((max(0.0, y0 - margin), min(1.0, y1 + margin)))
    return boxes


def scene_faces(scenes, fmt, **kw):
    """Face bands per scene: both shots' faces when the scene has a second shot, so a line clears either picture."""
    out = []
    for s in scenes:
        bands = []
        for src in (mv.scene_src(s, fmt), mv.scene_src2(s, fmt)):
            try:
                bands += frame_faces(src, fmt, **kw) if src and Path(src).suffix.lower() in mv.IMAGE_EXT else []
            except OSError:
                pass
        out.append(bands)
    return out


def place_line(n_lines, fmt, style, face_boxes):
    """The first caption centre (share of the height) whose band misses every face; else the one that overlaps least."""
    half = n_lines * LINE_HEIGHT[style] / 2

    def overlap(c):
        return sum(max(0.0, min(c + half, b1) - max(c - half, b0)) for b0, b1 in face_boxes)
    options = POSITIONS[fmt][style]
    return min(options, key=lambda c: (overlap(c) > 0, overlap(c), options.index(c)))


# ---------- layout ----------

def style_for(fmt, choice):
    return DEFAULT[fmt] if choice in (None, "auto") else choice


def layout(chunks, scenes, fmt, style, fps, lead, bounds, names=(), places_by_scene=None, face_boxes=None,
           hold=0.5, blocks=()):
    """Caption lines for one format on the final timeline: re-cut to the style's line length, words flagged as key,
    and each line given a height (y, a share of the frame) that keeps it off faces and off `blocks`
    ((start frame, end frame, y0, y1): a map moved down below a face)."""
    keys = mark_keys(chunks, scenes, names, places_by_scene)
    flat = [w for c in chunks for w in c]
    flags = {id(w): k for w, k in zip(flat, keys)}  # Word is a NamedTuple, so the flags are kept alongside
    lines = []
    for chunk in chunks:
        for part in mv.chunk_words(chunk, WORDS_PER_LINE[style]):
            lines.append(part)
    out = []
    for i, part in enumerate(lines):
        start = lead + round(part[0].start * fps)
        end = lead + round((part[-1].end + hold) * fps)
        if i + 1 < len(lines):
            end = min(end, lead + round(lines[i + 1][0].start * fps))
        scene = max([j for j, b in enumerate(bounds) if b <= start] or [0])
        n_lines = 1 if style == "bold" else (2 if sum(len(w.text) + 1 for w in part) > 34 else 1)
        taken = [(y0, y1) for b0, b1, y0, y1 in blocks if b0 < end and b1 > start]
        y = place_line(n_lines, fmt, style, (face_boxes or {}).get(scene, []) + taken)
        words = [{"text": w.text, "start": lead + round(w.start * fps), "end": lead + round(w.end * fps),
                  "key": flags[id(w)]} for w in part]
        out.append({"start": start, "end": max(end, start + 1), "y": y, "words": words})
    return out
