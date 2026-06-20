"""
DOUBLE SOURCE live transcription: microphone (Me) + system audio (Them) -> Deepgram multichannel.
Test use case: start a WhatsApp call/voice note and talk over it.

macOS prerequisites (see chat):
  - BlackHole 2ch installed
  - an "Aggregate Device" containing: microphone + BlackHole 2ch  (script input)
  - a "Multi-Output Device": headphones + BlackHole 2ch           (system output)

Use HEADPHONES on the Multi-Output device so the speakers don't leak back into the mic.
The Aggregate Device gives a SHARED CLOCK to both inputs -> no drift between channels.

UI: a curses live view.
  - Top:    scrollback of finalized turns, each prefixed with its [start-end] timestamp.
  - Bottom: two live lines (Me / Them) that keep updating from interim results
            until the segment is finalized, then it scrolls up into the history.

Deps:  pip install websockets sounddevice numpy
Run:   export DG_API_KEY=your_key  &&  python deepgram_double_source.py
Stop:  Ctrl+C
"""

import os
import json
import queue
import locale
import asyncio
import curses

import numpy as np
import sounddevice as sd
import websockets

# wide/utf locale must be set before curses.initscr for correct rendering
locale.setlocale(locale.LC_ALL, "")

DG_API_KEY = os.environ.get("DG_API_KEY", "YOUR_DEEPGRAM_API_KEY")

# --- device config (adapt to YOUR aggregate device channel indices) -----------
DEVICE     = "Aggregate In"
TOTAL_CH   = 3
MIC_CH     = 0      # channel 0 -> MacBook Pro Microphone -> Me
SYS_CH     = [1, 2] # channels 1-2 -> BlackHole 2ch -> Them
# ------------------------------------------------------------------------------

SAMPLE_RATE = 48000           # typical for the aggregate device; verify in Audio MIDI Setup
FRAME_MS = 20
BLOCKSIZE = SAMPLE_RATE * FRAME_MS // 1000   # samples per channel per frame
LANGUAGE = "en"

SPEAKER = {0: "Me", 1: "Them"}

URL = (
    "wss://api.deepgram.com/v1/listen?"
    "model=nova-3&"
    f"language={LANGUAGE}&"
    "encoding=linear16&"
    f"sample_rate={SAMPLE_RATE}&"
    "channels=2&"
    "multichannel=true&"      # diarize stays OFF
    "interim_results=true&"
    "smart_format=true&"
    "punctuate=true&"
    "endpointing=300&"
    "utterance_end_ms=1200"
)

out_q: "queue.Queue[bytes]" = queue.Queue()
AUDIO_STATUS = ""   # last sounddevice status (overflow/underflow), shown in the UI


def mic_callback(indata, frames, time, status):
    """Deinterleave the aggregate, extract mic + system, reinterleave as stereo (Me, Them)."""
    global AUDIO_STATUS
    if status:
        # never print: curses owns the screen. surface it via the UI instead.
        AUDIO_STATUS = str(status)
    buf = np.frombuffer(bytes(indata), dtype="<i2").reshape(-1, TOTAL_CH)
    me = buf[:, MIC_CH].astype(np.int32)
    them = buf[:, SYS_CH].mean(axis=1).astype(np.int32)   # downmix stereo->mono

    stereo = np.empty(me.size * 2, dtype="<i2")
    stereo[0::2] = np.clip(me, -32768, 32767)             # channel 0 -> Me
    stereo[1::2] = np.clip(them, -32768, 32767)           # channel 1 -> Them
    out_q.put(stereo.tobytes())


# --- curses live view ---------------------------------------------------------
class CursesUI:
    """
    Live transcription view.

      - history (top): finalized turns, '[start-end] Speaker: text', oldest first.
      - live (bottom): one line per speaker showing the current interim text,
        updated in place until that segment is finalized (then it moves to history).
    """

    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.history = []            # list[(ch, start, end, text)]
        self.interim = {0: "", 1: ""}
        curses.curs_set(0)
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_CYAN, -1)    # Me
            curses.init_pair(2, curses.COLOR_YELLOW, -1)  # Them
            curses.init_pair(3, curses.COLOR_WHITE, -1)   # chrome
        self.render()

    def _color(self, ch):
        return curses.color_pair(1) if ch == 0 else curses.color_pair(2)

    # called from the receiver coroutine (main asyncio thread) -> safe for curses
    def set_interim(self, ch, text):
        self.interim[ch] = text
        self.render()

    def add_final(self, ch, start, end, text):
        text = text.strip()
        if text:
            self.history.append((ch, start, end, text))
        self.interim[ch] = ""        # segment done: clear the live line
        self.render()

    def render(self):
        scr = self.stdscr
        h, w = scr.getmaxyx()
        scr.erase()

        live_top = h - 3             # separator row; live lines below it
        if live_top < 1:
            scr.refresh()
            return

        # history: show the last `live_top` finalized turns
        rows = self.history[-live_top:]
        for row, (ch, start, end, text) in enumerate(rows):
            line = f"[{start:6.2f}-{end:6.2f}] {SPEAKER[ch]}: {text}"
            self._addstr(row, 0, line, w, self._color(ch))

        # separator (+ audio status indicator on the right, if any)
        self._addstr(live_top, 0, "-" * (w - 1), w, curses.color_pair(3))
        if AUDIO_STATUS:
            tag = f" audio: {AUDIO_STATUS} "
            self._addstr(live_top, max(0, w - 1 - len(tag)), tag, w,
                         curses.color_pair(3) | curses.A_BOLD)

        # live lines: Me, Them
        for i, ch in enumerate((0, 1)):
            line = f"{SPEAKER[ch].ljust(4)} | {self.interim[ch]}"
            self._addstr(live_top + 1 + i, 0, line, w,
                         self._color(ch) | curses.A_BOLD)

        scr.refresh()

    def _addstr(self, y, x, text, w, attr):
        """Write a line, truncated to width; keep the tail so the latest words stay visible."""
        maxlen = w - 1 - x
        if maxlen <= 0:
            return
        if len(text) > maxlen:
            text = "..." + text[-(maxlen - 3):] if maxlen > 3 else text[:maxlen]
        try:
            self.stdscr.addstr(y, x, text, attr)
        except curses.error:
            pass


def handle_message(raw, ui):
    msg = json.loads(raw)
    if msg.get("type") == "Results":
        ch = msg["channel_index"][0]
        text = msg["channel"]["alternatives"][0].get("transcript", "")
        start = msg.get("start", 0.0)
        end = start + msg.get("duration", 0.0)
        if msg.get("is_final"):
            ui.add_final(ch, start, end, text)
        else:
            ui.set_interim(ch, text)
    # UtteranceEnd carries no transcript -> nothing to draw for this view


async def main(ui):
    loop = asyncio.get_event_loop()

    async with websockets.connect(URL, subprotocols=["token", DG_API_KEY]) as ws:
        async def sender():
            with sd.RawInputStream(
                samplerate=SAMPLE_RATE, blocksize=BLOCKSIZE, dtype="int16",
                channels=TOTAL_CH, device=DEVICE, callback=mic_callback,
            ):
                while True:
                    data = await loop.run_in_executor(None, out_q.get)
                    await ws.send(data)

        async def receiver():
            async for raw in ws:
                handle_message(raw, ui)

        await asyncio.gather(sender(), receiver())


def _run(stdscr):
    ui = CursesUI(stdscr)
    try:
        asyncio.run(main(ui))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    curses.wrapper(_run)
