"""
Curl-based integration test runner.

Reads YAML test files from the tests/curl/ directory, executes each test
via subprocess curl, and reports pass/fail with colored output.

Usage:
    uv run tests/curl/runner.py [--base-url http://localhost:8000] [file.yml ...]

If no files are given, all *.yml files in the same directory are run.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

# ── ANSI colours ──────────────────────────────────────────────────────────────

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
RESET = "\033[0m"
BOLD = "\033[1m"


def green(s: str) -> str:
    return f"{GREEN}{s}{RESET}"


def red(s: str) -> str:
    return f"{RED}{s}{RESET}"


def yellow(s: str) -> str:
    return f"{YELLOW}{s}{RESET}"


def bold(s: str) -> str:
    return f"{BOLD}{s}{RESET}"


# ── Matchers ──────────────────────────────────────────────────────────────────

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)

_MATCHERS: dict[str, Any] = {
    "any_string": lambda v: isinstance(v, str),
    "any_number": lambda v: isinstance(v, (int, float)),
    "any_uuid": lambda v: isinstance(v, str) and bool(_UUID_RE.fullmatch(v)),
}

_MISSING = object()


def match_value(expected: Any, actual: Any, path: str = "") -> list[str]:
    """
    Recursively compare expected against actual.

    Returns a list of failure messages (empty = pass).
    Strings in expected that equal a matcher name are evaluated as matchers.
    """
    failures: list[str] = []

    if isinstance(expected, str) and expected in _MATCHERS:
        if not _MATCHERS[expected](actual):
            type_name = type(actual).__name__
            failures.append(
                f"  {path}: expected {expected} but got {type_name}({actual!r})"
            )
        return failures

    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            failures.append(f"  {path}: expected dict but got {type(actual).__name__}")
            return failures
        for key, exp_val in expected.items():
            act_val = actual.get(key, _MISSING)
            if act_val is _MISSING:
                failures.append(f"  {path}.{key}: key missing in response")
            else:
                failures.extend(match_value(exp_val, act_val, f"{path}.{key}"))
        return failures

    if isinstance(expected, list):
        if not isinstance(actual, list):
            failures.append(f"  {path}: expected list but got {type(actual).__name__}")
            return failures
        for i, exp_item in enumerate(expected):
            if i >= len(actual):
                failures.append(
                    f"  {path}[{i}]: index out of range (actual len={len(actual)})"
                )
            else:
                failures.extend(match_value(exp_item, actual[i], f"{path}[{i}]"))
        return failures

    # Scalar — exact equality
    if expected != actual:
        failures.append(f"  {path}: expected {expected!r} but got {actual!r}")
    return failures


# ── Interpolation ─────────────────────────────────────────────────────────────


def interpolate(value: Any, context: dict[str, Any]) -> Any:
    """
    Replace ${prev.field} references with values from context["prev"].

    Works recursively on dicts, lists, and strings.
    """
    if isinstance(value, str):

        def replacer(m: re.Match) -> str:  # type: ignore[type-arg]
            key_path = m.group(1).split(".")
            node: Any = context
            for k in key_path:
                if isinstance(node, dict):
                    node = node.get(k)
                else:
                    return m.group(0)  # can't resolve — leave as-is
            return str(node) if node is not None else m.group(0)

        return re.sub(r"\$\{([^}]+)\}", replacer, value)

    if isinstance(value, dict):
        return {k: interpolate(v, context) for k, v in value.items()}

    if isinstance(value, list):
        return [interpolate(item, context) for item in value]

    return value


# ── Curl execution ────────────────────────────────────────────────────────────


def run_curl(
    method: str,
    url: str,
    headers: dict[str, str] | None,
    body: Any | None,
    max_time: int = 10,
) -> tuple[int, dict[str, Any] | str | None, dict[str, str]]:
    """
    Execute a curl request.

    Returns (status_code, response_body, response_headers).
    response_body is parsed JSON if valid JSON, else raw string.
    """
    cmd = [
        "curl",
        "--silent",
        "--show-error",
        "--include",  # include response headers in output
        "--max-time",
        str(max_time),
        "--write-out",
        "\n__STATUS__%{http_code}",
        "--request",
        method.upper(),
        url,
    ]

    # Default Content-Type for requests with a body
    effective_headers: dict[str, str] = {"Content-Type": "application/json"}
    if headers:
        effective_headers.update(headers)

    for header_name, header_val in effective_headers.items():
        cmd += ["--header", f"{header_name}: {header_val}"]

    if body is not None:
        cmd += ["--data", json.dumps(body)]

    result = subprocess.run(cmd, capture_output=True, text=True)

    raw = result.stdout

    # Split off our sentinel status line
    parts = raw.rsplit("\n__STATUS__", 1)
    full_text = parts[0].strip()
    status_code = int(parts[1].strip()) if len(parts) == 2 else 0

    # Split headers from body: headers end at first blank line
    response_headers: dict[str, str] = {}
    body_text = full_text
    if "\r\n\r\n" in full_text:
        header_section, body_text = full_text.split("\r\n\r\n", 1)
        body_text = body_text.strip()
        for line in header_section.splitlines()[1:]:  # skip the HTTP status line
            if ":" in line:
                hname, _, hval = line.partition(":")
                response_headers[hname.strip().lower()] = hval.strip()
    elif "\n\n" in full_text:
        header_section, body_text = full_text.split("\n\n", 1)
        body_text = body_text.strip()
        for line in header_section.splitlines()[1:]:
            if ":" in line:
                hname, _, hval = line.partition(":")
                response_headers[hname.strip().lower()] = hval.strip()

    # Normalise content-type: strip charset/boundary suffix for comparison
    if "content-type" in response_headers:
        response_headers["content-type"] = (
            response_headers["content-type"].split(";")[0].strip()
        )

    # Parse response body
    response_body: dict[str, Any] | str | None
    if body_text:
        try:
            response_body = json.loads(body_text)
        except json.JSONDecodeError:
            response_body = body_text
    else:
        response_body = None

    return status_code, response_body, response_headers


# ── Test runner ───────────────────────────────────────────────────────────────


def run_test(
    test: dict[str, Any],
    base_url: str,
    prev_response: Any,
) -> tuple[bool, str, Any]:
    """
    Execute a single test case.

    Returns (passed, detail_message, response_body).
    """
    request = test.get("request", {})
    expect = test.get("expect", {})

    context: dict[str, Any] = {}
    if prev_response is not None:
        context["prev"] = prev_response if isinstance(prev_response, dict) else {}

    method = interpolate(request.get("method", "GET"), context)
    path = interpolate(request.get("url", "/"), context)
    raw_headers = request.get("headers")
    headers = interpolate(raw_headers, context) if raw_headers else None
    raw_body = request.get("body")
    body = interpolate(raw_body, context) if raw_body is not None else None

    url = base_url.rstrip("/") + path

    try:
        status_code, response_body, response_headers = run_curl(
            method, url, headers, body
        )
    except Exception as exc:
        return False, f"  curl error: {exc}", None

    failures: list[str] = []

    # Status assertion
    expected_status = expect.get("status")
    if expected_status is not None and status_code != expected_status:
        failures.append(f"  status: expected {expected_status} but got {status_code}")

    # Body assertion
    expected_body = expect.get("body")
    if expected_body is not None:
        if response_body is None:
            failures.append("  body: expected body but response was empty")
        else:
            failures.extend(match_value(expected_body, response_body, "body"))

    # Header assertion
    expected_headers = expect.get("headers", {})
    for header_name, header_val in (expected_headers or {}).items():
        actual_val = response_headers.get(header_name.lower())
        if actual_val is None:
            failures.append(f"  header {header_name}: missing")
        elif actual_val != header_val:
            failures.append(
                f"  header {header_name}: expected {header_val!r}"
                f" but got {actual_val!r}"
            )

    if failures:
        detail = "\n".join(failures)
        return False, detail, response_body

    return True, "", response_body


def run_file(
    path: Path,
    base_url: str,
    results: list[tuple[str, bool, str]],
    named_responses: dict[str, Any],
) -> None:
    """Load a YAML file and run all tests in it."""
    with path.open() as f:
        data = yaml.safe_load(f)

    if data is None:
        return

    # A file can be a single test dict or a list of tests
    tests = data if isinstance(data, list) else [data]

    for test in tests:
        name = test.get("name", path.name)
        depends_on = test.get("depends_on")
        prev_response = named_responses.get(depends_on) if depends_on else None

        passed, detail, response_body = run_test(test, base_url, prev_response)

        if passed:
            # Store the actual response body so later tests can reference by name
            named_responses[name] = response_body
            print(f"  {green('PASS')}  {name}")
        else:
            named_responses[name] = response_body
            print(f"  {red('FAIL')}  {name}")
            if detail:
                print(detail)

        results.append((name, passed, detail))


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Curl-based YAML integration test runner"
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="Base URL for the API (default: http://localhost:8000)",
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="YAML test files to run (default: all *.yml in same directory)",
    )
    args = parser.parse_args()

    runner_dir = Path(__file__).parent

    if args.files:
        files = [Path(f) for f in args.files]
    else:
        files = sorted(runner_dir.glob("*.yml"))

    if not files:
        print(yellow("No test files found."))
        return 0

    print(bold("\nYes Chef — curl integration tests"))
    print(f"Base URL: {args.base_url}\n")

    results: list[tuple[str, bool, str]] = []
    named_responses: dict[str, Any] = {}

    for file_path in files:
        print(bold(f"  {file_path.name}"))
        run_file(file_path, args.base_url, results, named_responses)
        print()

    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = total - passed

    print("─" * 50)
    if failed == 0:
        print(green(bold(f"  {passed}/{total} passed")))
    else:
        print(red(bold(f"  {passed}/{total} passed")) + f"  ({failed} failed)")
    print()

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
