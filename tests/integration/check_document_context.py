"""Real local context/planner smoke test; source copies are NOT quality evidence."""

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import document_context
import run_state
import task_context


def cli(script, *args):
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / script), *map(str, args)],
                            capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout


def main():
    artifacts = ROOT / "tests" / ".artifacts" / "academic-stageB1"
    artifacts.mkdir(parents=True, exist_ok=True)
    cli("convert.py", ROOT / "tests/fixtures/academic/context-book.md", "--profile", "academic",
        "--subject", "agent", "--subject", "math", "--olang", "zh", "--chunk-size", "100",
        "--temp-root", artifacts, "--format", "md")
    temp = artifacts / "context-book_temp"
    cli("document_context.py", temp, "--prepare")
    data = document_context.load_context(temp)
    source = (temp / "input.md").read_text(encoding="utf-8")
    mapping = json.loads((temp / "document_map.json").read_text(encoding="utf-8"))
    section = next(s["section_id"] for s in data["sections"] if s["title"] == "Agent systems")
    excerpt = "We define a policy as a mapping from histories to actions."
    start = source.index(excerpt)
    block = next(b for b in mapping["blocks"] if b["source_start"] <= start < b["source_end"])
    fact = {"id": "policy", "key": "policy", "statement": excerpt, "confirmed": True,
            "scope": {"kind": "section", "section_id": section},
            "evidence_block_ids": [block["block_id"]],
            "evidence": [{"block_id": block["block_id"], "source_span": [start, start + len(excerpt)],
                          "source_excerpt": excerpt}]}
    facts = {group: [] for group in document_context.FACT_GROUPS}
    facts["definitions"] = [fact]
    facts_path = artifacts / "context-facts.json"
    facts_path.write_text(json.dumps(facts, indent=2), encoding="utf-8")
    cli("document_context.py", temp, "--prepare", "--facts-file", facts_path)
    chunks = [c["id"] for c in mapping["chunks"]]
    expected = []
    for chunk_id in chunks:
        context = task_context.write_context(temp, chunk_id)
        if any(item["fact"]["id"] == "policy" for item in context["document_context"]["facts"]):
            expected.append(chunk_id)
        # Deliberate pipeline mock: never record semantic reviews or build a
        # validated translation from these source copies.
        (temp / f"output_{chunk_id}.md").write_bytes((temp / f"{chunk_id}.md").read_bytes())
    run_state.record_chunks(str(temp), chunks)
    assert not run_state.plan(str(temp))["translation_chunk_ids"]
    fact["statement"] += " Policy here denotes the decision mapping, not an access-control rule."
    facts_path.write_text(json.dumps(facts, indent=2), encoding="utf-8")
    cli("document_context.py", temp, "--prepare", "--facts-file", facts_path)
    for chunk_id in chunks:
        task_context.write_context(temp, chunk_id)
    plan = json.loads(cli("run_state.py", "plan", temp))
    assert plan["translation_chunk_ids"] == expected
    assert 0 < len(expected) < len(chunks)
    report = {"status": "passed", "evaluation_kind": "pipeline_mock_not_translation",
              "source_chunk_count": len(chunks), "affected_chunk_ids": expected,
              "unchanged_chunk_ids": plan["unchanged_chunk_ids"],
              "semantic_quality_evaluated": False}
    (artifacts / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
