"""Audit reported numbers against a manifest or a particular CSV row."""

from __future__ import annotations

import csv
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from cjvmt.store import read_json

CITATION = re.compile(
    r"<!--\s*run=(?P<run>[\w-]+)\s+file=(?P<file>[\w./-]+)\s+key=(?P<key>[^\s]+)\s+value=(?P<value>[^\s]+)\s*-->"
)


def evidence(run: Path, file: str, key: str) -> object:
    if file.endswith(".json"):
        value: object = read_json(run / file)
        for part in key.split("."):
            value = value[part]
        return value
    if file.endswith(".csv"):
        column, match, selected = key.split(":", 2)
        with (run / file).open(encoding="utf-8", newline="") as stream:
            row = next(entry for entry in csv.DictReader(stream) if entry[column] == match)
        return row[selected]
    raise ValueError(f"不支持的证据文件 {file}")


def check(report: Path, results: Path) -> int:
    checked = 0
    for line_number, line in enumerate(report.read_text(encoding="utf-8").splitlines(), 1):
        for item in CITATION.finditer(line):
            reference = item.groupdict()
            run = results / reference["run"]
            manifest = read_json(run / "manifest.json")
            if manifest["status"] != "complete" or manifest["model"]["backend"] == "mock":
                raise ValueError(f"报告第 {line_number} 行引用的 run 无可用真实结果。")
            actual = evidence(run, reference["file"], reference["key"])
            expected = reference["value"]
            try:
                matches = Decimal(str(actual)) == Decimal(expected)
            except InvalidOperation:
                matches = str(actual) == expected
            if not matches or expected not in line[: item.start()]:
                raise ValueError(f"报告第 {line_number} 行的 {reference['key']} 与实际证据不符或未显示。")
            checked += 1
    if not checked:
        raise ValueError("报告中没有可核对的运行数字。")
    return checked


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "reports/final_report.md"
    print(f"已核对 {check(path, root / 'results')} 个实际运行数字。")
