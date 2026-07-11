# srx — systematic review extraction engine

Engine for automating the **systematic review** extraction + synthesis workflow.
Phase 0 (this repo state): dual-pass AI data extraction with quote/page
provenance on every value, benchmarked against a human gold-standard sheet.
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
  gold/gold.csv       # your human extraction (benchmark reference)
  gold/mapping.yaml   # gold column -> schema field mapping
  config.yaml         # optional: extract_model, judge_model, cost_cap_usd
```

**Study id convention:** `studies/smith-2019.pdf` ↔ a `gold.csv` row with
`study_id` = `smith-2019`.

## Workflow

```bash
cd projects/my-review
sr schema init            # draft schema.yaml from gold/gold.csv headers
$EDITOR schema.yaml       # refine types, units, allowed values, descriptions
sr extract                # dual-pass extraction (default cost cap: $20/run)
sr bench                  # score vs gold, write runs/<id>/report.html
sr report                 # re-render the latest benched report
sr extract --resume <id>  # continue a cost-capped or interrupted run
```

Every value the engine accepts carries provenance (verbatim quote, page,
method, confidence) — visible in the report by clicking a value. Values whose
quotes cannot be verified in the PDF text are flagged for human review, never
silently accepted, and flagged values score as "missed" in the benchmark.

Pricing for cost logging lives in `srx/claude_client.py` (`DEFAULT_PRICING`) —
verify against current Anthropic pricing and adjust if needed.

## Phase 0 success gate

≥95% agreement on objective numeric fields after adjudication, zero
unanchored numeric values accepted — measured by `sr bench` against your own
previous review. Prompt changes bump `PROMPT_VERSION`; every bench run diffs
against the previous one in the report.
