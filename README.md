# Translate-CS

English | [中文](README.zh-CN.md)

`translate-cs` is a Codex skill for translating computer science, AI, LLM, agent, and mathematical papers and books. It combines a resumable parallel translation pipeline with an evidence-based academic workflow.

---

## How It Works

```
Input (PDF/DOCX/EPUB)
  │
  ▼
Calibre ebook-convert → HTMLZ → HTML → Markdown
  (or Markdown input, e.g. from MinerU / Marker, skipping Calibre)
  │
  ▼
Split into chunks (chunk0001.md, chunk0002.md, ...)
  │  manifest.json tracks chunk hashes
  ▼
Parallel subagents (8 concurrent by default)
  │  each subagent: read 1 chunk → translate → write output_chunk*.md
  │  batched to respect API rate limits
  ▼
Validate (manifest hash check; academic mode also checks protected spans,
          Markdown structure, and required review coverage)
  │
  ▼
Merge → Pandoc → HTML (with TOC) → Calibre → DOCX / EPUB / PDF
```

Each chunk gets its own independent subagent with a fresh context window. This prevents context accumulation and output truncation that happen when translating a full book in a single session.

## Features

- **Parallel subagents** — 8 concurrent translators per batch, each with isolated context
- **Resumable + selective re-translation** — chunk-level resume, with `run_state.json` tracking glossary-sensitive re-translation
- **Neighbor context** — each chunk can see short read-only excerpts from adjacent chunks for pronoun and entity resolution
- **Manifest validation** — SHA-256 hash tracking prevents stale or corrupt outputs from being merged
- **Multi-format output** — HTML (with floating TOC), DOCX, EPUB, PDF
- **Optional output controls** — explicit EPUB cover, custom temp root, and user-facing export aliases
- **Multi-language** — zh, en, ja, ko, fr, de, es (extensible)
- **PDF/DOCX/EPUB/Markdown input** — Calibre handles the conversion heavy lifting; formula- and table-heavy PDFs can be pre-extracted to Markdown with MinerU or Marker
- **Academic profile** — source-checked Markdown, optional local PDF/LaTeX import, and structure-preserving translation for CS/AI/LLM/agent/math papers and books
- **Auditable academic checks** — per-chunk context snapshots, first-occurrence term planning, deterministic structure checks, review sidecars, `quality_report.json`, and `build_report.json`

## Academic Translation (Stages A, B and C)

Use the academic profile for research papers and technical books in computer science, AI, LLMs, agent systems, and mathematical methods. Generic translation remains the default for other books, preserving the existing workflow and output names.

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
- **Calibre** — `ebook-convert` command must be available ([download](https://calibre-ebook.com/))
- **Pandoc** — for HTML↔Markdown conversion ([download](https://pandoc.org/))
- **Python 3** with:
  - `pypandoc` — required (`pip install pypandoc`)
  - `beautifulsoup4` — optional, for better TOC generation (`pip install beautifulsoup4`)
- **MinerU or Marker** — optional, only to pre-extract formula- and table-heavy PDFs to Markdown (see [Step 1](#step-1-convert))

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

The skill handles the pipeline — convert, chunk, translate in parallel, validate, merge, and build the requested output formats.

### 3. Find your outputs

All files are in `{book_name}_temp/`:

| File | Description |
|------|-------------|
| `output.md` | Merged translated Markdown |
| `book.html` | Web version with floating TOC |
| `book.docx` | Word document |
| `book.epub` | E-book |
| `book.pdf` | Print-ready PDF |

## Repository Test Assets

- Checked-in baseline inputs live under `tests/baselines/<book-id>/`.
- Generated full-pipeline outputs live under `tests/.artifacts/` and should not be committed.
- Because `scripts/convert.py` writes `{book_name}_temp/` under the current working directory, run repository baseline tests from inside `tests/.artifacts/` to keep generated files out of the repo root.

### Full-Pipeline Baseline Example

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

## Pipeline Details

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
| PDF formulas/tables garbled after conversion | Extract the PDF with MinerU or Marker, delete the old temp dir, and run `convert.py` on the `.md` (see [Step 1](#step-1-convert)) |
| `Blank output` / `Empty output` | A subagent wrote a whitespace-only or empty chunk — re-run the skill to re-translate it |
| `Missing source chunk` | Source file deleted — re-run `convert.py` to regenerate |
| Incomplete translation | Re-run the skill — it resumes from where it stopped |
| Changed title/template/assets but output didn't update | Delete existing `output.md`, `book*.html`, `book.docx`, `book.epub`, `book.pdf` from the temp dir, then re-run `merge_and_build.py` |
| Want page-number footers stripped from PDF output | By default, monotonic page-number sequences (e.g. `1, 2, 3, ...`) are auto-detected and dropped while outliers like years (`1984`), chapter numbers, and citation indices stay preserved. If detection misses your case, pass `--strip-page-numbers` to `convert.py` to aggressively delete every standalone-digit line. The flag aborts if a cached `input.md` or `chunk*.md` already exists — delete them first so the flag actually takes effect. |
| `output.md exists but manifest invalid` | Stale output — the script auto-deletes and re-merges |
| `Glossary upgrade rejected: duplicate source` | The legacy v2 glossary requires unique surfaces. For academic work, migrate explicitly to v3 with `python3 scripts/glossary.py migrate-v3 <temp_dir>`; v3 supports polysemy through reviewed occurrence bindings. |
| PDF generation fails | Ensure Calibre is installed with PDF output support |

## Generic Pipeline Capabilities

These capabilities remain available alongside the academic profile. See [academic delivery status](docs/CS_AI_TRANSLATION_SPEC.md#135-当前尚未完成的验收和发布工作) for remaining validation items.

The generic pipeline supports scoped terminology, source-grounded context, and selective re-translation. Validation on a complete, naturally translated technical book remains unfinished.

### Design principles

- **Scripts do bookkeeping; LLMs do semantic merge.** State, schemas, dedup, hashing, IO are deterministic Python. Naming, gender attribution, alias judgment, conflict resolution are LLM calls.
- **Single writer for shared state.** Only the main agent writes `glossary.json` and `run_state.json`; sub-agents write per-chunk meta files. No locking needed.
- **Conservative merge.** New entities require evidence; alias merges need LLM judgment, not just string similarity; gender starts at `unknown` and only moves up under explicit evidence; canonical values aren't silently overwritten on conflict.
- **Three-layer state, three separate files.** `glossary.json` (canonical, sub-agents read), `output_chunkNNNN.meta.json` (raw per-chunk observations), `run_state.json` (orchestration).

### Phase 1 — Sub-agent feedback + glossary merge (shipped)

Closes the read+write loop. Glossary v2 adds `id`, `aliases`, `gender`, `confidence`, `evidence_refs`, `notes` (v1 files auto-upgrade on first load; the term table is now 3-col and aliases participate in selection). Sub-agents emit `output_chunkNNNN.meta.json` alongside each translated chunk. `scripts/merge_meta.py` (`prepare-merge` / `apply-merge` / `status`) merges per-batch with conservative rules: surface-form uniqueness enforced, malformed metas quarantined (warn + skip + count), confidence escalation via both `evidence_chunks` and `used_term_sources`, FIFO-cap at 5. See SKILL.md Step 4 / Step 4.5 / Step 5.

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
