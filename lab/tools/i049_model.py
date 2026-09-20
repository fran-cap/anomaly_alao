"""I-049: build a faithful offline model of the two dispatch arms.

What is modelled, and why it has to be modelled rather than loaded: the real
`drx_da_main.script` is 3.5k lines that index `level`, `alife`, `game_difficulties`
and a dozen other engine tables at module scope, so it cannot be loaded under
lupa. What I changed, though, is only the registration + throttle + dispatch
path, and that path is small enough to reproduce EXACTLY:

  * `hspairs`, `sift_down_*`, `safe_order`, `sort_func_values_ascend` and
    `make_callback` are lifted VERBATIM out of the live loose
    `Anomaly/gamedata/scripts/{_g_patches,axr_main}.script` (the live winners:
    the only mod shipping `axr_main.script` is 420- Tactical Compass, which is
    disabled in the G.A.M.M.A profile; `_g_patches.script` is loose only).
    Extraction asserts on the anchors, so a GAMMA update breaks this loudly.
  * `register_callback` / `unregister_callback` / `callbacks` are lifted
    verbatim from `drx_da_main.script` (original arm) and from the file the
    I-049 patcher produces (patched arm).
  * the per-anomaly work itself is replaced, in BOTH arms, by the same recorder
    stub. The patcher lifts the body byte-for-byte (minus one tab) and asserts
    it, so the body is not what a differential test can inform.

`time_global()` is stubbed as a per-frame integer clock, which is what
`Device.dwTimeGlobal` is: the engine advances it once per frame in `OnFrame`,
so it does not move while a callback pass runs. That stub is the load-bearing
assumption behind "one sample for the whole pass is identical"; if it were
wrong -- if time_global() could advance mid-pass -- the patched arm would
throttle a handful of binders one frame later than the original on a frame
where the millisecond ticks over mid-dispatch. Everything else the stub hides
(sound, detector UI, positions) lives inside the lifted body and is identical
by construction.
"""
from __future__ import annotations

import re
from pathlib import Path

GAME = Path(r"D:\GOG_Games\Gamma\S.T.A.L.K.E.R. GAMMA")
LOOSE = GAME / "Anomaly" / "gamedata" / "scripts"
G_PATCHES = LOOSE / "_g_patches.script"
AXR_MAIN = LOOSE / "axr_main.script"
DRX = (Path(r"C:\code\GIT\anomaly_alao\lab\coord\overlays\ref3-alao-b")
       / "gamedata" / "scripts" / "drx_da_main.script")

NL = "\r\n"


def _read(p: Path) -> str:
    return p.read_bytes().decode("utf-8", errors="strict") if p == DRX \
        else p.read_bytes().decode("cp1251")


def _between(text: str, start: str, end: str, *, what: str,
             include_end: bool = True) -> str:
    i = text.find(start)
    assert i >= 0, f"{what}: start anchor missing"
    j = text.find(end, i + len(start))
    assert j >= 0, f"{what}: end anchor missing"
    return text[i:j + len(end)] if include_end else text[i:j]


def engine_lua() -> str:
    """hspairs + make_callback, verbatim, plus the stubs they need."""
    gp = _read(G_PATCHES)
    heap = _between(
        gp,
        "-- Custom order via order parameter (t, a, b) as default spairs",
        "    end\r\nend\r\n",           # end of _G.hspairs
        what="hspairs block",
    )
    axr = _read(AXR_MAIN)
    mk = _between(axr, "function make_callback(name,...)", "\r\nend\r\n",
                  what="make_callback")
    assert "spairs(intercepts[name], sort_func_values_ascend)" in mk

    return "\n".join([
        "local math_floor = math.floor",
        "local nil_func = function() return nil end",
        "_G.sort_func_values_ascend = function(t, a, b) return t[a] < t[b] end",
        heap.replace("_G.hspairs", "hspairs_local").replace(NL, "\n"),
        "_G.hspairs = hspairs_local",
        # the live _G.spairs routes a non-default order function to hspairs
        "_G.spairs = function(t, order)",
        "  if order and order ~= sort_func_keys_ascend then return hspairs_local(t, order) end",
        "  error('unexpected spairs call')",
        "end",
        "",
        "-- axr_main, reduced to the intercept bookkeeping the callback path uses",
        "local intercepts = { actor_on_update = {} }",
        "local next_index = { actor_on_update = 1 }",
        "function RegisterScriptCallback(name, f)",
        "  intercepts[name][f] = next_index[name]",
        "  next_index[name] = next_index[name] + 1",
        "end",
        "function UnregisterScriptCallback(name, f) intercepts[name][f] = nil end",
        mk.replace(NL, "\n"),
        "_G.SendScriptCallback = make_callback",
        "function listener_count(name) local n=0 for _ in pairs(intercepts[name]) do n=n+1 end return n end",
        "",
        "-- engine stubs",
        "_G.CLOCK = 0",
        "function time_global() return CLOCK end",
        "function printf() end",
        "_G.TRACE = {}",
        "_G.TRACE_N = 0",
        "function record(key, tg)",
        "  TRACE_N = TRACE_N + 1",
        "  TRACE[TRACE_N] = CLOCK .. '|' .. key .. '|' .. tg",
        "end",
        "",
    ])


def _drx_callbacks(text: str) -> str:
    return _between(text, "callbacks = {}", NL + "-- Get psy table",
                    what="drx callbacks block",
                    include_end=False).replace(NL, "\n")


# The recorder that stands in for the anomaly body in BOTH arms. It reproduces
# the two state writes the real body always makes, so the throttle evolves the
# same way; the rest of the body is lifted verbatim by the patcher.
BODY_STUB = """
local function anomaly_body(self, level_name, tg)
  record(self.key, tg)
  -- stand-in for the real body's timer update; deterministic, no engine calls
  self.on_update_timer = self.on_update_timer_default * (1 + (self.seed + tg) % 7)
  if self.on_update_timer > self.on_update_timer_max then
    self.on_update_timer = self.on_update_timer_max
  end
  self.on_update_time = tg + self.on_update_timer
end
"""

ORIG_REG = """
function spawn_binder(key, seed, start_time)
  local self = {
    key = key, seed = seed,
    on_update_timer_default = 100,
    on_update_timer_max = 7500,
    on_update_timer = 100,
    on_update_time = start_time,
  }
  local level_name = "zaton"
  register_callback("actor_on_update", function()
    local tg = time_global()
    if tg < self.on_update_time then return end
    anomaly_body(self, level_name, tg)
  end, nil, key)
  return self
end

function destroy_binder(key) unregister_callback(key) end
function registered_count() return listener_count("actor_on_update") end
"""

PATCHED_REG = """
function spawn_binder(key, seed, start_time)
  local self = {
    key = key, seed = seed,
    on_update_timer_default = 100,
    on_update_timer_max = 7500,
    on_update_timer = 100,
    on_update_time = start_time,
  }
  local level_name = "zaton"
  register_anomaly_update(key, self, level_name)
  return self
end

function destroy_binder(key) unregister_callback(key) end
function registered_count() local n = anomaly_update_count() return n end
"""


def arm_lua(patched_drx: str | None = None) -> str:
    """Build one arm. patched_drx = the text the I-049 patcher produced."""
    if patched_drx is None:
        block = _drx_callbacks(_read(DRX))
        return "\n".join([engine_lua(), BODY_STUB, block, ORIG_REG])

    block = _drx_callbacks(patched_drx)
    # the dispatcher block lives further down the patched file; lift it too
    disp = _between(
        patched_drx,
        "local au_self  = {}",
        "function anomaly_update_count()\r\n\treturn au_n, au_holes\r\nend",
        what="I-049 dispatcher block",
    ).replace(NL, "\n")
    # the lifted real body is replaced by the shared recorder stub
    i = disp.find("local function anomaly_update_body(self, level_name, tg)")
    j = disp.find("\nlocal function au_compact()")
    assert i > 0 and j > i, "cannot isolate the lifted body"
    disp = disp[:i] + "local anomaly_update_body = anomaly_body\n" + disp[j:]
    return "\n".join([engine_lua(), BODY_STUB, block, disp, PATCHED_REG])


def lifted_body_is_verbatim(original: str, patched: str) -> bool:
    """The patcher's own invariant, checked from the outside."""
    orig_body = _between(
        original,
        '\tregister_callback("actor_on_update", function()' + NL
        + "\t\tlocal tg = time_global()" + NL
        + "\t\tif tg < self.on_update_time then return end" + NL,
        "\tend, nil, self.update_key)" + NL,
        what="original closure",
    )
    orig_body = orig_body.split(NL, 3)[3]
    orig_body = orig_body[: orig_body.rindex("\tend, nil, self.update_key)" + NL)]
    dedent = NL.join(
        (ln[1:] if ln.startswith("\t") else ln) for ln in orig_body.split(NL)
    )
    new_body = _between(
        patched,
        "local function anomaly_update_body(self, level_name, tg)" + NL,
        NL + "local function au_compact()",
        what="patched body",
    )
    new_body = new_body.split(NL, 1)[1]
    new_body = new_body[: new_body.rindex("end" + NL + NL + "local function au_compact()")]
    return dedent.strip(NL) == new_body.strip(NL)


ANOM_KEY = re.compile(r"^[a-z_]+_\d+$")
