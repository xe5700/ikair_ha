#!/usr/bin/env python3
"""版本号自动提升 & git 提交工具

用法:
  python bump_version.py patch    # 1.0.1 → 1.0.2（默认）
  python bump_version.py minor    # 1.0.1 → 1.1.0
  python bump_version.py major    # 1.0.1 → 2.0.0
  python bump_version.py show     # 显示当前版本

示例:
  python bump_version.py patch -m "fix: 修复重连超时问题"
  python bump_version.py minor -a  # 自动收集未提交的变更描述
"""

import json
import re
import subprocess
import sys
from pathlib import Path


MANIFEST = Path(__file__).parent / "custom_components" / "ikair" / "manifest.json"


def get_version() -> str:
    with open(MANIFEST) as f:
        return json.load(f)["version"]


def set_version(version: str) -> None:
    data = json.loads(MANIFEST.read_text())
    data["version"] = version
    MANIFEST.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    )


def bump(part: str) -> str:
    major, minor, patch = map(int, get_version().split("."))
    if part == "major":
        major += 1
        minor = patch = 0
    elif part == "minor":
        minor += 1
        patch = 0
    else:
        patch += 1
    return f"{major}.{minor}.{patch}"


def get_pending_changes() -> str:
    """从 git log 收集未推送的提交作为 commit message"""
    result = subprocess.run(
        ["git", "log", "--oneline", "--no-decorate", "origin/master..HEAD"],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip().split("\n")[-1]
    return ""


def main():
    part = "patch"
    msg = ""
    auto_msg = False

    args = sys.argv[1:]
    for a in args:
        if a in ("patch", "minor", "major", "show"):
            part = a
        elif a == "-a":
            auto_msg = True
        elif a.startswith("-m"):
            idx = args.index(a)
            if idx + 1 < len(args):
                msg = args[idx + 1]

    if part == "show":
        print(get_version())
        return

    if auto_msg and not msg:
        msg = get_pending_changes()

    if not msg:
        msg = f"bump version to {bump(part)}"

    old_ver = get_version()
    new_ver = bump(part)
    set_version(new_ver)

    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-m", msg], check=True)
    subprocess.run(["git", "tag", f"v{new_ver}"], check=True)

    print(f"✅ {old_ver} → {new_ver}")
    print(f"   commit: {msg}")
    print(f"   tag: v{new_ver}")
    print("  现在执行 git push --tags 推送到 GitHub")


if __name__ == "__main__":
    main()
