import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import content_guard  # noqa: E402
import quality  # noqa: E402
import task_context  # noqa: E402
import document_context  # noqa: E402
from manifest import create_manifest, file_hash  # noqa: E402


class AcademicQualityTests(unittest.TestCase):
    def test_added_context_stales_old_reviews_and_deleted_used_context_blocks(self):
        self._make_run(["An ordinary model statement."])
        self._write_review("chunk0001", "self_check")
        self._write_review("chunk0001", "coordinator_review")
        self.assertEqual(quality.build_quality_report(str(self.root))["quality_status"], "validated")
        document_context.prepare_context(self.root)
        self.assertEqual(quality.build_quality_report(str(self.root))["quality_status"], "needs_review")
        task_context.write_context(str(self.root), "chunk0001")
        self.assertEqual(quality.build_quality_report(str(self.root))["quality_status"], "needs_review")
        (self.root / "document_context.json").unlink()
        report = quality.build_quality_report(str(self.root))
        self.assertEqual(report["quality_status"], "blocked")
        self.assertTrue(any(issue["rule_id"] == "CONTEXT-INVALID" for issue in report["issues"]))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _make_run(self, sources, sample_rate=0.1, review_mode="risk_based"):
        names = []
        for index, source in enumerate(sources, 1):
            chunk_id = f"chunk{index:04d}"
            names.append(chunk_id + ".md")
            (self.root / names[-1]).write_text(source, encoding="utf-8")
        input_text = "\n\n".join(sources)
        (self.root / "input.md").write_text(input_text, encoding="utf-8")
        create_manifest(str(self.root), names, str(self.root / "input.md"))
        map_chunks = []
        cursor = 0
        for index, name in enumerate(names, 1):
            source_text = (self.root / name).read_text(encoding="utf-8")
            start = input_text.find(source_text, cursor)
            end = start + len(source_text)
            map_chunks.append({
                "id": Path(name).stem,
                "order": index,
                "source_file": name,
                "source_start": start,
                "source_end": end,
                "source_sha256": file_hash(self.root / name),
                "block_ids": [],
            })
            cursor = end
        document_map = {
            "schema_version": 1,
            "source_sha256": file_hash(self.root / "input.md"),
            "coordinate_unit": "python_unicode_codepoint",
            "range_convention": "zero_based_left_closed_right_open",
            "parser": "test-fixture",
            "chunks": map_chunks,
            "blocks": [],
        }
        (self.root / "document_map.json").write_text(json.dumps(document_map), encoding="utf-8")
        manual_checks = [
            "reading_order_and_scope",
            "equation_completeness_and_delimiters",
            "table_rows_columns_and_headers",
            "image_targets_and_missing_assets",
        ]
        ingestion = {
            "schema_version": 1,
            "profile": "academic",
            "normalized_input": {"sha256": file_hash(self.root / "input.md")},
            "parser": {"version": "test-fixture"},
            "manual_checks_required": manual_checks,
            "unresolved_resource_count": 0,
        }
        (self.root / "ingestion.json").write_text(json.dumps(ingestion), encoding="utf-8")
        spec = {
            "schema_version": 1,
            "profile": "academic",
            "document_kind": "paper",
            "subjects": ["ai"],
            "source_lang": "en",
            "target_lang": "zh",
            "review_mode": review_mode,
            "review_sample_rate": sample_rate,
            "review_policy_version": "academic-review-v1",
        }
        (self.root / "translation_spec.json").write_text(
            json.dumps(spec, ensure_ascii=False), encoding="utf-8"
        )
        for index, name in enumerate(names, 1):
            chunk_id = f"chunk{index:04d}"
            source_path = self.root / name
            content_guard.prepare_chunk(str(self.root), chunk_id)
            output_path = self.root / f"output_{name}"
            output_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
        input_review = self.root / "drafts" / "input-review.json"
        input_review.parent.mkdir(parents=True, exist_ok=True)
        input_review.write_text(json.dumps({"checks": manual_checks, "findings": []}), encoding="utf-8")
        quality.record_input_review(str(self.root), str(input_review))
        return spec

    def _write_review(self, chunk_id, kind):
        source_path = self.root / f"{chunk_id}.md"
        output_path = self.root / f"output_{chunk_id}.md"
        context = task_context.write_context(str(self.root), chunk_id)
        review = {
            "schema_version": 1,
            "source_hash": file_hash(source_path),
            "output_hash": file_hash(output_path),
            "task_context_hash": task_context.snapshot_hash(context),
            "review_policy_version": "academic-review-v1",
            "review_kind": kind,
            "coverage": {"chunk_ids": [chunk_id]},
            "findings": [],
        }
        path = self.root / "reviews" / f"{chunk_id}.{kind}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")

    def test_risk_sample_is_stable_and_covers_all_chunks(self):
        sources = ["A routine method description." for _ in range(11)] + ["We show the result." ]
        self._make_run(sources, sample_rate=0.1)
        manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
        plan = quality._review_plan("academic", "risk_based", manifest["chunks"], str(self.root), 0.1)
        again = quality._review_plan("academic", "risk_based", manifest["chunks"], str(self.root), 0.1)
        self.assertEqual(plan, again)
        self.assertEqual(len(plan), len(sources))
        self.assertEqual(sum("coordinator_review" in kinds for kinds in plan.values()), 3)
        self.assertEqual(sum("self_check" in kinds for kinds in plan.values()), 12)

    def test_quality_requires_each_chunk_check_and_risk_review(self):
        self._make_run(["A routine method description.", "We show the result."], sample_rate=0.1)
        self._write_review("chunk0001", "self_check")
        self._write_review("chunk0001", "coordinator_review")
        report = quality.build_quality_report(str(self.root))
        self.assertEqual(report["quality_status"], "needs_review")
        self.assertEqual(report["unreviewed_chunk_ids"], ["chunk0002"])

        self._write_review("chunk0002", "self_check")
        self._write_review("chunk0002", "coordinator_review")
        report = quality.build_quality_report(str(self.root))
        self.assertEqual(report["quality_status"], "validated")

    def test_quality_requires_fresh_input_structure_inspection(self):
        self._make_run(["A routine method description."], sample_rate=0.1)
        (self.root / "reviews" / "ingestion.json").unlink()
        report = quality.build_quality_report(str(self.root))
        self.assertEqual(report["input_review_status"], "missing")
        self.assertEqual(report["quality_status"], "needs_review")

    def test_stale_output_review_is_not_counted_as_valid(self):
        self._make_run(["We show the result."], sample_rate=0.1)
        self._write_review("chunk0001", "coordinator_review")
        (self.root / "output_chunk0001.md").write_text("We establish the result.", encoding="utf-8")
        report = quality.build_quality_report(str(self.root))
        self.assertEqual(report["quality_status"], "needs_review")
        self.assertEqual(report["unreviewed_chunk_ids"], ["chunk0001"])

    def test_full_review_mode_requires_coordinator_review_for_every_chunk(self):
        self._make_run(["A routine method.", "We show the result."], sample_rate=0.1, review_mode="full")
        self._write_review("chunk0001", "self_check")
        self._write_review("chunk0002", "coordinator_review")
        report = quality.build_quality_report(str(self.root))
        self.assertEqual(report["quality_status"], "needs_review")
        self.assertEqual(report["unreviewed_coordinator_review_chunk_ids"], ["chunk0001"])

    def test_record_review_computes_current_hashes_and_enforces_kind(self):
        self._make_run(["We show the result."], sample_rate=0.1)
        context = task_context.write_context(str(self.root), "chunk0001")
        findings = self.root / "drafts" / "chunk0001.review.json"
        findings.parent.mkdir(exist_ok=True)
        findings.write_text('{"findings": []}', encoding="utf-8")
        review_path = quality.record_review(str(self.root), "chunk0001", "coordinator_review", str(findings))
        review = json.loads(review_path.read_text(encoding="utf-8"))
        self.assertEqual(review["output_hash"], file_hash(self.root / "output_chunk0001.md"))
        self.assertEqual(review["task_context_hash"], task_context.snapshot_hash(context))
        quality.record_review(str(self.root), "chunk0001", "self_check", str(findings))
        self.assertEqual(quality.build_quality_report(str(self.root))["quality_status"], "validated")
        with self.assertRaisesRegex(ValueError, "requires self_check, coordinator_review"):
            quality.record_review(str(self.root), "chunk0001", "independent", str(findings))


if __name__ == "__main__":
    unittest.main()
