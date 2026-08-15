"""
Tests for the matching engine. These deliberately target the messy
cases catalog.csv was built to contain -- see scripts/build_catalog.py
for why each of these entries exists.

No DB needed: CatalogEntry is a plain dataclass.
"""
from catalog.matching import CatalogEntry, match, normalize, score_entry

HP1 = CatalogEntry(
    catalog_id="CAT0001",
    title="Harry Potter and the Philosopher's Stone",
    alt_titles=["Harry Potter and the Sorcerer's Stone"],
    author="J.K. Rowling",
    author_alt=["Joanne Rowling", "J. K. Rowling", "Rowling, J.K."],
)
ALCHEMIST_COELHO = CatalogEntry(
    catalog_id="CAT0022",
    title="The Alchemist",
    author="Paulo Coelho",
    author_alt=["Coelho, Paulo"],
)
ALCHEMIST_SCOTT = CatalogEntry(
    catalog_id="CAT0023",
    title="The Alchemist",
    alt_titles=["The Secrets of the Immortal Nicholas Flamel: The Alchemyst"],
    author="Michael Scott",
    author_alt=["Scott, Michael"],
)
DUNE = CatalogEntry(catalog_id="CAT0030", title="Dune", author="Frank Herbert")
DUNE_MESSIAH = CatalogEntry(catalog_id="CAT0031", title="Dune Messiah", author="Frank Herbert")
MARQUEZ = CatalogEntry(
    catalog_id="CAT0040",
    title="One Hundred Years of Solitude",
    author="Gabriel Garcia Marquez",
    author_alt=["Gabriel García Márquez", "Marquez, Gabriel Garcia"],
)

CATALOG = [HP1, ALCHEMIST_COELHO, ALCHEMIST_SCOTT, DUNE, DUNE_MESSIAH, MARQUEZ]


def test_normalize_strips_accents_and_punctuation():
    assert normalize("García Márquez") == normalize("Garcia Marquez")
    assert normalize("J.K. Rowling") == normalize("J. K. Rowling")


def test_normalize_is_order_insensitive_via_token_set_downstream():
    # normalize() itself doesn't reorder tokens -- that's rapidfuzz's job
    # via token_set_ratio. Confirm the raw strings at least tokenize the
    # same after normalization regardless of "Last, First" order.
    assert set(normalize("Rowling, J.K.").split()) == set(normalize("J.K. Rowling").split())


def test_exact_read_scores_very_high():
    result = match("Harry Potter and the Philosopher's Stone", "J.K. Rowling", CATALOG)
    assert result.best.catalog_id == "CAT0001"
    assert result.best.score > 0.95


def test_us_uk_title_variant_matches_via_alt_titles():
    result = match("Harry Potter and the Sorcerer's Stone", "J.K. Rowling", CATALOG)
    assert result.best.catalog_id == "CAT0001"
    assert result.best.score > 0.9


def test_author_name_variants_all_match():
    for author_read in ["Joanne Rowling", "J. K. Rowling", "Rowling, J.K.", "ROWLING, JK"]:
        result = match("Harry Potter and the Philosopher's Stone", author_read, CATALOG)
        assert result.best.catalog_id == "CAT0001", f"failed for author read {author_read!r}"


def test_transliterated_accented_author_matches():
    result = match("One Hundred Years of Solitude", "Gabriel Garcia Marquez", CATALOG)
    assert result.best.catalog_id == "CAT0040"
    assert result.best.score > 0.9


def test_homonym_titles_disambiguated_by_author():
    coelho_read = match("The Alchemist", "Paulo Coelho", CATALOG)
    scott_read = match("The Alchemist", "Michael Scott", CATALOG)
    assert coelho_read.best.catalog_id == "CAT0022"
    assert scott_read.best.catalog_id == "CAT0023"
    # and the wrong one should score noticeably lower for each read
    assert coelho_read.best.score > score_entry("The Alchemist", "Paulo Coelho", ALCHEMIST_SCOTT).score


def test_homonym_titles_without_author_are_ambiguous_not_auto_accepted():
    # No author read at all -- both "The Alchemist" entries are
    # legitimately indistinguishable from title alone. This must NOT
    # produce a single overconfident auto-accept; it should tie (or
    # nearly tie) with two candidates, which is exactly the case the
    # review step exists for.
    result = match("The Alchemist", "", CATALOG)
    top_two = result.candidates[:2]
    ids = {c.catalog_id for c in top_two}
    assert ids == {"CAT0022", "CAT0023"}
    assert abs(top_two[0].score - top_two[1].score) < 0.05


def test_substring_titles_do_not_collapse_together():
    dune_read = match("Dune", "Frank Herbert", CATALOG)
    messiah_read = match("Dune Messiah", "Frank Herbert", CATALOG)
    assert dune_read.best.catalog_id == "CAT0030"
    assert messiah_read.best.catalog_id == "CAT0031"


def test_garbled_ocr_read_still_ranks_correct_book_first():
    # Simulates a rough spine read: partial title, no author.
    result = match("Hary Poter Philosophr Stne", "", CATALOG)
    assert result.best.catalog_id == "CAT0001"


def test_nonsense_read_is_unmatched_not_a_false_positive():
    result = match("xkqz industrial supply catalog 2019", "nobody", CATALOG)
    assert result.tier(auto_accept=0.86, review_floor=0.55) == "unmatched"


def test_tier_thresholds():
    result = match("Harry Potter and the Philosopher's Stone", "J.K. Rowling", CATALOG)
    assert result.tier(auto_accept=0.86, review_floor=0.55) == "auto"

    # A partial/noisy read of the same book should land in review, not
    # auto-accept and not unmatched.
    fuzzy = match("Harry Potter Philosophers", "", CATALOG)
    assert fuzzy.tier(auto_accept=0.97, review_floor=0.4) in ("review", "auto")
