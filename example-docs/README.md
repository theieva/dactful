# Get started with the example docs

Two fictional documents live in this folder so you can try the full Dactful
loop without risking anything real. (Feyre Archeron is a fantasy character;
Sunny is a made-up utility company. Nothing here belongs to a real person.)

## Play 1: the resume round trip

This one shows the whole redact, work, restore cycle.

1. Start Dactful and open the **Redact** tab.
2. Upload `Feyre_Archeron_Resume.pdf` or `Feyre_Archeron_Resume.docx`
   (either works; the PDF gets its text extracted, the docx keeps its
   formatting).
3. Review what the scan found: her name, email, and phone number. Tick what
   to hide, keep the suggested tags as they are, and click **Redact**. You
   get a redacted copy where the personal details read `[[PERSON_1]]`,
   `[[EMAIL_1]]`, `[[PHONE_1]]`.
4. Normally you'd paste that copy into an AI and ask it to punch up the
   resume. We've done that part for you: `Feyre_Archeron_Resume_v2.docx` is
   the "finished draft that came back from the AI," rewritten and improved,
   tags still in place.
5. Open the **Restore** tab, upload the v2 file, and click
   **Put my info back**. Dactful swaps the real name, email, and phone back
   into the polished resume. That's the loop: the AI improved her resume and
   never saw who she was.

Notice that the v2 is not the same document you redacted: the AI reworded,
restructured, and reformatted it. Restore doesn't care. The mapping matches
tags wherever they ended up, in whatever document now contains them, so your
draft can be transformed as much as you like between redact and restore.

Tip: keep the default tags in step 3. The v2 file expects `[[PERSON_1]]`,
`[[EMAIL_1]]`, and `[[PHONE_1]]`, so renaming them (or having existing
entries in your dictionary that shift the numbering) will leave some tags
unmatched, which Dactful will report rather than guess at.

## Play 2: the electricity bill

This one shows you don't need a perfectly formatted document to get value
out of the process.

1. On the **Redact** tab, upload `Sunny_Electricity_Bill.pdf`.
2. The scan flags the account holder's name, address, and account numbers.
   Confirm what to hide and redact. The output is a plain Word doc: the
   bill's layout is gone, but every charge, rate, and date survives.
3. Upload the redacted docx to any AI and ask: "Can you explain this
   electricity bill to me? Why is it higher than last month?"
4. The AI explains the charges just fine. It never needed the pretty PDF,
   and it never saw whose bill it was.

That's the core idea of Dactful: an AI can be genuinely useful with the
shape and substance of your document, without the private details riding
along.
