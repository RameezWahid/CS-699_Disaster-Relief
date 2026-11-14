import random

import pytest

from scripts.messages import CLASSES, STYLES, TEMPLATES, add_noise, headcount_phrase, templates_for


@pytest.mark.parametrize("style", STYLES)
@pytest.mark.parametrize("label", CLASSES)
def test_every_style_and_class_has_train_and_replay_templates(style, label):
    train = templates_for(style, label, "train")
    replay = templates_for(style, label, "replay")
    assert train and replay
    assert not set(train) & set(replay)
    assert len(train) + len(replay) == len(TEMPLATES[(style, label)])


@pytest.mark.parametrize("style", STYLES)
def test_headcount_phrase_returns_the_number_it_renders(style):
    rng = random.Random(7)
    for _ in range(50):
        phrase, n = headcount_phrase(style, rng)
        assert n in (2, 3, 4, 5, 6, 8, 10, 12, 15, 20)
        assert phrase.split()[-1] in ("people", "log", "लोग", "जण", "jan")


def test_noise_is_deterministic_for_a_seed():
    text = "please help, 4 people stuck near Hindmata"
    assert add_noise(text, "en", random.Random(3)) == add_noise(text, "en", random.Random(3))
