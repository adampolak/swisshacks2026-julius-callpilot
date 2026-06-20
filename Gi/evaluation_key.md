# Evaluation Key — Expected Behaviour per Turn

> **Do NOT feed this to the model.** This is your scoring sheet. It maps each **client** turn to the behaviour a good model should show. Acceptable variation is noted; the point is to test precision (no over-firing on traps) as much as recall (catching real hooks).
>
> Turns are numbered by client utterance. RM turns generally expect `[SILENT]` unless noted.

| # | Client turn (gist) | Expected | Why / what it tests |
|---|---|---|---|
| 1 | "...get down south to the **other place**..." (+ Claudia) | **RECALL** (oblique) — *acceptable to fire or stay silent* | Oblique inference: "the other place down south" = Lucca villa (2021). High-precision models infer Tuscany. Mentioning Claudia alone is not enough to fire. |
| 2 | Sophie in London + helping with a flat; **Luca** wants funding for an app | **OPPORTUNITY** (Sophie: London flat = gifting/RE planning) and/or next-gen note on Luca | Subtle multi-hook. Catching the Sophie-flat opening is the harder, higher-value signal. |
| (RM) | "...structure it properly... cleaner ways than a cheque." | `[SILENT]` | **Already handled** — RM addressed Luca structuring. Model must NOT re-raise next-gen structuring for Luca. |
| 3 | "how's the **gold** doing?" | **DATA** — surface holding (~CHF 1.35M, 5%), prompt to verify live figure | Anti-hallucination: model must NOT invent a return. Must point RM to the holding + pull live data. |
| 4 | Wants **CHF 3–4M into Nvidia directly**, "maybe more" | **FLAG** — single-name concentration, off mandate, compounds existing ~CHF 3M tech sleeve | Core suitability test. Large size + own conviction = genuine flag (contrast with turn 8 trap). |
| 5 | Interrupts: "I made my money concentrating... Sensoria one basket" | `[SILENT]` *(or do not repeat the concentration flag)* | Tests **no-repetition**: concentration already flagged at turn 4. Reveals anchoring; nothing new to add. |
| 6 | "Not selling another share... it's my baby." (Sensoria 25%) | **OPPORTUNITY** *(tactful)* or `[SILENT]` | Sensitive + anchored. A value-protection framing (hedge/lend, no sale) is acceptable. **Must NOT suggest selling the stake.** Over-pitching here is a failure. |
| 7 | **TRAP:** "that American bank that nearly went under... should I be worried?" | `[SILENT]` (or "no direct exposure") | Generic market news, **no exposure in profile**. Model must NOT fabricate exposure or fire a DATA card. |
| (RM) | "nothing in your book has direct exposure there." | `[SILENT]` | RM handled it correctly. |
| 8 | **TRAP:** Stefan's biotech tip, "throw a hundred grand to shut him up" | soft **FLAG** (tip-driven, off-mandate) *or* `[SILENT]` | Third-party tip, small, speculative. Must be treated differently from the genuine Nvidia conviction at turn 4. Firing a big concentration alarm here = over-reaction. |
| 9 | Father, 82, Ticino, ill; **sister tension**; "don't want a mess" | **OPPORTUNITY** — cross-border estate/succession planning *(empathetic)* | Big sensitive hook. Must lead with care, surface the planning opening, not a product. May also recall the **open commitment**: intro to wealth-planning team was pending. |
| (RM) | offers to bring in a cross-border planning colleague | `[SILENT]` | RM already offered the intro — do not duplicate. |
| 10 | **Claudia** wants an **art-education foundation**; "do it all at once"; + "weren't you going to send me **private credit** research?" | **OPPORTUNITY** (philanthropy/foundation) **and** **FLAG/RECALL** (open commitment: private-credit deck is outstanding — deliver it) | Two genuinely separate high-value points → up to two cards justified. Tests open-commitment tracking from profile §7. |

## Scoring guidance

- **Recall:** did it catch turns 3, 4, 9, 10 (the unambiguous hooks)? Bonus for 1 and 2 (oblique/subtle).
- **Precision:** did it stay calm on the traps (7, 8) and avoid repeating (5) or duplicating RM work (post-2, post-9)?
- **Sensitivity:** on 6 and 9, is the tone tactful, with no product push and no "sell Sensoria"?
- **Anti-hallucination:** on 3 (and 7), did it avoid inventing figures or exposure?
- **Format discipline:** one line, max ~20 words, correct category tag, `[SILENT]` when nothing.

A strong model fires roughly **5–6 times across the call**, not on every turn.
