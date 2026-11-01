"""Which ESPN leagues the analysis can give sound advice for."""

SCORING_TYPES = {"H2H_POINTS"}
SLOTS = {"QB", "RB", "WR", "TE", "RB/WR", "WR/TE", "RB/WR/TE", "OP", "D/ST", "K", "BE", "IR"}


def check_supported(data) -> list[str]:
    """Why this league can't be used, in words for the person connecting it. Empty means it's fine."""
    problems = []
    if data.scoring_type not in SCORING_TYPES:
        problems.append(
            f"WaiverWise only supports head-to-head points leagues, and this one uses {data.scoring_type}."
        )
    if other := sorted(set(data.lineup_slots) - SLOTS):
        problems.append(f"WaiverWise has no projections for these lineup slots: {', '.join(other)}.")
    return problems
