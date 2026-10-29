import pytest

import core.database as db


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Point core.database at a throwaway sqlite file for this test only."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    return db


@pytest.mark.integration
def test_save_and_fetch_fresh_stories_roundtrip(isolated_db, verified_story):
    saved = isolated_db.save_stories([verified_story])
    assert saved == 1

    fresh = isolated_db.get_fresh_stories(min_confidence=0.5, max_age_hours=48)
    assert len(fresh) == 1
    assert fresh[0].raw.id == verified_story.raw.id


@pytest.mark.integration
def test_mark_broadcast_excludes_story_from_fresh_results(isolated_db, verified_story):
    isolated_db.save_stories([verified_story])
    isolated_db.mark_broadcast(verified_story.raw.id, "breaking")

    fresh = isolated_db.get_fresh_stories(min_confidence=0.5, max_age_hours=48)
    assert fresh == []
