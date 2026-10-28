from agents.image_agent import extract_keywords


def test_extract_keywords_prefers_curated_story_keywords(verified_story):
    keywords = extract_keywords(verified_story)
    assert "Pakistan" in keywords


def test_extract_keywords_filters_stopwords_from_title(make_verified_story):
    story = make_verified_story(
        title="The Government and the People of Pakistan", keywords=[],
    )
    keywords = [k.lower() for k in extract_keywords(story)]

    assert "the" not in keywords
    assert "and" not in keywords


def test_extract_keywords_returns_fallback_when_nothing_usable(make_verified_story):
    story = make_verified_story(title="", keywords=[])
    assert extract_keywords(story) == ["world news"]
