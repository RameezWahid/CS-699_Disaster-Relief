from scripts.extract import extract
from scripts.text import normalize, tag_language
from scripts.triage import CATEGORY_GROUP, credibility, urgency

SPEC_EXAMPLE = (
    "Andheri subway pe paani bhar gaya hai, gaadi mein 5 log phase hain, ek baccha bhi hai, "
    "jaldi madad bhejo"
)


def test_spec_worked_example():
    text = normalize(SPEC_EXAMPLE)
    fields = extract(text)
    assert tag_language(text) == "hinglish"
    assert fields["category"]["label"] == "rescue"
    assert fields["headcount"]["value"] == 5
    assert fields["vulnerability"] == ["child"]
    assert fields["needs"] == ["rescue"]
    assert fields["override"] == "trapped"
    triage = urgency("rescue", 5, True, 0, "trapped")
    assert (triage["S"], triage["base_level"], triage["level"]) == (0.675, 4, 5)


def test_urgency_bands():
    assert urgency("info_noise", None, False, 0, None)["level"] == 1
    # rescue, 10+ people, vulnerable, waited 2 h: S = 1.0, still capped at level 5
    assert urgency("rescue", 40, True, 500, None)["level"] == 5


def test_credibility_spec_examples():
    # One anonymous report with GPS, at a chronic spot, during heavy rain: 1-(.7)(.85)(.85)(.8)
    assert credibility(1, True, False, True, True, False, False)["R"] == 0.595
    assert credibility(1, False, False, False, False, False, False)["R"] == 0.3


def test_credibility_caps_senders_at_three():
    three = credibility(3, False, False, False, False, False, False)["R"]
    assert credibility(10, False, False, False, False, False, False)["R"] == three


def test_rescue_and_supply_never_share_a_group():
    assert CATEGORY_GROUP["rescue"] != CATEGORY_GROUP["food_water"]
    assert CATEGORY_GROUP["food_water"] == CATEGORY_GROUP["shelter"]
