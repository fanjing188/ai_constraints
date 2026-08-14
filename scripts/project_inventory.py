#!/usr/bin/env python3
"""为 AI Constraints 提供确定性的项目文件清单与增量变化事实。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence


SCHEMA_VERSION = 1
TOOLKIT_VERSION = "1.0"
PACKAGE_DIRECTORY = "ai_constraints"
MAX_TEXT_FILE_BYTES = 2 * 1024 * 1024

# 这些目录只代表第三方、缓存或生成内容；被排除目录仍会进入摘要计数。
EXCLUDED_DIRECTORY_REASONS = {
    ".git": "metadata",
    ".hg": "metadata",
    ".svn": "metadata",
    "node_modules": "dependency",
    "vendor": "dependency",
    ".venv": "dependency",
    "venv": "dependency",
    ".next": "generated",
    "dist": "generated",
    "build": "generated",
    "coverage": "generated",
    ".cache": "generated",
    ".pytest_cache": "generated",
    "__pycache__": "generated",
}

# 先按路径排除秘密，确保脚本不会为了判断文本类型而打开秘密文件。
SECRET_BASENAMES = {
    ".env",
    ".authinfo",
    ".envrc",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "credentials",
    "credentials.json",
    "secrets.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "kubeconfig",
    "service-account.json",
}
SECRET_SUFFIXES = {".key", ".p12", ".pfx", ".pem", ".jks", ".keystore"}
SECRET_DIRECTORY_NAMES = {".aws", ".azure", ".gcloud", "gcloud"}
SECRET_PATH_SUFFIXES = {
    (".config", "gh", "hosts.yaml"),
    (".config", "gh", "hosts.yml"),
    (".docker", "config.json"),
    (".kube", "config"),
}
SECRET_NAME_PATTERN = re.compile(
    r"(?:^|[._-])(?:auth|credentials?|secrets?|tokens?)(?:$|[._-])", re.IGNORECASE
)
SECRET_SERVICE_NAME_PATTERN = re.compile(
    r"^(?:kubeconfig|service[-_]account)(?:$|[._-])", re.IGNORECASE
)

# 明确的二进制扩展名可在不读取内容的情况下排除。
BINARY_SUFFIXES = {
    ".7z",
    ".a",
    ".avi",
    ".bin",
    ".bmp",
    ".class",
    ".db",
    ".dll",
    ".dylib",
    ".eot",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".lockb",
    ".mov",
    ".mp3",
    ".mp4",
    ".o",
    ".otf",
    ".pdf",
    ".png",
    ".pyc",
    ".sqlite",
    ".sqlite3",
    ".tar",
    ".tgz",
    ".ttf",
    ".wav",
    ".webm",
    ".webp",
    ".woff",
    ".woff2",
    ".xls",
    ".xlsx",
    ".zip",
}

STRUCTURAL_ENTRY_BASENAMES = {
    ".gitlab-ci.yml",
    ".gitlab-ci.yaml",
    "azure-pipelines.yml",
    "azure-pipelines.yaml",
    "build.gradle",
    "build.gradle.kts",
    "build.rs",
    "cargo.toml",
    "cmakelists.txt",
    "compose.yaml",
    "compose.yml",
    "conftest.py",
    "deno.json",
    "deno.jsonc",
    "docker-compose.yaml",
    "docker-compose.yml",
    "dockerfile",
    "go.mod",
    "gradle.properties",
    "jest.config.js",
    "jest.config.mjs",
    "jest.config.ts",
    "makefile",
    "meson.build",
    "noxfile.py",
    "package.json",
    "playwright.config.cjs",
    "playwright.config.js",
    "playwright.config.mjs",
    "playwright.config.ts",
    "pom.xml",
    "pyproject.toml",
    "pytest.ini",
    "setup.cfg",
    "setup.py",
    "tox.ini",
    "vitest.config.js",
    "vitest.config.mjs",
    "vitest.config.ts",
}
STRUCTURAL_CI_BASENAMES = {
    ".travis.yaml",
    ".travis.yml",
    "jenkinsfile",
}
STRUCTURAL_PATH_TOKEN_KINDS = {
    "registries": "共享注册表",
    "registry": "共享注册表",
    "schema": "schema",
    "schemas": "schema",
}
STRUCTURAL_REGISTRY_SEGMENTS = {
    "registries",
    "registry",
    "shared-registries",
    "shared-registry",
}
PLAYWRIGHT_CONFIG_SUFFIXES = {"cjs", "cts", "js", "jsx", "mjs", "mts", "ts", "tsx"}

GENERATED_START = "<!-- ai-constraints:generated:start -->"
GENERATED_END = "<!-- ai-constraints:generated:end -->"
MANUAL_START = "<!-- ai-constraints:manual:start -->"
MANUAL_END = "<!-- ai-constraints:manual:end -->"
AGENTS_START = "<!-- ai-constraints:start -->"
AGENTS_END = "<!-- ai-constraints:end -->"
AGENTS_INCLUDE = "@ai_constraints/codex/入口.md"


class InventoryError(RuntimeError):
    """表示不能安全继续的显式前置条件错误。"""


def run_git(project_root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """运行 Git 并保留原始错误，避免把基线或编码问题静默降级。"""

    try:
        return subprocess.run(
            ["git", *args],
            cwd=project_root,
            check=check,
            text=True,
            capture_output=True,
        )
    except FileNotFoundError as exc:
        raise InventoryError("无法执行 git：系统未安装 Git。") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout).strip()
        raise InventoryError(f"Git 命令失败：git {' '.join(args)}\n{detail}") from exc


def require_git_root(project_root: Path | str) -> Path:
    """确认传入路径本身就是 Git 根，防止相对路径被写入错误项目。"""

    root = Path(project_root).resolve()
    if not root.is_dir():
        raise InventoryError(f"项目路径不存在或不是目录：{root}")
    result = run_git(root, "rev-parse", "--show-toplevel", check=False)
    if result.returncode != 0:
        raise InventoryError(f"目标不是 Git 项目：{root}")
    actual = Path(result.stdout.strip()).resolve()
    if actual != root:
        raise InventoryError(f"--project-root 必须是 Git 根：应为 {actual}")
    return root


def validate_package_location(project_root: Path | str, package_root: Path | str) -> str:
    """验证唯一安装契约，并返回 standalone 或 embedded 模式。"""

    project = require_git_root(project_root)
    package = Path(package_root).resolve()
    if package == project and package.name == PACKAGE_DIRECTORY:
        return "standalone"
    expected = project / PACKAGE_DIRECTORY
    if package == expected and package.name == PACKAGE_DIRECTORY:
        return "embedded"
    raise InventoryError(
        "工作包必须位于项目根的 ai_constraints/ 目录；"
        f"当前为 {package}，应移动到 {expected}。"
    )


def _is_secret_path(relative_path: str) -> bool:
    """只根据路径识别秘密；匹配后绝不读取文件内容。"""

    path = PurePosixPath(relative_path)
    lowered_parts = tuple(part.lower() for part in path.parts)
    name = path.name.lower()
    if name == ".env" or name.startswith(".env."):
        return True
    if (
        any(
            len(lowered_parts) >= len(suffix)
            and lowered_parts[-len(suffix) :] == suffix
            for suffix in SECRET_PATH_SUFFIXES
        )
        or name in SECRET_BASENAMES
        or path.suffix.lower() in SECRET_SUFFIXES
        or SECRET_NAME_PATTERN.search(name)
        or SECRET_SERVICE_NAME_PATTERN.search(name)
    ):
        return True
    return any(
        part.lower() in {".secrets", "secrets"} | SECRET_DIRECTORY_NAMES
        for part in path.parts[:-1]
    )


def _looks_minified(relative_path: str, data: bytes) -> bool:
    """排除超大压缩源码，避免一次性扫描把生成产物误当第一方源文件。"""

    name = PurePosixPath(relative_path).name.lower()
    if re.search(r"\.min\.(?:css|js|mjs|cjs)$", name):
        return True
    if len(data) < 200_000:
        return False
    lines = data.count(b"\n") + 1
    return len(data) // lines > 1_000


def _read_text_bytes(path: Path, relative_path: str) -> tuple[bytes | None, str | None]:
    """读取符合范围的文件；返回排除原因时调用方只记录事实。"""

    if path.suffix.lower() in BINARY_SUFFIXES:
        return None, "binary"
    size = path.stat().st_size
    if size > MAX_TEXT_FILE_BYTES:
        return None, "oversized"
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise InventoryError(f"无法读取文件 {relative_path}：{exc}") from exc
    if b"\0" in data:
        return None, "binary"
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return None, "binary"
    if _looks_minified(relative_path, data):
        return None, "minified"
    return data, None


def _record_exclusion(
    excluded_files: list[dict[str, str]],
    counts: dict[str, int],
    relative_path: str,
    reason: str,
) -> None:
    """集中维护稳定的排除摘要，保证每个排除项都有可见原因。"""

    excluded_files.append({"path": relative_path, "reason": reason})
    counts[reason] = counts.get(reason, 0) + 1


def _excluded_entry_metadata(path: Path, relative_path: str) -> dict[str, Any]:
    """只用 lstat 记录排除项元数据，不跟随链接也不读取文件内容。"""

    try:
        details = path.lstat()
    except OSError as exc:
        raise InventoryError(f"无法读取排除项元数据 {relative_path}：{exc}") from exc
    if stat.S_ISREG(details.st_mode):
        file_type = "file"
    elif stat.S_ISLNK(details.st_mode):
        file_type = "symlink"
    elif stat.S_ISDIR(details.st_mode):
        file_type = "directory"
    else:
        file_type = "special"
    return {
        "file_type": file_type,
        "mtime_ns": details.st_mtime_ns,
        "size": details.st_size,
    }


def _iter_project_entries(project_root: Path) -> Iterable[tuple[Path, str, str | None]]:
    """按稳定顺序枚举文件，并把被剪枝目录作为明确排除项返回。"""

    for current, directory_names, file_names in os.walk(project_root, topdown=True):
        current_path = Path(current)
        relative_directory = current_path.relative_to(project_root)
        kept_directories: list[str] = []
        for directory_name in sorted(directory_names):
            relative = (relative_directory / directory_name).as_posix()
            directory_path = current_path / directory_name
            reason = EXCLUDED_DIRECTORY_REASONS.get(directory_name)
            # 嵌入模式必须完整排除工作包自身，避免用规则文件推断业务事实。
            if relative == PACKAGE_DIRECTORY:
                reason = "toolkit"
            if directory_path.is_symlink():
                reason = "symlink"
            if reason:
                yield directory_path, f"{relative}/", reason
            else:
                kept_directories.append(directory_name)
        directory_names[:] = kept_directories
        for file_name in sorted(file_names):
            path = current_path / file_name
            relative = (relative_directory / file_name).as_posix()
            yield path, relative, None


def _git_head(project_root: Path) -> str:
    """读取当前提交；空仓库没有可建立的增量基线，因此明确失败。"""

    result = run_git(project_root, "rev-parse", "HEAD", check=False)
    if result.returncode != 0:
        raise InventoryError("Git 项目尚无提交，无法建立 indexed_commit 基线。")
    return result.stdout.strip()


def _tracked_paths(project_root: Path) -> set[str]:
    """读取 Git 跟踪来源，清单仍以磁盘当前内容为事实。"""

    result = run_git(project_root, "ls-files", "-z")
    return {path for path in result.stdout.split("\0") if path}


def _status_path_in_scope(relative_path: str) -> bool:
    """Git 状态只保留业务范围路径，秘密只暴露路径而不读取内容。"""

    parts = PurePosixPath(relative_path).parts
    if not parts or parts[0] == PACKAGE_DIRECTORY:
        return False
    lowered_directories = {part.lower() for part in parts[:-1]}
    return "ci" in lowered_directories or not any(
        part in EXCLUDED_DIRECTORY_REASONS for part in parts[:-1]
    )


def _git_worktree_changes(project_root: Path) -> list[dict[str, str]]:
    """读取暂存、未暂存与未跟踪状态，并稳定表达重命名双方。"""

    result = run_git(project_root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    records = result.stdout.split("\0")
    changes: list[dict[str, str]] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if len(record) < 4 or record[2] != " ":
            raise InventoryError(f"无法解析 Git 状态记录：{record!r}")
        status = record[:2]
        path = record[3:]
        change = {"path": path, "status": status}
        if "R" in status or "C" in status:
            if index >= len(records) or not records[index]:
                raise InventoryError(f"Git 重命名状态缺少原路径：{path}")
            change["old_path"] = records[index]
            index += 1
        if _status_path_in_scope(path):
            changes.append(change)
    return sorted(changes, key=lambda item: (item["path"], item["status"]))


def scan_project(project_root: Path | str) -> dict[str, Any]:
    """读取全部符合合同的第一方文本文件并返回确定性机械事实。"""

    root = require_git_root(project_root)
    eligible_files: list[str] = []
    fingerprints: dict[str, str] = {}
    excluded_files: list[dict[str, str]] = []
    excluded_counts: dict[str, int] = {}
    excluded_metadata: dict[str, dict[str, Any]] = {}

    for path, relative, directory_reason in _iter_project_entries(root):
        if directory_reason:
            _record_exclusion(excluded_files, excluded_counts, relative, directory_reason)
            continue
        if path.is_symlink():
            _record_exclusion(excluded_files, excluded_counts, relative, "symlink")
            excluded_metadata[relative] = _excluded_entry_metadata(path, relative)
            continue
        if not path.is_file():
            _record_exclusion(excluded_files, excluded_counts, relative, "special")
            excluded_metadata[relative] = _excluded_entry_metadata(path, relative)
            continue
        if _is_secret_path(relative):
            _record_exclusion(excluded_files, excluded_counts, relative, "secret")
            excluded_metadata[relative] = _excluded_entry_metadata(path, relative)
            continue
        data, reason = _read_text_bytes(path, relative)
        if reason:
            _record_exclusion(excluded_files, excluded_counts, relative, reason)
            excluded_metadata[relative] = _excluded_entry_metadata(path, relative)
            continue
        assert data is not None
        eligible_files.append(relative)
        fingerprints[relative] = hashlib.sha256(data).hexdigest()

    eligible_files.sort()
    excluded_files.sort(key=lambda item: (item["path"], item["reason"]))
    tracked_paths = _tracked_paths(root)
    file_sources = {
        relative: "tracked" if relative in tracked_paths else "untracked"
        for relative in eligible_files
    }
    # 项目指纹同时覆盖路径与内容，文件重命名不会被误判为无变化。
    digest = hashlib.sha256()
    for relative in eligible_files:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(fingerprints[relative].encode("ascii"))
        digest.update(b"\n")

    return {
        "schema_version": SCHEMA_VERSION,
        "toolkit_version": TOOLKIT_VERSION,
        "indexed_commit": _git_head(root),
        "project_fingerprint": digest.hexdigest(),
        "eligible_file_count": len(eligible_files),
        "eligible_files": eligible_files,
        "eligible_file_fingerprints": dict(sorted(fingerprints.items())),
        "eligible_file_sources": file_sources,
        "excluded_file_count_by_reason": dict(sorted(excluded_counts.items())),
        "excluded_files": excluded_files,
        "excluded_file_metadata": dict(sorted(excluded_metadata.items())),
        "worktree_changes": _git_worktree_changes(root),
        "modules": [],
        "contracts": [],
        "verification": {
            "fast_commands": [],
            "full_commands": [],
            "last_status": "not_run",
        },
    }


def stable_json_bytes(value: Any) -> bytes:
    """使用唯一 JSON 形式，保证相同输入字节级一致。"""

    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _reject_symlink_path(path: Path) -> None:
    """拒绝目标及任何现有祖先符号链接，避免写入逃逸到项目外。"""

    absolute = Path(os.path.abspath(path))
    system_aliases = {Path("/etc"), Path("/home"), Path("/tmp"), Path("/var")}
    for candidate in reversed((absolute, *absolute.parents)):
        # macOS 的这些根级目录是只读系统提供的固定别名，不属于项目可控祖先。
        if candidate not in system_aliases and candidate.is_symlink():
            raise InventoryError(f"拒绝经符号链接写入：{candidate}")


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    """在已校验目录中原子替换文件，不跟随最终目标符号链接。"""

    _reject_symlink_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_path(path)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        if path.exists():
            os.chmod(temporary_path, path.stat().st_mode & 0o7777)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        _reject_symlink_path(path)
        os.replace(temporary_path, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def write_json_if_changed(path: Path | str, value: Any) -> bool:
    """只有字节变化时才写文件，避免无变化更新时间或 Git diff。"""

    destination = Path(path)
    content = stable_json_bytes(value)
    _reject_symlink_path(destination)
    if destination.exists() and destination.read_bytes() == content:
        return False
    _atomic_write_bytes(destination, content)
    return True


def load_state(path: Path | str) -> dict[str, Any]:
    """读取状态并立即校验，损坏状态不能进入增量路径。"""

    state_path = Path(path)
    try:
        value = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InventoryError(f"项目状态不存在：{state_path}") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InventoryError(f"项目状态无法读取：{state_path}：{exc}") from exc
    validate_state(value)
    return value


def _require_relative_path(value: Any, field: str) -> str:
    """拒绝绝对路径和父目录跳转，状态文件只能描述项目内路径。"""

    if not isinstance(value, str) or not value:
        raise InventoryError(f"状态字段 {field} 必须是非空相对路径。")
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or "\\" in value:
        raise InventoryError(f"状态字段 {field} 不是安全相对路径：{value}")
    return value


def validate_state(state: Any) -> None:
    """验证增量算法依赖的最小 schema，不为旧 schema 添加猜测性兼容。"""

    if not isinstance(state, dict):
        raise InventoryError("项目状态根节点必须是对象。")
    if state.get("schema_version") != SCHEMA_VERSION:
        raise InventoryError(
            f"项目状态 schema 不兼容：需要 {SCHEMA_VERSION}，实际为 {state.get('schema_version')}。"
        )
    if state.get("toolkit_version") != TOOLKIT_VERSION:
        raise InventoryError(
            f"项目状态工具包版本不兼容：需要 {TOOLKIT_VERSION}，实际为 {state.get('toolkit_version')}。"
        )
    for field in (
        "toolkit_version",
        "indexed_commit",
        "project_fingerprint",
        "eligible_file_count",
        "eligible_files",
        "eligible_file_fingerprints",
        "eligible_file_sources",
        "excluded_file_count_by_reason",
        "excluded_files",
        "worktree_changes",
        "modules",
        "contracts",
        "verification",
    ):
        if field not in state:
            raise InventoryError(f"项目状态缺少字段：{field}")
    files = state["eligible_files"]
    if not isinstance(files, list) or files != sorted(files) or len(files) != len(set(files)):
        raise InventoryError("eligible_files 必须稳定排序且不能重复。")
    for index, relative in enumerate(files):
        _require_relative_path(relative, f"eligible_files[{index}]")
    if state["eligible_file_count"] != len(files):
        raise InventoryError("eligible_file_count 与 eligible_files 数量不一致。")
    fingerprints = state["eligible_file_fingerprints"]
    if not isinstance(fingerprints, dict) or sorted(fingerprints) != files:
        raise InventoryError("eligible_file_fingerprints 必须与 eligible_files 完整对应。")
    for relative, fingerprint in fingerprints.items():
        if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            raise InventoryError(f"文件指纹格式错误：{relative}")
    sources = state["eligible_file_sources"]
    if not isinstance(sources, dict) or sorted(sources) != files:
        raise InventoryError("eligible_file_sources 必须与 eligible_files 完整对应。")
    if any(source not in {"tracked", "untracked"} for source in sources.values()):
        raise InventoryError("eligible_file_sources 只能使用 tracked 或 untracked。")
    if not re.fullmatch(r"[0-9a-f]{40,64}", state["indexed_commit"]):
        raise InventoryError("indexed_commit 必须是完整十六进制提交 ID。")
    if not re.fullmatch(r"[0-9a-f]{64}", state["project_fingerprint"]):
        raise InventoryError("project_fingerprint 必须是 SHA-256 十六进制值。")
    excluded_files = state["excluded_files"]
    if not isinstance(excluded_files, list) or excluded_files != sorted(
        excluded_files, key=lambda item: (item.get("path", ""), item.get("reason", ""))
    ):
        raise InventoryError("excluded_files 必须按路径和原因稳定排序。")
    calculated_exclusions: dict[str, int] = {}
    for index, excluded in enumerate(excluded_files):
        if not isinstance(excluded, dict) or set(excluded) != {"path", "reason"}:
            raise InventoryError(f"excluded_files[{index}] 必须只包含 path 和 reason。")
        _require_relative_path(excluded["path"].rstrip("/"), f"excluded_files[{index}].path")
        reason = excluded["reason"]
        if not isinstance(reason, str) or not reason:
            raise InventoryError(f"excluded_files[{index}].reason 不能为空。")
        calculated_exclusions[reason] = calculated_exclusions.get(reason, 0) + 1
    if state["excluded_file_count_by_reason"] != dict(sorted(calculated_exclusions.items())):
        raise InventoryError("excluded_file_count_by_reason 与 excluded_files 不一致。")
    excluded_metadata = state.get("excluded_file_metadata")
    if excluded_metadata is not None:
        expected_metadata_paths = sorted(
            excluded["path"] for excluded in excluded_files if not excluded["path"].endswith("/")
        )
        if not isinstance(excluded_metadata, dict) or sorted(excluded_metadata) != expected_metadata_paths:
            raise InventoryError("excluded_file_metadata 必须与排除文件完整对应。")
        for relative, metadata in excluded_metadata.items():
            if not isinstance(metadata, dict) or set(metadata) != {
                "file_type",
                "mtime_ns",
                "size",
            }:
                raise InventoryError(f"排除文件元数据字段无效：{relative}")
            if metadata["file_type"] not in {"file", "symlink", "directory", "special"}:
                raise InventoryError(f"排除文件类型无效：{relative}")
            if type(metadata["mtime_ns"]) is not int or type(metadata["size"]) is not int:
                raise InventoryError(f"排除文件元数据必须是整数：{relative}")
            if metadata["size"] < 0:
                raise InventoryError(f"排除文件大小不能为负数：{relative}")
    worktree_changes = state["worktree_changes"]
    if not isinstance(worktree_changes, list) or worktree_changes != sorted(
        worktree_changes, key=lambda item: (item.get("path", ""), item.get("status", ""))
    ):
        raise InventoryError("worktree_changes 必须按路径和状态稳定排序。")
    for index, change in enumerate(worktree_changes):
        if not isinstance(change, dict) or set(change) not in (
            {"path", "status"},
            {"old_path", "path", "status"},
        ):
            raise InventoryError(f"worktree_changes[{index}] 字段无效。")
        _require_relative_path(change["path"], f"worktree_changes[{index}].path")
        if "old_path" in change:
            _require_relative_path(
                change["old_path"], f"worktree_changes[{index}].old_path"
            )
        if not isinstance(change["status"], str) or len(change["status"]) != 2:
            raise InventoryError(f"worktree_changes[{index}].status 格式错误。")
    modules = state["modules"]
    if not isinstance(modules, list):
        raise InventoryError("modules 必须是数组。")
    if [module.get("id") for module in modules] != sorted(
        module.get("id", "") for module in modules
    ):
        raise InventoryError("modules 必须按模块 ID 稳定排序。")
    module_ids: set[str] = set()
    archives: set[str] = set()
    for index, module in enumerate(modules):
        if not isinstance(module, dict):
            raise InventoryError(f"modules[{index}] 必须是对象。")
        module_id = module.get("id")
        if not isinstance(module_id, str) or not module_id or module_id in module_ids:
            raise InventoryError(f"模块 ID 缺失或重复：{module_id}")
        module_ids.add(module_id)
        archive = _require_relative_path(module.get("archive"), f"modules[{index}].archive")
        if archive in archives:
            raise InventoryError(f"模块档案重复：{archive}")
        archives.add(archive)
        roots = module.get("roots")
        if (
            not isinstance(roots, list)
            or not roots
            or roots != sorted(set(roots))
        ):
            raise InventoryError(f"模块 {module_id} 必须至少声明一个根路径。")
        for root_index, relative in enumerate(roots):
            _require_relative_path(relative, f"modules[{index}].roots[{root_index}]")
        tests = module.get("tests", [])
        if not isinstance(tests, list) or tests != sorted(set(tests)):
            raise InventoryError(f"模块 {module_id} 的 tests 必须稳定排序且不能重复。")
        for test_index, relative in enumerate(tests):
            _require_relative_path(relative, f"modules[{index}].tests[{test_index}]")
    contracts = state["contracts"]
    if not isinstance(contracts, list):
        raise InventoryError("contracts 必须是数组。")
    if [contract.get("path") for contract in contracts] != sorted(
        contract.get("path", "") for contract in contracts
    ):
        raise InventoryError("contracts 必须按路径稳定排序。")
    contract_paths: set[str] = set()
    for index, contract in enumerate(contracts):
        contract_path = _require_relative_path(
            contract.get("path"), f"contracts[{index}].path"
        )
        if contract_path in contract_paths:
            raise InventoryError(f"公开契约路径重复：{contract_path}")
        contract_paths.add(contract_path)
        consumers = contract.get("consumers")
        if (
            not isinstance(consumers, list)
            or not consumers
            or consumers != sorted(set(consumers))
        ):
            raise InventoryError(f"contracts[{index}].consumers 不能为空；不确定时使用 UNKNOWN。")
        for consumer_index, relative in enumerate(consumers):
            if relative != "UNKNOWN":
                _require_relative_path(relative, f"contracts[{index}].consumers[{consumer_index}]")
    verification = state["verification"]
    if not isinstance(verification, dict):
        raise InventoryError("verification 必须是对象。")
    for field in ("fast_commands", "full_commands"):
        commands = verification.get(field)
        if not isinstance(commands, list) or any(
            not isinstance(command, str) for command in commands
        ):
            raise InventoryError(f"verification.{field} 必须是字符串数组。")
    if not isinstance(verification.get("last_status"), str):
        raise InventoryError("verification.last_status 必须是字符串。")


def _commit_is_ancestor(project_root: Path, ancestor: str) -> bool:
    """先确认对象存在，再判断祖先关系，区分历史重写与普通未提交变化。"""

    exists = run_git(project_root, "cat-file", "-e", f"{ancestor}^{{commit}}", check=False)
    if exists.returncode != 0:
        return False
    result = run_git(project_root, "merge-base", "--is-ancestor", ancestor, "HEAD", check=False)
    return result.returncode == 0


def _committed_changed_paths(project_root: Path, indexed_commit: str) -> list[str]:
    """只读取基线到 HEAD 的 Git 路径元数据，不打开或哈希工作树文件。"""

    result = run_git(
        project_root,
        "diff",
        "--name-only",
        "-z",
        "--no-renames",
        "--no-ext-diff",
        "--no-textconv",
        f"{indexed_commit}..HEAD",
        "--",
    )
    return sorted({path for path in result.stdout.split("\0") if path})


def _path_in_root(path: str, root: str) -> bool:
    """按路径分段匹配模块根，避免 src/a 与 src/ab 错配。"""

    normalized = root.rstrip("/")
    return path == normalized or path.startswith(normalized + "/")


def _changed_paths(previous: dict[str, str], current: dict[str, str]) -> list[str]:
    """通过稳定内容指纹统一识别新增、删除、重命名和未提交修改。"""

    all_paths = set(previous) | set(current)
    return sorted(path for path in all_paths if previous.get(path) != current.get(path))


def _worktree_only_changed_paths(
    previous: list[dict[str, str]],
    current: list[dict[str, str]],
    content_changed: Sequence[str],
) -> list[str]:
    """找出未被合格文件指纹覆盖的 Git 工作树变化，且只暴露路径事实。"""

    def record(change: dict[str, str]) -> tuple[str, str, str]:
        return (change["path"], change["status"], change.get("old_path", ""))

    changed_records = {record(change) for change in previous} ^ {
        record(change) for change in current
    }
    paths: set[str] = set()
    for path, _status, old_path in changed_records:
        paths.add(path)
        if old_path:
            paths.add(old_path)
    return sorted(paths - set(content_changed))


def _excluded_metadata_changed_paths(
    previous: dict[str, dict[str, Any]], current: dict[str, dict[str, Any]]
) -> list[str]:
    """比较不含内容与哈希的排除项元数据，覆盖 Git 状态不变的秘密修改。"""

    all_paths = set(previous) | set(current)
    return sorted(path for path in all_paths if previous.get(path) != current.get(path))


def _structural_change_kind(relative_path: str) -> str | None:
    """按文档列出的机械路径证据识别必须全量重扫的结构入口。"""

    path = PurePosixPath(relative_path)
    lowered_parts = tuple(part.lower() for part in path.parts)
    name = path.name.lower()
    normalized_segments = {
        part.replace("_", "-") for part in lowered_parts[:-1]
    }
    normalized_stem = path.stem.lower().replace("_", "-")
    stem_tokens = set(re.split(r"[^a-z0-9]+", path.stem.lower()))
    path_tokens = set(lowered_parts[:-1]) | stem_tokens
    if path.suffix.lower() == ".proto":
        return "Protocol Buffers schema"
    if (
        normalized_segments & STRUCTURAL_REGISTRY_SEGMENTS
        or normalized_stem in STRUCTURAL_REGISTRY_SEGMENTS
    ):
        return "共享注册表"
    for token, kind in STRUCTURAL_PATH_TOKEN_KINDS.items():
        if token in path_tokens:
            return kind
    if "ci" in normalized_segments:
        return "CI 入口"
    if (
        name in STRUCTURAL_CI_BASENAMES
        or name.startswith("jenkinsfile")
        or name.endswith("jenkinsfile")
    ):
        return "CI 入口"
    playwright_prefix = "playwright.config."
    if (
        name.startswith(playwright_prefix)
        and name[len(playwright_prefix) :] in PLAYWRIGHT_CONFIG_SUFFIXES
    ):
        return "测试入口"
    if name in STRUCTURAL_ENTRY_BASENAMES:
        return "包管理、构建或测试入口"
    if len(lowered_parts) >= 2 and lowered_parts[:2] == (".github", "workflows"):
        return "CI 入口"
    if lowered_parts and lowered_parts[0] in {".circleci", ".buildkite"}:
        return "CI 入口"
    return None


def analyze_changes(project_root: Path | str, state: dict[str, Any]) -> dict[str, Any]:
    """基于已确认状态映射变化；不从文件名臆测业务语义。"""

    validate_state(state)
    root = require_git_root(project_root)
    current = scan_project(root)
    if not _commit_is_ancestor(root, state["indexed_commit"]):
        return {
            "status": "full_rescan_required",
            "mode": "full-rescan",
            "risk_level": "R3",
            "changed_files": [],
            "impacted_modules": [],
            "consumer_files": [],
            "test_candidates": [],
            "reasons": ["indexed_commit 不存在或不是当前 HEAD 的祖先"],
        }

    previous_excluded_metadata = state.get("excluded_file_metadata")
    if previous_excluded_metadata is None:
        return {
            "status": "full_rescan_required",
            "mode": "full-rescan",
            "risk_level": "R3",
            "changed_files": [],
            "impacted_modules": [],
            "consumer_files": [],
            "test_candidates": [],
            "reasons": ["旧状态缺少排除文件元数据，无法安全证明工作树未变化"],
        }

    changed = _changed_paths(
        state["eligible_file_fingerprints"], current["eligible_file_fingerprints"]
    )
    committed_only = sorted(
        set(_committed_changed_paths(root, state["indexed_commit"])) - set(changed)
    )
    excluded_metadata_changed = _excluded_metadata_changed_paths(
        previous_excluded_metadata, current["excluded_file_metadata"]
    )
    worktree_only = _worktree_only_changed_paths(
        state["worktree_changes"], current["worktree_changes"], changed
    )
    if not changed and not committed_only and not worktree_only and not excluded_metadata_changed:
        return {
            "status": "项目档案已是最新",
            "mode": "no-change",
            "risk_level": "R0",
            "changed_files": [],
            "impacted_modules": [],
            "consumer_files": [],
            "test_candidates": [],
            "reasons": [],
        }

    impacted: set[str] = set()
    tests: set[str] = set()
    unmapped: list[str] = []
    for path in changed:
        matched = [
            module for module in state["modules"] if any(_path_in_root(path, item) for item in module["roots"])
        ]
        if len(matched) != 1:
            unmapped.append(path)
            continue
        module = matched[0]
        impacted.add(module["id"])
        tests.update(module.get("tests", []))

    consumers: set[str] = set()
    unmapped_consumers: list[str] = []
    contract_changes: list[dict[str, Any]] = []
    for contract in state["contracts"]:
        if contract["path"] not in changed:
            continue
        contract_changes.append(contract)
        consumers.update(contract["consumers"])
        for consumer in contract["consumers"]:
            if consumer == "UNKNOWN":
                continue
            matched = [
                module
                for module in state["modules"]
                if any(_path_in_root(consumer, item) for item in module["roots"])
            ]
            if len(matched) != 1:
                unmapped_consumers.append(consumer)
                continue
            impacted.add(matched[0]["id"])
            tests.update(matched[0].get("tests", []))

    reasons: list[str] = []
    mode = "incremental"
    risk_level = "R2" if contract_changes else "R1"
    if unmapped:
        mode = "full-rescan"
        risk_level = "R3"
        reasons.append("变化文件无法唯一映射到模块：" + ", ".join(unmapped))
    if any("UNKNOWN" in contract["consumers"] for contract in contract_changes):
        mode = "full-rescan"
        risk_level = "R3"
        reasons.append("公开契约的直接消费者为 UNKNOWN")
    if unmapped_consumers:
        mode = "full-rescan"
        risk_level = "R3"
        reasons.append(
            "公开契约消费者无法唯一映射到模块："
            + ", ".join(sorted(set(unmapped_consumers)))
        )
    if any(contract.get("force_full_rescan") is True for contract in contract_changes):
        mode = "full-rescan"
        risk_level = "R3"
        reasons.append("变化命中状态中声明的强制全量重扫契约")
    structural_changes = [
        (path, kind)
        for path in sorted(set(changed) | set(committed_only) | set(worktree_only))
        if (kind := _structural_change_kind(path))
    ]
    if structural_changes:
        mode = "full-rescan"
        risk_level = "R3"
        reasons.append(
            "文档规定的结构变化要求全量重扫："
            + ", ".join(f"{path}（{kind}）" for path, kind in structural_changes)
        )
    if worktree_only:
        mode = "full-rescan"
        risk_level = "R3"
        reasons.append("Git 工作树变化未被合格文件指纹覆盖：" + ", ".join(worktree_only))
    if committed_only:
        mode = "full-rescan"
        risk_level = "R3"
        reasons.append(
            "Git 已提交变化未被合格文件指纹覆盖：" + ", ".join(committed_only)
        )
    if excluded_metadata_changed:
        mode = "full-rescan"
        risk_level = "R3"
        reasons.append(
            "排除文件元数据变化要求全量重扫：" + ", ".join(excluded_metadata_changed)
        )

    return {
        "status": "full_rescan_required" if mode == "full-rescan" else "changes_detected",
        "mode": mode,
        "risk_level": risk_level,
        "changed_files": sorted(
            set(changed)
            | set(committed_only)
            | set(worktree_only)
            | set(excluded_metadata_changed)
        ),
        "impacted_modules": sorted(impacted),
        "consumer_files": sorted(consumers),
        "test_candidates": sorted(tests),
        "reasons": reasons,
    }


def check_project(project_root: Path | str, state: dict[str, Any]) -> dict[str, Any]:
    """只做轻量一致性检查；无变化时不写任何文件。"""

    return analyze_changes(project_root, state)


def refresh_state_inventory(
    state: dict[str, Any], inventory: dict[str, Any], last_status: str
) -> dict[str, Any]:
    """验证通过后刷新机械字段，同时保留 Codex 已确认的模块与契约事实。"""

    validate_state(state)
    refreshed = dict(state)
    for field in (
        "schema_version",
        "toolkit_version",
        "indexed_commit",
        "project_fingerprint",
        "eligible_file_count",
        "eligible_files",
        "eligible_file_fingerprints",
        "eligible_file_sources",
        "excluded_file_count_by_reason",
        "excluded_files",
        "excluded_file_metadata",
        "worktree_changes",
    ):
        refreshed[field] = inventory[field]
    verification = dict(refreshed["verification"])
    verification["last_status"] = last_status
    refreshed["verification"] = verification
    validate_state(refreshed)
    return refreshed


def require_uninitialized(state_path: Path | str) -> None:
    """有效状态存在时拒绝重复初始化，且不触碰任何项目文件。"""

    path = Path(state_path)
    if not path.exists():
        return
    load_state(path)
    raise InventoryError("项目已存在有效初始化状态，请显式调用 $project-update。")


def _validate_single_block(text: str, start: str, end: str, label: str) -> tuple[int, int]:
    """要求托管标记唯一且有序，异常时不尝试猜测修复。"""

    if text.count(start) != 1 or text.count(end) != 1:
        raise InventoryError(f"{label} 标记必须各出现一次。")
    start_index = text.index(start)
    end_index = text.index(end)
    if start_index >= end_index:
        raise InventoryError(f"{label} 标记顺序异常。")
    return start_index, end_index + len(end)


def validate_archive_markers(text: str) -> None:
    """确认 generated/manual 两个区块唯一、有序且不嵌套。"""

    generated = _validate_single_block(text, GENERATED_START, GENERATED_END, "generated")
    manual = _validate_single_block(text, MANUAL_START, MANUAL_END, "manual")
    if generated[1] > manual[0]:
        raise InventoryError("generated/manual 标记嵌套或顺序异常。")


def replace_generated_block(text: str, generated_content: str) -> str:
    """只替换机器区块，manual 区块及文件其余字节保持原样。"""

    validate_archive_markers(text)
    start_index = text.index(GENERATED_START) + len(GENERATED_START)
    end_index = text.index(GENERATED_END)
    normalized = generated_content.strip("\n")
    return text[:start_index] + "\n" + normalized + "\n" + text[end_index:]


def ensure_agents_managed_block(path: Path | str) -> bool:
    """安全创建或校正根 AGENTS 托管块，块外内容逐字保留。"""

    agents_path = Path(path)
    _reject_symlink_path(agents_path)
    original = agents_path.read_text(encoding="utf-8") if agents_path.exists() else ""
    starts = original.count(AGENTS_START)
    ends = original.count(AGENTS_END)
    desired = f"{AGENTS_START}\n{AGENTS_INCLUDE}\n{AGENTS_END}"
    if starts == 0 and ends == 0:
        separator = "" if not original else ("\n" if original.endswith("\n") else "\n\n")
        updated = original + separator + desired + "\n"
    elif starts == 1 and ends == 1:
        start_index, end_index = _validate_single_block(original, AGENTS_START, AGENTS_END, "AGENTS 托管块")
        updated = original[:start_index] + desired + original[end_index:]
    else:
        raise InventoryError("AGENTS 托管块标记缺失、重复或不成对。")
    if updated == original:
        return False
    _atomic_write_bytes(agents_path, updated.encode("utf-8"))
    return True


def ensure_skill_links(project_root: Path | str, package_root: Path | str) -> list[str]:
    """创建唯一规定的相对链接；同名非目标链接或实体立即报错。"""

    project = require_git_root(project_root)
    package = Path(package_root).resolve()
    validate_package_location(project, package)
    links_root = project / ".agents" / "skills"
    _reject_symlink_path(links_root)
    plans: list[tuple[Path, Path]] = []
    for skill_name in ("project-initialize", "project-update"):
        link = links_root / skill_name
        expected = Path("../../ai_constraints/.agents/skills") / skill_name
        if link.is_symlink():
            if Path(os.readlink(link)) != expected:
                raise InventoryError(f"Skill 链接目标冲突：{link}")
            continue
        if link.exists():
            raise InventoryError(f"Skill 路径已存在且不是规定链接：{link}")
        plans.append((link, expected))
    # 先完成全部冲突预检再落盘，避免第二个目标冲突时留下半套链接。
    links_root.mkdir(parents=True, exist_ok=True)
    _reject_symlink_path(links_root)
    created: list[str] = []
    for link, expected in plans:
        link.symlink_to(expected)
        created.append(link.relative_to(project).as_posix())
    return created


def _write_result(result: dict[str, Any], output: str | None) -> None:
    """CLI 统一使用稳定 JSON；指定输出时不额外打印模拟成功信息。"""

    if output:
        write_json_if_changed(Path(output), result)
    else:
        sys.stdout.buffer.write(stable_json_bytes(result))


def build_parser() -> argparse.ArgumentParser:
    """定义 PRD 约定的 scan、changed、check 三个标准库命令。"""

    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    scan_parser = subparsers.add_parser("scan", help="建立完整第一方文本文件清单")
    scan_parser.add_argument("--project-root", required=True)
    scan_parser.add_argument("--output", required=True)
    for command in ("changed", "check"):
        command_parser = subparsers.add_parser(command, help="计算增量变化或检查状态")
        command_parser.add_argument("--project-root", required=True)
        command_parser.add_argument("--state", required=True)
        command_parser.add_argument("--output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """执行命令并把可操作错误写到标准错误。"""

    arguments = build_parser().parse_args(argv)
    try:
        if arguments.command == "scan":
            result = scan_project(arguments.project_root)
            _write_result(result, arguments.output)
        else:
            state = load_state(arguments.state)
            result = analyze_changes(arguments.project_root, state)
            _write_result(result, arguments.output)
        return 0
    except InventoryError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
