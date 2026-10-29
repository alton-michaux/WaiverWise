from analysis.lineup import Candidate, optimal, starters

SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "RB/WR/TE": 1, "K": 1, "D/ST": 1, "BE": 7, "IR": 1}


def c(pid, pos, value):
    flex = ("RB/WR/TE",) if pos in ("RB", "WR", "TE") else ()
    return Candidate(pid, (pos, *flex, "BE"), value)


def test_fills_position_slots_then_flex():
    roster = [
        c(1, "QB", 20), c(2, "QB", 18),
        c(3, "RB", 15), c(4, "RB", 12), c(5, "RB", 11),
        c(6, "WR", 14), c(7, "WR", 13), c(8, "WR", 10),
        c(9, "TE", 8), c(10, "K", 7), c(11, "D/ST", 6),
    ]
    lineup = optimal(roster, SLOTS)
    assert lineup["QB"] == [1]
    assert sorted(lineup["RB"]) == [3, 4]
    assert sorted(lineup["WR"]) == [6, 7]
    assert lineup["RB/WR/TE"] == [5]  # 11 beats WR 10
    assert sorted(lineup["BE"]) == [2, 8]


def test_te_can_win_flex():
    roster = [c(1, "RB", 15), c(2, "RB", 14), c(3, "RB", 3), c(4, "TE", 12), c(5, "TE", 9)]
    lineup = optimal(roster, SLOTS)
    assert lineup["TE"] == [4]
    assert lineup["RB/WR/TE"] == [5]


def test_locked_players_stay_put():
    roster = [c(1, "RB", 15), c(2, "RB", 14), c(3, "RB", 3)]
    lineup = optimal(roster, SLOTS, locked={3: "RB", 1: "BE"})
    assert 3 in lineup["RB"]
    assert 1 in lineup["BE"]
    assert 2 in starters(lineup)


def test_empty_slot_when_no_eligible_player():
    lineup = optimal([c(1, "QB", 20)], SLOTS)
    assert lineup["K"] == []
