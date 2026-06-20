import argparse
import ast
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from deepgram_transcriber import DeepgramDuplexTranscriber, TranscriptionUnavailable
from demo_data import (
    DEMO_CLM_PROFILE,
    DEMO_TRANSCRIPT,
    GI_SYSTEM_PROMPT,
    MOCK_ROUND_1,
    MOCK_ROUND_2,
    MOCK_ROUND_3,
)
from prompts import REPORT_PROMPT


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
API_KEY_FILES = (ROOT / "deepseek_api_key.txt", ROOT.parent / "deepseek_api_key.txt")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
LOCAL_MODEL = os.environ.get("LOCAL_STEERING_MODEL", "qwen2.5:1.5b")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
CARD_PATTERN = re.compile(r"^(?:[-*]\s*)?\[(RECALL|DATA|FLAG|OPPORTUNITY)\]\s+(.+)$", re.IGNORECASE)
LIVE_TRANSCRIBER = DeepgramDuplexTranscriber()
WRAPPING_QUOTES = "\"'`"
LLM_CHAT_LOCK = threading.RLock()
LLM_CHAT_STATE = {
    "messages": [],
    "last_turn_index": -1,
    "profile_markdown": None,
}


def _read_json(handler):
    length = int(handler.headers.get("content-length", "0"))
    if length == 0:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8"))


def _request_path(handler):
    return urllib.parse.urlsplit(handler.path).path


def _json_response(handler, payload, status=200):
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("content-type", "application/json; charset=utf-8")
    handler.send_header("content-length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _live_transcript():
    return LIVE_TRANSCRIBER.current_turns()


def _read_api_key():
    api_key_file = next((path for path in API_KEY_FILES if path.exists()), None)
    if api_key_file is None:
        raise RuntimeError("deepseek_api_key.txt was not found in steerandreport or the repository root")
    key = api_key_file.read_text(encoding="utf-8").strip()
    if not key:
        raise RuntimeError("deepseek_api_key.txt is empty")
    return key


def _call_deepseek_json(system_prompt, user_payload, temperature=0.2, max_tokens=900):
    request_body = {
        "model": DEEPSEEK_MODEL,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt.strip()},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
    }
    return json.loads(_deepseek_request(request_body))


def _deepseek_request(request_body):
    request = urllib.request.Request(
        DEEPSEEK_URL,
        data=json.dumps(request_body).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {_read_api_key()}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def _call_ollama_text(messages, temperature=0.05, max_tokens=110):
    request_body = {
        "model": LOCAL_MODEL,
        "stream": False,
        "think": False,
        "keep_alive": "10m",
        "options": {"temperature": temperature, "num_predict": max_tokens},
        "messages": messages,
    }
    request = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data.get("message", {}).get("content", "[SILENT]")


def _format_transcript(turns, start_index=0):
    lines = []
    for offset, turn in enumerate(turns):
        fallback_index = start_index + offset
        turn_index = int(turn.get("turn_index", fallback_index))
        role = str(turn.get("role", "unknown")).lower()
        label = "RM" if role == "rm" else "Client" if role == "client" else role.upper()
        text = str(turn.get("text", "")).strip()
        if text:
            lines.append(f"{turn_index + 1}. {label}: {text}")
    return "\n".join(lines)


def _profile_markdown(profile):
    return profile.get("profile_markdown") or DEMO_CLM_PROFILE["profile_markdown"]


def _initial_ollama_messages(profile):
    return [
        {"role": "system", "content": GI_SYSTEM_PROMPT.strip()},
        {
            "role": "user",
            "content": (
                "CLIENT PROFILE (ground truth):\n\n"
                + _profile_markdown(profile)
                + "\n\nThe following user messages append only new finalized transcript turns since your previous call."
            ),
        },
    ]


def _prior_non_silent_outputs(history):
    outputs = []
    for item in history or []:
        output = str(item.get("output", "")).strip()
        if output and output != "[SILENT]":
            outputs.append(output)
    return outputs


def _reset_ollama_chat_state(profile, history=None, last_turn_index=-1):
    messages = _initial_ollama_messages(profile)
    prior_outputs = _prior_non_silent_outputs(history)
    if prior_outputs:
        messages.append(
            {
                "role": "assistant",
                "content": "PREVIOUS NON-SILENT STEERING OUTPUTS ALREADY SHOWN:\n" + "\n".join(prior_outputs),
            }
        )
    LLM_CHAT_STATE["messages"] = messages
    LLM_CHAT_STATE["last_turn_index"] = last_turn_index
    LLM_CHAT_STATE["profile_markdown"] = _profile_markdown(profile)


def _history_last_turn_index(history):
    indexes = []
    for item in history or []:
        try:
            indexes.append(int(item.get("turn_index", -1)))
        except (TypeError, ValueError):
            continue
    return max(indexes, default=-1)


def _ensure_ollama_chat_state(profile, turns, history):
    latest_index = len(turns) - 1
    profile_markdown = _profile_markdown(profile)
    history_last_index = _history_last_turn_index(history)
    should_reset = (
        not LLM_CHAT_STATE["messages"]
        or LLM_CHAT_STATE["profile_markdown"] != profile_markdown
        or (not history and LLM_CHAT_STATE["last_turn_index"] >= 0)
        or latest_index < LLM_CHAT_STATE["last_turn_index"]
        or (history and history_last_index < min(LLM_CHAT_STATE["last_turn_index"], latest_index) - 1)
    )
    if should_reset:
        resume_index = history_last_index if history else -1
        _reset_ollama_chat_state(profile, history=history, last_turn_index=resume_index)


def _call_ollama_incremental(profile, turns, history, max_tokens=180):
    with LLM_CHAT_LOCK:
        _ensure_ollama_chat_state(profile, turns, history)
        start_index = LLM_CHAT_STATE["last_turn_index"] + 1
        new_turns = turns[start_index:]
        if not new_turns:
            return "[SILENT]", LLM_CHAT_STATE["last_turn_index"], False

        user_message = {
            "role": "user",
            "content": (
                "NEW TRANSCRIPT TURN(S) SINCE LAST CALL:\n\n"
                + _format_transcript(new_turns, start_index)
            ),
        }
        messages = LLM_CHAT_STATE["messages"] + [user_message]
        raw_output = _call_ollama_text(messages, max_tokens=max_tokens)
        latest_sent_index = len(turns) - 1
        LLM_CHAT_STATE["messages"] = messages + [{"role": "assistant", "content": str(raw_output or "[SILENT]").strip()}]
        LLM_CHAT_STATE["last_turn_index"] = latest_sent_index
        return raw_output, latest_sent_index, True


def _replace_last_ollama_assistant_output(output):
    with LLM_CHAT_LOCK:
        if LLM_CHAT_STATE["messages"] and LLM_CHAT_STATE["messages"][-1].get("role") == "assistant":
            LLM_CHAT_STATE["messages"][-1]["content"] = output


def _is_silent_output(raw_output):
    lines = _llm_output_lines(raw_output)
    if len(lines) != 1:
        return False
    return _clean_llm_line(lines[0]) in {"[SILENT]", "SILENT"}


def _clean_llm_line(value):
    text = str(value or "").strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.strip("`").strip()
    while len(text) >= 2 and text[0] in WRAPPING_QUOTES and text[-1] == text[0]:
        text = text[1:-1].strip()
    return text


def _llm_output_lines(raw_output):
    raw_text = str(raw_output or "").strip()
    try:
        parsed = ast.literal_eval(raw_text)
    except (SyntaxError, ValueError):
        parsed = None

    if isinstance(parsed, str):
        return [_clean_llm_line(parsed)]
    if isinstance(parsed, (list, tuple)):
        return [_clean_llm_line(item) for item in parsed]

    lines = []
    for raw_line in raw_text.splitlines():
        line = _clean_llm_line(raw_line)
        if line.startswith("```"):
            continue
        if line:
            lines.append(line)
    return lines


def _parse_llm_cards(raw_output):
    cards = []
    for line in _llm_output_lines(raw_output):
        match = CARD_PATTERN.match(line)
        if match:
            cards.append({"category": match.group(1).upper(), "text": match.group(2).strip(), "sources": ["Ollama"]})
    if cards:
        return cards

    text = _clean_llm_line(raw_output)
    if not text:
        return []
    return [{"category": "LLM", "text": text, "sources": ["Ollama"]}]


def _canonical_card_output(cards):
    return "\n".join(f"[{card['category']}] {card['text']}" for card in cards)


def steer_turn(profile, turns, history):
    start = time.perf_counter()
    if not turns:
        return {
            "silent": True,
            "cards": [],
            "output": "[SILENT]",
            "model_source": "idle",
            "latency_ms": 0,
            "knowledge_sources": [],
            "knowledge_route": [],
            "active_source": None,
        }

    model_source = f"local:{LOCAL_MODEL}"
    route_error = ""
    sent_new_transcript = False
    try:
        raw_output, sent_turn_index, sent_new_transcript = _call_ollama_incremental(
            profile,
            turns,
            history,
            max_tokens=180,
        )
    except Exception as exc:
        raw_output = "[SILENT]"
        sent_turn_index = len(turns) - 1
        model_source = "local-unavailable"
        route_error = str(exc)

    output = _clean_llm_line(raw_output) or "[SILENT]"
    is_silent = _is_silent_output(output)
    cards = [] if is_silent else _parse_llm_cards(output)
    parsed = {
        "silent": is_silent,
        "cards": cards,
        "output": "[SILENT]" if is_silent else _canonical_card_output(cards) if cards else output,
        "model_source": model_source,
        "latency_ms": max(1, int((time.perf_counter() - start) * 1000)),
        "turn_index": sent_turn_index,
        "routed": False,
        "sent_new_transcript": sent_new_transcript,
        "knowledge_sources": [],
        "knowledge_route": [],
        "active_source": None,
    }
    if sent_new_transcript and model_source != "local-unavailable":
        _replace_last_ollama_assistant_output(parsed["output"])
    if route_error:
        parsed["route_error"] = route_error
    return parsed


def _mock_pipeline():
    return {
        "mode": "mock",
        "rounds": [
            {"name": "Round 1 - Gi Live Steering", "latency_ms": 12, "prompt": "Gi/system_prompt.md", "output": MOCK_ROUND_1},
            {"name": "Round 2 - Full Conversation Report", "latency_ms": 742, "prompt": "REPORT_PROMPT", "output": MOCK_ROUND_2},
            {"name": "Round 3 - Report Refinement", "latency_ms": 588, "prompt": "REPORT_PROMPT", "output": MOCK_ROUND_3},
        ],
    }


def run_pipeline(clm_profile, transcript, mode):
    if mode == "mock":
        return _mock_pipeline()

    rounds = [
        {"name": "Round 1 - Gi Live Steering", "latency_ms": 0, "prompt": "Gi/system_prompt.md", "output": MOCK_ROUND_1}
    ]
    start = time.perf_counter()
    report = _call_deepseek_json(
        REPORT_PROMPT,
        {"clm_profile": clm_profile, "full_transcript": transcript, "stage": "draft_report"},
        temperature=0.2,
        max_tokens=1400,
    )
    rounds.append(
        {"name": "Round 2 - Full Conversation Report", "latency_ms": int((time.perf_counter() - start) * 1000), "prompt": "REPORT_PROMPT", "output": report}
    )
    start = time.perf_counter()
    refined = _call_deepseek_json(
        REPORT_PROMPT,
        {
            "clm_profile": clm_profile,
            "full_transcript": transcript,
            "draft_report": report,
            "stage": "refine_for_rm_action_pack",
            "refinement_instruction": "Make it concise, actionable, relationship-aware, and explicit about unanswered questions and overdue commitments.",
        },
        temperature=0.15,
        max_tokens=1400,
    )
    rounds.append(
        {"name": "Round 3 - Report Refinement", "latency_ms": int((time.perf_counter() - start) * 1000), "prompt": "REPORT_PROMPT", "output": refined}
    )
    return {"mode": "deepseek", "rounds": rounds}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def do_GET(self):
        path = _request_path(self)
        if path == "/api/demo":
            transcription = LIVE_TRANSCRIBER.snapshot()
            _json_response(
                self,
                {
                    "clmProfile": DEMO_CLM_PROFILE,
                    "transcript": transcription["turns"],
                    "transcription": transcription,
                    "steeringContract": "Gi/system_prompt.md",
                },
            )
            return
        if path == "/api/transcription":
            _json_response(self, LIVE_TRANSCRIBER.snapshot())
            return
        return super().do_GET()

    def do_POST(self):
        path = _request_path(self)
        if path == "/api/transcription/start":
            try:
                body = _read_json(self)
                _json_response(self, LIVE_TRANSCRIBER.start(reset=bool(body.get("reset", False))))
            except TranscriptionUnavailable as exc:
                _json_response(self, {"error": str(exc), "transcription": LIVE_TRANSCRIBER.snapshot()}, status=503)
            except Exception as exc:
                _json_response(self, {"error": str(exc), "transcription": LIVE_TRANSCRIBER.snapshot()}, status=500)
            return

        if path == "/api/transcription/stop":
            _json_response(self, LIVE_TRANSCRIBER.stop())
            return

        if path == "/api/transcription/reset":
            try:
                _json_response(self, LIVE_TRANSCRIBER.reset())
            except TranscriptionUnavailable as exc:
                _json_response(self, {"error": str(exc), "transcription": LIVE_TRANSCRIBER.snapshot()}, status=409)
            return

        if path == "/api/steering":
            try:
                body = _read_json(self)
                turns = body.get("turns") or []
                latest_index = int(body.get("latestTurnIndex", len(turns) - 1))
                turns = turns[: latest_index + 1]
                result = steer_turn(
                    body.get("clmProfile") or DEMO_CLM_PROFILE,
                    turns,
                    body.get("history") or [],
                )
                _json_response(self, result)
            except Exception as exc:
                _json_response(self, {"error": str(exc)}, status=500)
            return

        if path != "/api/run-pipeline":
            _json_response(self, {"error": "Not found"}, status=404)
            return

        try:
            body = _read_json(self)
            mode = body.get("mode", "mock")
            transcript = body.get("transcript")
            if transcript is None:
                transcript = _live_transcript()
            if mode != "mock" and not transcript:
                _json_response(
                    self,
                    {"error": "No live transcript has been recorded yet. Start a call before generating a DeepSeek report."},
                    status=400,
                )
                return
            result = run_pipeline(body.get("clmProfile") or DEMO_CLM_PROFILE, transcript or [], mode)
            _json_response(self, result)
        except (RuntimeError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            _json_response(self, {"error": str(exc), "fallback": _mock_pipeline()}, status=502)


def main():
    parser = argparse.ArgumentParser(description="Private-banking conversation intelligence prototype")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--mode", choices=("mock", "deepseek"), default="mock")
    args = parser.parse_args()

    if args.once:
        print(json.dumps(run_pipeline(DEMO_CLM_PROFILE, DEMO_TRANSCRIPT, args.mode), ensure_ascii=False, indent=2))
        return

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Serving on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
