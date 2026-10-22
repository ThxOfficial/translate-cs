# AGENTS.md

## Project

translate-cs is a Codex/Claude Code/OpenClaw skill specialized for computer science, AI, LLM, agent, and mathematical literature. GitHub repository: `ThxOfficial/translate-cs`.

Default workflow: the host model reads the original, assesses length/context, translates directly or uses TXT for a long source, edits against the original, then generates and visually checks PDF. Do not require PDF-to-Markdown preprocessing. The existing Markdown pipeline is opt-in or for resuming legacy runs.

## Structure

- `SKILL.md` — Skill definition, the orchestration logic that Codex / Claude Code / OpenClaw follows
- `scripts/source_workflow.py` — Source-first registration, optional TXT, version-bound editorial evidence and reviewed PDF build
- `references/source-first.md` — Default workflow commands and schemas
- `references/legacy-pipeline.md` — Compatible opt-in/resume Markdown pipeline
- `scripts/convert.py` — PDF/DOCX/EPUB → Markdown chunks (via Calibre HTMLZ); Markdown input (e.g. from MinerU/Marker) skips Calibre
- `scripts/manifest.py` — SHA-256 chunk tracking and merge validation
- `scripts/glossary.py` — Term-consistency glossary; per-chunk term tables injected into sub-agent prompts
- `scripts/chunk_context.py` — Read-only previous/next chunk excerpts injected into sub-agent prompts
- `scripts/meta.py` — Per-chunk sub-agent observation file schema
- `scripts/merge_meta.py` — Batch-boundary merge of sub-agent observations into the canonical glossary
- `scripts/run_state.py` — Selective re-translation planner and run_state.json recorder
- `scripts/merge_and_build.py` — Merge translated chunks → HTML/DOCX/EPUB/PDF
- `scripts/calibre_html_publish.py` — Calibre format conversion wrapper
- `scripts/template.html`, `scripts/template_ebook.html` — HTML templates

## Testing changes

Run stdlib unit tests for code changes. New source-first tests are in `tests/test_source_workflow.py`; reading and semantic accuracy still require actual source/model evidence. The commands below exercise the preserved legacy pipeline, not the default source-first path.

Quick smoke test:

```bash
python3 scripts/convert.py /path/to/small.pdf --olang zh
# then run translation via the skill
python3 scripts/merge_and_build.py --temp-dir <name>_temp --title "test"
```

Full baseline test:

```bash
mkdir -p tests/.artifacts
cd tests/.artifacts
python3 ../../scripts/convert.py ../baselines/standard-alice/standard-alice.epub --olang zh
# then run translation via the skill
python3 ../../scripts/merge_and_build.py --temp-dir standard-alice_temp --title "test"
```

Verify: all output_chunk*.md files exist, manifest validation passes, output formats generate.

## Conventions

- Only `chunk*.md` naming — no `page*` legacy support
- Pipeline output artifacts use the canonical names `book.html`, `book_doc.html`, `book.docx`, `book.epub`, `book.pdf`. Internal scripts and skip/cache logic depend on these names; if title-based filenames are added later they must be optional aliases/copies, not silent replacements
- SKILL.md frontmatter must stay single-line per field (OpenClaw parser requirement)
- Script paths in SKILL.md use `{baseDir}` not hardcoded paths
- Subagent instructions in SKILL.md must be platform-neutral (work on Codex, Claude Code, OpenClaw)
- Checked-in baseline inputs live under `tests/baselines/<book-id>/`; generated full-pipeline outputs live under `tests/.artifacts/`
- README changes must be synced to both README.md and README.zh-CN.md
- GitHub releases follow `.claude/commands/release.md`: push `main`, then create and push a `vX.Y.Z` tag when a versioned release is requested. A normal push does not create a release.

## Do not

- Do not reintroduce `page*` file support — it was intentionally removed
- Do not hardcode per-runtime skill paths (`~/.agents/skills/`, `~/.claude/skills/`) in SKILL.md — use `{baseDir}`
- Do not put platform-specific tool names (Agent, sessions_spawn) in `allowed-tools` as the only option — keep the whitelist cross-platform
- Do not add mtime-based incremental rebuild for HTML/format generation — the current skip logic is intentionally simple (existence check). Metadata/template changes require manual cleanup. This is documented in the README.

## Cursor Cloud specific instructions

### Environment

- Python 3.12+ is pre-installed; no version manager needed.
- System dependencies (Calibre, Pandoc) and pip packages (pypandoc, beautifulsoup4) are installed by the update script.
- Unit tests depend only on the Python stdlib (no pip packages or external binaries needed; `python3 -m unittest discover` runs as-is).

### Running tests

- **Unit tests (CI-equivalent):** `python3 -m unittest discover -s tests -p 'test_*.py' -v` — runs from repo root, no setup needed.
- **Compile check:** `python3 -m compileall scripts tests`

### Full pipeline integration test

Run from `tests/.artifacts/` to keep generated files out of the repo root:

```bash
mkdir -p tests/.artifacts && cd tests/.artifacts
python3 ../../scripts/convert.py ../baselines/standard-alice/standard-alice.epub --olang zh
# Create mock output_chunk*.md files (copy source chunks) since actual translation requires LLM subagents
for f in standard-alice_temp/chunk*.md; do cp "$f" "standard-alice_temp/output_$(basename $f)"; done
python3 ../../scripts/merge_and_build.py --temp-dir standard-alice_temp --title "test"
```

### Known issues

- Ubuntu's Calibre 7.6.0 package has an EPUB generation bug (bytes/str mismatch in `container.py`). DOCX and PDF generation work fine. This is a distro packaging issue, not a codebase bug.
- `pypandoc` installs its CLI script to `~/.local/bin` which may not be on PATH, but the Python library import works regardless.
