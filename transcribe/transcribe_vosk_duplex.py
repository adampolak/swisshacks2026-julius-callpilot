#!/usr/bin/env python3
"""Real-time English mic + speaker transcription using Vosk.

This script captures two input streams:
  - microphone: the default input device unless --mic-device is provided
  - speaker/system audio: an input-like loopback/monitor device

On Ubuntu, speaker audio is usually a PulseAudio/PipeWire "monitor" source.
On macOS, install and route audio through a virtual device such as BlackHole.
Use --list-devices to find the device names/indices available to PortAudio.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib
import json
import os
import queue
import sys
import threading
import time
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


def emit(lock: threading.Lock, as_json: bool, source: str, result_type: str, text: str) -> None:
    text = " ".join(text.split())
    if not text:
        return
    with lock:
        if as_json:
            payload = {
                "time": dt.datetime.now().isoformat(timespec="seconds"),
                "source": source,
                "type": result_type,
                "text": text,
            }
            print(json.dumps(payload, ensure_ascii=False), flush=True)
        else:
            timestamp = dt.datetime.now().strftime("%H:%M:%S")
            label = f"{source} {result_type}" if result_type != "final" else source
            print(f"[{timestamp}] {label}: {text}", flush=True)


def recognizer_loop(
    source: str,
    audio_queue: "queue.Queue[bytes | None]",
    model: Any,
    sample_rate: int,
    stop_event: threading.Event,
    output_lock: threading.Lock,
    as_json: bool,
    partials: bool,
    words: bool,
) -> None:
    vosk = require_module("vosk", "pip install vosk")
    recognizer = vosk.KaldiRecognizer(model, sample_rate)
    recognizer.SetWords(words)
    last_partial = ""

    while not stop_event.is_set() or not audio_queue.empty():
        try:
            data = audio_queue.get(timeout=0.2)
        except queue.Empty:
            continue
        if data is None:
            break

        if recognizer.AcceptWaveform(data):
            result = json.loads(recognizer.Result())
            last_partial = ""
            emit(output_lock, as_json, source, "final", result.get("text", ""))
        elif partials:
            partial = json.loads(recognizer.PartialResult()).get("partial", "")
            if partial and partial != last_partial:
                last_partial = partial
                emit(output_lock, as_json, source, "partial", partial)

    final_result = json.loads(recognizer.FinalResult())
    emit(output_lock, as_json, source, "final", final_result.get("text", ""))


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Transcribe microphone and speaker/loopback audio in real time using Vosk."
    )
    parser.add_argument("--list-devices", action="store_true", help="List input-capable audio devices and exit.")
    parser.add_argument("--model", default=os.getenv("VOSK_MODEL_PATH"), help="Path to an English Vosk model directory.")
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=16000,
        help="Recognizer sample rate after resampling. Vosk English models usually use 16000.",
    )
    parser.add_argument(
        "--capture-sample-rate",
        type=int,
        help="Device capture sample rate. Defaults to each device's PortAudio default rate.",
    )
    parser.add_argument("--block-ms", type=int, default=100, help="Audio callback block size in milliseconds.")
    parser.add_argument("--mic-device", help="Microphone input device index or name substring. Defaults to OS default input.")
    parser.add_argument(
        "--speaker-device",
        default="auto",
        help="Speaker loopback/monitor input device index/name, or 'auto'. Defaults to auto.",
    )
    parser.add_argument("--no-mic", action="store_true", help="Do not capture microphone audio.")
    parser.add_argument("--no-speaker", action="store_true", help="Do not capture speaker/system audio.")
    parser.add_argument("--partials", action="store_true", help="Print partial recognition updates as they arrive.")
    parser.add_argument("--json", action="store_true", help="Emit JSON lines instead of plain text.")
    parser.add_argument("--words", action="store_true", help="Ask Vosk to include word timings in internal results.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    sd = require_module("sounddevice", "pip install sounddevice")
    np = require_module("numpy", "pip install numpy")

    if args.list_devices:
        list_devices(sd)
        return 0

    if not args.model:
        raise SystemExit(
            "A Vosk English model path is required. Pass --model or set VOSK_MODEL_PATH. "
            "Example model: vosk-model-small-en-us-0.15"
        )
    if not os.path.isdir(args.model):
        raise SystemExit(f"Vosk model directory does not exist: {args.model}")

    vosk = require_module("vosk", "pip install vosk")
    model = vosk.Model(args.model)

    if args.no_mic and args.no_speaker:
        raise SystemExit("Both --no-mic and --no-speaker were set; there is nothing to transcribe.")

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

    try:
        for source_name, device_index in sources:
            capture_sample_rate = device_capture_sample_rate(sd, device_index, args.capture_sample_rate)
            channels = device_input_channels(sd, device_index)
            blocksize = max(1, int(capture_sample_rate * args.block_ms / 1000))

            audio_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=200)
            audio_queues.append(audio_queue)
            worker = threading.Thread(
                target=recognizer_loop,
                args=(
                    source_name,
                    audio_queue,
                    model,
                    args.sample_rate,
                    stop_event,
                    output_lock,
                    args.json,
                    args.partials,
                    args.words,
                ),
                daemon=True,
            )
            worker.start()
            threads.append(worker)

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

        eprint("Listening. Press Ctrl-C to stop.")
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
        for thread in threads:
            thread.join(timeout=3)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
