from scripts import synth
from scripts.messages import CLASSES, STYLES

PLACES = {"latin": ["Hindmata", "Milan Subway", "Kurla"], "deva": ["हिंदमाता", "कुर्ला"]}


def test_plausible_place_drops_company_and_address_names():
    assert synth.plausible_place("Milan Subway")
    assert not synth.plausible_place("Acme Pvt Ltd")
    assert not synth.plausible_place("Shop 4, Station Road")
    assert not synth.plausible_place("Old Municipal Market Building Dadar")


def test_is_devanagari():
    assert synth.is_devanagari("कुर्ला")
    assert not synth.is_devanagari("Kurla")


def test_generate_covers_every_cell_and_is_reproducible(monkeypatch):
    monkeypatch.setattr(synth, "load_places", lambda: PLACES)
    rows = synth.generate(3)
    assert len(rows) == 3 * len(STYLES) * len(CLASSES)
    assert {(r["style"], r["label"]) for r in rows} == {(s, c) for s in STYLES for c in CLASSES}
    assert all(r["source"] == "synthetic" and "{" not in r["text"] for r in rows)
    assert rows == synth.generate(3)
