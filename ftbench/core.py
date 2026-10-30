"""Strict JSONL validation and offline scoring; only Python's standard library."""

import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

CHOICES = ("A", "B", "C", "D")
TRACKS = ("synthetic_pipeline", "competition_agreement", "rule_reasoning", "real_event")
OUTCOMES = ("correct", "incorrect", "invalid", "refused", "error", "missing")
CASE_FIELDS = {"id", "year", "track", "question", "choices", "answer", "source_id", "synthetic"}
PRED_FIELDS = {"id", "response", "status", "error_code", "latency_seconds"}
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class ValidationError(ValueError):
    """An input is ambiguous or does not conform to the documented schema."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValidationError("non-finite JSON number")


def read_jsonl(path):
    """Reject blank lines, duplicate keys, NaN, and non-object records."""
    rows = []
    try:
        with Path(path).open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                try:
                    if not line.strip():
                        raise ValidationError("blank line")
                    row = json.loads(line, object_pairs_hook=_unique_object,
                                     parse_constant=_reject_constant)
                    if not isinstance(row, dict):
                        raise ValidationError("record must be an object")
                except (ValueError, TypeError) as exc:
                    raise ValidationError(f"{path}:{line_number}: invalid JSONL record") from exc
                rows.append(row)
    except UnicodeError as exc:
        raise ValidationError(f"{path}: expected UTF-8") from exc
    return rows


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _check_ids(rows):
    seen = set()
    for number, row in enumerate(rows, 1):
        identifier = row.get("id")
        if not isinstance(identifier, str) or not ID_PATTERN.fullmatch(identifier):
            raise ValidationError(f"record {number}: invalid id")
        if identifier in seen:
            raise ValidationError(f"record {number}: duplicate id")
        seen.add(identifier)


def load_cases(path):
    rows = read_jsonl(path)
    if not rows:
        raise ValidationError("case file must not be empty")
    _check_ids(rows)
    for number, row in enumerate(rows, 1):
        prefix = f"case record {number}"
        if set(row) != CASE_FIELDS:
            raise ValidationError(f"{prefix}: missing or unknown fields")
        if type(row["year"]) is not int or not 1900 <= row["year"] <= 2100:
            raise ValidationError(f"{prefix}: year must be an integer in 1900..2100")
        if row["track"] not in TRACKS:
            raise ValidationError(f"{prefix}: unknown track")
        if not _nonempty(row["question"]) or not _nonempty(row["source_id"]):
            raise ValidationError(f"{prefix}: question and source_id must be nonempty strings")
        choices = row["choices"]
        if not isinstance(choices, dict) or set(choices) != set(CHOICES):
            raise ValidationError(f"{prefix}: choices must contain exactly A, B, C, D")
        if not all(_nonempty(value) for value in choices.values()):
            raise ValidationError(f"{prefix}: each choice must be a nonempty string")
        if not isinstance(row["answer"], str) or row["answer"] not in CHOICES:
            raise ValidationError(f"{prefix}: answer must be exactly A, B, C, or D")
        if type(row["synthetic"]) is not bool:
            raise ValidationError(f"{prefix}: synthetic must be boolean")
        if row["track"] == "synthetic_pipeline" and not row["synthetic"]:
            raise ValidationError(f"{prefix}: synthetic_pipeline requires synthetic=true")
    return rows


def load_predictions(path):
    rows = read_jsonl(path)
    _check_ids(rows)
    for number, row in enumerate(rows, 1):
        prefix = f"prediction record {number}"
        if not {"id", "response", "status"} <= set(row) or set(row) - PRED_FIELDS:
            raise ValidationError(f"{prefix}: missing or unknown fields")
        status = row["status"]
        if status not in ("ok", "refusal", "error"):
            raise ValidationError(f"{prefix}: unknown status")
        if status == "ok" and not isinstance(row["response"], str):
            raise ValidationError(f"{prefix}: ok response must be a string")
        if status != "ok" and row["response"] is not None:
            raise ValidationError(f"{prefix}: refusal/error response must be null")
        if "error_code" in row and (status != "error" or not _nonempty(row["error_code"])):
            raise ValidationError(f"{prefix}: error_code is only allowed on errors")
        if "latency_seconds" in row:
            value = row["latency_seconds"]
            if (type(value) not in (float, int) or value < 0
                    or (isinstance(value, float) and not math.isfinite(value))):
                raise ValidationError(f"{prefix}: invalid latency_seconds")
    return rows


def classify(case, prediction):
    if prediction is None:
        return "missing"
    if prediction["status"] == "refusal":
        return "refused"
    if prediction["status"] == "error":
        return "error"
    response = prediction["response"].strip()
    if response == "REFUSE":
        return "refused"
    if response not in CHOICES:
        return "invalid"
    return "correct" if response == case["answer"] else "incorrect"


def summarize(counts):
    result = {key: counts[key] for key in OUTCOMES}
    result["total"] = sum(result.values())
    result["answered"] = result["correct"] + result["incorrect"]
    result["accuracy"] = result["correct"] / result["total"] if result["total"] else 0.0
    result["answer_coverage"] = result["answered"] / result["total"] if result["total"] else 0.0
    return result


def evaluate(cases, predictions):
    """Every case stays in the denominator, including absent predictions."""
    _check_ids(cases)
    _check_ids(predictions)
    case_ids = {row["id"] for row in cases}
    if any(row["id"] not in case_ids for row in predictions):
        raise ValidationError("predictions contain unknown case ids")
    indexed = {row["id"]: row for row in predictions}
    total = Counter()
    years = defaultdict(Counter)
    tracks = defaultdict(Counter)
    details = []
    for case in cases:
        outcome = classify(case, indexed.get(case["id"]))
        total[outcome] += 1
        years[str(case["year"])][outcome] += 1
        tracks[case["track"]][outcome] += 1
        details.append({"id": case["id"], "outcome": outcome})
    return {
        "schema_version": 1,
        "metric": "exact_choice_agreement_all_cases",
        "answer_normalization": "strip_only_case_sensitive",
        "summary": summarize(total),
        "by_year": {key: summarize(value) for key, value in sorted(years.items())},
        "by_track": {key: summarize(value) for key, value in sorted(tracks.items())},
        "per_case": details,
    }
