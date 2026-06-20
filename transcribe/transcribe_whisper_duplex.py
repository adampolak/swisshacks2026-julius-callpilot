#!/usr/bin/env python3
"""Near-real-time English mic + speaker transcription using faster-whisper or whisper.cpp.

Whisper is not a native streaming recognizer. This script captures microphone
and speaker/loopback audio continuously, cuts it into short chunks, transcribes
each chunk, and prints results to stdout.

Use the default faster-whisper backend for a pure Python workflow. Use
--backend whispercpp when you already have a whisper.cpp binary and ggml model.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from dataclasses import dataclass, field
from typing import Any


LOOPBACK_HINTS = (
    "monitor",
    "loopback",
    "blackhole",
    "soundflower",
    "vb-cable",
    "cable output",
    "stereo mix",
    "what u hear",
)

WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?", re.IGNORECASE)
WHISPERCPP_TIMESTAMP_RE = re.compile(
    r"^\[(?P<start>\d{2}:\d{2}:\d{2}(?:[\.,]\d+)?)\s+-->\s+"
    r"(?P<end>\d{2}:\d{2}:\d{2}(?:[\.,]\d+)?)\]\s*(?P<text>.*)$"
)
WHISPERCPP_DIAGNOSTIC_PREFIXES = (
    "whisper_",
    "system_info:",
    "main:",
    "ggml_",
    "build:",
    "print_timings:",
    "load_time",
    "mel_time",
    "sample_time",
    "encode_time",
    "decode_time",
    "batchd_time",
    "prompt_time",
    "total_time",
)


@dataclass(frozen=True)
class Chunk:
    source: str
    audio: Any
    started_at: float
    ended_at: float


@dataclass(frozen=True)
class TranscriptSegment:
    text: str
    start: float | None = None
    end: float | None = None


@dataclass
class SourceDedupeState:
    recent_words: list[str] = field(default_factory=list)
    last_audio_end_with_text: float | None = None


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr, flush=True)


def require_module(module_name: str, install_hint: str) -> Any:
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        raise SystemExit(f"Missing dependency '{module_name}'. Install with: {install_hint}") from exc


def hostapi_name(sd: Any, device: dict[str, Any]) -> str:
    try:
        return sd.query_hostapis(device["hostapi"])["name"]
    except Exception:
        return "unknown"


def input_devices(sd: Any) -> list[tuple[int, dict[str, Any]]]:
    return [
        (idx, device)
        for idx, device in enumerate(sd.query_devices())
        if int(device.get("max_input_channels", 0)) > 0
    ]


def list_devices(sd: Any) -> None:
    default_input = sd.default.device[0] if sd.default.device else None
    print("Input-capable devices:")
    for idx, device in input_devices(sd):
        flags = []
        if idx == default_input:
            flags.append("default-input")
        if any(hint in device["name"].lower() for hint in LOOPBACK_HINTS):
            flags.append("loopback-candidate")
        suffix = f" ({', '.join(flags)})" if flags else ""
        print(
            f"  {idx}: {device['name']} "
            f"[{hostapi_name(sd, device)}; in={device['max_input_channels']}; "
            f"default_sr={int(device['default_samplerate'])}]{suffix}"
        )


def device_description(sd: Any, device_index: int | None) -> str:
    if device_index is None:
        return "default input"
    device = sd.query_devices(device_index)
    return f"{device_index}: {device['name']} [{hostapi_name(sd, device)}]"


def device_capture_sample_rate(sd: Any, device_index: int, requested_rate: int | None) -> int:
    if requested_rate:
        return requested_rate
    device = sd.query_devices(device_index)
    return int(round(float(device["default_samplerate"])))


def device_input_channels(sd: Any, device_index: int) -> int:
    device = sd.query_devices(device_index)
    return max(1, min(2, int(device.get("max_input_channels", 1))))


def resolve_input_device(sd: Any, selector: str | None, role: str) -> int:
    devices = input_devices(sd)
    if not devices:
        raise SystemExit("No input-capable audio devices found. Check OS audio permissions and PortAudio setup.")

    if selector is None:
        default_input = sd.default.device[0] if sd.default.device else None
        if default_input is None or int(default_input) < 0:
            raise SystemExit(f"No default input device found for {role}. Use --list-devices and --{role}-device.")
        return int(default_input)

    try:
        index = int(selector)
    except ValueError:
        index = -1

    if index >= 0:
        try:
            device = sd.query_devices(index)
        except Exception as exc:
            raise SystemExit(f"Audio device index {index} does not exist.") from exc
        if int(device.get("max_input_channels", 0)) <= 0:
            raise SystemExit(f"Audio device {index} is not input-capable.")
        return index

    needle = selector.lower()
    exact_matches = [(idx, dev) for idx, dev in devices if dev["name"].lower() == needle]
    substring_matches = [(idx, dev) for idx, dev in devices if needle in dev["name"].lower()]
    matches = exact_matches or substring_matches
    if not matches:
        raise SystemExit(f"No input device matched '{selector}'. Run with --list-devices.")
    if len(matches) > 1:
        eprint(f"Multiple devices matched '{selector}', using {matches[0][0]}: {matches[0][1]['name']}")
    return int(matches[0][0])


def auto_loopback_device(sd: Any, avoid_index: int | None = None) -> int | None:
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for idx, device in input_devices(sd):
        name = device["name"].lower()
        score = 0
        for hint in LOOPBACK_HINTS:
            if hint in name:
                score += 10 if hint in ("monitor", "loopback", "blackhole") else 6
        if score:
            scored.append((score, idx, device))

    if not scored:
        return None

    scored.sort(key=lambda item: (-item[0], item[1]))
    for _, idx, _ in scored:
        if idx != avoid_index:
            return int(idx)
    return int(scored[0][1])


def emit(lock: threading.Lock, as_json: bool, chunk: Chunk, result_type: str, text: str) -> None:
    text = " ".join(text.split())
    if not text:
        return
    with lock:
        if as_json:
            payload = {
                "time": dt.datetime.now().isoformat(timespec="seconds"),
                "source": chunk.source,
                "type": result_type,
                "chunk_started_at": dt.datetime.fromtimestamp(chunk.started_at).isoformat(timespec="seconds"),
                "chunk_ended_at": dt.datetime.fromtimestamp(chunk.ended_at).isoformat(timespec="seconds"),
                "text": text,
            }
            print(json.dumps(payload, ensure_ascii=False), flush=True)
        else:
            timestamp = dt.datetime.now().strftime("%H:%M:%S")
            print(f"[{timestamp}] {chunk.source}: {text}", flush=True)


def normalized_word_spans(text: str) -> list[tuple[str, int, int]]:
    return [(match.group(0).lower(), match.start(), match.end()) for match in WORD_RE.finditer(text)]


def normalized_words(text: str) -> list[str]:
    return [word for word, _start, _end in normalized_word_spans(text)]


def drop_duplicate_prefix(
    text: str,
    state: SourceDedupeState,
    max_prefix_words: int,
    min_match_words: int,
) -> str:
    if max_prefix_words <= 0 or min_match_words <= 0 or not state.recent_words:
        return text

    current = normalized_word_spans(text)
    if not current:
        return text

    current_words = [word for word, _start, _end in current]
    max_match = min(len(state.recent_words), len(current_words), max_prefix_words)
    if max_match < min_match_words:
        return text

    duplicate_words = 0
    for word_count in range(max_match, min_match_words - 1, -1):
        if state.recent_words[-word_count:] == current_words[:word_count]:
            duplicate_words = word_count
            break

    if duplicate_words == 0:
        return text
    if duplicate_words >= len(current):
        return ""

    cut_position = current[duplicate_words - 1][2]
    return text[cut_position:].lstrip(" \t\r\n,.;:!?-")


def update_text_history(state: SourceDedupeState, text: str, history_words: int) -> None:
    if history_words <= 0:
        state.recent_words = []
        return
    words = normalized_words(text)
    if words:
        state.recent_words = (state.recent_words + words)[-history_words:]


def trim_segments_by_timestamp(
    chunk: Chunk,
    segments: list[TranscriptSegment],
    state: SourceDedupeState,
    margin_seconds: float,
) -> list[TranscriptSegment]:
    if state.last_audio_end_with_text is None:
        return segments

    kept: list[TranscriptSegment] = []
    trim_before = state.last_audio_end_with_text + margin_seconds
    for segment in segments:
        if segment.end is None:
            kept.append(segment)
            continue
        absolute_end = chunk.started_at + segment.end
        if absolute_end > trim_before:
            kept.append(segment)
    return kept


def segments_to_text(segments: list[TranscriptSegment]) -> str:
    return " ".join(segment.text.strip() for segment in segments if segment.text.strip())


def max_segment_audio_end(chunk: Chunk, segments: list[TranscriptSegment]) -> float:
    timestamped_ends = [chunk.started_at + segment.end for segment in segments if segment.end is not None]
    if timestamped_ends:
        return max(timestamped_ends)
    return chunk.ended_at


def dedupe_and_emit_segments(
    output_lock: threading.Lock,
    as_json: bool,
    chunk: Chunk,
    segments: list[TranscriptSegment],
    state: SourceDedupeState,
    timestamp_dedupe: bool,
    timestamp_margin_seconds: float,
    text_dedupe: bool,
    dedupe_history_words: int,
    dedupe_max_prefix_words: int,
    dedupe_min_match_words: int,
) -> None:
    usable_segments = [segment for segment in segments if segment.text.strip()]
    if timestamp_dedupe:
        usable_segments = trim_segments_by_timestamp(chunk, usable_segments, state, timestamp_margin_seconds)

    text = segments_to_text(usable_segments)
    if text_dedupe:
        text = drop_duplicate_prefix(text, state, dedupe_max_prefix_words, dedupe_min_match_words)
    text = " ".join(text.split())
    if not text:
        return

    emit(output_lock, as_json, chunk, "final", text)
    update_text_history(state, text, dedupe_history_words)

    audio_end = max_segment_audio_end(chunk, usable_segments)
    if state.last_audio_end_with_text is None:
        state.last_audio_end_with_text = audio_end
    else:
        state.last_audio_end_with_text = max(state.last_audio_end_with_text, audio_end)


def mono_pcm16_bytes(np: Any, indata: Any, capture_sample_rate: int, target_sample_rate: int) -> bytes:
    if indata.ndim == 1:
        mono = indata.astype(np.float32)
    elif indata.shape[1] == 1:
        mono = indata[:, 0].astype(np.float32)
    else:
        mono = indata.astype(np.float32).mean(axis=1)

    if capture_sample_rate != target_sample_rate and mono.size > 1:
        output_size = max(1, int(round(mono.size * target_sample_rate / capture_sample_rate)))
        original_positions = np.arange(mono.size, dtype=np.float32)
        target_positions = np.linspace(0, mono.size - 1, output_size, dtype=np.float32)
        mono = np.interp(target_positions, original_positions, mono)

    return np.clip(mono, -32768, 32767).astype("<i2").tobytes()


def make_audio_callback(
    source: str,
    audio_queue: "queue.Queue[bytes | None]",
    np: Any,
    capture_sample_rate: int,
    target_sample_rate: int,
):
    dropped_blocks = 0

    def callback(indata: Any, frames: int, time_info: Any, status: Any) -> None:
        nonlocal dropped_blocks
        if status:
            eprint(f"{source} audio status: {status}")
        try:
            audio_queue.put_nowait(mono_pcm16_bytes(np, indata, capture_sample_rate, target_sample_rate))
        except queue.Full:
            dropped_blocks += 1
            if dropped_blocks == 1 or dropped_blocks % 100 == 0:
                eprint(f"{source}: audio queue full, dropped {dropped_blocks} block(s)")

    return callback


def enqueue_chunk(transcription_queue: "queue.Queue[Chunk | None]", chunk: Chunk, source: str) -> None:
    try:
        transcription_queue.put_nowait(chunk)
        return
    except queue.Full:
        pass

    try:
        transcription_queue.get_nowait()
        eprint(f"{source}: transcription backlog full, dropped the oldest chunk")
    except queue.Empty:
        pass

    try:
        transcription_queue.put_nowait(chunk)
    except queue.Full:
        eprint(f"{source}: transcription backlog full, dropped a chunk")


def chunker_loop(
    source: str,
    audio_queue: "queue.Queue[bytes | None]",
    transcription_queue: "queue.Queue[Chunk | None]",
    sample_rate: int,
    chunk_seconds: float,
    overlap_seconds: float,
    silence_threshold: float,
    stop_event: threading.Event,
) -> None:
    np = require_module("numpy", "pip install numpy")

    chunk_samples = max(1, int(sample_rate * chunk_seconds))
    overlap_samples = max(0, int(sample_rate * overlap_seconds))
    if overlap_samples >= chunk_samples:
        overlap_samples = max(0, chunk_samples - 1)
    hop_samples = chunk_samples - overlap_samples

    buffer = np.empty(0, dtype=np.float32)
    buffer_started_at: float | None = None

    while not stop_event.is_set() or not audio_queue.empty():
        try:
            data = audio_queue.get(timeout=0.2)
        except queue.Empty:
            continue
        if data is None:
            break

        samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        if samples.size == 0:
            continue
        if buffer_started_at is None:
            buffer_started_at = time.time()
        buffer = np.concatenate((buffer, samples))

        while buffer.size >= chunk_samples:
            chunk_audio = buffer[:chunk_samples].copy()
            now = time.time()
            if silence_threshold <= 0 or float(np.sqrt(np.mean(chunk_audio * chunk_audio))) >= silence_threshold:
                start = buffer_started_at if buffer_started_at is not None else now - chunk_seconds
                enqueue_chunk(
                    transcription_queue,
                    Chunk(source=source, audio=chunk_audio, started_at=start, ended_at=now),
                    source,
                )
            buffer = buffer[hop_samples:]
            retained_seconds = buffer.size / sample_rate
            buffer_started_at = now - retained_seconds if buffer.size else None

    if buffer.size >= sample_rate:
        now = time.time()
        if silence_threshold <= 0 or float(np.sqrt(np.mean(buffer * buffer))) >= silence_threshold:
            start = buffer_started_at if buffer_started_at is not None else now - (buffer.size / sample_rate)
            enqueue_chunk(transcription_queue, Chunk(source=source, audio=buffer.copy(), started_at=start, ended_at=now), source)


def faster_whisper_worker(
    transcription_queue: "queue.Queue[Chunk | None]",
    stop_event: threading.Event,
    output_lock: threading.Lock,
    as_json: bool,
    model: Any,
    beam_size: int,
    vad_filter: bool,
    timestamp_dedupe: bool,
    timestamp_margin_seconds: float,
    text_dedupe: bool,
    dedupe_history_words: int,
    dedupe_max_prefix_words: int,
    dedupe_min_match_words: int,
) -> None:
    states: dict[str, SourceDedupeState] = {}

    while not stop_event.is_set() or not transcription_queue.empty():
        try:
            chunk = transcription_queue.get(timeout=0.2)
        except queue.Empty:
            continue
        if chunk is None:
            break

        segments, _info = model.transcribe(
            chunk.audio,
            language="en",
            task="transcribe",
            beam_size=beam_size,
            vad_filter=vad_filter,
            condition_on_previous_text=False,
        )
        transcript_segments = [
            TranscriptSegment(text=segment.text, start=float(segment.start), end=float(segment.end))
            for segment in segments
        ]
        state = states.setdefault(chunk.source, SourceDedupeState())
        dedupe_and_emit_segments(
            output_lock,
            as_json,
            chunk,
            transcript_segments,
            state,
            timestamp_dedupe,
            timestamp_margin_seconds,
            text_dedupe,
            dedupe_history_words,
            dedupe_max_prefix_words,
            dedupe_min_match_words,
        )


def write_wav(path: str, audio: Any, sample_rate: int) -> None:
    np = require_module("numpy", "pip install numpy")
    pcm = np.clip(audio * 32767.0, -32768, 32767).astype("<i2")
    with wave.open(path, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm.tobytes())


def timestamp_to_seconds(value: str) -> float:
    time_part, separator, fraction = value.replace(",", ".").partition(".")
    hours_text, minutes_text, seconds_text = time_part.split(":")
    seconds = int(hours_text) * 3600 + int(minutes_text) * 60 + int(seconds_text)
    if separator:
        seconds += float(f"0.{fraction}")
    return seconds


def is_whispercpp_diagnostic(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    return stripped.startswith(WHISPERCPP_DIAGNOSTIC_PREFIXES)


def parse_whispercpp_segments(raw_output: str) -> list[TranscriptSegment]:
    segments: list[TranscriptSegment] = []
    untimestamped_lines: list[str] = []

    for line in raw_output.splitlines():
        stripped = line.strip()
        if is_whispercpp_diagnostic(stripped):
            continue

        match = WHISPERCPP_TIMESTAMP_RE.match(stripped)
        if match:
            segments.append(
                TranscriptSegment(
                    text=match.group("text"),
                    start=timestamp_to_seconds(match.group("start")),
                    end=timestamp_to_seconds(match.group("end")),
                )
            )
            continue

        cleaned = re.sub(r"^\[[^\]]+\]\s*", "", stripped)
        if cleaned:
            untimestamped_lines.append(cleaned)

    if segments:
        return segments
    if untimestamped_lines:
        return [TranscriptSegment(text=" ".join(untimestamped_lines))]
    return []


def whispercpp_worker(
    transcription_queue: "queue.Queue[Chunk | None]",
    stop_event: threading.Event,
    output_lock: threading.Lock,
    as_json: bool,
    sample_rate: int,
    binary: str,
    model_path: str,
    extra_args: list[str],
    timestamp_dedupe: bool,
    timestamp_margin_seconds: float,
    text_dedupe: bool,
    dedupe_history_words: int,
    dedupe_max_prefix_words: int,
    dedupe_min_match_words: int,
) -> None:
    states: dict[str, SourceDedupeState] = {}

    while not stop_event.is_set() or not transcription_queue.empty():
        try:
            chunk = transcription_queue.get(timeout=0.2)
        except queue.Empty:
            continue
        if chunk is None:
            break

        wav_path = ""
        try:
            with tempfile.NamedTemporaryFile(prefix="whisper_chunk_", suffix=".wav", delete=False) as tmp:
                wav_path = tmp.name
            write_wav(wav_path, chunk.audio, sample_rate)
            command = [
                binary,
                "-m",
                model_path,
                "-f",
                wav_path,
                "-l",
                "en",
            ]
            command.extend(extra_args)
            completed = subprocess.run(command, capture_output=True, text=True, check=False)
            if completed.returncode != 0:
                eprint(f"whisper.cpp failed with exit code {completed.returncode}: {completed.stderr.strip()}")
                continue
            transcript_segments = parse_whispercpp_segments(completed.stdout)
            state = states.setdefault(chunk.source, SourceDedupeState())
            dedupe_and_emit_segments(
                output_lock,
                as_json,
                chunk,
                transcript_segments,
                state,
                timestamp_dedupe,
                timestamp_margin_seconds,
                text_dedupe,
                dedupe_history_words,
                dedupe_max_prefix_words,
                dedupe_min_match_words,
            )
        finally:
            if wav_path:
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Transcribe microphone and speaker/loopback audio in near real time using Whisper."
    )
    parser.add_argument("--list-devices", action="store_true", help="List input-capable audio devices and exit.")
    parser.add_argument("--backend", choices=("faster-whisper", "whispercpp"), default="faster-whisper")
    parser.add_argument("--sample-rate", type=int, default=16000, help="Recognizer sample rate after resampling.")
    parser.add_argument(
        "--capture-sample-rate",
        type=int,
        help="Device capture sample rate. Defaults to each device's PortAudio default rate.",
    )
    parser.add_argument("--block-ms", type=int, default=100, help="Audio callback block size in milliseconds.")
    parser.add_argument("--chunk-seconds", type=float, default=5.0, help="Seconds of audio per Whisper transcription chunk.")
    parser.add_argument("--overlap-seconds", type=float, default=0.0, help="Chunk overlap. Can improve context but may duplicate text.")
    parser.add_argument(
        "--silence-threshold",
        type=float,
        default=0.003,
        help="RMS threshold below which chunks are skipped. Use 0 to disable.",
    )
    parser.add_argument("--mic-device", help="Microphone input device index or name substring. Defaults to OS default input.")
    parser.add_argument(
        "--speaker-device",
        default="auto",
        help="Speaker loopback/monitor input device index/name, or 'auto'. Defaults to auto.",
    )
    parser.add_argument("--no-mic", action="store_true", help="Do not capture microphone audio.")
    parser.add_argument("--no-speaker", action="store_true", help="Do not capture speaker/system audio.")
    parser.add_argument("--json", action="store_true", help="Emit JSON lines instead of plain text.")
    parser.add_argument("--backlog", type=int, default=8, help="Maximum queued chunks waiting for transcription.")
    parser.add_argument(
        "--no-timestamp-dedupe",
        action="store_true",
        help="Disable timestamp-based overlap trimming.",
    )
    parser.add_argument(
        "--timestamp-dedupe-margin",
        type=float,
        default=0.2,
        help="Seconds of tolerance when dropping timestamped overlap text.",
    )
    parser.add_argument(
        "--no-text-dedupe",
        action="store_true",
        help="Disable normalized suffix/prefix text deduplication.",
    )
    parser.add_argument(
        "--dedupe-history-words",
        type=int,
        default=80,
        help="Number of previously emitted normalized words kept per source.",
    )
    parser.add_argument(
        "--dedupe-max-prefix-words",
        type=int,
        default=40,
        help="Maximum current prefix length checked against previous output.",
    )
    parser.add_argument(
        "--dedupe-min-match-words",
        type=int,
        default=3,
        help="Minimum repeated word count required before text is removed.",
    )

    parser.add_argument("--faster-whisper-model", default="base.en", help="faster-whisper model name or local path.")
    parser.add_argument("--compute-device", default="cpu", help="faster-whisper compute device, e.g. cpu or cuda.")
    parser.add_argument("--compute-type", default="int8", help="faster-whisper compute type, e.g. int8, float16, float32.")
    parser.add_argument("--cpu-threads", type=int, default=4, help="CPU threads for faster-whisper.")
    parser.add_argument("--beam-size", type=int, default=1, help="Whisper beam size.")
    parser.add_argument("--no-vad", action="store_true", help="Disable faster-whisper VAD filtering.")

    parser.add_argument("--whispercpp-bin", default=os.getenv("WHISPERCPP_BIN", "whisper-cli"))
    parser.add_argument("--whispercpp-model", default=os.getenv("WHISPERCPP_MODEL"))
    parser.add_argument(
        "--whispercpp-extra-arg",
        action="append",
        default=[],
        help="Extra argument passed to whisper.cpp. Repeat for multiple args.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    sd = require_module("sounddevice", "pip install sounddevice")
    np = require_module("numpy", "pip install numpy")

    if args.list_devices:
        list_devices(sd)
        return 0

    if args.no_mic and args.no_speaker:
        raise SystemExit("Both --no-mic and --no-speaker were set; there is nothing to transcribe.")
    if args.chunk_seconds <= 0:
        raise SystemExit("--chunk-seconds must be greater than zero.")
    if args.overlap_seconds < 0:
        raise SystemExit("--overlap-seconds cannot be negative.")
    if args.timestamp_dedupe_margin < 0:
        raise SystemExit("--timestamp-dedupe-margin cannot be negative.")
    if args.dedupe_history_words < 0:
        raise SystemExit("--dedupe-history-words cannot be negative.")
    if args.dedupe_max_prefix_words < 0:
        raise SystemExit("--dedupe-max-prefix-words cannot be negative.")
    if args.dedupe_min_match_words < 1:
        raise SystemExit("--dedupe-min-match-words must be at least 1.")
    if args.backend == "whispercpp" and not args.whispercpp_model:
        raise SystemExit("The whisper.cpp backend requires --whispercpp-model or WHISPERCPP_MODEL.")

    sources: list[tuple[str, int]] = []
    mic_device: int | None = None
    if not args.no_mic:
        mic_device = resolve_input_device(sd, args.mic_device, "mic")
        sources.append(("mic", mic_device))

    if not args.no_speaker:
        if args.speaker_device == "auto":
            speaker_device = auto_loopback_device(sd, avoid_index=mic_device)
            if speaker_device is None:
                raise SystemExit(
                    "Could not auto-detect a speaker loopback/monitor input. Run --list-devices and pass "
                    "--speaker-device. On macOS, install a virtual loopback device such as BlackHole."
                )
        else:
            speaker_device = resolve_input_device(sd, args.speaker_device, "speaker")
        sources.append(("speaker", speaker_device))

    output_lock = threading.Lock()
    stop_event = threading.Event()
    audio_queues: list[queue.Queue[bytes | None]] = []
    threads: list[threading.Thread] = []
    streams: list[Any] = []
    transcription_queue: queue.Queue[Chunk | None] = queue.Queue(maxsize=max(1, args.backlog))

    if args.backend == "faster-whisper":
        faster_whisper = require_module("faster_whisper", "pip install faster-whisper")
        eprint(f"Loading faster-whisper model '{args.faster_whisper_model}'...")
        whisper_model = faster_whisper.WhisperModel(
            args.faster_whisper_model,
            device=args.compute_device,
            compute_type=args.compute_type,
            cpu_threads=args.cpu_threads,
        )
        eprint("faster-whisper model loaded.")
        worker = threading.Thread(
            target=faster_whisper_worker,
            args=(
                transcription_queue,
                stop_event,
                output_lock,
                args.json,
                whisper_model,
                args.beam_size,
                not args.no_vad,
                not args.no_timestamp_dedupe,
                args.timestamp_dedupe_margin,
                not args.no_text_dedupe,
                args.dedupe_history_words,
                args.dedupe_max_prefix_words,
                args.dedupe_min_match_words,
            ),
            daemon=True,
        )
    else:
        whispercpp_binary = shutil.which(args.whispercpp_bin) if not os.path.isabs(args.whispercpp_bin) else args.whispercpp_bin
        if not whispercpp_binary or not os.path.exists(whispercpp_binary):
            raise SystemExit(f"whisper.cpp binary not found: {args.whispercpp_bin}")
        if not os.path.isfile(args.whispercpp_model):
            raise SystemExit(f"whisper.cpp model file not found: {args.whispercpp_model}")
        worker = threading.Thread(
            target=whispercpp_worker,
            args=(
                transcription_queue,
                stop_event,
                output_lock,
                args.json,
                args.sample_rate,
                whispercpp_binary,
                args.whispercpp_model,
                args.whispercpp_extra_arg,
                not args.no_timestamp_dedupe,
                args.timestamp_dedupe_margin,
                not args.no_text_dedupe,
                args.dedupe_history_words,
                args.dedupe_max_prefix_words,
                args.dedupe_min_match_words,
            ),
            daemon=True,
        )
    worker.start()
    threads.append(worker)

    try:
        for source_name, device_index in sources:
            capture_sample_rate = device_capture_sample_rate(sd, device_index, args.capture_sample_rate)
            channels = device_input_channels(sd, device_index)
            blocksize = max(1, int(capture_sample_rate * args.block_ms / 1000))

            audio_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=200)
            audio_queues.append(audio_queue)
            chunker = threading.Thread(
                target=chunker_loop,
                args=(
                    source_name,
                    audio_queue,
                    transcription_queue,
                    args.sample_rate,
                    args.chunk_seconds,
                    args.overlap_seconds,
                    args.silence_threshold,
                    stop_event,
                ),
                daemon=True,
            )
            chunker.start()
            threads.append(chunker)

            callback = make_audio_callback(source_name, audio_queue, np, capture_sample_rate, args.sample_rate)
            stream = sd.InputStream(
                samplerate=capture_sample_rate,
                blocksize=blocksize,
                device=device_index,
                channels=channels,
                dtype="int16",
                callback=callback,
            )
            stream.start()
            streams.append(stream)
            eprint(
                f"{source_name}: {device_description(sd, device_index)} "
                f"(capture {capture_sample_rate} Hz/{channels} ch -> recognize {args.sample_rate} Hz mono)"
            )

        eprint(f"Listening with {args.backend}. Press Ctrl-C to stop.")
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        eprint("Stopping...")
    finally:
        stop_event.set()
        for stream in streams:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        for audio_queue in audio_queues:
            try:
                audio_queue.put_nowait(None)
            except queue.Full:
                pass
        try:
            transcription_queue.put_nowait(None)
        except queue.Full:
            pass
        for thread in threads:
            thread.join(timeout=3)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
