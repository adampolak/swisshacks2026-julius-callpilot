# Private Banking Conversation Intelligence Prototype

Local prototype for a three-round CLM conversation pipeline using a DeepSeek API
key stored in `deepseek_api_key.txt`.

## Run

```powershell
cd steerandreport
.\start.ps1
```

This starts `server.py`, opens `http://127.0.0.1:8000`, and stops the server
when the launcher is closed or interrupted.

From Explorer, you can also double-click `start.bat`.

Optional overrides:

```powershell
.\start.ps1 -Port 8010
.\start.ps1 -HostName 0.0.0.0 -Port 8000
.\start.ps1 -NoBrowser
```

## Pipeline

The prototype uses two core prompt sources:

1. `Gi/system_prompt.md` for turn-based local-model steering.
2. `REPORT_PROMPT` for the full call report and final action-pack refinement.

The three generation rounds are:

1. Turn-based real-time steering with explicit RM/client speaker labels and
   role-aware handled-point detection.
2. Full conversation report from the complete transcript and CLM profile.
3. Refined RM action pack using the draft report, transcript, and CLM profile.

The live transcript simulation renders speech-to-text one character at a time
with explicit RM and Client attribution. Steering appears only when a complete
turn contains a meaningful signal.

## Local Steering Model

The backend expects Ollama on `http://127.0.0.1:11434` and uses:

```powershell
ollama pull qwen2.5:1.5b
```

You can override the model:

```powershell
$env:LOCAL_STEERING_MODEL = "qwen2.5:1.5b"
```

The local model follows the Gi silence-by-default contract and emits either
`[SILENT]` or up to two `[RECALL|DATA|FLAG|OPPORTUNITY]` cards. A guardrail layer
validates card count, length, repetition, gold-performance claims, and the
Sensoria no-sale boundary.

Live DeepSeek routing is disabled. Answerable business questions retrieve
simulated internal knowledge from Bank API; event and market-news questions
retrieve simulated News API context. The local 1.5B model receives that context
before producing a card. Unsupported figures are rejected, and each card names
its CLM, Bank API, or News API source. The right dossier switches to the active
source and highlights the matched sentence while retaining the complete CLM
record in a dedicated scrollable view.

## Gi Source Data

The live prototype reads `Gi/customer_profile.md`, `Gi/conversation_transcript.md`,
and `Gi/system_prompt.md` at startup. `Gi/evaluation_key.md` is reserved for
evaluation and is never sent to either model.

## Report

`Open Report` opens `/report.html`, a separate page for the slower post-call
report flow. `Upload` represents sending the current report to the JB internal
database and is intentionally inert in this prototype. `Download PDF` opens the
browser's native A4 PDF save flow with a report-only print layout. DeepSeek
remains optional for post-call report generation and is never used by live steering.

You can also run the pipeline once in the terminal:

```powershell
python server.py --once --mode mock
python server.py --once --mode deepseek
```
