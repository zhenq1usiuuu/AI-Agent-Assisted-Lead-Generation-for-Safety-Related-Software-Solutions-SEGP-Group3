# Safety Signal Lab

Terminal-first Malaysian safety-software research. The implemented stages are Receive prompt (Kimi), Fetch + clean (local tools), Extract signals (Kimi plus evidence checks), Filter + score (deterministic rules), and LLM reasoning (a separate configurable model). Customer review and CRM writes are not executed.

## Setup

Python 3.11+ is required. If PyCharm already created the virtual environment, reuse it; do not recreate it.

```powershell
Set-Location '<path-to-your-cloned-repository>'
# Only if no environment exists:
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Set these values in `.env` and restart the CLI:

```dotenv
MOONSHOT_API_KEY=YOUR_API_KEY_HERE
MOONSHOT_BASE_URL=https://api.moonshot.ai/v1
MOONSHOT_MODEL=kimi-k3
MOONSHOT_REASONING_EFFORT=low
```

The built-in USD budget guard currently supports this official international K3 endpoint only. Other models/providers require a reviewed pricing configuration before use. Existing environment variables override `.env`. Keys never belong in prompts, shell arguments or saved results.

Optional local OCR for scanned PDFs:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-ocr.txt
```

OCR uses RapidOCR and ONNX Runtime locally. First use may download model weights; document images are not sent to an OCR API. The recognized text can subsequently be sent to Kimi for signal extraction. The project virtual environment used for the 2026-10-25 remediation already has these packages installed. See [RapidOCR usage](https://rapidai.github.io/RapidOCRDocs/main/en/install_usage/rapidocr/usage/).

## Interactive terminal

```powershell
.\.venv\Scripts\python.exe -m app.cli
# Or from an activated/PyCharm environment:
python -m app.cli
# Optional shortcut, if local scripts are permitted:
.\start.ps1
```

1. Choose live or demo (Enter defaults to live).
2. Choose category numbers or names; a full prompt is not needed.
3. Review the generated English prompt, selected URLs and processing limits.
4. At `URL:`, press Enter to skip optional additional pages. These URLs apply only to this run.
5. Confirm reviewed source terms/access permissions. This does not bypass robots rules.
6. Inspect JSON, answer any clarification questions, and optionally save a separate result file.

| Number | Keyword | Sources |
|---|---|---|
| 1 | DOSH | Prosecution records, public enforcement dashboard wrapper, fatal cases |
| 2 | SIRIM | Malaysian Certified directory and supported public query discovery |
| 3 | ESG | FTSE4Good Bursa factsheet and Bursa indices |
| 4 | FMM | Newsroom, publications, Business in Action Vol. 2/2025 |
| 5 | UNIONS | MTUC |
| 6 | ASSOCIATIONS | MEF, MBAM, CICM, MPMA, MARGMA |
| 7 | OSH | MSOSH |
| 0 | ALL | All 16 evidence-source entries; excludes reference-only sources |

Examples: `1`, `1,3,4`, `DOSH ESG FMM`, `ALL`. Names are case-insensitive. Empty/invalid choices are rejected before model calls. `quit` or `exit` at selection/clarification exits; Ctrl+C also exits. Demo remains a fixed synthetic fixture with no model/network use.

`prompts/research_template.txt` and `app/selection.py` assemble the task and source IDs. The runner enforces the selected seeds. Registry source restrictions still apply. Generated narratives are English; quotations and organisation names remain in the source language.

## One-shot examples

```powershell
python -m app.cli --categories ESG --permissions-confirmed --output esg-result.json
python -m app.cli --categories "DOSH,FMM" --max-pages 20 --max-depth 3 --budget-usd 1 --permissions-confirmed --output research-result.json
python -m app.cli --categories ALL --budget-usd 2 --max-chunks 40 --permissions-confirmed --output all-result.json
python -m app.cli --categories ESG --ocr --permissions-confirmed --output pdf-result.json
python -m app.cli --categories SIRIM --source-query "ISO 45001" --permissions-confirmed --output certificate-result.json
python -m app.cli --mode demo --categories ESG --output demo-result.json
python -m app.cli --list-sources
python -m app.cli --help
```

Custom prompts remain available through mutually exclusive `--prompt` or `--prompt-file` (UTF-8, including BOM). Only custom-prompt mode accepts repeated `--source` IDs. `--categories` and interactive selection reject conflicting `--source` arguments. Up to four `--url` arguments can supplement scope, with `--source-class company_owned` or `industry_procurement`.

```powershell
python -m app.cli --source fmm_newsroom --prompt "Review public FMM workplace-safety articles. Preserve direct company evidence and abstain from unsupported needs." --permissions-confirmed --output fmm-result.json
```

`--output` refuses existing files. In one-shot mode JSON goes to stdout; progress/errors go to stderr. Use saved JSON rather than copying wrapped terminal text. Exit codes: 0 completed/partial/clarification, 1 failed/error, 2 argument error, 130 cancelled. Always inspect JSON status/errors.

## Bounds, budget and resume

| Setting | Default | Range/meaning |
|---|---|---|
| `--max-pages` | 20 | 1-32 total page attempts, shared across selected sources |
| `--max-depth` | 3 | 0-3; seeds are depth 0 |
| `--links-per-page` | 3 | 1-5 outgoing targets per page |
| `--max-chunks` | 24 | 1-100 extraction attempts per invocation; includes split/retry work |
| `--budget-usd` | 1.00 | More than 0, at most 10; cumulative across a run and its resumes |
| `--semantic-review` | risk | risk-sensitive signals only, or off |
| `--ocr` | disabled | Opt-in local OCR for text-poor PDF pages |
| `--source-query` | none | Explicit term for an observed supported public SIRIM GET form |

Live runs automatically save `data/runs/<run_id>/checkpoint.json` and their budget ledger. This is a change from the earlier prototype that saved results only on request. Local checkpoints contain source text, prompt, outputs and unfinished work, but no API key. `--output` remains an optional export. Retain these files only as permitted by source conditions.

The run guard reserves a conservative byte-based input allowance and maximum output cost before each paid attempt. Returned complete usage settles that attempt at a conservative rate ($6/M input, $15/M output); uncertain failures keep their full reservation. Reported estimates also account for default cache hits. Reservations/committed bounds are not actual charges, and may stop a run early. The provider invoice is authoritative. The per-run lock prevents simultaneous resumes. Separate run IDs have separate caps; this is not an account-wide spending limit.

Budget, page, chunk or time limits preserve completed work. Resume using the run ID printed in JSON:

```powershell
python -m app.cli --resume YOUR_RUN_ID --budget-usd 2 --max-pages 32 --max-chunks 40 --permissions-confirmed --output resumed-result.json
```

Replace YOUR_RUN_ID with the actual UUID. The new budget is the total cap for that run, not an extra allowance. Source scope, OCR and review settings remain those of the saved run. Only budget/page/chunk limits can be adjusted on resume. Successful extraction chunks are not repeated. Page fetch failures are recorded rather than endlessly retried; start a new run to recheck them. Changing the static prefix invalidates old checkpoints to avoid mixing extraction contracts. Work is checkpointed after completed operations; an interrupted in-flight request can be uncertain and may need repeating, with its earlier reservation retained.

The runner stops scheduling near 14 minutes; CLI/API have an outer 15-minute timeout. Interrupted requests may return an error, but the last atomic checkpoint remains available. All saved pages are reused when resuming; a new run is needed for a fresh snapshot.

## Retrieval and source connectors

Seeds are visited before their discovered descendants, so one source cannot consume every slot before other selected roots. Links are selected using labels and paths, word/phrase boundaries and bounded English/Malay safety aliases. Hostnames and query strings are not keyword evidence. Depth, outgoing links and total attempts prevent loops/unbounded crawling. Tracking parameters are removed; record IDs and pagination parameters remain.

PublicFetcher enforces selected exact hosts, public DNS/pinned IP, robots, TLS/redirect rules, minimum delays and a 2 MB response limit. A single retry is allowed for transient connection/timeouts or HTTP 429/502/503/504, not access denials or 404. Same-host public embeds/downloads may be scheduled within the normal budgets. Cross-host iframe URLs are reported but not automatically opened.

`inspect_public_source` implements observed-route adapters:

- SIRIM: accepts only an unambiguous observed GET search form with one supported text field, an explicit source query, no hidden/session/password/file/select fields, and a same-host action. An actual queried table can be record evidence; the landing page cannot. Observed next-page links must preserve the supplied query. POST, CAPTCHA and authenticated forms remain unsupported.
- DOSH: detects missing tables, same-host PDF downloads, embeds and explicit next-page links. An opaque dashboard shell stays context only. If no usable public target is available, `requires_connector` is reported. No private Power BI or other dashboard API is guessed.

Real checks on 2026-10-25 still found SIRIM robots HTTP 500 and a DOSH cross-host dashboard embed; these prevent a claim that live certification/dashboard records are now accessible. See [SOURCES.md](SOURCES.md) and [REMEDIATION.md](REMEDIATION.md).

## Cleaning, PDF/OCR and extraction

HTML cleaning retains article headers and wrapper-form content while removing controls, menus and calendars. Text retention is bounded at 180,000 characters; links at 1,000 unique same-host targets. Truncation is explicit. Exact normalized-body duplicates within the same source class/evidence scope reuse extraction and retain alternate URLs. Near matches are flagged but kept, especially when dates, company names or amounts differ.

PDF parsing uses pdfplumber for at most 30 pages and 180,000 characters. It retains original extracted text, page spans, page coordinates, separate metadata dates, and bounded table rows/bounding boxes as context. Tables do not replace quotation text, and difficult layouts can still misalign rows. Optional local OCR records method and confidence and marks low-quality text. OCR excerpts are recognized transcription, not verified source characters. Missing OCR/text pages are reported and prevent full coverage claims.

Retained text is divided into contiguous chunks, with neighboring text and bounded table context. Kimi is instructed to extract all eligible records in each chunk, without the former six-signal cap. Live runner calls allow at most 4,000 output tokens; incomplete chunks can be subdivided. Verified FTSE factsheet layouts separate the main column from the sidebar to avoid mixing dates into company rows. It reports coverage_complete/coverage_note; incomplete or output-truncated chunks can be split up to four times. Unresolved chunks remain visible. Quotes must occur in the target chunk and full document, with global character offsets and PDF page references. Results are sorted by document/position; duplicates across split work are removed.

Risk-mode review is part of Extract signals, not the later reasoning/scoring stage. Local rules check discovery-only consistency and whether explicit ISO/year/currency values in generated claims occur in source text. Enforcement, incident, certification, OCR or rule-flagged signals are reviewed in batches of five, with quotes and adjacent context. The reviewer returns supported/uncertain/unsupported; it does not change source evidence. Unsupported/uncertain items remain visible with `eligible_for_scoring=false`. ESG discovery-only signals are also not eligible for need-based scoring. A supported review is source-support assessment, not proof of truth.

## Result contract for the next module

Existing `signals`, `documents`, `errors`, `source_decisions`, `tool_trace`, `llm_usage`, and `not_executed` remain. Added fields include:

- `coverage`, `extraction_complete`, `remaining_chunks`, `unattempted_urls`, `stop_reason`, `checkpoint_path` and `cost`.
- Per signal: stable signal ID, chunk ID, rule checks, semantic review status, evidence text method, original request URL and evidence level.
- Per document: page/table metadata, quality warnings, exact duplicate source aliases or near-duplicate candidates.

`extraction_complete` means all retained eligible chunks were processed without known truncation and no queued retrieval remains. It is not a guarantee of model recall, entire-site coverage or source accessibility. `coverage.scope_complete` separately reports unresolved fetch failures. `coverage.pending_review` must also be checked. Empty signals are valid; partial runs can supply valid candidates while retaining failures and pending work. `discovery_only` is not a demonstrated safety need. Every signal still requires human review. Company entity resolution and lead scoring belong downstream.

## Static context and tools

`app/context.py` assembles the fixed prefix from `prompts/system.md`, `config/sources.json`, and strict Plan/ChunkExtraction/SemanticReview schemas. Dynamic prompts, plans, chunks and timestamps follow in user messages. Six tool definitions are shared with each model call: fetch_public_page, clean_html, clean_pdf, discover_links, verify_evidence, inspect_public_source. The runner invokes these tools; `tool_choice=none` prevents model-controlled network expansion. Stable prefixes support caching without promising cache hits.

The supplied project brief is context, not executable instructions. Retrieved pages and quoted text remain untrusted data. No embedded instruction may change workflow boundaries, disclose keys or contact anyone.

The optional legacy FastAPI UI is retained, but the terminal is the supported interaction flow. To start it explicitly: `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`. `/api/context`, `/api/config`, `/api/run` and `/openapi.json` remain; the API uses the same bounded runner and saves checkpoints. The legacy UI does not expose all new CLI controls.

## Validation and references

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Offline tests use fixtures/mocks and no paid model calls. [EVALUATION.md](EVALUATION.md) records the previous 20-call evaluation; [REMEDIATION.md](REMEDIATION.md) records this implementation and its additional verification. Dependency/transport warnings and connector limitations are reported honestly; mocked tests do not establish live source access or production accuracy.

The original project brief defines the Malaysian use case. The [AI agent book](https://github.com/bojieli/ai-agent-book) informed fixed context versus dynamic run data and tool contracts. No external brief or website instruction is executed as an operator request.

## Module One handoff

This branch implements Receive prompt, Fetch + clean, Extract signals, Filter + score, and LLM reasoning for the SEGP group project. Customer review and CRM writes remain for downstream development.

Local environments, API keys, captured source documents, checkpoints, and evaluation artifacts are intentionally excluded from Git. Links into data/ in EVALUATION.md and REMEDIATION.md refer to local historical artifacts and are unavailable in this checkout. Recreate the environment and configure your own .env using the setup instructions above.

For downstream integration, consume the exported result JSON (or the result object inside a local checkpoint). Preserve signals, documents, coverage, errors, status, and remaining_chunks. Partial results can contain usable evidence; discovery_only records with eligible_for_scoring=false require further research before demand scoring.

## Filter + score (Module 2 v0)

`app/scoring.py` implements the deterministic Filter + score stage: entity grouping, duplicate handling, hard gates and the placeholder rubric (weights must be calibrated and documented by the team). Add `--score` to a one-shot or resumed run to attach `filter_score` decisions to the result JSON, or score a saved result with `python -m app.scoring result.json`. The stage makes no model calls and writes nothing to a CRM. When run by itself, `llm_reasoning`, `customer_review` and `write_to_crm` remain in `not_executed`; Module 3 below advances `llm_reasoning`. The function is registered in `ToolRunner` only, not in the model-visible `TOOLS` list.

## LLM reasoning (Module 3)

`app/reasoning.py` consumes a scored result JSON and sends a compact evidence brief to a second model. It returns a user-facing overall summary plus, for every account, a recommendation, reason, risks, supporting evidence and next-step hypothesis. The model does not change scores, gates, evidence, organisation names or quotations.

| Pipeline stage | Executor |
|---|---|
| Receive prompt, Fetch + clean, Extract signals | Kimi K3 and deterministic tools |
| Filter + score | Deterministic rules |
| LLM reasoning | Separate OpenAI-compatible model |

The second model defaults to OpenAI `gpt-4o-mini`. DeepSeek endpoints are detected automatically and use DeepSeek's `json_object` output mode. Configure it in `.env`:

```dotenv
REASONING_API_KEY=YOUR_SECOND_MODEL_API_KEY
REASONING_BASE_URL=https://api.deepseek.com/v1
REASONING_MODEL=deepseek-v4-pro
```

Run a complete offline demo through scoring, then generate the reasoning report:

```powershell
python -m app.cli --mode demo --prompt "Test research" --score --output scored.json
python -m app.reasoning scored.json --out reasoned.json
```

The default output is a readable report. Add `--json` to print the combined JSON. `--out` writes the complete result and never overwrites an existing file. `app/reasoning.py` refuses to run when the second model is the same model on the same endpoint as the Kimi stage, and rejects model output that references evidence outside its supplied brief. See [MODULE3.md](MODULE3.md) for the detailed Module 3 handoff.
