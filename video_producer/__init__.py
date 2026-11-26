"""End-to-end AI video production package.

Provides a unified, stage-driven pipeline from topic research through
structured script authoring, fact-checking, visual generation/upscaling,
voiceover, captions, multi-backend rendering (HyperFrames, Remotion, FFmpeg),
Shorts splitting, and YouTube metadata generation.
"""

from video_producer.script_model import (
    CharacterSpec,
    SceneSpec,
    ScriptDocument,
    export_script_json,
    format_script_txt,
    load_script_json,
    parse_script_document,
)

__all__ = [
    "CharacterSpec",
    "SceneSpec",
    "ScriptDocument",
    "export_script_json",
    "format_script_txt",
    "load_script_json",
    "parse_script_document",
]
