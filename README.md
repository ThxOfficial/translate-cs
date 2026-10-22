# Translate-CS

English | [中文](README.zh-CN.md)

`translate-cs` is a Codex skill for translating computer science, AI, LLM, agent, and mathematical papers and books. The model reads the original, chooses direct translation or TXT for a long document, edits against the source, then generates a reviewed PDF.

---

## How It Works

```
Original source file (PDF/DOCX/EPUB/TXT/other readable source)
  │
  ▼
Model reads original pages/sections and assesses length + context budget
  │
  ▼
Manageable source → translate directly
Long source       → export source.txt → translate bounded sections
  │  original pages remain the authority for math, tables and figures
  ▼
Edit and compare every requested source unit; record current review evidence
  │
  ▼
Typeset the edited translation → book.pdf → visual inspection
```

There is no mandatory source-to-Markdown conversion. The model chooses the route from actual source density and available context, rather than a fixed page threshold. Short sources need no TXT; long runs retain source locations, batch drafts and terminology. `output.md` is the edited target-language typesetting source created during/after translation.

## Features

- **Original-first reading** — use actual native document/page/vision tools before choosing an extraction path
- **Model-assessed TXT fallback** — long documents use UTF-8 TXT, with original-page checks for extraction loss
- **Editing before PDF** — source coverage, terms, math/code, figures/tables, claims/citations and language are reviewed before rendering
- **Version-bound evidence** — changed source, TXT, drafts, terms/progress, translation or images invalidate editorial review
- **PDF by default** — installed Pandoc plus Word or Chromium/Edge; visual inspection is recorded separately
- **Persistent long-document progress** — section/page ranges, batch drafts and terminology, with optional fresh worker contexts
- **Multi-language** — zh, en, ja, ko, fr, de, es (extensible)
- **Legacy compatibility** — the existing generic/academic Markdown pipeline, glossary, manifest, selective reruns and optional DOCX/EPUB outputs remain available explicitly or for old runs

## Default Source-First Workflow

Ask the model to translate the source directly. It records its actual reading/length assessment, registers the unchanged source, and exports TXT only if it selected the long-document path:

```bash
python3 scripts/source_workflow.py prepare paper.pdf --assessment-file assessment.json --target-lang zh
# Only when the model selected "txt":
python3 scripts/source_workflow.py extract-txt paper_temp
```

After translation and editing, it records complete source-to-translation comparison and then builds the PDF:

```bash
python3 scripts/source_workflow.py record-review paper_temp --evidence-file editorial-evidence.json
python3 scripts/source_workflow.py build-pdf paper_temp --title "Translated title"
```

See the [execution contract](references/source-first.md) for actual model-authored assessment/review schemas, page coverage, TXT correction, progress, and visual inspection. The scripts do not call an LLM or prove semantic accuracy. Missing/stale editorial evidence blocks PDF generation; successful file generation remains `needs_visual_review` until the actual PDF has been inspected. Use `--rebuild` after changing title or rendering inputs; failed native rebuilds retain the previous PDF without declaring it current.

The model must have reliable source-reading tools. TXT extraction does not preserve all PDF math/layout; inspect the original when correcting it. Models, OCR services and source uploads are never enabled automatically. Legacy test evidence below does not certify the new workflow's real-world translation accuracy.

Validation for this change: the 320-test baseline passed; all 333 stdlib tests now pass, including 13 source-first regressions. A real two-page synthetic PDF passed the new prepare/extract CLI with installed `pdftotext`, retaining both pages and form-feed separators without creating source Markdown. Compilation and skill validation passed. This round did not evaluate actual model translation or rerun the Word/Chromium PDF renderer.

## Legacy Academic Translation (Stages A, B and C)

This section applies only when explicitly choosing the [legacy pipeline](references/legacy-pipeline.md) or resuming its existing run. Its `academic` profile and Markdown/HTML output defaults do not override the source-first PDF workflow above.

The academic profile accepts inspected layout-aware Markdown, explicitly selected local PDF parsers, and bounded LaTeX projects. For DOCX/EPUB or unsupported layouts, first extract and inspect Markdown. A Markdown run starts with:

```bash
python3 scripts/convert.py paper.md --profile academic \
  --document-kind paper --subject ai --subject llm --subject agent \
  --olang zh --review-mode risk_based --review-sample-rate 0.1 \
  --format md --format html
```

Academic conversion writes `ingestion.json` with the raw source fingerprint, recorded normalization and image-resource mappings, plus `document_map.json` with exact source slices, Unicode character offsets, block kinds, section paths, and hashes. Inspect `input.md` and the map for reading order, equations, tables, and image targets, then record that inspection before translating:

```bash
python3 scripts/quality.py <temp_dir> --record-input-review \
  --findings-file <temp_dir>/drafts/input-review.json
```

The findings file must include all check names listed in `ingestion.json`; use an empty findings list only after checking each item. For Chinese, `scripts/glossary.py seed-academic` adds curated terms and sense notes without replacing hand-edited entries; its Chinese seed translations are not applied to other target languages. The translation skill masks protected content, restores it only after exact token and structure checks, and records chunk-specific glossary, neighbor, and first-occurrence context before planning resumable work.

Academic review modes are `risk_based` (recorded self-checks for every chunk plus coordinator review of high-risk chunks and a reproducible 10% sample, with at least one sampled ordinary chunk), `full` (coordinator review of every chunk), and `structural_only`. The last mode always remains `needs_review`. The final quality gate also requires a current source-inspection sidecar; stale provenance, changed resources, or open review warnings block validation. A default final build requires a `validated` quality report; a user-requested intermediate draft can be built with `--allow-draft` and is marked in `build_report.json`. No stage certifies parser completeness or mathematical/semantic correctness, so inspect the extracted Markdown and generated reports.

Academic output defaults to `output.md` and `book.html`. Add `--format docx`, `--format epub`, or `--format pdf` only when requested; each requested format is recorded separately in the build report. Use `--rebuild` after template or renderer changes to force regeneration of derived outputs while preserving translations, glossary entries, and review evidence. Intermediate chunks, masked tasks, review records, and quality reports are retained for audit.

Stage B adds source-grounded whole-document context. Before taking task snapshots, run `python3 scripts/document_context.py <temp_dir> --prepare`, inspect its priority blocks and definition/acronym candidates, then confirm only source-supported facts using `--prepare --facts-file <facts.json>`. The file contains all four lists: `topics`, `definitions`, `abbreviations`, and `symbols`. See the [context contract](references/document-context.md) for exact evidence spans and document/section scopes. Each task receives relevant parent definitions and local symbols; ambiguous meanings remain explicit, context overflow fails visibly, and corrections invalidate only snapshots that actually used the changed fact. Existing Stage A runs without this sidecar remain compatible.

The [B/C execution contract](references/advanced-academic.md) documents the implemented commands and schemas:

- Explicit `glossary.py migrate-v3` preserves legacy glossaries and adds senses, section scopes, occurrence bindings, translation policies and manual locks. Meta v2 proposals use transactional merges; ambiguous sources require explicit decisions.
- Add `--independent-review` to require a separate reviewer and execution context. `review_tasks.py` prepares packets, permits one durable repair attempt per source chunk, and limits final glossary reconciliation to two rounds. Required independent coverage remains incomplete when a separate reviewer is unavailable.
- `convert.py paper.pdf --profile academic --pdf-parser marker` (or `mineru`) uses an installed local parser. `poppler --simple-pdf` is explicit and limited to text-only PDFs. `pdf_ingest.py --pages 3:5` produces an isolated page subset, never silently replaces the full source. No parser models are installed or documents uploaded automatically.
- LaTeX input supports a selected main file, recursive project-confined includes, macro/BibTeX/image provenance and supported theorem/math content through Pandoc, without executing TeX. Arbitrary TeX programs, complex macro graphics and package-specific bibliography styling require inspected Markdown.
- Academic DOCX uses native OMML; EPUB uses MathML; PDF uses installed Word on Windows or an installed Chromium engine. Successful generation requires separate visual inspection recorded by `format_review.py`; missing/stale inspection leaves the build partial. Failed native rebuilds preserve the previous artifact. Generic format behavior stays compatible.
- Optional `academic_views.py` creates exact block-aligned bilingual Markdown/HTML and separately confirmed translator notes or image-text comparisons. `table_tasks.py` splits oversized standalone pipe tables into row groups and commits one validated canonical chunk. Complex HTML rowspan tables remain atomic. Additional mathematical review rules cover linear algebra, probability, optimization and logic.

Validation on 2026-10-22: 320 stdlib tests passed, plus real Pandoc HTML checks, real Poppler full/subset and recursive LaTeX checks, and a five-chunk model translation/independent-review/repair demonstration. Actual Pandoc 3.9 DOCX/EPUB and Word 16 PDF samples were generated and visually checked, including five math expressions, Chinese, a table and a footnote; EPUB spine navigation was checked in the in-app Chromium reader. This is a synthetic workflow demonstration, not an accuracy benchmark or certification of every reader. Marker/MinerU models were not installed, so those adapters have invocation-contract tests only. A final repeat of the native-format integration command requiring hidden Word was declined; previous real-rendering evidence and current unit tests are retained. Generated evidence stays under ignored `tests/.artifacts/`.

Academic HTML explicitly enables dollar and backslash-delimited math and records structured Pandoc diagnostics in `output.html.pandoc.json`. Unsupported math blocks completion even when Pandoc exits successfully; unrelated localization warnings are retained for inspection. The opt-in real-engine check is `python3 tests/integration/check_academic_html.py` with Pandoc on PATH. It checks MathML for fractions, roots, matrices, subscripts/superscripts, and a failed unsupported macro; it does not validate EPUB/PDF appearance or semantic accuracy.

Run `python3 tests/integration/check_document_context.py` for the context/planner CLI smoke test. It keeps generated artifacts under `tests/.artifacts/academic-stageB1/`, labels source-copy outputs as pipeline mocks, and verifies that a late definition correction replans 2 of 12 chunks. This check is independent of Pandoc and is not a translation-quality benchmark.

## Prerequisites

- **Agent runtime** — Codex, Claude Code, or OpenClaw, installed and ready to run skills
- **Source reader** — native file/page/vision access supplied by the agent host
- **Python 3.12+** — source-first helper uses only stdlib
- **pdftotext** — optional installed tool for model-selected long-PDF TXT extraction; reader-exported UTF-8 TXT is also accepted
- **Pandoc + installed Word or Chromium/Edge** — needed only for final PDF typesetting/rendering
- **Legacy pipeline only:** Calibre (`ebook-convert`, [download](https://calibre-ebook.com/)), [Pandoc](https://pandoc.org/) and Python packages:
  - `pypandoc` — required (`pip install pypandoc`)
  - `beautifulsoup4` — optional, for better TOC generation (`pip install beautifulsoup4`)
- **MinerU or Marker** — optional legacy Markdown adapters, not default preprocessing

## Quick Start

### 1. Install the skill

#### Codex

Install the GitHub repository in Codex's personal skill directory:

```bash
mkdir -p ~/.agents/skills
git clone https://github.com/ThxOfficial/translate-cs.git ~/.agents/skills/translate-cs
```

In Windows PowerShell, use:

```powershell
$target = Join-Path $HOME ".agents\skills\translate-cs"
New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null
git clone https://github.com/ThxOfficial/translate-cs.git $target
```

Codex discovers skills from `~/.agents/skills`. Run `/skills` to view available skills or invoke this one explicitly with `$translate-cs`. Codex detects skill changes automatically; restart only if it does not appear.

#### Claude Code

```bash
npx skills add ThxOfficial/translate-cs -a claude-code -g
```

Or install it manually:

```bash
mkdir -p ~/.claude/skills
git clone https://github.com/ThxOfficial/translate-cs.git ~/.claude/skills/translate-cs
```

#### OpenClaw

For OpenClaw, follow the local skill-directory instructions for the installed runtime.

### 2. Translate a book

#### Codex

In the Codex CLI or IDE extension, enter:

```text
$translate-cs Translate /path/to/paper.pdf into Chinese, preserving formulas, terminology, and citation structure.
```

Codex can also select the skill automatically when your request matches its description.

#### Claude Code and OpenClaw

Ask the agent:

```text
translate /path/to/book.pdf to Chinese
```

In Claude Code, you can also use the slash command:

```text
/translate-cs translate /path/to/book.pdf to Japanese
```

The skill reads the source, assesses whether TXT is needed, translates, edits against the original, and generates/inspects the PDF.

### 3. Find your outputs

All files are in `{book_name}_temp/`:

| File | Description |
|------|-------------|
| `source_workflow.json` | Original source identity and model routing assessment |
| `source.txt` | Optional long-document source text |
| `drafts/chunk*.md` | Optional translated batches |
| `output.md` | Edited target-language typesetting source |
| `editorial_review.json` | Source-to-translation coverage and version-bound editorial evidence |
| `book.pdf` | Final PDF when the report is complete |
| `build_report.json` | Actual generation and visual-inspection status |

HTML/native DOCX may be retained as rendering intermediates. DOCX/EPUB deliverables and the old manifest-driven pipeline remain opt-in legacy capabilities.

## Repository Test Assets

- Checked-in baseline inputs live under `tests/baselines/<book-id>/`.
- Generated full-pipeline outputs live under `tests/.artifacts/` and should not be committed.
- Because `scripts/convert.py` writes `{book_name}_temp/` under the current working directory, run repository baseline tests from inside `tests/.artifacts/` to keep generated files out of the repo root.

### Legacy Full-Pipeline Baseline Example

```bash
mkdir -p tests/.artifacts
cd tests/.artifacts
python3 ../../scripts/convert.py ../baselines/standard-alice/standard-alice.epub --olang zh
# then run translation via the skill
python3 ../../scripts/merge_and_build.py --temp-dir standard-alice_temp --title "test"
```

## Push to GitHub

The configured `origin` is `https://github.com/ThxOfficial/translate-cs.git` and the working branch is `main`. Review the files before staging and committing:

```bash
git status --short --branch
git add -A
git diff --cached --check
git diff --cached --stat
git commit -m "feat: specialize translate-cs for academic translation"
git push origin main
```

`git add -A` stages every tracked modification and untracked project file, so inspect the staged summary before committing. Pushing `main` updates the repository; a tagged GitHub release is a separate action.

## Feedback and Contributions

Please open a detailed GitHub issue instead of starting with a pull request. This project is maintained as an AI-assisted skill pipeline, and changes need to be evaluated against the current orchestration rules, chunk/manifest contracts, baseline assets, and release flow in one maintainer-owned context.

Pull requests are not the preferred contribution path and may be closed in favor of an issue. If you already have a patch, include the idea, key diff, failing case, or verification notes in the issue; the maintainer may rework or split the implementation before merging.

A useful issue should include:

- Current behavior and expected behavior
- Input format and environment, such as PDF/DOCX/EPUB, OS, Python, Calibre, and Pandoc versions
- Minimal reproduction steps or a small public-domain sample when possible
- Logs, screenshots, or generated file names that show the failure

## Contact

For bugs and feature requests, open an issue in [ThxOfficial/translate-cs](https://github.com/ThxOfficial/translate-cs/issues).

## Legacy Pipeline Details (opt-in)

These commands are retained for existing runs and explicit legacy requests. New translations follow the [source-first contract](references/source-first.md); the PDF-to-Markdown conversion below is not their first step.

### Step 1: Convert

```bash
python3 scripts/convert.py /path/to/book.pdf --olang zh
```

Calibre converts the input to HTMLZ, which is extracted and converted to Markdown, then split into chunks (~6000 chars each). A `manifest.json` records the SHA-256 hash of each source chunk for later validation, and a `source_fingerprint.json` ties the temp dir to the exact source bytes it was built from — re-running against a replaced source file aborts instead of silently reusing stale chunks. Temp dirs created before fingerprinting are adopted with a warning on first re-run.

By default the working directory is `{book_name}_temp/` under the current directory. Use `--temp-root /path/to/work` to keep the same leaf directory name under a different parent.

#### Formula- and table-heavy PDFs: convert Markdown instead (optional)

Calibre reflows PDF text by coordinate heuristics. That works for plain prose, but academic and technical PDFs lose their structure: formulas break into fragments, tables flatten into one cell per line, and multi-column pages interleave. For those PDFs, extract them with a layout-aware parser first and give `convert.py` the Markdown it writes. `.md` / `.markdown` input skips Calibre entirely:

```bash
pip install -U "mineru>=4.0,<5"                # or: pip install marker-pdf
mineru-kit parse paper.pdf -o paper.md         # MinerU >= 4 (CPU-only basic tier: ~0.8 GB of models)
# marker_single paper.pdf --output_dir out/    # Marker: writes out/paper/paper.md
python3 scripts/convert.py paper.md --olang zh
```

The parsers keep formulas as `$...$` / `$$...$$` LaTeX and tables as tables. `convert.py` copies images referenced next to the Markdown file, and decodes the base64 images MinerU inlines, into `{book_name}_temp/images/`, so chunks never carry image payloads. Display-math blocks (`$$ ... $$`) are never split across chunks. YAML front matter (`title`, `author`, `lang`) fills `config.txt` and is not sent for translation; without it, a leading `#` heading becomes `original_title`. The temp dir is named after the Markdown file, so `paper.md` maps to `paper_temp/`. If that dir was built from `paper.pdf`, `convert.py` aborts on the source-fingerprint mismatch, so delete it first. Both parsers download ML models on first run. `--strip-page-numbers` only applies to Calibre input.

At build time Pandoc renders `$...$` / `$$...$$` as MathML, so formulas are typeset in `book.html` and `book.pdf`, and in `book.epub` on readers that support MathML. Calibre's DOCX output cannot typeset MathML: formulas appear there as flattened text followed by their TeX source.

### Step 1.5: Glossary (term consistency across chunks)

Each chunk is translated by a fresh-context sub-agent, which means the same proper noun can drift across multiple translations on a 100-chunk book. To fix this, the skill builds a glossary before translation:

1. Sample 5 chunks (first, last, 3 evenly-spaced middle).
2. Extract proper nouns and recurring domain terms; pick canonical translations.
3. Write `<temp_dir>/glossary.json` (hand-editable schema below).
4. Run `python3 scripts/glossary.py count-frequencies <temp_dir>` to populate per-term frequencies (ASCII terms use word-boundary regex so `cat` doesn't match `category`; CJK terms use substring; single-CJK-char terms are rejected; aliases count toward the term they belong to).
5. For each chunk, the orchestrator calls `python3 scripts/glossary.py print-terms-for-chunk <temp_dir> chunkNNNN.md` and injects the resulting 3-column (`原文 | 别名 | 译文`) markdown table into that chunk's prompt as a hard constraint. Term selection = (terms whose source OR any alias appears in this chunk) ∪ (top-N most-frequent book-wide).

```json
{
  "version": 2,
  "terms": [
    {"id": "Manhattan", "source": "Manhattan", "target": "曼哈顿",
     "category": "place", "aliases": [], "gender": "unknown",
     "confidence": "medium", "frequency": 12,
     "evidence_refs": [], "notes": ""}
  ],
  "high_frequency_top_n": 20,
  "applied_meta_hashes": {}
}
```

Existing v1 `glossary.json` files are auto-upgraded to v2 on first load. v2 forbids the same surface form (source or alias) appearing in two different terms; if a v1 file has polysemous duplicate sources, the upgrade aborts with a disambiguation message — fix the file by hand and reload.

Edit `glossary.json` between runs to fix translations; existing `glossary.json` is never overwritten — delete it to rebuild from scratch. `scripts/run_state.py` records which glossary terms each chunk used, so later glossary changes (including `target`, `category`, and `aliases` edits) only re-translate affected chunks after the state has been recorded.

### Step 2: Translate (parallel subagents)

The skill launches subagents in batches (default: 8 concurrent). Each subagent:

1. Reads one source chunk (e.g. `chunk0042.md`)
2. Translates to the target language
3. Uses a per-chunk term table and short read-only previous/next excerpts
4. Writes the result to `output_chunk0042.md`
5. Writes `output_chunk0042.meta.json` observations for glossary feedback

Before launching subagents, `scripts/run_state.py plan <temp_dir>` decides which chunks need translation, which existing outputs only need state recording, and which are unchanged. Use `--retranslate-untracked` only when adopting an old temp dir whose existing outputs should be forced through the current glossary. If a run is interrupted, re-running skips chunks that already have valid output files and current state. Failed chunks are retried once automatically.

### Step 3: Merge & Build

```bash
python3 scripts/merge_and_build.py --temp-dir book_temp --title "《translated title》"
```

Optional output flags:

```bash
python3 scripts/merge_and_build.py --temp-dir book_temp --title "《translated title》" --cover cover.jpg --export-name "translated-title"
```

`--cover` passes an explicit image to the EPUB Calibre step. `--export-name` creates alias copies such as `translated-title.epub` while preserving the canonical `book.*` pipeline artifacts.

Before merging, the script validates:
- Every source chunk has a corresponding output file (1:1 match)
- Source chunk hashes match the manifest (no stale outputs)
- No output files are empty, blank (whitespace-only), or unreadable — a blank chunk aborts the merge instead of silently dropping its content

Then: merge → Pandoc HTML → inject TOC → Calibre generates DOCX, EPUB, PDF.

**Note:** `{book_name}_temp/` is a working directory for a single translation run. If you change the title, author, output language, template, or image assets, either use a fresh temp directory or delete the existing final artifacts (`output.md`, `book*.html`, `book.docx`, `book.epub`, `book.pdf`) before re-running.

## Project Structure

| File | Purpose |
|------|---------|
| `SKILL.md` | Agent skill definition — orchestrates the full pipeline |
| `scripts/source_workflow.py` | Source registration, optional TXT, editorial evidence and PDF after review |
| `references/source-first.md` | Current default reading/translation/editing/PDF contract |
| `references/legacy-pipeline.md` | Compatible opt-in Markdown pipeline |
| `scripts/convert.py` | PDF/DOCX/EPUB → Markdown chunks via Calibre HTMLZ; Markdown input skips Calibre |
| `scripts/manifest.py` | Chunk manifest: SHA-256 tracking and merge validation |
| `scripts/glossary.py` | Glossary management: per-chunk term tables for consistent terminology |
| `scripts/chunk_context.py` | Read-only previous/next chunk excerpts for sub-agent prompts |
| `scripts/meta.py` | Per-chunk sub-agent observation file schema (`output_chunkNNNN.meta.json`) |
| `scripts/merge_meta.py` | Batch-boundary merge: sub-agent observations → canonical glossary |
| `scripts/run_state.py` | Selective re-translation planner and `run_state.json` recorder |
| `scripts/merge_and_build.py` | Merge chunks → HTML → DOCX/EPUB/PDF |
| `scripts/calibre_html_publish.py` | Calibre wrapper for format conversion |
| `scripts/template.html` | Web HTML template with floating TOC |
| `scripts/template_ebook.html` | Ebook HTML template |
| `tests/baselines/` | Checked-in baseline book inputs for full-pipeline testing |
| `tests/.artifacts/` | Ignored full-pipeline test outputs |

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `Calibre ebook-convert not found` | Install Calibre and ensure `ebook-convert` is in PATH |
| `Manifest validation failed` | Source chunks changed since splitting — re-run `convert.py` |
| `was created from different source bytes` | The temp dir belongs to a different source file — delete the temp dir or use a fresh `--temp-root` |
| TXT formulas/tables are incomplete | Re-read the original pages, correct the TXT and preserve figures; do not guess missing technical content. Markdown adapters remain a legacy option. |
| `Blank output` / `Empty output` | A subagent wrote a whitespace-only or empty chunk — re-run the skill to re-translate it |
| `Missing source chunk` | Source file deleted — re-run `convert.py` to regenerate |
| Incomplete translation | Re-run the skill — it resumes from where it stopped |
| Changed title/template/assets but output didn't update | For source-first, refresh editorial review if tracked content changed, then run `source_workflow.py build-pdf ... --rebuild`; preserve edited `output.md`. Legacy cleanup is described above. |
| Page numbers in TXT | Keep page locations/form-feed boundaries and check the original before removing footers. The old `convert.py --strip-page-numbers` option applies only to legacy Calibre input. |
| `output.md exists but manifest invalid` | Stale output — the script auto-deletes and re-merges |
| `Glossary upgrade rejected: duplicate source` | The legacy v2 glossary requires unique surfaces. For academic work, migrate explicitly to v3 with `python3 scripts/glossary.py migrate-v3 <temp_dir>`; v3 supports polysemy through reviewed occurrence bindings. |
| Default PDF generation fails | Inspect `build_report.json`; verify Pandoc and installed Word/Chromium/Edge |
| PDF is blocked before rendering | Complete current source-to-translation editorial evidence; changes require a new review |

## Legacy Generic Pipeline Capabilities

These capabilities remain available in the opt-in legacy pipeline. See [academic delivery status](docs/CS_AI_TRANSLATION_SPEC.md#135-当前尚未完成的验收和发布工作) for its remaining validation items.

The generic pipeline supports scoped terminology, source-grounded context, and selective re-translation. Validation on a complete, naturally translated technical book remains unfinished.

### Design principles

- **Scripts do bookkeeping; LLMs do semantic merge.** State, schemas, dedup, hashing, IO are deterministic Python. Naming, gender attribution, alias judgment, conflict resolution are LLM calls.
- **Single writer for shared state.** Only the main agent writes `glossary.json` and `run_state.json`; sub-agents write per-chunk meta files. No locking needed.
- **Conservative merge.** New entities require evidence; alias merges need LLM judgment, not just string similarity; gender starts at `unknown` and only moves up under explicit evidence; canonical values aren't silently overwritten on conflict.
- **Three-layer state, three separate files.** `glossary.json` (canonical, sub-agents read), `output_chunkNNNN.meta.json` (raw per-chunk observations), `run_state.json` (orchestration).

### Phase 1 — Sub-agent feedback + glossary merge (shipped)

Closes the read+write loop. Glossary v2 adds `id`, `aliases`, `gender`, `confidence`, `evidence_refs`, `notes` (v1 files auto-upgrade on first load; the term table is now 3-col and aliases participate in selection). Sub-agents emit `output_chunkNNNN.meta.json` alongside each translated chunk. `scripts/merge_meta.py` (`prepare-merge` / `apply-merge` / `status`) merges per-batch with conservative rules: surface-form uniqueness enforced, malformed metas quarantined (warn + skip + count), confidence escalation via both `evidence_chunks` and `used_term_sources`, FIFO-cap at 5. See [legacy steps 4–5](references/legacy-pipeline.md).

### Phase 2 — Neighbor context for pronouns (shipped)

`scripts/chunk_context.py` injects `prev_excerpt` (last ~300 chars of previous chunk) and `next_excerpt` (first ~300 chars of next chunk) into each sub-agent prompt as read-only context. No new state files are introduced.

### Phase 3 — Selective re-translation (shipped)

Phase 1's batch feedback only improves *forward*. Selective rerun closes the *backward* loop with `scripts/run_state.py` and `run_state.json`: per-chunk tracking of `glossary_version_used`, `entity_ids_used`, `output_hash`, source hash, and selected entity hashes; five planning rules cover missing/empty output, manifest source drift, untracked outputs, source drift since record, and glossary term selection/hash changes.

### Phase 4 — Bootstrap warm-up (experimental, gated on Phase 1 data)

Phase 1 grows the glossary batch-by-batch, so the first batch sees the smallest glossary and has the highest drift risk. Possible approaches: sequential bootstrap, variable concurrency, or skip entirely. Decision belongs to whoever has run the system on real books.

> Phase 4 remains gated on real-book evidence. The shipped schemas can still evolve under compatibility-aware migrations if production runs expose gaps.

### Pipeline / UX capabilities (partly shipped)

Recent PR discussions also surfaced several useful workflow improvements, but these are broader than one-off patches and touch repo contracts (artifact names, temp-dir behavior, cleanup semantics, or EPUB compatibility scope). Current status:

- **Explicit EPUB cover support (shipped).** `merge_and_build.py --cover <image>` passes the image through the HTML -> EPUB Calibre step. `--cover-from <epub>` / EPUB cover auto-extraction remains out of scope until the project is ready to own EPUB parsing compatibility across different package layouts.
- **Configurable temp workspace location (shipped).** `convert.py --temp-root <dir>` keeps the default cwd-local `{book_name}_temp/` behavior unless explicitly overridden.
- **Safer Calibre/Pandoc artifact cleanup (partly shipped).** Page-number and Calibre-marker cleanup is regression-tested, preserving years, chapter numbers, and non-monotonic standalone numbers. Continue improving cleanup incrementally under tests.
- **Optional user-facing export names (shipped).** `merge_and_build.py --export-name <stem>` creates alias copies while preserving canonical pipeline artifacts as `book.html`, `book_doc.html`, `book.docx`, `book.epub`, and `book.pdf`.

## Star History

If you find this project helpful, please consider giving it a Star ⭐!

[![Star History Chart](https://api.star-history.com/svg?repos=ThxOfficial/translate-cs&type=Date)](https://star-history.com/#ThxOfficial/translate-cs&Date)

## Model execution

`translate-cs` does not call a model API. Translation and review run through the user's own agent host, such as Codex, Claude Code, or OpenClaw.

## License

[MIT](LICENSE)
