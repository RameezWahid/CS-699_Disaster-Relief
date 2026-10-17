"""Rule-based extraction: headcount, needs, vulnerability, hard rules, rules-only category.

Spec 4.2. Lexicon entries are matched against tokens. A multi-word entry must appear as
consecutive tokens. An entry ending in "*" is a prefix match on one token (Devanagari and English
stems).
"""

import re

from scripts.text import tokens

# Lexicons are data tables kept one group per line so fluent speakers can review them.
# fmt: off
NUMBER_WORDS = {
    # English
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "fifty": 50,
    # Romanized Hindi and Marathi
    "ek": 1, "do": 2, "don": 2, "teen": 3, "tin": 3, "char": 4, "chaar": 4, "paanch": 5,
    "panch": 5, "pach": 5, "chhe": 6, "che": 6, "saha": 6, "saat": 7, "sat": 7, "aath": 8,
    "ath": 8, "nau": 9, "nav": 9, "das": 10, "daha": 10, "barah": 12, "bara": 12, "bees": 20,
    "vees": 20, "pachas": 50,
    # Devanagari Hindi
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "छह": 6, "छः": 6, "सात": 7,
    "आठ": 8, "नौ": 9, "दस": 10, "बारह": 12, "बीस": 20, "पचास": 50,
    # Devanagari Marathi
    "दोन": 2, "पाच": 5, "सहा": 6, "नऊ": 9, "दहा": 10, "बारा": 12, "वीस": 20, "पन्नास": 50,
}
# Words that follow a number when it counts people ("5 log", "पाच जण", "10 people").
PEOPLE_COUNTERS = [
    "people", "persons", "person", "members", "adults", "children", "kids", "of us",
    "log", "logo", "logon", "jan", "jane", "jana", "admi", "aadmi", "bacche", "bachche", "lok",
    "manse", "लोग", "लोगों", "जन", "व्यक्ति", "सदस्य", "आदमी", "बच्चे", "जण", "जणं", "लोक",
    "माणसं", "व्यक्ती", "मुले",
]

NEEDS = {
    "boat": ["boat", "boats", "naav", "naw", "नाव", "बोट", "नौका"],
    "food": ["food", "khana", "khaana", "khane", "jevan", "ration", "भोजन", "खाना", "खाने", "जेवण",
             "अन्न", "राशन", "शिधा"],
    # Plain "paani" usually means flood water, so drinking water needs a qualifier.
    "water": ["drinking water", "peene ka paani", "pine ka pani", "peene ko paani", "pine ko pani",
              "pinyache pani", "water bottle*", "पीने का पानी", "पीने को पानी", "पिण्याचे पाणी"],
    "medicine": ["medicine*", "dawa", "dawai", "dava", "davai", "insulin", "tablet*", "दवा", "दवाई",
                 "दवाइयां", "औषध*", "गोळ्या"],
    "doctor": ["doctor*", "ambulance", "daktar", "nurse", "डॉक्टर", "एम्बुलेंस", "एंबुलेंस",
               "रुग्णवाहिका", "ॲम्ब्युलन्स"],
    "blanket": ["blanket*", "kambal", "chadar", "kapde", "dry clothes", "कंबल", "कम्बल", "चादर",
                "ब्लँकेट", "घोंगडी", "कपडे"],
    "shelter": ["shelter", "relief camp", "rehne ki jagah", "rahne ki jagah", "rahayla jaga",
                "आश्रय", "निवारा", "रहने की जगह", "राहायला जागा"],
    "rescue": ["rescue", "bachao", "bachaao", "bacha lo", "nikalo", "nikaalo", "vachva", "vachava",
               "evacuat*", "बचाओ", "बचा लो", "निकालो", "वाचवा"],
}

VULNERABILITY = {
    "child": ["child*", "kid", "kids", "baby", "babies", "infant*", "baccha", "bachcha", "bacche",
              "bachche", "bacchi", "bachi", "बच्चा", "बच्चे", "बच्ची", "शिशु", "बाळ", "मूल", "मुलं",
              "मुले", "lahan mul"],
    "elderly": ["old", "elderly", "aged", "senior*", "budhe", "buddhe", "budhi", "buzurg", "dadi",
                "aaji", "ajji", "ajoba", "mhatare", "बुजुर्ग", "बूढ़े", "बुढ़े", "वृद्ध*", "आजी",
                "आजोबा", "म्हातारे"],
    "pregnant": ["pregnan*", "garbhvati", "garbhwati", "गर्भवती", "डिलीवरी", "प्रसूती", "delivery"],
    "disabled": ["disabled", "handicap*", "wheelchair", "viklang", "divyang", "paralys*", "lakwa",
                 "bedridden", "अपंग", "विकलांग", "दिव्यांग"],
    "oxygen": ["oxygen", "ऑक्सीजन", "ऑक्सिजन"],
    "dialysis": ["dialysis", "डायलिसिस"],
}

# Safety net (spec 4.2 step 5): any hit forces rescue and urgency 5, whatever the classifier says.
HARD_RULES = {
    "trapped": ["trapped", "stuck", "phase", "phanse", "phas gaye", "fase", "fanse", "fasa",
                "fas gaye", "adakle", "adakalo", "adkale", "adkun", "फंस*", "फँस*", "अडकल*",
                "अडकून"],
    "drowning": ["drown*", "doob*", "dub rahe", "washed away", "swept away", "beh gaye", "budat",
                 "डूब*", "बह गए", "बुडत*", "बुडाल*", "वाहून"],
    "not_breathing": ["not breathing", "cannot breathe", "can t breathe", "saans nahi", "sans nahi",
                      "shwas gheta yet nahi", "सांस नहीं", "साँस नहीं", "श्वास घेता येत नाही"],
}

# Phase 1 has no trained classifier yet; these cues stand in for it.
MEDICAL_CUES = ["injur*", "hurt", "bleeding", "fever", "sick", "unconscious", "behosh", "chot",
                "ghayal", "bimar", "heart attack", "snake bite", "saanp", "बीमार", "घायल", "चोट",
                "बेहोश", "बुखार", "जखमी", "आजारी", "ताप", "साप"]
ROAD_CUES = ["road block*", "blocked", "road band", "road closed", "tree fallen", "tree fell",
             "ped gira", "jam", "traffic", "रास्ता बंद", "सड़क बंद", "रस्ता बंद", "झाड पडले",
             "पेड़ गिरा"]
SHELTER_CUES = ["house flooded", "ghar mein paani", "ghar me pani", "ghar me paani",
                "gharat pani", "घर में पानी", "घरात पाणी"]
# fmt: on

NUMBER = r"(\d{1,3}|" + "|".join(sorted(map(re.escape, NUMBER_WORDS), key=len, reverse=True)) + ")"
COUNTER = "(?:" + "|".join(sorted(map(re.escape, PEOPLE_COUNTERS), key=len, reverse=True)) + ")"
NOT_WORD = r"[\wऀ-ॿ]"
# "5 log", "5-6 log", "5 to 6 people"; and "family of 4".
HEADCOUNT_PATTERNS = [
    re.compile(rf"(?<!{NOT_WORD}){NUMBER}(?:\s*(?:-|to|se)\s*{NUMBER})?\s+{COUNTER}(?!{NOT_WORD})"),
    re.compile(rf"family of\s+{NUMBER}(?!{NOT_WORD})"),
]
# Above this a number is almost always a year, a building number, or a road number.
MAX_PLAUSIBLE_HEADCOUNT = 500
RULE_CATEGORY_P = 0.6


def has_term(words: list[str], term: str) -> bool:
    parts = term.split()
    for start in range(len(words) - len(parts) + 1):
        if all(
            (
                words[start + i].startswith(part[:-1])
                if part.endswith("*")
                else words[start + i] == part
            )
            for i, part in enumerate(parts)
        ):
            return True
    return False


def labels_present(words: list[str], lexicon: dict[str, list[str]]) -> list[str]:
    return [label for label, terms in lexicon.items() if any(has_term(words, t) for t in terms)]


def extract_headcount(text: str) -> dict | None:
    """Largest people count in normalized text, or None. A range like '5-6 log' counts as 6."""
    values = []
    for pattern in HEADCOUNT_PATTERNS:
        for match in pattern.finditer(text):
            for raw in match.groups():
                if raw is None:
                    continue
                value = int(raw) if raw.isdigit() else NUMBER_WORDS[raw]
                if 0 < value <= MAX_PLAUSIBLE_HEADCOUNT:
                    values.append((value, raw.isdigit()))
    if not values:
        return None
    value, is_digit = max(values)
    return {"value": value, "confidence": 0.9 if is_digit else 0.85}


def rule_category(
    words: list[str], needs: list[str], vulnerability: list[str], override: str | None
):
    if override or "rescue" in needs or "boat" in needs:
        return "rescue"
    medical_needs = {"medicine", "doctor"} & set(needs) or {"oxygen", "dialysis"} & set(
        vulnerability
    )
    if medical_needs or any(has_term(words, t) for t in MEDICAL_CUES):
        return "medical"
    if "shelter" in needs or any(has_term(words, t) for t in SHELTER_CUES):
        return "shelter"
    if {"food", "water"} & set(needs):
        return "food_water"
    if any(has_term(words, t) for t in ROAD_CUES):
        return "road_blocked"
    return "info_noise"


def extract(text: str) -> dict:
    """Extract structured fields from normalized text."""
    words = tokens(text)
    fired = labels_present(words, HARD_RULES)
    override = fired[0] if fired else None
    needs = labels_present(words, NEEDS)
    if override and "rescue" not in needs:
        needs.append("rescue")
    vulnerability = labels_present(words, VULNERABILITY)
    category = rule_category(words, needs, vulnerability, override)
    return {
        "category": {"label": category, "p": RULE_CATEGORY_P},
        "headcount": extract_headcount(text),
        "needs": needs,
        "vulnerability": vulnerability,
        "override": override,
    }
