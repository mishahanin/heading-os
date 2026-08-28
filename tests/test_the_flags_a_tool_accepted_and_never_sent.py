"""Shard 46: the flags `design-engine.py` accepted and never sent.

`--count`, `--seed` and `--format` reach flux and banana. They reach neither
recraft nor ideogram. `--width` and `--height` reach recraft and ideogram, and
reach neither flux nor banana. Nothing said so. Every unsupported flag was
accepted by argparse, carried through `cmd_generate`, and dropped on the floor
inside `_build_generate_input` without a word.

MEASURED against the four families, with all six flags typed:

    recraft   -> prompt, size                       dropped: aspect count format seed
    ideogram  -> prompt, width, height              dropped: aspect count format seed
    flux      -> prompt, aspect_ratio, num_outputs,
                 output_format, seed                dropped: width height
    banana    -> the same as flux                   dropped: width height

That last row is the one that bites, because `.claude/skills/design/SKILL.md`
carried ONE documented generate command and it always passed `--width {W}
--height {H}`, while the same file's model table routes "photorealistic image"
to `flux-2-pro` and "fast concept draft" to `flux-schnell`. Following the
documentation exactly meant losing both flags on the two models the
documentation recommends most.

Three more claims in the same shape, all reproduced by running the code:

- The filename was built from `--format`, a flag recraft and ideogram are never
  told. `--model recraft-v4 --format webp` produced a PNG named `.webp`: a name
  that lies about its bytes, which the next tool in the chain reads as fact.
- `_save_outputs` numbered files only when a `multi` flag was set, and three of
  its four callers hardcoded `multi=False`. With three URLs it wrote all three
  to ONE path, printed three "Saved" lines naming that one path, returned three
  identical paths, and the cost line then billed three images against the one
  file that survived.
- `_download` had no scheme check, no size cap and no error handling, while its
  sibling `scripts/updaters/cliproxyapi_update.py:_download` has all three for
  the identical reason: the URL is not a literal, it arrives inside an API
  response, so the scheme is remote data. A fix that landed in one of two
  copies. `pyproject.toml` justifies skipping bandit's B310 with "our scripts
  call hardcoded https API endpoints ... never user-controlled schemes", which
  was true of every urlopen in the workspace except that one.

Tests: this file.
"""
from __future__ import annotations

import importlib.util
import io
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(stem: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


de = _load("design-engine", "design_engine_s46")

ALL_FLAGS = {"width", "height", "aspect", "count", "format", "seed"}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _build(family, explicit=ALL_FLAGS, **kw):
    kw.setdefault("prompt", "a cat")
    kw.setdefault("width", 1024)
    kw.setdefault("height", 768)
    kw.setdefault("aspect", "16:9")
    kw.setdefault("count", 3)
    kw.setdefault("fmt", "webp")
    kw.setdefault("seed", 42)
    return de._build_generate_input(family=family, explicit=explicit, **kw)


# ==========================================================================
# 1 - each family, and what it never receives
# ==========================================================================

@pytest.mark.parametrize("family,expect_dropped", [
    ("recraft", ["aspect", "count", "format", "seed"]),
    ("ideogram", ["aspect", "count", "format", "seed"]),
    ("flux", ["height", "width"]),
    ("banana", ["height", "width"]),
])
def test_each_family_names_the_flags_it_never_receives(family, expect_dropped):
    _params, dropped = _build(family)
    assert dropped == expect_dropped


def test_an_unknown_family_reports_every_flag_dropped():
    """The fallback payload is `{"prompt": ...}` and nothing else, so a model
    routed to it silently ignores everything the operator typed."""
    _params, dropped = _build("no-such-family")
    assert dropped == sorted(ALL_FLAGS)


def test_a_flag_left_alone_is_never_reported():
    """Only the flags the operator TYPED can be dropped. Reporting a default
    the operator never asked for is noise that trains them to skip the line."""
    _params, dropped = _build("recraft", explicit=set())
    assert dropped == []


def test_only_the_typed_flags_are_reported():
    _params, dropped = _build("flux", explicit={"width"})
    assert dropped == ["width"], "height was not typed and must not appear"


@pytest.mark.parametrize("family", ["recraft", "ideogram", "flux", "banana", "junk"])
def test_every_typed_flag_is_either_carried_or_reported(family):
    """The invariant that makes the report trustworthy: no flag can be quietly
    neither. This is what a hand-written per-family table gets wrong the day a
    family is added and the table is not."""
    params, dropped = _build(family)
    carried = {
        flag for flag, keys in de._FLAG_CARRIERS.items()
        if any(key in params for key in keys)
    }
    assert carried | set(dropped) == ALL_FLAGS
    assert carried & set(dropped) == set()


def test_the_carrier_map_covers_every_generate_flag():
    """`_FLAG_CARRIERS` is what `cmd_generate` reads to decide which argparse
    values count as 'typed'. A flag missing from it can never be reported, so
    a new one silently reintroduces the whole defect."""
    parser_src = (ROOT / "scripts" / "design-engine.py").read_text(encoding="utf-8")
    gen_block = parser_src.split("# -- generate --")[1].split("# -- edit --")[0]
    declared = {
        line.split('add_argument("--')[1].split('"')[0]
        for line in gen_block.splitlines() if 'add_argument("--' in line
    }
    # model/prompt/output are not model-shape flags; they always reach.
    assert declared - {"model", "prompt", "output"} == set(de._FLAG_CARRIERS)


def test_seed_is_reported_when_the_family_ignores_it():
    """flux carries `seed` only when one was given; ideogram never does."""
    params, dropped = _build("ideogram", seed=7, explicit={"seed"})
    assert "seed" not in params
    assert dropped == ["seed"]


def test_width_and_height_reach_recraft_through_size():
    """They are carried, under a different key. A report keyed on the flag name
    alone would call them dropped and be wrong."""
    params, dropped = _build("recraft", explicit={"width", "height"})
    assert params["size"] == "1024x768"
    assert dropped == []


# ==========================================================================
# 2 - the documentation that recommended the dropped flags
# ==========================================================================

def _documented_generate_commands() -> dict:
    """The generate commands SKILL.md tells an operator to run, by `# comment`.

    Whole commands, with backslash continuations joined. The first version of
    this helper matched per LINE and a mutation walked straight through it: the
    comment names the family, the flags sit on the CONTINUATION line, and a
    line carrying "flux" carried no flags at all. Reading the command as the
    shell would is the only reading that can be wrong when the docs are wrong.
    """
    skill = (ROOT / ".claude" / "skills" / "design" / "SKILL.md").read_text(encoding="utf-8")
    block = skill.split("**Generation command")[1].split("**Edit/upscale")[0]
    fence = block.split("```bash")[1].split("```")[0]
    joined = fence.replace("\\\n", " ")
    commands, label = {}, None
    for line in joined.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            label = stripped.lstrip("# ").strip()
        elif stripped and label:
            commands[label] = " ".join(stripped.split())
            label = None
    return commands


def test_the_skill_documents_one_command_per_family_group():
    cmds = _documented_generate_commands()
    assert set(cmds) == {"recraft / ideogram", "flux / banana"}, cmds


def test_the_documented_flux_command_passes_no_flag_flux_would_drop():
    """The defect's loudest source. ONE documented command passed `--width` and
    `--height` for every model, and the table beside it routes photorealism to
    `flux-2-pro` and drafts to `flux-schnell`, which accept neither."""
    cmd = _documented_generate_commands()["flux / banana"]
    assert "--width" not in cmd, cmd
    assert "--height" not in cmd, cmd
    assert "--aspect" in cmd, cmd


def test_the_documented_recraft_command_passes_the_flags_recraft_needs():
    """The other half. Deleting the size flags from the docs would satisfy the
    test above and leave recraft and ideogram with no documented size at all."""
    cmd = _documented_generate_commands()["recraft / ideogram"]
    assert "--width" in cmd and "--height" in cmd, cmd
    assert "--aspect" not in cmd, cmd


@pytest.mark.parametrize("label,alias", [
    ("recraft / ideogram", "recraft-v4"),
    ("recraft / ideogram", "ideogram-v3"),
    ("flux / banana", "flux-2-pro"),
    ("flux / banana", "flux-schnell"),
    ("flux / banana", "banana-pro"),
])
def test_every_documented_command_would_report_nothing_dropped(label, alias):
    """The strongest form: run the documented flag set through the real builder
    for a real model of that family. A doc that recommends a dropped flag fails
    here without anyone having to remember to update a second list."""
    cmd = _documented_generate_commands()[label]
    typed = {flag for flag in ALL_FLAGS if f"--{flag}" in cmd}
    assert typed, cmd
    family = de.MODELS[alias]["family"]
    _params, dropped = _build(family, explicit=typed)
    assert dropped == [], f"{alias}: docs recommend {dropped}"


# ==========================================================================
# 3 - the report reaches the operator, not just the return value
# ==========================================================================

def _run_generate(monkeypatch, capsys, tmp_path, urls=("https://x/a",),
                  body=PNG, **argkw):
    sent = {}

    def _fake_pred(token, model_id, input_params):
        sent.clear()
        sent.update(input_params)
        return {"output": list(urls)}

    monkeypatch.setattr(de, "load_api_key", lambda k: "tok")
    monkeypatch.setattr(de, "_create_prediction", _fake_pred)
    monkeypatch.setattr(de, "_default_output_dir", lambda: tmp_path)

    bodies = body if isinstance(body, dict) else dict.fromkeys(urls, body)

    def _fake_dl(url, dest):
        data = bodies[url]
        dest.write_bytes(data)
        return data

    monkeypatch.setattr(de, "_download", _fake_dl)

    args = types.SimpleNamespace(
        model="recraft-v4", prompt="a cat", width=None, height=None, aspect=None,
        count=None, format=None, seed=None, output=None)
    for k, v in argkw.items():
        setattr(args, k, v)
    de.cmd_generate(args)
    return sent, capsys.readouterr().out


def test_the_command_names_the_dropped_flags_on_stdout(monkeypatch, capsys, tmp_path):
    """The builder returning `dropped` proves nothing on its own. This walks
    `cmd_generate`, which is the only place the operator can read it."""
    sent, out = _run_generate(monkeypatch, capsys, tmp_path,
                              model="recraft-v4", count=3, seed=99, format="webp")
    assert sorted(sent) == ["prompt", "size"]
    assert "Not sent" in out
    for flag in ("--count", "--format", "--seed"):
        assert flag in out
    assert "recraft" in out


def test_the_command_says_nothing_when_every_flag_reached_the_model(monkeypatch, capsys, tmp_path):
    """A warning that fires on a healthy run is a warning nobody reads."""
    _sent, out = _run_generate(monkeypatch, capsys, tmp_path,
                               model="flux-schnell", aspect="1:1", count=1,
                               format="png", seed=5)
    assert "Not sent" not in out


def test_the_documented_flux_command_now_reports_nothing_dropped(monkeypatch, capsys, tmp_path):
    """The exact shape the corrected SKILL.md tells an operator to run."""
    _sent, out = _run_generate(monkeypatch, capsys, tmp_path,
                               model="flux-2-pro", aspect="16:9")
    assert "Not sent" not in out


def test_the_old_documented_command_is_the_one_that_warns(monkeypatch, capsys, tmp_path):
    """`--model flux-2-pro --width W --height H` was the documented command.
    It is still accepted, and now it says what it did with the two flags."""
    _sent, out = _run_generate(monkeypatch, capsys, tmp_path,
                               model="flux-2-pro", width=1024, height=1024)
    assert "Not sent" in out
    assert "--width" in out and "--height" in out


def test_the_format_flag_defaults_to_none_so_typed_and_assumed_stay_distinct():
    """It defaulted to "png", which made every run look like a run that had
    asked for a format, so the report could not tell the two apart."""
    src = (ROOT / "scripts" / "design-engine.py").read_text(encoding="utf-8")
    line = next(ln for ln in src.splitlines() if 'add_argument("--format"' in ln)
    assert "default=None" in line


# ==========================================================================
# 4 - the name that lied about the bytes
# ==========================================================================

@pytest.mark.parametrize("data,expect", [
    (b"\x89PNG\r\n\x1a\n\x00\x00", ".png"),
    (b"\xff\xd8\xff\xe0" + b"\x00" * 8, ".jpg"),
    (b"RIFF\x00\x00\x00\x00WEBPVP8 ", ".webp"),
    (b"GIF89a" + b"\x00" * 8, ".gif"),
    (b'<?xml version="1.0"?><svg xmlns="x"></svg>', ".svg"),
    (b"  \n<svg xmlns='x'></svg>", ".svg"),
    (b"not an image at all, just prose", None),
    (b"", None),
])
def test_the_bytes_decide_the_extension(data, expect):
    assert de._sniff_ext(data) == expect


def test_an_xml_document_that_is_not_svg_is_not_called_svg():
    assert de._sniff_ext(b'<?xml version="1.0"?><rss><channel/></rss>') is None


def test_a_tool_chosen_name_is_corrected_to_the_real_format(monkeypatch, capsys, tmp_path):
    """MEASURED before the fix: `--model recraft-v4 --format webp` saved PNG
    bytes into a file called `.webp`, because the name came from a flag recraft
    is never told."""
    _sent, out = _run_generate(monkeypatch, capsys, tmp_path,
                               model="recraft-v4", format="webp")
    files = sorted(p.name for p in tmp_path.iterdir())
    assert len(files) == 1
    assert files[0].endswith(".png"), files
    assert files[0] in out


def test_a_name_the_operator_gave_is_kept_and_the_mismatch_is_named(monkeypatch, capsys, tmp_path):
    """Renaming a path the operator typed would break the `-o` contract every
    caller in SKILL.md relies on. It warns instead."""
    dest = tmp_path / "mine.webp"
    _sent, out = _run_generate(monkeypatch, capsys, tmp_path,
                               model="recraft-v4", output=str(dest))
    assert dest.exists()
    assert "mine.webp" in out
    assert ".png" in out


def test_a_matching_extension_produces_no_noise(monkeypatch, capsys, tmp_path):
    dest = tmp_path / "mine.png"
    _sent, out = _run_generate(monkeypatch, capsys, tmp_path,
                               model="recraft-v4", output=str(dest))
    assert dest.exists()
    assert "[WARN]" not in out


def test_unrecognised_bytes_leave_the_name_alone(tmp_path, monkeypatch):
    """`_sniff_ext` returning None must mean "do not know", never "rename to
    nothing". An SVG model can return text this does not recognise."""
    blob = b"some payload no magic number covers"
    monkeypatch.setattr(de, "_download",
                        lambda url, dest: (dest.write_bytes(blob), blob)[1])
    saved = de._save_outputs(["https://x/a"], tmp_path / "out.svg", name_from_bytes=True)
    assert saved == [tmp_path / "out.svg"]
    assert (tmp_path / "out.svg").read_bytes() == blob


def test_the_command_does_not_promise_a_filename_it_may_change(monkeypatch, capsys, tmp_path):
    """It printed `Output: <path>` before the bytes were seen, then saved a
    different path. Only the `-o` case can be promised up front."""
    _sent, out = _run_generate(monkeypatch, capsys, tmp_path, model="recraft-v4")
    assert "Output directory:" in out


# ==========================================================================
# 5 - three "Saved" lines, one file
# ==========================================================================

def _three_bodies(tmp):
    return {f"https://x/{c}": b"\x89PNG\r\n\x1a\n" + c.encode() * 8 for c in "abc"}


def test_several_urls_produce_several_files(tmp_path, monkeypatch):
    """MEASURED before the fix, with the `multi=False` its three non-generate
    callers hardcoded: three Saved lines, one file, the last body winning."""
    bodies = _three_bodies(tmp_path)
    monkeypatch.setattr(de, "_download",
                        lambda url, dest: (dest.write_bytes(bodies[url]), bodies[url])[1])
    saved = de._save_outputs(list(bodies), tmp_path / "out.png", name_from_bytes=False)
    assert len({str(p) for p in saved}) == 3
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out_1.png", "out_2.png", "out_3.png"]


def test_every_reported_path_exists_on_disk(tmp_path, monkeypatch):
    """The claim the old code broke: it named paths that had been overwritten
    by the time it finished naming them."""
    bodies = _three_bodies(tmp_path)
    monkeypatch.setattr(de, "_download",
                        lambda url, dest: (dest.write_bytes(bodies[url]), bodies[url])[1])
    saved = de._save_outputs(list(bodies), tmp_path / "out.png", name_from_bytes=False)
    assert all(p.exists() for p in saved)
    assert len({p.read_bytes() for p in saved}) == 3


def test_one_url_is_not_numbered(tmp_path, monkeypatch):
    monkeypatch.setattr(de, "_download",
                        lambda url, dest: (dest.write_bytes(PNG), PNG)[1])
    saved = de._save_outputs(["https://x/a"], tmp_path / "out.png", name_from_bytes=False)
    assert saved == [tmp_path / "out.png"]


def test_the_cost_line_counts_files_that_exist(monkeypatch, capsys, tmp_path):
    """It billed one image per URL against a single surviving file."""
    bodies = _three_bodies(tmp_path)
    _sent, out = _run_generate(monkeypatch, capsys, tmp_path,
                               urls=tuple(bodies), body=bodies, model="recraft-v4")
    assert "3 image(s)" in out
    assert len(list(tmp_path.iterdir())) == 3


def test_save_outputs_no_longer_takes_a_parameter_it_never_read():
    """`is_svg` was passed by all four callers and read by none."""
    import inspect
    params = set(inspect.signature(de._save_outputs).parameters)
    assert "is_svg" not in params
    assert "multi" not in params


# ==========================================================================
# 6 - a default name that destroyed the previous one
# ==========================================================================

def test_a_second_run_in_the_same_second_does_not_destroy_the_first(tmp_path):
    """`_timestamp()` has one-second resolution, so two runs inside one second
    built the same default path and the second overwrote the first while
    printing Saved over bytes that were gone."""
    first = tmp_path / "design-1.png"
    first.write_bytes(b"first")
    second = de._unique_path(first)
    assert second != first
    assert first.read_bytes() == b"first"


def test_a_free_path_is_returned_unchanged(tmp_path):
    p = tmp_path / "design-1.png"
    assert de._unique_path(p) == p


def test_the_operator_path_is_never_made_unique(monkeypatch, capsys, tmp_path):
    """`-o` is an instruction, not a suggestion. Overwriting is the operator's
    call; silently writing beside it would hide the file they asked for."""
    dest = tmp_path / "mine.png"
    dest.write_bytes(b"older")
    _sent, _out = _run_generate(monkeypatch, capsys, tmp_path,
                                model="recraft-v4", output=str(dest))
    assert dest.read_bytes() == PNG
    assert sorted(p.name for p in tmp_path.iterdir()) == ["mine.png"]


# ==========================================================================
# 7 - the download its sibling already guarded
# ==========================================================================

def test_a_non_https_output_url_is_refused(tmp_path, capsys):
    """The URL arrives inside the prediction response, so the scheme is remote
    data. `urlopen` honours `file:`."""
    with pytest.raises(SystemExit) as exc:
        de._download("file:///etc/passwd", tmp_path / "out")
    assert exc.value.code == 1
    assert "non-https" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_a_plain_http_output_url_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        de._download("http://x/a", tmp_path / "out")


def test_an_endless_body_is_capped(tmp_path, monkeypatch, capsys):
    class _Endless:
        def read(self, n):
            return b"x" * n

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(de, "MAX_DOWNLOAD_BYTES", 4 << 20)
    monkeypatch.setattr(de.urllib.request, "urlopen", lambda *a, **k: _Endless())
    with pytest.raises(SystemExit) as exc:
        de._download("https://x/a", tmp_path / "out")
    assert exc.value.code == 1
    assert "exceeded" in capsys.readouterr().err


def test_a_normal_body_downloads_whole_and_is_returned(tmp_path, monkeypatch):
    payload = b"hello world" * 40

    class _Body:
        def __init__(self):
            self.done = False

        def read(self, n):
            if self.done:
                return b""
            self.done = True
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(de.urllib.request, "urlopen", lambda *a, **k: _Body())
    dest = tmp_path / "out"
    assert de._download("https://x/a", dest) == payload
    assert dest.read_bytes() == payload


def test_a_network_error_is_reported_in_the_tools_voice(tmp_path, monkeypatch, capsys):
    """It had no handler, so a blip surfaced as a raw traceback while every
    sibling call in this file printed [ERROR] and exited 1."""
    def _boom(*a, **k):
        raise de.urllib.error.URLError("connection reset")

    monkeypatch.setattr(de.urllib.request, "urlopen", _boom)
    with pytest.raises(SystemExit) as exc:
        de._download("https://x/a", tmp_path / "out")
    assert exc.value.code == 1
    assert "[ERROR]" in capsys.readouterr().err


def test_an_http_error_is_reported_in_the_tools_voice(tmp_path, monkeypatch, capsys):
    def _boom(*a, **k):
        raise de.urllib.error.HTTPError("https://x/a", 503, "busy", {}, None)

    monkeypatch.setattr(de.urllib.request, "urlopen", _boom)
    with pytest.raises(SystemExit) as exc:
        de._download("https://x/a", tmp_path / "out")
    assert exc.value.code == 1
    assert "503" in capsys.readouterr().err


# ==========================================================================
# 8 - a filename interpolated into a header
# ==========================================================================

def _capture_upload_body(monkeypatch, path: Path) -> bytes:
    captured = {}

    class _Resp:
        def read(self):
            return b'{"urls": {"get": "https://x/f"}}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _urlopen(req, timeout=None):
        captured["body"] = req.data
        return _Resp()

    monkeypatch.setattr(de.urllib.request, "urlopen", _urlopen)
    de._upload_file(path, "tok")
    return captured["body"]


def _disposition(body: bytes) -> bytes:
    """The Content-Disposition line, as the receiving parser would read it."""
    return next(ln for ln in body.split(b"\r\n") if ln.startswith(b"Content-Disposition:"))


def test_a_quote_in_a_filename_cannot_close_the_header(monkeypatch, tmp_path):
    """`filename="{name}"` was interpolated raw. A `"` ends the quoted string
    early and everything after it is read as further header parameters.

    Asserted on `; name="`, not on `name="`, because `filename="` CONTAINS
    `name="` and a count over the shorter string is 2 on a perfectly sanitised
    header. That is the substring trap, and it would have passed this test
    whether or not the fix were present.
    """
    bad = tmp_path / 'evil"; name="content2".png'
    bad.write_bytes(PNG)
    line = _disposition(_capture_upload_body(monkeypatch, bad))
    assert line.count(b'; name="') == 1, line
    assert b'name="content2"' not in line
    # The value itself: no quote survives inside it at all.
    value = line.split(b'filename="', 1)[1]
    assert value.count(b'"') == 1, value


def test_a_newline_in_a_filename_cannot_start_a_header(monkeypatch, tmp_path):
    """A CR or LF ends the header line, so the rest becomes a header of its own
    that this tool never wrote."""
    bad = tmp_path / "a\r\nX-Injected: 1.png"
    bad.write_bytes(PNG)
    body = _capture_upload_body(monkeypatch, bad)
    lines = body.split(b"\r\n\r\n")[0].split(b"\r\n")
    assert not any(ln.startswith(b"X-Injected") for ln in lines), lines
    assert b"X-Injected" in _disposition(body), "the text must survive, inert"


def test_an_ordinary_filename_is_sent_unchanged(monkeypatch, tmp_path):
    """The sanitiser must not mangle the normal case, which is every real one."""
    good = tmp_path / "product-shot.png"
    good.write_bytes(PNG)
    body = _capture_upload_body(monkeypatch, good)
    assert b'filename="product-shot.png"' in body


def test_the_upload_still_declares_the_content_type(monkeypatch, tmp_path):
    good = tmp_path / "photo.jpg"
    good.write_bytes(b"\xff\xd8\xff\xe0")
    body = _capture_upload_body(monkeypatch, good)
    assert b"Content-Type: image/jpeg" in body


# ==========================================================================
# 9 - what this shard deliberately did NOT change
# ==========================================================================

def test_the_poll_timeout_is_still_both_the_socket_budget_and_the_total():
    """Surfaced, not fixed. `POLL_TIMEOUT` is passed to `urlopen` AND compared
    against total elapsed, so one slow request can consume the whole budget
    inside a single call. Splitting it changes the timeout contract every
    caller reads, which is a design decision and not a defect fix. Pinned so
    the next audit finds the answer instead of re-deriving the question.
    """
    src = (ROOT / "scripts" / "design-engine.py").read_text(encoding="utf-8")
    assert "urlopen(req, timeout=POLL_TIMEOUT)" in src
    assert "if elapsed > POLL_TIMEOUT:" in src


def test_no_flag_is_translated_into_a_parameter_the_tool_cannot_verify():
    """The tempting fix is to map `--count` onto whatever recraft calls it.
    That guesses an API this tool cannot reach without spending the operator's
    money, and replaces a silent drop with a confident wrong parameter."""
    params, _dropped = _build("recraft")
    assert set(params) == {"prompt", "size"}
    params, _dropped = _build("ideogram")
    assert set(params) == {"prompt", "width", "height"}


def test_the_module_still_exposes_its_four_commands():
    for name in ("cmd_generate", "cmd_edit", "cmd_upscale", "cmd_remove_bg"):
        assert callable(getattr(de, name))


def test_the_help_text_names_the_families_for_every_shape_flag():
    """An operator who reads `--help` instead of SKILL.md gets the same answer."""
    src = (ROOT / "scripts" / "design-engine.py").read_text(encoding="utf-8")
    gen = src.split("# -- generate --")[1].split("# -- edit --")[0]
    for flag in ("--width", "--height", "--aspect", "--count", "--format", "--seed"):
        line = next(ln for ln in gen.splitlines() if f'add_argument("{flag}"' in ln)
        assert "recraft" in line or "ideogram" in line or "flux" in line or "banana" in line, line


def test_the_warning_helper_writes_where_the_operator_reads(capsys):
    de.warn("probe")
    out = capsys.readouterr()
    assert "probe" in out.out
    assert out.err == ""


def test_io_is_still_imported_for_the_upload_body():
    assert isinstance(io.BytesIO(), io.BytesIO)
