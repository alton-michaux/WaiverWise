"""Score raw stat lines (Sleeper stat keys) with an ESPN league's scoring rules.

`rules` is League.scoring: {espn_stat_id: {"points": float, "overrides": {slot_id: float}}}.
ESPN puts D/ST scoring in overrides keyed by slot "16".
"""

DST_SLOT = "16"

# ESPN stat id -> Sleeper stat keys (summed) for individual players
PLAYER_STATS = {
    3: ["pass_yd"],
    4: ["pass_td"],
    19: ["pass_2pt"],
    20: ["pass_int"],
    24: ["rush_yd"],
    25: ["rush_td"],
    26: ["rush_2pt"],
    42: ["rec_yd"],
    43: ["rec_td"],
    44: ["rec_2pt"],
    53: ["rec"],
    72: ["fum_lost"],
    80: ["fgm_0_19", "fgm_20_29", "fgm_30_39"],
    77: ["fgm_40_49"],
    198: ["fgm_50p", "fgm_50_59"],
    201: ["fgm_60p"],
    85: ["fgmiss", "fgmiss_0_19", "fgmiss_20_29", "fgmiss_30_39", "fgmiss_40_49", "fgmiss_50p"],
    86: ["xpm"],
    101: ["kr_td"],
    102: ["pr_td"],
}

# ESPN stat id -> Sleeper stat keys for team defenses
DST_STATS = {
    95: ["int"],
    96: ["fum_rec"],
    97: ["blk_kick"],
    98: ["safe"],
    99: ["sack"],
    101: ["def_kr_td"],
    102: ["def_pr_td"],
    103: ["pass_int_td"],
    104: ["def_fum_td"],
}

# Bucketed D/ST stats: ESPN stat id -> inclusive (low, high) range
POINTS_ALLOWED_BUCKETS = {
    89: (0, 0),
    90: (1, 6),
    91: (7, 13),
    92: (14, 17),
    121: (18, 21),
    122: (22, 27),
    123: (28, 34),
    124: (35, 45),
    125: (46, float("inf")),
}
YARDS_ALLOWED_BUCKETS = {
    128: (0, 99),
    129: (100, 199),
    130: (200, 299),
    131: (300, 349),
    132: (350, 399),
    133: (400, 449),
    134: (450, 499),
    135: (500, 549),
    136: (550, float("inf")),
}


def rule_points(rules: dict, stat_id: int, is_dst: bool) -> float:
    rule = rules.get(str(stat_id))
    if not rule:
        return 0.0
    if is_dst and DST_SLOT in rule.get("overrides", {}):
        return rule["overrides"][DST_SLOT]
    return rule["points"]


def score(stats: dict, rules: dict, is_dst: bool = False) -> float:
    total = 0.0
    for stat_id, keys in (DST_STATS if is_dst else PLAYER_STATS).items():
        value = sum(stats.get(k, 0) or 0 for k in keys)
        if value:
            total += value * rule_points(rules, stat_id, is_dst)
    if is_dst:
        total += _bucket(stats.get("pts_allow"), POINTS_ALLOWED_BUCKETS, rules)
        total += _bucket(stats.get("yds_allow"), YARDS_ALLOWED_BUCKETS, rules)
    return round(total, 2)


def _bucket(value, buckets: dict, rules: dict) -> float:
    """Projected allowed totals are fractional means; bucket the rounded value."""
    if value is None:
        return 0.0
    value = round(value)
    for stat_id, (low, high) in buckets.items():
        if low <= value <= high:
            return rule_points(rules, stat_id, is_dst=True)
    return 0.0
