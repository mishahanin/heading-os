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


import pytest  # noqa: E402

# Every TEXT field `--detail` renders. `ts` is excluded because it is rendered
# through `_stamp`, which parses it as a float and never prints the raw value.
#
# Written out as a list rather than derived from `_write_record`'s dict so that
# narrowing the renderer cannot narrow the case list with it. A field ADDED to
# the record and then rendered raw is a case this list does not carry; the
# structural test below is what covers that direction.
_RENDERED_TEXT_FIELDS = ("mechanism", "action", "path", "context")


@pytest.mark.parametrize("field", _RENDERED_TEXT_FIELDS)
def test_no_rendered_field_replays_an_escape_sequence(tmp_path, field):
    """The escaping is per FIELD, and three of the four had no test.

    The two tests above pin `path` and `mechanism`. `scripts/denials.py` calls
    `_printable` on four fields, and MEASURED 2026-09-01, dropping that call
    from `action` left 92 tests across seven files green, as did dropping it
    from `context`. Half the render surface was unguarded, which is the
    one-of-N-copies shape this repository keeps paying for: the guard was
    repaired on the two fields somebody happened to write a case for.

    `action` is the denied call's `tool_name`, read off the PreToolUse payload
    on stdin. `context` is `HEADING_OS_DENIAL_CONTEXT` out of the environment of
    whichever process was refusing. Neither is a literal this workspace chose,
    so both reach the log as text and both reach the operator's terminal when he
    reads it back.
    """
    marker = "anchor-for-this-case"
    fields = {"mechanism": "probe", "action": "Write",
              "path": "outputs/x.txt", "context": "push"}
    fields[field] = f"{marker}{_ESC}[31m{_ESC}]0;pwned\x07"
    _write_record(tmp_path, **fields)
    proc = _run(_CLI, ["--detail"], tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    assert marker in proc.stdout, (
        f"the record carrying the {field} case never reached stdout, so this "
        f"test would pass against a renderer that printed nothing")
    assert f"{_ESC}[31m" not in proc.stdout, (
        f"the record's ESC survived into stdout through the {field} field")
    assert "\x07" not in proc.stdout
    assert "\\x1b" in proc.stdout, (
        f"the {field} field's escape was DELETED rather than shown as text; a "
        f"reader must be able to see that a record carried one")


def test_a_carriage_return_cannot_overwrite_the_line_above(tmp_path):
    """CR is the cheap way to hide a record behind another one."""
    _write_record(tmp_path, path="outputs/a.txt\rBENIGN")
    proc = _run(_CLI, ["--detail"], tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "\\r" in proc.stdout


def test_every_record_field_interpolated_into_a_printed_line_is_escaped():
    """The other direction: a field ADDED tomorrow and printed raw.

    The parametrized test above carries the four fields that exist today. It
    cannot fail for a fifth, because a case list is written by hand and the
    fifth field's author is the person who would have to remember to add one.

    Asked of the AST rather than by grepping for `_printable`, per the lesson
    this repository has already paid for twice: a source-scan for the guard's
    NAME measures the text that mentions it, stays green when the call is parked
    in a branch that never runs, and goes red on an innocent re-wrap. The
    question here is structural and scoped: inside `main`, every f-string slot
    whose expression READS `record` must have a `_printable` or `_stamp` call at
    its top. `{context}` is exempt by construction rather than by exception - it
    reads a local, and that local was itself built by `_printable` one line
    above, which this same rule checks.
    """
    import ast

    tree = ast.parse(_CLI.read_text(encoding="utf-8"))
    main = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "main")

    checked = 0
    unescaped: list[str] = []
    for slot in ast.walk(main):
        if not isinstance(slot, ast.FormattedValue):
            continue
        reads_record = any(isinstance(n, ast.Name) and n.id == "record"
                           for n in ast.walk(slot.value))
        if not reads_record:
            continue
        checked += 1
        top = slot.value
        escaped = (isinstance(top, ast.Call) and isinstance(top.func, ast.Name)
                   and top.func.id in {"_printable", "_stamp"})
        if not escaped:
            unescaped.append(ast.unparse(top))

    # Floor: an AST question that matches nothing answers "clean" for every
    # renderer, including one that prints every field raw. Measured 4 on
    # 2026-09-01 (ts, mechanism, action, path); context is interpolated one line
    # earlier, in its own f-string, and is counted there too.
    assert checked >= 4, (
        f"only {checked} record-reading f-string slot(s) found in denials.py "
        f"main(); the AST question has stopped reaching the renderer")
    assert not unescaped, (
        "these record fields are interpolated into a printed line without "
        "passing through `_printable`, so a record written at denial time can "
        "replay control bytes into the operator's terminal when he reads the "
        "log back:\n  " + "\n  ".join(unescaped))


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
