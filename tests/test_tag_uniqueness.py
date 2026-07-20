"""Tag ids must always be globally unique - never two [[EMAIL_1]]s."""

from app.detect import analyze
from app.tags import next_tag, norm_key


def test_norm_key_ignores_case_punctuation_whitespace():
    assert norm_key("Company Name Inc.") == norm_key("company Name  inc")
    assert norm_key("O'Brien & Co.") == norm_key("obrien  co")


def test_next_tag_skips_taken():
    taken = {"[[EMAIL_1]]", "[[EMAIL_2]]"}
    assert next_tag("EMAIL", taken) == "[[EMAIL_3]]"
    assert "[[EMAIL_3]]" in taken


def test_two_emails_in_one_doc_get_distinct_tags():
    sugs = analyze("Contact a@x.com and also b@y.com", dictionary=[], use_ner=False)
    tags = [s.tag for s in sugs if s.type == "email"]
    assert len(tags) == 2
    assert len(set(tags)) == 2


def test_new_tag_avoids_dictionary_collision():
    # The dictionary already used EMAIL_1 for a different address (not in this
    # text). A newly detected email must not reuse EMAIL_1.
    dictionary = [{"term": "old@z.com", "tag": "[[EMAIL_1]]"}]
    sugs = analyze("Reach me at new@w.com.", dictionary=dictionary, use_ner=False)
    email = next(s for s in sugs if s.type == "email")
    assert email.tag != "[[EMAIL_1]]"
