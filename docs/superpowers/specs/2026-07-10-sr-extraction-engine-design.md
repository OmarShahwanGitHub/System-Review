# Systematic Review Extraction Engine — Design Spec

**Date:** 2026-07-10
**Status:** Approved (brainstorm with Omar, 2026-07-10)
**Amendment (2026-07-10, final whole-branch review):** (1) The "temperature 0 everywhere" rule is retired — the current Anthropic API rejects non-default temperature on the Fable 5 / Sonnet 5 models (400); no sampling parameters are sent, and reproducibility rests on prompt-version pinning plus benchmark regression, which was always the operative mechanism. (2) Cost accounting explicitly includes prompt-cache tokens (cache-creation at 1.25×, cache-read at 0.1× input price) so the cost cap cannot fail open on PDF-heavy runs.
**Scope of this spec:** Phases 0–1 (extraction engine + validation benchmark + meta-analysis core). Implementation planning begins with Phase 0; Phase 1 is planned once the Phase 0 gate passes. Phases 2–4 are committed and sketched here; each gets its own spec before build. Phase 5 (web app) gets its own brainstorm → spec → plan cycle.

## 1. Product context

The long-term product is an app for medical researchers that automates the systematic review process. The v1 wedge is **extraction + synthesis**: users arrive with their already-screened included-study PDFs and the app does data extraction, risk-of-bias assistance, GRADE, meta-analysis, and manuscript-ready outputs — the weeks-long tail of every review, and the least-well-solved part of the market (Covidence/Rayyan own screening; LLM-native tools lack publication-grade rigor).

**Target user:** publication-grade academic teams (PhD students, clinician-researchers, methodologists) writing journal-bound systematic reviews and meta-analyses. Industry HEOR/regulatory is a v2 up-market move.

**Build strategy (Approach A):** engine first, app second. The extraction engine is built as a standalone, testable Python core with a CLI, validated against a real review, and only then wrapped in a web product. The engine stays UI-agnostic throughout so the app phase wraps it without rework.

**Terminology rule:** all user-facing text says "systematic review" — never "system review."

## 2. Success criteria

1. **Phase 0 gate (validation run):** the engine extracts from the included-study PDFs of Omar's previous review and is scored against his human extraction sheet. First run establishes the baseline; we iterate until the engine is trustworthy as a second extractor. Target bar: **≥95% agreement on objective numeric fields after adjudication, and zero unanchored (hallucinated) numeric values accepted.** The bar may be revised after the baseline run, explicitly and in writing, not silently.
2. **Phase 1 gate:** end-to-end reproduction of the pooled estimates and forest plot from that same published review — PDFs in, matching forest plot out (pooled estimates and CIs matching the published values within rounding).
3. Then: real external researchers in beta (web app phase), then paying users.

## 3. Non-goals for this spec

- No web UI, auth, billing, collaboration, or hosting (Phase 5).
- No screening, search, or deduplication features (different wedge, later product stages).
- No RoB/GRADE/manuscript implementation yet (Phases 2–4; sockets described in §11).
- Detailed prompt wording, exact dependency versions, and package internals belong to the implementation plan, not this spec.

## 4. Core data rule: provenance on every value

Every extracted value is stored as:

```
{ value, unit, quote, page, method (pass-agreed | adjudicated | human),
  anchor_status (verified | vision-only | failed), confidence }
```

No naked numbers, ever. **Presentation rule (user amendment):** provenance is never shown inline in tables. Displayed tables are clean values only; provenance appears on demand — click-to-expand in HTML reports, click-to-open-PDF-at-highlight in the later web app. Exports split the same way: a clean evidence-table sheet plus a linked provenance annex sheet.

## 5. Stack

- Python 3.12 package (working name `srx`) exposing a `sr` CLI: `sr extract`, `sr bench`, `sr report`.
- Storage: SQLite + plain files in a project directory. No server.
- Claude API for extraction — Fable-class model for the hard reads, cheaper models for utility steps. No sampling parameters are sent (current API rejects non-default temperature on these models — see Amendment); reproducibility via prompt-version pinning + benchmark regression.
- Cost: per-run token/cost logging including prompt-cache tokens (creation 1.25×, read 0.1× input price); configurable cost cap (default $20/run) that aborts cleanly at the checkpoint boundary.
- Phase 1 adds R + `metafor` as a subprocess dependency (§10).

Project directory layout:

```
project/
  studies/*.pdf          # user's included studies (stay local; never uploaded anywhere but the model API)
  schema.yaml            # evidence-table field definitions
  gold/gold.csv          # user's human extraction (any tabular source, converted)
  gold/mapping.yaml      # column → schema-field mapping
  runs/<run_id>/         # append-only: extraction.json, report.html, log.jsonl
  sr.sqlite              # run registry, provenance, benchmark scores
```

## 6. Pipeline components

Each component is independently testable with a documented interface.

1. **Ingestion.** Each PDF is (a) sent natively to the Claude API so tables, figures, and scanned pages are readable, and (b) parsed locally with pdfplumber to build a per-page text layer used for quote verification. PDFs with no usable text layer are marked `scanned`.
2. **Schema.** `schema.yaml` defines evidence-table fields: name, type (numeric | int | categorical | text | bool), canonical unit + accepted units, allowed values, extraction guidance, and level (study-level vs arm-level vs outcome × timepoint × arm). The outcome/arm structure exists so Phase 1 can compute effect sizes without re-modeling. v0 schema is generated by assisted mapping from the column headers of the user's extraction sheet, then hand-refined once.
3. **Extractor.** Per study, schema-driven structured-output calls to Claude. **Two independent passes** with different prompt framings and field orderings (optional cross-model mode: Fable + Sonnet). Every value must be returned with its supporting quote and page. Claude's citations mode is used as a strengthener where available, but the design does not depend on it — verification is local (component 4).
4. **Anchor verifier.** Each value's quote is fuzzy-matched (whitespace/hyphenation-normalized, ≥0.9 similarity) into the pdfplumber text layer of the claimed page (±1 page tolerance). Match → `verified`. No match on a text-bearing PDF → `failed` and the value is flagged, never silently accepted. Scanned PDFs → `vision-only` (page-level anchor) and auto-flagged for human verification. This is the anti-hallucination gate.
5. **Reconciler.** Normalizes both passes (thousands separators, percent vs proportion, unit conversions from a small table, CI and p-value format variants), then compares field-by-field. Agree → accepted (`pass-agreed`). Disagree → a third adjudication call sees both candidates plus targeted source excerpts and re-reads; if it decisively matches one, accept as `adjudicated`; otherwise the field enters the human-flag queue. Disagreements are never averaged.
6. **Benchmark harness.** Loads `gold.csv` via `mapping.yaml`, scores per field type — numeric: exact match at the gold value's reported precision (per-field tolerance override allowed in schema); categorical: exact after canonicalization; free text: model-judge semantic equivalence with human spot-check. Emits per-field × per-study results.
7. **Run store.** Append-only run records (inputs hash, schema version, model IDs, prompts hash, costs, scores) in SQLite. Every prompt/model change re-runs the benchmark and `sr report` diffs against the prior run — the benchmark doubles as a permanent regression suite and the audit trail starts at day zero.

## 7. Benchmark error taxonomy

Every gold-vs-engine mismatch is classified, because the fixes differ:

| Class | Meaning |
|---|---|
| `wrong-value` | Engine anchored a value but it differs from gold |
| `unit-mismatch` | Same quantity, unit/format normalization failed |
| `missed` | Gold has a value, engine returned null |
| `spurious-hallucinated` | Engine value with `failed` anchor, gold null |
| `spurious-anchored` | Engine value with verified anchor, gold null — possible gold omission |
| `gold-suspect` | Engine value verified in source and contradicting gold — human adjudicates; human extractions contain errors too, and the report surfaces both sides |

Scoring denominator: only *accepted* values (§4 `method`) are scored for agreement. Values flagged by the anchor gate or left unresolved by the reconciler are excluded from the agreement rate and counted as `missed` (with a flagged notation) — they would go to a human anyway, so they can't inflate the score.

The HTML report shows the agreement matrix, taxonomy counts, and drill-down with provenance-on-click (§4 presentation rule).

## 8. Error handling

- **Per-study checkpointing:** a 40-PDF run resumes after any failure; it never restarts from zero.
- API errors: retry with exponential backoff; a study that exhausts retries is marked `errored` and skipped, not fatal to the run.
- Cost cap reached: abort cleanly at the next checkpoint with a partial-run report.
- Unreadable/scanned PDFs: vision path with a warning and `vision-only` anchors (§6.4).
- Anchor failure: flag, never accept (§6.4). No silent degradation anywhere.

## 9. Testing

- Unit tests: normalizer, anchor matcher, reconciler, schema mapping, gold loader.
- Synthetic fixture set: small generated PDFs with known planted values, so CI never depends on copyrighted study PDFs.
- Integration + regression: the benchmark itself, diffed run-over-run.
- Stats (Phase 1): golden tests against published worked examples (metafor documentation datasets, Cochrane Handbook examples) — correctness is checked against the literature, not against ourselves.

## 10. Phase 1 — meta-analysis core

The verified evidence table feeds R `metafor` via a subprocess with a JSON contract (JSON in: effect measure, per-study data, options; JSON + SVG out). `metafor` is the field's reference implementation, letting users write "analyses performed with metafor" in methods sections; Python's meta-analysis ecosystem is too thin for publication-grade work, so this one non-Python dependency earns its keep.

Features: effect sizes for common measures (OR / RR / HR, MD / SMD, proportions); random-effects pooling as default (REML with Knapp–Hartung) with fixed-effect option; I² / τ²; subgroup and sensitivity analyses; forest and funnel plots as SVG; Egger's test only where study count justifies it (n ≥ 10). Every number in any downstream text is injected programmatically from analysis objects — the model writes sentences, never statistics.

## 11. Phases 2–4 (committed, own specs before build)

- **Phase 2 — Risk of bias:** RoB 2 (RCTs) and ROBINS-I (non-randomized). AI drafts per-domain judgments *with supporting quotes* (same provenance rule); a human signs off per domain before any judgment counts; standard traffic-light figure output.
- **Phase 3 — GRADE:** per-outcome certainty drafted from evidence the system already holds (RoB summary → risk of bias; I² → inconsistency; CI width/sample size → imprecision; funnel results → publication bias), human adjudication on every downgrade; Summary of Findings table.
- **Phase 4 — Manuscript outputs:** publication-formatted study-characteristics table, SoF table, figure exports (SVG/PNG/EPS), drafted methods + results paragraphs with programmatically injected numbers, PRISMA-flow entry (screening happened outside the tool, so the user supplies counts).

Sockets already present for these: outcome-structured schema (§6.2), provenance store (§4), append-only run registry (§6.7).

## 12. Phase 5 — web app (separate cycle)

Own brainstorm → spec → plan once the engine passes its gates. Wraps the engine as a library/service: projects, upload, click-to-reveal verification UI (the §4 presentation rule becomes the core interaction), team collaboration, auth, billing — the road to beta users, then paying users.

## 13. Risks

- **Model variance across PDF quality/eras** — mitigated by the benchmark loop, dual-pass + adjudication, and the anchor gate; scanned pre-2000 PDFs will need the vision path and human verification.
- **Gold-standard errors** — expected; `gold-suspect` class turns them into a feature (the tool finds human extraction errors) rather than a scoring dispute.
- **R/metafor dependency** — documented install now, containerized in the app phase.
- **API feature drift** (citations mode, PDF handling) — design depends only on structured output + local anchor verification; API extras are strengtheners.
- **Copyright/privacy** — study PDFs stay in the local project directory and are sent only to the model API; nothing is redistributed or stored elsewhere in Phases 0–4.
