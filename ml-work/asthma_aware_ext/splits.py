"""Season-balanced weekly-block train/validation/test split.

Replaces the chronological 70/15/15 split of ``asthma_aware.data_pipeline.assign_splits``
(which is left unchanged) for new models. The year is cut into 7-day blocks starting on
Jan 1; within each season, blocks are assigned at random (fixed seed) so every season
appears in every set. Neighboring days are strongly correlated (same weather system,
gap-filled labels), so one day is dropped at every boundary between blocks of different
sets; dropped days are used by no set.

Dates are UTC calendar days, matching ``record["target_time"][:10]`` in the catalog.
"""

from __future__ import annotations

import copy
import datetime as dt
import random

SEASONS = {12: "DJF", 1: "DJF", 2: "DJF", 3: "MAM", 4: "MAM", 5: "MAM",
           6: "JJA", 7: "JJA", 8: "JJA", 9: "SON", 10: "SON", 11: "SON"}


def weekly_blocks_range(start, end):
    """7-day blocks from ``start`` up to ``end`` (exclusive); a short tail joins the last block."""
    days = [start + dt.timedelta(d) for d in range((end - start).days)]
    blocks = [days[i:i + 7] for i in range(0, len(days), 7)]
    if len(blocks) > 1 and len(blocks[-1]) < 7:
        blocks[-2].extend(blocks.pop())
    return blocks


def weekly_blocks(year):
    """7-day blocks from Jan 1; the last block absorbs the remaining 1-2 days."""
    return weekly_blocks_range(dt.date(year, 1, 1), dt.date(year + 1, 1, 1))


def weekly_split(year=2023, seed=2023, validation_per_season=2, test_per_season=2, gap_days=1):
    """Return ({"YYYY-MM-DD": "train" | "validation" | "test" | "gap"}, summary).

    A block's season is the season of its middle day. Within each season the blocks are
    shuffled with ``seed``; the first ``validation_per_season`` become validation, the next
    ``test_per_season`` test, the rest train.
    """
    mapping, summary = _split_blocks(weekly_blocks(year), seed, validation_per_season, test_per_season, gap_days)
    return mapping, dict({"year": year}, **summary)


def weekly_split_range(start, end, seed=2023, validation_per_season=2, test_per_season=2, gap_days=1):
    """``weekly_split`` over any date range: blocks start on ``start``; ``end`` is exclusive.

    Dates are ``datetime.date`` or "YYYY-MM-DD". Same seasonal rule and gaps as ``weekly_split``;
    a season with several years' weeks (e.g. two autumns) is still one season.
    """
    start, end = (d if isinstance(d, dt.date) else dt.date.fromisoformat(d) for d in (start, end))
    mapping, summary = _split_blocks(weekly_blocks_range(start, end), seed, validation_per_season,
                                     test_per_season, gap_days)
    return mapping, dict({"start": start.isoformat(), "end": end.isoformat()}, **summary)


def _split_blocks(blocks, seed, validation_per_season, test_per_season, gap_days):
    rng = random.Random(seed)
    by_season = {}
    for i, block in enumerate(blocks):
        by_season.setdefault(SEASONS[block[len(block) // 2].month], []).append(i)
    label = {}
    for season in ("DJF", "MAM", "JJA", "SON"):
        ids = sorted(by_season[season])
        if len(ids) < validation_per_season + test_per_season + 1:
            raise ValueError(f"{season} has too few weeks for this split")
        rng.shuffle(ids)
        for k, i in enumerate(ids):
            label[i] = ("validation" if k < validation_per_season
                        else "test" if k < validation_per_season + test_per_season else "train")
    mapping = {}
    for i, block in enumerate(blocks):
        for day in block:
            mapping[day.isoformat()] = label[i]
    # Gaps: at each boundary between different sets, drop days on the training side
    # (validation/test weeks are scarce); between validation and test, drop the earlier side.
    for i in range(1, len(blocks)):
        a, b = label[i - 1], label[i]
        if a == b:
            continue
        if b == "train":
            drop = blocks[i][:gap_days]
        else:
            drop = blocks[i - 1][-gap_days:]
        for day in drop:
            mapping[day.isoformat()] = "gap"
    summary = {
        "seed": seed, "gap_days": gap_days,
        "weeks": {s: sum(1 for i in label if label[i] == s) for s in ("train", "validation", "test")},
        "days": {s: sum(1 for v in mapping.values() if v == s) for s in ("train", "validation", "test", "gap")},
        "weeks_by_season": {
            season: {s: sum(1 for i in ids if label[i] == s) for s in ("train", "validation", "test")}
            for season, ids in by_season.items()
        },
    }
    return mapping, summary


def apply_split(bundle, mapping):
    """Copy of ``bundle`` with every source's records re-labelled by ``mapping``.

    Records on gap days, or on dates outside the mapping, are removed. Fit normalization
    after this (on the new training set), never before.
    """
    out = copy.deepcopy(bundle)
    counts = {}
    for source, records in out["indexes"].items():
        kept = []
        for r in records:
            split = mapping.get(r["target_time"][:10])
            if split in ("train", "validation", "test"):
                r["split"] = split
                kept.append(r)
        out["indexes"][source] = kept
        counts[source] = {s: sum(r["split"] == s for r in kept) for s in ("train", "validation", "test")}
        out["reports"][source]["split_counts"] = counts[source]
        out["reports"][source]["split_dates"] = {
            d: s for d, s in mapping.items() if any(r["target_time"][:10] == d for r in kept)
        }
        out["reports"][source]["split_policy"] = "weekly_blocks_by_season"
    return out, counts
