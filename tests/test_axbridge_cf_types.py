"""Source regression for the macOS AXUIElement Swift CoreFoundation cast issue.

This test catches the Swift compiler diagnostic reported during the one-command
setup. Native type-check and runtime permission verification require macOS.
"""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src-tauri" / "resources" / "axbridge.swift"


class AccessibilityBridgeSourceTests(unittest.TestCase):
    def test_corefoundation_elements_checked_by_type_id(self):
        content = SOURCE.read_text(encoding="utf-8")
        self.assertIn("CFGetTypeID(object) == AXUIElementGetTypeID()", content)
        self.assertIn("func elementAttribute(", content)
        self.assertNotIn("as? AXUIElement", content)
        self.assertIn("elementAttribute(app, kAXFocusedWindowAttribute", content)
        self.assertIn("elementAttribute(app, kAXMenuBarAttribute", content)

    @unittest.skipUnless(shutil.which("swiftc"), "Swift compiler not installed")
    def test_swift_source_parses(self):
        subprocess.run(["swiftc", "-frontend", "-parse", str(SOURCE)], check=True)


if __name__ == "__main__":
    unittest.main()
