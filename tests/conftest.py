"""
Shared fixtures for the ALAO test suite.

Everything here is deliberately file-based: ALAO's analyzer and transformer both
take a Path, detect the encoding themselves, and read the bytes. Faking that with
in-memory strings would test a different code path than the CLI actually runs, so
we always write a real temp file and hand over the Path.
"""

import contextlib
import faulthandler
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# ALAO is a flat set of top-level modules, no package. Put the repo root on the
# path so `import ast_analyzer` works no matter where pytest was invoked from.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# tests/ itself, so modules under tests/test_patterns/ can `from conftest import ...`
# to reuse the small assertion helpers at the bottom of this file.
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from ast_analyzer import ASTAnalyzer  # noqa: E402
from ast_transformer import ASTTransformer  # noqa: E402
from models import detect_file_encoding  # noqa: E402

CLI = REPO_ROOT / "stalker_lua_lint.py"

# The vanilla Anomaly corpus, read-only. Only used by the --corpus smoke test.
VANILLA_CORPUS = Path(
    r"D:\GOG_Games\Gamma\S.T.A.L.K.E.R. GAMMA\Anomaly\gamedata\scripts"
)


# ---------------------------------------------------------------------------
# pytest plumbing
# ---------------------------------------------------------------------------

def pytest_addoption(parser):
    parser.addoption(
        "--corpus",
        action="store_true",
        default=False,
        help="run the read-only smoke test against the real vanilla Anomaly corpus",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--corpus"):
        return
    skip = pytest.mark.skip(reason="needs --corpus")
    for item in items:
        if "corpus" in item.keywords:
            item.add_marker(skip)


# ---------------------------------------------------------------------------
# writing snippets to disk
# ---------------------------------------------------------------------------

def _dedent(src: str) -> str:
    """Let tests write snippets as indented triple-quoted strings."""
    if src.startswith("\n"):
        src = src[1:]
    return textwrap.dedent(src)


@pytest.fixture
def write_script(tmp_path):
    """Write a Lua snippet to a real file and return its Path.

    encoding defaults to utf-8; pass cp1251 to exercise the Russian-comment path.
    """
    counter = {"n": 0}

    def _write(src, name=None, encoding="utf-8", subdir=None):
        counter["n"] += 1
        base = tmp_path if subdir is None else tmp_path / subdir
        base.mkdir(parents=True, exist_ok=True)
        path = base / (name or f"snippet_{counter['n']}.script")
        path.write_bytes(_dedent(src).encode(encoding))
        return path

    return _write


@pytest.fixture
def mods_tree(tmp_path):
    """Build a temp MO2-style tree: <mods>/<Mod>/gamedata/scripts/<file>.

    Call with a dict {mod_name: {file_name: lua_source}}. Returns the <mods> root.
    Sources may be (text, encoding) tuples to force a non-UTF-8 file.
    """
    def _build(spec, root_name="mods"):
        root = tmp_path / root_name
        for mod_name, files in spec.items():
            scripts = root / mod_name / "gamedata" / "scripts"
            scripts.mkdir(parents=True, exist_ok=True)
            for fname, src in files.items():
                encoding = "utf-8"
                if isinstance(src, tuple):
                    src, encoding = src
                (scripts / fname).write_bytes(_dedent(src).encode(encoding))
        root.mkdir(parents=True, exist_ok=True)
        return root

    return _build


# ---------------------------------------------------------------------------
# analyzer / transformer helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def analyze(write_script):
    """Run ASTAnalyzer over a snippet, return the list of Findings."""
    def _analyze(src, cache_threshold=4, experimental=False, name=None):
        path = write_script(src, name=name)
        analyzer = ASTAnalyzer(cache_threshold=cache_threshold, experimental=experimental)
        return analyzer.analyze_file(path)

    return _analyze


@pytest.fixture
def transform(write_script):
    """Run ASTTransformer over a snippet, return the rewritten source.

    Runs dry (no backup, no write) so the snippet file stays pristine; pass
    dry_run=False when you actually want the write/backup behaviour tested.
    """
    def _transform(src, name=None, path=None, **flags):
        if path is None:
            path = write_script(src, name=name)
        flags.setdefault("backup", False)
        flags.setdefault("dry_run", True)
        transformer = ASTTransformer()
        modified, content, edit_count = transformer.transform_file(path, **flags)
        return content

    return _transform


@pytest.fixture
def transform_full(write_script):
    """Like `transform` but returns the whole (modified, content, edit_count)."""
    def _transform(src, name=None, path=None, **flags):
        if path is None:
            path = write_script(src, name=name)
        flags.setdefault("backup", False)
        flags.setdefault("dry_run", True)
        transformer = ASTTransformer()
        return transformer.transform_file(path, **flags)

    return _transform


def findings_named(findings, pattern_name):
    """All findings with an exact pattern name."""
    return [f for f in findings if f.pattern_name == pattern_name]


def find_one(findings, pattern_name):
    """Exactly one finding with this pattern name, else a readable failure."""
    hits = findings_named(findings, pattern_name)
    assert len(hits) == 1, (
        f"expected exactly 1 {pattern_name!r}, got {len(hits)}; "
        f"all findings: {[(f.pattern_name, f.line_num) for f in findings]}"
    )
    return hits[0]


def pattern_names(findings):
    return {f.pattern_name for f in findings}


# ---------------------------------------------------------------------------
# Lua execution via lupa (bundled LuaJIT)
# ---------------------------------------------------------------------------

# lupa 2.x ships several Lua flavours; luajit20 is the one Anomaly actually runs
# (LuaJIT 2.0.x / Lua 5.1 semantics). The default LuaRuntime is Lua 5.5 and would
# happily accept syntax the game rejects, so never use it here.
from lupa import luajit20 as _luajit


def _new_runtime():
    return _luajit.LuaRuntime(unpack_returned_tuples=True)


# Minimal stand-ins for the Anomaly engine globals our snippets touch. Kept
# deliberately dumb and deterministic so differential runs compare cleanly.
LUA_STUB_PRELUDE = """
local _sink = {}
function log(...) _sink[#_sink+1] = {...} end
log1, log2, log3 = log, log, log
function printf(...) _sink[#_sink+1] = {...} end
printe, printd = printf, printf
function DebugLog(...) _sink[#_sink+1] = {...} end
debug_log, trace, dump = DebugLog, DebugLog, DebugLog

-- math.pow is gone in LuaJIT builds compiled without COMPAT; define it so the
-- "before" side of a differential run still executes.
if not math.pow then
    math.pow = function(a, b) return a ^ b end
end
if not table.getn then
    table.getn = function(t) return #t end
end

local vector_mt = {}
vector_mt.__index = vector_mt
function vector_mt:set(x, y, z) self.x, self.y, self.z = x, y, z; return self end
function vector_mt:distance_to(o)
    local dx, dy, dz = self.x - o.x, self.y - o.y, self.z - o.z
    return math.sqrt(dx*dx + dy*dy + dz*dz)
end
function vector_mt:distance_to_sqr(o)
    local dx, dy, dz = self.x - o.x, self.y - o.y, self.z - o.z
    return dx*dx + dy*dy + dz*dz
end
function vector() return setmetatable({x = 0, y = 0, z = 0}, vector_mt) end

local function make_object(id, section)
    local o = {}
    function o:id() return id end
    function o:section() return section end
    function o:name() return section .. "_" .. tostring(id) end
    function o:position() return vector():set(id, 0, 0) end
    function o:health() return 1.0 end
    function o:parent() return nil end
    function o:best_enemy() return nil end
    function o:active_item() return nil end
    return o
end
make_object_stub = make_object

db = {actor = make_object(0, "actor"), storage = {}}

local _sim = {}
function _sim:object(id) return make_object(id, "sim_obj") end
function _sim:actor() return db.actor end
function _sim:story_object(id) return make_object(id, "story") end
function alife() return _sim end

level = {}
function level.object_by_id(id) return make_object(id, "lvl_obj") end
function level.get_target_obj() return nil end
function level.vertex_position(v) return vector() end
function level.name() return "l01_escape" end

local _ini = {}
function _ini:r_string(s, f) return s .. "." .. f end
function system_ini() return _ini end
game_ini = system_ini

local _dev = {precache_frame = 0, time_delta = 16}
function device() return _dev end

local _console = {}
function _console:execute(c) end
function get_console() return _console end

local _hud = {}
function get_hud() return _hud end

function get_story_object(sid) return make_object(1, "story") end
function get_object_by_name(n) return make_object(2, n) end
function time_global() return 1000 end
"""


@pytest.fixture(scope="session")
def lua_stub_prelude():
    return LUA_STUB_PRELUDE


def luajit_compiles(src):
    """Return (ok, error_message) for compiling `src` under the bundled LuaJIT.

    Compile only, never run - we just want the syntax verdict. `src` may be a
    str or bytes; bytes get decoded with the same detector ALAO uses at runtime.
    """
    if isinstance(src, (bytes, bytearray)):
        src = bytes(src).decode("cp1251", errors="replace")
    lua = _new_runtime()
    # Always hand back exactly two values - loadstring returns one value on
    # success, and lupa would then have nothing to unpack into (ok, err).
    loader = lua.eval("""
        function(s)
            local f, e = loadstring(s)
            if f then return true, '' else return false, tostring(e) end
        end
    """)
    ok, err = loader(src)
    if not ok:
        return False, err
    return True, None


def luajit_compiles_path(path):
    """Compile-check a file on disk, honouring ALAO's own encoding detection."""
    encoding = detect_file_encoding(Path(path))
    return luajit_compiles(Path(path).read_text(encoding=encoding))


@pytest.fixture
def compiles():
    """Assert-friendly wrapper: raises with the LuaJIT message on failure."""
    def _compiles(src):
        ok, err = luajit_compiles(src)
        assert ok, f"LuaJIT rejected the transformed source: {err}\n---\n{src}"
        return True

    return _compiles


def _run_in_fresh_runtime(src, fn_name, args, prelude=LUA_STUB_PRELUDE):
    """Load prelude + src into a brand new runtime, call fn_name(*args)."""
    lua = _new_runtime()
    lua.execute(prelude)
    lua.execute(src)
    fn = lua.globals()[fn_name]
    assert fn is not None, f"{fn_name!r} is not defined after loading the snippet"
    return fn(*args)


@contextlib.contextmanager
def _quiet_faulthandler():
    """Silence faulthandler for the duration of a deliberately failing Lua call.

    LuaJIT unwinds errors through Windows SEH. Even when the error is caught by
    Lua's own pcall, faulthandler sees the exception code and dumps a full
    traceback, which buries the real test output. Nothing has actually crashed.
    """
    was_enabled = faulthandler.is_enabled()
    if was_enabled:
        faulthandler.disable()
    try:
        yield
    finally:
        if was_enabled:
            faulthandler.enable()


def lua_pcall(src, fn_name, *args, prelude=LUA_STUB_PRELUDE):
    """Call fn_name under Lua's own pcall; return (ok, result_or_error_string).

    Errors are caught inside the VM. Letting a LuaJIT error unwind out through
    lupa works, but on Windows it trips faulthandler and dumps a scary SEH
    traceback into the test log for what is a perfectly expected failure.
    """
    lua = _new_runtime()
    lua.execute(prelude)
    lua.execute(_dedent(src))
    probe = lua.eval(r"""
        function(f, ...)
            local ok, res = pcall(f, ...)
            if ok then return true, res else return false, tostring(res) end
        end
    """)
    fn = lua.globals()[fn_name]
    assert fn is not None, f"{fn_name!r} is not defined after loading the snippet"
    with _quiet_faulthandler():
        ok, res = probe(fn, *args)
    return bool(ok), res


@pytest.fixture
def lua_call():
    """Fixture wrapper around `lua_pcall`."""
    return lua_pcall


def _normalize(value):
    """Flatten lupa return values into something comparable across runtimes."""
    lupa = _luajit

    if isinstance(value, tuple):
        return tuple(_normalize(v) for v in value)
    if lupa.lua_type(value) == "table":
        # Compare tables structurally. Deterministic on the array part, which is
        # all our snippets build; the hash part is sorted by repr for stability.
        array = [_normalize(v) for v in value.values()]
        try:
            array.sort(key=repr)
        except TypeError:
            pass
        return ("<table>", tuple(array))
    if lupa.lua_type(value) == "function":
        return "<function>"
    return value


@pytest.fixture
def run_both():
    """Differential helper: run original and transformed code, compare results.

    Each side gets its own LuaRuntime with a fresh copy of the stubbed engine
    globals, so caching a stubbed singleton on one side can't leak to the other.
    Returns the normalized (original_result, transformed_result) pair after
    asserting they match.
    """
    def _run_both(original, transformed, fn_name, *args, prelude=LUA_STUB_PRELUDE):
        original = _dedent(original)
        transformed = _dedent(transformed)

        ok, err = luajit_compiles(prelude + "\n" + transformed)
        assert ok, f"transformed source does not compile: {err}\n---\n{transformed}"

        before = _normalize(_run_in_fresh_runtime(original, fn_name, args, prelude))
        after = _normalize(_run_in_fresh_runtime(transformed, fn_name, args, prelude))
        assert before == after, (
            f"behaviour changed for {fn_name}{args!r}: "
            f"original -> {before!r}, transformed -> {after!r}\n"
            f"--- original ---\n{original}\n--- transformed ---\n{transformed}"
        )
        return before, after

    return _run_both


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------

@pytest.fixture
def run_cli():
    """Run stalker_lua_lint.py as a subprocess; return CompletedProcess.

    Subprocess rather than calling main() so we exercise the real argparse path,
    the multiprocessing setup and the exit code, exactly as a user would.
    """
    def _run(*args, cwd=None, timeout=180, check=False, stdin=""):
        cmd = [sys.executable, str(CLI)] + [str(a) for a in args]
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else str(REPO_ROOT),
            # --revert and --clean-backups prompt; feed them an answer or they
            # die on EOFError instead of doing the thing under test
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
        if check:
            assert proc.returncode == 0, (
                f"CLI failed ({proc.returncode})\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
            )
        return proc

    return _run


@pytest.fixture
def read_json():
    def _read(path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    return _read
