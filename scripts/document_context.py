#!/usr/bin/env python3
"""Source-grounded document context; candidates are never confirmed facts.

The index visits every mapped block. A coordinator confirms only facts with
exact source evidence, then tasks receive relevant facts within their section
ancestry. Whole-document hashes validate the sidecar but are not prompt inputs.
"""

import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path

import content_guard
import glossary
from manifest import file_hash


SCHEMA_VERSION = 1
FACT_GROUPS = ("topics", "definitions", "abbreviations", "symbols")
DEFAULT_MAX_CHARS = 8000
_PRIORITY_RE = re.compile(
    r"\b(?:abstract|introduction|contributions?|conclusions?|definition|theorem|lemma)\b"
    r"|摘要|引言|贡献|结论|定义|定理|引理", re.I
)
_DEFINITION_RE = re.compile(
    r"\b(?:definition|theorem|lemma|denote|defined|we define|let)\b|定义|定理|记作|设", re.I
)
_ABBREVIATION_RE = re.compile(r"\b[A-Za-z][A-Za-z -]{2,100}\s+\([A-Z][A-Z0-9-]{1,11}\)")
_REFERENCE_RE = re.compile(r"^(?:references|bibliography|参考文献)\b", re.I)
_GREEK = set(("alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda "
              "mu nu xi pi rho sigma tau upsilon phi varphi chi psi omega Gamma Delta Theta "
              "Lambda Xi Pi Sigma Upsilon Phi Psi Omega").split())


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source(temp_dir):
    root = Path(temp_dir)
    source = (root / "input.md").read_text(encoding="utf-8")
    mapping = _json(root / "document_map.json")
    if not isinstance(mapping, dict) or mapping.get("schema_version") != 1:
        raise ValueError("document_context requires document_map.json schema 1")
    if mapping.get("source_sha256") != file_hash(root / "input.md"):
        raise ValueError("document_map.json is stale for input.md")
    if (mapping.get("coordinate_unit") != "python_unicode_codepoint"
            or mapping.get("range_convention") != "zero_based_left_closed_right_open"):
        raise ValueError("document_context requires Unicode left-closed/right-open source coordinates")
    blocks = mapping.get("blocks")
    if not isinstance(blocks, list):
        raise ValueError("document_map.json must list source blocks")
    chunks = mapping.get("chunks")
    if not isinstance(chunks, list):
        raise ValueError("document_map.json must list source chunks")
    chunk_ids = set()
    for chunk in chunks:
        if not isinstance(chunk, dict):
            raise ValueError("Source chunk records must be objects")
        chunk_id = chunk.get("id")
        start, end = chunk.get("source_start"), chunk.get("source_end")
        if (not isinstance(chunk_id, str) or not re.fullmatch(r"chunk\d+", chunk_id)
                or chunk_id in chunk_ids or type(start) is not int or type(end) is not int
                or not 0 <= start <= end <= len(source)):
            raise ValueError("Invalid source chunk in document_map.json")
        chunk_ids.add(chunk_id)
        if (chunk.get("source_sha256") != _text_hash(source[start:end])
                or file_hash(root / f"{chunk_id}.md") != chunk.get("source_sha256")):
            raise ValueError(f"Source chunk {chunk_id} no longer matches document_map.json")
    by_id = {}
    previous_end = 0
    for block in blocks:
        if not isinstance(block, dict):
            raise ValueError("Source block records must be objects")
        start, end = block.get("source_start"), block.get("source_end")
        if (type(start) is not int or type(end) is not int
                or not previous_end <= start <= end <= len(source)):
            raise ValueError("Invalid source block coordinates in document_map.json")
        block_id = block.get("block_id")
        if not isinstance(block_id, str) or not block_id or block_id in by_id:
            raise ValueError("Source block IDs must be unique nonempty strings")
        if block.get("source_sha256") != _text_hash(source[start:end]):
            raise ValueError(f"Source block {block_id} has a stale hash")
        by_id[block_id] = block
        previous_end = end
    for chunk in chunks:
        if chunk.get("block_ids") != [b["block_id"] for b in blocks if b.get("chunk_id") == chunk["id"]]:
            raise ValueError(f"Chunk {chunk['id']} block membership is inconsistent")
    return root, source, mapping, by_id


def _sections(source, blocks):
    sections, stack, block_sections = [], [], {}
    for block in blocks:
        if block.get("kind") == "heading":
            text = source[block["source_start"]:block["source_end"]]
            match = re.match(r"^ {0,3}(#{1,6})\s+(.*)$", text)
            if not match:
                raise ValueError(f"Invalid heading block {block['block_id']}")
            level = len(match[1])
            stack = [section for section in stack if section["level"] < level]
            section = {
                "section_id": block["block_id"],
                "parent_id": stack[-1]["section_id"] if stack else None,
                "level": level,
                "title": re.sub(r"\s*\{[^}]*\}\s*$", "", match[2]).strip(),
                "evidence_block_ids": [block["block_id"]],
                "source_excerpt": text,
            }
            sections.append(section)
            stack.append(section)
        block_sections[block["block_id"]] = [item["section_id"] for item in stack]
    return sections, block_sections


def _views(text):
    """Return coordinate-preserving prose and math views, excluding code/prompts."""
    _, protection = content_guard.mask_text(text, "chunk0000")
    prose = list(text)
    math = [" "] * len(text)
    for record in protection["protected_spans"]:
        start, end = record["start"], record["end"]
        if record["kind"] in ("inline_math", "display_math", "math_environment"):
            math[start:end] = text[start:end]
        prose[start:end] = " " * (end - start)
    return "".join(prose), "".join(math)


def _symbol_matches(math):
    for match in re.finditer(r"\\[A-Za-z]+|(?<![A-Za-z\\])[A-Za-z](?![A-Za-z])", math):
        if not match[0].startswith("\\") or match[0][1:] in _GREEK:
            yield match


def _is_reference(ancestry, sections):
    return any(_REFERENCE_RE.match(sections[item]["title"]) for item in ancestry)


def _prose_matches(key, text):
    if glossary._contains_cjk(key):
        return key.casefold() in text.casefold()
    return re.search(rf"(?<!\w){re.escape(key)}(?!\w)", text, re.I) is not None


def build_index(temp_dir):
    root, source, mapping, _ = _source(temp_dir)
    sections, ancestry = _sections(source, mapping["blocks"])
    section_by_id = {item["section_id"]: item for item in sections}
    candidates, symbols, priority = [], [], []
    for block in mapping["blocks"]:
        block_id = block["block_id"]
        path = ancestry[block_id]
        if _is_reference(path, section_by_id):
            continue
        text = source[block["source_start"]:block["source_end"]]
        prose, math = _views(text)
        if _PRIORITY_RE.search(prose) or any(_PRIORITY_RE.search(section_by_id[s]["title"]) for s in path):
            priority.append(block_id)
        for kind, pattern in (("definition", _DEFINITION_RE), ("abbreviation", _ABBREVIATION_RE)):
            for match in pattern.finditer(prose):
                start, end = max(0, match.start() - 100), min(len(text), match.end() + 180)
                candidates.append({
                    "kind": kind, "status": "candidate", "evidence_block_ids": [block_id],
                    "source_span": [block["source_start"] + start, block["source_start"] + end],
                    "source_excerpt": text[start:end], "section_id": path[-1] if path else None,
                })
        for match in _symbol_matches(math):
            symbols.append({
                "symbol": match[0], "evidence_block_ids": [block_id],
                "source_span": [block["source_start"] + match.start(), block["source_start"] + match.end()],
                "section_id": path[-1] if path else None,
            })
    return {
        "schema_version": SCHEMA_VERSION,
        "source_sha256": file_hash(root / "input.md"),
        "document_map_sha256": file_hash(root / "document_map.json"),
        "coordinate_unit": "python_unicode_codepoint",
        "sections": sections, "priority_block_ids": priority,
        "candidates": candidates, "symbol_occurrences": symbols,
        **{group: [] for group in FACT_GROUPS},
    }


def _validate_facts(data, source, blocks, sections, ancestry):
    seen = set()
    for group in FACT_GROUPS:
        facts = data.get(group)
        if not isinstance(facts, list):
            raise ValueError(f"document_context {group} must be a list")
        for fact in facts:
            if not isinstance(fact, dict) or fact.get("confirmed") is not True:
                raise ValueError("Only explicitly confirmed facts belong in context fact lists")
            for field in ("id", "key", "statement"):
                if not isinstance(fact.get(field), str) or not fact[field].strip():
                    raise ValueError(f"Context fact requires a nonempty {field}")
            if fact["id"] in seen:
                raise ValueError(f"Duplicate context fact ID {fact['id']}")
            seen.add(fact["id"])
            scope = fact.get("scope")
            if not isinstance(scope, dict) or scope.get("kind") not in ("document", "section"):
                raise ValueError(f"Fact {fact['id']} requires a document or section scope")
            section_id = scope.get("section_id")
            if scope["kind"] == "section" and section_id not in sections:
                raise ValueError(f"Unknown section scope for fact {fact['id']}")
            if scope["kind"] == "document" and section_id is not None:
                raise ValueError("Document scope cannot specify a section_id")
            evidence = fact.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                raise ValueError(f"Fact {fact['id']} needs exact source evidence")
            evidence_ids = []
            for item in evidence:
                if not isinstance(item, dict):
                    raise ValueError("Context evidence must be an object")
                block_id, span = item.get("block_id"), item.get("source_span")
                if block_id not in blocks or not isinstance(span, list) or len(span) != 2:
                    raise ValueError(f"Invalid evidence location for fact {fact['id']}")
                start, end = span
                block = blocks[block_id]
                if (type(start) is not int or type(end) is not int
                        or not block["source_start"] <= start < end <= block["source_end"]
                        or source[start:end] != item.get("source_excerpt")):
                    raise ValueError(f"Evidence is not an exact source slice for fact {fact['id']}")
                if scope["kind"] == "section" and section_id not in ancestry[block_id]:
                    raise ValueError(f"Evidence for fact {fact['id']} is outside its section scope")
                evidence_ids.append(block_id)
            if fact.get("evidence_block_ids") != sorted(set(evidence_ids)):
                raise ValueError(f"Fact {fact['id']} evidence_block_ids do not match its evidence")


def load_context(temp_dir):
    root, source, mapping, blocks = _source(temp_dir)
    data = _json(root / "document_context.json")
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported document_context.json schema")
    if (data.get("source_sha256") != file_hash(root / "input.md")
            or data.get("document_map_sha256") != file_hash(root / "document_map.json")):
        raise ValueError("document_context.json is stale; inspect the new source in a fresh run")
    sections, ancestry = _sections(source, mapping["blocks"])
    if data.get("sections") != sections:
        raise ValueError("document_context section tree does not match source headings")
    _validate_facts(data, source, blocks, {s["section_id"]: s for s in sections}, ancestry)
    return data


def prepare_context(temp_dir, facts_file=None):
    """Atomically initialize/update context without discarding confirmed facts."""
    path = Path(temp_dir) / "document_context.json"
    previous = load_context(temp_dir) if path.is_file() else None
    data = build_index(temp_dir)
    if previous:
        for group in FACT_GROUPS:
            data[group] = previous[group]
    if facts_file is not None:
        supplied = _json(facts_file)
        if not isinstance(supplied, dict) or set(supplied) != set(FACT_GROUPS):
            raise ValueError("Facts file must contain exactly topics, definitions, abbreviations, symbols")
        # This is an explicit replacement of the confirmed fact lists. The
        # coordinator supplies all four; partial updates cannot erase others.
        for group in FACT_GROUPS:
            data[group] = supplied[group]
    root, source, mapping, blocks = _source(temp_dir)
    sections, ancestry = _sections(source, mapping["blocks"])
    _validate_facts(data, source, blocks, {s["section_id"]: s for s in sections}, ancestry)
    content_guard._atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return data


def select_for_chunk(temp_dir, chunk_id, max_chars=DEFAULT_MAX_CHARS):
    data = load_context(temp_dir)
    _, source, mapping, _ = _source(temp_dir)
    chunks = {chunk["id"]: chunk for chunk in mapping["chunks"]}
    if chunk_id not in chunks:
        raise ValueError(f"Unknown chunk {chunk_id}")
    sections, ancestry = _sections(source, mapping["blocks"])
    section_by_id = {s["section_id"]: s for s in sections}
    block_by_id = {b["block_id"]: b for b in mapping["blocks"]}
    selected_paths = []
    for block_id in chunks[chunk_id]["block_ids"]:
        block = block_by_id[block_id]
        path = ancestry[block_id]
        if path not in selected_paths:
            selected_paths.append(path)
    # Resolve per section, even when a chunk straddles sibling sections.
    # A local T never becomes a chunk-wide rule for an unrelated section.
    selected, ambiguities = [], []
    for path in selected_paths:
        applicable = set(path)
        section_blocks = [block_by_id[b] for b in chunks[chunk_id]["block_ids"] if ancestry[b] == path]
        local_prose, local_math = [], []
        for block in section_blocks:
            if _is_reference(path, section_by_id):
                continue
            visible, math = _views(source[block["source_start"]:block["source_end"]])
            local_prose.append(visible)
            local_math.append(math)
        local_text = "\n".join(local_prose)
        local_symbols = {m[0] for m in _symbol_matches("\n".join(local_math))}
        groups = {}
        for group in FACT_GROUPS:
            for fact in data[group]:
                scope = fact["scope"]
                sid = scope.get("section_id")
                if scope["kind"] == "section" and sid not in applicable:
                    continue
                key = fact["key"]
                relevant = (group == "topics" or (key in local_symbols if group == "symbols" else
                            _prose_matches(key, local_text)))
                if relevant:
                    groups.setdefault((group, key if group == "symbols" else key.casefold()), []).append(fact)
        for (group, key), candidates in groups.items():
            rank = lambda f: path.index(f["scope"]["section_id"]) + 1 if f["scope"]["kind"] == "section" else 0
            closest = max(rank(f) for f in candidates)
            winners = sorted(({
                field: f[field] for field in ("id", "key", "statement", "scope", "evidence", "evidence_block_ids")
            } for f in candidates if rank(f) == closest), key=lambda f: f["id"])
            placement = {"section_id": path[-1] if path else None,
                         "block_ids": [b["block_id"] for b in section_blocks]}
            if len(winners) > 1:
                ambiguities.append({"kind": group, "key": key, **placement, "alternatives": winners})
            else:
                selected.append({"kind": group, **placement, "fact": winners[0]})
    used_sections = {sid for path in selected_paths for sid in path}
    result = {
        "schema_version": SCHEMA_VERSION,
        "sections": [s for s in sections if s["section_id"] in used_sections],
        "facts": selected, "ambiguities": ambiguities,
    }
    size = len(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if type(max_chars) is not int or max_chars <= 0 or size > max_chars:
        raise ValueError(f"Required document context is {size} characters; budget {max_chars}. "
                         "Curate facts or reduce task scope; context was not silently truncated.")
    return copy.deepcopy(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("temp_dir")
    parser.add_argument("--prepare", action="store_true", help="Scan all blocks, preserving confirmed facts")
    parser.add_argument("--facts-file", help="Validated replacement for all four confirmed fact lists")
    parser.add_argument("--chunk-id", help="Show the exact scoped context for one chunk")
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    args = parser.parse_args()
    try:
        if args.facts_file and not args.prepare:
            parser.error("--facts-file requires --prepare")
        if args.prepare:
            data = prepare_context(args.temp_dir, args.facts_file)
            result = {"path": str(Path(args.temp_dir) / "document_context.json"),
                      "candidate_count": len(data["candidates"]), "section_count": len(data["sections"]),
                      "confirmed_fact_count": sum(len(data[g]) for g in FACT_GROUPS)}
        elif args.chunk_id:
            result = select_for_chunk(args.temp_dir, args.chunk_id, args.max_chars)
        else:
            result = load_context(args.temp_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
