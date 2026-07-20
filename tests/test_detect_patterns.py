"""Contextual pattern detection: account/document numbers, zips, states."""

from app.detect import detect_patterns
from app.matching import redact_text, term_rule


def _terms(text, typ):
    return {s.term for s in detect_patterns(text).values() if s.type == typ}


def test_account_number_value_only():
    accts = [s for s in detect_patterns("Account Number: 4471-88231").values() if s.type == "account"]
    assert accts and accts[0].term == "4471-88231"  # just the value, not the label


def test_invoice_and_document_numbers():
    assert "INV-2024-0091" in _terms("Invoice #: INV-2024-0091", "doc_number")
    assert "55871234" in _terms("Statement No. 55871234", "doc_number")
    assert "C-90887" in _terms("Customer ID: C-90887", "doc_number")


def test_zip_codes():
    assert "90210" in _terms("San Francisco, CA 90210", "zip")
    assert "78701-1234" in _terms("Austin, TX 78701-1234", "zip")


def test_state_abbreviation_is_place():
    assert "CA" in _terms("San Francisco, CA 90210", "place")


def test_no_false_positive_without_label():
    # A bare number with no account/invoice label must not be flagged.
    got = detect_patterns("The total was 4471 units.")
    assert not any(s.type in ("account", "doc_number") for s in got.values())


def test_ner_boilerplate_filter():
    from app.detect import _is_ner_boilerplate

    # Form/bill headers spaCy mistakes for entities -> dropped.
    assert _is_ner_boilerplate("Statement Date")
    assert _is_ner_boilerplate("Total Current")
    assert _is_ner_boilerplate("Remittance Slip")
    # Real names/companies -> kept.
    assert not _is_ner_boilerplate("Sunny Electricity")
    assert not _is_ner_boilerplate("John Doe")


def test_redaction_preserves_the_label():
    acct = [s for s in detect_patterns("Account Number: 4471-88231").values() if s.type == "account"][0]
    out = redact_text("Account Number: 4471-88231", [term_rule(acct.term, acct.tag)])
    assert out == "Account Number: [[ACCOUNT_1]]"  # label stays, value hidden
