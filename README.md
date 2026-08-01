# JLL-AI-Product-Engineer---Case-Study
# charity-donor-outreach (rewritten skill)

This repo contains my rewrite of the `charity-donor-outreach` skill from the AI Product Engineer case study, submitted for review.

## What this is

The original skill generated personalized fundraising letters from a hardcoded, in-prompt donor table, with no data validation, an ambiguous ask-amount formula, and several ethics/compliance issues (e.g. instructing the AI to claim an unconfirmed donation match). This rewrite addresses those issues — full write-up sent separately — and moves all deterministic calculation (donor tier, ask amount) out of the prompt and into a standalone, testable script.

## Structure

```
.
├── skill.md
├── scripts/
│   └── calculate_donor_metrics.py
└── sample_donors.csv
```

| File | Purpose |
|---|---|
| `skill.md` | The rewritten skill definition. Describes the end-to-end letter-generation workflow: collecting campaign inputs, computing tier/ask amount, drafting letters, and producing a human-review report. Letters are always drafted for review — the skill never sends anything. |
| `scripts/calculate_donor_metrics.py` | The deterministic calculation engine referenced by `skill.md`. Computes each donor's tier and ask amount from their gift history, and cross-checks any tier/lifetime-total/last-gift-year values already present in the donor file, flagging disagreements rather than silently trusting or overwriting either source. |
| `sample_donors.csv` | A transcription of the original case study's 50-donor table (same values, same donors) — used to test `calculate_donor_metrics.py` end to end. No values were altered or added; donors are identified by first + last name, matching how the source data was organized. |

## Quick start

No external dependencies — pure Python 3 standard library.

```bash
python scripts/calculate_donor_metrics.py sample_donors.csv \
    --campaign-type annual \
    --campaign-date 2024-06-01 \
    --out-dir ./output
```

`--campaign-type` accepts `emergency`, `annual`, `capital`, or `event`.

### Output

Running the command above writes three files to `./output/`:

- **`donor_metrics.json`** — full per-donor detail: computed tier, ask amount, salutation, and any flags
- **`review_summary.csv`** — flat table for human review (one row per donor)
- **`exceptions.csv`** — donors excluded from generation due to missing/invalid required fields, with reasons
