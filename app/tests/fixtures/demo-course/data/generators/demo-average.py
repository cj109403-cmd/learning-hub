"""Recomputes the answers in demo-average.answers.json from the task's data file."""
import csv
import json
from pathlib import Path

rows = Path(__file__).resolve().parents[1] / "tasks" / "demo-average" / "absorbance.csv"
values = [float(row["absorbance"]) for row in csv.DictReader(rows.open(encoding="utf-8"))]
answers = {"c1": round(sum(values) / len(values), 2), "c2": "abcde"[values.index(max(values))], "c3": len(values)}
print(json.dumps({"card": "demo-average", "checks": answers}, ensure_ascii=False, indent=2))
