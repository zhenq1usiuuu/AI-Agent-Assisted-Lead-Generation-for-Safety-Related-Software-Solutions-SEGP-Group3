# Safety Signal Lab

Terminal-first Malaysian safety-software research. The implemented stages are Receive prompt (Kimi), Fetch + clean (local tools), Extract signals (Kimi plus evidence checks), Filter + score (deterministic rules), and LLM reasoning (a separate configurable model). Human review and local SQLite CRM writes are implemented; app.workflow provides a single-session entry point.

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

The run guard reserves a conservative byte-based input allowance and maximum output cost before each paid attempt. Returned complete usage settles that attempt using separate uncached-input, cache-read, cache-write and output rates; uncertain failures keep their full reservation. Settlement releases unused reservations and records the cache billing breakdown. Reservations/committed bounds are not actual charges, and may stop a run early. The provider invoice is authoritative. The per-run lock prevents simultaneous resumes. Separate run IDs have separate caps; this is not an account-wide spending limit.

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

This branch implements Receive prompt, Fetch + clean, Extract signals, Filter + score, and LLM reasoning for the SEGP group project. Customer review is implemented in app.review. app.crm writes approved records to local SQLite; app.workflow supports bounded feedback reruns.

Local environments, API keys, captured source documents, checkpoints, and evaluation artifacts are intentionally excluded from Git. Links into data/ in EVALUATION.md and REMEDIATION.md refer to local historical artifacts and are unavailable in this checkout. Recreate the environment and configure your own .env using the setup instructions above.

For downstream integration, consume the exported result JSON (or the result object inside a local checkpoint). Preserve signals, documents, coverage, errors, status, and remaining_chunks. Partial results can contain usable evidence; discovery_only records with eligible_for_scoring=false require further research before demand scoring.

## Filter + score (Module 2 v0)

`app/scoring.py` implements the deterministic Filter + score stage: entity grouping, duplicate handling, hard gates and the placeholder rubric (weights must be calibrated and documented by the team). Add `--score` to a one-shot or resumed run to attach `filter_score` decisions to the result JSON, or score a saved result with `python -m app.scoring result.json`. The stage makes no model calls and writes nothing to a CRM. When run by itself, `llm_reasoning`, `customer_review` and `write_to_crm` remain in `not_executed`; Module 3 below advances `llm_reasoning`. The function is registered in `ToolRunner` only, not in the model-visible `TOOLS` list.

## LLM reasoning (Module 3)

`app/reasoning.py` consumes a scored result JSON and sends a compact evidence brief to a second model. It returns a user-facing overall summary plus, for every account, a code-assigned recommendation, reason, risks, supporting evidence and next-step hypothesis. The model does not change scores, gates, evidence, organisation names or quotations. Deterministic checks reject altered scores, fabricated quotes and forbidden claims; invalid output retries and is otherwise marked for human review.

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

The default output is a readable report. Add `--json` to print the combined JSON. `--out` writes the complete result and never overwrites an existing file. `app/reasoning.py` refuses by default (unless REASONING_USE_KIMI=true) to run when the second model is the same model on the same endpoint as the Kimi stage, and rejects model output that references evidence outside its supplied brief. See [MODULE3.md](MODULE3.md) for the detailed Module 3 handoff.

## Local Kimi testing of all four modules

Use this repository root, not the historical duplicate under OneDrive/.
The local .env uses MOONSHOT_API_KEY. REASONING_USE_KIMI=true explicitly reuses
the Moonshot key, endpoint and model for Module 3. This is a single-model
integration test, not independent cross-model validation. Set it to false
to restore the separate-model configuration. Environment variables override .env.

After installing requirements.txt into .venv, run these PowerShell commands:

```powershell
New-Item -ItemType Directory -Force data | Out-Null
# Modules 1 and 2: retrieval/extraction and deterministic scoring.
.\.venv\Scripts\python.exe -m app.cli --categories ESG --score --budget-usd 1 --permissions-confirmed --output data/scored.json
# Module 3: paid Kimi reasoning.
.\.venv\Scripts\python.exe -m app.reasoning data/scored.json --out data/reasoned.json
# Module 4: interactive human decisions, no model call or CRM write.
.\.venv\Scripts\python.exe -m app.review data/reasoned.json --reviewer "Local tester" --out data/reviewed.json
```

Only use --permissions-confirmed after reviewing source access conditions.
Inspect status, errors and coverage. Output files must be new on every run.
The Module 1 --budget-usd cap does NOT cover Module 3. Module 3 can retry
requests/validation and has no enforced dollar cap. Moonshot reasoning cost
estimates are null unless pricing rates are configured; inspect returned
usage and your provider bill.

No-cost checks:

```powershell
.\.venv\Scripts\python.exe -m app.cli --mode demo --categories ESG --score --output data/demo-scored.json
.\.venv\Scripts\python.exe -m app.review tests/fixtures/reasoned_demo.json --reviewer "Local tester" --out data/demo-reviewed.json
.\.venv\Scripts\python.exe -m pytest -q tests
```

Demo fixtures are synthetic. Running app.reasoning on demo data still costs tokens.

## Signal extraction diagnostics (2026-11-06)

The inspected DOSH-only live runs had HTTP 404, a dashboard without usable
same-host records, and a missing fatal-case table. Increasing OCR, depth or
model budget cannot recover records absent from the fetched pages.
A multi-source live run extracted four ESG candidates before its budget guard
stopped with 12 chunks remaining. Its PDF had usable text and no OCR warnings.

Extraction now schedules company/table/event-rich chunks first, keeps all
remaining spans for coverage/resume, and allows 24 lines per chunk instead of
12 to reduce repeated static-context calls. Existing checkpoints retain their
original chunk boundaries. Link discovery now recognizes haze/jerebu and
worker-health signals. Depth, host restrictions, robots and evidence gates are
unchanged. No candidate is promoted to a demonstrated need without evidence.

Inspect signal_diagnostics, coverage, errors and abstentions in every output.
For company discovery start with ESG; DOSH-only can still return no records
until accessible source data exists. Use --ocr only for scanned PDFs after
installing requirements-ocr.txt. A partial run can be resumed with --resume
RUN_ID --budget-usd 2 --max-chunks 40 --permissions-confirmed; this increases
the cumulative cap, not a guaranteed charge. Do not interpret ESG membership
as safety demand or legal compliance. Scoring also uses a manufacturing/CV
safety rubric, which is narrower than general inspection research.

Module 3 repairs one unambiguous schema mistake locally: if root open_questions
is absent and account-level open_questions values are all string arrays, it
moves those questions to the root without changing account analysis. The repair
is recorded in model usage. Other schema errors trigger correction within the
existing three-attempt limit; exhausted attempts fail explicitly. Returned API
usage is recorded before content validation, including invalid responses.
Correction retries can incur additional model charges.

## Integrated workflow and CRM (2026-11-07)

The fetched feature/one-stop-cli-and-crm branch added a SQLite writer and
command aliases, but did not contain orchestration or feedback reruns.
This local integration adds both while retaining the Kimi configuration,
structure repair, extraction scheduling, and evidence gates.

From this project directory:

```powershell
# One session: research -> score -> reasoning -> human review -> local CRM.
python -m app.cli --full --categories ESG --reviewer "Will"
# Equivalent dedicated entry point:
python -m app.workflow --categories ESG --reviewer "Will"
# Continue existing work without repeating extraction/reasoning unnecessarily:
python -m app.cli --full --from-result data/reasoned.json --reviewer "Will"
```

Without --categories, the workflow asks for categories. Source terms must be
reviewed before a fresh run. Module 3 requires explicit confirmation because
its calls/retries are billed outside Module 1's --budget-usd cap. There is no
global workflow dollar cap. A new feedback round has its own research cap;
--max-rounds defaults to 2 and accepts 1-3 (including the first round).
An imported result retains its stored research settings for feedback reruns.
No command claiming a fully offline demo calls Module 3: use tests/mocks for
an offline full-flow check, or the existing reasoned_demo.json review fixture.

Every workflow writes a unique data/workflows/<uuid>/ directory containing
research, scored, reasoned, reviewed and feedback JSON, a workflow manifest,
and a CRM receipt when written. Failure or cancellation preserves completed
stage artifacts. Continue from an exported scored/reasoned file with
--from-result; an interrupted review must be reviewed again before CRM write.
This is not automatic process-level resume. Never pass the checkpoint wrapper.

Approve and edit decisions enter the local CRM queue. A separate terminal
confirmation writes that queue to data/crm.db (override with --db). Reject,
defer and merge do not enter CRM. They can trigger an optional clarification
and fresh research round with saved feedback and an optional extra public URL.
The source categories remain unchanged; the feedback is a research direction,
not factual evidence. The new result is rescored and re-reviewed; prior
approval is never copied forward. Merge records intent only, not entity merging.

SQLite is the implemented CRM backend, not Salesforce/HubSpot or a remote CRM.
The writer checks completed review decisions against queue identities, names,
reviewer, timestamps, scores and evidence. It is not cryptographic proof of
human identity; review files remain trusted local artifacts. Writes use one
transaction. Exact repeats are skipped; changed records sharing a key raise
an explicit conflict and roll back that batch rather than silently discard edits.
No automatic update of previously imported accounts is implemented.

Standalone writer:

```powershell
python -m app.crm data/reviewed.json --db data/crm.db
```

Optional editable installation (run in the virtual environment):

```powershell
python -m pip install -e .
signal-lab --categories ESG --reviewer "Will"
```

signal-lab runs the integrated workflow; signal-research retains the previous
research CLI; signal-review and signal-crm are standalone aliases. Use an
editable source checkout because prompts/config/static are read from the
project directory. Plain python -m app.cli retains its prior behavior; add
--full for the integrated workflow. No external CRM connection is configured.

## Module progress and timing

The integrated entry points (--full, app.workflow and signal-lab) display a
short step labels and a live elapsed stopwatch for Modules 1-4, CRM and
clarification/rerun, followed by a timing summary at the end. Detailed timestamps
and result summaries are written to files only. These are execution explanations,
not the model's private chain of thought. No additional model calls are made.
Module 1 timing covers receive prompt, fetch/clean and extraction together.

Each workflow directory now contains progress.jsonl, appended at stage start
and finish, and workflow.json with per-round modules and total timing.
Timestamps use UTC ISO 8601. elapsed_seconds uses a monotonic clock;
user_wait_seconds measures time inside input prompts, including review choices;
processing_seconds excludes that input wait but includes model/network waits.
The timer begins after initial category/reviewer/access setup. Stage file I/O
outside the timed blocks contributes to total time, so stage sums need not equal
the workflow total. Timing is local to this invocation, not historical source runs.
Imported results are marked reused; skipped, deferred, not_executed, interrupted
and failed stages are distinguished. Reuse durations measure only local handling.
All previously saved workflow records remain unchanged.

The stopwatch refreshes one console line every half second in an interactive
terminal. It pauses drawing while you type (elapsed time continues counting),
then resumes after input. Redirected output or consoles without TTY support
receive plain step labels without animation. No precise timestamps or JSON
progress objects are printed to the terminal. Human-review evidence cards and
questions remain visible. The final summary includes total and per-stage times.

## Business contacts and Twenty (2026-11-13)

Company approval and person/contact approval are separate decisions. Missing
contacts never block saving a company. No emails, calls or messages are sent.

### Automatic public contact discovery

Run the full pipeline without preparing a contacts JSON file:

```powershell
python -m app.cli --full --reviewer "Will" --contact-budget-usd 1 --max-rounds 1
```

After company review, approve the paid contact search prompt. The program searches
for each approved company's website using the same MOONSHOT_API_KEY, lists actual
search results, and asks you to select the verified company website and confirm
its access terms. It fetches that page and observed same-host contact/about/team
links with the existing robots, public-IP, rate and size protections. Search
snippets alone are never contact evidence. Contact footers and mailto/tel values
are retained. Kimi extracts published names, roles, work emails and business
phones; missing fields remain null. General company routes remain separate from
named individuals. Each populated field must occur verbatim in its source quote.
Company identity must appear on the same fetched page; strict name matching can
miss abbreviated or rebranded companies. JavaScript-only and image-only contacts
are not automatically resolved. This is bounded discovery, not whole-site coverage.

Review each candidate and its company association, supply a storage basis, and
approve the final CRM write. Approved contacts are written to SQLite together
with evidence and review records. Rejecting a contact does not block its company.
Public visibility is not marketing consent: unknown/denied defaults to
 do_not_contact=true. No outreach is sent. Never fabricate data to fill gaps.

Limits per round: five approved companies, one search per company, four fetched
pages per company, eight local text windows per page, and ten candidate contacts
per model response. Remaining companies and errors are recorded in
round-N-contacts.json. The separate model reservation cap defaults to USD 1;
search API fees are billed separately and bounded by the five-call limit.
The research and reasoning budgets are unchanged. Contact model reservations
and returned usage are saved in round-N-contact-budget.json. No automatic search
retries occur. Unsupported API access produces an explicit error and no invented
contacts. API availability depends on the account/provider.

Search API reference: https://platform.kimi.ai/docs/guide/best-practices-for-web-search
The implementation uses POST /v1/tools/search with include_content=false.
Use --skip-contacts to skip automatic discovery. Optional --contacts-file still
supports explicit URLs/general inbox extraction or supplied verbatim evidence
(config/contacts.example.json); it overrides automatic discovery and does not
make contact-model calls. Example entries are synthetic and must not enter CRM.
Use --from-result only with an existing exported research/scored/reasoned JSON.

To inspect stored contacts from the project terminal:

```powershell
python -c "import sqlite3; c=sqlite3.connect('data/crm.db'); print(c.execute('SELECT account_key,name,job_title,work_email,phone,do_not_contact FROM contacts').fetchall()); c.close()"
```

New SQLite tables: companies, contacts, contact_evidence and contact_review_events.
Existing leads and their records are preserved. Companies and contacts are linked
by account_key; company routes and named people are distinguished by kind.
Newer approved contact decisions update the contact while retaining review history;
unknown/denied states cannot activate contact. The same submitted evidence is
not duplicated. Sources stay in separate evidence rows; full source_text is kept
in local workflow JSON, not copied to the contact database table. Protect and
retain local JSON only as appropriate for its data. Rejected candidates are not
inserted as contacts. Existing company conflict behavior remains unchanged.

### Twenty adapter

The local SQLite flow works without Twenty. To connect a self-hosted instance,
configure TWENTY_BASE_URL (HTTPS origin) and TWENTY_API_KEY in .env. Copy
config/twenty.mapping.example.json into a local data/ mapping and verify every
resource and field against your own workspace Settings -> Developers -> API.
The example is NOT a ready-to-use workspace schema. Create the required custom
fields/objects first; the adapter does not modify metadata. Dot notation maps
composite fields, e.g. name.firstName and emails.primaryEmail. The full sourced
name is preserved in the firstName example rather than guessing family names.
Phone/custom permission fields must be adapted to your workspace's real types.

Mappings cover Company, Person, Opportunity, Lead Signal, Review Event and a
custom Business Contact Route object for general company inboxes. Evidence and
permission fields must be mapped, not silently dropped. Contacts must belong to
approved companies and pass the same local evidence/approval checks before export.
Old review files may lack full signal provenance; do not invent missing values.

```powershell
# Local export plan only; no network or API key needed.
python -m app.twenty data/reviewed-with-contacts.json --mapping data/twenty.mapping.json --out data/twenty-plan.json
# After inspecting the plan and verifying schema, set workspace_verified=true.
# This command actually sends business/contact data to your configured Twenty.
python -m app.twenty data/reviewed-with-contacts.json --mapping data/twenty.mapping.json --out data/twenty-receipt.json --apply
```

Use the new round-*-reviewed.json generated by your workflow as the input above.
The adapter uses deterministic UUIDs and GET/create-or-PATCH per record, saves
confirmed remote IDs to a receipt after each operation, and stops on uncertain
errors. The target workspace must support caller-supplied IDs and these REST
operations. No automatic retry of an uncertain write occurs. Subsequent explicit
runs look up the same ID before writing. Multi-object writes are NOT a remote
transaction: earlier successes remain if a later write fails. Keep the receipt
and retry the same reviewed data after resolving the cause. Do not sync concurrent
runs for the same accounts or replay an older consent state over a newer one.
Use a separate test workspace for synthetic examples.

Official schema and authentication reference:
https://docs.twenty.com/developers/extend/api
The connector has mock-HTTP coverage; live Twenty validation requires your actual
workspace URL, credentials and verified mapping. It is a separate explicit export,
not an automatic network side effect of approving a company in the terminal.

## Usage settlement and category coverage

The default budget is unchanged. Before every model attempt, the ledger reserves
conservatively for that request. A successful response settles using its actual
Chat Completions usage: uncached input, cache reads, 5m/1h cache writes, and output
are separate billing buckets. Cache writes are not counted again as uncached
input. Cache-write response headers identify mixed TTLs when supplied; otherwise
the recorded request TTL applies (5m by default). Missing/invalid usage and
uncertain failed attempts retain reservations. Valid usage is recorded even if
subsequent JSON/schema validation fails. Existing ledgers with retained usage
are recalculated on load; no historical calls are erased.

The unified terminal prints Estimated used, Next reservation, Unresolved
reservations, and Model budget in USD. The estimate is not an invoice and does
not include search API fees or unmetered Module 3 charges. The next reservation
is a preflight upper-bound estimate, not money already spent. A request can still
be blocked when its reservation exceeds the remaining cap.

K3 rates used (USD per million tokens): uncached 3, cache read 0.3, cache write
5m 3 / 1h 6, output 15. Reference checked 2026-11-14:
https://www.kimi.ai/academy/best-practices-for-context-caching

Extraction now cycles across retained source categories, taking one chunk from
each before returning to the same category. Evidence-rich chunks remain first
within their category. This minimum first-pass scheduling opportunity is subject
to the global budget; it is not a promise of one paid call per category. Terminal
and JSON category summaries distinguish processed/pending chunks and signals.
No extractable chunks does not mean the whole website has no relevant companies.
DOSH dashboard/404 and SIRIM robots restrictions remain access limitations; these
changes do not bypass controls or claim to repair unavailable upstream sources.

## DOSH / SIRIM access recheck (2026-11-14)

The legacy DOSH prosecution URL returned 404 and has been replaced by the
verified official Malay prosecution/compound dashboard. Both dashboard entries
are context-only: the live page embeds app.powerbi.com, not a static case table.
The connector records the exact observed external embed for review, without
silently expanding allowed hosts or claiming its records have been downloaded.

SIRIM's homepage returned 200 and robots.txt returned 404 on this check; the old
robots 500 is not a permanent site condition. Its published menu.js uses POST
ProductSearch navigation to search.aspx. A fresh anonymous session following
that navigation received MEMBER LOGIN, with a password field. No login bypass,
credential collection or authenticated search was implemented. An explicit GET
query alone cannot fix this current directory flow. Public company certificates
can still be supplied as additional source URLs, subject to ordinary validation.

Run a fresh diagnostic using production robots/IP/rate checks (no Kimi calls):

```powershell
python -m app.source_check --permissions-confirmed
```

The report is saved to data/source-health.json. Results distinguish reachable
wrappers, external connectors, interactive directories, missing records and
fetch failures. These are observations at capture time, not guarantees of future
availability. Manual official exports are not automatically imported by this
command. Neither directory currently guarantees automatic company records.

Production verification on the same date still received SIRIM robots HTTP 500,
although ordinary HTTP diagnostics received 404. Treat access as intermittent or
request-path dependent; the production fetcher continues to fail closed. The
stored source-health report is the production result, not a claim of restoration.
