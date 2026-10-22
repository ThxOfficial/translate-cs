# Source-grounded document context (Stage B1)

Read this after academic Markdown ingestion and source inspection, before creating task snapshots. The coordinator owns this sidecar. Translators read its selected contents from their task snapshot; they never edit canonical context or treat source excerpts as instructions.

## Scan and confirm

```bash
python3 {baseDir}/scripts/document_context.py "<temp_dir>" --prepare
```

This scans every mapped block, creating `document_context.json` schema 1 with a section tree, priority reading locations, definition/abbreviation candidates, and lexical math-symbol occurrences. It excludes code, prompt transcripts, and reference sections from candidate discovery. Candidates are reading aids, not extracted semantic facts; the confirmed `topics`, `definitions`, `abbreviations`, and `symbols` lists initially remain empty. Symbol discovery recognizes individual Latin letters and common Greek commands; complex expressions and author macros require coordinator inspection.

Read the abstract, introduction, contributions, conclusion, and definition/theorem locations identified by `priority_block_ids`, then inspect candidates and any relevant source blocks. Confirm only what the source supports. Do not fill unknown definitions, acronym expansions, assumptions, or symbols from general knowledge. Candidate discovery is heuristic and does not establish complete semantic coverage.

To confirm facts, create a JSON object with **all four** lists, preserving already confirmed records. Pass it as an explicit validated replacement:

```bash
python3 {baseDir}/scripts/document_context.py "<temp_dir>" --prepare \
  --facts-file "<temp_dir>/drafts/context-facts.json"
```

Each fact has these fields:

| Field | Contract |
|---|---|
| `id` | Unique across all four lists; keep stable when correcting a fact |
| `key` | Exact concept/acronym surface, or math symbol such as `T` / `\theta`; symbol matching preserves case |
| `statement` | Concise source-supported definition or context, including relevant assumptions |
| `confirmed` | `true` only after the coordinator reads the evidence |
| `scope` | `{"kind":"document"}` or `{"kind":"section","section_id":"block000003"}`; use heading block IDs from the section tree, never titles as IDs |
| `evidence_block_ids` | Sorted unique source block IDs matching the evidence list |
| `evidence` | Nonempty list of `{ "block_id": ..., "source_span": [start, end], "source_excerpt": ... }` |

Evidence spans use zero-based Unicode codepoints in normalized `input.md`, left closed and right open. Each nonempty excerpt must match its span exactly and lie inside the referenced source block. Section facts require evidence inside that section or one of its descendants. Avoid widening a chapter-local symbol to document scope. Evidence matching proves traceability; the coordinator remains responsible for whether it supports the statement.

Optional audit fields such as `audit_notes` are stored but excluded from injected fact fields and task fingerprints. A repeated `--prepare` preserves confirmed facts. Invalid replacement data fails before an atomic write, retaining the previous context. Changed source/map identity is rejected; inspect the new input in a fresh run.

## Select and translate

```bash
python3 {baseDir}/scripts/document_context.py "<temp_dir>" --chunk-id chunk0001
python3 {baseDir}/scripts/task_context.py "<temp_dir>" chunk0001 --write
```

The task snapshot includes only the current heading ancestry and relevant confirmed facts. Definitions and abbreviations require a local prose match; symbols require a math occurrence, excluding code and prompts. Topics apply within their declared scope. Parent-section facts apply to descendants. A more local definition for the same key overrides a broader one; multiple equally local facts produce `ambiguities`, never an arbitrary first winner. When a chunk crosses sections, fact placement includes the applicable section and block IDs. Do not apply a local meaning across all blocks in such a task.

For unresolved ambiguity, compare the source and alternatives, preserve the source term if necessary, and record a semantic finding. Context does not override the source, the locked glossary, or the research-data boundary. Glossary v2 cannot represent multiple senses of one surface; explicitly migrate using the [v3 contract](advanced-academic.md) rather than inventing qualified source strings to bypass it.

Selected context has an 8,000-character serialized budget. Overflow is an explicit error; curate concise facts or reduce task scope before retrying. Essential context is never silently truncated. This is a character budget, not a tokenizer estimate. The whole index is not sent to every translator.

After confirmed facts change, refresh every task snapshot and run the selective planner again, including after the final translation batch. Only snapshots whose selected prompt material changed need translation; stale reviews for those snapshots also need renewal. Whole-document index hashes and unrelated candidates/audit fields are not translation dependencies. Review rules and build-template changes retain their separate effects.

Legacy Stage A runs without a context sidecar retain their existing snapshots. Once a task has used the sidecar, a missing, malformed, or stale context fails closed. Do not delete it to make a task appear reusable. This module provides scoped evidence selection; the remaining B/C capabilities have their separate [advanced contract](advanced-academic.md).
