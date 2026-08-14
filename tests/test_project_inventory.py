import copy
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "project_inventory.py"
FIXTURES = ROOT / "tests" / "fixtures"


def load_inventory_module():
    spec = importlib.util.spec_from_file_location("project_inventory", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, check=True, text=True, capture_output=True
    )
    return result.stdout.strip()


def initialize_git(root: Path) -> None:
    run_git(root, "init", "-q")
    run_git(root, "config", "user.name", "Test User")
    run_git(root, "config", "user.email", "test@example.com")


def complete_state(inventory, scan, modules, contracts=None, commands=None):
    """测试只补充明确 fixture 事实，不让机械扫描器猜模块语义。"""

    state = copy.deepcopy(scan)
    normalized_modules = copy.deepcopy(modules)
    for module in normalized_modules:
        module["roots"] = sorted(set(module["roots"]))
        module["tests"] = sorted(set(module.get("tests", [])))
    state["modules"] = sorted(normalized_modules, key=lambda module: module["id"])
    normalized_contracts = copy.deepcopy(contracts or [])
    for contract in normalized_contracts:
        contract["consumers"] = sorted(set(contract["consumers"]))
    state["contracts"] = sorted(normalized_contracts, key=lambda contract: contract["path"])
    verification_commands = commands or ["python3 -m unittest discover -s tests -v"]
    state["verification"] = {
        "fast_commands": verification_commands,
        "full_commands": verification_commands,
        "last_status": "passed",
    }
    inventory.validate_state(state)
    return state


class ProjectInventoryTests(unittest.TestCase):
    def setUp(self):
        self.inventory = load_inventory_module()

    def make_project(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        initialize_git(root)
        (root / "src").mkdir()
        (root / "src" / "app.py").write_text(
            "def hello():\n    return 'world'\n", encoding="utf-8"
        )
        (root / "README.md").write_text("# Fixture\n", encoding="utf-8")
        run_git(root, "add", ".")
        run_git(root, "commit", "-qm", "initial")
        return root

    def test_scan_includes_first_party_text_and_excludes_secrets_and_dependencies(self):
        root = self.make_project()
        (root / "src" / "untracked.py").write_text("VALUE = 1\n", encoding="utf-8")
        (root / ".env.local").write_text("API_TOKEN=do-not-read\n", encoding="utf-8")
        (root / "node_modules" / "pkg").mkdir(parents=True)
        (root / "node_modules" / "pkg" / "index.js").write_text(
            "secret dependency\n", encoding="utf-8"
        )
        (root / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")

        first = self.inventory.scan_project(root)
        second = self.inventory.scan_project(root)

        self.assertEqual(first, second)
        self.assertEqual(
            first["eligible_files"],
            ["README.md", "src/app.py", "src/untracked.py"],
        )
        self.assertEqual(first["eligible_file_count"], 3)
        self.assertEqual(first["eligible_file_sources"]["README.md"], "tracked")
        self.assertEqual(first["eligible_file_sources"]["src/untracked.py"], "untracked")
        self.assertIn(
            ".env.local", {change["path"] for change in first["worktree_changes"]}
        )
        self.assertEqual(first["indexed_commit"], run_git(root, "rev-parse", "HEAD"))
        self.assertGreaterEqual(first["excluded_file_count_by_reason"]["secret"], 1)
        self.assertGreaterEqual(first["excluded_file_count_by_reason"]["dependency"], 1)
        self.assertGreaterEqual(first["excluded_file_count_by_reason"]["binary"], 1)
        serialized = json.dumps(first, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("do-not-read", serialized)
        self.assertNotIn(str(root), serialized)

    def test_scan_excludes_common_token_auth_and_cloud_credential_paths(self):
        root = self.make_project()
        secret_paths = (
            ".netrc",
            "api.token",
            "token.txt",
            "auth.json",
            ".aws/config",
            ".azure/accessTokens.json",
            ".config/gcloud/application_default_credentials.json",
            ".docker/config.json",
            ".kube/config",
            ".config/gh/hosts.yml",
            ".authinfo",
            ".envrc",
            "deploy/service-account.json",
            "ops/kubeconfig",
            "nested/.docker/config.json",
            "nested/.KUBE/Config",
            "deep/nested/.Config/GH/HOSTS.YML",
            "ops/kubeconfig.yaml",
            "deploy/service_account.json",
            "deploy/SERVICE-ACCOUNT.yaml",
            "config/app.credential.toml",
            "config/app_credentials.yaml",
            "config/deploy-token.txt",
            "config/client.auth.json",
            "config/runtime-secret.yml",
        )
        sentinels = {}
        for index, relative in enumerate(secret_paths):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            sentinel = f"SECOND_REVIEW_SECRET_SENTINEL_{index}_MUST_NOT_BE_READ"
            sentinels[relative] = sentinel
            path.write_text(sentinel + "\n", encoding="utf-8")

        result = self.inventory.scan_project(root)

        excluded = {
            item["path"]: item["reason"] for item in result["excluded_files"]
        }
        serialized = json.dumps(result, ensure_ascii=False, sort_keys=True)
        output = self.inventory.stable_json_bytes(result)
        fingerprints = set(result["eligible_file_fingerprints"].values())
        for relative, sentinel in sentinels.items():
            with self.subTest(path=relative):
                sentinel_digest = hashlib.sha256(
                    (sentinel + "\n").encode("utf-8")
                ).hexdigest()
                self.assertEqual(excluded.get(relative), "secret")
                self.assertNotIn(relative, result["eligible_files"])
                self.assertNotIn(relative, result["eligible_file_fingerprints"])
                self.assertNotIn(sentinel, serialized)
                self.assertNotIn(sentinel_digest, fingerprints)
                self.assertNotIn(sentinel_digest, serialized)
                self.assertNotIn(sentinel.encode("utf-8"), output)
                self.assertNotIn(sentinel_digest.encode("ascii"), output)

    def test_scan_cli_is_byte_stable_and_non_git_project_fails(self):
        root = self.make_project()
        output_directory = tempfile.TemporaryDirectory()
        self.addCleanup(output_directory.cleanup)
        first = Path(output_directory.name) / "first.json"
        second = Path(output_directory.name) / "second.json"
        for output in (first, second):
            subprocess.run(
                [
                    "python3",
                    str(MODULE_PATH),
                    "scan",
                    "--project-root",
                    str(root),
                    "--output",
                    str(output),
                ],
                check=True,
                text=True,
                capture_output=True,
            )
        self.assertEqual(first.read_bytes(), second.read_bytes())

        not_git = Path(output_directory.name) / "not-git"
        not_git.mkdir()
        result = subprocess.run(
            [
                "python3",
                str(MODULE_PATH),
                "scan",
                "--project-root",
                str(not_git),
                "--output",
                str(first),
            ],
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("目标不是 Git 项目", result.stderr)

    def test_package_location_contract_rejects_wrong_name_or_position(self):
        root = self.make_project()
        package = root / "ai_constraints"
        package.mkdir()
        self.assertEqual(
            self.inventory.validate_package_location(root, package), "embedded"
        )
        wrong = root / "tools" / "ai_constraints"
        wrong.mkdir(parents=True)
        with self.assertRaisesRegex(self.inventory.InventoryError, "工作包必须位于"):
            self.inventory.validate_package_location(root, wrong)

    def test_python_fixture_declares_python_3_9_support(self):
        pyproject = (
            FIXTURES / "minimal-python-project" / "pyproject.toml"
        ).read_text(encoding="utf-8")
        self.assertIn('requires-python = ">=3.9"', pyproject)

    def test_private_change_maps_to_one_module_and_exact_tests(self):
        root = self.make_project()
        (root / "tests").mkdir()
        (root / "tests" / "test_app.py").write_text("# exact test\n", encoding="utf-8")
        run_git(root, "add", "tests/test_app.py")
        run_git(root, "commit", "-qm", "add exact test")
        scan = self.inventory.scan_project(root)
        state = complete_state(
            self.inventory,
            scan,
            [
                {
                    "id": "application",
                    "roots": ["src"],
                    "archive": "ai_constraints/codex/模块/application.md",
                    "tests": ["tests/test_app.py"],
                }
            ],
        )
        (root / "src" / "app.py").write_text(
            "def hello():\n    return 'updated'\n", encoding="utf-8"
        )

        result = self.inventory.analyze_changes(root, state)

        self.assertEqual(result["mode"], "incremental")
        self.assertEqual(result["risk_level"], "R1")
        self.assertEqual(result["impacted_modules"], ["application"])
        self.assertEqual(result["test_candidates"], ["tests/test_app.py"])

    def make_contract_project(self):
        root = self.make_project()
        (root / "core").mkdir()
        (root / "client").mkdir()
        (root / "tests").mkdir()
        (root / "core" / "schema.py").write_text("FIELDS = ('id',)\n", encoding="utf-8")
        (root / "client" / "consumer.py").write_text(
            "from core.schema import FIELDS\n", encoding="utf-8"
        )
        (root / "tests" / "test_contract.py").write_text("# contract\n", encoding="utf-8")
        run_git(root, "add", ".")
        run_git(root, "commit", "-qm", "add contract")
        scan = self.inventory.scan_project(root)
        modules = [
            {
                "id": "core",
                "roots": ["core"],
                "archive": "ai_constraints/codex/模块/core.md",
                "tests": ["tests/test_contract.py"],
            },
            {
                "id": "client",
                "roots": ["client"],
                "archive": "ai_constraints/codex/模块/client.md",
                "tests": ["tests/test_contract.py"],
            },
        ]
        return root, scan, modules

    def test_schema_contract_expands_consumers_and_requires_full_rescan(self):
        root, scan, modules = self.make_contract_project()
        state = complete_state(
            self.inventory,
            scan,
            modules,
            [{"path": "core/schema.py", "consumers": ["client/consumer.py"]}],
        )
        (root / "core" / "schema.py").write_text(
            "FIELDS = ('id', 'name')\n", encoding="utf-8"
        )

        result = self.inventory.analyze_changes(root, state)

        self.assertEqual(result["risk_level"], "R3")
        self.assertEqual(result["mode"], "full-rescan")
        self.assertEqual(result["consumer_files"], ["client/consumer.py"])
        self.assertEqual(result["impacted_modules"], ["client", "core"])

    def test_contract_consumer_requires_exactly_one_module_mapping(self):
        for mapping in ("zero", "multiple"):
            with self.subTest(mapping=mapping):
                root, scan, modules = self.make_contract_project()
                if mapping == "zero":
                    mapped_modules = [modules[0]]
                else:
                    mapped_modules = modules + [
                        {
                            "id": "client-overlap",
                            "roots": ["client/consumer.py"],
                            "archive": "ai_constraints/codex/模块/client-overlap.md",
                            "tests": ["tests/test_contract.py"],
                        }
                    ]
                state = complete_state(
                    self.inventory,
                    scan,
                    mapped_modules,
                    [{"path": "core/schema.py", "consumers": ["client/consumer.py"]}],
                )
                (root / "core" / "schema.py").write_text(
                    "FIELDS = ('id', 'changed')\n", encoding="utf-8"
                )

                result = self.inventory.analyze_changes(root, state)

                self.assertEqual(
                    (result["mode"], result["risk_level"]),
                    ("full-rescan", "R3"),
                )
                self.assertTrue(
                    any("消费者无法唯一映射" in reason for reason in result["reasons"]),
                    result["reasons"],
                )

    def test_unknown_consumer_or_unmapped_change_requires_full_rescan(self):
        root, scan, modules = self.make_contract_project()
        unknown_state = complete_state(
            self.inventory,
            scan,
            modules,
            [{"path": "core/schema.py", "consumers": ["UNKNOWN"]}],
        )
        (root / "core" / "schema.py").write_text("FIELDS = ()\n", encoding="utf-8")
        unknown = self.inventory.analyze_changes(root, unknown_state)
        self.assertEqual((unknown["mode"], unknown["risk_level"]), ("full-rescan", "R3"))

        (root / "core" / "schema.py").write_text("FIELDS = ('id',)\n", encoding="utf-8")
        (root / "new_top_level.py").write_text("VALUE = 1\n", encoding="utf-8")
        unmapped = self.inventory.analyze_changes(root, unknown_state)
        self.assertEqual((unmapped["mode"], unmapped["risk_level"]), ("full-rescan", "R3"))

    def test_documented_structural_changes_require_full_rescan(self):
        structural_paths = (
            "src/schema.py",
            "src/shared_registry.py",
            "pyproject.toml",
            "pytest.ini",
            ".github/workflows/ci.yml",
            "schemas/user.proto",
            "src/registry/index.py",
            "Jenkinsfile",
            ".travis.yml",
            "build.rs",
            "playwright.config.ts",
            "proto/user.proto",
            "api/v1/user.proto",
            "src/shared-registries/index.py",
            "src/shared-registry/index.py",
            "src/registries/index.py",
            "ci/Jenkinsfile.deploy",
            "build/ci/check.py",
            "playwright.config.tsx",
            "playwright.config.jsx",
            "playwright.config.mts",
            "playwright.config.cts",
        )
        for relative in structural_paths:
            with self.subTest(path=relative):
                root = self.make_project()
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("version = 1\n", encoding="utf-8")
                run_git(root, "add", relative)
                run_git(root, "commit", "-qm", f"add {relative}")
                state = complete_state(
                    self.inventory,
                    self.inventory.scan_project(root),
                    [
                        {
                            "id": "structural",
                            "roots": [relative],
                            "archive": "ai_constraints/codex/模块/structural.md",
                            "tests": [],
                        }
                    ],
                )
                path.write_text("version = 2\n", encoding="utf-8")

                result = self.inventory.analyze_changes(root, state)

                self.assertEqual(
                    (result["mode"], result["risk_level"]),
                    ("full-rescan", "R3"),
                )
                self.assertTrue(
                    any("结构变化" in reason for reason in result["reasons"]),
                    result["reasons"],
                )

    def test_committed_excluded_ci_change_requires_full_rescan(self):
        root = self.make_project()
        ci_path = root / "build" / "ci" / "check.py"
        ci_path.parent.mkdir(parents=True)
        baseline_sentinel = "BASELINE_EXCLUDED_CI_SENTINEL"
        changed_sentinel = "CHANGED_EXCLUDED_CI_SENTINEL"
        ci_path.write_text(baseline_sentinel + "\n", encoding="utf-8")
        run_git(root, "add", "-f", "build/ci/check.py")
        run_git(root, "commit", "-qm", "add excluded CI entry")
        baseline_scan = self.inventory.scan_project(root)
        self.assertNotIn("build/ci/check.py", baseline_scan["eligible_file_fingerprints"])
        state = complete_state(
            self.inventory,
            baseline_scan,
            [
                {
                    "id": "application",
                    "roots": ["src"],
                    "archive": "ai_constraints/codex/模块/application.md",
                    "tests": [],
                }
            ],
        )

        ci_path.write_text(changed_sentinel + "\n", encoding="utf-8")
        run_git(root, "add", "-f", "build/ci/check.py")
        run_git(root, "commit", "-qm", "change excluded CI entry")
        self.assertEqual(run_git(root, "status", "--porcelain"), "")

        result = self.inventory.analyze_changes(root, state)

        self.assertEqual((result["mode"], result["risk_level"]), ("full-rescan", "R3"))
        self.assertEqual(result["changed_files"], ["build/ci/check.py"])
        self.assertTrue(any("CI 入口" in reason for reason in result["reasons"]))
        serialized_outputs = json.dumps(
            {"state": state, "result": result}, ensure_ascii=False, sort_keys=True
        )
        for sentinel in (baseline_sentinel, changed_sentinel):
            self.assertNotIn(sentinel, serialized_outputs)
            self.assertNotIn(
                hashlib.sha256((sentinel + "\n").encode("utf-8")).hexdigest(),
                serialized_outputs,
            )

    def test_non_ancestor_baseline_requires_full_rescan(self):
        root = self.make_project()
        state = complete_state(
            self.inventory,
            self.inventory.scan_project(root),
            [
                {
                    "id": "application",
                    "roots": ["src"],
                    "archive": "ai_constraints/codex/模块/application.md",
                    "tests": [],
                }
            ],
        )
        state["indexed_commit"] = "0" * 40

        result = self.inventory.analyze_changes(root, state)

        self.assertEqual(result["status"], "full_rescan_required")
        self.assertIn("祖先", result["reasons"][0])

    def test_excluded_worktree_only_content_change_requires_full_rescan(self):
        root = self.make_project()
        secret_path = root / ".env.local"
        baseline_secret = "BASELINE_WORKTREE_SECRET_SENTINEL"
        changed_secret = "CHANGED_WORKTREE_SECRET_SENTINEL_WITH_DIFFERENT_SIZE"
        secret_path.write_text(baseline_secret + "\n", encoding="utf-8")
        baseline_scan = self.inventory.scan_project(root)
        state = complete_state(
            self.inventory,
            baseline_scan,
            [
                {
                    "id": "application",
                    "roots": ["src"],
                    "archive": "ai_constraints/codex/模块/application.md",
                    "tests": [],
                }
            ],
        )
        baseline_status = [
            change for change in baseline_scan["worktree_changes"] if change["path"] == ".env.local"
        ]
        secret_path.write_text(changed_secret + "\n", encoding="utf-8")
        current_scan = self.inventory.scan_project(root)
        current_status = [
            change for change in current_scan["worktree_changes"] if change["path"] == ".env.local"
        ]

        self.assertEqual(baseline_status, [{"path": ".env.local", "status": "??"}])
        self.assertEqual(current_status, baseline_status)
        result = self.inventory.analyze_changes(root, state)

        self.assertEqual((result["mode"], result["risk_level"]), ("full-rescan", "R3"))
        self.assertEqual(result["changed_files"], [".env.local"])
        self.assertTrue(any("排除文件元数据" in reason for reason in result["reasons"]))
        serialized_outputs = (
            json.dumps(state, ensure_ascii=False, sort_keys=True)
            + json.dumps(current_scan, ensure_ascii=False, sort_keys=True)
            + json.dumps(result, ensure_ascii=False, sort_keys=True)
        )
        for secret in (baseline_secret, changed_secret):
            secret_digest = hashlib.sha256((secret + "\n").encode("utf-8")).hexdigest()
            self.assertNotIn(secret, serialized_outputs)
            self.assertNotIn(secret_digest, serialized_outputs)

    def test_legacy_state_without_excluded_metadata_fails_closed_to_full_rescan(self):
        root = self.make_project()
        state = complete_state(
            self.inventory,
            self.inventory.scan_project(root),
            [
                {
                    "id": "application",
                    "roots": ["src"],
                    "archive": "ai_constraints/codex/模块/application.md",
                    "tests": [],
                }
            ],
        )
        state.pop("excluded_file_metadata")
        self.inventory.validate_state(state)

        result = self.inventory.analyze_changes(root, state)

        self.assertEqual((result["mode"], result["risk_level"]), ("full-rescan", "R3"))
        self.assertTrue(any("旧状态" in reason for reason in result["reasons"]))

    def test_no_change_does_not_rewrite_state(self):
        root = self.make_project()
        state = complete_state(
            self.inventory,
            self.inventory.scan_project(root),
            [
                {
                    "id": "application",
                    "roots": ["src"],
                    "archive": "ai_constraints/codex/模块/application.md",
                    "tests": [],
                }
            ],
        )
        state_directory = tempfile.TemporaryDirectory()
        self.addCleanup(state_directory.cleanup)
        state_path = Path(state_directory.name) / "state.json"
        self.assertTrue(self.inventory.write_json_if_changed(state_path, state))
        before = state_path.read_bytes()

        result = self.inventory.check_project(root, state)
        rewritten = self.inventory.write_json_if_changed(state_path, state)

        self.assertEqual(result["mode"], "no-change")
        self.assertFalse(rewritten)
        self.assertEqual(state_path.read_bytes(), before)
        for command in ("changed", "check"):
            cli = subprocess.run(
                [
                    "python3",
                    str(MODULE_PATH),
                    command,
                    "--project-root",
                    str(root),
                    "--state",
                    str(state_path),
                ],
                check=True,
                text=True,
                capture_output=True,
            )
            self.assertEqual(json.loads(cli.stdout)["mode"], "no-change")

    def test_manual_block_is_byte_stable_and_abnormal_markers_stop(self):
        original = (
            "# Module\n\n"
            f"{self.inventory.GENERATED_START}\nold\n{self.inventory.GENERATED_END}\n\n"
            f"{self.inventory.MANUAL_START}\n人工内容：空格  保留\n{self.inventory.MANUAL_END}\n"
        )
        manual_before = original[original.index(self.inventory.MANUAL_START) :]

        updated = self.inventory.replace_generated_block(original, "new")

        self.assertEqual(updated[updated.index(self.inventory.MANUAL_START) :], manual_before)
        malformed = original.replace(
            self.inventory.GENERATED_END, self.inventory.GENERATED_START, 1
        )
        with self.assertRaises(self.inventory.InventoryError):
            self.inventory.replace_generated_block(malformed, "unsafe")

    def test_agents_block_is_unique_and_malformed_content_is_untouched(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "AGENTS.md"
        path.write_text("# 原有内容\n", encoding="utf-8")

        self.assertTrue(self.inventory.ensure_agents_managed_block(path))
        first = path.read_bytes()
        self.assertFalse(self.inventory.ensure_agents_managed_block(path))
        self.assertEqual(path.read_bytes(), first)
        self.assertEqual(first.count(self.inventory.AGENTS_START.encode()), 1)

        path.write_text(f"原有\n{self.inventory.AGENTS_START}\n", encoding="utf-8")
        malformed = path.read_bytes()
        with self.assertRaises(self.inventory.InventoryError):
            self.inventory.ensure_agents_managed_block(path)
        self.assertEqual(path.read_bytes(), malformed)

    def test_write_helpers_reject_symlink_targets_and_ancestors(self):
        root = self.make_project()
        external_temporary = tempfile.TemporaryDirectory()
        self.addCleanup(external_temporary.cleanup)
        external = Path(external_temporary.name)

        outside_json = external / "outside.json"
        outside_json.write_text("sentinel-json\n", encoding="utf-8")
        output_link = root / "output.json"
        output_link.symlink_to(outside_json)
        with self.assertRaisesRegex(self.inventory.InventoryError, "符号链接"):
            self.inventory.write_json_if_changed(output_link, {"unsafe": True})
        self.assertEqual(outside_json.read_text(encoding="utf-8"), "sentinel-json\n")

        linked_parent = root / "linked-output"
        linked_parent.symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(self.inventory.InventoryError, "符号链接"):
            self.inventory.write_json_if_changed(
                linked_parent / "created.json", {"unsafe": True}
            )
        self.assertFalse((external / "created.json").exists())

        outside_agents = external / "AGENTS.md"
        outside_agents.write_text("external-agents\n", encoding="utf-8")
        agents_link = root / "AGENTS.md"
        agents_link.symlink_to(outside_agents)
        with self.assertRaisesRegex(self.inventory.InventoryError, "符号链接"):
            self.inventory.ensure_agents_managed_block(agents_link)
        self.assertEqual(
            outside_agents.read_text(encoding="utf-8"), "external-agents\n"
        )

        package = root / "ai_constraints"
        for skill_name in ("project-initialize", "project-update"):
            (package / ".agents" / "skills" / skill_name).mkdir(parents=True)
        external_agents_root = external / "agents-root"
        external_agents_root.mkdir()
        (root / ".agents").symlink_to(external_agents_root, target_is_directory=True)
        with self.assertRaisesRegex(self.inventory.InventoryError, "符号链接"):
            self.inventory.ensure_skill_links(root, package)
        self.assertEqual(list(external_agents_root.iterdir()), [])

    def test_skill_link_conflict_stops_before_creating_partial_links(self):
        root = self.make_project()
        package = root / "ai_constraints"
        for skill_name in ("project-initialize", "project-update"):
            (package / ".agents" / "skills" / skill_name).mkdir(parents=True)
        conflict = root / ".agents" / "skills" / "project-update"
        conflict.mkdir(parents=True)

        with self.assertRaisesRegex(self.inventory.InventoryError, "不是规定链接"):
            self.inventory.ensure_skill_links(root, package)

        self.assertFalse((root / ".agents" / "skills" / "project-initialize").exists())

    def test_valid_initialization_state_stops_repeated_initialize(self):
        root = self.make_project()
        state = complete_state(
            self.inventory,
            self.inventory.scan_project(root),
            [
                {
                    "id": "application",
                    "roots": ["src"],
                    "archive": "ai_constraints/codex/模块/application.md",
                    "tests": [],
                }
            ],
        )
        state_directory = tempfile.TemporaryDirectory()
        self.addCleanup(state_directory.cleanup)
        state_path = Path(state_directory.name) / "state.json"
        self.inventory.write_json_if_changed(state_path, state)
        before = state_path.read_bytes()

        with self.assertRaisesRegex(self.inventory.InventoryError, "project-update"):
            self.inventory.require_uninitialized(state_path)

        self.assertEqual(state_path.read_bytes(), before)


class WorkflowExerciseTests(unittest.TestCase):
    """在真实临时 Git fixture 中串行演练初始化与更新合同。"""

    def setUp(self):
        self.inventory = load_inventory_module()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project_root = Path(temporary.name)
        shutil.copytree(
            FIXTURES / "minimal-python-project", self.project_root, dirs_exist_ok=True
        )
        (self.project_root / "AGENTS.md").write_text("# 保留的项目规则\n", encoding="utf-8")
        initialize_git(self.project_root)
        run_git(self.project_root, "add", ".")
        run_git(self.project_root, "commit", "-qm", "fixture baseline")
        self.package_root = self.project_root / "ai_constraints"
        shutil.copytree(
            ROOT,
            self.package_root,
            ignore=shutil.ignore_patterns(".git", ".DS_Store", "__pycache__", "*.pyc"),
        )

    def _module_archive_text(self, generated_note="首次生成"):
        return (
            "# 应用模块\n\n"
            f"{self.inventory.GENERATED_START}\n"
            "## 目标与边界\n\n"
            "- 目标：返回由公开响应结构约束的问候结果。\n"
            "- 边界：不负责网络传输或持久化。\n\n"
            "## 核心不变量与验证\n\n"
            "| 核心不变量 | 实现入口 | 验证证据 |\n"
            "|---|---|---|\n"
            "| 响应键来自公开结构 | `app/api.py` | `tests/test_api.py` |\n\n"
            "## 公开契约与直接消费者\n\n"
            "- 公开契约：`app/schema.py` 的 `RESPONSE_KEYS`。\n"
            "- 直接消费者：`app/api.py`。\n\n"
            "## 修改风险\n\n"
            "- 结构变化需同步验证 API 消费者。\n\n"
            "## 验证命令\n\n"
            "- 快速：`python3 -m unittest discover -s tests -v`\n"
            "- 完整：`python3 -m unittest discover -s tests -v`\n"
            f"- 状态：{generated_note}。\n"
            f"{self.inventory.GENERATED_END}\n\n"
            f"{self.inventory.MANUAL_START}\n"
            "人工内容：这两个连续空格  必须保留。\n"
            f"{self.inventory.MANUAL_END}\n"
        )

    def _run_business_tests(self):
        """执行 Python fixture 已声明的标准库聚合门禁。"""

        return subprocess.run(
            ["python3", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
            cwd=self.project_root,
            check=False,
            text=True,
            capture_output=True,
        )

    def _initialize(self):
        state_path = self.package_root / "codex" / "项目状态.json"
        self.inventory.require_uninitialized(state_path)
        initial_scan = self.inventory.scan_project(self.project_root)
        self.assertGreater(initial_scan["eligible_file_count"], 0)

        architecture_path = self.package_root / "codex" / "架构.md"
        architecture = architecture_path.read_text(encoding="utf-8")
        architecture_generated = (
            "## 项目快照\n\n"
            "- 目标：提供最小 Python 响应。\n"
            "- 技术栈：Python 3 标准库。\n"
            "- 入口：`app/api.py`。\n\n"
            "## 模块索引\n\n"
            "| 模块 | 根目录 | 档案 |\n"
            "|---|---|---|\n"
            "| 应用 | `app/` | `ai_constraints/codex/模块/应用.md` |\n\n"
            "## 跨模块依赖\n\n"
            "单模块，无跨模块依赖。"
        )
        architecture_path.write_text(
            self.inventory.replace_generated_block(architecture, architecture_generated),
            encoding="utf-8",
        )
        archive = self.package_root / "codex" / "模块" / "应用.md"
        archive.write_text(self._module_archive_text(), encoding="utf-8")
        self.inventory.ensure_agents_managed_block(self.project_root / "AGENTS.md")
        created = self.inventory.ensure_skill_links(self.project_root, self.package_root)
        self.assertEqual(len(created), 2)

        business_test = self._run_business_tests()
        self.assertEqual(business_test.returncode, 0, business_test.stderr)

        final_scan = self.inventory.scan_project(self.project_root)
        state = complete_state(
            self.inventory,
            final_scan,
            [
                {
                    "id": "application",
                    "roots": ["app"],
                    "archive": "ai_constraints/codex/模块/应用.md",
                    "tests": ["tests/test_api.py"],
                }
            ],
            [{"path": "app/schema.py", "consumers": ["app/api.py"]}],
        )
        self.inventory.write_json_if_changed(state_path, state)
        return state_path, archive, state

    def test_real_initialize_repeat_no_change_incremental_contract_and_marker_protection(self):
        state_path, archive, state = self._initialize()
        verify = subprocess.run(
            ["bash", str(self.package_root / "scripts" / "verify.sh")],
            cwd=self.project_root,
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertEqual(verify.returncode, 0, verify.stderr)

        # 重复初始化必须先停止，且不触碰已有集成产物。
        repeated_before = {
            "state": state_path.read_bytes(),
            "archive": archive.read_bytes(),
            "agents": (self.project_root / "AGENTS.md").read_bytes(),
        }
        with self.assertRaises(self.inventory.InventoryError):
            self.inventory.require_uninitialized(state_path)
        self.assertEqual(state_path.read_bytes(), repeated_before["state"])
        self.assertEqual(archive.read_bytes(), repeated_before["archive"])
        self.assertEqual((self.project_root / "AGENTS.md").read_bytes(), repeated_before["agents"])

        # 无变化更新只读检查，不应重写状态或档案。
        no_change = self.inventory.analyze_changes(self.project_root, state)
        self.assertEqual(no_change["mode"], "no-change")
        self.assertEqual(state_path.read_bytes(), repeated_before["state"])
        self.assertEqual(archive.read_bytes(), repeated_before["archive"])

        # 私有实现变化走 R1，更新 generated 后刷新机械基线，manual 字节保持不变。
        service = self.project_root / "app" / "service.py"
        service.write_text(
            service.read_text(encoding="utf-8").replace(
                'return "hello"', 'message = "hello"\n    return message'
            ),
            encoding="utf-8",
        )
        private_change = self.inventory.analyze_changes(self.project_root, state)
        self.assertEqual((private_change["mode"], private_change["risk_level"]), ("incremental", "R1"))
        private_test = self._run_business_tests()
        self.assertEqual(private_test.returncode, 0, private_test.stderr)
        archive_text = archive.read_text(encoding="utf-8")
        manual_before = archive_text[archive_text.index(self.inventory.MANUAL_START) :]
        archive.write_text(
            self.inventory.replace_generated_block(
                archive_text,
                self._module_archive_text("增量更新").split(self.inventory.GENERATED_START, 1)[1].split(
                    self.inventory.GENERATED_END, 1
                )[0],
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            archive.read_text(encoding="utf-8")[
                archive.read_text(encoding="utf-8").index(self.inventory.MANUAL_START) :
            ],
            manual_before,
        )
        state = self.inventory.refresh_state_inventory(
            state, self.inventory.scan_project(self.project_root), "passed"
        )
        self.inventory.write_json_if_changed(state_path, state)
        self.assertEqual(self.inventory.analyze_changes(self.project_root, state)["mode"], "no-change")

        # schema 结构变化必须全量重扫，同时展开真实消费者。
        schema = self.project_root / "app" / "schema.py"
        schema.write_text('RESPONSE_KEYS = ("message", "version")\n', encoding="utf-8")
        shared = self.inventory.analyze_changes(self.project_root, state)
        self.assertEqual((shared["mode"], shared["risk_level"]), ("full-rescan", "R3"))
        self.assertEqual(shared["consumer_files"], ["app/api.py"])
        api = self.project_root / "app" / "api.py"
        api.write_text(
            api.read_text(encoding="utf-8").replace(
                "values = (greeting(),)", 'values = (greeting(), "1")'
            ),
            encoding="utf-8",
        )
        test_api = self.project_root / "tests" / "test_api.py"
        test_api.write_text(
            test_api.read_text(encoding="utf-8").replace(
                '{"message": "hello"}', '{"message": "hello", "version": "1"}'
            ),
            encoding="utf-8",
        )
        shared_test = self._run_business_tests()
        self.assertEqual(shared_test.returncode, 0, shared_test.stderr)
        completed_shared = self.inventory.analyze_changes(self.project_root, state)
        self.assertEqual(completed_shared["risk_level"], "R3")
        self.assertEqual(completed_shared["consumer_files"], ["app/api.py"])
        state = self.inventory.refresh_state_inventory(
            state, self.inventory.scan_project(self.project_root), "passed"
        )
        self.inventory.write_json_if_changed(state_path, state)
        self.assertEqual(self.inventory.analyze_changes(self.project_root, state)["mode"], "no-change")

        # 异常标记保护在真实文件上先失败，文件字节保持不变。
        malformed = self.package_root / "codex" / "模块" / "异常.md"
        malformed.write_text(
            f"{self.inventory.GENERATED_START}\n缺少结束标记\n{self.inventory.MANUAL_START}\n人工\n{self.inventory.MANUAL_END}\n",
            encoding="utf-8",
        )
        malformed_before = malformed.read_bytes()
        with self.assertRaises(self.inventory.InventoryError):
            self.inventory.replace_generated_block(
                malformed.read_text(encoding="utf-8"), "不得写入"
            )
        self.assertEqual(malformed.read_bytes(), malformed_before)


class JavaScriptWorkflowExerciseTests(unittest.TestCase):
    """在第二个真实 fixture 上验证同一安装合同不依赖 Python 项目结构。"""

    def setUp(self):
        self.inventory = load_inventory_module()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project_root = Path(temporary.name)
        shutil.copytree(
            FIXTURES / "minimal-js-project", self.project_root, dirs_exist_ok=True
        )
        (self.project_root / "AGENTS.md").write_text("# JavaScript 项目规则\n", encoding="utf-8")
        initialize_git(self.project_root)
        run_git(self.project_root, "add", ".")
        run_git(self.project_root, "commit", "-qm", "fixture baseline")
        self.package_root = self.project_root / "ai_constraints"
        shutil.copytree(
            ROOT,
            self.package_root,
            ignore=shutil.ignore_patterns(".git", ".DS_Store", "__pycache__", "*.pyc"),
        )

    def _archive_text(self):
        return (
            "# JavaScript 应用\n\n"
            f"{self.inventory.GENERATED_START}\n"
            "## 目标与边界\n\n"
            "- 目标：提供由共享键定义约束的结果对象。\n"
            "- 边界：不负责网络与存储。\n\n"
            "## 核心不变量与验证\n\n"
            "| 核心不变量 | 实现入口 | 验证证据 |\n"
            "|---|---|---|\n"
            "| 结果键来自共享契约 | `src/service.js` | `test/service.test.js` |\n\n"
            "## 公开契约与直接消费者\n\n"
            "- 公开契约：`src/contracts.js`。\n"
            "- 直接消费者：`src/service.js`。\n\n"
            "## 修改风险\n\n"
            "- 共享键变化需验证服务消费者。\n\n"
            "## 验证命令\n\n"
            "- 快速：`node --test`\n"
            "- 完整：`node --test`\n"
            f"{self.inventory.GENERATED_END}\n\n"
            f"{self.inventory.MANUAL_START}\n"
            "人工内容：JavaScript fixture 保留。\n"
            f"{self.inventory.MANUAL_END}\n"
        )

    def _run_node_tests(self):
        """执行 fixture 自带的无依赖业务门禁，并返回原始结果供断言。"""

        node = shutil.which("node")
        self.assertIsNotNone(node, "JavaScript fixture 需要现有 Node.js 执行内置测试")
        return subprocess.run(
            [node, "--test"],
            cwd=self.project_root,
            check=False,
            text=True,
            capture_output=True,
        )

    def _initialize(self):
        architecture = self.package_root / "codex" / "架构.md"
        architecture.write_text(
            self.inventory.replace_generated_block(
                architecture.read_text(encoding="utf-8"),
                "## 项目快照\n\n"
                "- 目标：提供最小 JavaScript 结果对象。\n"
                "- 技术栈：Node.js ESM 与内置测试器。\n"
                "- 入口：`src/service.js`。\n\n"
                "## 模块索引\n\n"
                "| 模块 | 根目录 | 档案 |\n"
                "|---|---|---|\n"
                "| JavaScript 应用 | `src/` | `ai_constraints/codex/模块/javascript.md` |\n\n"
                "## 跨模块依赖\n\n"
                "单模块，无跨模块依赖。",
            ),
            encoding="utf-8",
        )
        archive = self.package_root / "codex" / "模块" / "javascript.md"
        archive.write_text(self._archive_text(), encoding="utf-8")
        self.inventory.ensure_agents_managed_block(self.project_root / "AGENTS.md")
        self.inventory.ensure_skill_links(self.project_root, self.package_root)
        result = self._run_node_tests()
        self.assertEqual(result.returncode, 0, result.stderr)
        state = complete_state(
            self.inventory,
            self.inventory.scan_project(self.project_root),
            [
                {
                    "id": "javascript",
                    "roots": ["src"],
                    "archive": "ai_constraints/codex/模块/javascript.md",
                    "tests": ["test/service.test.js"],
                }
            ],
            [{"path": "src/contracts.js", "consumers": ["src/service.js"]}],
            ["node --test"],
        )
        state_path = self.package_root / "codex" / "项目状态.json"
        self.inventory.write_json_if_changed(state_path, state)
        return state_path, state

    def test_javascript_fixture_initialization_and_updates(self):
        state_path, state = self._initialize()
        verify_result = subprocess.run(
            ["bash", str(self.package_root / "scripts" / "verify.sh")],
            cwd=self.project_root,
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertEqual(verify_result.returncode, 0, verify_result.stderr)
        with self.assertRaises(self.inventory.InventoryError):
            self.inventory.require_uninitialized(state_path)
        self.assertEqual(self.inventory.analyze_changes(self.project_root, state)["mode"], "no-change")

        service = self.project_root / "src" / "service.js"
        service.write_text(
            service.read_text(encoding="utf-8").replace(
                "return { [resultKeys[0]]: 1 };",
                "const value = 1;\n  return { [resultKeys[0]]: value };",
            ),
            encoding="utf-8",
        )
        private = self.inventory.analyze_changes(self.project_root, state)
        self.assertEqual((private["mode"], private["risk_level"]), ("incremental", "R1"))
        private_test = self._run_node_tests()
        self.assertEqual(private_test.returncode, 0, private_test.stderr)
        state = self.inventory.refresh_state_inventory(
            state, self.inventory.scan_project(self.project_root), "passed"
        )
        self.inventory.write_json_if_changed(state_path, state)

        contract = self.project_root / "src" / "contracts.js"
        contract.write_text('export const resultKeys = ["value", "version"];\n', encoding="utf-8")
        shared = self.inventory.analyze_changes(self.project_root, state)
        self.assertEqual(shared["risk_level"], "R2")
        self.assertEqual(shared["consumer_files"], ["src/service.js"])
        shared_test = self._run_node_tests()
        self.assertEqual(shared_test.returncode, 0, shared_test.stderr)
        state = self.inventory.refresh_state_inventory(
            state, self.inventory.scan_project(self.project_root), "passed"
        )
        self.inventory.write_json_if_changed(state_path, state)
        self.assertEqual(self.inventory.analyze_changes(self.project_root, state)["mode"], "no-change")


if __name__ == "__main__":
    unittest.main()
