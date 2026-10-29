import pytest

from agents.voice_agent import SECTION_ORDER
from video.video_composer import SECTION_LABELS, _clean, _dt_escape


@pytest.mark.parametrize("raw,forbidden", [
    ("Reuters: 'breaking'", ":"),
    ("100% confirmed", "%"),
])
def test_dt_escape_escapes_ffmpeg_drawtext_special_characters(raw, forbidden):
    escaped = _dt_escape(raw)
    # the literal character must not appear unescaped
    assert forbidden not in escaped.replace(f"\\{forbidden}", "")


def test_clean_truncates_long_text_with_ellipsis():
    result = _clean("x" * 200, max_len=20)
    assert len(result) == 20
    assert result.endswith("…")


def test_clean_collapses_internal_whitespace():
    assert _clean("too   many    spaces") == "too many spaces"


def test_section_labels_cover_every_voice_agent_section():
    for section in SECTION_ORDER:
        assert section in SECTION_LABELS
