"""Scoring of a replay against planted truth (pure functions, no database)."""

from scripts.evaluate import bcubed, incident_outcomes, root_incident, score


def row(incident, category, predicted, truth_category=None, distance=50.0, headcount=4):
    truth = {"incident": incident, "category": truth_category or category, "headcount": 4}
    return {
        "truth": truth,
        "category": category,
        "incident": predicted,
        "distance_m": distance,
        "headcount": headcount,
    }


def test_perfect_grouping_scores_one():
    pairs = [("I1", 10), ("I1", 10), ("I2", 20)]
    assert bcubed(pairs) == {"p": 1.0, "r": 1.0, "f1": 1.0}
    outcomes = incident_outcomes(pairs)
    assert (outcomes["found"], outcomes["split"], outcomes["merged"]) == (2, 0, 0)


def test_a_split_incident_lowers_recall_only():
    scores = bcubed([("I1", 10), ("I1", 11)])
    assert scores["p"] == 1.0 and scores["r"] == 0.5
    assert incident_outcomes([("I1", 10), ("I1", 11)])["split"] == 1


def test_two_incidents_in_one_cluster_lower_precision_and_count_as_merged():
    pairs = [("I1", 10), ("I1", 10), ("I2", 10), ("I2", 10)]
    assert bcubed(pairs)["p"] == 0.5
    assert incident_outcomes(pairs)["merged"] == 2


def test_reports_left_out_of_any_incident_count_as_missed():
    outcomes = incident_outcomes([("I1", None), ("I1", None)])
    assert outcomes["found"] == 0 and outcomes["missed"] == ["I1"]
    assert bcubed([("I1", None), ("I1", None)])["r"] == 0.5


def test_merged_incidents_resolve_to_the_survivor():
    assert root_incident(3, {3: 2, 2: 1, 1: None}) == 1
    assert root_incident(5, {5: None}) == 5
    assert root_incident(7, {7: 8, 8: 7}) in (7, 8)  # a loop does not hang


def test_score_combines_triage_location_headcount_and_noise():
    rows = [
        row("I1", "rescue", 10),
        row("I1", "rescue", 10, distance=400.0, headcount=None),
        row("I2", "shelter", 20, truth_category="rescue"),
        row(None, "info_noise", None),
        row(None, "info_noise", 10),  # joined a real incident
        row(None, "info_noise", 99),  # its own chatter incident is fine
        {**row("I3", "rescue", None), "category": None},  # not processed yet
    ]
    scores = score(rows)
    assert (scores["messages"], scores["processed"]) == (7, 6)
    assert scores["triage"]["rescue"]["r"] == round(2 / 3, 3)
    assert scores["location"]["within_250m"] == round(2 / 3, 3)
    assert scores["headcount"]["exact"] == round(2 / 3, 3)
    assert scores["noise"] == {"kept_apart": round(2 / 3, 3), "of": 3}
