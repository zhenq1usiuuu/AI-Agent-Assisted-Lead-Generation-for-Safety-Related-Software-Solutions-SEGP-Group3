# Remediation delivery and verification

Date: 2026-10-25. This report supersedes the implementation limitations described in the earlier EVALUATION.md, while retaining that report as historical evidence.

## Implemented changes

1. Public source adapters: a new inspect_public_source tool inspects actual SIRIM GET forms and DOSH embedded/download/pagination links. No guessed endpoints, hidden/session fields, POST or cross-host automatic access. Dashboard shells remain context only. Known source restrictions are preserved on directly supplied URL variants.
2. Bounded multi-layer retrieval: depth 0-3, default 20 total pages (maximum 32), default three outgoing links per page, roots scheduled before descendants, cycle/tracking normalization, and one transient transport retry. Source terms/robots, pinned public IPs and exact-host boundaries remain enforced.
3. Full retained-text chunking: text cap increased from 18,000 to 180,000 characters, explicit truncation, contiguous chunks, neighboring context, page spans, and bounded table context. No six-company output cap. Incomplete/output-truncated chunks subdivide to a bounded depth; unresolved work remains visible. Results are deduplicated and ordered by source position.
4. Evidence checking: exact citations and company checks remain; discovery-only consistency and unsupported explicit ISO/year/currency values are flagged. Risk-sensitive cases/certification/OCR/flagged signals receive optional five-item semantic review batches. Uncertain/unsupported results are preserved and excluded from direct scoring eligibility. No lead score is calculated.
5. PDF/OCR: pdfplumber text/tables/page coordinates, bounded optional local RapidOCR, OCR confidence and missing-page warnings. A verified FTSE sidebar layout adapter separates columns before extraction to avoid splicing index dates into constituent rows. Complex table associations remain unverified, not magically guaranteed.
6. Deduplication: normalized-body exact matches reuse extraction only within matching source class/scope, retaining alternate sources. Near duplicates remain separate with candidate links, so differing names, dates and amounts are not silently merged.
7. Normal CLI budget and checkpoints: per-run $1 default cap, configurable up to $10, checked before every paid attempt, including retry attempts. Known usage settles conservatively; unknown attempts retain full reservations. Atomic checkpoints, OS run locks and --resume preserve completed work. Page/chunk/budget limits can be raised cumulatively on resume without repeating completed model calls.

The default max output is 4,000 tokens per live model call. Local OCR/cleaning/link discovery/chunking do not call Kimi. Risk review remains inside Extract signals, not the later scoring/reasoning pipeline stages.

## Real source probes

[Connector observations](data/remediation-2026-10-25/connectors.json):

- SIRIM: robots returned HTTP 500; no page/form query was attempted past that block. The generic observed-GET adapter is tested with fixtures, but live SIRIM certification queries remain unverified and currently inaccessible.
- DOSH dashboard: the wrapper was retrieved. Its observed iframe was on another host, so no automatic access was made; no usable same-host public dataset target was found. The opaque dashboard data connector remains unresolved.

These external access/data limitations cannot be honestly marked fixed by adding parser code. The system now detects/reports them and can consume supported actual public targets when present, without pretending to retrieve records.

## Verification

- Offline suite: **56 passed** with one existing Starlette/httpx deprecation warning. Tests cover multi-depth cycles and fairness, full-text chunk coverage, 25 extracted records in source order, budget pause/resume, chunk-limit resume, exact/near duplicate handling, public GET/POST/cross-host boundaries, PDF page offsets, evidence rules, review routing, and repeated-quote offsets anchored to the actual target chunk/page. No Kimi charges for offline tests.
- Local OCR: a synthetic image-only PDF was generated solely as a test fixture. Its text was recovered locally with minimum line confidence 0.99155; page/method/coordinates were retained. This is a smoke test, not OCR accuracy measurement on arbitrary source scans. [OCR result](data/remediation-2026-10-25/ocr-result.json).
- Real saved ESG PDF replay: the full pipeline used the previously captured public PDF and paid Kimi, not a fresh download. At a $1 cap, the guard saved a checkpoint with seven completed chunks and remaining work. Resuming the same run at a $3 cumulative cap reused the saved plan/fetch and successful chunks; the run finished with **10 companies, 20 completed leaf chunks, zero remaining chunks and no execution errors**. Incomplete chunks had been subdivided. [Initial bounded result](data/remediation-2026-10-25/kimi-pdf-replay.json), [resumed result](data/remediation-2026-10-25/kimi-pdf-resumed.json).
- The replay exposed main/sidebar interleaving in FTSE PDF text. The final layout adapter removes that interleaving. After the final prompt/registry updates, a focused paid test of the entire top-10 table returned **10 accepted companies, zero rejected quotes, coverage_complete=true**. This final test is a table-subset extraction, not another entire-document run. [Final contract test](data/remediation-2026-10-25/final-table-contract.json).
- CLI help and new argument wiring were inspected. The terminal remains category-driven; --budget-usd, --max-depth, --links-per-page, --max-chunks, --semantic-review, --ocr, --source-query and --resume expose the controls. README and source notes were updated.

The replay used an earlier in-turn context revision. Final context changes intentionally invalidate that checkpoint for further resumption; its saved outputs remain reviewable evidence of the resume test. New runs use the final contract. No claim of statistical recall, real-world incident accuracy or fully operational blocked connectors is made.

## Additional Kimi usage

| Item | Total |
|---|---:|
| Paid calls in this implementation | 25 |
| Input tokens | 243,079 |
| Cached input tokens (subset) | 189,952 |
| Output tokens | 10,791 |
| Estimated additional cost | $0.378232 |
| Previous evaluation estimate | $0.287972 |
| Combined estimate | $0.666203 |

All added tests remain within the prior $10 authorization. Estimates use official international K3 default-cache rates and returned usage, not a provider invoice. Conservative guard commitments are deliberately larger than estimated actual charges. No additional paid tests are needed for delivery.

[Usage summary](data/remediation-2026-10-25/summary.json), [pipeline test ledger](data/runs/df01ae32-2a2c-4186-83e8-cb3cc1b43093/budget.json), [final-table ledger](data/remediation-2026-10-25/final-contract-budget.json).

## Remaining boundaries

Public POST/authenticated/CAPTCHA forms, cross-host opaque dashboards, JavaScript-only downloads and source access failures remain unsupported. The fixed byte/page/text caps can leave source coverage incomplete. Model completion flags cannot prove perfect recall; semantic review cannot prove truth. PDF table/OCR errors can still need review. Near duplicates are deliberately not automatically merged. Downstream company entity resolution, lead scoring, final reasoning and CRM integration remain outside this implementation.

> Handoff note: linked data/ artifacts are local historical records and are not included in this repository.
