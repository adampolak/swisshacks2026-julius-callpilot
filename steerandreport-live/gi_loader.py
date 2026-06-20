import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent
GI_DIR = ROOT / "Gi"


def _read(name):
    return (GI_DIR / name).read_text(encoding="utf-8")


def _profile_value(markdown, label, default=""):
    match = re.search(rf"^- \*\*{re.escape(label)}:\*\*\s*(.+)$", markdown, re.MULTILINE)
    return match.group(1).strip() if match else default


def _section_numbered_items(markdown, heading):
    match = re.search(
        rf"^##\s+{re.escape(heading)}\s*$([\s\S]*?)(?=^##\s+|\Z)",
        markdown,
        re.MULTILINE,
    )
    if not match:
        return []
    return [
        item.replace("**", "").replace("*", "").replace("—", "-").strip()
        for item in re.findall(r"^\d+\.\s+(.+)$", match.group(1), re.MULTILINE)
    ]


def parse_transcript(markdown):
    turns = []
    current = None
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        match = re.match(r"^\*\*(RM|Client):\*\*\s*(.*)$", line)
        if match:
            if current:
                current["text"] = " ".join(current.pop("parts")).strip()
                turns.append(current)
            role = match.group(1).lower()
            current = {
                "role": role,
                "speaker": "Relationship Manager" if role == "rm" else "Client",
                "parts": [match.group(2)],
            }
        elif current and line and not line.startswith("---"):
            current["parts"].append(line)

    if current:
        current["text"] = " ".join(current.pop("parts")).strip()
        turns.append(current)

    for index, turn in enumerate(turns):
        turn["turn_index"] = index
    return turns


def build_profile(markdown):
    total_match = re.search(r"Wealth overview \(≈\s*([^\)]+) total\)", markdown)
    risk_match = re.search(r"risk score (\d+ of \d+)", markdown, re.IGNORECASE)
    managed_match = re.search(r"Liquid assets managed by Julius Baer \|\s*([^|]+)", markdown)
    cash_match = re.search(r"\| Cash & equivalents \|\s*([^|]+)", markdown)
    gold_match = re.search(r"Gold position ≈\s*([^\*]+)", markdown)

    rm_value = _profile_value(markdown, "Relationship manager", "Nicole Brandt")
    relationship_manager = re.split(r"\s*\(RM\)|,", rm_value, maxsplit=1)[0].strip()

    return {
        "client_id": "GI-ANDREAS-VOGEL",
        "primary_client": "Andreas Vogel",
        "relationship_manager": relationship_manager,
        "segment": _profile_value(markdown, "Segment", "HNW / UHNW"),
        "jurisdiction": "Switzerland / Italy",
        "languages": ["Swiss-German", "German", "English", "Italian"],
        "total_wealth": total_match.group(1).strip() if total_match else "CHF 42M",
        "managed_assets": managed_match.group(1).strip() if managed_match else "CHF 27M",
        "risk_profile": risk_match.group(1) if risk_match else "3 of 5",
        "cash_weight": cash_match.group(1).strip() if cash_match else "27%",
        "gold_position": gold_match.group(1).strip() if gold_match else "CHF 1.35M (5%)",
        "portfolio_snapshot": [
            "CHF 3M direct tech and semiconductor sleeve",
            "25% retained Sensoria stake, approximately CHF 9M",
            "Approximately CHF 7M undeployed cash, partly in USD",
            "No US regional-bank or material single-bank credit exposure",
        ],
        "family_context": [
            "Sophie, 24, is in London; the family is considering helping with a flat",
            "Luca, 19, wants funding for a technology start-up",
            "Father, 82, is unwell in Ticino; estate tension with sister Marina",
            "Claudia is considering an art-education foundation",
        ],
        "open_commitments": _section_numbered_items(
            markdown, "7. Open items from the last meeting (≈ 2 months ago)"
        ),
        "boundaries": [
            "Never suggest directly selling the retained Sensoria stake",
            "Do not invent live prices, performance, news, or exposures",
            "Treat concentrated technology ideas against the balanced mandate",
            "Lead with empathy on family and succession matters",
        ],
        "profile_markdown": markdown,
    }


def load_gi_bundle():
    profile_markdown = _read("customer_profile.md")
    transcript_markdown = _read("conversation_transcript.md")
    return {
        "profile": build_profile(profile_markdown),
        "profile_markdown": profile_markdown,
        "transcript": parse_transcript(transcript_markdown),
        "transcript_markdown": transcript_markdown,
        "system_prompt": _read("system_prompt.md"),
        "evaluation_key": _read("evaluation_key.md"),
    }
