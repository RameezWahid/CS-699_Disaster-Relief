"""Text normalization, script/language tagging, and SimHash (spec 4.1, 4.2 steps 1-2)."""

import hashlib
import re
import unicodedata

DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
# Common SMS shortcuts seen in English and Hinglish messages.
SMS_SHORTCUTS = {
    "pls": "please",
    "plz": "please",
    "plzz": "please",
    "ppl": "people",
    "hlp": "help",
    "u": "you",
    "ur": "your",
    "bcz": "because",
    "coz": "because",
    "nd": "and",
    "abt": "about",
    "msg": "message",
    "govt": "government",
    "hosp": "hospital",
    "jldi": "jaldi",
    "mdd": "madad",
    "nhi": "nahi",
}
# A Latin letter repeated three or more times ("helppp", "jaldiiii") is emphasis, not spelling.
# Doubled letters are left alone because they are real spelling ("baccha", "paani").
REPEATED_LETTER = re.compile(r"([a-z])\1{2,}")
TOKEN = re.compile(r"[\wऀ-ॿ]+")

# Function words that mark Romanized Hindi and Romanized Marathi. Chosen from the scenario styles
# in spec 4.9 (en, hinglish, mr_latin); kept short so English words are not caught.
HINGLISH_MARKERS = {
    "hai",
    "hain",
    "mein",
    "pe",
    "par",
    "ka",
    "ki",
    "ke",
    "ko",
    "se",
    "aur",
    "nahi",
    "bhi",
    "gaya",
    "gaye",
    "hua",
    "log",
    "paani",
    "pani",
    "jaldi",
    "madad",
    "bhejo",
    "kuch",
    "yahan",
    "phase",
    "phanse",
    "fasa",
    "fase",
    "bachao",
    "kar",
    "raha",
    "rahe",
    "hum",
    "mera",
    "meri",
}
MARATHI_LATIN_MARKERS = {
    "ahe",
    "aahe",
    "ahet",
    "aahet",
    "madat",
    "lavkar",
    "lavkara",
    "adakle",
    "adakalo",
    "amhi",
    "aamhi",
    "javal",
    "samor",
    "pathva",
    "pathava",
    "kara",
    "nahi",
    "ithe",
    "tithe",
    "majha",
}
# Devanagari words that separate Marathi from Hindi. "ळ" is common in Marathi and rare in Hindi.
MARATHI_DEVA_MARKERS = {"आहे", "आहेत", "आम्ही", "मध्ये", "लवकर", "अडकले", "अडकलो", "पाठवा", "करा"}
HINDI_DEVA_MARKERS = {"है", "हैं", "में", "हम", "पानी", "जल्दी", "भेजो", "नहीं", "फंसे", "फँसे"}


def normalize(text: str) -> str:
    """NFC, Devanagari digits to ASCII, lowercase Latin, collapse emphasis, expand SMS shortcuts."""
    text = unicodedata.normalize("NFC", text).translate(DEVANAGARI_DIGITS).lower()
    text = REPEATED_LETTER.sub(r"\1", text)
    words = [SMS_SHORTCUTS.get(word, word) for word in text.split()]
    return " ".join(words)


def tokens(text: str) -> list[str]:
    return TOKEN.findall(text)


def tag_language(text: str) -> str:
    """Return en, hi, mr, hinglish, or mixed (Romanized Marathi is mr). Expects normalized text."""
    deva = sum(1 for ch in text if "ऀ" <= ch <= "ॿ")
    latin = sum(1 for ch in text if "a" <= ch <= "z")
    if deva + latin == 0:
        return "en"
    deva_share = deva / (deva + latin)
    words = set(tokens(text))
    if deva_share >= 0.8:
        marathi = len(words & MARATHI_DEVA_MARKERS) + ("ळ" in text)
        hindi = len(words & HINDI_DEVA_MARKERS)
        return "mr" if marathi > hindi else "hi"
    if deva_share > 0.2:
        return "mixed"
    marathi = len(words & MARATHI_LATIN_MARKERS)
    hinglish = len(words & HINGLISH_MARKERS)
    if max(marathi, hinglish) < 2:
        return "en"
    return "mr" if marathi > hinglish else "hinglish"


def simhash(text: str) -> int:
    """64-bit SimHash over word unigrams and bigrams, as a signed int for Postgres bigint."""
    words = tokens(text)
    features = words + [f"{a} {b}" for a, b in zip(words, words[1:], strict=False)]
    counts = [0] * 64
    for feature in features:
        h = int.from_bytes(hashlib.blake2b(feature.encode(), digest_size=8).digest(), "big")
        for bit in range(64):
            counts[bit] += 1 if h >> bit & 1 else -1
    value = sum(1 << bit for bit in range(64) if counts[bit] > 0)
    return value - (1 << 64) if value >= 1 << 63 else value


def hamming(a: int, b: int) -> int:
    return bin((a ^ b) & (1 << 64) - 1).count("1")


# Spelling swaps seen in Romanized Mumbai place names ("Lalbagh"/"Lalbaug", "Vile Parle"/"Wile").
PLACE_SWAPS = [
    ("ph", "f"),
    ("w", "v"),
    ("z", "j"),
    ("ck", "k"),
    ("q", "k"),
    ("aa", "a"),
    ("ee", "i"),
    ("oo", "u"),
    ("au", "a"),
    ("gh", "g"),
    ("bh", "b"),
]


def normalize_place(name: str) -> str:
    """Key for fuzzy place matching. Applied to both gazetteer names and candidate spans."""
    key = " ".join(tokens(normalize(name).replace("'", "")))
    for old, new in PLACE_SWAPS:
        key = key.replace(old, new)
    return key
