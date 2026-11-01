import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PackageTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("npm"), "npm is required to inspect the package")
    def test_published_package_contains_the_hook_scripts_and_manifests(self):
        with tempfile.TemporaryDirectory() as cache:
            env = os.environ.copy()
            env["npm_config_cache"] = cache
            result = subprocess.run(
                [shutil.which("npm"), "pack", "--dry-run", "--ignore-scripts", "--json"],
                cwd=ROOT, env=env, text=True, encoding="utf-8", capture_output=True, check=True,
                shell=os.name == "nt",
            )
        packed = json.loads(result.stdout)
        package = packed[0] if isinstance(packed, list) else next(iter(packed.values()))
        files = {item["path"] for item in package["files"]}
        required = {
            ".codex-plugin/plugin.json", ".qoder-plugin/plugin.json",
            "hooks/codex.json", "hooks/qoder.json", "scripts/rename_hook.py", "README.md", "LICENSE",
        }
        self.assertTrue(required <= files, f"missing from package: {required - files}")
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        for platform in ("codex", "qoder"):
            manifest = json.loads((ROOT / f".{platform}-plugin/plugin.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["name"], package["name"])
            self.assertEqual(manifest["version"], package["version"])
            self.assertEqual(manifest["license"], package["license"])
            self.assertTrue(manifest["hooks"].startswith("./"))
            self.assertIn(manifest["hooks"][2:], files)
        interface = json.loads((ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))["interface"]
        for field in ("logo", "composerIcon"):
            self.assertTrue(interface[field].startswith("./"))
            self.assertIn(interface[field][2:], files)
        hooks = json.loads((ROOT / "hooks/qoder.json").read_text(encoding="utf-8"))["hooks"]
        self.assertIn("UserPromptSubmit", hooks)
        permission = hooks["PermissionRequest"][0]
        self.assertEqual(permission["matcher"], "Bash")
        self.assertIn("permit-qoder", permission["hooks"][0]["command"])


if __name__ == "__main__":
    unittest.main()
