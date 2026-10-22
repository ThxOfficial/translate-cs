"""Opt-in real Pandoc check; run with Pandoc on PATH, no third-party Python deps."""

import contextlib
import io
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import merge_and_build


class RealAcademicHtmlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.artifacts = ROOT / "tests" / ".artifacts" / "academic-rendering"
        cls.artifacts.mkdir(parents=True, exist_ok=True)
        version = subprocess.run(["pandoc", "--version"], check=True, capture_output=True, text=True).stdout
        (cls.artifacts / "engine.txt").write_text(version, encoding="utf-8")

    def test_supported_math_becomes_mathml_without_losing_code(self):
        output = self.artifacts / "supported.html"
        source = ROOT / "tests" / "fixtures" / "academic" / "math-rendering.md"
        self.assertTrue(merge_and_build.convert_with_pandoc(
            str(source), str(output), "Math fixture", "en", academic=True
        ))
        html = output.read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"<math\b", html)), 5)
        for element in ("mfrac", "msqrt", "mtable", "msub", "msubsup"):
            self.assertRegex(html, rf"<{element}\b")
        self.assertIn("\u03b8", html)
        self.assertIn(r"\unknown_macro{x}", html)

    def test_unsupported_macro_is_a_failed_render(self):
        source = self.artifacts / "unsupported.md"
        source.write_text(r"Unknown $\notAnAcademicMacro{x}$.", encoding="utf-8")
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            ok = merge_and_build.convert_with_pandoc(
                str(source), str(self.artifacts / "unsupported.html"), "Unsupported", "en", academic=True
            )
        (self.artifacts / "unsupported.log").write_text(log.getvalue(), encoding="utf-8")
        self.assertFalse(ok)


if __name__ == "__main__":
    result = unittest.main(exit=False)
    report = {"tests": result.result.testsRun, "successful": result.result.wasSuccessful()}
    (RealAcademicHtmlTests.artifacts / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    sys.exit(0 if report["successful"] else 1)
