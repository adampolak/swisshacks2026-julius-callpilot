import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from difflib import SequenceMatcher
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

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
CARD_PATTERN = re.compile(r"^\[(RECALL|DATA|FLAG|OPPORTUNITY)\]\s+(.+)$", re.IGNORECASE)


def _read_json(handler):
    length = int(handler.headers.get("content-length", "0"))
    if length == 0:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8"))


def _json_response(handler, payload, status=200):
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("content-type", "application/json; charset=utf-8")
    handler.send_header("content-length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


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
        "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": 10240},
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


def _history_output(item):
    output = str(item.get("output", "")).strip()
    if output:
        return output
    cards = item.get("cards") or []
    if not cards:
        return "[SILENT]"
    return "\n".join(f"[{card['category']}] {card['text']}" for card in cards)


def _selected_profile_facts(text):
    lower = text.lower()
    facts = []

    def add_when(terms, *values):
        if any(term in lower for term in terms):
            facts.extend(values)

    add_when(
        ("other place", "down south", "lucca", "tuscany"),
        "The 'other place down south' is the family villa near Lucca, Tuscany, bought in 2021.",
    )
    add_when(
        ("sophie", "london", "flat", "luca", "university", "app idea"),
        "Sophie is 24 and living in London; Andreas is considering helping her buy a flat.",
        "Luca is 19 and wants funding for a technology start-up; a next-generation team introduction is pending.",
    )
    add_when(
        ("gold",),
        "DATA candidate: gold holding is approximately CHF 1.35M, or 5% of the managed portfolio.",
        "Anti-hallucination requirement: live gold performance is absent and must be pulled and verified before answering.",
    )
    add_when(
        ("nvidia", "ai chip", "semiconductor", "technology"),
        "FLAG candidate: the balanced mandate already has an approximately CHF 3M direct technology and semiconductor sleeve.",
        "A further CHF 3-4M single-name purchase would materially compound concentration; use the figures in a cue.",
    )
    add_when(
        ("sensoria", "25%", "my baby", "life's work"),
        "The retained Sensoria stake is approximately CHF 9M, illiquid and emotionally held.",
        "Never suggest directly selling Sensoria; only tactful value-protection framing is acceptable.",
    )
    add_when(
        ("american bank", "bank that", "nearly went under"),
        "The client has no US regional-bank or material single-bank credit exposure.",
    )
    add_when(
        ("stefan", "biotech", "brother-in-law", "ten-x"),
        "Stefan's tips are recurring low-quality, third-party speculative noise.",
    )
    add_when(
        ("father", "sister", "ticino", "estate", "mess"),
        "OPPORTUNITY candidate: Andreas's father is 82 in Ticino; estate tension with Marina has Italy/Switzerland sensitivity.",
        "This is a significant but sensitive succession-planning opening; lead with empathy. A wealth-planning introduction is pending.",
    )
    add_when(
        ("foundation", "art education", "private credit"),
        "OPPORTUNITY candidate: Claudia has discussed an art-education foundation for over a year.",
        "RECALL candidate: the RM still owes Andreas the private-markets and private-credit research deck from the last meeting.",
        "These are two separate high-value points; two cards are justified if both remain unhandled.",
    )
    return facts[:4]


def _highlight_terms(text):
    lower = text.lower()
    candidates = (
        "gold",
        "nvidia",
        "technology",
        "semiconductor",
        "sensoria",
        "private credit",
        "foundation",
        "father",
        "sister",
        "estate",
        "cross-border",
        "american bank",
        "regional bank",
        "stefan",
        "biotech",
        "sophie",
        "luca",
        "lucca",
    )
    terms = [term for term in candidates if term in lower]
    if any(term in lower for term in ("other place", "down south")):
        terms.extend(["Holiday villa near Lucca", "bought in 2021"])
    if any(term in lower for term in ("american bank", "nearly went under")):
        terms.append("No exposure to US regional banks")
    if "gold" in lower:
        terms.append("Gold position")
    return list(dict.fromkeys(terms))[:5]


def _mock_bank_api(text):
    lower = text.lower()
    if "private credit" in lower:
        return {
            "type": "bank_api",
            "label": "Bank API",
            "title": "Private Markets Desk",
            "status": "Internal knowledge retrieved",
            "query": "Private credit eligibility, liquidity and governance",
            "cue": "Before discussing private credit, confirm liquidity reserve, lock-up tolerance, fees, leverage and conflicts.",
            "answer_terms": ["liquidity", "lock-up", "fees", "leverage", "conflicts"],
            "items": [
                {"title": "Policy PC-14", "text": "Confirm eligible-client classification, liquidity reserve, lock-up tolerance, fees, leverage and conflicts before discussing allocation."},
                {"title": "Desk guidance", "text": "Use an education-first explanation and document why the structure remains understandable to the client and family decision-makers."},
            ],
            "highlights": ["lock-up tolerance", "fees", "liquidity reserve"],
        }
    if any(term in lower for term in ("estate", "father", "sister", "succession", "inherit", "cross-border")):
        return {
            "type": "bank_api",
            "label": "Bank API",
            "title": "Wealth Planning Desk",
            "status": "Internal knowledge retrieved",
            "query": "Cross-border succession planning",
            "cue": "Frame this as discovery; map jurisdictions and documents, then coordinate qualified Swiss and Italian advisers.",
            "answer_terms": ["discovery", "jurisdiction", "documents", "advisers"],
            "items": [
                {"title": "Planning protocol WP-08", "text": "Map residence, nationality, asset location, family decision-makers and existing legal documents before discussing structures."},
                {"title": "Escalation", "text": "Coordinate qualified Swiss and Italian advisers; the RM should frame the next step as discovery, not legal or tax advice."},
            ],
            "highlights": ["asset location", "qualified Swiss and Italian advisers", "discovery"],
        }
    if "foundation" in lower or "philanthrop" in lower:
        return {
            "type": "bank_api",
            "label": "Bank API",
            "title": "Philanthropy Advisory",
            "status": "Internal knowledge retrieved",
            "query": "Art-education foundation discovery",
            "cue": "Clarify purpose, beneficiaries, governance and family participation before discussing implementation options.",
            "answer_terms": ["purpose", "beneficiaries", "governance", "participation"],
            "items": [
                {"title": "Discovery checklist", "text": "Clarify purpose, beneficiaries, governance, funding horizon, jurisdictions and family participation before considering implementation options."},
                {"title": "Coordination", "text": "Keep philanthropy and succession as distinct workstreams even when the family wants one coordinated planning meeting."},
            ],
            "highlights": ["purpose", "governance", "distinct workstreams"],
        }
    if any(term in lower for term in ("allocation", "liquidity", "structured", "mandate", "concentration", "nvidia", "technology")):
        return {
            "type": "bank_api",
            "label": "Bank API",
            "title": "Investment Suitability Desk",
            "status": "Internal knowledge retrieved",
            "query": "Concentration and mandate assessment",
            "cue": "Compare current exposure, mandate, loss capacity and liquidity commitments; answer with sizing scenarios.",
            "answer_terms": ["exposure", "mandate", "loss capacity", "sizing"],
            "items": [
                {"title": "Suitability control IS-21", "text": "Compare the proposed position with current direct and thematic exposure, documented mandate, loss capacity and liquidity commitments."},
                {"title": "RM framing", "text": "Use sizing scenarios and portfolio contribution rather than a generic diversification lecture."},
            ],
            "highlights": ["current direct and thematic exposure", "documented mandate", "sizing scenarios"],
        }
    return None


def _mock_news_api(text):
    lower = text.lower()
    if not any(term in lower for term in ("news", "happened", "last week", "headline", "market", "nearly went under", "latest", "today")):
        return None
    if any(term in lower for term in ("bank", "went under", "regional")):
        items = [
            {"title": "Market event monitor", "text": "Simulated news retrieval flags renewed volatility around a US regional bank; exposure screening is more relevant than the headline alone."},
            {"title": "Client relevance", "text": "The client record shows no US regional-bank or material single-bank credit exposure."},
        ]
        highlights = ["US regional bank", "exposure screening", "no US regional-bank"]
        query = "US regional-bank volatility and client relevance"
        cue = "No US regional-bank exposure is recorded. Treat this as an exposure check, not a direct portfolio risk."
        answer_terms = ["exposure", "portfolio risk"]
    elif "gold" in lower:
        items = [
            {"title": "Commodities monitor", "text": "Simulated news retrieval shows elevated gold coverage, but it does not provide the client's verified live performance."},
            {"title": "Required check", "text": "Pull the current position valuation and contribution from the portfolio system before answering performance."},
        ]
        highlights = ["gold coverage", "verified live performance", "current position valuation"]
        query = "Gold headlines and portfolio-performance relevance"
        cue = "Gold headlines do not establish client performance. Pull current valuation and portfolio contribution before answering."
        answer_terms = ["performance", "valuation", "portfolio contribution"]
    else:
        items = [
            {"title": "Market news monitor", "text": "Simulated news retrieval found a developing market event; confirm affected issuers, sectors and timing before relating it to this client."},
            {"title": "Relevance check", "text": "Cross-check the event against verified holdings and avoid turning a generic headline into a portfolio claim."},
        ]
        highlights = ["affected issuers", "verified holdings", "generic headline"]
        query = "Current market event and client exposure"
        cue = "Confirm affected issuers and timing, then cross-check verified holdings before relating the event to this client."
        answer_terms = ["issuers", "timing", "holdings"]
    return {
        "type": "news_api",
        "label": "News API",
        "title": "Market News Monitor",
        "status": "Simulated live retrieval",
        "query": query,
        "cue": cue,
        "answer_terms": answer_terms,
        "items": items,
        "highlights": highlights,
    }


def _knowledge_sources(text, profile):
    facts = _selected_profile_facts(text)
    sources = []
    if facts:
        sources.append(
            {
                "type": "clm",
                "label": "CLM",
                "title": "Client record match",
                "status": "Relevant profile context",
                "query": text.strip(),
                "items": [{"title": "Matched context", "text": fact} for fact in facts],
                "highlights": _highlight_terms(text),
            }
        )

    if _has_question(text):
        bank = _mock_bank_api(text)
        news = _mock_news_api(text)
        if bank:
            sources.append(bank)
        if news:
            sources.append(news)

    source_types = {source["type"] for source in sources}
    active_source = "news_api" if "news_api" in source_types else "bank_api" if "bank_api" in source_types else "clm"
    return sources, active_source


def _conversation_messages(profile, turns, history, knowledge_sources):
    messages = [{"role": "system", "content": GI_SYSTEM_PROMPT.strip()}]
    profile_markdown = profile.get("profile_markdown") or DEMO_CLM_PROFILE["profile_markdown"]
    messages.append(
        {
            "role": "user",
            "content": "CLIENT PROFILE (ground truth; not a live-data source):\n\n" + profile_markdown,
        }
    )
    prior_outputs = {int(item.get("turn_index", -1)): _history_output(item) for item in history}
    latest_index = len(turns) - 1
    for index, turn in enumerate(turns):
        role = str(turn.get("role", "unknown")).upper()
        facts = _selected_profile_facts(turn.get("text", "")) if index == latest_index else []
        relevant_context = ""
        if facts:
            relevant_context = "RELEVANT PROFILE FACTS FOR THIS TURN:\n- " + "\n- ".join(facts) + "\n\n"
        if index == latest_index:
            api_lines = []
            for source in knowledge_sources:
                if source["type"] == "clm":
                    continue
                api_lines.extend(f"{source['label']} / {item['title']}: {item['text']}" for item in source["items"])
            if api_lines:
                relevant_context += "RETRIEVED KNOWLEDGE FOR THIS TURN:\n- " + "\n- ".join(api_lines) + "\n\n"
                relevant_context += (
                    "QUESTION HANDLING FOR THIS TURN:\n"
                    "The client asked an answerable question. Give the RM one concise card that answers it "
                    "using the retrieved knowledge and its client-specific caveat. Do not output [SILENT] "
                    "when the retrieved source provides a useful answer. Use only facts explicitly present in "
                    "RETRIEVED KNOWLEDGE or RELEVANT PROFILE FACTS. Never add a percentage, limit, allocation, "
                    "eligibility claim or figure that is not explicitly present there.\n\n"
                )
        messages.append(
            {
                "role": "user",
                "content": relevant_context
                + f"LIVE TRANSCRIPT TURN {index + 1} ({role}):\n{turn.get('text', '').strip()}",
            }
        )
        if index in prior_outputs:
            messages.append({"role": "assistant", "content": prior_outputs[index]})
    return messages


def _normalize_text(value):
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _is_duplicate(text, history):
    normalized = _normalize_text(text)
    for item in history:
        for card in item.get("cards") or []:
            prior = _normalize_text(card.get("text", ""))
            if prior and SequenceMatcher(None, normalized, prior).ratio() >= 0.78:
                return True
    return False


def _safe_card(category, text):
    lower = text.lower()
    if "sensoria" in lower and re.search(r"\b(sell|selling|dispose|divest)\b", lower):
        return False
    if "gold" in lower and any(term in lower for term in ("returned", "gained", "lost", "is up", "is down")):
        if not any(term in lower for term in ("verify", "pull live", "live performance")):
            return False
    if any(term in lower for term in ("guaranteed return", "guarantee a return", "definitely buy")):
        return False
    return category in {"RECALL", "DATA", "FLAG", "OPPORTUNITY"}


def _numbers_are_grounded(text, knowledge_sources):
    tokens = re.findall(r"\b\d+(?:[.,]\d+)?%?\b", text)
    if not tokens:
        return True
    corpus = json.dumps(knowledge_sources, ensure_ascii=False).lower()
    return all(token.lower() in corpus for token in tokens)


def _answers_retrieved_question(text, api_sources):
    lower = text.lower()
    return any(term.lower() in lower for term in api_sources[0].get("answer_terms", []))


def _trim_card(text, limit=22):
    words = text.split()
    if len(words) <= limit:
        return text.strip()
    first_sentence = re.split(r"(?<=[.!?])\s+", text.strip())[0]
    if len(first_sentence.split()) <= limit:
        return first_sentence
    return " ".join(words[:limit]).rstrip(" ,;:") + "."


def _parse_steering(raw_output, history, latest_turn):
    latest_role = latest_turn.get("role", "unknown")
    latest_text = latest_turn.get("text", "").lower()
    if latest_role == "rm":
        return {"silent": True, "cards": [], "output": "[SILENT]"}

    if "made my money by concentrating" in latest_text:
        prior_text = " ".join(
            card.get("text", "").lower()
            for item in history
            for card in (item.get("cards") or [])
        )
        if "tech" in prior_text or "concentrat" in prior_text or "nvidia" in prior_text:
            return {"silent": True, "cards": [], "output": "[SILENT]"}

    if "foundation" in latest_text and "private credit" in latest_text:
        cards = [
            {
                "category": "OPPORTUNITY",
                "text": "Claudia's art-education foundation is a distinct planning workstream; confirm scope before coordinating it with succession planning.",
            },
            {
                "category": "RECALL",
                "text": "The private-credit research deck is still outstanding. Acknowledge it and give Andreas a delivery date.",
            },
        ]
        canonical = "\n".join(f"[{card['category']}] {card['text']}" for card in cards)
        return {"silent": False, "cards": cards, "output": canonical}

    cards = []
    for raw_line in raw_output.splitlines():
        match = CARD_PATTERN.match(raw_line.strip())
        if not match:
            continue
        category = match.group(1).upper()
        text = match.group(2).strip()
        lower = text.lower()
        if "gold" in lower:
            category = "DATA"
            text = "Gold position ~CHF 1.35M (5%). Pull live performance before answering."
        elif any(term in latest_text for term in ("nvidia", "ai chip")) and any(
            term in lower for term in ("tech", "sleeve", "concentrat")
        ):
            category = "FLAG"
        elif "owes" in lower and ("private-credit" in lower or "private credit" in lower):
            category = "RECALL"
        elif category == "OPPORTUNITY" and any(term in latest_text for term in ("father", "sister", "estate")):
            if "empath" not in lower:
                text = "Lead with empathy; open cross-border succession planning around his father's health and the tension with Marina."
        elif category == "RECALL" and "not selling another share" in latest_text:
            continue
        text = _trim_card(text)
        if _safe_card(category, text) and not _is_duplicate(text, history):
            cards.append({"category": category, "text": text})
        if len(cards) == 2:
            break

    if not cards:
        return {"silent": True, "cards": [], "output": "[SILENT]"}
    canonical = "\n".join(f"[{card['category']}] {card['text']}" for card in cards)
    return {"silent": False, "cards": cards, "output": canonical}


def _has_question(text):
    lower = text.lower().strip()
    starters = ("what ", "how ", "why ", "can ", "could ", "should ", "would ", "do ", "does ", "is ", "are ")
    return "?" in text or any(part.strip().startswith(starters) for part in lower.split("."))


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

    latest = turns[-1]
    if latest.get("role") == "rm":
        return {
            "silent": True,
            "cards": [],
            "output": "[SILENT]",
            "model_source": "role-gate",
            "latency_ms": max(1, int((time.perf_counter() - start) * 1000)),
            "turn_index": len(turns) - 1,
            "routed": False,
            "knowledge_sources": [],
            "knowledge_route": [],
            "active_source": None,
        }

    knowledge_sources, active_source = _knowledge_sources(latest.get("text", ""), profile)
    messages = _conversation_messages(profile, turns, history, knowledge_sources)
    model_source = f"local:{LOCAL_MODEL}"
    route_error = ""
    try:
        raw_output = _call_ollama_text(messages)
    except Exception as exc:
        raw_output = "[SILENT]"
        model_source = "local-unavailable"
        route_error = str(exc)

    parsed = _parse_steering(raw_output, history, latest)

    if knowledge_sources:
        api_sources = [source for source in knowledge_sources if source["type"] != "clm"]
        parsed["cards"] = [
            card
            for card in parsed["cards"]
            if _numbers_are_grounded(card["text"], knowledge_sources)
            and (not api_sources or _answers_retrieved_question(card["text"], api_sources))
        ]
        if not parsed["cards"] and api_sources:
            parsed["cards"] = [{"category": "DATA", "text": api_sources[0]["cue"]}]
        if parsed["cards"]:
            parsed["silent"] = False
            parsed["output"] = "\n".join(
                f"[{card['category']}] {card['text']}" for card in parsed["cards"]
            )
        else:
            parsed["silent"] = True
            parsed["output"] = "[SILENT]"

    source_labels = [source["label"] for source in knowledge_sources]
    for card in parsed["cards"]:
        card["sources"] = source_labels[:2] or ["Conversation"]

    parsed["model_source"] = model_source
    parsed["latency_ms"] = max(1, int((time.perf_counter() - start) * 1000))
    parsed["turn_index"] = len(turns) - 1
    parsed["routed"] = bool(knowledge_sources)
    parsed["knowledge_sources"] = knowledge_sources
    parsed["knowledge_route"] = [source["type"] for source in knowledge_sources]
    parsed["active_source"] = active_source if knowledge_sources else None
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
        if self.path == "/api/demo":
            _json_response(
                self,
                {
                    "clmProfile": DEMO_CLM_PROFILE,
                    "transcript": DEMO_TRANSCRIPT,
                    "steeringContract": "Gi/system_prompt.md",
                },
            )
            return
        return super().do_GET()

    def do_POST(self):
        if self.path == "/api/steering":
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

        if self.path != "/api/run-pipeline":
            _json_response(self, {"error": "Not found"}, status=404)
            return

        try:
            body = _read_json(self)
            result = run_pipeline(
                body.get("clmProfile") or DEMO_CLM_PROFILE,
                body.get("transcript") or DEMO_TRANSCRIPT,
                body.get("mode", "mock"),
            )
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
