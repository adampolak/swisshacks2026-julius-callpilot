# Private Banking Conversation Intelligence Prototype

Local prototype for a three-round CLM conversation pipeline using a DeepSeek API
key stored in `deepseek_api_key.txt`.

## Run

```powershell
cd steerandreport
python server.py
```

Open `http://127.0.0.1:8000`.

## Pipeline

The prototype uses two core prompt sources:

1. `Gi/system_prompt.md` for turn-based local-model steering.
2. `REPORT_PROMPT` for the full call report and final action-pack refinement.

The three generation rounds are:

1. Turn-based real-time steering. The transcript is visually unattributed, while
   hidden RM/client turn metadata is retained for handled-point detection.
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
Sensoria no-sale boundary. If `DeepSeek route` is enabled, complex business
questions are escalated while preserving the same output contract.

## Gi Source Data

The live prototype reads `Gi/customer_profile.md`, `Gi/conversation_transcript.md`,
and `Gi/system_prompt.md` at startup. `Gi/evaluation_key.md` is reserved for
evaluation and is never sent to either model.

## Report

`Open Report` opens `/report.html`, a separate page for the slower post-call
report flow. This keeps the live steering workstation uncluttered.

You can also run the pipeline once in the terminal:

```powershell
python server.py --once --mode mock
python server.py --once --mode deepseek
```
