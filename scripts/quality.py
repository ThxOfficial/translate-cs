#!/usr/bin/env python3
"""Deterministic academic translation integrity checks and quality reports."""

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path

from content_guard import GuardError, validate_restored
from manifest import file_hash, load_manifest, read_output_text


REPORT_SCHEMA_VERSION = 1
REVIEW_SCHEMA_VERSION = 1
HIGH_RISK_RE = re.compile(
    r"\b(abstract|contribution|conclusion|limitation|theorem|lemma|proposition|corollary|proof|"
    r"assumption|definition|experiment|result|evaluation|accuracy|benchmark|ablation|p-value|"
    r"agent|tool call|prompt injection|we prove|we show|we observe)\b|"
    r"\\begin\{(?:equation|align|gather)|\$\$|\\\[",
    re.IGNORECASE,
)
VALID_REVIEW_KINDS = {"independent", "self_check", "coordinator_review"}
VALID_FINDING_SEVERITIES = {"error", "warning", "info"}
VALID_FINDING_STATES = {"open", "fixed", "accepted"}
INPUT_REVIEW_POLICY_VERSION = "academic-input-review-v1"


def _sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def _add_issue(issues, rule_id, severity, chunk_id, message, **details):
    issue = {
        "rule_id": rule_id,
        "severity": severity,
        "source_chunk_id": chunk_id,
        "status": "open",
        "message": message,
    }
    issue.update(details)
    issues.append(issue)
    return issue


def _load_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _validate_review(review, chunk_id, expected_kind, source_text, source_hash, output_hash, task_context_hash):
    problems = []
    if not isinstance(review, dict) or review.get("schema_version") != REVIEW_SCHEMA_VERSION:
        return ["unsupported review schema"]
    if review.get("source_hash") != source_hash or review.get("output_hash") != output_hash:
        problems.append("review is stale for the current source or output hash")
    if not task_context_hash or review.get("task_context_hash") != task_context_hash:
        problems.append("review is stale for the current task context")
    if review.get("review_kind") not in VALID_REVIEW_KINDS:
        problems.append("review_kind is missing or unsupported")
    elif review.get("review_kind") != expected_kind:
        problems.append(f"review_kind must be {expected_kind} for this chunk")
    coverage = review.get("coverage")
    if expected_kind == 'independent':
        execution = review.get('execution', {})
        fields = ('reviewer_id', 'translator_id', 'review_context_id', 'translation_context_id')
        if any(not isinstance(execution.get(k), str) or not execution[k] for k in fields):
            problems.append('independent review requires actual reviewer/translator and context identities')
        elif (execution['reviewer_id'] == execution['translator_id']
              or execution['review_context_id'] == execution['translation_context_id']):
            problems.append('independent review must use a different actor and context')
    if not isinstance(coverage, dict) or not isinstance(coverage.get("chunk_ids"), list):
        problems.append("coverage.chunk_ids must be a list")
    elif chunk_id not in coverage["chunk_ids"]:
        problems.append("review does not declare coverage for this chunk")
    if not isinstance(review.get("findings"), list):
        problems.append("findings must be a list")
        return problems
    for index, finding in enumerate(review["findings"]):
        prefix = f"finding #{index + 1}"
        if not isinstance(finding, dict):
            problems.append(f"{prefix} must be an object")
            continue
        if finding.get("severity") not in VALID_FINDING_SEVERITIES:
            problems.append(f"{prefix} has an unsupported severity")
        if finding.get("status") not in VALID_FINDING_STATES:
            problems.append(f"{prefix} has an unsupported status")
        if not isinstance(finding.get("rule_id"), str) or not finding.get("rule_id"):
            problems.append(f"{prefix} is missing rule_id")
        if not isinstance(finding.get("message"), str) or not finding.get("message"):
            problems.append(f"{prefix} is missing message")
        evidence = finding.get("evidence")
        if not isinstance(evidence, str) or not evidence or evidence not in source_text:
            problems.append(f"{prefix} evidence is not present in the source chunk")
        if finding.get("status") == "accepted" and not finding.get("resolution"):
            problems.append(f"{prefix} is accepted without a resolution")
        if finding.get("status") == "accepted" and not finding.get("resolution_source"):
            problems.append(f"{prefix} is accepted without a resolution source")
        if finding.get("status") == "fixed" and not finding.get("resolution"):
            problems.append(f"{prefix} is fixed without a resolution")
        if finding.get("status") == "fixed" and finding.get("severity") == "error" and finding.get("resolution_checked") is not True:
            problems.append(f"{prefix} is a fixed error without a verification record")
    return problems


def _validate_finding_list(findings, source_text):
    problems = []
    if not isinstance(findings, list):
        return ["findings must be a list"]
    for index, finding in enumerate(findings):
        prefix = f"finding #{index + 1}"
        if not isinstance(finding, dict):
            problems.append(f"{prefix} must be an object")
            continue
        if finding.get("severity") not in VALID_FINDING_SEVERITIES:
            problems.append(f"{prefix} has an unsupported severity")
        if finding.get("status") not in VALID_FINDING_STATES:
            problems.append(f"{prefix} has an unsupported status")
        if not isinstance(finding.get("rule_id"), str) or not finding.get("rule_id"):
            problems.append(f"{prefix} is missing rule_id")
        if not isinstance(finding.get("message"), str) or not finding.get("message"):
            problems.append(f"{prefix} is missing message")
        evidence = finding.get("evidence")
        if not isinstance(evidence, str) or not evidence or evidence not in source_text:
            problems.append(f"{prefix} evidence is not present in the inspected source")
        if finding.get("status") == "accepted" and not finding.get("resolution"):
            problems.append(f"{prefix} is accepted without a resolution")
        if finding.get("status") == "accepted" and not finding.get("resolution_source"):
            problems.append(f"{prefix} is accepted without a resolution source")
        if finding.get("status") == "fixed" and not finding.get("resolution"):
            problems.append(f"{prefix} is fixed without a resolution")
        if finding.get("status") == "fixed" and finding.get("severity") == "error" and finding.get("resolution_checked") is not True:
            problems.append(f"{prefix} is a fixed error without a verification record")
    return problems


def _validate_academic_input_artifacts(temp_path, issues):
    input_path = temp_path / "input.md"
    ingestion_path = temp_path / "ingestion.json"
    map_path = temp_path / "document_map.json"
    if not input_path.is_file():
        _add_issue(issues, "INPUT-MARKDOWN-MISSING", "error", None, "Academic input.md is missing")
        return {"status": "invalid", "input_hash": "", "map_hash": "", "required_checks": [], "review_status": "missing"}
    input_text = input_path.read_text(encoding="utf-8")
    input_hash = file_hash(input_path)
    if not ingestion_path.is_file():
        _add_issue(issues, "INGESTION-MISSING", "error", None, "Academic ingestion.json is missing")
        ingestion = {}
    else:
        try:
            ingestion = _load_json(ingestion_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            ingestion = {}
            _add_issue(issues, "INGESTION-INVALID", "error", None, f"Academic ingestion.json cannot be read: {exc}")
        if not isinstance(ingestion, dict):
            ingestion = {}
            _add_issue(issues, "INGESTION-INVALID", "error", None, "Academic ingestion.json must be a JSON object")
        normalized_input = ingestion.get("normalized_input")
        normalized_hash = normalized_input.get("sha256") if isinstance(normalized_input, dict) else None
        if ingestion.get("profile") != "academic" or normalized_hash != input_hash:
            _add_issue(issues, "INGESTION-STALE", "error", None, "ingestion.json does not match the current input.md")
        origin = ingestion.get('original_input')
        if isinstance(origin, dict):
            if origin.get('source_path') and origin.get('source_sha256'):
                original = Path(origin['source_path'])
                if not original.is_file() or file_hash(original) != origin['source_sha256']:
                    _add_issue(issues, 'ORIGINAL-SOURCE-STALE', 'error', None, 'Original PDF no longer matches parser provenance')
            if origin.get('project_root'):
                project = Path(origin['project_root']).resolve()
                for collection in ('files', 'assets', 'bibliographies'):
                    for relative, digest in origin.get(collection, {}).items():
                        original = (project / relative).resolve()
                        if not original.is_relative_to(project) or not original.is_file() or file_hash(original) != digest:
                            _add_issue(issues, 'ORIGINAL-SOURCE-STALE', 'error', None, 'LaTeX dependency changed: ' + relative)
        if ingestion.get("unresolved_resource_count", 0):
            _add_issue(issues, "INGESTION-RESOURCE-MISSING", "warning", None,
                       f"{ingestion['unresolved_resource_count']} local image resource(s) could not be resolved")
        resources = ingestion.get("resource_rewrites", [])
        if not isinstance(resources, list):
            _add_issue(issues, "INGESTION-RESOURCES", "error", None, "ingestion.json resource_rewrites must be a list")
            resources = []
        for resource in resources:
            if not isinstance(resource, dict):
                _add_issue(issues, "INGESTION-RESOURCE", "error", None, "ingestion.json contains a malformed resource record")
                continue
            localized = resource.get("localized_reference")
            expected_hash = resource.get("sha256")
            if expected_hash:
                if not isinstance(localized, str) or not localized.startswith("images/") or ".." in Path(localized).parts:
                    _add_issue(issues, "INGESTION-RESOURCE-PATH", "error", None, "Localized resource path is invalid")
                    continue
                asset_path = temp_path / localized
                if not asset_path.is_file() or file_hash(asset_path) != expected_hash:
                    _add_issue(issues, "INGESTION-RESOURCE-HASH", "error", None,
                               f"Localized resource is missing or changed: {localized}")
                source_path = resource.get("source_path")
                if resource.get("kind") == "local_image" and isinstance(source_path, str) and os.path.isfile(source_path):
                    if file_hash(source_path) != expected_hash:
                        _add_issue(issues, "INGESTION-SOURCE-RESOURCE-HASH", "error", None,
                                   f"Original linked resource changed since import: {source_path}")
    if not map_path.is_file():
        _add_issue(issues, "DOCUMENT-MAP-MISSING", "error", None, "Academic document_map.json is missing")
        document_map = {}
        map_hash = ""
    else:
        try:
            document_map = _load_json(map_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            document_map = {}
            _add_issue(issues, "DOCUMENT-MAP-INVALID", "error", None, f"document_map.json cannot be read: {exc}")
        if not isinstance(document_map, dict):
            document_map = {}
            _add_issue(issues, "DOCUMENT-MAP-INVALID", "error", None, "document_map.json must be a JSON object")
        map_hash = file_hash(map_path)
        if document_map.get("source_sha256") != input_hash:
            _add_issue(issues, "DOCUMENT-MAP-STALE", "error", None, "document_map.json does not match the current input.md")
        if document_map.get("coordinate_unit") != "python_unicode_codepoint" or document_map.get("range_convention") != "zero_based_left_closed_right_open":
            _add_issue(issues, "DOCUMENT-MAP-RANGE", "error", None, "document_map.json uses an unsupported source coordinate convention")
        map_chunks = document_map.get("chunks", [])
        if not isinstance(map_chunks, list):
            _add_issue(issues, "DOCUMENT-MAP-CHUNKS", "error", None, "document_map.json chunks must be a list")
            map_chunks = []
        valid_chunk_ids = set()
        map_chunk_by_id = {}
        for chunk in map_chunks:
            if not isinstance(chunk, dict):
                _add_issue(issues, "DOCUMENT-MAP-CHUNK", "error", None, "document_map.json contains a malformed chunk")
                continue
            chunk_id = chunk.get("id")
            source_file = chunk.get("source_file")
            if not isinstance(chunk_id, str) or not re.fullmatch(r"chunk\d+", chunk_id) or source_file != f"{chunk_id}.md":
                _add_issue(issues, "DOCUMENT-MAP-CHUNK-ID", "error", chunk_id if isinstance(chunk_id, str) else None,
                           "document_map chunk has an invalid id or source filename")
                continue
            valid_chunk_ids.add(chunk_id)
            map_chunk_by_id[chunk_id] = chunk
            chunk_path = temp_path / source_file
            start, end = chunk.get("source_start"), chunk.get("source_end")
            if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int) or start < 0 or end < start or end > len(input_text):
                _add_issue(issues, "DOCUMENT-MAP-CHUNK-SPAN", "error", chunk_id, "document_map chunk has an invalid source span")
                continue
            if not chunk_path.is_file() or input_text[start:end] != chunk_path.read_text(encoding="utf-8"):
                _add_issue(issues, "DOCUMENT-MAP-CHUNK-SLICE", "error", chunk_id,
                           "document_map chunk does not match its exact input.md source slice")
            elif file_hash(chunk_path) != chunk.get("source_sha256"):
                _add_issue(issues, "DOCUMENT-MAP-CHUNK-HASH", "error", chunk_id, "document_map chunk hash is stale")
        manifest = load_manifest(str(temp_path))
        manifest_chunk_ids = {item.get("id") for item in (manifest or {}).get("chunks", []) if isinstance(item, dict)}
        if valid_chunk_ids != manifest_chunk_ids:
            _add_issue(issues, "DOCUMENT-MAP-COVERAGE", "error", None,
                       "document_map.json chunk coverage does not match manifest.json")
        blocks = document_map.get("blocks", [])
        if not isinstance(blocks, list):
            _add_issue(issues, "DOCUMENT-MAP-BLOCKS", "error", None, "document_map.json blocks must be a list")
            blocks = []
        for block in blocks:
            if not isinstance(block, dict):
                _add_issue(issues, "DOCUMENT-MAP-BLOCK", "error", None, "document_map.json contains a malformed block")
                continue
            if block.get("chunk_id") not in valid_chunk_ids:
                _add_issue(issues, "DOCUMENT-MAP-BLOCK-CHUNK", "error", block.get("chunk_id"),
                           f"Block {block.get('block_id', 'unknown block')} does not reference a declared chunk")
            start, end = block.get("source_start"), block.get("source_end")
            if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int) or start < 0 or end < start or end > len(input_text):
                _add_issue(issues, "DOCUMENT-MAP-SPAN", "error", block.get("chunk_id"),
                           f"Invalid source span for {block.get('block_id', 'unknown block')}")
            elif _sha256_text(input_text[start:end]) != block.get("source_sha256"):
                _add_issue(issues, "DOCUMENT-MAP-HASH", "error", block.get("chunk_id"),
                           f"Source slice hash mismatch for {block.get('block_id', 'unknown block')}")
            else:
                chunk = map_chunk_by_id.get(block.get("chunk_id"), {})
                if not (chunk.get("source_start", 0) <= start <= end <= chunk.get("source_end", -1)):
                    _add_issue(issues, "DOCUMENT-MAP-OWNERSHIP", "error", block.get("chunk_id"),
                               f"Block {block.get('block_id', 'unknown block')} is outside its declared chunk")

    required_checks = ingestion.get("manual_checks_required", []) if isinstance(ingestion, dict) else []
    if not isinstance(required_checks, list) or not all(isinstance(item, str) for item in required_checks):
        required_checks = []
        _add_issue(issues, "INGESTION-CHECKS", "error", None, "ingestion.json manual_checks_required must be a list of strings")
    input_review_status = "missing"
    review_path = temp_path / "reviews" / "ingestion.json"
    if review_path.is_file() and input_hash and map_hash:
        try:
            review = _load_json(review_path)
            problems = []
            if not isinstance(review, dict):
                raise ValueError("Input review must be a JSON object")
            if review.get("schema_version") != REVIEW_SCHEMA_VERSION or review.get("review_kind") != "input_review":
                problems.append("unsupported input review schema or kind")
            if review.get("review_policy_version") != INPUT_REVIEW_POLICY_VERSION:
                problems.append("input review is stale for the current review policy")
            if review.get("input_hash") != input_hash or review.get("document_map_hash") != map_hash:
                problems.append("input review is stale for the current input or document map")
            checks = review.get("checks")
            if not isinstance(checks, list) or not all(isinstance(item, str) for item in checks) or not set(required_checks).issubset(set(checks)):
                problems.append("input review does not cover every required parser-quality check")
            problems.extend(_validate_finding_list(review.get("findings"), input_text))
            if problems:
                input_review_status = "stale_or_invalid"
                for problem in problems:
                    _add_issue(issues, "INPUT-REVIEW-INVALID", "warning", None, problem)
            else:
                input_review_status = "complete"
                for finding in review["findings"]:
                    _add_issue(
                        issues, finding["rule_id"], finding["severity"], None, finding["message"],
                        evidence=finding["evidence"], status=finding["status"],
                        **{key: finding[key] for key in ("resolution", "resolution_source", "resolution_checked") if key in finding},
                    )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            input_review_status = "stale_or_invalid"
            _add_issue(issues, "INPUT-REVIEW-INVALID", "warning", None, f"Input review cannot be read: {exc}")
    if input_review_status == "missing":
        _add_issue(issues, "INPUT-REVIEW-MISSING", "warning", None,
                   "Reading order, equations, tables, and image targets have not been reviewed")
    return {
        "status": "valid" if map_hash and ingestion_path.is_file() else "invalid",
        "input_hash": input_hash,
        "map_hash": map_hash,
        "required_checks": required_checks,
        "review_status": input_review_status,
    }


def _review_plan(profile, review_mode, entries, temp_dir, sample_rate=0.1, require_independent=False):
    """Require a self-check on every chunk and deterministic coordinator coverage."""
    if profile != "academic" or review_mode == "structural_only":
        return {}
    if review_mode not in ("risk_based", "full"):
        raise ValueError(f"Unsupported academic review mode: {review_mode}")
    review_kind = 'independent' if require_independent else 'coordinator_review'
    if review_mode == "full":
        return {entry["id"]: ["self_check", review_kind] for entry in entries}
    required = {entry["id"]: ["self_check"] for entry in entries}
    ordinary = []
    coordinator_ids = set()
    for entry in entries:
        source_path = Path(temp_dir) / entry["source_file"]
        if source_path.is_file() and HIGH_RISK_RE.search(source_path.read_text(encoding="utf-8")):
            coordinator_ids.add(entry["id"])
        else:
            source_hash = entry.get("source_hash") or (file_hash(source_path) if source_path.is_file() else "")
            ordinary.append((hashlib.sha256(f"academic-review-v1:{source_hash}".encode("utf-8")).hexdigest(), entry["id"]))
    if ordinary:
        sample_count = min(len(ordinary), max(1, math.ceil(len(ordinary) * sample_rate)))
        ordinary.sort()
        coordinator_ids.update(chunk_id for _, chunk_id in ordinary[:sample_count])
    for chunk_id in coordinator_ids:
        required[chunk_id].append(review_kind)
    return {chunk_id: required[chunk_id] for chunk_id in sorted(required)}


def _review_path(temp_path, chunk_id, review_kind):
    return Path(temp_path) / "reviews" / f"{chunk_id}.{review_kind}.json"


def build_quality_report(temp_dir):
    temp_path = Path(temp_dir)
    manifest = load_manifest(temp_dir)
    if not manifest:
        raise ValueError("manifest.json is required for academic quality checks")
    spec_path = temp_path / "translation_spec.json"
    spec = _load_json(spec_path) if spec_path.is_file() else {"profile": "generic", "review_mode": "structural_only"}
    profile = spec.get("profile", "generic")
    review_mode = spec.get("review_mode", "risk_based")
    review_policy_version = spec.get("review_policy_version", "academic-review-v1")
    sample_rate = spec.get("review_sample_rate", 0.1)
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, (int, float)) or not 0 < sample_rate <= 1:
        raise ValueError("translation_spec.json review_sample_rate must be in (0, 1]")
    chunks = sorted(manifest.get("chunks", []), key=lambda item: item.get("order", 0))
    issues = []
    input_state = (
        _validate_academic_input_artifacts(temp_path, issues)
        if profile == "academic" else
        {"status": "not_required", "input_hash": "", "map_hash": "", "required_checks": [], "review_status": "not_required"}
    )
    completed = []
    review_plan = _review_plan(profile, review_mode, chunks, temp_dir, sample_rate, spec.get('require_independent_review', False))
    required_reviews = {
        (chunk_id, review_kind)
        for chunk_id, review_kinds in review_plan.items()
        for review_kind in review_kinds
    }
    accepted_reviews = set()
    accepted_by_chunk = {}
    coordinator_reviews = set()
    chunk_reports = []

    for entry in chunks:
        chunk_id = entry["id"]
        source_path = temp_path / entry["source_file"]
        output_path = temp_path / entry["output_file"]
        if not source_path.is_file():
            _add_issue(issues, "INPUT-MISSING", "error", chunk_id, f"Source chunk is missing: {entry['source_file']}")
            continue
        source_text = source_path.read_text(encoding="utf-8")
        source_hash = file_hash(source_path)
        expected_source_hash = entry.get("source_hash")
        if expected_source_hash and source_hash != expected_source_hash:
            _add_issue(issues, "INPUT-HASH", "error", chunk_id, "Source chunk changed since the manifest was created")
        if not output_path.is_file():
            _add_issue(issues, "OUTPUT-MISSING", "error", chunk_id, f"Translated chunk is missing: {entry['output_file']}")
            continue
        output_text = read_output_text(str(output_path))
        if output_text is None or not output_text.strip():
            _add_issue(issues, "OUTPUT-BLANK", "error", chunk_id, "Translated chunk is unreadable or blank")
            continue
        output_hash = file_hash(output_path)
        chunk_findings = []

        if profile == "academic":
            protected_path = temp_path / "protected" / f"{chunk_id}.json"
            if not protected_path.is_file():
                finding = _add_issue(issues, "PROTECT-MANIFEST", "error", chunk_id, "Academic chunk has no protected-span manifest")
                chunk_findings.append(finding)
            else:
                try:
                    protection = _load_json(protected_path)
                    chunk_findings.extend(validate_restored(source_text, output_text, protection))
                    for finding in chunk_findings:
                        finding.setdefault("source_chunk_id", chunk_id)
                        finding.setdefault("status", "open")
                        issues.append(finding)
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    finding = _add_issue(issues, "PROTECT-MANIFEST", "error", chunk_id, f"Invalid protection manifest: {exc}")
                    chunk_findings.append(finding)

        required_kinds = review_plan.get(chunk_id, [])
        if required_kinds:
            context_path = temp_path / "tasks" / f"{chunk_id}.context.json"
            context_problem = None
            if context_path.is_file():
                import task_context as task_context_mod
                try:
                    context_data = task_context_mod.build_context(temp_dir, chunk_id)
                    current_context = task_context_mod.snapshot_hash(context_data)
                    if any(o.get('status') == 'ambiguous' for o in context_data.get('terminology', {}).get('occurrences', [])):
                        _add_issue(issues, 'TERM-AMBIGUOUS', 'warning', chunk_id, 'Bind or explicitly resolve ambiguous v3 term occurrences before final build')
                    stored_context_hash = task_context_mod.stored_context_hash(temp_dir, chunk_id)
                    task_context_hash = current_context
                    if stored_context_hash != current_context:
                        context_problem = "stored task context no longer matches current translation inputs"
                        _add_issue(issues, "REVIEW-CONTEXT-STALE", "warning", chunk_id, context_problem)
                except (OSError, ValueError, KeyError) as exc:
                    task_context_hash = ""
                    context_problem = f"Translation context cannot be validated: {exc}"
                    _add_issue(issues, "CONTEXT-INVALID", "error", chunk_id, context_problem)
            else:
                task_context_hash = ""
            for expected_kind in required_kinds:
                review_path = _review_path(temp_path, chunk_id, expected_kind)
                if not review_path.is_file():
                    _add_issue(issues, "REVIEW-MISSING", "warning", chunk_id,
                               f"Required {expected_kind} has not been recorded")
                    continue
                try:
                    review = _load_json(review_path)
                    problems = _validate_review(
                        review, chunk_id, expected_kind, source_text, source_hash, output_hash, task_context_hash
                    )
                    if context_problem:
                        problems.append(context_problem)
                    if expected_kind == 'independent':
                        import run_state
                        origin = run_state.load_run_state(temp_dir)['chunks'].get(chunk_id, {})
                        execution = review.get('execution', {})
                        if any(not origin.get(k) or origin.get(k) != execution.get(k)
                               for k in ('translator_id', 'translation_context_id')):
                            problems.append('independent review does not match the recorded translation origin')
                    if not isinstance(review, dict) or review.get("review_policy_version") != review_policy_version:
                        problems.append("review is stale for the current review policy")
                    for problem in problems:
                        _add_issue(issues, "REVIEW-INVALID", "warning", chunk_id, problem)
                    if problems:
                        continue
                    accepted_reviews.add((chunk_id, expected_kind))
                    accepted_by_chunk.setdefault(chunk_id, set()).add(expected_kind)
                    if expected_kind == "coordinator_review":
                        coordinator_reviews.add(chunk_id)
                    for finding in review["findings"]:
                        details = {
                            "evidence": finding["evidence"],
                            "status": finding["status"],
                            "review_kind": expected_kind,
                        }
                        for key in ("suggestion", "resolution", "resolution_source", "resolution_checked", "source_span", "output_span"):
                            if key in finding:
                                details[key] = finding[key]
                        _add_issue(
                            issues,
                            finding["rule_id"],
                            finding["severity"],
                            chunk_id,
                            finding["message"],
                            **details,
                        )
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    _add_issue(issues, "REVIEW-INVALID", "warning", chunk_id, f"Review file cannot be read: {exc}")

        completed.append(chunk_id)
        chunk_reports.append({"chunk_id": chunk_id, "source_sha256": source_hash, "output_sha256": output_hash})

    if profile == 'academic' and (temp_path / 'run_state.json').is_file():
        try:
            import run_state
            for cid in run_state.plan(temp_dir)['translation_chunk_ids']:
                _add_issue(issues, 'TRANSLATION-STALE', 'warning', cid, 'Translation dependencies changed; complete reconciliation')
        except (OSError, ValueError, KeyError) as exc:
            _add_issue(issues, 'CONTEXT-INVALID', 'error', None, str(exc))
    open_errors = [issue for issue in issues if issue["severity"] == "error" and issue.get("status", "open") == "open"]
    open_warnings = [issue for issue in issues if issue["severity"] == "warning" and issue.get("status", "open") == "open"]
    unresolved_reviews = sorted(required_reviews - accepted_reviews)
    required_coordinator_reviews = {
        chunk_id for chunk_id, kinds in review_plan.items() if "coordinator_review" in kinds
    }
    unresolved_coordinator_reviews = sorted(required_coordinator_reviews - coordinator_reviews)
    reviewed_chunks = sorted(
        chunk_id for chunk_id, kinds in review_plan.items()
        if set(kinds).issubset(accepted_by_chunk.get(chunk_id, set()))
    )
    unreviewed_chunks = sorted(set(review_plan) - set(reviewed_chunks))
    if open_errors:
        quality_status = "blocked"
    elif open_warnings or unresolved_reviews or unresolved_coordinator_reviews or review_mode == "structural_only":
        quality_status = "needs_review"
    else:
        quality_status = "validated"

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "profile": profile,
        "review_mode": review_mode,
        "quality_status": quality_status,
        "input_review_status": input_state["review_status"],
        "required_input_checks": input_state["required_checks"],
        "source_chunk_count": len(chunks),
        "completed_chunk_ids": completed,
        "missing_or_invalid_chunk_ids": sorted({issue.get("source_chunk_id") for issue in issues if issue["severity"] == "error" and issue.get("source_chunk_id")}),
        "required_review_chunk_ids": sorted(review_plan),
        "required_review_count": len(required_reviews),
        "completed_review_count": len(accepted_reviews),
        "review_plan": {chunk_id: review_plan[chunk_id] for chunk_id in sorted(review_plan)},
        "reviewed_chunk_ids": reviewed_chunks,
        "unreviewed_chunk_ids": unreviewed_chunks,
        "unreviewed_review_checks": [
            {"chunk_id": chunk_id, "review_kind": review_kind}
            for chunk_id, review_kind in unresolved_reviews
        ],
        "required_coordinator_review_chunk_ids": sorted(required_coordinator_reviews),
        "coordinator_reviewed_chunk_ids": sorted(coordinator_reviews),
        "unreviewed_coordinator_review_chunk_ids": unresolved_coordinator_reviews,
        "issue_counts": {
            severity: sum(1 for issue in issues if issue["severity"] == severity)
            for severity in ("error", "warning", "info")
        },
        "chunks": chunk_reports,
        "issues": issues,
    }


def render_quality_report(report):
    lines = [
        "# Translation Quality Report",
        "",
        f"- Profile: `{report['profile']}`",
        f"- Review mode: `{report['review_mode']}`",
        f"- Quality status: **{report['quality_status']}**",
        f"- Source inspection: `{report.get('input_review_status', 'not_required')}`",
        f"- Source chunks: {report['source_chunk_count']}",
        f"- Completed chunks: {len(report['completed_chunk_ids'])}",
        f"- Semantic reviews: {len(report['reviewed_chunk_ids'])}/{len(report['required_review_chunk_ids'])} required",
        f"- Review checks: {report.get('completed_review_count', 0)}/{report.get('required_review_count', 0)} required",
        f"- Coordinator reviews: {len(report['coordinator_reviewed_chunk_ids'])}/{len(report['required_coordinator_review_chunk_ids'])} required",
        "",
        "This report verifies declared structural checks and review coverage. It does not prove mathematical claims or translation correctness beyond recorded evidence.",
        "",
        "## Issues",
        "",
    ]
    if not report["issues"]:
        lines.append("No issues recorded.")
    else:
        lines.extend(["| Severity | Chunk | Rule | Status | Finding |", "|---|---|---|---|---|"])
        for issue in report["issues"]:
            text = str(issue["message"]).replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {issue['severity']} | {issue.get('source_chunk_id') or '—'} | "
                f"{issue['rule_id']} | {issue.get('status', 'open')} | {text} |"
            )
    lines.append("")
    return "\n".join(lines)


def write_quality_report(temp_dir, report):
    temp_path = Path(temp_dir)
    _atomic_write(temp_path / "quality_report.json", json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    _atomic_write(temp_path / "quality_report.md", render_quality_report(report))


def _current_review_context(temp_dir, chunk_id):
    import task_context as task_context_mod

    context_path = Path(temp_dir) / "tasks" / f"{chunk_id}.context.json"
    if not context_path.is_file():
        raise ValueError(f"Task context snapshot is missing for {chunk_id}")
    current_hash = task_context_mod.snapshot_hash(task_context_mod.build_context(temp_dir, chunk_id))
    stored_hash = task_context_mod.stored_context_hash(temp_dir, chunk_id)
    if stored_hash != current_hash:
        raise ValueError(f"Task context snapshot is stale for {chunk_id}; refresh it before review")
    return current_hash


def record_review(temp_dir, chunk_id, review_kind, findings_path):
    temp_path = Path(temp_dir)
    manifest = load_manifest(temp_dir)
    if not manifest:
        raise ValueError("manifest.json is required to record a review")
    entry = next((item for item in manifest.get("chunks", []) if item.get("id") == chunk_id), None)
    if entry is None:
        raise ValueError(f"Unknown chunk id {chunk_id!r}")
    spec_path = temp_path / "translation_spec.json"
    spec = _load_json(spec_path) if spec_path.is_file() else {}
    plan = _review_plan(
        spec.get("profile", "generic"), spec.get("review_mode", "structural_only"),
        manifest.get("chunks", []), temp_dir, spec.get("review_sample_rate", 0.1),
        spec.get('require_independent_review', False),
    )
    expected_kinds = plan.get(chunk_id, [])
    if not expected_kinds:
        raise ValueError(f"No semantic review is requested for {chunk_id} under the current review mode")
    if review_kind not in expected_kinds:
        raise ValueError(f"{chunk_id} requires {', '.join(expected_kinds)}, got {review_kind}")
    source_path = temp_path / entry["source_file"]
    output_path = temp_path / entry["output_file"]
    if not source_path.is_file() or not output_path.is_file():
        raise ValueError(f"Source or output chunk is missing for {chunk_id}")
    findings_payload = _load_json(Path(findings_path))
    findings = findings_payload.get("findings") if isinstance(findings_payload, dict) else findings_payload
    if not isinstance(findings, list):
        raise ValueError("Findings file must be a list or an object with a findings list")
    source_text = source_path.read_text(encoding="utf-8")
    output_text = output_path.read_text(encoding="utf-8")
    task_context_hash = _current_review_context(temp_dir, chunk_id)
    review = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "source_hash": file_hash(source_path),
        "output_hash": file_hash(output_path),
        "task_context_hash": task_context_hash,
        "review_policy_version": spec.get("review_policy_version", "academic-review-v1"),
        "review_kind": review_kind,
        "coverage": {"chunk_ids": [chunk_id]},
        "findings": findings,
    }
    if review_kind == 'independent':
        review['execution'] = findings_payload.get('execution', {})
        import run_state
        origin = run_state.load_run_state(temp_dir)['chunks'].get(chunk_id, {})
        if any(not origin.get(k) or origin.get(k) != review['execution'].get(k)
               for k in ('translator_id', 'translation_context_id')):
            raise ValueError('Independent reviewer must declare the recorded translator/context origin')
    problems = _validate_review(
        review, chunk_id, review_kind, source_text, file_hash(source_path),
        file_hash(output_path), task_context_hash,
    )
    if problems:
        raise ValueError("Review record is invalid: " + "; ".join(problems))
    review_path = _review_path(temp_path, chunk_id, review_kind)
    _atomic_write(review_path, json.dumps(review, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return review_path


def record_input_review(temp_dir, findings_path):
    temp_path = Path(temp_dir)
    issues = []
    state = _validate_academic_input_artifacts(temp_path, issues)
    blocking = [issue for issue in issues if issue["severity"] == "error"]
    if blocking:
        raise ValueError("Academic input artifacts are invalid: " + "; ".join(issue["message"] for issue in blocking))
    payload = _load_json(Path(findings_path))
    if not isinstance(payload, dict):
        raise ValueError("Input review file must be an object with checks and findings")
    checks = payload.get("checks")
    findings = payload.get("findings")
    if not isinstance(checks, list) or not all(isinstance(item, str) for item in checks) or not set(state["required_checks"]).issubset(set(checks)):
        raise ValueError("Input review must cover every required check listed in ingestion.json")
    input_text = (temp_path / "input.md").read_text(encoding="utf-8")
    problems = _validate_finding_list(findings, input_text)
    if problems:
        raise ValueError("Input review record is invalid: " + "; ".join(problems))
    review = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "review_kind": "input_review",
        "review_policy_version": INPUT_REVIEW_POLICY_VERSION,
        "input_hash": state["input_hash"],
        "document_map_hash": state["map_hash"],
        "checks": sorted(set(checks)),
        "findings": findings,
    }
    review_path = temp_path / "reviews" / "ingestion.json"
    _atomic_write(review_path, json.dumps(review, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return review_path


def main():
    parser = argparse.ArgumentParser(description="Check academic translation structure and semantic review coverage")
    parser.add_argument("temp_dir")
    parser.add_argument("--no-write", action="store_true", help="Print the report without writing report files")
    parser.add_argument("--review-plan", action="store_true", help="Print the required review kind for every academic chunk")
    parser.add_argument("--record-review", metavar="CHUNK_ID", help="Record a reviewed chunk after computing its source/output/context hashes")
    parser.add_argument("--record-input-review", action="store_true",
                        help="Record inspection of reading order, equations, tables, and image targets")
    parser.add_argument("--review-kind", choices=sorted(VALID_REVIEW_KINDS))
    parser.add_argument("--findings-file", help="JSON findings list or {\"findings\": [...]} for --record-review")
    args = parser.parse_args()
    try:
        if args.record_review:
            if not args.review_kind or not args.findings_file:
                parser.error("--record-review requires --review-kind and --findings-file")
            path = record_review(args.temp_dir, args.record_review, args.review_kind, args.findings_file)
            print(json.dumps({"review_file": str(path), "status": "recorded"}, ensure_ascii=False))
            return 0
        if args.record_input_review:
            if not args.findings_file:
                parser.error("--record-input-review requires --findings-file")
            path = record_input_review(args.temp_dir, args.findings_file)
            print(json.dumps({"review_file": str(path), "status": "recorded"}, ensure_ascii=False))
            return 0
        if args.review_plan:
            report = build_quality_report(args.temp_dir)
            print(json.dumps({"review_plan": report["review_plan"],
                              "required_coordinator_review_chunk_ids": report["required_coordinator_review_chunk_ids"],
                              "required_review_count": report["required_review_count"],
                              "input_review_status": report["input_review_status"],
                              "required_input_checks": report["required_input_checks"]},
                             ensure_ascii=False, indent=2))
            return 0
        report = build_quality_report(args.temp_dir)
        if not args.no_write:
            write_quality_report(args.temp_dir, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["quality_status"] == "validated" else 2 if report["quality_status"] == "needs_review" else 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
