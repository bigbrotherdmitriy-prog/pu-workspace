#!/usr/bin/env python3
"""Validate and score the evidence-backed MVP completion matrix."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


STATUS_SCORE = {
    "verified": 1.0,
    "partial": 0.5,
    "not_verified": 0.0,
    "blocked": 0.0,
    "not_applicable": None,
}
DIMENSIONS = ("implemented", "tested_runtime", "live_provider", "canary", "production")


class MatrixError(ValueError):
    pass


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MatrixError(f"cannot read matrix: {exc}") from exc
    if not isinstance(value, dict):
        raise MatrixError("matrix root must be an object")
    return value


def _evidence_kind(criterion: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [item for item in criterion["evidence"] if item["kind"] == kind]


def validate(matrix: dict[str, Any], repo_root: Path) -> list[str]:
    errors: list[str] = []
    candidate_sha = matrix.get("candidate_sha")
    weights = matrix.get("formula", {}).get("dimension_weights", {})
    if set(weights) != set(DIMENSIONS) or any(not isinstance(weights.get(k), (int, float)) or weights[k] <= 0 for k in DIMENSIONS):
        errors.append("formula.dimension_weights must contain five positive dimensions")
    criteria = matrix.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        return errors + ["criteria must be a non-empty array"]
    seen: set[str] = set()
    mvps: set[int] = set()
    for index, criterion in enumerate(criteria):
        prefix = f"criteria[{index}]"
        if not isinstance(criterion, dict):
            errors.append(f"{prefix} must be an object")
            continue
        cid = criterion.get("id")
        if not isinstance(cid, str) or not cid:
            errors.append(f"{prefix}.id must be non-empty")
        elif cid in seen:
            errors.append(f"duplicate criterion id: {cid}")
        else:
            seen.add(cid)
        mvp = criterion.get("mvp")
        if not isinstance(mvp, int) or not 1 <= mvp <= 7:
            errors.append(f"{prefix}.mvp must be 1..7")
        else:
            mvps.add(mvp)
        if not isinstance(criterion.get("weight"), int) or criterion["weight"] <= 0:
            errors.append(f"{prefix}.weight must be a positive integer")
        dimensions = criterion.get("dimensions")
        if not isinstance(dimensions, dict) or set(dimensions) != set(DIMENSIONS):
            errors.append(f"{prefix}.dimensions must contain exactly {DIMENSIONS}")
            continue
        evidence = criterion.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"{prefix}.evidence must be non-empty")
            continue
        for item in evidence:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("kind"), str):
                errors.append(f"{prefix}.evidence entries require path and kind")
                continue
            if not (repo_root / item["path"]).is_file():
                errors.append(f"{prefix} missing evidence path: {item['path']}")
        for dimension, state in dimensions.items():
            if state not in STATUS_SCORE:
                errors.append(f"{prefix}.{dimension} has invalid state {state!r}")
            if state == "verified":
                matching = _evidence_kind(criterion, dimension)
                if not matching:
                    errors.append(f"{prefix}.{dimension}=verified requires matching evidence kind")
                if dimension != "implemented" and not any(item.get("sha") == candidate_sha for item in matching):
                    errors.append(f"{prefix}.{dimension}=verified requires evidence at candidate_sha")
        if dimensions["tested_runtime"] == "verified" and dimensions["implemented"] != "verified":
            errors.append(f"{prefix} runtime cannot be verified when implementation is not verified")
        if dimensions["production"] == "verified" and dimensions["canary"] != "verified":
            errors.append(f"{prefix} production cannot be verified without canary")
    if mvps != set(range(1, 8)):
        errors.append(f"criteria must cover MVP1..MVP7; got {sorted(mvps)}")
    decisions = matrix.get("decisions", {})
    if decisions.get("limited_pilot", {}).get("status") == "PASS":
        if any(item["dimensions"]["tested_runtime"] != "verified" for item in criteria if item["weight"] == 5):
            errors.append("limited pilot cannot be PASS while mandatory runtime criteria are not exact-SHA verified")
    if decisions.get("full_tz", {}).get("status") == "PASS":
        if any(
            state not in {"verified", "not_applicable"}
            for item in criteria
            for state in item["dimensions"].values()
        ):
            errors.append("full TZ cannot be PASS while an applicable dimension is not verified")
    return errors


def _score(criteria: list[dict[str, Any]], dimension_weights: dict[str, float], selected: tuple[str, ...]) -> float:
    numerator = 0.0
    denominator = 0.0
    for criterion in criteria:
        criterion_weight = float(criterion["weight"])
        for dimension in selected:
            value = STATUS_SCORE[criterion["dimensions"][dimension]]
            if value is None:
                continue
            weight = criterion_weight * float(dimension_weights[dimension])
            numerator += weight * value
            denominator += weight
    return 0.0 if denominator == 0 else round(100.0 * numerator / denominator, 1)


def summarize(matrix: dict[str, Any]) -> dict[str, Any]:
    criteria = matrix["criteria"]
    weights = matrix["formula"]["dimension_weights"]
    pilot_dimensions = tuple(matrix["formula"]["pilot_dimensions"])
    result: dict[str, Any] = {
        "candidate_sha": matrix["candidate_sha"],
        "criterion_count": len(criteria),
        "formula": matrix["formula"],
        "pilot_readiness_percent": _score(criteria, weights, pilot_dimensions),
        "full_tz_readiness_percent": _score(criteria, weights, DIMENSIONS),
        "decisions": matrix["decisions"],
        "by_mvp": {},
    }
    for mvp in range(1, 8):
        subset = [item for item in criteria if item["mvp"] == mvp]
        result["by_mvp"][str(mvp)] = {
            "criteria": len(subset),
            "pilot_readiness_percent": _score(subset, weights, pilot_dimensions),
            "full_tz_readiness_percent": _score(subset, weights, DIMENSIONS),
            "blocking_criteria": [item["id"] for item in subset if "blocked" in item["dimensions"].values()],
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("matrix", nargs="?", default="docs/audits/v7-mvp-completion-matrix.json")
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args()
    repo_root = Path(args.repo_root).resolve()
    matrix = _load((repo_root / args.matrix).resolve())
    errors = validate(matrix, repo_root)
    if errors:
        print(json.dumps({"status": "FAIL", "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"status": "PASS", **summarize(matrix)}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
