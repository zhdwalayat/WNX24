from core.models import AvatarVideo, VideoScript, VideoStatus


def test_video_script_defaults_word_count_and_duration_to_zero():
    script = VideoScript(story_id="abc", hook="Breaking.")
    assert script.word_count == 0
    assert script.duration_secs == 0


def test_avatar_video_defaults_to_pending_with_no_local_path():
    video = AvatarVideo()
    assert video.status == VideoStatus.PENDING
    assert video.local_path == ""
