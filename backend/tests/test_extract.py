import pytest

from scripts.extract import extract
from scripts.text import normalize, tag_language


@pytest.mark.parametrize(
    ("message", "lang", "headcount"),
    [
        ("हिंदमाता जवळ पाणी भरले आहे, आम्ही ४ जण अडकलो आहोत", "mr", 4),
        ("मिलन सबवे में पानी भर गया है, दस लोग फंसे हैं", "hi", 10),
        (
            "Kurla station javal pani bharla ahe, amhi 6-7 jan adakle ahot, lavkar madat pathva",
            "mr",
            7,
        ),
        ("Family of 4 stuck on the first floor near Sion", "en", 4),
    ],
)
def test_language_and_headcount(message, lang, headcount):
    text = normalize(message)
    assert tag_language(text) == lang
    assert extract(text)["headcount"]["value"] == headcount


def test_years_and_road_numbers_are_not_headcounts():
    assert extract(normalize("Flood like 2005 again on road no 14"))["headcount"] is None


def test_flood_water_is_not_a_drinking_water_need():
    assert "water" not in extract(normalize("Hindmata pe bahut paani bhar gaya"))["needs"]
    assert "water" in extract(normalize("peene ka paani khatam, 3 log hain"))["needs"]


def test_no_hard_rule_means_no_override():
    fields = extract(normalize("Heavy rain in Dadar today, stay safe everyone"))
    assert fields["override"] is None
    assert fields["category"]["label"] == "info_noise"
