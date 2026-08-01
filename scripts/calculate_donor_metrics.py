#!/usr/bin/env python3
"""
calculate_donor_metrics.py

Deterministic calculation engine for the charity-donor-outreach skill.
Reads a donor CSV file, computes each donor's tier, ask amount, and any
data-quality flags, and writes structured output for the skill to consume.

This script is the single source of truth for tier and ask-amount logic.
The skill (and the LLM running it) must not recompute or override these
numbers by hand — it should read them from this script's output.

Usage:
    python calculate_donor_metrics.py <donor_csv> \
        --campaign-type emergency|annual|capital|event \
        --campaign-date YYYY-MM-DD \
        [--out-dir OUTPUT_DIR]

Outputs (written to --out-dir, default "./output"):
    donor_metrics.json   - one record per donor with computed fields
    review_summary.csv   - flat summary table for human review
    exceptions.csv        - donors excluded from generation, with reasons
"""

import argparse
import csv
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

REQUIRED_FIELDS = ["first_name", "last_name", "gift_history"]

TIER_PERCENT = {
    "Platinum": 0.40,
    "Gold": 0.25,
    "Silver": 0.15,
}
FLAT_ASK = {
    "Bronze": 150,
    "Lapsed": 50,
}

LAPSED_MONTHS_THRESHOLD = 36

# Matches "YYYY: $X,XXX" pairs anywhere in the string, tolerating the
# original "$" and thousands-comma formatting (e.g.
# "2010: $25,000, 2013: $30,000"). This lets the donor file keep gift
# history in the same human-readable format as the source data instead of
# requiring donors' records to be reformatted.
GIFT_PATTERN = re.compile(r"(\d{4})\s*:\s*\$?\s*([\d,]+)")


def parse_money(raw: str):
    """Parse a '$12,345' or '12345' style string into a float, or None."""
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    cleaned = raw.replace("$", "").replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_gift_history(raw: str):
    """Parse 'YYYY: $X,XXX, YYYY: $X,XXX' into a list of (year, amount)."""
    gifts = []
    if not raw:
        return gifts
    for year_str, amount_str in GIFT_PATTERN.findall(raw):
        gifts.append((int(year_str), float(amount_str.replace(",", ""))))
    return gifts


def months_between(d1: date, d2: date) -> int:
    return (d1.year - d2.year) * 12 + (d1.month - d2.month)


def compute_tier(lifetime_total: float, last_gift_year: int, campaign_date: date):
    """
    Returns (tier, is_lapsed, giving_level_tier).
    Lapsed is evaluated based on recency and overrides the giving-level
    tier for messaging/ask purposes, but the giving-level tier is retained
    for reporting.
    """
    if last_gift_year is None:
        giving_level_tier = "Bronze"
    elif lifetime_total >= 50000:
        giving_level_tier = "Platinum"
    elif lifetime_total >= 10000:
        giving_level_tier = "Gold"
    elif lifetime_total >= 1000:
        giving_level_tier = "Silver"
    else:
        giving_level_tier = "Bronze"

    is_lapsed = False
    if last_gift_year is not None:
        last_gift_date = date(last_gift_year, 12, 31)
        if months_between(campaign_date, last_gift_date) > LAPSED_MONTHS_THRESHOLD:
            is_lapsed = True

    effective_tier = "Lapsed" if is_lapsed else giving_level_tier
    return effective_tier, is_lapsed, giving_level_tier


def compute_ask(effective_tier: str, largest_gift: float, gave_last_year: bool,
                 is_volunteer: bool, campaign_type: str):
    """
    Ask amount formula, applied in a fixed order:
      1. base (percentage of largest gift, or flat)
      2. loyalty uplift (x1.10) if gave in the calendar year before campaign
      3. volunteer bonus (+$100 flat)
      4. emergency multiplier (x1.2) if Emergency Appeal
      5. round once, to nearest $50, minimum $50
      6. cap check: flag if > 120% of largest gift (percentage tiers only)
    """
    if effective_tier in TIER_PERCENT:
        base = largest_gift * TIER_PERCENT[effective_tier]
    else:
        base = FLAT_ASK.get(effective_tier, FLAT_ASK["Bronze"])

    amount = base
    if gave_last_year:
        amount *= 1.10
    if is_volunteer:
        amount += 100
    if campaign_type == "emergency":
        amount *= 1.2

    rounded = max(50, round(amount / 50) * 50)

    exceeds_cap = False
    if effective_tier in TIER_PERCENT and largest_gift > 0:
        if rounded > 1.2 * largest_gift:
            exceeds_cap = True

    return rounded, exceeds_cap


def process_donor(row: dict, campaign_type: str, campaign_date: date):
    flags = []

    missing = [f for f in REQUIRED_FIELDS if not row.get(f, "").strip()]
    if missing:
        return None, f"Missing required field(s): {', '.join(missing)}"

    try:
        gifts = parse_gift_history(row["gift_history"])
    except Exception as e:
        return None, f"Could not parse gift_history: {e}"

    if not gifts:
        return None, "No parseable gifts in gift_history"

    largest_gift = max(amount for _, amount in gifts)
    lifetime_total = sum(amount for _, amount in gifts)
    last_gift_year = max(year for year, _ in gifts)
    gave_last_year = (campaign_date.year - 1) in {year for year, _ in gifts}

    # Cross-check against the file's own largest_gift/lifetime_total/
    # last_gift_year columns, if present. These columns are informational —
    # the values computed from gift_history above are used for all
    # calculations — but a disagreement usually means the file is stale or
    # was hand-edited, so it's surfaced as a flag rather than silently
    # overwritten or ignored.
    file_largest_gift = parse_money(row.get("largest_gift", ""))
    if file_largest_gift is not None and abs(file_largest_gift - largest_gift) > 0.01:
        flags.append(
            f"file largest_gift (${file_largest_gift:,.0f}) disagrees with "
            f"value computed from gift_history (${largest_gift:,.0f})"
        )

    file_lifetime_total = parse_money(row.get("lifetime_total", ""))
    if file_lifetime_total is not None and abs(file_lifetime_total - lifetime_total) > 0.01:
        flags.append(
            f"file lifetime_total (${file_lifetime_total:,.0f}) disagrees with "
            f"value computed from gift_history (${lifetime_total:,.0f})"
        )

    file_last_gift_year = row.get("last_gift_year", "").strip()
    if file_last_gift_year and file_last_gift_year.isdigit() and int(file_last_gift_year) != last_gift_year:
        flags.append(
            f"file last_gift_year ({file_last_gift_year}) disagrees with "
            f"value computed from gift_history ({last_gift_year})"
        )

    is_volunteer = row.get("volunteer", "").strip().lower() in ("yes", "true", "1")

    effective_tier, is_lapsed, giving_level_tier = compute_tier(
        lifetime_total, last_gift_year, campaign_date
    )

    file_tier_label = row.get("tier_label", "").strip()
    tier_mismatch = False
    if file_tier_label:
        if file_tier_label == "Lapsed":
            # File says lapsed: mismatch only if our recency calc disagrees.
            tier_mismatch = not is_lapsed
        else:
            # File gives a giving-level label: compare against computed
            # giving-level tier (independent of lapsed status).
            tier_mismatch = file_tier_label != giving_level_tier
    if tier_mismatch:
        flags.append(
            f"tier_label in file ('{file_tier_label}') disagrees with "
            f"computed tier (giving-level: '{giving_level_tier}', "
            f"lapsed: {is_lapsed})"
        )

    ask_amount, exceeds_cap = compute_ask(
        effective_tier, largest_gift, gave_last_year, is_volunteer, campaign_type
    )
    if exceeds_cap:
        flags.append("ask amount exceeds 120% of largest single gift — review before sending")

    title = row.get("title", "").strip()
    if effective_tier in ("Platinum", "Gold"):
        if title:
            salutation = f"Dear {title} {row['last_name']},"
        else:
            salutation = f"Dear {row['first_name']} {row['last_name']},"
    elif effective_tier == "Lapsed":
        salutation = f"Dear {row['first_name']},"
    else:
        salutation = f"Hi {row['first_name']},"

    record = {
        "first_name": row["first_name"],
        "last_name": row["last_name"],
        "region": row.get("region", ""),
        "largest_gift": largest_gift,
        "lifetime_total": lifetime_total,
        "last_gift_year": last_gift_year,
        "is_volunteer": is_volunteer,
        "giving_level_tier": giving_level_tier,
        "effective_tier": effective_tier,
        "is_lapsed": is_lapsed,
        "tier_mismatch": tier_mismatch,
        "gave_last_year": gave_last_year,
        "ask_amount": ask_amount,
        "exceeds_cap": exceeds_cap,
        "salutation": salutation,
        "flags": flags,
    }
    return record, None


def main():
    parser = argparse.ArgumentParser(description="Compute donor tiers and ask amounts.")
    parser.add_argument("donor_csv", help="Path to donor CSV file")
    parser.add_argument(
        "--campaign-type",
        required=True,
        choices=["emergency", "annual", "capital", "event"],
        help="Campaign type (affects ask amount and messaging)",
    )
    parser.add_argument(
        "--campaign-date",
        default=date.today().isoformat(),
        help="Campaign date as YYYY-MM-DD (default: today)",
    )
    parser.add_argument("--out-dir", default="./output", help="Output directory")
    args = parser.parse_args()

    try:
        campaign_date = datetime.strptime(args.campaign_date, "%Y-%m-%d").date()
    except ValueError:
        print(f"Error: --campaign-date must be YYYY-MM-DD, got '{args.campaign_date}'", file=sys.stderr)
        sys.exit(1)

    donor_csv = Path(args.donor_csv)
    if not donor_csv.exists():
        print(f"Error: file not found: {donor_csv}", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    records = []
    exceptions = []

    with open(donor_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        seen_names = set()
        for row in reader:
            name_key = (row.get("first_name", "").strip().lower(),
                        row.get("last_name", "").strip().lower())
            display_name = f"{row.get('first_name', '').strip()} {row.get('last_name', '').strip()}".strip()

            if name_key != ("", "") and name_key in seen_names:
                exceptions.append({
                    "name": display_name or "(unknown)",
                    "reason": "Duplicate donor (same first + last name already seen)",
                })
                continue
            if name_key != ("", ""):
                seen_names.add(name_key)

            record, error = process_donor(row, args.campaign_type, campaign_date)
            if error:
                exceptions.append({
                    "name": display_name or "(unknown)",
                    "reason": error,
                })
            else:
                records.append(record)

    # Write donor_metrics.json
    with open(out_dir / "donor_metrics.json", "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)

    # Write review_summary.csv
    with open(out_dir / "review_summary.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "first_name", "last_name", "effective_tier",
            "giving_level_tier", "tier_mismatch", "ask_amount",
            "exceeds_cap", "is_volunteer", "gave_last_year", "flags",
        ])
        for r in records:
            writer.writerow([
                r["first_name"], r["last_name"], r["effective_tier"],
                r["giving_level_tier"], r["tier_mismatch"], r["ask_amount"],
                r["exceeds_cap"], r["is_volunteer"], r["gave_last_year"],
                "; ".join(r["flags"]),
            ])

    # Write exceptions.csv
    with open(out_dir / "exceptions.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "reason"])
        for e in exceptions:
            writer.writerow([e["name"], e["reason"]])

    print(f"Processed {len(records)} donor(s); {len(exceptions)} exception(s).")
    print(f"Output written to: {out_dir.resolve()}")
    flagged = [r for r in records if r["flags"]]
    if flagged:
        print(f"{len(flagged)} record(s) have review flags — see review_summary.csv.")


if __name__ == "__main__":
    main()
