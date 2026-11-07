# Signal extraction diagnosis and validation

Date: 2026-11-06

## Evidence from local live runs

- ef6486c9-7092-48c8-979e-72695e0a4ee1 and d29a1341-5282-4890-8f44-d4558af3776f: DOSH-only, zero eligible documents and zero extraction calls. Prosecution returned 404, the dashboard had no usable same-host records, and fatal cases reported a missing table. OCR and deeper crawling cannot repair these responses.
- a224b854-2f1e-4316-bff1-d055b9cf23ec: four accepted ESG discovery signals, budget_limit, eight completed chunks and twelve remaining. Saved PDF text had no quality warnings. The original sequential scheduler spent calls on introductory text before company rows.
- FMM newsroom had real article links mentioning haze/jerebu; the previous safety vocabulary did not select them. Configured depth was not the binding constraint because no matching outgoing URL was scheduled.
- Module 2 has a manufacturing/computer-vision safety rubric. ESG discovery-only records cannot legitimately be converted into verified demand merely to pass scoring.

## Changes

- Prioritize company, numeric table and incident spans before background spans; preserve all spans and offsets for coverage/resume.
- Increase the default line cap from 12 to 24, retaining the 4,500-character cap and existing split-on-incomplete behavior.
- Add bounded workplace-health/haze/jerebu vocabulary to link discovery without changing robots, hosts, depth or page caps.
- Add signal_diagnostics describing source, budget, retrieval, text and discovery-only limitations.
- Keep current budget rates/caps and evidence/scoring gates unchanged. Existing checkpoints keep old chunk boundaries; start a new run to use the new chunk size.

## Validation

110 offline tests passed (one existing Starlette/httpx deprecation warning).
A single paid Kimi extraction on the saved real FTSE text selected the full company table first: 10 accepted signals, zero rejected citations, coverage_complete=true for that chunk. Full PDF chunk count decreased from 20 to 10. Estimated cost: USD 0.063804; provider billing is authoritative.
The same saved FMM links now produce three candidate follow-up URLs. Their live contents and company specificity have not been verified in this validation.

Local artifacts (ignored by Git): data/recovery-validation.json and data/recovery-budget.json.
This is a saved-text replay, not a new end-to-end live crawl, and does not prove that DOSH/SIRIM access problems are fixed or that every category produces a qualified lead.
