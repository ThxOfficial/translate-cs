import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import glossary  # noqa: E402
import task_context  # noqa: E402


class FirstOccurrencePlanTests(unittest.TestCase):
    def test_first_visible_prose_occurrence_ignores_code_and_is_cached_by_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "chunk0001.md").write_text(
                '```python\nprint("attention")\n```\n', encoding="utf-8"
            )
            (root / "chunk0002.md").write_text("Attention is a model mechanism.\n", encoding="utf-8")
            (root / "chunk0003.md").write_text("Attention appears again.\n", encoding="utf-8")
            (root / "translation_spec.json").write_text(
                json.dumps({"schema_version": 1, "profile": "academic", "target_lang": "zh"}),
                encoding="utf-8",
            )
            glossary.save_glossary(str(root / "glossary.json"), {
                "version": 2,
                "terms": [{
                    "id": "attention", "source": "attention", "target": "注意力机制",
                    "category": "ai", "aliases": [], "gender": "unknown",
                    "confidence": "high", "frequency": 2, "evidence_refs": [], "notes": "Model mechanism.",
                }],
                "high_frequency_top_n": 20,
                "applied_meta_hashes": {},
            })

            plan = task_context.prepare_first_occurrence_plan(tmp)
            self.assertEqual(plan["first_occurrences"]["attention"]["chunk_id"], "chunk0002")
            context = task_context.write_context(tmp, "chunk0002")
            first_context_hash = task_context.snapshot_hash(context)
            self.assertEqual(context["first_occurrence_terms"][0]["surface"], "Attention")

            changed = glossary.load_glossary(str(root / "glossary.json"))
            changed["terms"][0]["notes"] = "Ordinary attention is not the model mechanism."
            glossary.save_glossary(str(root / "glossary.json"), changed)
            refreshed = task_context.prepare_first_occurrence_plan(tmp)
            self.assertEqual(plan["term_hashes"], refreshed["term_hashes"])
            refreshed_context = task_context.write_context(tmp, "chunk0002")
            self.assertNotEqual(first_context_hash, task_context.snapshot_hash(refreshed_context))


if __name__ == "__main__":
    unittest.main()
