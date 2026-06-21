# Duplex live transcription

This directory contains a Python script that captures microphone audio and
speaker/system audio at the same time and prints English transcripts to stdout.

- `transcribe_whisper_duplex.py`: chunked near-real-time recognizer using
  `faster-whisper` by default, with an optional `whisper.cpp` CLI backend.

Diagnostics and device selection messages go to stderr. Transcript lines go to
stdout, so they can be piped into another process.

## OS audio setup

Microphone capture uses the OS default input unless `--mic-device` is passed.
Speaker capture requires an input-like loopback device.

On Ubuntu with PulseAudio or PipeWire, use the output monitor source. Run:

```bash
python3 transcribe_whisper_duplex.py --list-devices
```

Look for a device whose name contains `monitor` or `loopback`, then pass its
index or part of its name with `--speaker-device` if auto-detection does not
pick it.

On macOS, install a virtual audio loopback device such as BlackHole, Soundflower,
or a commercial Loopback device. Route system output to that device, or create a
Multi-Output Device in Audio MIDI Setup so you can still hear the audio. Then
select the virtual device with `--speaker-device`.

## Install

Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y python3-venv libportaudio2 portaudio19-dev
python3 -m venv .venv
. .venv/bin/activate
```

macOS:

```bash
brew install portaudio
python3 -m venv .venv
. .venv/bin/activate
```

For faster-whisper:

```bash
pip install -r requirements-whisper.txt
```

For whisper.cpp, build or install `whisper-cli` and download an English ggml
model. Then pass `--backend whispercpp --whispercpp-model /path/to/model.bin`.

## Usage

List capture devices:

```bash
python3 transcribe_whisper_duplex.py --list-devices
```

Run faster-whisper on CPU:

```bash
python3 transcribe_whisper_duplex.py \
  --backend faster-whisper \
  --faster-whisper-model /path/to/local/faster-whisper-base.en \
  --compute-device cpu \
  --compute-type int8 \
  --speaker-device auto
```

Run whisper.cpp:

```bash
python3 transcribe_whisper_duplex.py \
  --backend whispercpp \
  --whispercpp-bin /path/to/whisper-cli \
  --whispercpp-model /path/to/ggml-base.en.bin \
  --speaker-device auto
```

Machine-readable output:

```bash
python3 transcribe_whisper_duplex.py --json
```

Disable one side if needed:

```bash
python3 transcribe_whisper_duplex.py --no-mic --speaker-device "BlackHole"
```

## Whisper overlap deduplication

`transcribe_whisper_duplex.py` supports overlapping chunks with
`--overlap-seconds`. Both Whisper backends deduplicate repeated overlap text by
default:

- Timestamp dedupe drops segments whose timestamps end inside audio that was
  already emitted for the same source.
- Text dedupe keeps a normalized word history per source and removes a repeated
  prefix when it matches the suffix of prior output.

Example:

```bash
python3 transcribe_whisper_duplex.py \
  --backend faster-whisper \
  --faster-whisper-model /path/to/local/faster-whisper-base.en \
  --chunk-seconds 4 \
  --overlap-seconds 1
```

For `whisper.cpp`, the script now keeps timestamp output enabled so timestamp
dedupe can work. If you pass `--whispercpp-extra-arg=-nt`, timestamp dedupe will
not have timestamps and only text dedupe will apply.
