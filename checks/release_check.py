"""验证发布标签输入边界和目标源码的版本一致性,不构建或访问网络."""

import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "validate_serial_tool_release.py"
PARSE_TAG_VERSION = runpy.run_path(str(SCRIPT))["parse_tag_version"]


class ReleaseChecks(unittest.TestCase):
    def test_tag_prefix_accepts_both_cases(self):
        for tag in ("v1.2.6", "V1.2.6"):
            with self.subTest(tag=tag):
                self.assertEqual(PARSE_TAG_VERSION(tag), "1.2.6")

    def test_invalid_tag_formats_are_rejected(self):
        for tag in ("1.2.6", "vv1.2.6", "v1.2", "V1.2.6.0", "v1.2.6-rc1",
                    "v1.2.6\n", " V1.2.6", "refs/tags/V1.2.6", "v１.2.6"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                PARSE_TAG_VERSION(tag)

    def run_validator(self, tag: str, readme_version: str, tool_version: str):
        """以临时源码元数据运行真实发布 CLI,不读取或覆盖用户配置."""
        with tempfile.TemporaryDirectory(prefix="serial-release-check-") as directory:
            target = Path(directory)
            readme = target / "README.md"
            version = target / "version_info.py"
            readme.write_text(f"版本 {readme_version}\n", encoding="utf-8")
            version.write_text(f'TOOL_VERSION = "{tool_version}"\n', encoding="utf-8")
            return subprocess.run(
                [sys.executable, "-B", str(SCRIPT), tag, str(readme), str(version)],
                capture_output=True, text=True, encoding="utf-8", timeout=10,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )

    def test_cli_outputs_same_version_for_both_prefixes(self):
        for tag in ("v1.2.6", "V1.2.6"):
            with self.subTest(tag=tag):
                result = self.run_validator(tag, "1.2.6", "1.2.6")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines(), [
                    "tag_version=1.2.6", "readme_version=1.2.6", "tool_version=1.2.6",
                ])

    def test_cli_rejects_wrong_target_metadata(self):
        for readme_version, tool_version in (("1.2.5", "1.2.6"), ("1.2.6", "1.2.5")):
            with self.subTest(readme=readme_version, tool=tool_version):
                result = self.run_validator("V1.2.6", readme_version, tool_version)
                self.assertEqual(result.returncode, 1)
                self.assertIn("版本不一致", result.stderr)
                self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
