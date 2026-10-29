import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def no_user_defaults(monkeypatch, tmp_path):
    """Tests never read the real make_video.toml, .env or VIDEO_STYLE."""
    import ai_visuals
    import make_video
    monkeypatch.setattr(make_video, "CONFIG_FILE", tmp_path / "no-config.toml")
    monkeypatch.setattr(ai_visuals, "ENV_FILE", tmp_path / "no.env")
    monkeypatch.delenv("VIDEO_STYLE", raising=False)
