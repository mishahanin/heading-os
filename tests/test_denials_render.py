"""The refusal reader must not become a delivery mechanism.

Regressions from a /scrutinize execution pass, 2026-08-01, on the two ends of
one record's lifetime:

1. **What goes IN.** `leak-guard:check-paths` wrote the matched literal into the
   reason, and that regex captures the data-path token PLUS everything up to the
   closing quote - so `"outputs/clients/<name>-contract.pdf"` entered the record
   whole. `denial_log` states that a record never carries the refused content,
   and `redact()` does not strip it because a path is not credential-shaped. The
   sibling guards (content-guard, the push content wall) already log only the
   class.

2. **What comes OUT.** `denials.py --detail` printed record fields raw. A
   record's `path` is the denied tool call's `file_path`, which a prompt
   injection can shape, so an ESC sequence written at denial time would be
   replayed into the operator's terminal when he later read the log.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CLI = _ROOT / "scripts" / "denials.py"
_LEAK_GUARD = _ROOT / "scripts" / "leak-guard.py"

_ESC = "\x1b"


def _run(script: Path, args, log_root: Path, cwd: Path = None, env_extra=None):
    env = dict(os.environ)
    env["WORKSPACE_LOG_DIR"] = str(log_root)
    if env_extra:
        env.update(env_extra)
    return subprocess.run([sys.executable, str(script), *args], capture_output=True,
                          text=True, cwd=str(cwd or _ROOT), env=env, timeout=120)


def _write_record(log_root: Path, **fields):
    target = log_root / "denials"
    target.mkdir(parents=True, exist_ok=True)
    record = {"ts": 1785000000.0, "mechanism": "probe", "action": "Write",
              "path": None, "reason": "", "context": None}
    record.update(fields)
    with (target / "denials.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def test_detail_does_not_replay_an_escape_sequence_from_a_record(tmp_path):
    _write_record(tmp_path, path=f"outputs/{_ESC}[2J{_ESC}]0;pwned\x07evil.txt")
    proc = _run(_CLI, ["--detail"], tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # The renderer's own colour codes are legitimate; the RECORD's are not.
    record_line = [ln for ln in proc.stdout.splitlines() if "evil.txt" in ln]
    assert record_line, proc.stdout
    assert "\\x1b" in record_line[0], "the record's escape survived into stdout"
    assert "\x07" not in record_line[0]


def test_detail_does_not_replay_an_escape_from_the_mechanism_name(tmp_path):
    _write_record(tmp_path, mechanism=f"probe{_ESC}[31m", path="outputs/x.txt")
    proc = _run(_CLI, ["--detail"], tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"probe{_ESC}[31m" not in proc.stdout


def test_a_carriage_return_cannot_overwrite_the_line_above(tmp_path):
    """CR is the cheap way to hide a record behind another one."""
    _write_record(tmp_path, path="outputs/a.txt\rBENIGN")
    proc = _run(_CLI, ["--detail"], tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "\\r" in proc.stdout


def test_leak_guard_records_the_token_not_the_matched_literal(tmp_path):
    """The record names the class, like its sibling guards. The private tail of
    the path is the refused content and never enters the log."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "config").mkdir()
    # check_paths only lints engine-routed code, and load_routing_map fails
    # CLOSED to private when the map is missing - so the fixture needs one.
    (repo / "config" / "routing-map.yaml").write_text(
        "version: 1\ndefault: engine\nrules: {}\n", encoding="utf-8")
    offender = repo / "scripts" / "offender.py"
    offender.write_text(
        'TARGET = "outputs/clients/verysecretclientname-contract.pdf"\n',
        encoding="utf-8")
    log_root = tmp_path / "logs"

    proc = _run(_LEAK_GUARD, ["check-paths", "--files", "scripts/offender.py"],
                log_root, cwd=repo, env_extra={"WORKSPACE_ROOT": str(repo)})
    assert proc.returncode != 0, f"the guard did not refuse:\n{proc.stdout}"

    records = [json.loads(line) for line
               in (log_root / "denials" / "denials.jsonl").read_text(
                   encoding="utf-8").splitlines() if line.strip()]
    assert records, "the refusal was not counted"
    blob = json.dumps(records)
    assert "verysecretclientname" not in blob, "the record carried the refused content"
    assert "outputs/" in blob, "the record lost the token class"
