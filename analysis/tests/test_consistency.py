from analysis.consistency import consistency


def test_counts_games_inside_the_starter_cutoff():
    weekly = {1: [30, 20, 10, 5], 2: [30, 20, 10, 5], 3: [40, 30, 25, 20]}
    result = consistency({1: 20, 2: 10, 3: 25}, weekly, starters=2)
    assert (result.games, result.startable, result.high, result.low) == (3, 1, 25, 10)


def test_tying_the_cutoff_counts():
    assert consistency({1: 20}, {1: [30, 20, 20]}, starters=2).startable == 1


def test_fewer_players_than_starters_all_count():
    assert consistency({1: 2}, {1: [30, 2]}, starters=5).startable == 1


def test_position_nobody_starts_is_never_startable():
    assert consistency({1: 30}, {1: [30]}, starters=0).startable == 0


def test_no_games_has_no_result():
    assert consistency({}, {}, starters=2) is None
