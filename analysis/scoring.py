"""Score raw stat lines (Sleeper stat keys) with an ESPN league's scoring rules.

`rules` is League.scoring: {espn_stat_id: {"points": float, "overrides": {slot_id: float}}}.
ESPN puts D/ST scoring in overrides keyed by slot "16". Other positions' overrides (such as a
TE premium) are assumed to use the same scheme of lineup slot ids (inferred: espn-api's
POSITION_MAP numbers those slots, but only a real league with such a rule can confirm it).
"""

POSITION_SLOTS = {"QB": "0", "RB": "2", "WR": "4", "TE": "6", "D/ST": "16", "K": "17"}

# ESPN stat id -> Sleeper stat keys (summed) for individual players
PLAYER_STATS = {
    0: ["pass_att"],
    1: ["pass_cmp"],
    2: ["pass_inc"],
    3: ["pass_yd"],
    4: ["pass_td"],
    19: ["pass_2pt"],
    20: ["pass_int"],
    22: ["pass_yd"],
    23: ["rush_att"],
    24: ["rush_yd"],
    25: ["rush_td"],
    26: ["rush_2pt"],
    40: ["rush_yd"],
    41: ["rec"],
    42: ["rec_yd"],
    43: ["rec_td"],
    44: ["rec_2pt"],
    53: ["rec"],
    58: ["rec_tgt"],
    61: ["rec_yd"],
    62: ["pass_2pt", "rush_2pt", "rec_2pt"],
    63: ["def_fum_td"],
    64: ["pass_sack"],
    68: ["fum"],
    72: ["fum_lost"],
    73: ["pass_int", "fum_lost"],
    74: ["fgm_50p"],
    75: ["fgm_50p", "fgmiss_50p"],
    76: ["fgmiss_50p"],
    77: ["fgm_40_49"],
    78: ["fgm_40_49", "fgmiss_40_49"],
    79: ["fgmiss_40_49"],
    80: ["fgm_0_19", "fgm_20_29", "fgm_30_39"],
    81: ["fgm_0_19", "fgm_20_29", "fgm_30_39", "fgmiss_0_19", "fgmiss_20_29", "fgmiss_30_39"],
    82: ["fgmiss_0_19", "fgmiss_20_29", "fgmiss_30_39"],
    83: ["fgm"],
    84: ["fga"],
    85: ["fgmiss", "fgmiss_0_19", "fgmiss_20_29", "fgmiss_30_39", "fgmiss_40_49", "fgmiss_50p"],
    86: ["xpm"],
    87: ["xpa"],
    88: ["xpmiss"],
    101: ["kr_td"],
    102: ["pr_td"],
    198: ["fgm_50p", "fgm_50_59"],
    201: ["fgm_60p"],
}

# "1 point every N yards" rules: ESPN stat id -> (Sleeper key, N). Checked against real ESPN stat
# lines, where each is the total integer-divided by N. Projections are fractional means, so keep the fraction.
PER_UNIT_STATS = {
    5: ("pass_yd", 5),
    6: ("pass_yd", 10),
    7: ("pass_yd", 20),
    8: ("pass_yd", 25),
    9: ("pass_yd", 50),
    10: ("pass_yd", 100),
    11: ("pass_cmp", 5),
    12: ("pass_cmp", 10),
    13: ("pass_inc", 5),
    14: ("pass_inc", 10),
    27: ("rush_yd", 5),
    28: ("rush_yd", 10),
    29: ("rush_yd", 20),
    30: ("rush_yd", 25),
    31: ("rush_yd", 50),
    32: ("rush_yd", 100),
    33: ("rush_att", 5),
    34: ("rush_att", 10),
    47: ("rec_yd", 5),
    48: ("rec_yd", 10),
    49: ("rec_yd", 20),
    50: ("rec_yd", 25),
    51: ("rec_yd", 50),
    52: ("rec_yd", 100),
    54: ("rec", 5),
    55: ("rec", 10),
}

# ESPN stat id -> Sleeper stat keys for team defenses
DST_STATS = {
    94: ["def_td"],
    95: ["int"],
    96: ["fum_rec"],
    97: ["blk_kick"],
    98: ["safe"],
    99: ["sack"],
    101: ["def_kr_td"],
    102: ["def_pr_td"],
    103: ["pass_int_td"],
    104: ["def_fum_td"],
    106: ["ff"],
    120: ["pts_allow"],
    127: ["yds_allow"],
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


def rule_points(rules: dict, stat_id: int, position: str = "") -> float:
    """What the stat is worth at this position, honoring a position-specific rule like a TE premium."""
    rule = rules.get(str(stat_id))
    if not rule:
        return 0.0
    overrides = rule.get("overrides", {})
    slot = POSITION_SLOTS.get(position)
    return overrides[slot] if slot in overrides else rule["points"]


def score(stats: dict, rules: dict, position: str = "") -> float:
    is_dst = position == "D/ST"
    total = 0.0
    for stat_id, keys in (DST_STATS if is_dst else PLAYER_STATS).items():
        value = sum(stats.get(k, 0) or 0 for k in keys)
        if value:
            total += value * rule_points(rules, stat_id, position)
    if is_dst:
        total += _bucket(stats.get("pts_allow"), POINTS_ALLOWED_BUCKETS, rules)
        total += _bucket(stats.get("yds_allow"), YARDS_ALLOWED_BUCKETS, rules)
    else:
        for stat_id, (key, per) in PER_UNIT_STATS.items():
            if value := stats.get(key):
                total += value / per * rule_points(rules, stat_id, position)
    return round(total, 2)


def unscored_stats(rules: dict) -> list[int]:
    """Stats this league scores that Sleeper's projections can't price, such as 40+ yard TD or 300-yard
    bonuses. Their points are missing from the Sleeper half of the consensus."""
    known = {*PLAYER_STATS, *PER_UNIT_STATS, *DST_STATS, *POINTS_ALLOWED_BUCKETS, *YARDS_ALLOWED_BUCKETS}
    return sorted(
        int(stat_id)
        for stat_id, rule in rules.items()
        if int(stat_id) not in known and (rule.get("points") or any(rule.get("overrides", {}).values()))
    )


def _bucket(value, buckets: dict, rules: dict) -> float:
    """Projected allowed totals are fractional means; bucket the rounded value."""
    if value is None:
        return 0.0
    value = round(value)
    for stat_id, (low, high) in buckets.items():
        if low <= value <= high:
            return rule_points(rules, stat_id, "D/ST")
    return 0.0
