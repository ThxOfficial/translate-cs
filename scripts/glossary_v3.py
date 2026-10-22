"""Explicitly migrated, sense-aware terminology with occurrence resolution."""
import copy
import hashlib
import json
import re
from pathlib import Path

import content_guard
from manifest import file_hash

POLICIES = {"translate", "keep_source", "first_occurrence_bilingual"}
PROMPT_FIELDS = ("id", "source", "target", "aliases", "category", "sense", "scope",
                 "translation_policy", "locked", "semantic_notes")


def defaults(term):
    term.setdefault("sense", "")
    term.setdefault("scope", {"kind": "document"})
    term.setdefault("translation_policy", "first_occurrence_bilingual")
    term.setdefault("locked", False)
    term.setdefault("provenance", {"kind": "migrated_v2"})
    term.setdefault("semantic_notes", term.get("notes", ""))
    return term


def validate(data):
    if not isinstance(data, dict) or data.get("version") != 3 or not isinstance(data.get("terms"), list):
        raise ValueError("Expected glossary version 3 with a terms array")
    ids = set()
    for term in data["terms"]:
        if not isinstance(term, dict):
            raise ValueError("v3 term must be an object")
        for field in ("id", "source", "target", "category", "sense", "semantic_notes"):
            if not isinstance(term.get(field), str) or (field in ("id", "source") and not term[field]):
                raise ValueError(f"Invalid v3 term field {field}")
        if term["id"] in ids:
            raise ValueError("v3 term IDs must be unique")
        ids.add(term["id"])
        aliases = term.get("aliases")
        if (not isinstance(aliases, list) or any(not isinstance(a, str) or not a for a in aliases)
                or len(set(aliases)) != len(aliases) or term["source"] in aliases):
            raise ValueError("v3 aliases must be unique nonempty strings distinct from source")
        scope = term.get("scope")
        if not isinstance(scope, dict) or scope.get("kind") not in ("document", "section"):
            raise ValueError("v3 scope must be document or section")
        if scope["kind"] == "section" and not re.fullmatch(r"block\d+", str(scope.get("section_id", ""))):
            raise ValueError("v3 section scope requires a heading block ID")
        if term.get("translation_policy") not in POLICIES or type(term.get("locked")) is not bool:
            raise ValueError("Invalid v3 translation policy or locked flag")
        if not isinstance(term.get("provenance"), dict) or not term["provenance"].get("kind"):
            raise ValueError("v3 provenance requires a kind")
    if not isinstance(data.get("applied_meta_hashes", {}), dict):
        raise ValueError("Invalid v3 consumed-meta map")
    return data


def migrate(data):
    if data.get("version") == 3:
        return validate(copy.deepcopy(data))
    if data.get("version") != 2:
        raise ValueError("Load legacy glossary through glossary.py before migrating")
    result = copy.deepcopy(data)
    result["version"] = 3
    for term in result["terms"]:
        defaults(term)
    return validate(result)


def prompt_term(term):
    return {key: term.get(key) for key in PROMPT_FIELDS}


def term_hash(term):
    return hashlib.sha256(json.dumps(prompt_term(term), ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _positions(text, surface):
    # CJK concepts need substring matching; ASCII/non-CJK words use boundaries.
    cjk = any('\u3400' <= c <= '\u9fff' or '\u3040' <= c <= '\u30ff' for c in surface)
    pattern = re.escape(surface) if cjk else rf"(?<!\w){re.escape(surface)}(?!\w)"
    return re.finditer(pattern, text, re.I)


def resolve_chunk(temp_dir, chunk_id, data):
    import document_context
    validate(data)
    root, source, mapping, blocks = document_context._source(temp_dir)
    sections, ancestry = document_context._sections(source, mapping["blocks"])
    section_ids = {s["section_id"] for s in sections}
    for term in data["terms"]:
        if term["scope"]["kind"] == "section" and term["scope"]["section_id"] not in section_ids:
            raise ValueError(f"Unknown section scope for term {term['id']}")
    chunk = next((c for c in mapping["chunks"] if c["id"] == chunk_id), None)
    if chunk is None:
        raise ValueError(f"Unknown chunk {chunk_id}")
    bindings_path = root / "term_bindings.json"
    bindings = []
    if bindings_path.is_file():
        payload = json.loads(bindings_path.read_text(encoding="utf-8"))
        if (payload.get("schema_version") != 1 or payload.get("source_sha256") != file_hash(root / "input.md")
                or not isinstance(payload.get("bindings"), list)):
            raise ValueError("Invalid or stale term_bindings.json")
        bindings = payload["bindings"]
        seen = set()
        term_by_id = {t["id"]: t for t in data["terms"]}
        for binding in bindings:
            if not isinstance(binding, dict):
                raise ValueError("Term binding must be an object")
            bid, span, tid = binding.get("block_id"), binding.get("source_span"), binding.get("term_id")
            if bid not in blocks or tid not in term_by_id or not isinstance(span, list) or len(span) != 2:
                raise ValueError("Term binding has an unknown identity")
            a, b = span
            block = blocks[bid]
            if type(a) is not int or type(b) is not int or not block["source_start"] <= a < b <= block["source_end"]:
                raise ValueError("Term binding has invalid source coordinates")
            term = term_by_id[tid]
            if source[a:b].casefold() not in {s.casefold() for s in [term["source"], *term["aliases"]]}:
                raise ValueError("Bound source span is not a term surface")
            scope = term["scope"]
            if scope["kind"] == "section" and scope["section_id"] not in ancestry[bid]:
                raise ValueError("Term binding lies outside the term scope")
            key = (bid, a, b)
            if key in seen:
                raise ValueError("Duplicate occurrence binding")
            seen.add(key)
    occurrences, used = [], set()
    section_map = {s["section_id"]: s for s in sections}
    for bid in chunk["block_ids"]:
        block = blocks[bid]
        if document_context._is_reference(ancestry[bid], section_map):
            continue
        text = source[block["source_start"]:block["source_end"]]
        prose, _ = document_context._views(text)
        matches = []
        for term in data["terms"]:
            scope = term["scope"]
            if scope["kind"] == "section" and scope["section_id"] not in ancestry[bid]:
                continue
            for surface in [term["source"], *term["aliases"]]:
                for match in _positions(prose, surface):
                    matches.append((match.start() + block["source_start"], match.end() + block["source_start"], term["id"]))
        clusters = []
        for a, b, tid in sorted(set(matches)):
            if clusters and a < clusters[-1]["end"]:
                clusters[-1]["end"] = max(b, clusters[-1]["end"])
                clusters[-1]["matches"].append((a, b, tid))
            else:
                clusters.append({"start": a, "end": b, "matches": [(a, b, tid)]})
        for cluster in clusters:
            ids = sorted({m[2] for m in cluster["matches"]})
            bound = [b for b in bindings if b["block_id"] == bid
                     and (b["source_span"][0], b["source_span"][1], b["term_id"]) in cluster["matches"]]
            if len(bound) > 1:
                raise ValueError("Overlapping term bindings need one explicit resolution")
            tid = bound[0]["term_id"] if bound else (ids[0] if len(ids) == 1 else None)
            span = bound[0]["source_span"] if bound else [cluster["start"], cluster["end"]]
            occurrence = {"block_id": bid, "source_span": span, "surface": source[span[0]:span[1]],
                          "status": "resolved" if tid else "ambiguous", "candidate_term_ids": ids, "term_id": tid}
            occurrences.append(occurrence)
            if tid:
                used.add(tid)
    # Bindings in this chunk must point at visible prose occurrences, not code,
    # references or substrings inside larger words.
    for binding in bindings:
        if binding["block_id"] in chunk["block_ids"] and not any(
                o["block_id"] == binding["block_id"] and o["source_span"] == binding["source_span"]
                and o["term_id"] == binding["term_id"] for o in occurrences):
            raise ValueError("Binding is not a translatable prose occurrence")
    return {"schema_version": 1, "occurrences": occurrences,
            "terms": [prompt_term(t) for t in sorted(data["terms"], key=lambda t: t["id"]) if t["id"] in used]}


def first_occurrences(temp_dir, data):
    mapping = json.loads((Path(temp_dir) / "document_map.json").read_text(encoding="utf-8"))
    first = {}
    terms = {t["id"]: t for t in data["terms"]}
    for chunk in sorted(mapping["chunks"], key=lambda c: c["order"]):
        for o in resolve_chunk(temp_dir, chunk["id"], data)["occurrences"]:
            tid = o["term_id"]
            if tid and tid not in first and terms[tid]["translation_policy"] == "first_occurrence_bilingual":
                first[tid] = {"chunk_id": chunk["id"], "surface": o["surface"], "block_id": o["block_id"],
                              "source_span": o["source_span"], "source": terms[tid]["source"], "target": terms[tid]["target"]}
    return first
