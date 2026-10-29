# Evaluation and optimization report

Date: 2026-10-25. Authorized Kimi budget: $10 USD. All project changes and reports are in English; original evidence remains verbatim.

## Outcome

Offline baseline: 38 passed. Final offline suite: 45 passed, with one existing Starlette/httpx deprecation warning. No paid requests were used by these tests.

Paid work: four targeted stage scenarios (ESG, FMM, DOSH, SIRIM), each before and after changes, followed by a fresh ALL pipeline run and one focused MEF pipeline run. This was a diagnostic evaluation, not 30 statistical repetitions or a comprehensive accuracy benchmark. All 20 API attempts returned successful responses; there were no retry reservations or incomplete model responses in the ledger.

| Usage | Total |
|---|---:|
| Input tokens | 156,741 |
| Cached input tokens (subset of input) | 109,056 |
| Output tokens | 7,480 |
| Total input + output | 164,221 |
| Estimated Kimi charge | $0.2879718 |
| Conservative guard reservations | $5.992314 |
| Authorized budget | $10.00 |

Estimate = ((156741 - 109056) * 3 + 109056 * 0.30 + 7480 * 15) / 1000000. Output includes 416 reported reasoning tokens; they are not added again. Assumes the configured official international kimi-k3 endpoint and default five-minute cache pricing. Cache-write tokens are part of the input total, not charged twice in this formula. Provider invoices remain authoritative. Reservations are deliberately pessimistic safety allowances, not money actually spent. No further paid calls were made after the final MEF test. Ordinary CLI invocations do not automatically use this audit ledger.

Official rate references: [Kimi K3 announcement](https://forum.moonshot.ai/t/kimi-k3-is-here-our-most-capable-model/480), [cache pricing](https://www.kimi.ai/academy/best-practices-for-context-caching).

## Sequence and controls

1. Ran the existing offline suite before changes.
2. Attempted all 15 non-manual registry evidence sources using the real PublicFetcher; the sixteenth source is a manual dashboard. Existing DNS, robots, rate and access checks were retained. Saved successful raw responses and baseline cleaned text locally.
3. Ran seven paid baseline stage calls: four Receive prompt calls and three Extract signals calls on real saved PDF/HTML evidence. SIRIM had no retrievable evidence and received no extraction call. The FMM fish-inspection article was fetched from a real discovered link as a negative example.
4. Compared cleaners and discovery on identical snapshots, then updated tools and context. Ran six optimized stage calls: four receive and two extraction calls. Missing DOSH records were skipped locally. PDF input was unchanged across the ESG context comparison.
5. Ran a fresh ALL pipeline with a 19-attempt budget (15 actual attempts, no relevant links selected by that plan). Then added bounded workplace-safety aliases after observing that OSH-only headlines were missed, and validated that change in a focused, fresh MEF run (five actual attempts). The ALL result predates this final alias-only change; it was not needlessly rerun.
6. Ran the final 45-test offline suite and independently checked all six ALL-run company quotes, offsets and discovery-only labels.

## Tool findings and changes

| Finding | Before | After on the same input |
|---|---|---|
| DOSH hostname keyword leakage | OSH matched dosh.gov.my and selected privacy/navigation URLs | Score label and decoded path only; boundary matching; ignore hostname/query and navigation |
| FMM irrelevant inspection link | Fish-export quarantine article selected | Excluded unless its label/path explicitly indicates workplace safety |
| FMM newsroom boilerplate | 14,562 characters, first 100 links only | 11,326 characters, 80 unique content links; calendar/footer removed |
| FMM publications | 2,836 characters | 1,134 characters |
| FMM BIA detail | 2,410 characters, article header removed | 953 characters, article header retained; no invented PDF URL |
| MEF ASP.NET wrapper form | Entire public body removed, zero characters | 6,918 characters retained; controls removed without deleting evidence |
| DOSH fatal-case shell | 902 characters including boilerplate; paid extraction needed to abstain | 36-character missing-table message; quality warning and no extraction call |
| Link cap and schema | Relevant links after first 100 lost; tool limit still 8 despite CLI support for 32 | Up to 1,000 unique links with truncation metadata; tool schema and runtime limit both 32 |
| Vocabulary mismatch | Workplace-safety plan could miss OSH headlines | Bounded OSH/HSE and Malay occupational-safety aliases; regression and real MEF validation |

The link-cap fix was also verified with a deterministic fixture placing a relevant link after 120 unrelated links. The current captured FMM newsroom does not contain a relevant workplace-safety article link; the changes do not pretend otherwise. Some MEF event pages concern the same event with different URLs/content; semantic event deduplication remains future work.

## Context and output findings

Baseline ESG extraction returned no signals because it interpreted every task as requiring proven workflow needs. The user's category workflow also includes candidate discovery. The extractor now receives the original request, and both the static system rules and stage instructions explicitly permit discovery-only ESG/certification facts without inventing workflow needs.

On the unchanged PDF, optimized extraction returned six named candidates, all with exact quotes, source-stated sectors, null buyer roles, research_more, and explicit no-direct-workflow-evidence flags. SD Guthrie retained Food Producers rather than an invented plantation sector. The validator adds evidence_level=discovery_only to ESG/certification signals. Other signals use requires_semantic_review, which does not certify a direct safety need. Source provenance now includes both the original stable request URL and the resolved PDF URL.

Baseline abstention explanations sometimes asserted that other planned sources were not retrieved or inaccessible even though the extraction call only saw one document. Context now limits these explanations to the supplied document and requires execution evidence for claims about other fetches. Registry observations are explicitly historical.

The fish-export article returned zero signals before and after optimization. The DOSH missing-table input was correctly rejected by the baseline model and now avoids that paid extraction altogether. SIRIM planning preserved the form-connector limitation without fabricating certificate results.

## Fresh pipeline results

### ALL

Artifact: [ALL live result](data/evaluation-2026-10-25/optimized.all.live.json).

- Overall status: partial; 15 fetch attempts; 10 retained documents; 9 context-only and 1 eligible PDF.
- Six accepted ESG discovery candidates; zero rejected excerpts; all six checked against saved text offsets and company mentions.
- Candidates: Malayan Banking, Tenaga Nasional, Press Metal Aluminium Holdings, Gamuda, IHH Healthcare, SD Guthrie.
- Seven recorded issues: unavailable DOSH dashboard connector; DOSH prosecution HTTP 404; fatal-case Table not found; SIRIM robots HTTP 500; Bursa robots HTTP 403; MBAM and MARGMA HTTP 500.
- No company was identified as having a demonstrated current safety-software need. No contacts, scores or CRM writes were generated.

### Focused MEF after the alias fix

Artifact: [MEF live result](data/evaluation-2026-10-25/optimized.mef.live.json).

- Overall status: completed; five fetch attempts and five documents, no errors.
- One discovery homepage and four eligible public event/article pages.
- Zero signals: three training-event pages and one general workplace-mental-health conference page lacked company-level workflow evidence. All four abstentions were appropriate on manual inspection of the saved content.

The six-company ESG subset differed between the isolated ESG and ALL prompts (five companies overlapped). This is not a complete constituent extraction: the model has a six-signal cap and no deterministic company-ranking stage. Do not treat these results as stable lead ranking or statistical recall/precision evidence.

## Artifacts and reproducibility

- [Budget ledger](data/evaluation-2026-10-25/budget.json): reservation and returned usage for every API attempt.
- [Machine-readable summary](data/evaluation-2026-10-25/summary.json).
- [Initial fetch audit](data/evaluation-2026-10-25/fetch-baseline.json).
- [Fixed-snapshot tool comparison](data/evaluation-2026-10-25/tool-comparison.json): generated before the last synonym addition.
- Baseline and optimized category records: data/evaluation-2026-10-25/{baseline,optimized}.{ESG,FMM,DOSH,SIRIM}.json.
- Raw snapshots, cleaned snapshots and baseline source copies are in the same local, Git-ignored directory. They preserve actual capture dates/hashes and are not synthetic production evidence.
- Audit scripts in that directory are task artifacts, not the regular CLI. They use the shared serial budget ledger and 3,000 maximum output tokens per request. Do not run scripts concurrently or delete results to force paid reruns casually.

## Remaining limitations

DOSH historical case coverage and SIRIM certificate queries are still blocked by unavailable records/connectors. No access controls were bypassed. FMM's observed publication download button has no usable PDF anchor and is not executed. Text-PDF extraction still does not reconstruct arbitrary tables or perform OCR. This audit did not supply a labeled positive real-world safety-need dataset, so it establishes concrete regression fixes and appropriate abstention, not production lead-generation accuracy. Entity/event merging, deterministic candidate selection and next-stage scoring remain unimplemented.

> Handoff note: linked data/ artifacts are local historical records and are not included in this repository.
