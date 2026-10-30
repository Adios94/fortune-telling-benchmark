"""Run with python -m ftbench; installation is unnecessary."""

import argparse
import json
import math
import sys
from pathlib import Path

from .core import ValidationError, evaluate, load_cases, load_predictions
from .runner import run


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def positive_float(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be finite and positive")
    return number


def temperature(value):
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 2:
        raise argparse.ArgumentTypeError("must be finite and in [0, 2]")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description="Synthetic-first, strict four-choice benchmark starter")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="validate case JSONL without network access")
    validate.add_argument("--cases", required=True)
    score = sub.add_parser("score", help="score offline; absent predictions remain in denominator")
    score.add_argument("--cases", required=True)
    score.add_argument("--predictions", required=True)
    score.add_argument("--output", help="write JSON report to a NEW file; stdout otherwise")
    execute = sub.add_parser("run", help="plan only unless --execute is explicitly provided")
    execute.add_argument("--cases", required=True)
    execute.add_argument("--endpoint", required=True, help="full HTTPS .../chat/completions URL")
    execute.add_argument("--model", required=True)
    execute.add_argument("--output-dir", required=True, help="must not exist for an actual run")
    execute.add_argument("--api-key-env", default="BENCH_API_KEY")
    execute.add_argument("--temperature", type=temperature, default=0.0)
    execute.add_argument("--max-tokens", type=positive_int, default=32)
    execute.add_argument("--timeout", type=positive_float, default=60.0)
    execute.add_argument("--execute", action="store_true", help="allow requests that may cost money")
    execute.add_argument("--allow-local-http", action="store_true", help="loopback-only development override")
    args = parser.parse_args(argv)
    try:
        cases = load_cases(args.cases)
        if args.command == "validate":
            result = {"valid": True, "cases": len(cases), "synthetic_cases": sum(c["synthetic"] for c in cases)}
        elif args.command == "score":
            result = evaluate(cases, load_predictions(args.predictions))
        else:
            result = run(args, cases)
        formatted = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.command == "score" and args.output:
            with Path(args.output).open("x", encoding="utf-8") as handle:
                handle.write(formatted)
        else:
            sys.stdout.write(formatted)
        return 0
    except (ValidationError, OSError) as exc:
        # Avoid server bodies, request headers, and environment-variable values in logs.
        if isinstance(exc, ValidationError):
            print(f"error: {exc}", file=sys.stderr)
        else:
            print(f"error: local file operation failed ({type(exc).__name__})", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
