"""Where each scene happens, for the Remotion renderer's location labels and world-map pins.

A place is found in the narration (never invented): a known region ("Ladakh" -> "Ladakh, India"), a "Region, Country"
phrase, or a country, with a direction kept ("northern Nigeria" -> "Northern Nigeria"). A region the narration names is
also matched against the scene's '# Visuals:' line ("Ladakh valley in Ladakh, India"). A '# location: Name, Country'
line in a scene sets or overrides it; '# location: none' stops a label for that scene.
Labels appear only when the place changes, and the map only the first time each country comes up.
"""
import re
from typing import NamedTuple

# country names exactly as the map data (world-atlas countries-110m) spells them
MAP_COUNTRIES = """Afghanistan|Albania|Algeria|Angola|Argentina|Armenia|Australia|Austria|Azerbaijan|Bahamas|Bangladesh|Belarus|
Belgium|Belize|Benin|Bhutan|Bolivia|Bosnia and Herz.|Botswana|Brazil|Brunei|Bulgaria|Burkina Faso|Burundi|Cambodia|Cameroon|
Canada|Central African Rep.|Chad|Chile|China|Colombia|Congo|Costa Rica|Croatia|Cuba|Cyprus|Czechia|Côte d'Ivoire|
Dem. Rep. Congo|Denmark|Djibouti|Dominican Rep.|Ecuador|Egypt|El Salvador|Eq. Guinea|Eritrea|Estonia|Ethiopia|Fiji|Finland|
France|Gabon|Gambia|Georgia|Germany|Ghana|Greece|Greenland|Guatemala|Guinea|Guinea-Bissau|Guyana|Haiti|Honduras|Hungary|
Iceland|India|Indonesia|Iran|Iraq|Ireland|Israel|Italy|Jamaica|Japan|Jordan|Kazakhstan|Kenya|Kosovo|Kuwait|Kyrgyzstan|Laos|
Latvia|Lebanon|Lesotho|Liberia|Libya|Lithuania|Luxembourg|Macedonia|Madagascar|Malawi|Malaysia|Mali|Mauritania|Mexico|
Moldova|Mongolia|Montenegro|Morocco|Mozambique|Myanmar|Namibia|Nepal|Netherlands|New Zealand|Nicaragua|Niger|Nigeria|
North Korea|Norway|Oman|Pakistan|Palestine|Panama|Papua New Guinea|Paraguay|Peru|Philippines|Poland|Portugal|Puerto Rico|
Qatar|Romania|Russia|Rwanda|S. Sudan|Saudi Arabia|Senegal|Serbia|Sierra Leone|Slovakia|Slovenia|Somalia|South Africa|
South Korea|Spain|Sri Lanka|Sudan|Suriname|Sweden|Switzerland|Syria|Taiwan|Tajikistan|Tanzania|Thailand|Timor-Leste|Togo|
Trinidad and Tobago|Tunisia|Turkey|Turkmenistan|Uganda|Ukraine|United Arab Emirates|United Kingdom|
United States of America|Uruguay|Uzbekistan|Vanuatu|Venezuela|Vietnam|Yemen|Zambia|Zimbabwe|eSwatini""".replace("\n", "").split("|")

# how people say it -> the map's name
ALIASES = {
    "USA": "United States of America", "United States": "United States of America", "America": "United States of America",
    "UK": "United Kingdom", "Britain": "United Kingdom", "England": "United Kingdom", "Scotland": "United Kingdom",
    "Wales": "United Kingdom", "Ivory Coast": "Côte d'Ivoire", "Cote d'Ivoire": "Côte d'Ivoire",
    "DR Congo": "Dem. Rep. Congo", "Democratic Republic of the Congo": "Dem. Rep. Congo", "Bosnia": "Bosnia and Herz.",
    "Czech Republic": "Czechia", "Eswatini": "eSwatini", "Swaziland": "eSwatini", "Burma": "Myanmar",
    "North Macedonia": "Macedonia", "South Sudan": "S. Sudan", "Central African Republic": "Central African Rep.",
    "Dominican Republic": "Dominican Rep.", "Equatorial Guinea": "Eq. Guinea", "East Timor": "Timor-Leste",
    "UAE": "United Arab Emirates", "Holland": "Netherlands", "Bosnia and Herzegovina": "Bosnia and Herz.",
}
# also first names, birds and US states: only a place after "in", "from", ...
AMBIGUOUS = {"Chad", "Jordan", "Georgia", "Turkey", "Guinea", "China", "America", "Niger"}
PREPOSITION = re.compile(r"(?:\bin|\bfrom|\bof|\bto|\bacross|\bacross|\bvisit|\bnear|\bthrough)\s+(?:the\s+)?$", re.I)

# regions people name without their country: name -> (map country, lat, lon)
REGIONS = {
    "Ladakh": ("India", 34.2, 77.6), "Meghalaya": ("India", 25.5, 91.4), "Rajasthan": ("India", 27.0, 74.2),
    "Kerala": ("India", 10.3, 76.3), "Gujarat": ("India", 22.3, 71.2), "Assam": ("India", 26.2, 92.9),
    "Sikkim": ("India", 27.5, 88.5), "Nagaland": ("India", 26.1, 94.5), "Odisha": ("India", 20.5, 84.4),
    "Bihar": ("India", 25.8, 85.6), "Tamil Nadu": ("India", 11.1, 78.7), "Atacama": ("Chile", -24.5, -69.3),
    "Patagonia": ("Argentina", -45.0, -69.0), "Bali": ("Indonesia", -8.4, 115.2), "Java": ("Indonesia", -7.4, 110.0),
    "Sumatra": ("Indonesia", 0.5, 101.5), "Yunnan": ("China", 25.0, 101.5), "Tibet": ("China", 31.5, 88.0),
    "Sichuan": ("China", 30.6, 102.7), "Mindanao": ("Philippines", 7.5, 125.0), "Luzon": ("Philippines", 16.5, 121.0),
    "Ifugao": ("Philippines", 16.8, 121.1), "Zanzibar": ("Tanzania", -6.1, 39.3), "Tigray": ("Ethiopia", 14.0, 39.0),
    "Darfur": ("Sudan", 13.5, 24.0), "Sahara": (None, 23.0, 13.0), "Okinawa": ("Japan", 26.5, 128.0),
    "Hokkaido": ("Japan", 43.2, 142.8), "Andalusia": ("Spain", 37.5, -4.7), "Sicily": ("Italy", 37.5, 14.1),
    "Yucatan": ("Mexico", 20.6, -88.8), "Oaxaca": ("Mexico", 17.0, -96.7), "Cusco": ("Peru", -13.5, -71.9),
    "Lake Titicaca": ("Peru", -15.8, -69.4), "Amazonas": ("Brazil", -3.4, -65.9), "Mekong Delta": ("Vietnam", 10.0, 105.8),
    "Mustang": ("Nepal", 29.0, 83.9), "Kashmir": ("India", 34.1, 74.8), "Balochistan": ("Pakistan", 28.5, 65.0),
    "Gilgit-Baltistan": ("Pakistan", 35.8, 74.9), "Rift Valley": ("Kenya", 0.5, 36.0), "Turkana": ("Kenya", 3.1, 35.6),
}
DIRECTIONS = r"(?:northern|southern|eastern|western|central|north-eastern|north-western|south-eastern|south-western|rural|coastal)"
LOCATION_LINE = re.compile(r"^#\s*(?:location|place)\s*:\s*(.+?)\s*$", re.I)


class Place(NamedTuple):
    label: str  # "Ladakh, India"
    country: str | None  # map name, for the pin and the "new country" check
    lat: float | None = None  # exact pin for known regions; else the country's centre
    lon: float | None = None


def display(country):
    return {"United States of America": "USA", "Dem. Rep. Congo": "DR Congo", "Bosnia and Herz.": "Bosnia and Herzegovina",
            "Central African Rep.": "Central African Republic", "Dominican Rep.": "Dominican Republic",
            "Eq. Guinea": "Equatorial Guinea", "S. Sudan": "South Sudan", "eSwatini": "Eswatini"}.get(country, country)


_NAMES = sorted({**{c: c for c in MAP_COUNTRIES}, **ALIASES}.items(), key=lambda kv: -len(kv[0]))
COUNTRY_RE = re.compile(r"\b(" + "|".join(re.escape(n) for n, _ in _NAMES) + r")\b")
REGION_RE = re.compile(r"\b(" + "|".join(re.escape(r) for r in sorted(REGIONS, key=len, reverse=True)) + r")\b")
CAP = r"[A-Z][a-zA-Z'-]+(?: [A-Z][a-zA-Z'-]+)?"


def country_of(name):
    return dict(_NAMES).get(name)


def countries_in(text):
    """[(position, map name, the words used)] for each country mention, skipping ambiguous names without a preposition."""
    out = []
    for m in COUNTRY_RE.finditer(text):
        name = m.group(1)
        if name in AMBIGUOUS and not PREPOSITION.search(text[:m.start()]):
            continue
        out.append((m.start(), country_of(name), name))
    return out


def parse_override(value):
    """'# location: Ladakh, India' -> Place; 'none' -> False."""
    if value.strip().lower() in ("none", "-", "no"):
        return False
    *head, tail = [p.strip() for p in value.split(",")]
    country = country_of(tail)
    region = REGIONS.get(head[0]) if head else REGIONS.get(tail)
    if head:
        lat, lon = (region[1], region[2]) if region else (None, None)
        return Place(f"{', '.join(head)}, {display(country) if country else tail}", country, lat, lon)
    if region:
        return Place(f"{tail}, {display(region[0])}" if region[0] else tail, region[0], region[1], region[2])
    return Place(display(country) if country else tail, country)


def detect(text, description=""):
    """The place a scene's narration introduces, or None."""
    candidates = []  # (position, specificity, Place)
    for m in REGION_RE.finditer(text):
        country, lat, lon = REGIONS[m.group(1)]
        label = f"{m.group(1)}, {display(country)}" if country else m.group(1)
        candidates.append((m.start(), 2, Place(label, country, lat, lon)))
    for m in re.finditer(rf"\b({CAP}), ({CAP})\b", text):  # "Ladakh, India" said in the narration
        country = country_of(m.group(2))
        if country and not country_of(m.group(1)) and m.group(1) not in REGIONS:
            candidates.append((m.start(), 2, Place(f"{m.group(1)}, {display(country)}", country)))
    for pos, country, said in countries_in(text):
        direction = re.search(rf"\b({DIRECTIONS})\s+(?:the\s+)?$", text[:pos], re.I)
        label = f"{direction.group(1).capitalize()} {display(country)}" if direction else display(country)
        candidates.append((direction.start() if direction else pos, 1, Place(label, country)))
    # a name the narration uses that the visuals line places: "Sonam ... in a Ladakh valley in Ladakh, India"
    for m in re.finditer(rf"\b({CAP}), ({CAP})\b", description):
        country = country_of(m.group(2))
        if country and m.group(1) not in REGIONS and not country_of(m.group(1)) and \
                (pos := text.find(m.group(1))) >= 0:
            candidates.append((pos, 2, Place(f"{m.group(1)}, {display(country)}", country)))
    if not candidates:
        return None
    first = min(candidates, key=lambda c: c[0])
    # a region of the first-named country is more specific: "Chile's Atacama" -> "Atacama, Chile"
    specific = [c for c in candidates if c[1] == 2 and c[2].country == first[2].country]
    return min(specific, key=lambda c: c[0])[2] if specific else first[2]


def overrides(path):
    """Per scene (parallel to make_video.parse_script): the '# location:' value, or None."""
    from make_video import LEADING_TAG
    out = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig")):
        found, spoken = None, False
        for line in block.strip().splitlines():
            line = line.strip()
            if line.startswith("#"):
                if (m := LOCATION_LINE.match(line)):
                    found = parse_override(m.group(1))
                continue
            while (m := LEADING_TAG.match(line)):
                line = line[m.end():]
            spoken |= bool(line)
        if spoken:
            out.append(found)
    return out


def scene_places(scenes, script=None, first=0):
    """[(scene index, Place, show map, countries pinned before)] for each scene where the place changes. `scenes` may
    be a slice of the script starting at scene `first` (a vertical part), so its '# location:' lines still line up."""
    forced = overrides(script)[first:first + len(scenes)] if script else []
    if len(forced) != len(scenes):
        forced = [None] * len(scenes)
    out, last, visited = [], None, []
    for i, s in enumerate(scenes):
        place = forced[i] if forced[i] is not None else detect(s["text"], s.get("description", ""))
        if not place or place.label == last:
            continue
        new_country = bool(place.country) and place.country not in visited
        out.append((i, place, new_country, list(visited)))
        if new_country:
            visited.append(place.country)
        last = place.label
    return out
