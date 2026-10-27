# DarkShelter — build rules for this repo

- Read `docs/PLAN.md` (why) and `docs/SPEC.md` (what to build) in full before writing code. SPEC wins where they disagree.
- This folder is the repo root. Ignore SPEC §0's "Your workspace" bullet and any `/home/claude` paths: you are on the team's own laptop, with internet.
- Use a virtual environment (`.venv`, Python 3.11 or 3.12) and install packages with pip inside it. You may install `playwright` and its Chromium for the UI smoke test.
- The real models are already on this machine: Whisper `small` and `base` in the default Hugging Face cache, and `qwen2.5:1.5b` in Ollama at http://127.0.0.1:11434. Test the real adapters here, and keep the mocks and the Ollama stub (the tests use them). Don't download other models.
- Build in the order of SPEC §12 (C1 → C5), apply its 2-hour stop-the-line rule, and verify each checkpoint before starting the next.
- After each checkpoint, commit locally (`git add -A && git commit -m "C<n>: …"`). Never push and never create remote repos. Keep `.venv/`, `data/run/` and model files out of git via `.gitignore`.
- Keep `PROGRESS.md` current: time of each checkpoint, what works, what's mocked, what's verified with the real models.
- Work without asking questions — the team is asleep. When something is ambiguous, choose what best fits PLAN.md and note the decision in PROGRESS.md.
- Show evidence: when you say a check passes, show the command and its output.