import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import convert
import document_context
import run_state
import task_context
from manifest import create_manifest


class DocumentContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = (ROOT / "tests/fixtures/academic/context-book.md").read_text(encoding="utf-8")
        # input.md is normalized to LF by the academic importer on all OSes.
        (self.root / "input.md").write_bytes(self.source.encode("utf-8"))
        names = convert.split_markdown_structured(str(self.root / "input.md"), str(self.root), 100, academic=True)
        create_manifest(str(self.root), names, str(self.root / "input.md"))
        self.chunks = [Path(name).stem for name in names]
        self.mapping = json.loads((self.root / "document_map.json").read_text(encoding="utf-8"))
        self.index = document_context.prepare_context(self.root)
        self.facts = {group: [] for group in document_context.FACT_GROUPS}
        (self.root / "translation_spec.json").write_text(
            json.dumps({"schema_version": 1, "profile": "academic", "target_lang": "zh"}), encoding="utf-8"
        )

    def section(self, title):
        return next(s["section_id"] for s in self.index["sections"] if s["title"] == title)

    def block(self, excerpt):
        return next(b for b in self.mapping["blocks"] if excerpt in self.source[b["source_start"]:b["source_end"]])

    def fact(self, fact_id, key, excerpt, title=None):
        block = self.block(excerpt)
        start = self.source.index(excerpt, block["source_start"], block["source_end"])
        return {
            "id": fact_id, "key": key, "statement": excerpt, "confirmed": True,
            "scope": {"kind": "section", "section_id": self.section(title)} if title else {"kind": "document"},
            "evidence_block_ids": [block["block_id"]],
            "evidence": [{"block_id": block["block_id"], "source_span": [start, start + len(excerpt)],
                          "source_excerpt": excerpt}],
        }

    def commit(self, facts=None):
        path = self.root / "facts.json"
        path.write_text(json.dumps(self.facts if facts is None else facts), encoding="utf-8")
        return document_context.prepare_context(self.root, path)

    def install_facts(self):
        self.facts["definitions"] = [self.fact(
            "policy", "policy", "We define a policy as a mapping from histories to actions.", "Agent systems"
        )]
        self.facts["symbols"] = [
            self.fact("T-horizon", "T", "Let $T$ denote the execution horizon.", "Agent systems"),
            self.fact("T-statistic", "T", "Let $T$ denote a test statistic under the null hypothesis.", "Probability"),
        ]
        self.commit()

    def selection(self, excerpt):
        return document_context.select_for_chunk(self.root, self.block(excerpt)["chunk_id"])

    def test_scans_late_definitions_and_excludes_code_and_references(self):
        candidates = self.index["candidates"]
        self.assertTrue(any("completeness" in c["source_excerpt"] for c in candidates))
        self.assertFalse(any("payload" in c["source_excerpt"] for c in candidates))
        self.assertTrue(any(c["kind"] == "abbreviation" and "(LLM)" in c["source_excerpt"] for c in candidates))
        self.assertFalse(any(s["symbol"] == "Q" for s in self.index["symbol_occurrences"]))
        for candidate in candidates:
            a, b = candidate["source_span"]
            self.assertEqual(self.source[a:b], candidate["source_excerpt"])
        self.assertTrue(all(self.index[g] == [] for g in document_context.FACT_GROUPS))

    def test_section_ids_disambiguate_repeated_titles(self):
        self.source = self.source.replace("## Probability", "## Agent systems")
        (self.root / "input.md").write_bytes(self.source.encode("utf-8"))
        convert.split_markdown_structured(str(self.root / "input.md"), str(self.root), 100, academic=True)
        data = document_context.build_index(self.root)
        sections = [s for s in data["sections"] if s["title"] == "Agent systems"]
        self.assertEqual(len(sections), 2)
        self.assertNotEqual(sections[0]["section_id"], sections[1]["section_id"])

    def test_parent_definition_and_reused_symbol_are_scoped(self):
        self.install_facts()
        execution = self.selection("Increasing")
        ids = {item["fact"]["id"] for item in execution["facts"]}
        self.assertEqual(ids, {"policy", "T-horizon"})
        probability = self.selection("The distribution")
        self.assertEqual({item["fact"]["id"] for item in probability["facts"]}, {"T-statistic"})

    def test_same_key_in_scope_is_ambiguous_and_local_scope_shadows_document(self):
        self.install_facts()
        duplicate = copy.deepcopy(self.facts["symbols"][0])
        duplicate["id"] = "T-alternative"
        self.facts["symbols"].append(duplicate)
        self.commit()
        selected = self.selection("Increasing")
        self.assertTrue(any(a["key"] == "T" for a in selected["ambiguities"]))
        self.assertFalse(any(item["fact"]["key"] == "T" for item in selected["facts"]))
        duplicate["scope"] = {"kind": "document"}
        self.commit()
        selected = self.selection("Increasing")
        self.assertEqual(selected["ambiguities"], [])
        self.assertIn("T-horizon", {item["fact"]["id"] for item in selected["facts"]})

    def test_chunk_crossing_sections_keeps_block_specific_symbol_context(self):
        self.install_facts()
        # Re-chunk a fresh source run so one task crosses both sibling sections.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "input.md").write_bytes(self.source.encode("utf-8"))
            (root / "chunk0001.md").write_bytes(self.source.encode("utf-8"))
            convert._write_document_map(str(root), str(root / "input.md"), ["chunk0001.md"])
            (root / "facts.json").write_text(json.dumps(self.facts), encoding="utf-8")
            document_context.prepare_context(root, root / "facts.json")
            selected = document_context.select_for_chunk(root, "chunk0001", max_chars=20000)
            symbols = [item for item in selected["facts"] if item["kind"] == "symbols"]
            self.assertEqual({item["fact"]["id"] for item in symbols}, {"T-horizon", "T-statistic"})
            self.assertGreater(len({item["section_id"] for item in symbols}), 1)

    def test_prepare_preserves_confirmed_records_and_audit_notes_do_not_affect_prompt(self):
        self.install_facts()
        before = self.selection("Increasing")
        prepared = document_context.prepare_context(self.root)
        self.assertEqual(prepared["symbols"], self.facts["symbols"])
        self.facts["symbols"][0]["audit_notes"] = "Reviewed today"
        self.commit()
        self.assertEqual(before, self.selection("Increasing"))

    def test_unconfirmed_forged_or_wrong_scope_evidence_cannot_replace_good_context(self):
        self.install_facts()
        original = (self.root / "document_context.json").read_bytes()
        bad_facts = []
        for field, value in (("confirmed", False), ("id", ""), ("evidence_block_ids", ["block999999"])):
            bad = copy.deepcopy(self.facts)
            bad["symbols"][0][field] = value
            bad_facts.append(bad)
        bad = copy.deepcopy(self.facts)
        bad["symbols"][0]["evidence"][0]["source_excerpt"] = "invented statement"
        bad_facts.append(bad)
        bad = copy.deepcopy(self.facts)
        bad["symbols"][0]["scope"]["section_id"] = self.section("Probability")
        bad_facts.append(bad)
        bad = copy.deepcopy(self.facts)
        bad["symbols"].append(copy.deepcopy(bad["symbols"][0]))
        bad_facts.append(bad)
        for facts in bad_facts:
            with self.subTest(facts=facts), self.assertRaises(ValueError):
                self.commit(facts)
            self.assertEqual((self.root / "document_context.json").read_bytes(), original)

    def test_budget_overflow_is_explicit(self):
        self.install_facts()
        chunk_id = self.block("Increasing")["chunk_id"]
        with self.assertRaisesRegex(ValueError, "not silently truncated"):
            document_context.select_for_chunk(self.root, chunk_id, max_chars=100)

    def test_chinese_definition_matches_prose_and_uses_unicode_not_byte_offsets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = "## 中文定义\n\n定义：策略是历史到行动的映射。\n\n该策略只用于本节。\n"
            (root / "input.md").write_bytes(source.encode("utf-8"))
            convert.split_markdown_structured(str(root / "input.md"), str(root), 100, academic=True)
            data = document_context.prepare_context(root)
            mapping = json.loads((root / "document_map.json").read_text(encoding="utf-8"))
            excerpt = "定义：策略是历史到行动的映射。"
            start = source.index(excerpt)
            block = next(b for b in mapping["blocks"] if b["source_start"] == start)
            fact = {"id": "policy-zh", "key": "策略", "statement": excerpt, "confirmed": True,
                    "scope": {"kind": "section", "section_id": data["sections"][0]["section_id"]},
                    "evidence_block_ids": [block["block_id"]],
                    "evidence": [{"block_id": block["block_id"], "source_span": [start, start + len(excerpt)],
                                  "source_excerpt": excerpt}]}
            facts = {group: [] for group in document_context.FACT_GROUPS}
            facts["definitions"] = [fact]
            (root / "facts.json").write_text(json.dumps(facts), encoding="utf-8")
            document_context.prepare_context(root, root / "facts.json")
            chunk = next(b["chunk_id"] for b in mapping["blocks"] if "该策略" in source[b["source_start"]:b["source_end"]])
            selected = document_context.select_for_chunk(root, chunk)
            self.assertIn("policy-zh", {item["fact"]["id"] for item in selected["facts"]})
            self.assertNotEqual(start, len(source[:start].encode("utf-8")))

    def test_stale_source_map_or_edited_section_tree_is_rejected(self):
        original = (self.root / "document_context.json").read_bytes()
        bad = copy.deepcopy(self.index)
        bad["sections"][0]["title"] = "Invented title"
        (self.root / "document_context.json").write_text(json.dumps(bad), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "section tree"):
            document_context.load_context(self.root)
        (self.root / "document_context.json").write_bytes(original)
        mapping = copy.deepcopy(self.mapping)
        mapping["parser"] = "different parser"
        (self.root / "document_map.json").write_text(json.dumps(mapping), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "stale"):
            document_context.load_context(self.root)

    def test_legacy_tasks_remain_compatible_but_used_context_cannot_disappear(self):
        chunk_id = self.chunks[0]
        task_context.write_context(self.root, chunk_id)
        (self.root / "document_context.json").unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            task_context.build_context(self.root, chunk_id)
        (self.root / "tasks" / f"{chunk_id}.context.json").unlink()
        legacy = task_context.write_context(self.root, chunk_id)
        self.assertNotIn("document_context", legacy)
        self.assertEqual(task_context.stored_context_hash(self.root, chunk_id), task_context.snapshot_hash(legacy))

    def test_final_context_correction_replans_only_actual_dependent_chunks(self):
        self.install_facts()
        before = {}
        for chunk_id in self.chunks:
            (self.root / f"output_{chunk_id}.md").write_text(
                (self.root / f"{chunk_id}.md").read_text(encoding="utf-8"), encoding="utf-8"
            )
            before[chunk_id] = task_context.write_context(self.root, chunk_id)
        run_state.record_chunks(str(self.root), self.chunks)
        self.assertEqual(run_state.plan(str(self.root))["translation_chunk_ids"], [])
        self.facts["definitions"][0]["statement"] += " Here policy is a decision mapping."
        self.commit()
        affected = []
        for chunk_id in self.chunks:
            updated = task_context.write_context(self.root, chunk_id)
            if task_context.snapshot_hash(updated) != task_context.snapshot_hash(before[chunk_id]):
                affected.append(chunk_id)
        self.assertIn(self.block("Increasing")["chunk_id"], affected)
        self.assertNotIn(self.block("The distribution")["chunk_id"], affected)
        self.assertLess(len(affected), len(self.chunks))
        self.assertEqual(run_state.plan(str(self.root))["translation_chunk_ids"], affected)


if __name__ == "__main__":
    unittest.main()
