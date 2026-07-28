#!/usr/bin/env python3
"""Behaviour of the transcript renderers in scripts/transcribe-media.py.

The renderers are the part that can silently corrupt a deliverable: an
off-by-one in the SRT timestamp arithmetic produces subtitles that drift out of
sync without ever raising. They are pure functions of a segment list, so they
are tested directly - no model, no audio, no download.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Load the kebab-case CLI module by path.
_spec = importlib.util.spec_from_file_location("transcribe_media", ROOT / "scripts" / "transcribe-media.py")
transcribe_media = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(transcribe_media)


@pytest.fixture
def segments():
    return [
        {"start": 0.0, "end": 2.5, "text": " Первый сегмент."},
        {"start": 2.5, "end": 3661.75, "text": " Second segment."},
    ]


@pytest.fixture
def meta():
    return {"source": "clip.mp4", "language": "ru", "language_probability": 0.98, "duration_seconds": 3661.75}


# ---------------------------------------------------------------- timestamps

@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0.0, "00:00:00,000"),
        (1.5, "00:00:01,500"),
        (59.999, "00:00:59,999"),
        (60.0, "00:01:00,000"),
        (3600.0, "01:00:00,000"),
        (3661.75, "01:01:01,750"),
    ],
)
def test_format_timestamp_renders_srt_clock(seconds, expected):
    assert transcribe_media.format_timestamp(seconds) == expected


def test_format_timestamp_separator_switches_to_vtt_dot():
    assert transcribe_media.format_timestamp(1.5, ".") == "00:00:01.500"


def test_format_timestamp_clamps_negative_to_zero():
    """A negative offset must not render as a wrapped or signed clock."""
    assert transcribe_media.format_timestamp(-0.5) == "00:00:00,000"


def test_format_timestamp_rounds_rather_than_truncates():
    """0.9999s is 1000ms, not 999ms - truncation would drift over a long file."""
    assert transcribe_media.format_timestamp(0.9999) == "00:00:01,000"


# ------------------------------------------------------------------- renders

def test_render_txt_strips_leading_space_whisper_emits(segments):
    out = transcribe_media.render_txt(segments)
    assert out == "Первый сегмент.\nSecond segment.\n"


def test_render_txt_with_timestamps_prefixes_start_not_end(segments):
    """The prefix is where the line begins - segment 2 starts at 2.5s, ends at 1h."""
    out = transcribe_media.render_txt(segments, timestamps=True)
    assert out.splitlines()[0] == "[00:00:00] Первый сегмент."
    assert out.splitlines()[1] == "[00:00:02] Second segment."


def test_render_txt_timestamp_prefix_survives_the_hour_boundary():
    out = transcribe_media.render_txt([{"start": 3661.75, "end": 3665.0, "text": " Late."}], timestamps=True)
    assert out == "[01:01:01] Late.\n"


def test_render_txt_drops_empty_segments():
    out = transcribe_media.render_txt([{"start": 0.0, "end": 1.0, "text": "   "}])
    assert out == ""


def test_render_srt_numbers_cues_from_one(segments):
    out = transcribe_media.render_srt(segments)
    assert out.startswith("1\n00:00:00,000 --> 00:00:02,500\nПервый сегмент.\n")
    assert "2\n00:00:02,500 --> 01:01:01,750\nSecond segment.\n" in out


def test_render_vtt_has_header_and_dot_separator(segments):
    out = transcribe_media.render_vtt(segments)
    assert out.startswith("WEBVTT\n")
    assert "00:00:00.000 --> 00:00:02.500" in out
    assert "," not in out.split("Первый")[0].split("WEBVTT")[1]


def test_render_json_keeps_cyrillic_unescaped_and_carries_meta(segments, meta):
    out = transcribe_media.render_json(segments, meta)
    parsed = json.loads(out)
    assert parsed["language"] == "ru"
    assert parsed["source"] == "clip.mp4"
    assert len(parsed["segments"]) == 2
    assert "Первый" in out, "ensure_ascii must stay off so Russian transcripts stay readable"


def test_render_dispatches_every_advertised_format(segments, meta):
    """Every choice offered by --format must have a working renderer."""
    for fmt in transcribe_media.FORMATS:
        assert transcribe_media.render(segments, meta, fmt)


def test_render_rejects_unknown_format(segments, meta):
    with pytest.raises(ValueError, match="unknown format"):
        transcribe_media.render(segments, meta, "docx")


# ----------------------------------------------------------------------- CLI

def test_parser_defaults_match_documented_constants():
    args = transcribe_media.build_parser().parse_args(["clip.mp4"])
    assert args.model == transcribe_media.DEFAULT_MODEL
    assert args.compute_type == transcribe_media.DEFAULT_COMPUTE_TYPE
    assert args.fmt == "txt"
    assert args.language is None
    assert args.no_vad is False, "voice-activity filtering is on by default"
    assert args.batched is None, "batching is auto-resolved from the format unless forced"


# ------------------------------------------------------------------ batching

LONG = transcribe_media.SEQUENTIAL_UNDER_SECONDS + 1
SHORT = transcribe_media.SEQUENTIAL_UNDER_SECONDS - 1


def test_long_prose_files_batch_by_default():
    """Past the threshold the sequential penalty is measured in hours."""
    assert transcribe_media.resolve_batching("txt", None, LONG) is True
    assert transcribe_media.resolve_batching("json", None, LONG) is True


def test_short_files_stay_sequential_for_the_punctuation():
    """Measured on an 86s Russian note: batched medium left one chunk of three
    with no punctuation at all; sequential punctuated the whole thing."""
    assert transcribe_media.resolve_batching("txt", None, SHORT) is False


def test_unknown_duration_is_treated_as_long():
    """A file whose container hides its duration must not land on the slow path."""
    assert transcribe_media.resolve_batching("txt", None, None) is True


def test_subtitle_formats_stay_sequential_at_any_length():
    """Batched inference returned 5 cues where sequential returned 37 over the
    same 120s, which is unusable as subtitles."""
    for fmt in transcribe_media.FINE_GRAINED_FORMATS:
        assert transcribe_media.resolve_batching(fmt, None, LONG) is False
        assert transcribe_media.resolve_batching(fmt, None, None) is False


@pytest.mark.parametrize("fmt", ["txt", "srt", "vtt", "json"])
@pytest.mark.parametrize("duration", [SHORT, LONG, None])
def test_explicit_flag_overrides_every_default(fmt, duration):
    assert transcribe_media.resolve_batching(fmt, True, duration) is True
    assert transcribe_media.resolve_batching(fmt, False, duration) is False


def test_batched_and_sequential_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        transcribe_media.build_parser().parse_args(["clip.mp4", "--batched", "--sequential"])


# -------------------------------------------------------------------- prompt

def test_default_model_is_the_one_that_punctuates_when_batched():
    """Measured on 120s of real speech: large-v3 batched returned 0 commas, 0
    periods, 0 capitals; medium returned 29/34/53 and ran faster. The default
    must not be a model that silently strips punctuation on the default path."""
    assert transcribe_media.DEFAULT_MODEL not in transcribe_media.UNPUNCTUATED_WHEN_BATCHED
    assert transcribe_media.punctuation_warning(transcribe_media.DEFAULT_MODEL, True) is None


def test_large_v3_warns_when_batched():
    warning = transcribe_media.punctuation_warning("large-v3", True)
    assert warning is not None
    assert "--sequential" in warning


def test_large_v3_is_silent_when_sequential():
    """Sequential decoding punctuates fine; the warning would be noise there."""
    assert transcribe_media.punctuation_warning("large-v3", False) is None


def test_no_prompt_is_ever_auto_filled():
    """Regression guard on a measured data-loss bug: an auto-filled punctuation
    prompt was echoed back in place of real speech, wiping two of five chunks
    from a 120s transcript. Never default this on again."""
    assert transcribe_media.resolve_prompt(None) is None


def test_explicit_prompt_is_passed_through_and_empty_string_disables():
    assert transcribe_media.resolve_prompt("Custom.") == "Custom."
    assert transcribe_media.resolve_prompt("") is None


def test_transcribe_passes_batch_size_only_when_batching():
    """A batch_size kwarg sent down the sequential path is a TypeError."""
    captured = {}

    class _Info:
        language, language_probability, duration = "en", 0.9, 1.0

    class _Model:
        def transcribe(self, _audio, **kwargs):
            captured.update(kwargs)
            return iter([]), _Info()

    transcribe_media.transcribe(
        _Model(), Path("x.mp4"), language=None, word_timestamps=False, vad=True, quiet=True, batch_size=None
    )
    assert "batch_size" not in captured

    transcribe_media.transcribe(
        _Model(), Path("x.mp4"), language=None, word_timestamps=False, vad=True, quiet=True, batch_size=8
    )
    assert captured["batch_size"] == 8


def test_missing_file_exits_nonzero_without_loading_a_model(monkeypatch, capsys, tmp_path):
    """A bad path must fail fast, before the multi-GB model download."""
    def _explode(*_args, **_kwargs):
        raise AssertionError("model must not be loaded when the input does not exist")

    monkeypatch.setattr(transcribe_media, "load_model", _explode)
    monkeypatch.setattr(sys, "argv", ["transcribe-media.py", str(tmp_path / "nope.mp4")])
    assert transcribe_media.main() == 1
    assert "not a file" in capsys.readouterr().err
