import argparse
import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from ftbench.__main__ import main
from ftbench.core import ValidationError, evaluate, load_cases, load_predictions
from ftbench.runner import (NoRedirect, messages_for, prediction_from_response,
                            run, validate_endpoint)

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "data/synthetic/cases.jsonl"
PREDS = ROOT / "data/synthetic/predictions.jsonl"
SECRET = "test-only-secret-not-a-real-credential-928734"


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cases = load_cases(CASES)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, rows, name="input.jsonl"):
        path = self.root / name
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        return path

    def args(self, **changes):
        data = dict(cases=str(CASES), endpoint="https://provider.example/v1/chat/completions",
                    model="mock-model", temperature=0.0, max_tokens=32, timeout=2.0,
                    output_dir=str(self.root / "run"), api_key_env="BENCH_TEST_KEY",
                    allow_local_http=False, execute=False)
        data.update(changes)
        return argparse.Namespace(**data)

    def test_fixture_counts_and_year_totals(self):
        report = evaluate(self.cases, load_predictions(PREDS))
        self.assertEqual(report["summary"], dict(correct=4, incorrect=1, invalid=1, refused=1,
                                                error=0, missing=1, total=8, answered=5,
                                                accuracy=0.5, answer_coverage=0.625))
        self.assertEqual(list(report["by_year"]), ["2022", "2023", "2024", "2025"])
        self.assertTrue(all(year["total"] == 2 for year in report["by_year"].values()))
        self.assertEqual(report["by_track"]["synthetic_pipeline"]["total"], 8)

    def test_empty_predictions_all_missing(self):
        predictions = load_predictions(self.write([]))
        summary = evaluate(self.cases, predictions)["summary"]
        self.assertEqual(summary["missing"], 8)
        self.assertEqual(summary["accuracy"], 0)

    def test_case_file_cannot_be_empty(self):
        with self.assertRaises(ValidationError):
            load_cases(self.write([]))

    def test_duplicate_case_ids_rejected(self):
        with self.assertRaises(ValidationError):
            load_cases(self.write([self.cases[0], self.cases[0]]))

    def test_duplicate_prediction_ids_rejected(self):
        row = {"id": self.cases[0]["id"], "response": "A", "status": "ok"}
        with self.assertRaises(ValidationError):
            load_predictions(self.write([row, row]))

    def test_unknown_prediction_ids_rejected(self):
        with self.assertRaises(ValidationError):
            evaluate(self.cases, [{"id": "unknown", "response": "A", "status": "ok"}])

    def test_jsonl_rejects_ambiguous_or_malformed_records(self):
        for text in ('\n', '[]\n', '{"id":"x","id":"y"}\n', '{"id":NaN}\n', '{bad}\n'):
            with self.subTest(text=text):
                path = self.root / "bad.jsonl"
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(ValidationError):
                    load_predictions(path)

    def test_non_utf8_rejected(self):
        path = self.root / "bad.jsonl"
        path.write_bytes(b"\xff")
        with self.assertRaises(ValidationError):
            load_predictions(path)

    def test_invalid_cases_rejected(self):
        modifications = [
            {"year": True}, {"year": "2022"}, {"year": 2022.0}, {"year": 1800},
            {"answer": "a"}, {"question": "  "}, {"source_id": ""},
            {"track": "unknown"}, {"synthetic": False}, {"synthetic": 1},
            {"choices": {"A": "1", "B": "2", "C": "3"}}, {"extra": "hidden"},
            {"id": "bad id"}, {"answer": ["A"]},
        ]
        for change in modifications:
            with self.subTest(change=change):
                row = dict(self.cases[0], **change)
                with self.assertRaises(ValidationError):
                    load_cases(self.write([row]))

    def test_missing_case_field_rejected(self):
        row = dict(self.cases[0])
        row.pop("answer")
        with self.assertRaises(ValidationError):
            load_cases(self.write([row]))

    def test_empty_choice_rejected(self):
        row = copy.deepcopy(self.cases[0])
        row["choices"]["D"] = " "
        with self.assertRaises(ValidationError):
            load_cases(self.write([row]))

    def test_invalid_prediction_shapes_rejected(self):
        base = {"id": "sample", "response": "A", "status": "ok"}
        for change in ({"response": None}, {"status": "refusal"}, {"status": "error"},
                       {"status": "unknown"}, {"error_code": "bad"},
                       {"latency_seconds": -1}, {"latency_seconds": True}, {"extra": 1}):
            with self.subTest(change=change):
                with self.assertRaises(ValidationError):
                    load_predictions(self.write([dict(base, **change)]))

    def test_strict_matching_and_refusal(self):
        case = self.cases[0]  # expected A
        for response, expected in [("A", "correct"), (" A\n", "correct"), ("D", "incorrect"),
                                   ("a", "invalid"), ("答案 A", "invalid"), ("A B", "invalid"),
                                   ("", "invalid"), ("REFUSE", "refused"), ("拒绝", "invalid")]:
            with self.subTest(response=response):
                report = evaluate([case], [{"id": case["id"], "response": response, "status": "ok"}])
                self.assertEqual(report["summary"][expected], 1)
                self.assertEqual(report["summary"]["total"], 1)

    def test_error_stays_in_denominator(self):
        row = {"id": self.cases[0]["id"], "response": None, "status": "error", "error_code": "timeout"}
        predictions = load_predictions(self.write([row]))
        summary = evaluate(self.cases, predictions)["summary"]
        self.assertEqual((summary["error"], summary["missing"], summary["total"]), (1, 7, 8))

    def test_prompt_whitelist_excludes_answers_and_metadata(self):
        case = copy.deepcopy(self.cases[0])
        first = messages_for(case)
        case.update(answer="D", source_id="ANSWER_SECRET_MARKER", id="OTHER_ID", year=2025)
        self.assertEqual(first, messages_for(case))
        serialized = json.dumps(first)
        self.assertNotIn("ANSWER_SECRET_MARKER", serialized)
        self.assertNotIn("source_id", serialized)
        self.assertEqual(len(first), 2)

    def test_endpoint_security_validation(self):
        accepted = "https://provider.example/v1/chat/completions"
        self.assertEqual(validate_endpoint(accepted), accepted)
        local = "http://127.0.0.1:8000/v1/chat/completions"
        self.assertEqual(validate_endpoint(local, True), local)
        for endpoint in (local, "http://provider.example/v1/chat/completions",
                         "https://user:secret@provider.example/v1/chat/completions",
                         accepted + "?api_key=secret", accepted + "#fragment",
                         "https://provider.example/v1", "file:///v1/chat/completions"):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ValidationError):
                    validate_endpoint(endpoint)
        with self.assertRaises(ValidationError):
            validate_endpoint("http://provider.example/v1/chat/completions", True)

    def test_redirects_are_not_followed(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.example"))

    def test_dry_run_has_no_key_reads_network_or_files(self):
        with patch("ftbench.runner.os.environ.get", side_effect=AssertionError("key read")), \
                patch("ftbench.runner.post_completion", side_effect=AssertionError("network")):
            result = run(self.args(), self.cases)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["config"]["maximum_requests"], 8)
        self.assertFalse((self.root / "run").exists())

    def test_execute_requires_environment_key(self):
        with patch.dict(os.environ, {}, clear=True), patch("ftbench.runner.post_completion") as post:
            with self.assertRaises(ValidationError):
                run(self.args(execute=True), self.cases)
            post.assert_not_called()
        self.assertFalse((self.root / "run").exists())

    def test_execute_never_overwrites_directory(self):
        (self.root / "run").mkdir()
        with patch.dict(os.environ, {"BENCH_TEST_KEY": SECRET}), patch("ftbench.runner.post_completion") as post:
            with self.assertRaises(FileExistsError):
                run(self.args(execute=True), self.cases)
            post.assert_not_called()

    def test_mock_execution_records_config_and_all_results(self):
        response = {"choices": [{"message": {"content": "A"}}]}
        with patch.dict(os.environ, {"BENCH_TEST_KEY": SECRET}), \
                patch("ftbench.runner.post_completion", return_value=response) as post:
            result = run(self.args(execute=True), self.cases)
        self.assertEqual(post.call_count, 8)
        self.assertFalse(result["dry_run"])
        config = json.loads((self.root / "run/run_config.json").read_text())
        self.assertEqual(config["status"], "completed")
        self.assertEqual(config["completed_requests"], 8)
        self.assertEqual(config["model"], "mock-model")
        self.assertEqual(len(config["cases_sha256"]), 64)
        predictions = load_predictions(self.root / "run/predictions.jsonl")
        self.assertEqual(len(predictions), 8)
        for file in (self.root / "run").iterdir():
            self.assertNotIn(SECRET, file.read_text())

    def test_network_and_http_failures_are_sanitized(self):
        errors = [urllib.error.HTTPError("https://example.com", 401, SECRET, {}, None),
                  urllib.error.URLError(SECRET), ValueError(SECRET), RuntimeError(SECRET)]
        with patch.dict(os.environ, {"BENCH_TEST_KEY": SECRET}), \
                patch("ftbench.runner.post_completion", side_effect=errors):
            result = run(self.args(execute=True), self.cases[:4])
        self.assertEqual(result["summary"]["error"], 4)
        records = load_predictions(self.root / "run/predictions.jsonl")
        self.assertEqual([row["error_code"] for row in records],
                         ["http_401", "network_error", "invalid_response", "request_failed"])
        for file in (self.root / "run").iterdir():
            self.assertNotIn(SECRET, file.read_text())

    def test_interrupt_keeps_missing_cases_and_partial_output(self):
        response = {"choices": [{"message": {"content": "A"}}]}
        with patch.dict(os.environ, {"BENCH_TEST_KEY": SECRET}), \
                patch("ftbench.runner.post_completion", side_effect=[response, KeyboardInterrupt()]):
            with self.assertRaises(KeyboardInterrupt):
                run(self.args(execute=True), self.cases)
        config = json.loads((self.root / "run/run_config.json").read_text())
        report = json.loads((self.root / "run/report.json").read_text())
        self.assertEqual(config["status"], "interrupted")
        self.assertEqual(report["summary"]["total"], 8)
        self.assertEqual(report["summary"]["missing"], 7)
        self.assertEqual(len(load_predictions(self.root / "run/predictions.jsonl")), 1)

    def test_api_key_not_saved_if_misplaced_in_configuration(self):
        with patch.dict(os.environ, {"BENCH_TEST_KEY": SECRET}), patch("ftbench.runner.post_completion") as post:
            with self.assertRaises(ValidationError):
                run(self.args(execute=True, model=SECRET), self.cases)
            post.assert_not_called()
        self.assertFalse((self.root / "run").exists())

    def test_model_response_secret_redaction_and_refusal(self):
        row = prediction_from_response("x", {"choices": [{"message": {"content": SECRET}}]}, SECRET)
        self.assertNotIn(SECRET, row["response"])
        refusal = prediction_from_response("x", {"choices": [{"message": {"refusal": "no"}}]}, SECRET)
        self.assertEqual(refusal["status"], "refusal")
        filtered = prediction_from_response("x", {"choices": [{"finish_reason": "content_filter",
                                                               "message": {"content": None}}]}, SECRET)
        self.assertEqual(filtered["status"], "refusal")
        for response in ({}, None, [], {"choices": []}, {"choices": [{"message": {"content": None}}]}):
            with self.subTest(response=response):
                self.assertEqual(prediction_from_response("x", response, SECRET)["status"], "error")

    def test_cli_score_validate_and_no_overwrite(self):
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(main(["validate", "--cases", str(CASES)]), 0)
        self.assertEqual(json.loads(stdout.getvalue())["cases"], 8)
        output = self.root / "report.json"
        command = ["score", "--cases", str(CASES), "--predictions", str(PREDS), "--output", str(output)]
        self.assertEqual(main(command), 0)
        self.assertEqual(json.loads(output.read_text())["summary"]["total"], 8)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(command), 2)

    def test_cli_invalid_inputs_nonzero(self):
        bad = self.write([dict(self.cases[0], answer="E")])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["validate", "--cases", str(bad)]), 2)

    def test_cli_rejects_unbounded_or_nonfinite_parameters(self):
        base = ["run", "--cases", str(CASES), "--endpoint", "https://example.com/v1/chat/completions",
                "--model", "mock", "--output-dir", str(self.root / "run")]
        for extra in (["--max-tokens", "0"], ["--temperature", "nan"], ["--timeout", "inf"]):
            with self.subTest(extra=extra), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    main(base + extra)
                self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
