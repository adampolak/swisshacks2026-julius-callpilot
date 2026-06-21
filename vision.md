# A Real-Time Co-Pilot for Private Bankers

**An AI assistant that sits beside a Relationship Manager during a live client call, surfacing the right fact at the right moment to empower the RM with quick, context-aware, information retrivial and then writing the post-call report.**

## The problem

A private-banking relationship manager (RM) carries an enormous mental load on every client call. They have to:

- **Remember the person** — the client's family, their last conversation, the promise made six weeks ago, the daughter's dance performance last Tuesday.
- **Stay inside the mandate** — catch the moment a client drifts toward something that quietly conflicts with their agreed risk profile.
- **Never get a fact wrong** — no invented prices, no hallucinated performance, no off-the-cuff numbers.
- **And do all of this while holding a warm, human conversation.**

Today this lives entirely in the RM's head and in documents they don't have time to open mid-call. Good RMs are extraordinary at it; everyone has off days; and the institutional knowledge walks out the door when they do.

## What we built

Julius call-pilot is a co-pilot with two jobs, matching the two halves of a client relationship:

### 1. Steer — live, during the call

As the conversation is transcribed in real time, the assistant watches each turn and, *only when it genuinely helps*, drops a short, glanceable **card** onto the RM's screen:

- **Recall** — "Family: wife Sylvie, daughters Camille and Élodie. Decisions are joint with Sylvie."
- **Context** — a compressed, qualitative read on a country or market the client just mentioned.
- **Flag** — a quiet suitability warning when a client's idea conflicts with their documented mandate.
- **Data / Opportunity** — a grounded figure or an opening worth following up.

Crucially, it is **silent by default**. It does not narrate the call, it does not coach, it does not read lines for the RM to repeat, and it never does sentiment analysis. It speaks only when it has something factual and useful — which is what keeps an RM willing to glance at it.

### 2. Report — after the call

Once the call ends, a second, more powerful pass reads the full transcript against the client profile and produces an **executive-grade conversation report**: what the client wants, unresolved questions, missed prior commitments, suitability watch-outs, recommended follow-ups, and a polished draft note in the RM's own voice — ready to send to the internal system.

## The principles that shape it

These constraints are the product, not an afterthought:

- **Privacy first.** The live, in-call steering runs on a **small local model** — no client conversation has to leave the bank's own machine to get real-time help. The heavier report step can use a larger model offline.
- **Grounded, never inventive.** The assistant is given exactly two sources of truth — the client profile and a macroeconomic snapshot — and is forbidden from inventing live prices, performance, news, or exposures. If it isn't in front of it, it doesn't say it.
- **The human stays in charge.** It surfaces facts, not advice. Once the RM has handled a point, the assistant stays quiet rather than second-guessing them.
- **Built around real banking guardrails.** Hard boundaries (e.g. a client's emotionally-held position that must never be touched) are honoured at every step.

## How it works, at a glance

```
 Live call audio ──▶ Real-time transcription ──▶ Local steering model ──▶ Guidance cards
                                                  (grounded in client                │
                                                   profile + macro data)             ▼
                                                                            RM's live screen
 End of call ───────────────────────────────────▶ Report model ──▶ Executive report + draft note
```

The RM's screen is a single workbench: the **live transcript** on one side, the **guidance cards** in the middle, and the **client dossier** (profile, plus Bank-API and News-API context) on the other — with the active source highlighted as it's used.

## Why it matters

Private banking is built on relationships, judgment, and trust — exactly the things you can't fully automate. Steer & Report doesn't try to replace the banker. It gives them a **perfect memory and a tireless compliance conscience**, quietly, in the moment — so the human can stay fully present with the person across the table, and never drop the detail that makes a client feel genuinely known.
