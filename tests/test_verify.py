import copy
import importlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

project_inventory = importlib.import_module("project_inventory")
verify = importlib.import_module("verify")


def run_git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, check=True, text=True, capture_output=True
    )
    return result.stdout.strip()


def initialize_git(root: Path, commit=True) -> None:
    run_git(root, "init", "-q")
    run_git(root, "config", "user.name", "Test User")
    run_git(root, "config", "user.email", "test@example.com")
    if commit:
        (root / "README.md").write_text("# Fixture\n", encoding="utf-8")
        run_git(root, "add", "README.md")
        run_git(root, "commit", "-qm", "initial")


class VerifyTests(unittest.TestCase):
    def test_verify_shell_passes_when_rg_is_unavailable(self):
        tool_directory = tempfile.TemporaryDirectory()
        self.addCleanup(tool_directory.cleanup)
        bin_root = Path(tool_directory.name)
        # 只暴露验证实际需要的命令，明确证明整个路径中不存在 rg。
        (bin_root / "python3").symlink_to(Path(sys.executable))
        for command in ("dirname", "git"):
            executable = shutil.which(command)
            self.assertIsNotNone(executable)
            (bin_root / command).symlink_to(Path(executable))
        self.assertIsNone(shutil.which("rg", path=str(bin_root)))
        environment = os.environ.copy()
        environment["PATH"] = str(bin_root)

        result = subprocess.run(
            ["/bin/bash", str(ROOT / "scripts" / "verify.sh")],
            cwd=ROOT,
            env=environment,
            check=False,
            text=True,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("验证通过", result.stdout)

    def test_root_locator_supports_standalone_and_embedded_modes(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        parent = Path(temporary.name)

        standalone = parent / "standalone" / "ai_constraints"
        standalone.mkdir(parents=True)
        initialize_git(standalone, commit=False)
        package, project, mode = verify.locate_roots(standalone)
        self.assertEqual(
            (package, project, mode),
            (standalone.resolve(), standalone.resolve(), "standalone"),
        )

        embedded_project = parent / "embedded"
        embedded_package = embedded_project / "ai_constraints"
        embedded_package.mkdir(parents=True)
        initialize_git(embedded_project, commit=False)
        package, project, mode = verify.locate_roots(embedded_package)
        self.assertEqual(
            (package, project, mode),
            (embedded_package.resolve(), embedded_project.resolve(), "embedded"),
        )

    def test_root_locator_rejects_non_git_wrong_name_and_wrong_position(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        non_git = root / "ai_constraints"
        non_git.mkdir()
        with self.assertRaisesRegex(verify.VerificationError, "不在 Git"):
            verify.locate_roots(non_git)

        project = root / "project"
        project.mkdir()
        initialize_git(project, commit=False)
        wrong_name = project / "toolkit"
        wrong_name.mkdir()
        with self.assertRaisesRegex(verify.VerificationError, "目录名"):
            verify.locate_roots(wrong_name)
        wrong_position = project / "tools" / "ai_constraints"
        wrong_position.mkdir(parents=True)
        with self.assertRaisesRegex(verify.VerificationError, "位置错误"):
            verify.locate_roots(wrong_position)

    def test_archive_placeholder_and_abnormal_markers_fail_validation(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        archive = Path(temporary.name) / "module.md"
        archive.write_text(
            "# Module\n"
            f"{project_inventory.GENERATED_START}\n"
            "## 目标与边界\n[项目目标]\n"
            "- 错误路径：`/Users/example/project/app.py`\n"
            "## 核心不变量与验证\nUNKNOWN\n"
            "## 公开契约与直接消费者\n无\n"
            "## 修改风险\n无\n"
            "## 验证命令\npython3 -m unittest\n"
            f"{project_inventory.GENERATED_END}\n"
            f"{project_inventory.MANUAL_START}\n人工\n{project_inventory.MANUAL_END}\n",
            encoding="utf-8",
        )
        errors = verify._validate_archive(archive, allow_placeholders=False)
        self.assertTrue(any("占位符" in error for error in errors))
        self.assertTrue(any("绝对路径" in error for error in errors))

        archive.write_text(
            archive.read_text(encoding="utf-8").replace(project_inventory.MANUAL_END, ""),
            encoding="utf-8",
        )
        errors = verify._validate_archive(archive, allow_placeholders=False)
        self.assertTrue(any("标记" in error for error in errors))

    def test_fake_commands_absolute_paths_and_duplicate_archives_fail(self):
        command_errors = verify._validate_commands(
            {
                "verification": {
                    "fast_commands": ["TODO"],
                    "full_commands": [],
                    "last_status": "not_run",
                }
            }
        )
        self.assertTrue(any("占位命令" in error for error in command_errors))
        self.assertTrue(any("真实命令" in error for error in command_errors))

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        initialize_git(root)
        scan = project_inventory.scan_project(root)
        base_module = {
            "id": "one",
            "roots": ["src"],
            "archive": "ai_constraints/codex/模块/one.md",
            "tests": [],
        }
        scan["modules"] = [base_module, {**base_module, "id": "two"}]
        with self.assertRaisesRegex(project_inventory.InventoryError, "档案重复"):
            project_inventory.validate_state(scan)

        absolute = copy.deepcopy(scan)
        absolute["modules"] = [{**base_module, "archive": "/tmp/absolute.md"}]
        with self.assertRaisesRegex(project_inventory.InventoryError, "安全相对路径"):
            project_inventory.validate_state(absolute)

    def test_tracked_ds_store_is_reported(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        initialize_git(root)
        (root / ".DS_Store").write_bytes(b"local metadata")
        run_git(root, "add", ".DS_Store")
        run_git(root, "commit", "-qm", "track metadata")

        errors = verify._validate_repository_hygiene(ROOT, root)

        self.assertTrue(any(".DS_Store 仍被 Git 跟踪" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
