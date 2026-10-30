"""Animated overlays for still pictures in the Remotion renderer (remotion/src/Overlays.tsx draws them in code: no
downloads, no neurons). Each picture gets up to two, chosen from its Visuals line and image prompt, kept subtle.
"""
import re

MAX_PER_SCENE = 2
# priority order; the first two that suit each other win
CUES = [
    ("rain", r"\b(rain(?!forest)\w*|downpour|drizzle|monsoon|storm\w*)\b"),  # a rainforest isn't rain
    ("fire", r"\b(fire|fires|flames?|firelight|campfire|bonfire|candles?|lamps?|lanterns?|embers?|torch|hearth)\b"),
    ("smoke", r"\b(smoke|smoky|smoking|steam\w*|kiln|chimney|cooking fire)\b"),
    ("mist", r"\b(mist\w*|fog\w*|haze|hazy|clouds?|cloudy)\b"),
    ("rays", r"\b(sun ?beams?|beams? of (?:day|sun)?light|light rays|rays|shafts? of light|sunlight (?:streaming|"
             r"pouring|filling)|god rays)\b"),
    ("heat", r"\b(harsh (?:midday )?sun\w*|midday|scorching|blazing|heat|hot (?:afternoon|day|sun)|noon)\b"),
    ("dust", r"\b(dust\w*|sandy|desert|dry riverbed|dirt road|dry season|crusted|cracked)\b"),
    ("leaves", r"\b(forest\w*|leaves|leafy|rainforest|jungle|orchard|autumn)\b"),
    ("ripple", r"\b(river|lake|pond|pool|puddles?|stream|reservoir|tank of water|water surface)\b"),
]
CLASHES = {frozenset(p) for p in [("rain", "dust"), ("rain", "heat"), ("rain", "fire"), ("rain", "rays"),
                                  ("fire", "heat"), ("mist", "heat"), ("smoke", "mist"), ("rain", "smoke"),
                                  ("dust", "ripple"), ("heat", "ripple")]}


def choose(scene):
    """Overlay names for a still picture, from its Visuals line and image prompt (not the narration, which often
    talks about other times and places)."""
    text = f"{scene.get('description') or ''} {scene.get('image_prompt') or ''}".lower()
    picked = []
    for name, pattern in CUES:
        if len(picked) == MAX_PER_SCENE:
            break
        if re.search(pattern, text) and all(frozenset((name, p)) not in CLASHES for p in picked):
            picked.append(name)
    return picked
