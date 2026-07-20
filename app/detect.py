"""
Detection: propose candidate sensitive spans. Three layers,
all local, in descending confidence:

  Layer 1 - Saved dictionary : terms you've confirmed before (exact match).
  Layer 2 - Deterministic patterns : SSN, EIN, phone, email, etc. (regex).
  Layer 3 - Local NER (spaCy) : PERSON / ORG / GPE proposals (optional).

Everything is proposed, never applied. The caller (the review UI) decides.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Layer 2: deterministic patterns
# ---------------------------------------------------------------------------
# Each entry: (type, regex, tag_prefix, validator?). Validators cut false
# positives (e.g. Luhn for cards, checksum for routing numbers).


def _luhn_ok(digits: str) -> bool:
    d = [int(c) for c in digits if c.isdigit()]
    if len(d) < 13:
        return False
    checksum = 0
    parity = len(d) % 2
    for i, n in enumerate(d):
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        checksum += n
    return checksum % 10 == 0


def _routing_ok(digits: str) -> bool:
    d = [int(c) for c in digits if c.isdigit()]
    if len(d) != 9:
        return False
    checksum = (
        3 * (d[0] + d[3] + d[6])
        + 7 * (d[1] + d[4] + d[7])
        + (d[2] + d[5] + d[8])
    )
    return checksum % 10 == 0


@dataclass
class PatternDef:
    type: str
    regex: re.Pattern
    prefix: str
    validator: Optional[callable] = None
    value_group: int = 0  # which capture group is the value to redact (0 = whole match)


def _has_digit(s: str) -> bool:
    return len(s.strip()) >= 3 and any(c.isdigit() for c in s)


# US state abbreviations, used to spot state + zip in an address ("City, CA 90210").
_US_STATES = (
    "AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|"
    "MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC"
)


PATTERNS: List[PatternDef] = [
    PatternDef("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "EMAIL"),
    PatternDef("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "SSN"),
    PatternDef("ein", re.compile(r"\b\d{2}-\d{7}\b"), "EIN"),
    PatternDef(
        "phone",
        re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)"),
        "PHONE",
    ),
    PatternDef("iban", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"), "IBAN"),
    PatternDef(
        "credit_card",
        re.compile(r"\b(?:\d[ -]?){13,19}\b"),
        "CARD",
        validator=_luhn_ok,
    ),
    PatternDef(
        "routing",
        re.compile(r"\b\d{9}\b"),
        "ROUTING",
        validator=_routing_ok,
    ),
    PatternDef(
        "url",
        re.compile(r"\bhttps?://[^\s<>\"')]+"),
        "URL",
    ),
    # Account number: redact just the value after an "account"/"acct" label.
    PatternDef(
        "account",
        re.compile(
            r"(?i)\b(?:account|acct)\b\.?\s*(?:number|no|num|#|id)?\.?\s*[:#]?\s*"
            r"([0-9A-Za-z][0-9A-Za-z\-]{3,})"
        ),
        "ACCOUNT",
        validator=_has_digit,
        value_group=1,
    ),
    # Document/reference numbers (invoice, order, statement, policy, etc.).
    PatternDef(
        "doc_number",
        re.compile(
            r"(?i)\b(?:invoice|inv|document|doc|order|reference|ref|statement|"
            r"confirmation|policy|claim|customer|member)\b\.?\s*"
            r"(?:number|no|num|#|id)?\.?\s*[:#]?\s*([0-9A-Za-z][0-9A-Za-z\-]{2,})"
        ),
        "DOCUMENT",
        validator=_has_digit,
        value_group=1,
    ),
    # Zip codes: standalone ZIP+4, or a 5/9-digit zip right after a state.
    PatternDef("zip", re.compile(r"\b\d{5}-\d{4}\b"), "ZIP"),
    PatternDef(
        "zip",
        re.compile(r"\b(?:" + _US_STATES + r")\s+(\d{5}(?:-\d{4})?)\b"),
        "ZIP",
        value_group=1,
    ),
    # State abbreviation in an address (immediately before a zip). Grouped with
    # cities under "place".
    PatternDef(
        "place",
        re.compile(r"\b(" + _US_STATES + r")\b(?=\s+\d{5}(?:-\d{4})?\b)"),
        "PLACE",
        value_group=1,
    ),
]

# Dollar amounts: off by default, opt-in per document.
MONEY_PATTERN = PatternDef(
    "money", re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?\b"), "AMOUNT"
)


@dataclass
class Suggestion:
    term: str                       # exact source text
    type: str                       # ssn | email | person | org | ...
    source: str                     # dictionary | pattern | ner
    tag: str                        # proposed [[TAG]]
    count: int = 0                  # occurrences in the document
    contexts: List[str] = field(default_factory=list)
    checked: bool = False           # pre-checked in the UI?


def _context(text: str, start: int, end: int, width: int = 40) -> str:
    a = max(0, start - width)
    b = min(len(text), end + width)
    lead = "…" if a > 0 else ""
    tail = "…" if b < len(text) else ""
    snippet = text[a:b].replace("\n", " ")
    return f"{lead}{snippet}{tail}"


def _add(bag: Dict[str, Suggestion], key: str, sug_factory, text, start, end):
    sug = bag.get(key)
    if sug is None:
        sug = sug_factory()
        bag[key] = sug
    sug.count += 1
    if len(sug.contexts) < 5:
        sug.contexts.append(_context(text, start, end))


def detect_patterns(
    text: str, include_money: bool = False, taken: Optional[set] = None
) -> Dict[str, Suggestion]:
    from .tags import next_tag

    taken = taken if taken is not None else set()
    bag: Dict[str, Suggestion] = {}
    defs = PATTERNS + ([MONEY_PATTERN] if include_money else [])
    for pd in defs:
        for m in pd.regex.finditer(text):
            token = m.group(pd.value_group)  # value_group 0 = whole match
            if not token or (pd.validator and not pd.validator(token)):
                continue
            key = f"{pd.type}:{token}"
            if key not in bag:
                tag = next_tag(pd.prefix, taken)  # globally unique
            else:
                tag = bag[key].tag
            _add(
                bag, key,
                lambda t=token, ty=pd.type, tg=tag: Suggestion(
                    term=t, type=ty, source="pattern", tag=tg, checked=True
                ),
                text, m.start(pd.value_group), m.end(pd.value_group),
            )
    return bag


def detect_dictionary(text: str, dictionary: List[Dict]) -> Dict[str, Suggestion]:
    """Exact/case-insensitive matches for previously-confirmed terms."""
    from .matching import term_rule, find_matches

    if not dictionary:
        return {}
    rules = [term_rule(e["term"], e["tag"], source="dictionary", weight=1000)
             for e in dictionary]
    bag: Dict[str, Suggestion] = {}
    for m in find_matches(text, rules):
        key = f"dict:{m.rule.label}:{m.text.lower()}"
        _add(
            bag, key,
            lambda mm=m: Suggestion(
                term=mm.text, type="saved", source="dictionary",
                tag=mm.rule.label, checked=True,
            ),
            text, m.start, m.end,
        )
    return bag


# spaCy's small model over-fires on Title-Case labels in forms and bills
# ("Statement Date", "Total Current", "Remittance Slip"). We drop a name/company
# guess when every word in it is generic document boilerplate, and strip
# surrounding punctuation so "Sunny Electricity Inc. -" becomes clean.
_NER_STOP = {
    "statement", "date", "dates", "total", "subtotal", "current", "previous", "amount",
    "amounts", "due", "balance", "balances", "invoice", "invoices", "account", "accounts",
    "payment", "payments", "remittance", "slip", "summary", "usage", "charge", "charges",
    "outage", "outages", "emergency", "emergencies", "service", "services", "customer",
    "customers", "billing", "meter", "meters", "rate", "rates", "period", "periods", "number",
    "numbers", "page", "pages", "detail", "details", "message", "messages", "enroll",
    "enrollment", "enrolled", "paperless", "tax", "taxes", "state", "sales", "mail", "box",
    "remit", "enclosed", "thank", "thanks", "you", "dear", "please", "notice", "notices",
    "important", "information", "info", "terms", "conditions", "plan", "plans", "quantity",
    "description", "unit", "units", "price", "prices", "fee", "fees", "credit", "credits",
    "debit", "deposit", "deposits", "adjustment", "adjustments", "activity", "reference",
    "confirmation", "order", "orders", "delivery", "shipping", "contact", "phone", "email",
    "website", "hours", "new", "past", "next", "gross", "net", "paid", "payable", "overdue",
    "autopay", "auto", "monthly", "annual", "daily", "reading", "readings", "from", "for",
    "and", "or", "the", "of", "to", "your", "our", "this", "with", "per", "update", "updates",
    "alert", "alerts", "reminder", "reminders", "view", "online",
}

_NER_STRIP = " \t\r\n.,;:!?-–—&/|()[]\"'"


def _is_ner_boilerplate(token: str) -> bool:
    words = re.findall(r"[A-Za-z]+", token.lower())
    return not words or all(w in _NER_STOP for w in words)


def detect_ner(text: str, taken: Optional[set] = None) -> Dict[str, Suggestion]:
    """Optional local spaCy NER. Returns {} if spaCy/model unavailable."""
    from .tags import next_tag

    taken = taken if taken is not None else set()
    nlp = _load_spacy()
    if nlp is None:
        return {}

    # Neutral, personal-use-friendly labels: organizations are "company", not
    # "org"/"client" (the latter implies business/professional use).
    label_prefix = {"PERSON": "PERSON", "ORG": "COMPANY", "GPE": "PLACE", "FAC": "PLACE"}
    label_type = {"PERSON": "person", "ORG": "company", "GPE": "place", "FAC": "place"}
    bag: Dict[str, Suggestion] = {}
    seen_tag: Dict[str, str] = {}
    for ent in nlp(text).ents:
        if ent.label_ not in label_prefix:
            continue
        token = ent.text.strip(_NER_STRIP)
        if len(token) < 2 or _is_ner_boilerplate(token):
            continue
        norm = token.lower()
        key = f"ner:{ent.label_}:{norm}"
        if key not in bag:
            if norm in seen_tag:
                tag = seen_tag[norm]
            else:
                tag = next_tag(label_prefix[ent.label_], taken)  # globally unique
                seen_tag[norm] = tag
        else:
            tag = bag[key].tag
        _add(
            bag, key,
            lambda t=token, ty=label_type[ent.label_], tg=tag: Suggestion(
                term=t, type=ty, source="ner", tag=tg, checked=False
            ),
            text, ent.start_char, ent.end_char,
        )
    return bag


_SPACY_CACHE = {"loaded": False, "nlp": None}


def _load_spacy():
    if _SPACY_CACHE["loaded"]:
        return _SPACY_CACHE["nlp"]
    _SPACY_CACHE["loaded"] = True
    try:
        import spacy  # type: ignore

        _SPACY_CACHE["nlp"] = spacy.load("en_core_web_sm")
    except Exception:
        _SPACY_CACHE["nlp"] = None
    return _SPACY_CACHE["nlp"]


def analyze(
    text: str,
    dictionary: Optional[List[Dict]] = None,
    include_money: bool = False,
    use_ner: bool = True,
) -> List[Suggestion]:
    """Run all layers and merge. Dictionary wins over pattern wins over NER when
    the same exact text is proposed by more than one layer."""
    bag: Dict[str, str] = {}  # lowercased term -> owning source rank
    rank = {"dictionary": 3, "pattern": 2, "ner": 1}
    merged: Dict[str, Suggestion] = {}

    # Seed the "taken" set with every tag already in the dictionary, so newly
    # minted pattern/NER tags never collide with a saved one (or each other).
    dictionary = dictionary or []
    taken = {e["tag"] for e in dictionary if e.get("tag")}

    layers: List[Dict[str, Suggestion]] = [
        detect_dictionary(text, dictionary),
        detect_patterns(text, include_money=include_money, taken=taken),
    ]
    if use_ner:
        layers.append(detect_ner(text, taken=taken))

    for layer in layers:
        for sug in layer.values():
            k = sug.term.lower()
            if k in bag and rank[sug.source] <= rank[bag[k]]:
                continue
            bag[k] = sug.source
            merged[k] = sug

    # Order: dictionary, then pattern, then ner; then by descending count.
    return sorted(
        merged.values(),
        key=lambda s: (-rank[s.source], -s.count, s.term.lower()),
    )
