"""Read-only, path-based review gate for Prompt Atelier.

The gate never calls an AI model and never writes to GitHub. It classifies
repository-relative changed paths, rejects private data paths, and tells CI
when the security regression job should run.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable


MAX_FIX_ROUNDS = 2
MAX_REVIEW_ROUNDS = 3
MAX_CHANGED_PATHS = 5000

TRUST_BOUNDARY_RULES = (
    ("CI、工作流与权限", (".github/workflows/", "tools/review_gate.py")),
    ("网络、模型凭据与输入反序列化", ("ai_analyzer.py", "collector.py")),
    ("本地 API、浏览器交互与网络请求", ("ai_prompt_workbench.py", "index.html")),
    ("SQLite、文件路径、备份与恢复", ("storage.py",)),
    ("更新事务与程序替换", ("updater.py", "tools/sync_v065_candidate.ps1", "生成更新清单.py")),
)

DEPENDENCY_FILES = {
    "package.json",
    "package-lock.json",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "poetry.lock",
    "uv.lock",
}
SHELL_SUFFIXES = (".bat", ".cmd", ".ps1", ".sh")
PRIVATE_SUFFIXES = (
    ".sqlite",
    ".sqlite3",
    ".db",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
)
PRIVATE_ROOTS = {
    "datahome",
    "references",
    "reference_images",
    "user_data",
    "backups",
    "_backup",
}
PRIVATE_FILENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "prompt_atelier.config.json",
    "personal_config.json",
    "user_config.json",
}


class ReviewGateError(ValueError):
    """Raised for an unsafe or unusable review input."""


@dataclass(frozen=True)
class ReviewReport:
    paths: tuple[str, ...]
    triggers: dict[str, tuple[str, ...]]
    private_paths: tuple[str, ...]

    @property
    def security_required(self) -> bool:
        return bool(self.triggers)


def normalize_repo_path(value: str) -> str:
    """Return a safe repository-relative POSIX path or reject it."""
    candidate = value.strip().replace("\\", "/")
    if not candidate or "\x00" in candidate:
        raise ReviewGateError("变更路径为空或包含非法字符")
    while candidate.startswith("./"):
        candidate = candidate[2:]
    path = PurePosixPath(candidate)
    if path.is_absolute() or ".." in path.parts:
        raise ReviewGateError(f"变更路径越界: {value!r}")
    if len(candidate) >= 2 and candidate[1] == ":" and candidate[0].isalpha():
        raise ReviewGateError(f"不支持绝对 Windows 路径: {value!r}")
    normalized = path.as_posix()
    if normalized in ("", "."):
        raise ReviewGateError("变更路径必须指向文件")
    return normalized


def is_private_path(path: str) -> bool:
    pure = PurePosixPath(path)
    lowered = path.lower()
    if pure.parts and pure.parts[0].lower() in PRIVATE_ROOTS:
        return True
    if pure.name.lower() in PRIVATE_FILENAMES:
        return True
    return lowered.endswith(PRIVATE_SUFFIXES)


def trust_reasons(path: str) -> list[str]:
    lowered = path.lower()
    reasons = [
        reason
        for reason, prefixes in TRUST_BOUNDARY_RULES
        if any(
            lowered.startswith(prefix.lower()) if prefix.endswith("/") else lowered == prefix.lower()
            for prefix in prefixes
        )
    ]
    if PurePosixPath(path).name.lower() in DEPENDENCY_FILES:
        reasons.append("依赖清单与供应链")
    if lowered.endswith(SHELL_SUFFIXES):
        reasons.append("Shell 与本地命令执行")
    return reasons


def build_report(paths: Iterable[str]) -> ReviewReport:
    normalized = tuple(dict.fromkeys(normalize_repo_path(path) for path in paths if path.strip()))
    if len(normalized) > MAX_CHANGED_PATHS:
        raise ReviewGateError(f"变更文件超过 {MAX_CHANGED_PATHS} 个，请拆分审查范围")
    private_paths = tuple(path for path in normalized if is_private_path(path))
    trigger_map: dict[str, list[str]] = {}
    for path in normalized:
        for reason in trust_reasons(path):
            trigger_map.setdefault(reason, []).append(path)
    return ReviewReport(
        paths=normalized,
        triggers={reason: tuple(paths) for reason, paths in trigger_map.items()},
        private_paths=private_paths,
    )


def render_markdown(report: ReviewReport) -> str:
    lines = [
        "## Prompt Atelier 只读审查门禁",
        "",
        f"- 变更文件：{len(report.paths)} 个",
        f"- 自动返修上限：{MAX_FIX_ROUNDS} 轮；第 {MAX_REVIEW_ROUNDS} 次审查仍有发现时转人工",
        "- 当前阶段不调用模型，不自动评论、推送、合并或发布",
        "- 维护者始终是合并门禁；本工作流只有 contents: read 权限",
        "",
    ]
    if report.private_paths:
        lines.extend(["### 阻止：疑似私有资料或凭据路径", ""])
        lines.extend(f"- `{path}`" for path in report.private_paths[:100])
        lines.extend(["", "请从提交中移除真实数据库、参考图片、个人配置和凭据；不要通过改写路径绕过。", ""])
    if report.triggers:
        lines.extend(["### 需要加强安全回归的信任边界", ""])
        for reason, paths in report.triggers.items():
            lines.append(f"- **{reason}**：" + "、".join(f"`{path}`" for path in paths[:30]))
        lines.extend(["", "CI 将运行安全回归；测试通过不替代人工审查和 Windows/真实资料验收。", ""])
    else:
        lines.extend(["### 路径审查结果", "", "未触及预定义信任边界，条件安全回归跳过；完整 Release validation 仍独立运行。", ""])
    return "\n".join(lines)


def changed_paths_from_git(repo: Path, base: str, head: str) -> list[str]:
    if not base or not head or base.startswith("-") or head.startswith("-"):
        raise ReviewGateError("base/head 必须是非选项的 Git 引用")
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=ACMR", f"{base}...{head}", "--"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ReviewGateError("无法计算 Git 变更范围，请核对 base/head 和 checkout 深度") from exc
    return result.stdout.splitlines()


def write_ci_outputs(report: ReviewReport, markdown: str) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"security_required={'yes' if report.security_required else 'no'}\n")
            handle.write(f"changed_files={len(report.paths)}\n")
            handle.write(f"private_files={len(report.private_paths)}\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(markdown + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--paths-file", help="UTF-8 file containing one changed repository path per line")
    source.add_argument("--base", help="Git base ref for a three-dot diff")
    parser.add_argument("--head", default="HEAD", help="Git head ref (default: HEAD)")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args(argv)
    try:
        if args.paths_file:
            paths = Path(args.paths_file).read_text(encoding="utf-8").splitlines()
        else:
            paths = changed_paths_from_git(Path(args.repo).resolve(), args.base, args.head)
        report = build_report(paths)
        markdown = render_markdown(report)
        write_ci_outputs(report, markdown)
    except (ReviewGateError, OSError, UnicodeError) as exc:
        print(f"REVIEW_GATE_ERROR: {exc}", file=sys.stderr)
        return 2
    print(markdown)
    if report.private_paths:
        print("REVIEW_GATE_BLOCKED: private data or credential paths detected", file=sys.stderr)
        return 2
    print(f"SECURITY_REVIEW_REQUIRED={'yes' if report.security_required else 'no'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
