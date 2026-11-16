from scripts.worker import HINT_CATEGORY_P, analyse

SPEC_EXAMPLE = (
    "Andheri subway pe paani bhar gaya hai, gaadi mein 5 log phase hain, ek baccha bhi hai, "
    "jaldi madad bhejo"
)


def test_spec_example_runs_whole_pipeline():
    r = analyse({"text": SPEC_EXAMPLE, "lat": 19.12037, "lon": 72.84908, "accuracy_m": 20})
    assert r["lang"] == "hinglish"
    assert r["category"] == "rescue"
    assert r["headcount"] == 5
    assert r["vulnerability"] == ["child"]
    assert r["urgency"]["level"] == 5
    assert r["location"] == {"name": None, "source": "gps", "confidence": 1.0, "uncertainty_m": 20}
    assert isinstance(r["simhash"], int)


def test_hint_used_when_rules_find_nothing():
    r = analyse({"text": "please help us here", "category_hint": "food_water"})
    assert r["category"] == "food_water"
    assert r["category_p"] == HINT_CATEGORY_P


def test_hint_never_overrides_hard_rule():
    r = analyse({"text": "3 people trapped on roof", "category_hint": "road_blocked"})
    assert r["category"] == "rescue"
    assert r["override"] == "trapped"


def test_unknown_hint_ignored():
    assert analyse({"text": "hello", "category_hint": "bogus"})["category"] == "info_noise"


def test_no_location_and_empty_text():
    r = analyse({"text": "", "lat": None, "lon": None})
    assert r["location"]["source"] is None
    assert r["lat"] is None
    assert r["simhash"] is None
