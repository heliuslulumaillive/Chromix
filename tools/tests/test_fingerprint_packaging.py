"""Exercise real staging/inventory guards with explicitly synthetic tiny payloads."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PWSH = shutil.which("pwsh")


@unittest.skipUnless(PWSH, "PowerShell 7 is required for Windows packaging")
class FingerprintPackagingTest(unittest.TestCase):
    def test_capabilities_are_staged_before_inventory_and_cannot_change_during_validation(self):
        for fingerprint in (False, True):
            with self.subTest(fingerprint=fingerprint), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, work, dest = root / "repo", root / "work", root / "dist"
                out = work / "src/out/Default"
                out.mkdir(parents=True)
                for relative in ("build/windows/package-win.ps1", "tools/write_google_services_build_record.py"):
                    target = repo / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(ROOT / relative, target)
                (repo / "LICENSE").write_text("Synthetic packaging test license\n")
                (work / "src/LICENSE").write_text("Synthetic Chromium input fixture\n")
                source = {
                    "variant": "google-services-self-use", "chromium_version": "155.0.8059.39",
                    "patch_sha256": "1" * 64, "preparation_sha256": "2" * 64,
                }
                if fingerprint:
                    source["build_variant"] = "fingerprint-modes-win155"
                (work / "google-services-source.json").write_text(json.dumps(source))
                capability_bytes = (ROOT / "build/fingerprint-modes-capabilities.json").read_bytes()
                (repo / "build/fingerprint-modes-capabilities.json").write_bytes(capability_bytes)
                for name in (
                        "chrome.exe", "chrome_elf.dll", "chrome_100_percent.pak",
                        "chrome_200_percent.pak", "resources.pak", "icudtl.dat",
                        "libEGL.dll", "libGLESv2.dll", "v8_context_snapshot.bin",
                        "chrome.exe.manifest"):
                    (out / name).write_bytes(b"synthetic packaging fixture - not an executable")
                script = (repo / "build/windows/package-win.ps1").read_text()
                # Only exercise the packaging contract, not PE/Chromium execution.
                markers = re.findall(r'"([^"\r\n]*(?:uxr-webgl-|Google Inc\.|ANGLE \()[^"\r\n]*)"', script)
                (out / "chrome.dll").write_bytes("\n".join(markers).encode())
                (out / "locales").mkdir()
                (out / "locales/en-US.pak").write_bytes(b"synthetic locale")
                (out / "args.gn").write_text("is_debug = false\n")
                environment = {**os.environ, "GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "42",
                               "GITHUB_REPOSITORY": "fixture/packaging", "GITHUB_RUN_ATTEMPT": "1"}

                def invoke(*options):
                    return subprocess.run(
                        [PWSH, "-NoProfile", "-NonInteractive", "-File",
                         str(repo / "build/windows/package-win.ps1"),
                         "-Out", str(out), "-Dest", str(dest), "-Arch", "x64", *options],
                        env=environment, capture_output=True, text=True, encoding="utf-8",
                        timeout=45)

                stage = invoke("-SkipArchive")
                self.assertEqual(stage.returncode, 0, stage.stdout + stage.stderr)
                bundle = dest / "chromix"
                record_path = bundle / "google-services-build.json"
                record = json.loads(record_path.read_text())
                receipt_hash = hashlib.sha256(record_path.read_bytes()).hexdigest()
                name = "fingerprint-modes-capabilities.json"
                self.assertEqual((bundle / name).exists(), fingerprint)
                self.assertEqual(name in record["files"], fingerprint)
                if fingerprint:
                    self.assertEqual((bundle / name).read_bytes(), capability_bytes)
                    self.assertEqual(record["files"][name], hashlib.sha256(capability_bytes).hexdigest())
                    (bundle / name).write_bytes(capability_bytes + b"\n")
                    changed = invoke("-ArchiveOnly", "-ExpectedBuildRecordSHA256", receipt_hash)
                    self.assertNotEqual(changed.returncode, 0)
                    self.assertFalse((dest / "chromix-win-x64.zip").exists())
                    (bundle / name).write_bytes(capability_bytes)
                archive = invoke("-ArchiveOnly", "-ExpectedBuildRecordSHA256", receipt_hash)
                self.assertEqual(archive.returncode, 0, archive.stdout + archive.stderr)
                self.assertTrue((dest / "chromix-win-x64.zip").is_file())
                self.assertEqual(hashlib.sha256(record_path.read_bytes()).hexdigest(), receipt_hash)


if __name__ == "__main__":
    unittest.main()
