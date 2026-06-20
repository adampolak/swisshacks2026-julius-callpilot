# Design QA

- Source visual truth: `output/playwright/reference-julius-baer-financing.png`
- Live implementation: `output/playwright/premium-live-v1-1366x768.png`
- Report implementation: `output/playwright/premium-report-v1-1366x768.png`
- Combined comparison: `output/playwright/premium-design-comparison.png`
- Viewport: 1366 x 768 for implementation captures; source shown at its native aspect ratio in the comparison board.
- State: populated RM/Client transcript, two active guidance cards, populated mock report.

## Full-view comparison evidence

The source and both implementation views share the same sampled warm-grey canvas (`#F1F2F2`), white content surfaces, centered deep-blue wordmark, restrained borders, square geometry, light optical weights, and generous negative space. The workstation intentionally uses a denser three-column composition because it is an operational screen rather than a marketing page.

## Focused region evidence

Focused regions were reviewed directly in the full-resolution live and report screenshots. Separate crops were not required because typography, header alignment, card edges, transcript labels, tabs, and report body copy remain legible at the captured resolution.

## Required fidelity surfaces

- Fonts and typography: Aptos/Segoe UI approximates the source's refined sans-serif body; Georgia provides a restrained serif wordmark. Hierarchy, line height, wrapping, and optical weights are consistent across both pages.
- Spacing and layout rhythm: the 14px operational grid and 24-44px report rhythm preserve the source's clean alignment while keeping live information visible. All geometry is square and shadows are intentionally minimal.
- Colors and tokens: canvas, white surface, and deep navy match the sampled source palette. Gold, green, red, and client blue are limited to semantic details.
- Image quality and asset fidelity: the reference contains no content imagery required by this product workflow. No placeholder imagery, CSS illustrations, or emoji were introduced.
- Copy and content: existing Gi live guidance, client context, and report content remain intact and readable.

## Findings

No actionable P0, P1, or P2 mismatches remain.

## Patches made

- Replaced the dark theme with sampled warm-grey, white, and deep-blue tokens.
- Rebuilt both headers around a centered restrained wordmark.
- Converted live panels, guidance cards, dossier controls, report blocks, buttons, and states to the reference's square, low-elevation visual language.
- Verified desktop fixed-viewport behavior and mobile responsive stacking.

## Follow-up polish

- [P3] Replace the text-rendered wordmark with an approved official brand asset if one becomes available.
- [P3] A licensed corporate typeface could improve exact brand fidelity beyond the system-font approximation.

final result: passed
