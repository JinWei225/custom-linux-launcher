from launcher.ranking import best_score, fuzzy_score


def test_no_match_returns_none():
    assert fuzzy_score("xyz", "Firefox") is None
    assert fuzzy_score("fx", "") is None


def test_empty_query_matches_everything_with_zero():
    assert fuzzy_score("", "Firefox") == 0.0
    assert fuzzy_score("   ", "Firefox") == 0.0


def test_case_insensitive_exact_match():
    assert fuzzy_score("FIREFOX", "Firefox") == 1.0


def test_bands_are_ordered():
    exact = fuzzy_score("files", "Files")
    prefix = fuzzy_score("fi", "Files")
    boundary = fuzzy_score("code", "Visual Studio Code")
    substring = fuzzy_score("ire", "Firefox")
    initials = fuzzy_score("vsc", "Visual Studio Code")
    scattered = fuzzy_score("fex", "Firefox")
    assert exact > prefix > boundary > substring > initials > scattered


def test_shorter_candidate_wins_for_same_prefix():
    assert fuzzy_score("term", "Terminal") > fuzzy_score("term", "Terminal Emulator Settings")


def test_boundary_substring_found_after_mid_word_occurrence():
    # "code" first occurs mid-word ("decode"), but also at a word start.
    assert fuzzy_score("code", "decode code") >= 0.7


def test_initials_fall_back_to_plain_subsequence():
    assert fuzzy_score("fox", "firefox") is not None
    assert fuzzy_score("ffx", "firefox") is not None


def test_best_score_picks_highest_candidate_and_skips_empty():
    assert best_score("gh", ["GitHub", "", "gh"]) == 1.0
    assert best_score("zz", ["GitHub"]) is None
