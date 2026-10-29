from analysis.value import PlayerValue, replacement_levels, starters_by_position, vorp

SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "RB/WR/TE": 1, "BE": 5, "IR": 1}


def pool(pos, values, start_id):
    return [PlayerValue(start_id + i, pos, v) for i, v in enumerate(values)]


def test_flex_goes_to_best_leftovers():
    # 2 teams: 4 RB + 4 WR + 2 TE dedicated, then 2 flex
    players = (
        pool("QB", [300, 250, 200], 0)
        + pool("RB", [200, 190, 180, 170, 165, 160, 100], 10)
        + pool("WR", [210, 200, 190, 180, 150, 140], 30)
        + pool("TE", [150, 120, 90], 50)
    )
    counts = starters_by_position(players, SLOTS, team_count=2)
    assert counts == {"QB": 2, "RB": 6, "WR": 4, "TE": 2}  # RBs 165, 160 beat WR 150


def test_replacement_and_vorp():
    players = pool("QB", [300, 250, 200], 0) + pool("TE", [150, 120, 90], 50)
    levels = replacement_levels(players, {"QB": 1, "TE": 1}, team_count=2)
    assert levels == {"QB": 200, "TE": 90}
    v = vorp(players, {"QB": 1, "TE": 1}, team_count=2)
    assert v[0] == 100 and v[52] == 0


def test_shallow_position_uses_last_player():
    players = pool("K", [100], 0)
    assert replacement_levels(players, {"K": 1}, team_count=10) == {"K": 100}
