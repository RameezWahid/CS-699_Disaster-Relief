import random

from scripts.prepare import cap, map_humaid


def test_direct_and_info_like_labels():
    assert map_humaid("injured_or_dead_people", "two dead in collapse") == "medical"
    assert map_humaid("displaced_people_and_evacuations", "families moved out") == "shelter"
    assert map_humaid("sympathy_and_support", "praying for everyone") == "info_noise"
    assert map_humaid("dont_know_cant_judge", "???") is None


def test_infrastructure_damage_is_road_blocked_only_when_roads_are_mentioned():
    assert map_humaid("infrastructure_and_utility_damage", "bridge washed away") == "road_blocked"
    assert map_humaid("infrastructure_and_utility_damage", "power lines down") == "info_noise"


def test_urgent_requests_map_by_need_and_never_to_rescue():
    label = "requests_or_urgent_needs"
    assert map_humaid(label, "need medicine for my father") == "medical"
    assert map_humaid(label, "we have no food or water") == "food_water"
    assert map_humaid(label, "please donate generously") is None


def test_cap_limits_only_capped_classes():
    rows = [{"label": "info_noise"}] * 5000 + [{"label": "shelter"}] * 10
    counts = {}
    for row in cap(rows, random.Random(0)):
        counts[row["label"]] = counts.get(row["label"], 0) + 1
    assert counts == {"info_noise": 4000, "shelter": 10}
