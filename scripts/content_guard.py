#!/usr/bin/env python3
"""Protect source spans that an academic translation model must not rewrite.

This is deliberately a bounded Markdown/LaTeX scanner, not a general-purpose
parser. It preserves raw slices and fails closed on ambiguous or malformed
protected regions.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path


SCHEMA_VERSION = 1
TOKEN_PREFIX = "⟪CSAI:"
CHUNK_ID_RE = re.compile(r"^chunk\d+$")

_MATH_PATTERNS = (
    re.compile(r"\\begin\{(equation\*?|align\*?|gather\*?|multline\*?|cases|aligned|split)\}.*?\\end\{\1\}", re.DOTALL),
    re.compile(r"(?<!\\)\$\$.*?(?<!\\)\$\$", re.DOTALL),
    re.compile(r"(?<!\\)\\\[.*?\\\]", re.DOTALL),
    re.compile(r"(?<!\\)\\\(.*?\\\)"),
    re.compile(r"(?<!\\)(?<!\$)\$(?!\$)(?=\S)(?:\\.|[^$\n\\])*?(?<!\s)(?<!\\)\$(?!\$)"),
)
_INLINE_CODE_RE = re.compile(r"(?<!`)(`+)(?!`)([^\n]*?)(?<!`)\1(?!`)")
_LATEX_REFERENCE_RE = re.compile(
    r"\\(?:cite\w*|ref|eqref|autoref|cref|Cref|pageref|label|footnote|url|doi)\*?(?:\[[^\]]*\])*\{[^{}]*\}"
)
_PANDOC_CITE_RE = re.compile(r"(?<=\[)@[A-Za-z][A-Za-z0-9_.:-]*(?=[^\]]*\])")
_ARXIV_RE = re.compile(r"\barXiv:\d{4}\.\d{4,5}(?:v\d+)?\b", re.IGNORECASE)
_DOI_RE = re.compile(r"\b10\.\d{4,9}/[A-Za-z0-9._;()/:-]+", re.IGNORECASE)
_URL_RE = re.compile(r"\b(?:https?|ftp)://[^\s<>\"']+", re.IGNORECASE)
_NUMBER_RE = re.compile(r"(?<![\w])\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][+-]?\d+)?(?:\s?(?:%|‰))?(?![\w])")
_MODEL_ID_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:[-_.][A-Za-z0-9]+)*\b")
_LINK_RE = re.compile(r"!?\[[^\]\n]*\]\((<[^>\n]*>|[^)\s]+)(?:\s+(?:\"[^\"]*\"|'[^']*'))?\)")
_HTML_TARGET_RE = re.compile(r"\b(?:src|href)\s*=\s*([\"'])(.*?)\1", re.IGNORECASE | re.DOTALL)
_HTML_TAG_RE = re.compile(r"</?[A-Za-z][^<>]*>")
_HTML_ALT_ATTR_RE = re.compile(r"(?<![\w:-])(alt\s*=\s*)([\"'])(.*?)\2", re.IGNORECASE | re.DOTALL)
_REFERENCE_DEFINITION_RE = re.compile(r"(?m)^ {0,3}\[[^\]\n]+\]:[^\r\n]*")
_PROMPT_ROLE_LINE_RE = re.compile(
    r"^ {0,3}(?:>\s*)?(?:[-*+]\s+)?(?:\*\*)?(?:system|user|assistant|tool)(?:\*\*)?\s*[:：]",
    re.IGNORECASE,
)
_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})(?:\s+|$)(.*)$")
_LIST_RE = re.compile(r"^\s*(?:([-+*])\s+|(\d+)[.)]\s+)")
_TABLE_LINE_RE = re.compile(r"^ {0,3}\|")
_MATH_ENV_TOKEN_RE = re.compile(r"\\(begin|end)\{(equation\*?|align\*?|gather\*?|multline\*?|cases|aligned|split)\}")
_TOKEN_RE = re.compile(r"⟪CSAI:[0-9a-f]{16,64}:\d{4,}⟫")


class GuardError(ValueError):
    """Raised when source protection or deterministic restoration is unsafe."""


def _sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def _fenced_code_spans(text):
    spans = []
    offset = 0
    open_fence = None
    for line in text.splitlines(keepends=True):
        match = _FENCE_OPEN_RE.match(line)
        if open_fence is None and match:
            fence = match.group(1)
            open_fence = (offset, fence[0], len(fence))
        elif open_fence is not None:
            _, marker, width = open_fence
            stripped = line.lstrip(" ")
            indent = len(line) - len(stripped)
            closing = re.match(rf"{re.escape(marker)}{{{width},}}[ \t]*(?:\r?\n)?$", stripped)
            if indent <= 3 and closing:
                spans.append((open_fence[0], offset + len(line), "code_block"))
                open_fence = None
        offset += len(line)
    if open_fence is not None:
        raise GuardError(f"Unclosed {open_fence[1] * open_fence[2]} code fence at offset {open_fence[0]}")
    return spans


def _inline_code_spans(text):
    spans = []
    for match in _INLINE_CODE_RE.finditer(text):
        spans.append((match.start(), match.end(), "inline_code"))
    return spans


def _indented_code_spans(text):
    """Recognize Markdown indented code blocks without consuming prose blanks."""
    lines = text.splitlines(keepends=True)
    starts = []
    offset = 0
    for line in lines:
        starts.append(offset)
        offset += len(line)
    spans = []
    index = 0
    while index < len(lines):
        if not (lines[index].startswith("    ") or lines[index].startswith("\t")):
            index += 1
            continue
        start = index
        end = index + 1
        while end < len(lines):
            if lines[end].startswith("    ") or lines[end].startswith("\t"):
                end += 1
                continue
            if lines[end].strip("\r\n") == "":
                lookahead = end
                while lookahead < len(lines) and lines[lookahead].strip("\r\n") == "":
                    lookahead += 1
                if lookahead < len(lines) and (lines[lookahead].startswith("    ") or lines[lookahead].startswith("\t")):
                    end = lookahead
                    continue
            break
        spans.append((starts[start], starts[end - 1] + len(lines[end - 1]), "code_block"))
        index = end
    return spans


def _html_tag_spans(text):
    spans = []
    for match in _HTML_TAG_RE.finditer(text):
        tag_text = match.group()
        if re.match(r"<img\b", tag_text, re.IGNORECASE):
            alt = _HTML_ALT_ATTR_RE.search(tag_text)
            if alt:
                value_start = match.start() + alt.start(3)
                value_end = match.start() + alt.end(3)
                if match.start() < value_start:
                    spans.append((match.start(), value_start, "html_tag"))
                if value_end < match.end():
                    spans.append((value_end, match.end(), "html_tag"))
                continue
        spans.append((match.start(), match.end(), "html_tag"))
    return spans


def _prompt_transcript_spans(text):
    """Protect explicit role-labelled prompt/tool transcripts as source data."""
    spans = []
    offset = 0
    active_start = None
    active_end = None
    for line in text.splitlines(keepends=True):
        if _PROMPT_ROLE_LINE_RE.match(line):
            if active_start is not None:
                spans.append((active_start, active_end, "prompt_transcript"))
            active_start = offset
            active_end = offset + len(line)
        elif active_start is not None and line.strip():
            active_end = offset + len(line)
        elif active_start is not None:
            spans.append((active_start, active_end, "prompt_transcript"))
            active_start = None
            active_end = None
        offset += len(line)
    if active_start is not None:
        spans.append((active_start, active_end, "prompt_transcript"))
    return spans


def _candidate_spans(text):
    spans = _fenced_code_spans(text)
    indented_code = _indented_code_spans(text)
    code_spans = spans + indented_code + _inline_code_spans(text)
    spans.extend(indented_code)
    _validate_display_math_delimiters(text, code_spans)
    for pattern, kind in (
        (_MATH_PATTERNS[0], "math_environment"),
        (_MATH_PATTERNS[1], "display_math"),
        (_MATH_PATTERNS[2], "display_math"),
        (_MATH_PATTERNS[3], "inline_math"),
        (_MATH_PATTERNS[4], "inline_math"),
    ):
        spans.extend((m.start(), m.end(), kind) for m in pattern.finditer(text))
    spans.extend(_inline_code_spans(text))
    spans.extend(_prompt_transcript_spans(text))
    spans.extend(_html_tag_spans(text))
    spans.extend((m.start(), m.end(), "latex_reference") for m in _LATEX_REFERENCE_RE.finditer(text))
    spans.extend((m.start(), m.end(), "reference_definition") for m in _REFERENCE_DEFINITION_RE.finditer(text))
    spans.extend((m.start(), m.end(), "arxiv_id") for m in _ARXIV_RE.finditer(text))
    spans.extend((m.start(), _url_end(text, m.start(), m.end()), "doi") for m in _DOI_RE.finditer(text))
    spans.extend((m.start(), _url_end(text, m.start(), m.end()), "url") for m in _URL_RE.finditer(text))
    spans.extend((m.start(1), m.end(1), "link_target") for m in _LINK_RE.finditer(text))
    spans.extend((m.start(2), m.end(2), "html_target") for m in _HTML_TARGET_RE.finditer(text))
    spans.extend((m.start(), m.end(), "citation_key") for m in _PANDOC_CITE_RE.finditer(text))

    for match in _MODEL_ID_RE.finditer(text):
        value = match.group()
        if re.search(r"\d", value) and re.search(r"[A-Za-z]", value):
            spans.append((match.start(), match.end(), "model_or_dataset_id"))
    spans.extend((m.start(), m.end(), "numeric_value") for m in _NUMBER_RE.finditer(text))
    return spans


def _url_end(text, start, end):
    """Exclude sentence punctuation and unmatched closing brackets from URLs."""
    while end > start and text[end - 1] in ".,;:!?":
        end -= 1
    pairs = (("(", ")"), ("[", "]"), ("{", "}"))
    while end > start and text[end - 1] in ")]}":
        url_text = text[start:end]
        opening, closing = next(pair for pair in pairs if pair[1] == text[end - 1])
        if url_text.count(closing) <= url_text.count(opening):
            break
        end -= 1
    return end


def _validate_display_math_delimiters(text, code_spans):
    """Reject unbalanced display delimiters outside code before translation."""
    pairs = (("$$", "$$", "display_math"), (r"\[", r"\]", "display_math"),
             (r"\(", r"\)", "inline_math"))
    for opening, closing, kind in pairs:
        occurrences = []
        delimiters = ((opening, "toggle"),) if opening == closing else ((opening, "open"), (closing, "close"))
        for delimiter, role in delimiters:
            start = 0
            while True:
                index = text.find(delimiter, start)
                if index < 0:
                    break
                if index > 0 and text[index - 1] == "\\":
                    start = index + len(delimiter)
                    continue
                if not any(span_start <= index < span_end for span_start, span_end, _ in code_spans):
                    occurrences.append((index, role, delimiter))
                start = index + len(delimiter)
        occurrences.sort()
        open_at = None
        for index, role, delimiter in occurrences:
            if opening == closing:
                if open_at is None:
                    open_at = index
                else:
                    open_at = None
            elif role == "open":
                if open_at is not None:
                    raise GuardError(f"Nested {kind} delimiter at offset {index}")
                open_at = index
            elif open_at is None:
                raise GuardError(f"Unmatched {kind} closing delimiter {delimiter!r} at offset {index}")
            else:
                open_at = None
        if open_at is not None:
            raise GuardError(f"Unbalanced {kind} delimiter at offset {open_at}")

    environment_stack = []
    for match in _MATH_ENV_TOKEN_RE.finditer(text):
        if any(start <= match.start() < end for start, end, _ in code_spans):
            continue
        action, name = match.groups()
        if action == "begin":
            environment_stack.append((name, match.start()))
        elif not environment_stack or environment_stack[-1][0] != name:
            raise GuardError(f"Unmatched LaTeX environment end {name!r} at offset {match.start()}")
        else:
            environment_stack.pop()
    if environment_stack:
        name, start = environment_stack[-1]
        raise GuardError(f"Unclosed LaTeX environment {name!r} at offset {start}")


def _select_non_overlapping(spans):
    by_start = sorted(spans, key=lambda item: (item[0], -(item[1] - item[0]), item[2]))
    selected = []
    for start, end, kind in by_start:
        if start >= end:
            continue
        if selected and start < selected[-1][1]:
            prior_start, prior_end, _ = selected[-1]
            if start >= prior_start and end <= prior_end:
                continue
            raise GuardError(f"Overlapping protected spans at offsets {start} and {prior_end}")
        selected.append((start, end, kind))
    return selected


def mask_text(text, chunk_id):
    """Return (masked Markdown, manifest) without modifying source text."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not CHUNK_ID_RE.fullmatch(chunk_id):
        raise GuardError(f"Invalid chunk id: {chunk_id!r}")
    source_hash = _sha256_text(text)
    nonce = source_hash[:16]
    if f"{TOKEN_PREFIX}{nonce}:" in text:
        nonce = source_hash
    if f"{TOKEN_PREFIX}{nonce}:" in text:
        raise GuardError("Could not construct collision-free protected token namespace")

    spans = _select_non_overlapping(_candidate_spans(text))
    records = []
    parts = []
    cursor = 0
    for index, (start, end, kind) in enumerate(spans, 1):
        token = f"{TOKEN_PREFIX}{nonce}:{index:04d}⟫"
        original = text[start:end]
        records.append({
            "token": token,
            "kind": kind,
            "start": start,
            "end": end,
            "sha256": _sha256_text(original),
            "source": original,
        })
        parts.extend((text[cursor:start], token))
        cursor = end
    parts.append(text[cursor:])
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "chunk_id": chunk_id,
        "source_sha256": source_hash,
        "source_length": len(text),
        "protected_spans": records,
    }
    return "".join(parts), manifest


def _validate_manifest(manifest, source_text=None):
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
        raise GuardError("Unsupported protected-span manifest")
    if not CHUNK_ID_RE.fullmatch(str(manifest.get("chunk_id", ""))):
        raise GuardError("Invalid chunk id in protected-span manifest")
    records = manifest.get("protected_spans")
    if not isinstance(records, list):
        raise GuardError("protected_spans must be a list")
    previous_end = 0
    seen_tokens = set()
    for record in records:
        if not isinstance(record, dict):
            raise GuardError("Protected span entry must be an object")
        token = record.get("token")
        start, end = record.get("start"), record.get("end")
        original = record.get("source")
        if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token) or token in seen_tokens:
            raise GuardError("Invalid or duplicate protected token")
        if not isinstance(start, int) or not isinstance(end, int) or not (previous_end <= start < end):
            raise GuardError("Protected spans must be ordered, non-overlapping character offsets")
        if not isinstance(original, str) or _sha256_text(original) != record.get("sha256"):
            raise GuardError(f"Stored source hash mismatch for token {token}")
        if end - start != len(original):
            raise GuardError(f"Stored source length mismatch for token {token}")
        if source_text is not None and source_text[start:end] != original:
            raise GuardError(f"Protected source changed at offsets {start}:{end}")
        seen_tokens.add(token)
        previous_end = end
    if source_text is not None:
        if len(source_text) != manifest.get("source_length"):
            raise GuardError("Source length does not match protected-span manifest")
        if _sha256_text(source_text) != manifest.get("source_sha256"):
            raise GuardError("Source hash does not match protected-span manifest")
    return records


def _table_cells(line):
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    cells = []
    current = []
    escaped = False
    in_code = False
    code_ticks = 0
    index = 0
    while index < len(body):
        char = body[index]
        if char == "\\" and not escaped:
            escaped = True
            current.append(char)
            index += 1
            continue
        if char == "`" and not escaped:
            end = index
            while end < len(body) and body[end] == "`":
                end += 1
            width = end - index
            if not in_code:
                in_code, code_ticks = True, width
            elif width == code_ticks:
                in_code, code_ticks = False, 0
            current.extend(body[index:end])
            index = end
            escaped = False
            continue
        if char == "|" and not escaped and not in_code:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        escaped = False
        index += 1
    cells.append("".join(current).strip())
    return cells


def _structure_signature(text):
    signature = {"headings": [], "tables": [], "lists": [], "quotes": 0, "html_tags": []}
    in_fence = False
    fence_marker = None
    current_table = []
    for line in text.splitlines():
        fence_match = _FENCE_OPEN_RE.match(line)
        if fence_match:
            fence = fence_match.group(1)
            if not in_fence:
                in_fence, fence_marker = True, (fence[0], len(fence))
            else:
                close = re.match(rf"^ {{0,3}}{re.escape(fence_marker[0])}{{{fence_marker[1]},}}[ \t]*$", line)
                if close:
                    in_fence, fence_marker = False, None
            continue
        if in_fence:
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            anchor = re.search(r"\{#([^}]+)\}\s*$", heading.group(2))
            signature["headings"].append((len(heading.group(1)), anchor.group(1) if anchor else None))
        if _TABLE_LINE_RE.match(line):
            current_table.append(len(_table_cells(line)))
        elif current_table:
            signature["tables"].append(current_table)
            current_table = []
        list_match = _LIST_RE.match(line)
        if list_match:
            signature["lists"].append("ordered" if list_match.group(2) else list_match.group(1))
        if re.match(r"^ {0,3}>", line):
            signature["quotes"] += 1
    if current_table:
        signature["tables"].append(current_table)
    for match in _HTML_TAG_RE.finditer(text):
        tag = _HTML_ALT_ATTR_RE.sub(lambda alt: alt.group(1) + alt.group(2) + "<ALT>" + alt.group(2), match.group())
        signature["html_tags"].append(tag)
    return signature


def validate_restored(source_text, output_text, manifest):
    """Return deterministic findings after exact span restoration."""
    findings = []
    try:
        _validate_manifest(manifest, source_text)
    except GuardError as exc:
        return [{"rule_id": "PROTECT-SOURCE", "severity": "error", "message": str(exc)}]

    source_structure = _structure_signature(source_text)
    output_structure = _structure_signature(output_text)
    if source_structure != output_structure:
        findings.append({
            "rule_id": "STRUCTURE-MARKDOWN",
            "severity": "error",
            "message": "Heading, table, list, or blockquote structure changed",
            "source_structure": source_structure,
            "output_structure": output_structure,
        })

    for record in manifest["protected_spans"]:
        original = source_text[record["start"]:record["end"]]
        if _sha256_text(original) != record["sha256"]:
            findings.append({"rule_id": "PROTECT-HASH", "severity": "error", "token": record["token"], "message": "Protected span hash mismatch"})
    required = {}
    for record in manifest["protected_spans"]:
        key = (record["source"], record["kind"])
        required[key] = required.get(key, 0) + 1
    for (span, kind), count in required.items():
        if kind == "numeric_value":
            pattern = re.compile(rf"(?<![\w]){re.escape(span)}(?![\w])")
            actual = len(pattern.findall(output_text))
        else:
            actual = output_text.count(span)
        if actual < count:
            findings.append({
                "rule_id": "PROTECT-CONTENT",
                "severity": "error",
                "message": f"Protected {kind} content is missing or changed: {span[:80]!r}",
                "expected_occurrences": count,
                "actual_occurrences": actual,
            })
    return findings


def restore_text(masked_translation, manifest, source_text):
    """Restore exact protected slices; fail if the model changed token identity/order."""
    records = _validate_manifest(manifest, source_text)
    expected = [record["token"] for record in records]
    found = _TOKEN_RE.findall(masked_translation)
    if found != expected:
        raise GuardError("Protected tokens are missing, duplicated, altered, or out of order")
    if masked_translation.count(TOKEN_PREFIX) != len(expected):
        raise GuardError("Unknown or malformed protected token found in draft")

    restored = masked_translation
    for record in records:
        token = record["token"]
        original = source_text[record["start"]:record["end"]]
        if _sha256_text(original) != record["sha256"]:
            raise GuardError(f"Protected source hash mismatch for {token}")
        restored = restored.replace(token, original, 1)
    findings = validate_restored(source_text, restored, manifest)
    if findings:
        raise GuardError("Restored draft failed structural validation: " + "; ".join(f["message"] for f in findings))
    return restored


def prepare_chunk(temp_dir, chunk_id):
    if not CHUNK_ID_RE.fullmatch(chunk_id):
        raise GuardError(f"Invalid chunk id: {chunk_id!r}")
    temp_dir = Path(temp_dir)
    source_path = temp_dir / f"{chunk_id}.md"
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    source_text = source_path.read_text(encoding="utf-8")
    masked_text, manifest = mask_text(source_text, chunk_id)
    task_path = temp_dir / "tasks" / f"{chunk_id}.md"
    manifest_path = temp_dir / "protected" / f"{chunk_id}.json"
    _atomic_write(task_path, masked_text)
    _atomic_write(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return task_path, manifest_path, manifest


def restore_chunk(temp_dir, chunk_id, draft_path=None, output_path=None):
    temp_dir = Path(temp_dir)
    source_path = temp_dir / f"{chunk_id}.md"
    manifest_path = temp_dir / "protected" / f"{chunk_id}.json"
    if not source_path.is_file() or not manifest_path.is_file():
        raise FileNotFoundError("Source chunk or protection manifest is missing")
    draft_path = Path(draft_path) if draft_path else temp_dir / "drafts" / f"output_{chunk_id}.masked.md"
    output_path = Path(output_path) if output_path else temp_dir / f"output_{chunk_id}.md"
    source_text = source_path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    draft = draft_path.read_text(encoding="utf-8")
    restored = restore_text(draft, manifest, source_text)
    _atomic_write(output_path, restored)
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Protect, restore, and validate academic translation chunks")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare", help="Write a masked task and protection manifest")
    prepare_parser.add_argument("temp_dir")
    prepare_parser.add_argument("chunk_id")
    restore_parser = commands.add_parser("restore", help="Restore protected spans and commit a valid draft")
    restore_parser.add_argument("temp_dir")
    restore_parser.add_argument("chunk_id")
    restore_parser.add_argument("--draft", default=None)
    restore_parser.add_argument("--output", default=None)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            task_path, manifest_path, manifest = prepare_chunk(args.temp_dir, args.chunk_id)
            print(json.dumps({"task_file": str(task_path), "manifest_file": str(manifest_path), "protected_span_count": len(manifest["protected_spans"])}, ensure_ascii=False))
        else:
            output_path = restore_chunk(args.temp_dir, args.chunk_id, args.draft, args.output)
            print(json.dumps({"output_file": str(output_path), "status": "validated"}, ensure_ascii=False))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
