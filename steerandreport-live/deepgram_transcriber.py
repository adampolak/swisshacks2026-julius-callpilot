import asyncio
import importlib
import json
import os
import queue
import threading
import time
from urllib.parse import urlencode


class TranscriptionUnavailable(RuntimeError):
    pass


def _int_env(name, default):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise TranscriptionUnavailable(f"{name} must be an integer") from exc


def _channel_list_env(name, default):
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    try:
        return [int(part.strip()) for part in value.split(",") if part.strip()]
    except ValueError as exc:
        raise TranscriptionUnavailable(f"{name} must be a comma-separated list of integers") from exc


class DeepgramDuplexTranscriber:
    """
    Server-owned version of transcribe-api/deepgram_double_source.py.

    Captures an aggregate input device, sends mic/system audio as Deepgram stereo,
    and exposes browser-ready RM/client transcript turns.
    """

    def __init__(self):
        self.api_key = (
            os.environ.get("DG_API_KEY")
            or os.environ.get("DEEPGRAM_API_KEY")
            or os.environ.get("DEEPGRAM_API_KEY".lower())
        )
        self.device = os.environ.get("TRANSCRIBE_DEVICE", "Aggregate In")
        self.total_channels = _int_env("TRANSCRIBE_TOTAL_CH", 3)
        self.mic_channel = _int_env("TRANSCRIBE_MIC_CH", 0)
        self.system_channels = _channel_list_env("TRANSCRIBE_SYS_CH", [1, 2])
        self.sample_rate = _int_env("TRANSCRIBE_SAMPLE_RATE", 48000)
        self.frame_ms = _int_env("TRANSCRIBE_FRAME_MS", 20)
        self.language = os.environ.get("TRANSCRIBE_LANGUAGE", "en")
        self.model = os.environ.get("TRANSCRIBE_DEEPGRAM_MODEL", "nova-3")

        self.blocksize = self.sample_rate * self.frame_ms // 1000
        self._lock = threading.RLock()
        self._thread = None
        self._stop_event = threading.Event()
        self._audio_queue = queue.Queue(maxsize=250)
        self._turns = []
        self._interim = {"rm": "", "client": ""}
        self._status = "idle"
        self._last_error = ""
        self._audio_status = ""
        self._started_at = None
        self._np = None
        self._sd = None
        self._websockets = None
        self._connection_closed = None

    def _load_dependencies(self):
        missing = []
        for module in ("numpy", "sounddevice", "websockets"):
            try:
                importlib.import_module(module)
            except ImportError:
                missing.append(module)
        if missing:
            raise TranscriptionUnavailable(
                "Live transcription requires: "
                + ", ".join(missing)
                + ". Install the Deepgram capture dependencies before starting a call."
            )

        self._np = importlib.import_module("numpy")
        self._sd = importlib.import_module("sounddevice")
        self._websockets = importlib.import_module("websockets")
        self._connection_closed = importlib.import_module("websockets.exceptions").ConnectionClosed

    def _validate_config(self):
        if not self.api_key or self.api_key == "YOUR_DEEPGRAM_API_KEY":
            raise TranscriptionUnavailable("DG_API_KEY or DEEPGRAM_API_KEY is required for live transcription")
        if self.total_channels <= 0:
            raise TranscriptionUnavailable("TRANSCRIBE_TOTAL_CH must be greater than zero")
        channels = [self.mic_channel] + self.system_channels
        if not self.system_channels:
            raise TranscriptionUnavailable("TRANSCRIBE_SYS_CH must contain at least one channel")
        if min(channels) < 0 or max(channels) >= self.total_channels:
            raise TranscriptionUnavailable("Mic/system channel indexes must be inside TRANSCRIBE_TOTAL_CH")

    def start(self, reset=False):
        self._load_dependencies()
        self._validate_config()

        with self._lock:
            if self._thread and self._thread.is_alive():
                return self.snapshot()
            if reset:
                self._reset_locked()
            self._stop_event = threading.Event()
            self._audio_queue = queue.Queue(maxsize=250)
            self._status = "starting"
            self._last_error = ""
            self._started_at = time.time()
            self._thread = threading.Thread(target=self._thread_main, name="deepgram-transcriber", daemon=True)
            self._thread.start()
            return self.snapshot()

    def stop(self):
        thread = None
        with self._lock:
            if self._thread and self._thread.is_alive():
                self._status = "stopping"
                self._stop_event.set()
                thread = self._thread
            else:
                self._status = "stopped" if self._started_at else "idle"
        if thread:
            thread.join(timeout=1.5)
        return self.snapshot()

    def reset(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise TranscriptionUnavailable("Stop live transcription before resetting the transcript")
            self._reset_locked()
            return self.snapshot()

    def current_turns(self):
        with self._lock:
            return [dict(turn) for turn in self._turns]

    def snapshot(self):
        with self._lock:
            running = bool(self._thread and self._thread.is_alive())
            return {
                "status": self._status,
                "running": running,
                "turns": [dict(turn) for turn in self._turns],
                "transcript": [dict(turn) for turn in self._turns],
                "interim": dict(self._interim),
                "last_error": self._last_error,
                "audio_status": self._audio_status,
                "started_at": self._started_at,
                "config": {
                    "device": self.device,
                    "total_channels": self.total_channels,
                    "mic_channel": self.mic_channel,
                    "system_channels": list(self.system_channels),
                    "sample_rate": self.sample_rate,
                    "language": self.language,
                    "model": self.model,
                },
            }

    def _reset_locked(self):
        self._turns = []
        self._interim = {"rm": "", "client": ""}
        self._status = "idle"
        self._last_error = ""
        self._audio_status = ""
        self._started_at = None

    def _set_status(self, status):
        with self._lock:
            self._status = status

    def _set_error(self, exc):
        with self._lock:
            self._last_error = str(exc)
            if not self._stop_event.is_set():
                self._status = "error"

    def _set_audio_status(self, status):
        with self._lock:
            self._audio_status = status

    def _url(self):
        params = {
            "model": self.model,
            "language": self.language,
            "encoding": "linear16",
            "sample_rate": str(self.sample_rate),
            "channels": "2",
            "multichannel": "true",
            "interim_results": "true",
            "smart_format": "true",
            "punctuate": "true",
            "endpointing": "300",
            "utterance_end_ms": "1200",
        }
        return "wss://api.deepgram.com/v1/listen?" + urlencode(params)

    def _thread_main(self):
        try:
            asyncio.run(self._run())
        except Exception as exc:
            self._set_error(exc)
        finally:
            with self._lock:
                self._interim = {"rm": "", "client": ""}
                if self._stop_event.is_set() and self._status != "error":
                    self._status = "stopped"
                elif self._status not in {"error", "stopped"}:
                    self._status = "stopped"

    async def _run(self):
        async with self._websockets.connect(self._url(), subprotocols=["token", self.api_key]) as ws:
            self._set_status("running")
            await asyncio.gather(self._sender(ws), self._receiver(ws))

    async def _sender(self, ws):
        with self._sd.RawInputStream(
            samplerate=self.sample_rate,
            blocksize=self.blocksize,
            dtype="int16",
            channels=self.total_channels,
            device=self.device,
            callback=self._audio_callback,
        ):
            while not self._stop_event.is_set():
                data = await asyncio.get_running_loop().run_in_executor(None, self._next_audio_frame)
                if data is None:
                    continue
                await ws.send(data)
        try:
            await ws.send(json.dumps({"type": "CloseStream"}))
        except Exception:
            pass

    async def _receiver(self, ws):
        while not self._stop_event.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.25)
            except asyncio.TimeoutError:
                continue
            except self._connection_closed:
                break
            self._handle_message(raw)

    def _next_audio_frame(self):
        try:
            return self._audio_queue.get(timeout=0.25)
        except queue.Empty:
            return None

    def _audio_callback(self, indata, frames, callback_time, status):
        if status:
            self._set_audio_status(str(status))

        np = self._np
        buf = np.frombuffer(bytes(indata), dtype="<i2").reshape(-1, self.total_channels)
        me = buf[:, self.mic_channel].astype(np.int32)
        them = buf[:, self.system_channels].mean(axis=1).astype(np.int32)

        stereo = np.empty(me.size * 2, dtype="<i2")
        stereo[0::2] = np.clip(me, -32768, 32767)
        stereo[1::2] = np.clip(them, -32768, 32767)
        frame = stereo.tobytes()

        try:
            self._audio_queue.put_nowait(frame)
        except queue.Full:
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._audio_queue.put_nowait(frame)
            except queue.Full:
                pass

    def _handle_message(self, raw):
        msg = json.loads(raw)
        print(msg)
        if msg.get("type") != "Results":
            return

        indexes = msg.get("channel_index") or [0]
        channel = int(indexes[0])
        alternative = (msg.get("channel", {}).get("alternatives") or [{}])[0]
        text = alternative.get("transcript", "").strip()
        start = float(msg.get("start", 0.0) or 0.0)
        end = start + float(msg.get("duration", 0.0) or 0.0)
        role = "rm" if channel == 0 else "client"
        speaker = "Relationship Manager" if role == "rm" else "Client"

	

        with self._lock:
            if msg.get("is_final"):
                self._interim[role] = ""
                if text:
                    self._turns.append(
                        {
                            "turn_index": len(self._turns),
                            "role": role,
                            "speaker": speaker,
                            "text": text,
                            "channel": channel,
                            "start": round(start, 2),
                            "end": round(end, 2),
                        }
                    )
            else:
                self._interim[role] = text
