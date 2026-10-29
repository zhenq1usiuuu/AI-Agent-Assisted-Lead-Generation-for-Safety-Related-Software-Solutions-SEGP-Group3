# Official source registry

Updated 2026-10-21 following the user's review of the replacement shortlist. This replaces the original generic DOSH portal and working-at-heights discovery seeds. All entries below are included in the shared static LLM prefix. The registry is `config/sources.json`; this document explains how to interpret it.

## Retrieval and evidence roles

- `html`: fetch and clean a public HTML/text page. It does not execute JavaScript. A separate observed-route adapter may construct supported read-only SIRIM GET queries with an explicit operator term.
- `pdf`: fetch and extract bounded text from a text-based PDF. No OCR or reliable table reconstruction.
- `manual`: the necessary connector is unavailable. The public wrapper may be inspected for actual same-host embeds/downloads, but stays context only. If no usable target exists, the result reports `requires_connector`; opaque cross-host dashboard records remain unsupported.
- `context_only`: a landing/index/about/description page. Retain it for provenance and, where enabled, bounded same-host discovery up to three layers. Do not send it for company-signal extraction or accept company signals from it.
- `records`: a candidate record/article/PDF, still requiring relevant retrieved text and exact evidence validation. This label never guarantees company coverage, legal status, relevance or access.
- Unregistered same-host links discovered from sources with `follow_links=true` are candidate records, subject to fetching and evidence checks. Registered URLs keep their own scope/connector restrictions. Query variants of registered landing/manual routes keep those restrictions; query-specific PDF addresses must match their registered query. Only adapter-generated SIRIM GET responses with observed tables can be promoted to record scope. Observed next-page links are supported within depth/page limits; query variants supplied directly by users keep registry restrictions.
- `reference_only`: fixed background context, not selectable as a research seed. JHEKS supports union context; Twenty supports future CRM design.
- All access requires operator permission review and independent robots checks. Access notes are observations, not promises or automatic permission.

## Source list

| ID | Official page | Class | Connector / scope | Purpose |
|---|---|---|---|---|
| `sirim_directory` | [SIRIM Malaysian Certified directory](https://www.malaysiancertified.com.my/index.jsp) | certification | html / context_only | lead_evidence |
| `ftse4good_factsheet` | [FTSE4Good Bursa Malaysia factsheet](https://research.ftserussell.com/Analytics/FactSheets/Home/DownloadSingleIssue?issueName=F4GBM&openfile=open) | esg_index | pdf / records | lead_evidence |
| `bursa_indices` | [Bursa Malaysia indices and ESG information](https://www.bursamalaysia.com/trade/our_products_services/indices/ftse_bursa_malaysia_indices/overview) | esg_index | html / context_only | lead_evidence |
| `dosh_prosecution` | [DOSH prosecution records](https://dosh.gov.my/en/pendakwaan/) | regulatory | html / records | lead_evidence |
| `dosh_enforcement_dashboard` | [DOSH prosecution and compound dashboard](https://dosh.gov.my/en/dashboard/laporan-pendakwaan-dan-kompaun/) | regulatory | manual / context_only | lead_evidence |
| `dosh_fatal_cases` | [DOSH fatal accident cases](https://dosh.gov.my/en/kes-kemalangan-maut/) | regulatory | html / records | lead_evidence |
| `fmm_newsroom` | [FMM newsroom](https://www.fmm.org.my/Articles/Newsroom) | industry_procurement | html / context_only | lead_evidence |
| `fmm_publications` | [FMM publications](https://fmm.org.my/publications) | industry_procurement | html / context_only | lead_evidence |
| `fmm_bia_2025_2` | [FMM Business in Action Vol. 2/2025](https://www.fmm.org.my/Publications/PublicationCardsList/Detail/be4a1bef-e4da-4eed-a880-986630932a0d?returnUrl=%2FPublications%2FPublicationCardsList) | industry_procurement | html / context_only | lead_evidence |
| `mtuc` | [Malaysian Trades Union Congress](https://mtuc.org.my/about-us/) | trade_union | html / context_only | lead_evidence |
| `mef` | [Malaysian Employers Federation](https://www.mef.org.my/) | industry_association | html / context_only | lead_evidence |
| `mbam` | [Master Builders Association Malaysia](https://mbam.org.my/contact-us-2/) | industry_association | html / context_only | lead_evidence |
| `cicm` | [Chemical Industries Council of Malaysia](https://www.cicm.org.my/) | industry_association | html / context_only | lead_evidence |
| `mpma` | [Malaysian Plastics Manufacturers Association](https://www.mpma.org.my/home) | industry_association | html / context_only | lead_evidence |
| `margma` | [Malaysian Rubber Glove Manufacturers Association](https://margma.com.my/) | industry_association | html / context_only | lead_evidence |
| `msosh` | [Malaysian Society for Occupational Safety and Health](https://msosh.org.my/about-2/) | professional_body | html / context_only | lead_evidence |
| `jheks` | [Department of Trade Union Affairs](https://jheks.mohr.gov.my/index.php/en/front-page/) | regulatory_reference | manual / context_only | reference_only |
| `twenty_hosting` | [Twenty self-hosting documentation](https://docs.twenty.com/developers/self-host/capabilities/docker-compose) | technical_reference | manual / context_only | reference_only |
| `twenty_api` | [Twenty API documentation](https://docs.twenty.com/developers/extend/api) | technical_reference | manual / context_only | reference_only |
| `twenty_objects` | [Twenty custom-object documentation](https://docs.twenty.com/developers/contribute/capabilities/backend-development/custom-objects) | technical_reference | manual / context_only | reference_only |

## Access observations and interpretation limits

- **sirim_directory**: Landing page verified 2026-10-21. A bounded public GET-form adapter is implemented, but the latest check on 2026-10-25 stopped at robots HTTP 500; live certificate-query access remains unverified. Certification is scheme-, scope-, site- and status-specific. Landing text does not certify any named company.
- **ftse4good_factsheet**: PDF verified 2026-10-21; displayed reporting date 2026-09-30. Snapshot at capture time, not a full constituent directory. Provides selected constituents, sectors and market-cap information. Defined ESG index criteria are not certification of legal compliance or evidence of software buying intent.
- **bursa_indices**: Official entry point; verification returned HTTP 403 on 2026-10-21. Access controls must not be bypassed. Use official dated constituent/rating documents where accessible; do not assume every indexed company is large.
- **dosh_prosecution**: Official navigation lists prosecution; verification timed out on 2026-10-21. Current table and historical completeness unverified. Record exact company, case date, law and stated outcome only when actually present. A charge is not a conviction; historical records do not establish current non-compliance.
- **dosh_enforcement_dashboard**: Wrapper page verified 2026-10-21; embedded record access needs an unimplemented dashboard connector. Never extract company-level cases from a dashboard shell or fabricate hidden records.
- **dosh_fatal_cases**: Entry page verified 2026-10-21; individual case availability unverified. An accident report is not a conviction. Do not identify victims or infer blame, negligence, or hidden risk.
- **fmm_newsroom**: Public newsroom verified 2026-10-21; keyword discovery follows at most three same-host layers with bounded pages and outgoing links. Index is for discovery. Only an actual article explicitly relating a company to an incident supports that relation; affected companies are not necessarily operators or liable parties.
- **fmm_publications**: Official indexed publication listing; some items are paid. Exact-host redirects remain restricted. Publication listing is not an accident register. Only freely accessible permitted article/PDF content may supply evidence.
- **fmm_bia_2025_2**: Official indexed description discusses Putra Heights; current download path unverified. Old PDF URL returned 404. Description is context only. Follow an actual public same-host download link when available; do not infer incident responsibility from a publication summary.
- **mtuc**: Official union federation page; page-specific content still needs review. Worker representation, not an employer directory. Membership does not establish an incident or buyer need.
- **mef**: Official employer organisation website. Employer organisation, not a worker union. Public OSH activities are supplementary context only unless a linked article supplies direct evidence.
- **mbam**: Official contact page retained for context. Unexpected unrelated links were seen on a separate homepage during review; manual content review required. Construction association, not a worker union; do not use contact-page names as buyer contacts.
- **cicm**: Official chemical-industry association website. Chemical-sector and Responsible Care context; membership does not prove certification or non-compliance.
- **mpma**: Official plastics-industry association website. Use relevant public sector/news articles; membership alone is not evidence of incidents or purchase intent.
- **margma**: Official glove-industry association website. Industry association, not a worker union. Manufacturer profiles and incidents must be distinguished.
- **msosh**: Official OSH professional organisation page. Professional organisation; awards or membership are not regulator approval, compliance guarantees, or purchase intent.
- **jheks**: Official regulator for trade-union background and verification. Reference only; not an employer-incident or buyer directory.
- **twenty_hosting**: Reference context retained from the original project brief; no retrieval connector is enabled. Later CRM integration design only.
- **twenty_api**: Reference context retained from the original project brief; no retrieval connector is enabled. Later CRM integration design only.
- **twenty_objects**: Reference context retained from the original project brief; no retrieval connector is enabled. Later CRM integration design only.

## Excluded historical URLs

The former `dosh_portal` and `dosh_heights` IDs have been removed rather than silently aliased to new meanings. The indexed DOSH `/dummy/pendakwaan/` table and the obsolete FMM Vol. 2 PDF URL are not registered or recommended fallbacks: live retrieval was unverified or failed during review. Normal network/robots failures remain visible in run errors.

## Interpretation

DOSH charges, convictions, fines, compounds and accident reports are distinct records. Preserve the exact outcome and date; historical evidence is not proof of current non-compliance. ESG criteria are not blanket legal compliance, large-company status or buying intent. Certification is specific to its scheme, scope, site and status. FMM incident coverage can describe disrupted companies rather than responsible operators. Union/association membership, professional awards and training attendance do not establish incidents, violations or demand.

## Inspect without a key

```powershell
python -m app.cli --list-sources
python -m app.cli --list-sources --output source-registry.json
```

The terminal category selector fixes source scope. With an advanced custom prompt and no explicit source IDs, the model selects relevant sources. Use repeated `--source ID` options to select registered research sources explicitly; this overrides model seed choices but cannot enable an unsupported connector or turn context into evidence. A page budget applies to fetch attempts across seeds and discovered links, not PDF pages. PDF extraction has its own 30-page and 180,000-character caps with explicit truncation. Optional local OCR and table context are supported; associations still require review.



## Runtime audit on 2026-10-25

A fresh bounded audit found SIRIM robots HTTP 500, Bursa robots HTTP 403, DOSH prosecution HTTP 404, and MBAM/MARGMA HTTP 500. The DOSH fatal-case body explicitly contained `Table not found.`; it must not be treated as a case dataset. These are dated observations, not proof of permanent unavailability; no access controls were bypassed and no substitute URLs were invented. The embedded DOSH dashboard remains manual-only. See [EVALUATION.md](EVALUATION.md) for the full audit and any later same-day end-to-end observations.

FMM Business in Action Vol. 2/2025 contains a download button but no public PDF anchor in the captured HTML; the cleaner does not execute it or infer a download URL. MEF wraps body content in an HTML form: the cleaner now removes controls while retaining the public text. Registry URLs and source roles are unchanged by this audit.


## Remediation implementation

See [REMEDIATION.md](REMEDIATION.md) for the latest connector probe, PDF/OCR, multi-layer retrieval and chunk/resume verification. Public wrapper inspection is implemented; this is not a claim that SIRIM certificate queries or the cross-host DOSH dashboard are fully accessible. Reference-only hosts remain excluded. No source URL was silently replaced.
