# DealWise — Repository Audit, Architecture & Implementation Plan

> DealWise is a derivative of the open-source [Kotaemon](https://github.com/Cinnamon/kotaemon)
> project (Apache License 2.0, © Cinnamon). The upstream `LICENSE.txt`, copyright
> notices and contributor credits are preserved. This document describes what was
> found in the upstream code, what DealWise reuses, and what DealWise adds.

Status of this document: audit **approved** by the project owner. Requirements added
after approval (lifecycle/versioning, vendor management, deal comparison, data
integrity, security) are integrated below and marked **[R1]–[R5]**.

---

## 1. Current architecture (upstream Kotaemon)

Monorepo with two Python packages and a thin root.

| Path | Role |
|---|---|
| `libs/kotaemon` | Framework: loaders, splitters, embeddings, vector/doc stores, rerankers, LLM wrappers, QA + citation pipelines |
| `libs/ktem` | Gradio application built on the framework (`ktem.main.App` assembles the tabs) |
| `app.py` | Launches the Gradio app |
| `flowsettings.py` | Central configuration, driven by `.env` via `python-decouple` |

**Persistence**

- SQLite (SQLModel): `conversation`, `user`, `settings`, `issuereport`.
- Per file-index dynamic SQLAlchemy tables: `Source` (with a JSON `note`), `Index` (file→chunk links), `FileGroup`.
- Vector store: Chroma. Doc store: LanceDB. Original files: a filestorage folder.
- Alembic is disabled (`KH_ENABLE_ALEMBIC=False`); schemas are created with `create_all`.

**Extension points**: `KH_INDEX_TYPES`, `KH_REASONINGS`, `FILE_INDEX_*` overrides
(pipeline, retrievers, extractors, selector UI), pluggy-based extensions.

## 2. Existing capabilities that DealWise reuses

| Need | Existing component |
|---|---|
| Ingestion/parsing | `IndexDocumentPipeline`; readers for PDF, DOCX, XLSX, HTML, TXT, images; optional Docling / Azure DI / Adobe / Paddle OCR |
| Chunking / embeddings | `TokenSplitter`; OpenAI, Azure, Cohere, Voyage, Google, Mistral, Ollama, FastEmbed |
| Retrieval | `DocumentRetrievalPipeline`: hybrid / vector / text, MMR, table prioritisation, **scoping by `doc_ids`** |
| Reranking | Cohere, Voyage, TEI, LLM rerankers, LLM relevance scoring |
| LLMs | Manager with DB-backed configs (OpenAI, Azure, Anthropic, Gemini, Groq, Cohere, Mistral, Ollama) |
| Citations | `CitationPipeline` (function calling) / inline citation, `page_label` metadata, PDF.js viewer with highlights |
| Multi-doc QA | `FullQAPipeline`, decomposed QA, ReAct/ReWOO agents, GraphRAG indices |
| Users | `User` table, login page, admin gating of the Resources tab |

## 3. What did not exist and must be built

Structured contract extraction; contract/vendor/lifecycle data model; human review of
extracted fields with provenance; outcomes model and entry UI; deal evaluation and
historical comparison; pattern/insight computation with evidence checks; dashboard,
deadline tracking, reports; a product shell and branding; row-level authorisation.

## 4. Database design

All new tables live in a `dw_` namespace; **no upstream table is modified**. Every table
carries `org_id` (default `"default"`) so multi-organisation support does not need a
schema rewrite.

Four layers are kept strictly separate:

1. **Original documents** — upstream `Source` rows + files on disk. `dw_contract` links to the stored file; DealWise additionally records a SHA-256.
2. **Extracted / entered contract information** — `dw_vendor`, `dw_vendor_alias`, `dw_contract`, `dw_contract_term`, `dw_field_provenance` (origin: `extracted | user_entered | user_corrected`, source page, verbatim quote, confidence, revision history).
3. **User-entered outcomes** — `dw_outcome`, `dw_outcome_issue`.
4. **AI-generated analysis** — `dw_deal_evaluation`, `dw_evaluation_item` (each tagged `extracted_fact` vs `ai_interpretation`, with evidence references), `dw_insight`.

Plus `dw_audit_log`.

### [R1] Contract lifecycle & versioning

Every stored document is one row in `dw_contract` and belongs to a **lineage**
(`lineage_id` = the id of the original agreement; all amendments, renewals and
revisions share it).

| Column | Meaning |
|---|---|
| `relation_type` | `original`, `amendment`, `renewal`, `revision` |
| `parent_contract_id` | The document this one modifies / renews / replaces |
| `version_number` | Position within the lineage |
| `effective_from` / `effective_to` | Period during which *this document* governs (default to start/end date) |

Semantics, documented and unit-tested:

- A **base document** is `original`, `renewal` or `revision`. Amendments modify a base document.
- A document is **superseded** when a `revision` names it as parent.
- **Active version on date D**: among base documents in the lineage with `effective_from ≤ D`, take the one with the latest `effective_from`; it is active if `effective_to` is empty or `≥ D`. Amendments whose own effective window contains D (and that descend from the chosen base) are reported alongside it. If no base qualifies the answer is "no active version" — never a guess.
- Portfolio counts/values use *current agreements* (base documents that are not superseded). An amendment's value, if given, is the **full revised contract value** and overrides its parent's value from its effective date; if blank, the parent's value stands. Amendments are not counted as separate contracts.

### [R2] Centralised vendor management

- One `dw_vendor` per organisation, keyed on a **normalised name** (lower-cased, punctuation stripped, legal suffixes such as Inc/Ltd/LLC/GmbH/Pvt removed) with a unique constraint per organisation.
- Alternate names live in `dw_vendor_alias`.
- Creating a contract with a vendor name first resolves it: exact normalised match or alias → reuse; near match (similarity ≥ 0.88) → the UI asks the user to confirm "use existing" or "create new"; otherwise create.
- Admins can **merge** duplicate vendors: contracts are re-pointed, the merged name becomes an alias, and the action is audit-logged.
- Phase 4 outcomes attach to contracts, so vendor-level history is derived by joining through `vendor_id` — never stored as a free-standing vendor "score".

### [R3] Deal comparison (design, built in Phase 3)

- Terms are compared **per category** (financial, payment, delivery, penalties, termination, renewal, SLA, obligations) and never collapsed into a single score.
- Each compared term is classified: **directly comparable** (same unit/type, normalised values side by side), **comparable with caveats** (e.g. different currencies, stated assumptions), or **not meaningfully comparable** (different structure; both texts shown, no verdict).
- Every comparison lists the exact historical contracts (and versions) used as references, with links back to source pages.
- Verdicts (favourable/unfavourable) must carry a reason and an evidence reference.

### [R4] Historical data integrity

- `dw_contract.role ∈ {historical, proposal}` with a database `CHECK` constraint.
- All analytics and retrieval go through functions that hard-filter `role = 'historical'`; there is no code path that aggregates over both. Tests insert proposals and assert they never appear in counts, values or statistics.
- Outcomes can only be attached to historical contracts (enforced in the service layer and, in Phase 4, by a database constraint).
- Insights always display sample size (`n`), the data they were computed from and stated limitations; below a minimum sample size (default 3) the system reports "insufficient data".
- Vendor performance is only ever derived from recorded outcomes. With no outcome rows, the UI says so rather than inferring anything from contract text.

### [R5] Security

See §8 for the review and the fixes.

## 5. Application workflow

1. **Upload** → file stored, hashed, metadata recorded. *(Phase 1)*
2. **Index** with the existing pipeline. *(Phase 2)*
3. **Extract** terms with an LLM over the full text; every value needs a page + verbatim quote which is string-verified against the source; unverifiable values are flagged, not stored as facts. *(Phase 2)*
4. **Review/correct** extracted fields; corrections are kept with history. *(Phase 2)*
5. **Record outcomes** manually. *(Phase 4)*
6. **Evaluate a proposal**: extract, retrieve similar historical contracts (scoped by `doc_ids`, historical only), pull linked outcomes, compare per category, generate an evidence-referenced report. *(Phases 3 and 5)*

Insights are computed with deterministic queries; the LLM only phrases results. Observed
patterns are labelled as correlations, not causes.

## 6. Phased implementation plan

| Phase | Scope |
|---|---|
| **1** | Branding, navigation shell, real-data dashboard, contract repository (upload, metadata, search/filter), lifecycle/versioning foundations, vendor foundations, security hardening (auth, authorisation, redacted logging, LLM transparency page) |
| 2 | Indexing, structured extraction with provenance and review UI, document-grounded QA with page citations |
| 3 | Proposal upload/extraction, historical retrieval, per-category comparison [R3] |
| 4 | Outcome entry and linkage, pattern queries, vendor performance from outcomes, dashboard widgets |
| 5 | Evidence-gated recommendations, reports/export |

Each phase ends with: files changed, features, reused functionality, tests run, what is
incomplete, and run instructions.

## 7. Technical risks and limitations

- Gradio limits how "SaaS-like" the UI can look; DealWise styles it with CSS rather than replacing the framework.
- `ktem/pages/chat/__init__.py` is a ~1,450-line monolith; DealWise does not modify it.
- Alembic is off; later schema changes to `dw_` tables need a small migration approach.
- Extraction accuracy depends on parse quality (scans need OCR); DOCX has no reliable page numbers, so page references are PDF-only.
- Clauses can span chunk boundaries, so extraction reads full text, not top-k chunks; long contracts cost more LLM calls.
- Values in different currencies are **never summed**; no exchange rates are invented.
- Real-LLM extraction quality cannot be verified without a provider key; LLM-dependent code is tested with deterministic fakes.

## 8. Security review [R5]

### Findings in the upstream code

| # | Finding | Severity | Phase 1 action |
|---|---|---|---|
| 1 | Passwords stored as **unsalted SHA-256** (`login.py`, `resources/user.py`, `settings.py`) | High | **Fixed**: salted PBKDF2-HMAC-SHA256; legacy hashes still verify and are upgraded on next successful login |
| 2 | Login page persists the **plaintext password** in browser `localStorage` | High | **Mitigated**: credentials are held in `sessionStorage` by default (cleared when the browser closes); `DEALWISE_PERSIST_LOGIN=true` restores the old behaviour. Recommended long-term: SSO (`KH_SSO_ENABLED`) |
| 3 | Default admin `admin`/`admin` when user management is on | High | **Detected**: a warning is logged at startup and shown to admins in Security & Privacy; `DEALWISE_REQUIRE_STRONG_ADMIN=true` refuses to start with the default password |
| 4 | Upstream *Files* index is shared by all logged-in users | Medium | DealWise contracts are **not** stored in it in Phase 1. Phase 2 will use a DealWise-managed index whose retrieval is always filtered by the caller's authorised contract ids |
| 5 | Upstream `print()` calls log retrieval scopes and citation function output (which can contain contract text) | Medium | Not reached in Phase 1 (no contract text is processed). **Must be resolved before Phase 2** routes contract text through those paths |
| 6 | Browser loads third-party scripts/fonts (Google Fonts, skypack, jsDelivr, cdnjs) | Low–Medium | DealWise theme uses system fonts (no font request). The scripts used by the PDF viewer / mind-map remain and are disclosed in Security & Privacy |
| 7 | `KH_GRADIO_SHARE=true` would expose the app on a public tunnel | Medium | Surfaced in Security & Privacy if enabled |
| 9 | No login throttling (unlimited password guesses) | Medium | **Fixed**: in-memory lockout after 5 failures per username (per-process; use SSO or a shared store for multi-replica deployments) |
| 10 | Start-up makes outbound requests unrelated to contracts (tiktoken vocabulary from `openaipublic.blob.core.windows.net`, a Mistral tokenizer from `huggingface.co`) | Low | Not changed. No contract data is involved. Note for air-gapped installs: pre-seed these caches / set `HF_HUB_OFFLINE=1` |
| 8 | User identity for authorisation | — | `user_id` is Gradio server-side `State`, set only by the login function; DealWise re-reads the `User` row (admin flag) on **every** request rather than trusting cached flags |

### DealWise authorisation model (Phase 1)

- Single organisation (`org_id="default"`), designed for more.
- Each contract has an **owner** and a **visibility**: `organization` (all signed-in users) or `private` (owner and admins).
- Reads, list queries, dashboard aggregates, search, vendor contract counts and file downloads are all computed from the set of contracts the caller may view. A user can never obtain a count or value that includes contracts they cannot see.
- Edit/delete: owner or admin. Vendor merge/delete: admin only.
- When user management is disabled (single-user mode), the implicit `default` user is treated as admin.
- Admins can see all contracts in the organisation (documented decision; configurable later).
- Uploaded files are saved under a DealWise-controlled directory with a generated name. The directory is passed to Gradio as a `blocked_path` (defence in depth). In testing with the locked Gradio version, files in the working directory were **not** served by default (HTTP 403) while an allow-listed file was (HTTP 200), and the stored contract file was refused; the block protects against configurations or versions that do serve the working directory. Downloads re-check authorisation, are audit-logged, and are copied to a temporary folder that is purged after one hour.

### LLM transparency & logging

- A *Security & Privacy* page lists every configured LLM / embedding provider, whether it is local or external (by endpoint host), and states precisely which DealWise actions send contract content to a model. **Phase 1 sends no contract content to any model.**
- A logging filter redacts API keys, bearer tokens and common secret patterns; DealWise code never logs contract text, extracted values or file contents — only ids and field names.

## 9. Licensing & attribution

- `LICENSE.txt` (Apache-2.0) unchanged; all upstream file headers untouched.
- `NOTICE` added to credit upstream and describe modifications (Apache-2.0 §4).
- Upstream README preserved below the DealWise section.
- Internal Python package names (`kotaemon`, `ktem`) are intentionally **not** renamed.

## 10. Phase 1 status (implemented and verified)

Implemented: branding/theme (system fonts, no external font requests), Overview, Contracts
(upload, metadata, search, filters, download, delete, visibility), lifecycle linking and
active-version lookup, Vendors (central records, aliases, near-duplicate confirmation, admin merge),
Security & Privacy page, PBKDF2 hashing with transparent upgrade, login throttling, session-only
browser credentials, startup default-admin check, redacting log filter, audit log, `dw_` tables with
a forward-column migration helper.

Verification performed:

* 49 DealWise unit tests (authorisation, lifecycle, vendors, metrics, historical/proposal
  separation, redaction, migrations) and 6 password tests; 27 other upstream ktem tests still pass.
  (`ktem_tests/test_qa.py` was already broken upstream: it imports a module that does not exist.)
* The real app was launched and driven over HTTP with separate admin, standard-user and signed-out
  sessions (29 checks): uploads, duplicate rejection, validation, amendments, filters, dashboard
  figures, vendor de-duplication, per-user visibility of lists/totals/vendors, legacy-hash upgrade,
  lockout, file-serving block.
* UI handlers called directly for detail/edit/save/download/active-version/permissions.
* `DEALWISE_ENABLED=false` launched: title, tabs and theme match stock Kotaemon.

Not verified: appearance in a real browser (no browser was available in the test environment), the
Docker and `scripts/` install paths, and anything requiring a live LLM provider (nothing in Phase 1
calls one).

Known limitations carried into later phases: contract fields are manual until Phase 2; vendor
near-match detection is conservative (e.g. a typo inside a legal suffix such as "Corpp" is treated as
a different vendor; use admin merge); the throttle and Gradio session state are per-process; filtering
and metrics are computed in application code, which is fine for thousands of contracts, not millions;
admins can read private contracts (by design, documented above); the upstream Chat/Files index is not
subject to DealWise visibility rules.
