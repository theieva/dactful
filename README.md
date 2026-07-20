# Dactful

**Tactful redaction. Your documents never leave your machine.**
An experiment of Verdant Industries LLC.

Dactful strips sensitive information out of a document before you paste it into
an AI (Claude, ChatGPT, Gemini), then puts the real values back into the
finished draft afterward. It runs entirely on your machine: no server, no
account, no telemetry, no outbound network calls of any kind.

Dactful is open source under the [MIT License](LICENSE).

**No warranty:** Dactful gives you control over what gets redacted; it does not
guarantee completeness. You are the reviewer. Always double-check the redacted
output yourself before sharing it anywhere.

---

## Quick start

```bash
cd dactful
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

Your browser opens at <http://127.0.0.1:8000>. The server binds to 127.0.0.1
only; nothing is reachable from outside your machine.

First time? Try the guided walkthrough with the fictional sample documents in
[example-docs](example-docs/README.md): redact a resume, restore the polished
version, and get an AI to explain an electricity bill it can't identify.

### Desktop app (macOS)

Prefer a native window over a browser tab? Run:

```bash
python desktop.py
```

Or build the double-clickable app and disk image:

```bash
pip install pyinstaller pywebview
pyinstaller dactful.spec --noconfirm   # -> dist/Dactful.app
./build_dmg.sh                         # -> dist/Dactful-<version>.dmg
```

`dist/Dactful.app/Contents/MacOS/Dactful --smoke` runs a quick self-check of a
built bundle (server, pattern engine, and the bundled spaCy model). Builds are
unsigned: your own Mac opens them fine, but a downloaded copy triggers a
Gatekeeper warning unless you sign and notarize it with an Apple Developer ID.

---

## What it handles

**Inputs:** Word documents (`.docx`), text-based PDFs, screenshots and images
(read on-device with Apple Vision OCR, macOS only), or pasted text.

**Detection, in three local layers:**

1. **Your dictionary:** terms you've confirmed in past redactions are matched
   exactly, with the same tags as before.
2. **Deterministic patterns:** emails, SSNs, EINs, phone numbers, IBANs,
   credit cards (Luhn-checked), bank routing numbers (checksum-checked), URLs,
   ZIP codes, US state abbreviations, account numbers, document numbers
   (invoice / order / statement / policy), and, opt-in, dollar amounts.
   Labeled values keep their label: "Account Number: [[ACCOUNT_1]]".
3. **Optional local NER:** a small spaCy model (runs fully offline,
   deterministic, no LLM) suggests people, companies, and places it spots.

**Review before anything happens:** every candidate is shown with its context.
You tick what to redact, edit tags, and add anything the scan missed. Nothing
is redacted without your say-so.

**Context-aware output:** a file in gives a redacted `.docx` out (formatting
preserved, including tables, headers, footers, footnotes, and document
properties); pasted text in gives redacted text out, ready to copy.

**Images in PDFs:** embedded pictures and vector graphics (charts, logos) are
extracted and shown as thumbnails. Keep the ones the AI should see; drop
anything private. OCR flags images that appear to contain sensitive text.
Kept images are embedded in the redacted `.docx`. Dactful hides text, not
pixels: it cannot black out words drawn inside an image, so review each one.

**Validation sweep:** after redacting, Dactful re-reads every surface of the
file it just produced and refuses to release it if any confirmed term
survived. Failing loudly beats false confidence.

**Restore:** when the finished draft comes back from the AI, paste it (or
upload the `.docx`) on the Restore tab. Dactful swaps the real values back in,
repairs tags the AI mangled (`**[[X]]**`, `[[ X ]]`, and friends), and reports
any leftover tags it has no value for.

**Local Tag Dictionary:** a tab to view, add, and delete your saved term-tag
pairs, choose where the dictionary file lives (with a sync-detection badge
that warns if the chosen folder syncs to iCloud Drive or similar), and see
whether each entry came from a redaction or was added by hand.

**Mom and Dad mode:** a toggle that simplifies the whole UI for non-technical
family, with plainer explanations and fewer knobs.

---

## Why it's trustworthy

The hard, easy-to-get-wrong parts are covered by tests (`tests/`):

| Risk | Test |
|------|------|
| `Clienty Corp` half-eaten into `[[COMPANY_1]] Corp` | `test_substring_trap` |
| Longest-match-first across overlapping entities | `test_overlapping_entities_longest_first` |
| Word splitting a name across runs (silent-miss trap) | `test_docx_run_fragmentation` |
| Terms hiding in headers / footers / author metadata | `test_docx_hidden_surfaces` |
| Formatting of untouched text preserved | `test_docx_preserves_untouched_formatting` |
| Possessives (`Acme's`) left intact | `test_possessive_preserved` |
| Redact then restore returns the original exactly | `test_round_trip_docx` |
| LLM-mangled tags (`**[[X]]**`, `[[ X ]]`) repaired | `test_restore_repairs_mangled_tags` |
| Dropped tags reported, not silently missed | `test_restore_reports_leftover_unknown_tag` |
| Two emails never share a tag | `test_two_emails_in_one_doc_get_distinct_tags` |
| New tags never collide with dictionary tags | `test_new_tag_avoids_dictionary_collision` |

Run them with:

```bash
pytest tests/ -q
```

The security posture is defense-in-depth for a localhost app: host allowlist
(anti DNS-rebinding), CSRF guard on state-changing calls, 50 MB body cap,
hardened XML parsing (no XXE / entity expansion), zip-bomb and path-traversal
guards, and `0600` permissions on every local file that holds your data.

---

## Tags

Tags are always `[[UPPER_SNAKE]]`: bracketed so they survive markdown, stay
visually obvious in LLM output, and don't get "helpfully" reworded. Ids are
globally unique across your whole history: once something is `[[COMPANY_1]]`,
nothing else ever gets that tag, because your dictionary remembers it.

---

## Where your data lives

- **Documents:** processed in a per-session temp workspace, never written
  elsewhere unless you download them.
- **Dictionary:** your confirmed terms live in
  `~/.dactful/dactful_dictionary.json` (file mode `0600`). The Local Tag
  Dictionary tab can move it to a folder of your choosing; `DACTFUL_DICT`
  overrides the location for a single run.
- **Mappings:** each redaction's tag-to-value map is kept in
  `~/.dactful/mappings/` (also `0600`) so Restore works without you managing
  files. Mapping files contain exactly the info you removed. Keep them local.

---

## Known limitations

- **NER is a small local model.** It's imprecise on forms and bills; a
  boilerplate filter cuts the worst false positives, but you should expect to
  confirm or add names by hand sometimes (they're remembered after the first
  time).
- **Images are not pixel-redacted.** Keeping an image sends it exactly as-is;
  the OCR warning and thumbnail review exist so you can decide.
- **PDF layout is not preserved.** PDF text is extracted into a plain `.docx`;
  there is no true "keep the original PDF's look" round-trip.
- **OCR, the folder picker, and sync detection are macOS-only.** The rest
  degrades gracefully on other platforms.

---

## Project layout

```
app/
  matching.py      core engine: longest-match-first, non-overlapping
  docx_redact.py   Word run-fragmentation redaction + hidden surfaces
  restore.py       tag -> value, with mangled-tag repair
  detect.py        dictionary + patterns + optional NER
  service.py       orchestration + the validation sweep
  ingest.py        docx / pdf / image / text -> one docx path
  ocr.py           on-device Apple Vision OCR (macOS)
  pdf_images.py    raster + vector image extraction from PDFs
  mapping.py       human-readable guide + machine map (.json)
  mappings_store.py durable per-redaction mappings
  dictionary.py    local saved-terms store
  tags.py          tag normalization + globally unique ids
  config.py        local config (dictionary location)
  main.py          FastAPI app + security middleware (binds 127.0.0.1 only)
static/            the UI (plain HTML/CSS/JS, fonts vendored)
assets/            logomark sources + app icon
tests/             correctness, pattern, and storage fixtures
run.py             browser launcher
desktop.py         native-window launcher (pywebview)
dactful.spec       PyInstaller build for Dactful.app
build_dmg.sh       disk image builder
```
