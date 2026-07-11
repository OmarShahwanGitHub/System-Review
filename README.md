# srx — systematic review extraction engine

Engine for automating the **systematic review** extraction + synthesis workflow.
Phase 0 (this repo state): PDFs in → evidence spreadsheet out. Dual-pass AI
data extraction with quote/page provenance on every value; the field schema is
proposed by the model from your PDFs (or drafted from a template sheet if you
have one), and every run writes `evidence.csv` + a provenance annex. A human
gold-standard sheet is optional — it enables `sr bench` scoring.
Spec: `docs/superpowers/specs/2026-07-10-sr-extraction-engine-design.md`.

## Setup

```bash
/usr/bin/python3.12 -m venv ~/.venvs/srx
~/.venvs/srx/bin/pip install -e ".[dev]"
export ANTHROPIC_API_KEY=sk-ant-...
alias sr=~/.venvs/srx/bin/sr
```

Run tests (no network, no API key needed): `~/.venvs/srx/bin/pytest -q`

## Project layout

Projects live under `projects/` (gitignored — study PDFs are copyrighted and
stay local; they are sent only to the Anthropic API):

```
projects/my-review/
  studies/            # included-study PDFs; filename stem = study id
  schema.yaml         # evidence-table field definitions
  gold/gold.csv       # OPTIONAL: your human extraction (enables sr bench)
  gold/mapping.yaml   # OPTIONAL: gold column -> schema field mapping
  config.yaml         # optional: extract_model, judge_model, cost_cap_usd, pricing
```

**Study id convention:** `studies/smith-2019.pdf` ↔ a `gold.csv` row with
`study_id` = `smith-2019`.

## Workflow

```bash
cd projects/my-review
sr schema init            # model proposes schema.yaml from your PDFs
                          # (uses a template CSV's headers instead when
                          #  --from is given or gold/gold.csv exists)
$EDITOR schema.yaml       # review: types, units, allowed values, descriptions
sr extract                # dual-pass extraction -> runs/<id>/evidence.csv
                          # + provenance.csv + extraction.json
sr bench                  # OPTIONAL: score vs gold, write runs/<id>/report.html
sr report                 # re-render the latest benched report
sr extract --resume <id>  # continue a cost-capped or interrupted run
```

Every value the engine accepts carries provenance (verbatim quote, page,
method, confidence) — hover over a value in the report to see it, or open
`provenance.csv`. Values whose quotes cannot be verified in the PDF text are
flagged for human review (marked `[FLAGGED: reason]` in `evidence.csv`), never
silently accepted, and flagged values score as "missed" in the benchmark.

Pricing for cost logging lives in `srx/claude_client.py` (`DEFAULT_PRICING`) —
verify against current Anthropic pricing and adjust if needed.

## Phase 0 success gate

≥95% agreement on objective numeric fields after adjudication, zero
unanchored numeric values accepted — measured by `sr bench` against your own
previous review. Prompt changes bump `PROMPT_VERSION`; every bench run diffs
against the previous one in the report.
