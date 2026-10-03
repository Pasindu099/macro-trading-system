## Token and effort rules

Reading
- Read only what the current part needs. Use grep/rg and line ranges
  instead of opening whole files. Never open files > 500 lines in full.
- Do not re-read files already summarised in REPORT_*.md, DECISIONS_*.md
  or PROGRESS.md unless you are about to edit them.
- Do not explore unrelated modules "for context".

Running commands
- Tests: run only the tests for the code you changed
  (pytest path/to/test_file.py -q --maxfail=1). Run the full suite ONCE,
  at the end of each part, with -q.
- Never print large outputs: pipe through tail -n 40 or summarise.
  No full DB dumps, no full API responses, no full logs.
- Long jobs (backfills): run them, poll the status at most every few
  minutes, report only the final counts.
- If a command fails twice the same way, stop and report instead of
  trying variations.

Editing
- Edit files in place with minimal diffs. Never rewrite a whole file to
  change a few lines.
- No refactoring, renaming or reformatting outside the current part.
- No new abstractions unless the decisions file asks for them.

Reporting
- Reports: max ~60 lines. Tables and bullet facts, no restating the
  prompt, no explanations of what you are about to do.
- Chat replies to me: max 10 lines. Point to the report file instead of
  repeating its content.
- Don't paste code into reports; reference file:line.

Stopping
- If a decision is genuinely needed, ask ONE concise question with your
  recommended option, then stop. Do not investigate alternatives at length
  before asking.
- Do not continue past a STOP in the decisions file.
