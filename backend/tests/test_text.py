from scripts.text import hamming, normalize, normalize_place, simhash

SPEC_EXAMPLE = (
    "Andheri subway pe paani bhar gaya hai, gaadi mein 5 log phase hain, ek baccha bhi hai, "
    "jaldi madad bhejo"
)


def test_simhash_near_duplicates():
    a = simhash(normalize(SPEC_EXAMPLE))
    assert hamming(a, simhash(normalize(SPEC_EXAMPLE + "!!!"))) <= 3
    assert hamming(a, simhash(normalize("Tree fallen on SV road near Khar"))) > 3


def test_place_normalization_merges_spelling_variants():
    assert normalize_place("Lalbagh") == normalize_place("Lalbaug")
    assert normalize_place("King's Circle") == normalize_place("Kings Circle")
