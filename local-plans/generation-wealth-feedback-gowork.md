Goal: Drew has one PDF on Generation Wealth that lists what he asked for or complained about in the local transcripts (Sept 18, 2026 onward), each item tied to its source transcript with a date, plus a ready-to-send draft reply for each.
Done when: `python3 "/home/drewp/main-projects/automate 247/generation-wealth-feedback/check_progress.py"` passes and `/home/drewp/main-projects/automate 247/generation-wealth-feedback/Generation-Wealth-feedback.pdf` opens, showing a short top-asks list where every entry has a source and date and a draft reply.
Check: python3 "/home/drewp/main-projects/automate 247/generation-wealth-feedback/check_progress.py"
Try: open "/home/drewp/main-projects/automate 247/generation-wealth-feedback/Generation-Wealth-feedback.pdf"
Open: local file PDF, no web server needed

# Generation Wealth Transcript Feedback PDF Plan

## Goal

Create a long, useful PDF from all available local transcripts dated from September 18, 2026 through the current session time.

The PDF should focus on what Generation Wealth said, what he is asking or implying, and what Drew can answer or act on from Drew's computer.

The work should be cheap-first:

- Use cheap Codex/default local work for finding, extracting, sorting, and first drafting.
- Use a stronger model only if a worker clearly needs judgment-heavy synthesis or final writing polish.
- Do not start paid external agents, live provider tests, or broad web research unless Drew explicitly asks later.

## Output Folder

Write all artifacts under:

`/home/drewp/main-projects/automate 247/generation-wealth-feedback`

Expected final files:

- `source-index.md`
- `generation-wealth-quotes.md`
- `answerable-themes.md`
- `report-draft.md`
- `Generation-Wealth-feedback.pdf`
- `review-notes.md`
- `status.json`

## How To Try It

- Open the final PDF and check that it feels like feedback Drew can actually use.
- Look for direct Generation Wealth wording or short paraphrases tied back to source transcripts.
- Confirm the report clearly separates "what he said," "what it means," and "what Drew can answer from this computer."

## Tasks

- [x] Build the transcript source inventory.
  - Find every local transcript source dated September 18, 2026 through the current session time.
  - Check likely locations first: `/home/drewp/main-projects`, audio transcript folders, Discord/voice transcript databases, and project notes.
  - Record exact source paths, dates, speaker clues, and whether Generation Wealth appears.
  - Create `check_progress.py` if it does not exist; the check should pass after this task and validate every listed artifact that exists.
  - Write `source-index.md` and `status.json`.

- [x] Extract everything Generation Wealth said.
  - Use the source inventory only; do not invent quotes or rely on memory.
  - Pull exact quotes when available, and short faithful paraphrases when transcripts are messy.
  - Preserve dates, transcript/source path, and nearby context.
  - Flag unclear speaker attribution instead of pretending it is certain.
  - Write `generation-wealth-quotes.md` and update `status.json`.

- [x] Turn the quotes into answerable themes.
  - Group what Generation Wealth said into practical categories Drew can respond to from his computer.
  - For each category, list the user need, likely question, what Drew can answer, what local evidence is needed, and what should not be overclaimed.
  - Keep the language plain and useful, not academic.
  - Write `answerable-themes.md` and update `status.json`.

- [ ] Draft the long feedback report.
  - Build a report that can become the PDF.
  - Suggested structure: executive summary, source coverage, top feedback themes, direct answer opportunities, draft answers Drew can use, unanswered gaps, next recommended actions.
  - Make it long enough to be genuinely useful, but organized so Drew can skim it.
  - Use a stronger model only here if cheap drafting produces weak synthesis.
  - Write `report-draft.md` and update `status.json`.

- [ ] Create the first PDF.
  - Convert `report-draft.md` into a polished PDF using local tooling such as ReportLab, WeasyPrint, Pandoc, or another installed converter.
  - Use readable margins, clear headings, page numbers if practical, and enough whitespace for review.
  - Avoid tiny dense text.
  - Write `Generation-Wealth-feedback.pdf` and update `status.json`.

- [ ] Review the PDF and prepare the Discord-ready summary.
  - Inspect the PDF for missing pages, unreadable formatting, broken characters, awkward spacing, and whether the report answers Drew's actual request.
  - Write `review-notes.md` with what changed, what is strong, what still needs Drew's taste check, and any transcript gaps.
  - Append the final PDF path to `/home/drewp/main-projects/automate 247/.ccdb-attachments-1551392809292931072`.
  - If a Markdown summary card is useful, create it and append that path too.
  - Update `status.json`.
