from config.settings import config


def test_scheduler_show_ids_match_their_dict_keys():
    for show_id, show in config.scheduler.shows.items():
        assert show.id == show_id


def test_content_keywords_are_configured():
    assert len(config.content.keywords) > 0


def test_anchor_opens_has_a_default_fallback():
    assert "default" in config.anchor.opens


def test_every_show_anchor_open_resolves_to_a_configured_line():
    for show in config.scheduler.shows.values():
        assert show.anchor_open in config.anchor.opens or "default" in config.anchor.opens
