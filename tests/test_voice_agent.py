from agents.voice_agent import SECTION_ORDER, get_audio_duration


def test_get_audio_duration_returns_zero_for_missing_file(tmp_path):
    missing = tmp_path / "does_not_exist.mp3"
    assert get_audio_duration(str(missing)) == 0.0


def test_section_order_has_no_duplicates():
    assert len(SECTION_ORDER) == len(set(SECTION_ORDER))
