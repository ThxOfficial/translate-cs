#!/usr/bin/env python3
"""Build and fingerprint the exact reusable context for one translation chunk."""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import chunk_context
import content_guard
import document_context
import glossary
from manifest import file_hash


SCHEMA_VERSION = 1
FIRST_OCCURRENCE_SCHEMA_VERSION = 1


def _canonical_hash(data):
    serialized = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _surface_positions(text, surface):
    if not surface or (glossary._contains_cjk(surface) and len(surface) <= 1):
        return []
    if surface.isascii():
        pattern = re.compile(rf"(?<!\w){re.escape(surface)}(?!\w)", re.IGNORECASE)
    else:
        pattern = re.compile(re.escape(surface), re.IGNORECASE)
    return [(match.start(), text[match.start():match.end()]) for match in pattern.finditer(text)]


def _first_visible_surface(text, term):
    matches = []
    for surface in [term.get("source", ""), *(term.get("aliases") or [])]:
        occurrences = _surface_positions(text, surface)
        if occurrences:
            position, actual_surface = occurrences[0]
            matches.append((position, -len(surface), actual_surface))
    return min(matches)[2] if matches else None


def prepare_first_occurrence_plan(temp_dir):
    """Record each glossary term's first prose occurrence, ignoring protected spans."""
    temp_path = Path(temp_dir)
    glossary_path = temp_path / "glossary.json"
    chunk_paths = sorted(
        (path for path in temp_path.glob("chunk*.md") if re.fullmatch(r"chunk\d+\.md", path.name)),
        key=lambda path: int(path.stem[5:]),
    )
    glossary_data = glossary.load_glossary(str(glossary_path)) if glossary_path.is_file() else {"terms": []}
    if glossary_data.get('version') == 3:
        import glossary_v3
        identity = {"source": file_hash(temp_path / 'document_map.json'),
                    "terms": {t['id']: glossary_v3.term_hash(t) for t in glossary_data['terms']},
                    "bindings": file_hash(temp_path / 'term_bindings.json') if (temp_path / 'term_bindings.json').is_file() else ''}
        path = temp_path / 'tasks' / 'first_occurrences.json'
        if path.is_file():
            cached = json.loads(path.read_text(encoding='utf-8'))
            if cached.get('identity') == identity:
                return cached
        plan = {"schema_version": FIRST_OCCURRENCE_SCHEMA_VERSION, "identity": identity,
                "first_occurrences": glossary_v3.first_occurrences(temp_dir, glossary_data)}
        content_guard._atomic_write(path, json.dumps(plan, ensure_ascii=False, indent=2) + '\n')
        return plan
    source_hashes = {path.stem: file_hash(path) for path in chunk_paths}
    term_hashes = {
        term.get("id", term.get("source", "")): glossary.term_hash(term)
        for term in glossary_data.get("terms", [])
    }
    plan_path = temp_path / "tasks" / "first_occurrences.json"
    if plan_path.is_file():
        cached = json.loads(plan_path.read_text(encoding="utf-8"))
        if (
            cached.get("schema_version") == FIRST_OCCURRENCE_SCHEMA_VERSION
            and cached.get("source_hashes") == source_hashes
            and cached.get("term_hashes") == term_hashes
        ):
            return cached

    first_occurrences = {}
    for path in chunk_paths:
        source = path.read_text(encoding="utf-8")
        visible_text, _ = content_guard.mask_text(source, path.stem)
        for term in glossary_data.get("terms", []):
            term_id = term.get("id", term.get("source", ""))
            if term_id in first_occurrences:
                continue
            surface = _first_visible_surface(visible_text, term)
            if surface:
                first_occurrences[term_id] = {
                    "chunk_id": path.stem,
                    "surface": surface,
                    "source": term.get("source", ""),
                    "target": term.get("target", ""),
                }

    data = {
        "schema_version": FIRST_OCCURRENCE_SCHEMA_VERSION,
        "source_hashes": source_hashes,
        "term_hashes": term_hashes,
        "first_occurrences": first_occurrences,
    }
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".first-occurrences.", suffix=".tmp", dir=str(plan_path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp_name, plan_path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return data


def build_context(temp_dir, chunk_id):
    temp_path = Path(temp_dir)
    source_path = temp_path / f"{chunk_id}.md"
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    source_text = source_path.read_text(encoding="utf-8")
    source_hash = file_hash(source_path)

    spec_path = temp_path / "translation_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8")) if spec_path.is_file() else {}
    translation_fields = (
        "profile", "document_kind", "subjects", "source_lang", "target_lang",
        "style", "term_display", "code_policy", "prompt_example_policy",
        "bibliography_policy", "translator_notes", "custom_instructions",
        "translation_prompt_version", "translation_prompt_sha256", "protection_version",
    )
    translation_spec = {field: spec.get(field) for field in translation_fields}

    glossary_path = temp_path / "glossary.json"
    selected_terms = []
    term_table = ""
    semantic_notes = ""
    first_occurrence_terms = []
    terminology = None
    if glossary_path.is_file():
        glossary_data = glossary.load_glossary(str(glossary_path))
        if glossary_data.get('version') == 3:
            import glossary_v3
            terminology = glossary_v3.resolve_chunk(temp_dir, chunk_id, glossary_data)
            selected_terms = terminology['terms']
            term_table = json.dumps(terminology, ensure_ascii=False, sort_keys=True)
            if len(term_table) > 12000:
                raise ValueError('Resolved v3 terminology exceeds the 12000-character task budget')
        else:
            selected_terms = glossary.select_terms_for_chunk(
                glossary_data, source_text,
                fail_on_local_overflow=spec.get("profile") == "academic",
            )
            term_table = glossary.format_terms_for_prompt(selected_terms)
        if spec.get("profile") == "academic":
            semantic_notes = glossary.format_semantic_notes_for_prompt(selected_terms)
            occurrence_plan = prepare_first_occurrence_plan(temp_dir).get("first_occurrences", {})
            first_occurrence_terms = [
                {"term_id": term_id, **placement}
                for term_id, placement in occurrence_plan.items()
                if placement.get("chunk_id") == chunk_id
            ]

    neighbor_data = chunk_context.get_neighbor_context(str(temp_path), f"{chunk_id}.md")
    neighbor_prompt = chunk_context.format_for_prompt(neighbor_data)
    protection_path = temp_path / "protected" / f"{chunk_id}.json"
    protection_hash = file_hash(protection_path) if protection_path.is_file() else ""

    context = {
        "schema_version": SCHEMA_VERSION,
        "chunk_id": chunk_id,
        "source_sha256": source_hash,
        "translation_spec": translation_spec,
        "selected_term_ids": [term.get("id", term.get("source", "")) for term in selected_terms],
        "term_hashes": {
            term.get("id", term.get("source", "")): glossary.term_hash(
                term, include_notes=spec.get("profile") == "academic"
            )
            for term in selected_terms
        },
        "term_table": term_table,
        "semantic_notes": semantic_notes,
        "first_occurrence_terms": first_occurrence_terms,
        "neighbor_context": neighbor_prompt,
        "protected_manifest_sha256": protection_hash,
    }
    document_context_path = temp_path / "document_context.json"
    if terminology is not None:
        context['terminology'] = terminology
    if spec.get("profile") == "academic":
        if document_context_path.is_file():
            context["document_context"] = document_context.select_for_chunk(temp_dir, chunk_id)
        else:
            # Keep Stage A snapshots compatible. Once a task used the new
            # sidecar, deleting it must not silently drop its semantic inputs.
            previous = temp_path / "tasks" / f"{chunk_id}.context.json"
            if previous.is_file() and "document_context" in json.loads(previous.read_text(encoding="utf-8")):
                raise ValueError("document_context.json is missing for a task that previously used it")
    return context


def snapshot_hash(data):
    return _canonical_hash(data)


def stored_context_hash(temp_dir, chunk_id):
    path = Path(temp_dir) / "tasks" / f"{chunk_id}.context.json"
    if not path.is_file():
        return ""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Unsupported task context snapshot: {path}")
    return snapshot_hash(data)


def write_context(temp_dir, chunk_id):
    data = build_context(temp_dir, chunk_id)
    path = Path(temp_dir) / "tasks" / f"{chunk_id}.context.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise
    return data


def main():
    parser = argparse.ArgumentParser(description="Snapshot a chunk's prompt context and term dependencies")
    parser.add_argument("temp_dir")
    parser.add_argument("chunk_id", nargs="?")
    parser.add_argument("--write", action="store_true", help="Persist the snapshot under tasks/")
    parser.add_argument("--prepare-first-occurrences", action="store_true",
                        help="Build or reuse the document-wide first-occurrence plan")
    args = parser.parse_args()
    try:
        if args.prepare_first_occurrences:
            data = prepare_first_occurrence_plan(args.temp_dir)
            result = {"first_occurrence_plan": str(Path(args.temp_dir) / "tasks" / "first_occurrences.json"),
                      "plan_sha256": snapshot_hash(data), "term_count": len(data["first_occurrences"])}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if not args.chunk_id:
            parser.error("chunk_id is required unless --prepare-first-occurrences is used")
        data = write_context(args.temp_dir, args.chunk_id) if args.write else build_context(args.temp_dir, args.chunk_id)
        print(json.dumps({"context": data, "context_sha256": snapshot_hash(data)}, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
