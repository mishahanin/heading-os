#!/usr/bin/env python3
"""Transcribe speech in a local audio or video file to text, entirely on-machine.

Backed by faster-whisper (CTranslate2), so nothing is uploaded anywhere - the
audio, the model, and the transcript all stay on this laptop. Container decoding
goes through PyAV, so .mp4/.mkv/.mov/.webm are read directly with no ffmpeg step.

The model is downloaded once into the HuggingFace cache (~/.cache/huggingface)
on first use and reused offline afterwards.

Usage:
    python scripts/transcribe-media.py clip.mp4
    python scripts/transcribe-media.py clip.mp4 --format srt --out clip.srt
    python scripts/transcribe-media.py call.m4a --language ru --timestamps
    python scripts/transcribe-media.py talk.mp4 --model small --format json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from scripts.utils.venv import ensure_venv  # noqa: E402

ensure_venv()
from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET  # noqa: E402

# `medium`, not large-v3, and the measurement is the reason. Over 120s of real
# speech on the batched path, large-v3 returned the transcript with ZERO commas,
# periods or capitals, while medium punctuated it properly (29/34/53) AND ran
# faster (0.94x vs 0.76x realtime). An unreadable wall of lowercase is not the
# higher-quality answer. large-v3 stays reachable via --model, where it is worth
# having on the --sequential path; batched it is the pathological one.
DEFAULT_MODEL = "medium"
# Models that lose punctuation under batched decoding (measured, see above).
UNPUNCTUATED_WHEN_BATCHED = ("large-v3",)
# int8 is the only quantisation this CPU reports beyond float32, and it is the
# standard speed/accuracy tradeoff for CTranslate2 on CPU.
DEFAULT_COMPUTE_TYPE = "int8"
DEFAULT_BATCH_SIZE = 8
FORMATS = ("txt", "srt", "vtt", "json")
# Batched inference is ~7x faster on this CPU (measured: large-v3 over 120s of
# speech, 0.11x -> 0.76x realtime), but it segments by VAD chunk, so 120s came
# back as 5 cues instead of 37. That is fine for prose and useless for
# subtitles, so the subtitle formats default to the sequential path.
FINE_GRAINED_FORMATS = ("srt", "vtt")
# Batching also costs punctuation unevenly: on an 86s Russian note, `medium`
# punctuated two of three chunks and returned the third as a bare lowercase run,
# where the sequential path punctuated all of it and split it into 12 sentences.
# Sequential is 2.6x slower (0.33x vs 0.87x realtime), which is nothing on a
# short note and hours on a long recording - so short files buy the quality and
# long ones buy the speed.
SEQUENTIAL_UNDER_SECONDS = 600
# A punctuated `initial_prompt` looks like the obvious cure for batched
# large-v3's unpunctuated output, and on a 30s clip it works. On the full 120s
# it DESTROYS CONTENT: two of five chunks came back as a verbatim echo of the
# prompt, and the speech they covered was simply gone from the transcript.
# Losing 40% of a recording is far worse than losing commas, so there is no
# default prompt. --prompt stays available as an explicit, eyes-open override.
PROMPT_ECHO_WARNING = (
    "a prompt can be echoed back in place of real speech; check the transcript covers the whole recording"
)


# ============================================================
# Rendering (pure functions - no model, no I/O)
# ============================================================

def format_timestamp(seconds: float, separator: str = ",") -> str:
    """Render a second offset as an SRT/VTT timestamp, HH:MM:SS,mmm.

    SRT uses a comma before the milliseconds, WebVTT uses a dot.
    """
    total_ms = max(0, round(float(seconds) * 1000))
    hours, total_ms = divmod(total_ms, 3_600_000)
    minutes, total_ms = divmod(total_ms, 60_000)
    secs, millis = divmod(total_ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def render_txt(segments: list[dict], timestamps: bool = False) -> str:
    """Plain transcript, one segment per line, optionally time-prefixed."""
    lines = []
    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue
        if timestamps:
            lines.append(f"[{format_timestamp(seg['start'], '.')[:8]}] {text}")
        else:
            lines.append(text)
    return "\n".join(lines) + ("\n" if lines else "")


def render_srt(segments: list[dict]) -> str:
    """SubRip subtitles, 1-indexed cues."""
    blocks = []
    for index, seg in enumerate(segments, start=1):
        text = seg["text"].strip()
        if not text:
            continue
        start = format_timestamp(seg["start"], ",")
        end = format_timestamp(seg["end"], ",")
        blocks.append(f"{index}\n{start} --> {end}\n{text}\n")
    return "\n".join(blocks)


def render_vtt(segments: list[dict]) -> str:
    """WebVTT subtitles."""
    blocks = ["WEBVTT\n"]
    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue
        start = format_timestamp(seg["start"], ".")
        end = format_timestamp(seg["end"], ".")
        blocks.append(f"{start} --> {end}\n{text}\n")
    return "\n".join(blocks)


def render_json(segments: list[dict], meta: dict) -> str:
    """Machine-readable transcript: metadata plus every segment."""
    return json.dumps({**meta, "segments": segments}, ensure_ascii=False, indent=2) + "\n"


def render(segments: list[dict], meta: dict, fmt: str, timestamps: bool = False) -> str:
    """Dispatch to the renderer for `fmt`."""
    if fmt == "txt":
        return render_txt(segments, timestamps=timestamps)
    if fmt == "srt":
        return render_srt(segments)
    if fmt == "vtt":
        return render_vtt(segments)
    if fmt == "json":
        return render_json(segments, meta)
    raise ValueError(f"unknown format: {fmt!r}")


# ============================================================
# Transcription
# ============================================================

def probe_duration(media: Path) -> float | None:
    """Read the container's duration in seconds, or None if it does not say.

    Container metadata only - no decoding, no model, milliseconds of work.
    """
    from scripts.utils.optdeps import require

    require("av", extra="media")
    import av

    try:
        with av.open(str(media)) as container:
            if container.duration is None:
                return None
            return float(container.duration) / av.time_base
    except (OSError, ValueError) as exc:
        print(f"{GRAY}duration probe failed ({exc}); assuming a long file{RESET}", file=sys.stderr)
        return None


def resolve_batching(fmt: str, explicit: bool | None, duration: float | None = None) -> bool:
    """Decide whether to use batched inference.

    An explicit --batched/--sequential always wins. Otherwise batching is the
    fast path, declined in the two cases where it costs more than it saves:
    subtitle formats (it returns coarse cues) and short files (it drops
    punctuation on some chunks, and the sequential penalty is small there).
    An unknown duration is treated as long, so a huge file never lands on the
    slow path by accident.
    """
    if explicit is not None:
        return explicit
    if fmt in FINE_GRAINED_FORMATS:
        return False
    if duration is None:
        return True
    return duration >= SEQUENTIAL_UNDER_SECONDS


def resolve_prompt(explicit: str | None) -> str | None:
    """Return the initial prompt: whatever the operator passed, or none at all.

    There is deliberately no default. See PROMPT_ECHO_WARNING - an auto-filled
    prompt silently ate 40% of a measured 120s transcript.
    """
    return explicit or None


def punctuation_warning(model: str, batched: bool) -> str | None:
    """Warn before a model/mode pair that is known to drop all punctuation."""
    if batched and model in UNPUNCTUATED_WHEN_BATCHED:
        return f"{model} returns no punctuation or capitals when batched; use --sequential, or --model medium"
    return None


def load_model(name: str, compute_type: str, threads: int, batched: bool = False):
    """Load a faster-whisper model on CPU (downloads on first use).

    Returns either the model or a batched pipeline wrapping it; both expose the
    same ``.transcribe`` keyword interface, so callers do not branch.
    """
    from scripts.utils.optdeps import require

    require("faster_whisper", extra="media")
    from faster_whisper import BatchedInferencePipeline, WhisperModel

    model = WhisperModel(name, device="cpu", compute_type=compute_type, cpu_threads=threads)
    return BatchedInferencePipeline(model=model) if batched else model


def transcribe(
    model,
    media: Path,
    language: str | None,
    word_timestamps: bool,
    vad: bool,
    quiet: bool,
    batch_size: int | None = None,
    initial_prompt: str | None = None,
) -> tuple[list[dict], dict]:
    """Run the model over `media`, returning (segments, metadata).

    faster-whisper yields segments lazily, so decoding progress is reported to
    stderr as the generator is drained - stdout stays pure transcript.
    """
    extra = {"batch_size": batch_size} if batch_size else {}
    if initial_prompt:
        extra["initial_prompt"] = initial_prompt
    segment_iter, info = model.transcribe(
        str(media),
        language=language,
        vad_filter=vad,
        word_timestamps=word_timestamps,
        beam_size=5,
        **extra,
    )

    meta = {
        "source": media.name,
        "language": info.language,
        "language_probability": round(float(info.language_probability), 4),
        "duration_seconds": round(float(info.duration), 2),
    }
    if not quiet:
        detected = f"{info.language} (p={meta['language_probability']})"
        print(
            f"{CYAN}Language:{RESET} {detected}   "
            f"{CYAN}Audio:{RESET} {format_timestamp(info.duration, '.')[:8]}",
            file=sys.stderr,
        )

    segments: list[dict] = []
    for seg in segment_iter:
        entry = {"start": round(float(seg.start), 3), "end": round(float(seg.end), 3), "text": seg.text}
        if word_timestamps and seg.words:
            entry["words"] = [
                {"start": round(float(w.start), 3), "end": round(float(w.end), 3), "word": w.word}
                for w in seg.words
            ]
        segments.append(entry)
        if not quiet:
            done = format_timestamp(seg.end, ".")[:8]
            print(f"{GRAY}  {done} / {format_timestamp(info.duration, '.')[:8]}{RESET}", file=sys.stderr)

    return segments, meta


# ============================================================
# CLI
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Transcribe a local audio/video file to text, fully on-machine.",
    )
    parser.add_argument("media", type=Path, help="path to the audio or video file")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"whisper model (default: {DEFAULT_MODEL})")
    parser.add_argument("--language", default=None, help="force a language code (default: auto-detect)")
    parser.add_argument("--format", dest="fmt", choices=FORMATS, default="txt", help="output format (default: txt)")
    parser.add_argument("--out", type=Path, default=None, help="write to this path (default: stdout)")
    parser.add_argument("--timestamps", action="store_true", help="prefix each txt line with its start time")
    parser.add_argument("--word-timestamps", action="store_true", help="include per-word times (json format)")
    parser.add_argument("--no-vad", action="store_true", help="disable voice-activity filtering")
    parser.add_argument(
        "--compute-type",
        default=DEFAULT_COMPUTE_TYPE,
        help=f"CTranslate2 quantisation (default: {DEFAULT_COMPUTE_TYPE})",
    )
    parser.add_argument("--threads", type=int, default=0, help="CPU threads (default: all cores)")
    parser.add_argument("--quiet", action="store_true", help="suppress progress on stderr")
    parser.add_argument(
        "--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help=f"batched chunks (default: {DEFAULT_BATCH_SIZE})"
    )
    parser.add_argument(
        "--prompt", default=None,
        help="optional style/vocabulary prompt (off by default: it can be echoed over real speech)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--batched", dest="batched", action="store_const", const=True, default=None,
        help="force the fast batched path (~7x, coarser segments)",
    )
    mode.add_argument(
        "--sequential", dest="batched", action="store_const", const=False,
        help="force the slow path (fine segments; the default for srt/vtt)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    media = args.media.expanduser().resolve()
    if not media.is_file():
        print(f"{RED}[ERROR]{RESET} not a file: {media}", file=sys.stderr)
        return 1

    threads = args.threads if args.threads > 0 else (os.cpu_count() or 4)
    batched = resolve_batching(args.fmt, args.batched, probe_duration(media))

    if not args.quiet:
        mode = f"batched x{args.batch_size}" if batched else "sequential"
        print(
            f"{CYAN}Model:{RESET} {BOLD}{args.model}{RESET} "
            f"({args.compute_type}, {threads} threads, {mode}){GRAY} - first run downloads it{RESET}",
            file=sys.stderr,
        )

    warning = punctuation_warning(args.model, batched)
    if warning and not args.quiet:
        print(f"{RED}[WARN]{RESET} {warning}", file=sys.stderr)

    model = load_model(args.model, args.compute_type, threads, batched=batched)
    segments, meta = transcribe(
        model,
        media,
        language=args.language,
        word_timestamps=args.word_timestamps,
        vad=not args.no_vad,
        quiet=args.quiet,
        batch_size=args.batch_size if batched else None,
        initial_prompt=resolve_prompt(args.prompt),
    )
    if args.prompt and not args.quiet:
        print(f"{RED}[WARN]{RESET} {PROMPT_ECHO_WARNING}", file=sys.stderr)
    meta["model"] = args.model
    meta["batched"] = batched

    output = render(segments, meta, args.fmt, timestamps=args.timestamps)

    if args.out is None:
        sys.stdout.write(output)
    else:
        out_path = args.out.expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        if not args.quiet:
            print(f"{GREEN}Wrote{RESET} {out_path} ({len(segments)} segments)", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
