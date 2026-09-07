"""Exit checks for the YouTube pipeline.

The seams that can break silently: the upload must never bypass approval, the ffmpeg
command must actually use what the earlier stages produced, and fenced JSON from a
model must still parse.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from jarvis.db.models.agent import Risk
from jarvis.services.policy.service import Decision, PolicyService, ProposalContext
from jarvis.services.youtube.pipeline import _lenient_json, _srt_from_words, build_render_cmd


async def test_youtube_upload_is_r2_and_requires_approval():
    result = await PolicyService(None).evaluate(
        ProposalContext(
            user_id=uuid.uuid4(),
            tool="youtube.upload",
            args={"file_path": "/tmp/final.mp4", "title": "t"},
        )
    )
    assert result.risk is Risk.R2
    assert result.decision is Decision.REQUIRE_APPROVAL


async def test_youtube_reply_is_r2_and_requires_approval():
    result = await PolicyService(None).evaluate(
        ProposalContext(
            user_id=uuid.uuid4(),
            tool="youtube.reply",
            args={"parent_id": "abc", "text": "thanks!"},
        )
    )
    assert result.risk is Risk.R2
    assert result.decision is Decision.REQUIRE_APPROVAL


async def test_youtube_upload_from_untrusted_content_is_denied():
    result = await PolicyService(None).evaluate(
        ProposalContext(
            user_id=uuid.uuid4(),
            tool="youtube.upload",
            args={"file_path": "/tmp/final.mp4", "title": "t"},
            from_untrusted_source=True,
        )
    )
    assert result.decision is Decision.DENY


def test_render_cmd_concats_every_clip_and_burns_captions(tmp_path: Path):
    clips = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    audio, subs, out = tmp_path / "v.mp3", tmp_path / "c.srt", tmp_path / "out.mp4"

    cmd = build_render_cmd(clips, audio, subs, 42.0, out)
    graph = cmd[cmd.index("-filter_complex") + 1]

    assert cmd.count("-i") == 3  # two clips + the voiceover
    assert "concat=n=2" in graph
    assert "subtitles=filename=" in graph and "c.srt" in graph
    assert ["-map", "[out]"] == cmd[cmd.index("-map") : cmd.index("-map") + 2]
    assert cmd[-1] == str(out)


def test_render_cmd_without_broll_uses_gradient_background(tmp_path: Path):
    cmd = build_render_cmd([], tmp_path / "v.mp3", tmp_path / "c.srt", 30.0, tmp_path / "o.mp4")
    assert "lavfi" in cmd
    assert any("gradients=" in part for part in cmd)
    assert any("subtitles=filename=" in part for part in cmd)


def test_render_cmd_without_subs_skips_the_burn(tmp_path: Path):
    cmd = build_render_cmd([], tmp_path / "v.mp3", None, 30.0, tmp_path / "o.mp4")
    assert not any("subtitles" in part for part in cmd)


def test_render_cmd_mixes_looped_music_under_the_voice(tmp_path: Path):
    cmd = build_render_cmd(
        [tmp_path / "a.mp4"], tmp_path / "v.mp3", None, 30.0, tmp_path / "o.mp4",
        music=tmp_path / "bgm.mp3",
    )
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "-stream_loop" in cmd
    assert "amix=inputs=2:duration=first:normalize=0" in graph
    assert ["-map", "[aout]"] == cmd[cmd.index("[aout]") - 1 : cmd.index("[aout]") + 1]


def test_render_cmd_builds_slideshow_from_generated_images(tmp_path: Path):
    images = [tmp_path / "a.png", tmp_path / "b.png", tmp_path / "c.png"]
    cmd = build_render_cmd(
        [], tmp_path / "v.mp3", None, 30.0, tmp_path / "o.mp4", images=images,
    )
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert cmd.count("-loop") == 3, "one looped input per image"
    assert "concat=n=3" in graph
    assert not any("lavfi" in part for part in cmd)


def test_render_cmd_landscape_size_flows_through(tmp_path: Path):
    from jarvis.services.youtube.pipeline import frame_size, is_landscape, target_seconds

    assert target_seconds("make a 2 min video using cartoons") == 120
    assert target_seconds("a 5 minute video about chips") == 300
    assert target_seconds("90 sec explainer") == 90
    assert target_seconds("tech news today") == 55
    assert is_landscape(120) and not is_landscape(60)
    assert frame_size(300) == (1920, 1080)

    cmd = build_render_cmd(
        [tmp_path / "a.mp4"], tmp_path / "v.mp3", None, 120.0, tmp_path / "o.mp4",
        size=(1920, 1080),
    )
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "scale=1920:1080" in graph and "crop=1920:1080" in graph


def test_search_query_strips_production_instructions():
    """The raw request as a query returned cartoon clipart, not tech news."""
    from jarvis.services.youtube.pipeline import strip_production_words

    cleaned = strip_production_words(
        "find latest tech news and make a 2 min video using cartoons explaining it"
    )
    assert "latest tech news" in cleaned
    for noise in ("2 min", "video", "cartoons", "make", "find"):
        assert noise not in cleaned.lower(), f"{noise!r} would poison the search"


def test_named_style_overrides_the_model():
    """Weak models drop the style from the request; the topic decides it."""
    from jarvis.services.youtube.pipeline import style_from_topic, wants_art_style

    cartoon = style_from_topic("tech news using cartoons", "clean cinematic photography")
    assert "cartoon" in cartoon and wants_art_style(cartoon)
    assert "anime" in style_from_topic("anime tech news", "photography")
    # Nothing named: the model's own choice stands.
    assert style_from_topic("tech news", "clean cinematic photography") == (
        "clean cinematic photography"
    )


def test_slideshow_pans_each_still(tmp_path: Path):
    """Stills that just sit there are what made renders look broken."""
    images = [tmp_path / "a.png", tmp_path / "b.png"]
    cmd = build_render_cmd(
        [], tmp_path / "v.mp3", None, 20.0, tmp_path / "o.mp4", images=images,
        size=(1080, 1920),
    )
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert graph.count("zoompan") == 2, "every still gets a Ken Burns push"
    assert "s=1080x1920" in graph
    # Oversampled before zooming, or the pan judders.
    assert "scale=2160:3840" in graph


def test_art_styles_bypass_stock_footage():
    from jarvis.services.youtube.pipeline import wants_art_style

    assert wants_art_style("2D cartoon animation")
    assert wants_art_style("Japanese anime, vibrant")
    assert not wants_art_style("clean cinematic photography")


def test_chat_action_line_parses():
    from jarvis.api.routes.chat import _GENERATE, _SEARCH

    text = 'Starting that now.\nACTION youtube.generate {"topic": "AI news today"}'
    match = _GENERATE.search(text)
    assert match is not None
    assert _GENERATE.sub("", text).strip() == "Starting that now."
    assert _GENERATE.search("I cannot ACTION youtube.generate anything inline") is None

    search = _SEARCH.search('ACTION search {"query": "latest YouTube news"}')
    assert search is not None
    import json

    assert json.loads(search.group(1))["query"] == "latest YouTube news"


def test_lenient_json_unfences():
    assert _lenient_json('```json\n{"title": "x"}\n```') == {"title": "x"}
    assert _lenient_json('{"title": "x"}') == {"title": "x"}


def test_srt_from_words_formats_cues():
    srt = _srt_from_words([(0.0, 0.42, "Hello"), (0.42, 61.5, "world")])
    assert "1\n00:00:00,000 --> 00:00:00,420\nHello" in srt
    assert "2\n00:00:00,420 --> 00:01:01,500\nworld" in srt
