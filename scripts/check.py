#!/usr/bin/env python
"""本地 / CI 统一质量门禁入口。

按顺序执行 ruff（lint）与 pytest（测试），无论前一步是否失败都会全部跑完，
最后汇总给出一次性的通过 / 失败结论，并以进程退出码反映结果，便于机械化验证。

用法：
    python scripts/check.py            # 运行 lint + 测试
    python scripts/check.py --lint     # 仅运行 lint
    python scripts/check.py --test     # 仅运行测试

退出码：
    0  全部通过
    1  存在失败
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run_step(name: str, cmd: list[str]) -> bool:
    """执行单个检查步骤，返回是否通过。"""
    print(f"\n{'=' * 70}\n>> {name}: {' '.join(cmd)}\n{'=' * 70}", flush=True)
    start = time.perf_counter()
    result = subprocess.run(cmd, cwd=ROOT)
    elapsed = time.perf_counter() - start
    passed = result.returncode == 0
    status = "PASS" if passed else "FAIL"
    print(f"\n[{status}] {name} (exit={result.returncode}, {elapsed:.1f}s)", flush=True)
    return passed


def main() -> int:
    parser = argparse.ArgumentParser(description="质量门禁：ruff + pytest")
    parser.add_argument("--lint", action="store_true", help="仅运行 lint")
    parser.add_argument("--test", action="store_true", help="仅运行测试")
    args = parser.parse_args()

    run_lint = args.lint or not args.test
    run_test = args.test or not args.lint

    py = sys.executable
    results: dict[str, bool] = {}

    if run_lint:
        results["lint (ruff)"] = run_step("Lint", [py, "-m", "ruff", "check", "."])
    if run_test:
        results["test (pytest)"] = run_step("Test", [py, "-m", "pytest", "-q"])

    print(f"\n{'=' * 70}\n汇总 / Summary\n{'=' * 70}")
    for step, passed in results.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {step}")

    all_passed = all(results.values())
    print(f"\n结果 / Result: {'ALL PASS' if all_passed else 'FAILED'}\n")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
