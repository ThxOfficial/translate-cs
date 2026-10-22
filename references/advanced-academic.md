# Academic stages B and C

Read the relevant sections for sense-aware terminology, independent review, source adapters, native formats, or optional reading views. Keep the academic source-inspection and quality gates. Generated tests and views live in `tests/.artifacts/` during development; normal runs use their chosen temp directory. Generic translation keeps its existing interfaces and format path.

## Glossary v3 and meta v2

Seed a legacy glossary first if needed, then explicitly migrate:

```bash
python3 {baseDir}/scripts/glossary.py migrate-v3 "<temp_dir>"
```

Loading v1/v2 remains compatible; v3 is never enabled by silently relabeling an existing file. Migration places terms in document scope, preserves IDs/targets/aliases and carries prompt notes to `semantic_notes`; it leaves `sense` empty. Add actual senses and scopes only after reading evidence.

V3 terms retain the old identity/translation fields and add `sense`, `scope` (`{"kind":"document"}` or `{"kind":"section","section_id":"block000003"}`), `translation_policy` (`translate`, `keep_source`, `first_occurrence_bilingual`), boolean `locked`, provenance with a `kind`, and `semantic_notes`. IDs are independent of source strings. Repeated sources and aliases are allowed; simultaneous applicable matches are **ambiguous**, never resolved by list order. Do not invent source strings such as `agent#llm`.

The task snapshot's `terminology.occurrences` supplies actual block IDs and Unicode spans in `input.md`. Resolve ambiguity in `term_bindings.json` schema 1: `source_sha256` plus `bindings` records with `block_id`, `source_span: [start,end]`, and `term_id`. A bound span must be a visible prose surface of that term within its scope; code, formulas and references cannot acquire prose bindings. Unresolved ambiguity blocks a final build. Follow per-term keep/translate/display policies and program-selected bilingual first occurrences; do not independently repeat English in each chunk.

For v3, emit observation meta v2 with exactly `schema_version: 2`, `used_term_ids: [...]`, and `term_proposals: [...]`. Each proposal has `term_id` or an actual `source`, exact source `evidence` (at most 200 characters), and optional `target`, `sense`, `scope`, `alias`, `action`. Chunk identity still comes from the filename. Legacy meta v1 stays readable; ambiguous source proposals go to explicit decisions. New meta v2 is rejected by the legacy v2 merger rather than silently losing fields.

Use the existing `merge_meta.py prepare-merge` / `apply-merge` commands. V3 preparation returns a schema-3 transaction containing the base glossary hash, consumed meta hashes and pending proposal IDs. Apply receives that transaction with one decision per proposal: `skip`, `update` with an explicit `term_id` and a `patch`, or `new_term` with a complete unlocked v3 term. A new source surface must occur in evidence. Updates cannot alter locked terms or silently change IDs/scopes. A bad decision, changed source meta or stale glossary aborts before atomic write; successful replay is idempotent. Frequency/audit metadata do not affect translation fingerprints.

## Independent semantic review and bounded repair

For new B/C runs use `convert.py ... --profile academic --independent-review`; existing A runs preserve their old review mode unless explicitly upgraded. `risk_based` requires translator self-checks for all chunks plus independent review for high-risk and deterministically sampled chunks; `full` requires both for all chunks. Never claim a same-context pass is independent.

Record actual execution identity with the translation:

```bash
python3 {baseDir}/scripts/run_state.py record "<temp_dir>" chunk0001 \
  --translator-id "<actual_actor_id>" --translation-context-id "<actual_context_id>"
python3 {baseDir}/scripts/review_tasks.py "<temp_dir>" --reconcile
python3 {baseDir}/scripts/review_tasks.py "<temp_dir>" --prepare
```

Give a separate reviewer/context the generated `reviews/tasks/chunkNNNN.json` packet and its source/output/task files. Do not give it the intended answer or translator self-check. The reviewer compares the complete chunk, citing exact source evidence for negation, quantifiers, assumptions, metrics, semantic omissions and terminology findings. Its findings JSON contains `findings` plus `execution`: `reviewer_id`, `review_context_id`, `translator_id`, `translation_context_id`. The first two must differ from the recorded translator/context IDs. These are execution declarations, not a mathematical proof of independence; use actual runtime IDs, never fabricated ones.

```bash
python3 {baseDir}/scripts/quality.py "<temp_dir>" --record-review chunk0001 \
  --review-kind independent --findings-file "<reviewer_findings.json>"
```

Self-checks retain their separate records. For a repair, let the translator write a new masked draft. Use `review_tasks.py <temp_dir> --repair chunkNNNN --draft <path> --expected-output-hash <reviewed_hash> --translator-id <actor> --translation-context-id <context>`. A durable reservation permits at most one repair attempt per source chunk, including interrupted/failed attempts. Failed restores retain the old output; an intervening human edit cannot be overwritten. After a successful repair every old source/output/context-bound review becomes stale and must be renewed.

After **all** glossary merges, including the last batch, run `--reconcile` again, translate the listed dependency changes, record them and review again. This is limited to two different pending rounds. Unresolved work after the limit is reported, not called validated. When separate reviewer execution is unavailable, keep required independent coverage incomplete instead of downgrading it to a self-check.

## PDF and LaTeX sources

`convert.py input.pdf --profile academic --pdf-parser marker` or `--pdf-parser mineru` uses an explicitly selected **already installed** local parser. The adapter records help/version probes, argv, logs, exit code, total page count, selected scope and source/output hashes. MinerU kit is preferred when present; legacy MinerU is only used if its probed flags match. Models are not installed; model-hub offline environment flags are set. Cloud LLM/OCR options are not enabled. Missing local models or incompatible CLI capabilities fail explicitly. See the current [MinerU](https://github.com/opendatalab/MinerU) and [Marker](https://github.com/datalab-to/marker) contracts; check installed help rather than assuming all versions match.

For an explicitly simple text-only PDF, `--pdf-parser poppler --simple-pdf` uses installed `pdftotext -layout` plus `pdfinfo`; it does not recover scientific layout, images or OCR. It is never an automatic fallback for complex PDFs. `pdf_ingest.py input.pdf --parser <parser> --output-dir <fresh_dir> --pages 3:5` reparses an isolated, inclusive one-based page subset and marks it `page_subset`. It never patches canonical chunks or substitutes a partial result for a whole book. Inspect and explicitly reimport corrected Markdown in a fresh run.

`convert.py <main.tex-or-project-directory> --profile academic [--latex-main main.tex]` expands braced `input`/`include` recursively inside the project root. Ambiguous mains, cycles, missing/escaping dependencies and executable TeX directives fail. It records source/BibTeX/asset hashes, macro declarations and the main file, then calls Pandoc as a parser, never a TeX engine or shell escape. Literal code remains data; supported mathematical source slices are protected before parsing and restored exactly. Theorem/proof types are made explicit without inventing TeX counter values. Citations retain keys; original BibTeX and macro provenance remain available for inspection. Package-specific bibliography styling, arbitrary TeX programs, complex macro graphics and automatic theorem counters are not reproduced by this bounded importer. Supply inspected Markdown for those cases. Parser success is not source-completeness certification.

## Native formats and actual display checks

Academic DOCX goes directly from Markdown through Pandoc to OMML, and EPUB through Pandoc with MathML and bundled local CSS. A derived DOCX rendering copy wraps bare mathematical environments; canonical Markdown/TeX slices stay unchanged. Unsupported math and missing native math nodes fail. PDF uses an installed Windows Word OMML export when available; otherwise it uses an installed Chromium browser. No browser/TeX distribution is downloaded. A browser success exit without a valid output is a failure. Generic Calibre formats retain their existing path.

Each requested format records engine, validation, cache and failures separately. Existing outputs with changed source/config/CSS inputs require explicit `--rebuild`; new code adds no mtime cache behavior. File generation and ZIP checks are distinct from display validation. Without a current `format_reviews.json` record, native format results stay `needs_visual_review`, so a final build remains partial.

Inspect the actual DOCX in Word/LibreOffice, all PDF pages as rendered images, and EPUB spine content in the target reader. Record evidence JSON with `artifact_sha256`, actual `reviewer`, `engine`, `inspection_scope`, complete `checks`, and `findings: []` only after inspection. DOCX/PDF checks are `math`, `cjk`, `tables`, `footnotes`, `pagination`; EPUB replaces pagination with `reader_navigation`. Save using `format_review.py <temp_dir> --format <format> --evidence-file <path>`. The review binds to exact artifact bytes; regeneration invalidates it. State the tested reader/engine and do not generalize one renderer to every EPUB reader.

## Optional aligned views, notes, figures and long tables

Only when requested, run `academic_views.py <temp_dir>` for `alignment.json`, `bilingual.md`, and `bilingual.html`. It requires one exact corresponding structural block per source block, uses stable source block IDs and source/output hashes, and fails if that alignment cannot be established. No fuzzy alignment or prose rewriting is applied. The existing quality gate still applies; explicitly requested intermediate drafts can use `--allow-draft`.

`--annotations <file>` accepts schema 1 with current `source_sha256`, `output_hashes`, and optional `translator_notes` / `figure_text` lists. Each item needs a source `block_id`, exact `source_evidence`, confirmed text and `confirmed: true`. Figure text additionally binds `asset` under `images/`, `asset_sha256`, actual `original_text`, and `method` (`manual` or `local_ocr`). Inspect the actual image when transcribing. Notes and original/translated figure text appear separately from canonical translated prose. No OCR service or image upload is implicit.

For an oversized standalone pipe-table chunk, run `table_tasks.py prepare <temp_dir> chunkNNNN --rows-per-group 20`. It retains one canonical source chunk and creates a header task plus complete data-row groups under `tasks/chunkNNNN.table/`. Translators write `output_header.md` and `output_rows*.md`; shared header/separator context is read-only, never repeated in row-group outputs. Run `table_tasks.py commit ...` to assemble once and restore through the same content guard. Missing/extra rows, changed source or invalid tokens prevent commit. Review the complete assembled table. HTML rowspan tables and other unsupported complex tables remain atomic and require a separately inspected representation; do not guess cell dependencies.

For mathematical sources, read [math-domains.md](math-domains.md) for linear algebra, probability/statistics, optimization and logic review lenses. They add no assumptions or “corrections” to the source.
