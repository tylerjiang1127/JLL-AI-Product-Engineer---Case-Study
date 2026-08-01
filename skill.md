---
name: charity-donor-outreach
description: >-
  Generate personalized fundraising outreach letters from a donor data file
  (CSV or similar) for a nonprofit campaign. Use this skill when a user
  uploads a donor list and asks to draft donor letters, appeal letters, or a
  fundraising mailing — for example "generate letters for our annual fund
  from this donor CSV." Do not use for general email writing, reports, or
  communication tasks unrelated to donor outreach.
compatibility: >-
  Prefers an environment with Python code execution (used for deterministic
  tier and ask-amount calculations via scripts/calculate_donor_metrics.py).
  If code execution is unavailable, fall back to the Backup Plan and
  compute by hand, following the same steps in the same order.
---

# Charity Donor Outreach Letter Generator

Drafts personalized fundraising letters from an uploaded donor data file.
This skill **drafts letters for human review — it never sends anything**.
Every batch must be reviewed and approved by fundraising staff before any
letter is delivered to a donor.

## Bundled files

```
charity-donor-outreach/
├── SKILL.md
└── scripts/
    └── calculate_donor_metrics.py   — deterministic tier & ask-amount engine
```

All tier and ask-amount math lives in `calculate_donor_metrics.py`, not in
this document or in the model's head. Computation (arithmetic, thresholds,
date logic) is handled by code, which is deterministic and testable;
judgment calls (tone, phrasing, which giving-history detail to highlight)
are left to the model. Treat the script's output as ground truth — do not
recompute or "sanity check" its numbers by hand when code execution is
available.

## Data handling

Donor records are personal data. Use only the file the user uploads in this
session, only for generating this batch of letters. Do not retain, restate,
or export donor data beyond the requested outputs. Never invent, guess, or
"fill in" any donor fact that is not in the file.

## Step 0 — Collect required inputs

Before generating anything, confirm the following with the user. If any are
missing, ask — do not guess:

1. **Donor data file** (CSV preferred; see schema below)
2. **Charity name** (exactly as it should appear in letters)
3. **Campaign type**: Emergency Appeal / Annual Fund / Capital Campaign /
   Event Fundraiser (if unknown, confirm with the user; do not silently default)
4. **Campaign date** (defaults to today) — used for lapsed/loyalty calculations
5. **Donation URL**
6. **Sender**: name and title of the real staff member signing the letters.
   Optionally, a mapping of relationship managers to specific donors (real
   staff only — never invent a name). If no manager is mapped to a Platinum
   donor, flag that donor for review instead of signing with a placeholder.
7. **Matching gift details, only if a match is confirmed**: sponsor name,
   match ratio, deadline. If not provided, letters must not mention matching
   in any form.
8. **Event registration count** (Event Fundraiser campaigns only) — use the
   real number provided; never estimate one.

## Step 1 — Read and validate the donor file

Expected columns per donor:

| Field | Required | Notes |
|---|---|---|
| `first_name`, `last_name` | yes | used together as the donor's identifier |
| `title` | no | e.g., Ms., Mr., Dr. — used only if present |
| `region` | no | |
| `gift_history` | yes | e.g. `"2010: $25,000, 2013: $30,000"` |
| `volunteer` | no | boolean; treat missing as false |
| `tier_label` | no | advisory only; see Step 2 |
| `largest_gift`, `lifetime_total`, `last_gift_year` | no | advisory only; see below |

Validation rules:

- Parse `gift_history` and **compute** largest single gift, lifetime total,
  and last gift year — these computed values are what's used for all
  downstream tier and ask-amount math.
- If the file also provides `largest_gift`, `lifetime_total`, or
  `last_gift_year`, keep them in the output as given, but **cross-check**
  them against the computed values. Disagreement doesn't mean either value
  gets silently changed — flag the record for human review instead.
- A record missing any required field is **excluded from generation** and
  added to the exceptions report. Never proceed on assumptions.
- Treat `first_name` + `last_name` together as the donor's identifier
  (matching how the source data is organized); flag exact duplicates.

## Step 2 — Compute tier and ask amount

Run the calculation script instead of computing by hand:

```bash
python scripts/calculate_donor_metrics.py <donor_csv> \
    --campaign-type emergency|annual|capital|event \
    --campaign-date YYYY-MM-DD \
    --out-dir ./output
```

This produces:

- `output/donor_metrics.json` — per-donor tier, ask amount, salutation, and
  flags (tier mismatches, cap violations)
- `output/review_summary.csv` — flat table for human review
- `output/exceptions.csv` — donors excluded due to missing/invalid data,
  with reasons

**Use these numbers as-is.** Do not recompute tiers or ask amounts, round
differently, or "fix" a number that looks off — if something looks wrong,
surface it as a flag for human review rather than silently correcting it.

If code execution is unavailable in the current environment, follow the
**Backup Plan** at the end of this document and compute every value by hand,
in the exact order given, for every donor — do not skip steps or shortcut
the arithmetic.

### Tier treatments (for letter tone, not calculation)

| Tier | Tone | Tier-specific line |
|---|---|---|
| Platinum | Very formal | Naming opportunity (e.g., a room or bench) |
| Gold | Warm, professional | Legacy giving options |
| Silver | Friendly | Monthly giving upgrade |
| Bronze | Casual, encouraging | Peer fundraising pages |
| Lapsed | Warm, welcoming-back (not apologetic or guilt-based) | Simple invitation to reconnect |

## Step 3 — Salutation

Use the `salutation` field from the script's `donor_metrics.json` output as-is.
It already follows the rule: `Dear [Title] [Last Name],` for Platinum/Gold
when a title exists (otherwise full name), `Hi [First Name],` for
Silver/Bronze, and `Dear [First Name],` for Lapsed. **Never guess a title or
gender from a name** — this is exactly the kind of judgment call the script
avoids by only using what's in the data.

## Step 4 — Campaign paragraph (2 sentences)

- **Emergency Appeal:** urgency grounded in the real situation the user
  describes. Mention matching **only** if confirmed match details were
  provided in Step 0, and state them accurately (sponsor, ratio, deadline).
  Never imply a match otherwise.
- **Annual Fund:** consistency and community; mention the donor's giving
  streak only if the computed history shows consecutive-year gifts.
- **Capital Campaign:** legacy and permanence; building metaphors welcome.
- **Event Fundraiser:** fun and social proof, using the real registration
  count from Step 0.
- Every factual statement in the paragraph must trace to the donor file or
  a Step 0 input.

## Step 5 — Generate outputs (files + review report)

Do not paste letters into the chat. Produce:

1. **One HTML file per donor** using the template below, named
   `letters/[tier]/[last_name]_[first_name].html`
2. Pass through the script's `review_summary.csv` and `exceptions.csv`
   unchanged so staff can review computed tiers, asks, and flags
3. An in-chat summary that includes, at minimum:
   - Counts by tier
   - Number of exceptions (excluded from generation), with reasons
   - **Every flagged record named individually** — donor name, flag reason
     (e.g. "tier_label mismatch," "exceeds ask cap"), and the computed value
     in question. Do not summarize flags as a bare count ("4 records
     flagged") without listing who they are: staff should not have to open
     `review_summary.csv` to find out which donors need a second look.
   - A clear reminder that letters require human review and approval before
     sending

A record with a flag still gets a letter — flags are not the same as
exceptions. The point of computing them is defeated if they're logged to a
file but never said out loud, so treat the chat summary, not the CSV, as
the primary place a busy staff member will actually see them.

## HTML Letter Template

```html
<html>
<body style="font-family: Georgia; padding: 30px; max-width: 600px; color: #222;">

  <p style="text-align:right; color: #888;">[DATE]</p>

  <p>[SALUTATION]</p>

  <p>On behalf of everyone at <strong>[CHARITY NAME]</strong>, thank you for
  your generosity. Your lifetime support of
  <strong>$[LIFETIME_GIVING]</strong> has made a real difference.</p>

  <p>[CAMPAIGN_PARAGRAPH]</p>

  <p>Today, I'd like to invite you to make a gift of
  <strong>$[ASK_AMOUNT]</strong>. [TIER_SPECIFIC_LINE]</p>

  <p>To give, simply reply to this email or visit our donation page at
  <strong>[DONATION_URL]</strong>.</p>

  <p>With gratitude,<br>
  <strong>[SENDER_NAME]</strong><br>
  [SENDER_TITLE], [CHARITY NAME]</p>

</body>
</html>
```

All placeholders must be filled from the donor file or Step 0 inputs. If a
value is unavailable, the letter goes to the exceptions report — never send
a letter with a blank, guessed, or invented value.

---

## Backup Plan — Manual calculation fallback (only if code execution is unavailable)

Perform these steps for every donor, in this exact order, by hand.

**Tier** (using the campaign date):
1. Lapsed — last gift more than 36 months before the campaign date.
   Overrides the tiers below for messaging/ask purposes (still record the
   giving-level tier for reporting).
2. Platinum — lifetime total ≥ $50,000
3. Gold — lifetime total $10,000–$49,999.99
4. Silver — lifetime total $1,000–$9,999.99
5. Bronze — lifetime total < $1,000

If the file's `tier_label` disagrees with the computed tier, use the
computed tier and flag the mismatch.

**Ask amount:**
1. Base: Platinum 40% / Gold 25% / Silver 15% of largest single gift;
   Bronze flat $150; Lapsed flat $50.
2. If the donor gave in the calendar year immediately before the campaign
   date, multiply by 1.10.
3. If the donor is a volunteer, add $100.
4. If the campaign is an Emergency Appeal, multiply by 1.2.
5. Round once, to the nearest $50, minimum $50.
6. If the result exceeds 120% of the largest single gift (percentage tiers
   only), flag for review instead of using it as-is.

Do not skip steps, reorder them, or round more than once.
