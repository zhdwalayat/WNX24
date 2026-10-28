from core.models import AvatarVideo, Thumbnail, VideoPackage, VideoScript
from agents.seo_agent import build_chapters, build_metadata


def test_build_chapters_always_starts_with_introduction():
    script = VideoScript(story_id="x", hook="Hook.", what_happened="Some facts here today.")
    chapters = build_chapters(script, show=None)
    assert chapters.startswith("0:00 Introduction")


def test_build_chapters_includes_sections_present_in_script():
    script = VideoScript(
        story_id="x", hook="Hook.",
        what_happened="Facts about the story unfolding today.",
        why_it_matters="This matters because of the wider impact.",
        expert_insight="Analysts say this could escalate further.",
    )
    chapters = build_chapters(script, show=None)
    assert "What Happened" in chapters
    assert "Why It Matters" in chapters
    assert "Analysis" in chapters
    assert "Sign Off" in chapters


def test_build_metadata_title_under_youtube_limit(verified_story):
    script = VideoScript(
        story_id=verified_story.raw.id,
        hook="Rupee steadies after IMF review confirms continued economic support today.",
        what_happened="Facts here.",
    )
    package = VideoPackage(
        story=verified_story, script=script,
        video=AvatarVideo(),
        thumbnail=Thumbnail(),
    )
    metadata = build_metadata(package, show=None)
    assert len(metadata["title"]) <= 100
    assert metadata["title"].endswith("WNX24")


def test_build_metadata_tags_include_story_keywords(make_verified_story):
    story = make_verified_story(keywords=["Pakistan", "IMF"])
    script = VideoScript(story_id=story.raw.id, hook="Rupee steadies.", what_happened="Facts.")
    package = VideoPackage(
        story=story, script=script,
        video=AvatarVideo(),
        thumbnail=Thumbnail(),
    )
    metadata = build_metadata(package, show=None)
    assert "Pakistan" in metadata["tags"]
