#!/usr/bin/env python3
"""验证 AI Constraints 规则、Skills、状态和仓库卫生。"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence

import project_inventory


REQUIRED_FILES = (
    "AGENTS.md",
    "README.md",
    ".gitignore",
    "codex/入口.md",
    "codex/架构.md",
    "codex/模块/_模板.md",
    "codex/高风险.md",
    "codex/协作.md",
    "codex/任务池.md",
    "scripts/verify.sh",
    "scripts/verify.py",
    "scripts/project_inventory.py",
    ".agents/skills/project-initialize/SKILL.md",
    ".agents/skills/project-initialize/agents/openai.yaml",
    ".agents/skills/project-initialize/references/scan-contract.md",
    ".agents/skills/project-initialize/references/output-contract.md",
    ".agents/skills/project-update/SKILL.md",
    ".agents/skills/project-update/agents/openai.yaml",
    ".agents/skills/project-update/references/update-contract.md",
    ".agents/skills/project-update/references/escalation-rules.md",
)

SKILL_METADATA = {
    "project-initialize": "首次全仓扫描并生成 AI Constraints 项目档案",
    "project-update": "按代码变化增量同步 AI Constraints 项目档案",
}

MODULE_HEADINGS = (
    "## 目标与边界",
    "## 核心不变量与验证",
    "## 公开契约与直接消费者",
    "## 修改风险",
    "## 验证命令",
)

# 用模式封锁已经废弃的规则族，避免重新维护一长串墓碑文件名。
OBSOLETE_REFERENCE_PATTERN = re.compile(
    r"(?:^|/)codex/(?:工作流(?:/[^\s`]*)?|规范|影响分析|自查清单|子代理|坑点|模式)\.md"
)
PLACEHOLDER_PATTERN = re.compile(
    r"TODO|\[(?:模块名|项目目标|一句话|语言、框架|主启动文件|负责什么|接口、事件|项目已有的精确命令)|\{模块(?:名称|名)\}"
)
ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?:^|[\s`(])(?:/(?:Users|home|private|tmp|var|opt)/|[A-Za-z]:\\)", re.MULTILINE
)


class VerificationError(RuntimeError):
    """表示工作包结构无法安全验证。"""


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """执行只读 Git 检查并保留失败输出。"""

    try:
        return subprocess.run(
            ["git", *args], cwd=root, text=True, capture_output=True, check=False
        )
    except FileNotFoundError as exc:
        raise VerificationError("无法执行 git：系统未安装 Git。") from exc


def locate_roots(package_root: Path | str) -> tuple[Path, Path, str]:
    """同时支持独立仓库与项目根 ai_constraints/ 嵌入模式。"""

    package = Path(package_root).resolve()
    if package.name != "ai_constraints":
        raise VerificationError(f"工作包目录名必须是 ai_constraints：{package}")
    result = _run_git(package, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        raise VerificationError(f"工作包不在 Git 项目中：{package}")
    project = Path(result.stdout.strip()).resolve()
    if package == project:
        return package, project, "standalone"
    if package == project / "ai_constraints":
        return package, project, "embedded"
    raise VerificationError(
        f"工作包位置错误：当前为 {package}，应为 {project / 'ai_constraints'}"
    )


def _read_utf8(path: Path) -> str:
    """权威规则必须是可重复解析的 UTF-8 文本。"""

    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise VerificationError(f"无法读取 UTF-8 文件 {path}：{exc}") from exc


def _iter_rule_files(package_root: Path) -> Iterable[Path]:
    """集中枚举需要空白与引用检查的规则、Skill 和脚本。"""

    for relative in REQUIRED_FILES:
        path = package_root / relative
        if path.suffix in {".md", ".py", ".sh", ".yaml"}:
            yield path
    module_root = package_root / "codex" / "模块"
    if module_root.is_dir():
        for path in sorted(module_root.glob("*.md")):
            if path not in {package_root / item for item in REQUIRED_FILES}:
                yield path


def _parse_frontmatter(path: Path) -> dict[str, str]:
    """只解析 Skills 允许的扁平 frontmatter，拒绝额外元数据与模糊 YAML。"""

    lines = _read_utf8(path).splitlines()
    if not lines or lines[0] != "---":
        raise VerificationError(f"Skill 缺少 frontmatter：{path}")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise VerificationError(f"Skill frontmatter 未闭合：{path}") from exc
    metadata: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" not in line:
            raise VerificationError(f"Skill frontmatter 行无法解析：{path}：{line}")
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"\'')
    if set(metadata) != {"name", "description"}:
        raise VerificationError(f"Skill frontmatter 只能包含 name 和 description：{path}")
    return metadata


def _validate_skills(package_root: Path) -> list[str]:
    """验证名称、描述、显式调用策略与 UI 元数据。"""

    errors: list[str] = []
    for skill_name, description_prefix in SKILL_METADATA.items():
        skill_root = package_root / ".agents" / "skills" / skill_name
        try:
            metadata = _parse_frontmatter(skill_root / "SKILL.md")
        except VerificationError as exc:
            errors.append(str(exc))
            continue
        if metadata["name"] != skill_name:
            errors.append(f"Skill name 错误：{skill_name}")
        if (
            not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", metadata["name"])
            or len(metadata["name"]) > 64
        ):
            errors.append(f"Skill name 不符合 hyphen-case 或长度上限：{skill_name}")
        if not metadata["description"].startswith(description_prefix):
            errors.append(f"Skill description 未描述规定能力：{skill_name}")
        if (
            not metadata["description"]
            or len(metadata["description"]) > 1024
            or "<" in metadata["description"]
            or ">" in metadata["description"]
        ):
            errors.append(f"Skill description 格式无效：{skill_name}")
        yaml_text = _read_utf8(skill_root / "agents" / "openai.yaml")
        if not re.search(r"(?m)^policy:\n  allow_implicit_invocation: false\s*$", yaml_text):
            errors.append(f"Skill 未禁止隐式调用：{skill_name}")
        if f"${skill_name}" not in yaml_text:
            errors.append(f"Skill default_prompt 未显式提及 ${skill_name}")
        for field in ("display_name", "short_description", "default_prompt"):
            if not re.search(rf"(?m)^  {field}: \"[^\"]+\"$", yaml_text):
                errors.append(f"Skill openai.yaml 缺少或未引用字段：{skill_name}.{field}")
    return errors


def _validate_markdown_references(package_root: Path) -> list[str]:
    """验证相对 Markdown 链接和嵌入项目路径，避免文档指向不存在文件。"""

    errors: list[str] = []
    for path in _iter_rule_files(package_root):
        if path.suffix != ".md":
            continue
        text = _read_utf8(path)
        if OBSOLETE_REFERENCE_PATTERN.search(text):
            errors.append(f"发现废弃规则引用：{path.relative_to(package_root)}")
        for reference in re.findall(r"\[[^\]]+\]\(([^)]+\.md)\)", text):
            if "://" in reference or "{" in reference:
                continue
            target = (path.parent / reference).resolve()
            if not target.is_file():
                errors.append(f"Markdown 引用不存在：{path.relative_to(package_root)} -> {reference}")
        for reference in re.findall(r"`(ai_constraints/[^`\s]+)`", text):
            if "{" in reference or "$" in reference:
                continue
            target = package_root / reference.removeprefix("ai_constraints/")
            # 项目状态是初始化成功后才生成的合同产物，独立工作包中允许尚不存在。
            if reference == "ai_constraints/codex/项目状态.json" and not target.exists():
                continue
            if not target.exists():
                errors.append(f"工作包内部引用不存在：{reference}")
    return errors


def _validate_line_limits(package_root: Path) -> list[str]:
    """限制固定上下文与模板体积，保持 R0/R1 快速路径轻量。"""

    errors: list[str] = []
    limits = {"AGENTS.md": 15, "codex/入口.md": 60, "codex/模块/_模板.md": 80}
    for relative, limit in limits.items():
        count = len(_read_utf8(package_root / relative).splitlines())
        if count > limit:
            errors.append(f"{relative} 有 {count} 行，超过上限 {limit}")
    fixed_context = _read_utf8(package_root / "AGENTS.md") + _read_utf8(
        package_root / "codex" / "入口.md"
    )
    if len(fixed_context) > 1452:
        errors.append(f"固定上下文有 {len(fixed_context)} 个字符，超过基线 1452")
    return errors


def _validate_archive(path: Path, allow_placeholders: bool) -> list[str]:
    """验证档案必填骨架与双托管区块。"""

    text = _read_utf8(path)
    errors: list[str] = []
    try:
        project_inventory.validate_archive_markers(text)
    except project_inventory.InventoryError as exc:
        errors.append(f"{path}：{exc}")
    for heading in MODULE_HEADINGS:
        if heading not in text.splitlines():
            errors.append(f"{path} 缺少章节 {heading}")
    if not allow_placeholders and PLACEHOLDER_PATTERN.search(text):
        errors.append(f"{path} 存在占位符或 TODO")
    if not allow_placeholders and ABSOLUTE_PATH_PATTERN.search(text):
        errors.append(f"{path} 包含绝对路径")
    if len(text.splitlines()) > 80:
        errors.append(f"{path} 超过模块档案 80 行硬上限")
    return errors


def _validate_archives(package_root: Path, initialized: bool) -> list[str]:
    """验证模板、架构和初始化后所有唯一模块档案。"""

    errors = _validate_archive(package_root / "codex" / "模块" / "_模板.md", True)
    architecture = package_root / "codex" / "架构.md"
    architecture_text = _read_utf8(architecture)
    try:
        project_inventory.validate_archive_markers(architecture_text)
    except project_inventory.InventoryError as exc:
        errors.append(f"{architecture}：{exc}")
    if initialized and PLACEHOLDER_PATTERN.search(architecture_text):
        errors.append("初始化后的 codex/架构.md 存在占位符或 TODO")
    if initialized and ABSOLUTE_PATH_PATTERN.search(architecture_text):
        errors.append("初始化后的 codex/架构.md 包含绝对路径")
    for archive in sorted((package_root / "codex" / "模块").glob("*.md")):
        if archive.name != "_模板.md":
            errors.extend(_validate_archive(archive, False))
    return errors


def _walk_strings(value: Any) -> Iterable[str]:
    """递归枚举状态字符串，用于发现绝对路径和秘密路径泄漏。"""

    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)


def _validate_commands(state: dict[str, Any]) -> list[str]:
    """拒绝空命令和明显占位命令，不猜测目标项目的测试框架。"""

    errors: list[str] = []
    verification = state.get("verification", {})
    for field in ("fast_commands", "full_commands"):
        commands = verification.get(field)
        if not isinstance(commands, list) or not commands:
            errors.append(f"项目状态 verification.{field} 必须包含真实命令")
            continue
        for command in commands:
            if not isinstance(command, str) or not command.strip():
                errors.append(f"项目状态 verification.{field} 含空命令")
            elif re.search(r"TODO|UNKNOWN|\[(?:命令|项目已有)|<command>", command, re.I):
                errors.append(f"项目状态含占位命令：{command}")
    if verification.get("last_status") not in {"passed", "failed"}:
        errors.append("初始化状态的 verification.last_status 必须是 passed 或 failed")
    return errors


def _validate_state(package_root: Path, project_root: Path, mode: str) -> list[str]:
    """验证状态 schema、稳定 JSON、相对路径、档案唯一性与契约证据。"""

    state_path = package_root / "codex" / "项目状态.json"
    if not state_path.exists():
        return ["嵌入模式缺少 codex/项目状态.json"] if mode == "embedded" else []
    errors: list[str] = []
    try:
        raw = state_path.read_bytes()
        state = json.loads(raw.decode("utf-8"))
        project_inventory.validate_state(state)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, project_inventory.InventoryError) as exc:
        return [f"项目状态无效：{exc}"]
    if raw != project_inventory.stable_json_bytes(state):
        errors.append("项目状态 JSON 未使用稳定排序、两空格缩进和末尾换行")
    for value in _walk_strings(state):
        pure = PurePosixPath(value)
        if pure.is_absolute() or re.search(r"(?:^|\s)/(?:Users|home)/", value):
            errors.append(f"项目状态包含绝对路径：{value}")
            break
    eligible = set(state["eligible_files"])
    if any(path == "ai_constraints" or path.startswith("ai_constraints/") for path in eligible):
        errors.append("项目状态错误纳入 ai_constraints/ 工作包自身")
    for module in state["modules"]:
        archive = project_root / module["archive"]
        if not archive.is_file():
            errors.append(f"模块档案不存在：{module['archive']}")
    for contract in state["contracts"]:
        if contract["path"] not in eligible:
            errors.append(f"公开契约路径不在合格清单：{contract['path']}")
        for consumer in contract["consumers"]:
            if consumer != "UNKNOWN" and consumer not in eligible:
                errors.append(f"直接消费者路径不在合格清单：{consumer}")
    errors.extend(_validate_commands(state))
    return errors


def _validate_embedded_integration(package_root: Path, project_root: Path, mode: str) -> list[str]:
    """验证初始化后根托管块和两个相对 Skill 链接。"""

    if mode != "embedded":
        return []
    errors: list[str] = []
    agents_path = project_root / "AGENTS.md"
    if not agents_path.is_file():
        errors.append("嵌入模式缺少项目根 AGENTS.md")
    else:
        text = _read_utf8(agents_path)
        try:
            start, end = project_inventory._validate_single_block(
                text,
                project_inventory.AGENTS_START,
                project_inventory.AGENTS_END,
                "AGENTS 托管块",
            )
            expected = (
                f"{project_inventory.AGENTS_START}\n"
                f"{project_inventory.AGENTS_INCLUDE}\n"
                f"{project_inventory.AGENTS_END}"
            )
            if text[start:end] != expected:
                errors.append("项目根 AGENTS.md 托管块内容不正确")
        except project_inventory.InventoryError as exc:
            errors.append(f"项目根 AGENTS.md 托管块异常：{exc}")
    for skill_name in SKILL_METADATA:
        link = project_root / ".agents" / "skills" / skill_name
        expected = Path("../../ai_constraints/.agents/skills") / skill_name
        if not link.is_symlink() or Path(os.readlink(link)) != expected:
            errors.append(f"Skill 链接缺失或目标错误：{link.relative_to(project_root)}")
    return errors


def _validate_repository_hygiene(package_root: Path, project_root: Path) -> list[str]:
    """检查行尾空白、.DS_Store 跟踪和 Git diff 空白错误。"""

    errors: list[str] = []
    for path in _iter_rule_files(package_root):
        for number, line in enumerate(_read_utf8(path).splitlines(), 1):
            if line.endswith((" ", "\t")):
                errors.append(f"行尾空白：{path.relative_to(package_root)}:{number}")
    tracked = _run_git(project_root, "ls-files", "-z")
    if tracked.returncode != 0:
        errors.append("无法读取 Git 跟踪文件")
    else:
        for relative in tracked.stdout.split("\0"):
            if relative and PurePosixPath(relative).name == ".DS_Store":
                errors.append(f".DS_Store 仍被 Git 跟踪：{relative}")
    # 未暂存和已暂存边界各检查一次，职责集中在本函数，不在 shell 重复。
    for args in (("diff", "--check"), ("diff", "--cached", "--check")):
        result = _run_git(project_root, *args)
        if result.returncode != 0:
            errors.append((result.stdout or result.stderr).strip())
    return errors


def verify_package(package_root: Path | str) -> list[str]:
    """返回全部可操作错误；空列表表示工作包门禁通过。"""

    try:
        package, project, mode = locate_roots(package_root)
    except VerificationError as exc:
        return [str(exc)]
    errors: list[str] = []
    for relative in REQUIRED_FILES:
        if not (package / relative).is_file():
            errors.append(f"缺少必需文件：{relative}")
    if errors:
        return errors
    initialized = (package / "codex" / "项目状态.json").exists()
    errors.extend(_validate_skills(package))
    errors.extend(_validate_markdown_references(package))
    errors.extend(_validate_line_limits(package))
    errors.extend(_validate_archives(package, initialized))
    errors.extend(_validate_state(package, project, mode))
    errors.extend(_validate_embedded_integration(package, project, mode))
    errors.extend(_validate_repository_hygiene(package, project))
    return errors


def build_parser() -> argparse.ArgumentParser:
    """提供可在独立与嵌入模式复用的单一入口。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-root", default=Path(__file__).resolve().parents[1])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """打印真实验证结果并返回适合 CI 的状态码。"""

    arguments = build_parser().parse_args(argv)
    errors = verify_package(arguments.package_root)
    if errors:
        for error in errors:
            print(f"验证失败：{error}", file=sys.stderr)
        return 1
    print("AI Constraints 验证通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
