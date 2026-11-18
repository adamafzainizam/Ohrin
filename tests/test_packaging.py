import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"


class PackagingTests(unittest.TestCase):
    def test_spec_bundles_web_and_uses_entry(self):
        spec = (PACKAGING / "ohrin.spec").read_text(encoding="utf-8")
        compile(spec, "ohrin.spec", "exec")
        self.assertIn("ohrin_entry.py", spec)
        self.assertRegex(spec, r'name="Ohrin"')
        self.assertRegex(spec, r'"web"')
        for excluded in ("experiments", "scripts", "tkinter"):
            self.assertIn(f'"{excluded}"', spec)
        self.assertIn("OHRIN_FREEZE_GTK", spec)
        self.assertIn("BUNDLE", spec)
        self.assertNotIn("icon=", spec)

    def test_entry_calls_app_main(self):
        entry = (PACKAGING / "ohrin_entry.py").read_text(encoding="utf-8")
        self.assertIn("from ohrin.app import main", entry)
        self.assertRegex(entry, r"sys\.exit\(main\(\)\)")
        compile(entry, "ohrin_entry.py", "exec")

    def test_gitignore_covers_build_outputs(self):
        for p in ("dist/x", "build/x", "foo.spec"):
            try:
                rc = subprocess.run(["git", "check-ignore", "-q", p], cwd=ROOT).returncode
            except OSError:
                self.skipTest("git not available")
            self.assertEqual(rc, 0, p)
        rc = subprocess.run(["git", "check-ignore", "-q", "packaging/ohrin.spec"], cwd=ROOT).returncode
        self.assertEqual(rc, 1, "the tracked spec must not be ignored")

    def test_requirements_desktop_pins_majors(self):
        lines = (ROOT / "requirements-desktop.txt").read_text(encoding="utf-8").splitlines()
        lines = [l.strip() for l in lines if l.strip() and not l.startswith("#")]
        self.assertIn("-r requirements-ml.txt", lines)
        for name, upper in (("pywebview", "<7"), ("pyinstaller", "<7")):
            match = [l for l in lines if l.lower().startswith(name)]
            self.assertEqual(len(match), 1, name)
            self.assertIn(upper, match[0])
            self.assertRegex(match[0], r">=\d")

    def test_build_workflow_is_read_only_and_has_no_secrets(self):
        text = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
        self.assertIn("contents: read", text)
        self.assertNotIn("secrets.", text)
        self.assertNotRegex(text, r"(?i)release")
        self.assertNotIn("contents: write", text)
        self.assertIn("persist-credentials: false", text)

    def test_build_workflow_runs_self_test_on_every_os(self):
        text = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
        for os_name in ("ubuntu-24.04", "windows-latest", "macos-14"):
            self.assertIn(os_name, text)
        self.assertIn("dist/Ohrin/Ohrin --self-test --self-test-report st.json", text)
        self.assertIn("'--self-test-report'", text)
        self.assertRegex(text, r"Self-test \(Linux and macOS\)")
        self.assertIn("Start-Process", text)
        self.assertIn("-Wait -PassThru", text)
        self.assertIn("st.json", text)


if __name__ == "__main__":
    unittest.main()
