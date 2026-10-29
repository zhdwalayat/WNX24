from agents.script_writer_agent import parse_script


def test_parse_script_splits_marked_sections_into_fields(breaking_show):
    raw = (
        "[HOOK]\nRupee steadies.\n"
        "[OPEN]\nBreaking news on WNX24.\n"
        "[FLASH]\nAccording to Dawn, the rupee held steady today.\n"
        "[SIGN_OFF]\nStay informed.\n"
    )
    script = parse_script(raw, "story-1", breaking_show)

    assert script.hook == "Rupee steadies."
    assert "rupee held steady" in script.what_happened
    assert script.story_id == "story-1"


def test_parse_script_strips_llm_added_section_labels(breaking_show):
    raw = "[HOOK]\nHook: Rupee steadies.\n[FLASH]\nWhat happened: Facts here.\n"
    script = parse_script(raw, "story-2", breaking_show)

    assert not script.hook.lower().startswith("hook:")
    assert not script.what_happened.lower().startswith("what happened:")


def test_parse_script_falls_back_to_raw_text_when_no_markers_present(breaking_show):
    raw = "Just a plain headline with no section markers at all here today."
    script = parse_script(raw, "story-3", breaking_show)

    assert script.full_text
    assert script.word_count > 0
