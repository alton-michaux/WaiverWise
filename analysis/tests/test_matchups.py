import pytest

from analysis.matchups import (
    GOOD,
    MIN_GAMES,
    NEUTRAL,
    PRIOR_GAMES,
    TOUGH,
    GameScore,
    Rating,
    implied_totals,
    ratings,
    schedule_ratings,
)


def game(week, offense, defense, points, position="WR"):
    return GameScore(week, offense, defense, position, points)


def schedule(defense_points: dict[str, float], games: int = 4) -> list[GameScore]:
    """Each defense faces fresh offenses, each of which scores exactly 10 in its one other game, against BASE."""
    out = []
    for defense, points in defense_points.items():
        for w in range(1, games + 1):
            offense = f"O{defense}{w}"
            out += [game(w, offense, defense, points), game(w + 20, offense, "BASE", 10.0)]
    return out


def test_rating_is_the_shrunk_delta_against_each_offenses_own_average():
    rating = ratings(schedule({"MIA": 16.0}, games=3))[("MIA", "WR")]
    assert rating.points == pytest.approx(3 * 6 / (3 + PRIOR_GAMES), abs=0.01)


def test_facing_good_offenses_is_not_held_against_a_defense():
    # both allow 30 a game, but one faced an offense that always scores 30
    games = [game(w, "GOOD", "A", 30.0) for w in range(1, 5)]
    games += [game(w, "BAD", "B", 30.0) for w in range(1, 5)]
    games += [game(w, "BAD", "C", 10.0) for w in range(5, 9)]
    games += [game(w, "GOOD", "C", 30.0) for w in range(5, 9)]
    rated = ratings(games)
    assert rated[("B", "WR")].points > rated[("A", "WR")].points


def test_more_games_means_less_shrinkage():
    few = schedule({"A": 30.0}, games=MIN_GAMES)
    many = schedule({"A": 30.0}, games=MIN_GAMES + 5)
    assert ratings(many)[("A", "WR")].points > ratings(few)[("A", "WR")].points


def test_too_few_games_gets_no_rating():
    assert ("A", "WR") not in ratings(schedule({"A": 30.0}, games=MIN_GAMES - 1))


def test_offense_with_only_one_game_has_no_baseline():
    games = [game(w, f"O{w}", "A", 30.0) for w in range(1, 6)]
    assert ratings(games) == {}


def test_top_and_bottom_quarter_get_grades():
    defenses = {f"D{i:02}": 11.0 + i for i in range(31)}  # D30 allows the most; BASE makes 32 and allows the least
    rated = ratings(schedule(defenses))
    by_rank = sorted((r.rank, r.grade, d) for (d, _), r in rated.items())
    grades = [grade for _, grade, _ in by_rank]
    assert len(by_rank) == 32
    assert (by_rank[0][2], by_rank[-1][2]) == ("D30", "BASE")
    assert grades[:8] == [GOOD] * 8
    assert grades[8:24] == [NEUTRAL] * 16
    assert grades[24:] == [TOUGH] * 8


def test_positions_are_ranked_separately():
    games = schedule({"A": 30.0, "B": 10.0, "C": 20.0, "D": 15.0})
    games += [GameScore(g.week, g.offense, g.defense, "RB", 40 - g.points) for g in games]
    rated = ratings(games)
    assert rated[("A", "WR")].rank < rated[("B", "WR")].rank
    assert rated[("B", "RB")].rank < rated[("A", "RB")].rank


def test_implied_totals_split_the_total_by_the_spread():
    assert implied_totals(48.5, 3.0) == (25.75, 22.75)


def test_implied_totals_when_the_home_team_is_the_underdog():
    assert implied_totals(40.0, -4.0) == (18.0, 22.0)


@pytest.mark.parametrize("total,spread", [(None, 3.0), (48.5, None), (None, None)])
def test_implied_totals_need_both_lines(total, spread):
    assert implied_totals(total, spread) == (None, None)


RATED = {
    ("SOFT", "WR"): Rating(4.0, 1, GOOD),
    ("MID", "WR"): Rating(0.0, 2, NEUTRAL),
    ("HARD", "WR"): Rating(-4.0, 3, TOUGH),
    ("HARD", "RB"): Rating(4.0, 1, GOOD),
}


def test_schedule_averages_the_opponents_ratings():
    result = schedule_ratings({"A": ["SOFT", "MID"]}, "WR", RATED)
    assert result["A"].points == 2.0


def test_schedule_counts_an_unrated_opponent_as_average():
    assert schedule_ratings({"A": ["SOFT", "NEW"]}, "WR", RATED)["A"].points == 2.0


def test_schedule_is_graded_against_other_teams():
    opponents = {"EASY": ["SOFT"], "B": ["MID"], "C": ["MID"], "HARDEST": ["HARD"]}
    result = schedule_ratings(opponents, "WR", RATED)
    assert (result["EASY"].grade, result["B"].grade, result["HARDEST"].grade) == (GOOD, NEUTRAL, TOUGH)
    assert (result["EASY"].rank, result["HARDEST"].rank) == (1, 4)


def test_schedule_depends_on_position():
    assert schedule_ratings({"A": ["HARD"]}, "RB", RATED)["A"].points == 4.0


def test_team_with_no_games_has_no_schedule_rating():
    assert "A" not in schedule_ratings({"A": []}, "WR", RATED)
