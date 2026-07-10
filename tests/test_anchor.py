from srx.anchor import SIM_THRESHOLD, find_anchor, normalize_text

PAGES = [
    "Background and methods of the trial. " * 3,
    "A total of 124 patients were ran-\ndomised   to receive either drug X or placebo.",
    "Mean age was 61.2 years (SD 9.4) in the intervention arm.",
]


def test_normalize_text_repairs_hyphenation_and_whitespace():
    assert normalize_text("ran-\ndomised   to") == "randomised to"


def test_exact_quote_verifies():
    r = find_anchor("124 patients were randomised", PAGES, claimed_page=2)
    assert r.status == "verified"
    assert r.page == 2
    assert r.score == 1.0


def test_off_by_one_page_verifies():
    r = find_anchor("Mean age was 61.2 years", PAGES, claimed_page=2)
    assert r.status == "verified"
    assert r.page == 3


def test_near_match_above_threshold_verifies():
    # one-character variant ("randomized" vs "randomised") stays above 0.90
    r = find_anchor("124 patients were randomized to receive", PAGES, claimed_page=2)
    assert r.status == "verified"
    assert r.score >= SIM_THRESHOLD


def test_fabricated_quote_fails():
    r = find_anchor("mortality was reduced by 40 percent", PAGES, claimed_page=2)
    assert r.status == "failed"
    assert r.score < SIM_THRESHOLD


def test_two_pages_away_fails():
    r = find_anchor("Mean age was 61.2 years", PAGES, claimed_page=1)
    assert r.status == "failed"


def test_scanned_short_circuits_to_vision_only():
    r = find_anchor("anything", ["", ""], claimed_page=1, scanned=True)
    assert r.status == "vision-only"
    assert r.page == 1
