"""
AST-based Lua code analyzer implemented with https://pypi.org/project/luaparser/
"""

from luaparser import ast
from luaparser.astnodes import (
    Node, Chunk, Block,
    Function, LocalFunction, Method, AnonymousFunction,
    Assign, LocalAssign,
    While, Repeat, Fornum, Forin,
    If, ElseIf,
    Call, Invoke,
    Index, Name, String, Number, Nil, TrueExpr, FalseExpr,
    Table, Field,
    Concat, AddOp, SubOp, MultOp, FloatDivOp, ModOp, ExpoOp,
    AriOp, RelOp, BitOp,
    Return, Break,
    UMinusOp, UBNotOp, ULNotOp, ULengthOP,
    AndLoOp, OrLoOp,
    LessThanOp, GreaterThanOp, LessOrEqThanOp, GreaterOrEqThanOp, EqToOp, NotEqToOp,
    SemiColon, Comment,
)
from typing import List, Dict, Set, Optional, Tuple, Any
from dataclasses import dataclass, field
from pathlib import Path
from collections import defaultdict
import sys
import io
import re

from models import Finding, detect_file_encoding


# Hot callbacks that run frequently
HOT_CALLBACKS = frozenset({
    'actor_on_update', 'actor_on_first_update',
    'npc_on_update', 'monster_on_update',
    'on_key_press', 'on_key_release', 'on_key_hold',
    'actor_on_weapon_fired', 'actor_on_hud_animation_end',
    'on_before_hit', 'on_hit',
    'physic_object_on_hit_callback',
    'npc_on_before_hit', 'monster_on_before_hit',
    'npc_on_hit_callback', 'monster_on_hit_callback',
    'actor_on_feel_touch',
    'actor_on_item_take', 'actor_on_item_drop',
    'actor_on_item_use',
})

# Per-frame callbacks detection.
#
# I-010: this used to be four names and caught roughly a quarter of the real
# per-frame bodies in the corpus. The engine fires `*_on_update` on every
# frame for every registered script, and every binder / UI class does its
# per-frame work in an `:update` (or `:Update`) method. Both are now covered,
# see PER_FRAME_METHOD_NAMES and _is_per_frame_callback_name below.
#
# `*_on_first_update` is deliberately NOT here: it runs once.
PER_FRAME_CALLBACKS = frozenset({
    'actor_on_update',
    'npc_on_update',
    'monster_on_update',
    'physic_object_on_update',
    'squad_on_update',
    'hud_update',
})

# Method names that mean "this body runs every frame". `update` is the binder
# convention (`object_binder:update(delta)`), `Update` the UI/MCM one.
PER_FRAME_METHOD_NAMES = frozenset({'update', 'Update'})


def _is_per_frame_callback_name(name: str) -> bool:
    """True for engine callbacks the engine calls once per frame."""
    if not name:
        return False
    if name in PER_FRAME_CALLBACKS:
        return True
    # e.g. `bas_actor_on_update`, a mod's own prefixed copy of the callback
    return name.endswith('_on_update') and 'first_update' not in name

# Bare globals that benefit from caching
# I-040: how many time_global() calls in one function body justify
# `local tg = time_global()`. Two, not `cache_threshold`: every call is an
# engine C call that aborts the LuaJIT trace, so the body is running in the
# interpreter either way and each extra call costs a full Lua->C round trip
# (measured 26 ns interpreted with os.clock as a lua_CFunction stand-in, 5 ns
# with a plain Lua closure - the engine's luabind-bound call is at least the
# latter and probably nearer the former). db.actor & friends stay at 4.
TIME_GLOBAL_CACHE_THRESHOLD = 2

CACHEABLE_BARE_GLOBALS = frozenset({
    'pairs', 'ipairs', 'next', 'type', 'tostring', 'tonumber',
    'unpack', 'select', 'rawget', 'rawset',
})

# these are less beneficial to cache (error handling, output)
BARE_GLOBALS_UNSAFE_TO_CACHE = frozenset({
    'pcall', 'xpcall', 'error', 'assert', 'print',
})

# Module functions that benefit from caching
CACHEABLE_MODULE_FUNCS = {
    'math': frozenset({
        'floor', 'ceil', 'abs', 'min', 'max', 'sqrt', 'sin', 'cos', 'tan',
        'random', 'pow', 'log', 'exp', 'atan2', 'atan', 'asin', 'acos',
        'deg', 'rad', 'fmod', 'modf', 'huge',
    }),
    'string': frozenset({
        'find', 'sub', 'gsub', 'match', 'gmatch', 'format',
        'lower', 'upper', 'len', 'rep', 'byte', 'char', 'reverse',
    }),
    'table': frozenset({
        'insert', 'remove', 'concat', 'sort', 'getn', 'unpack',
    }),
    'bit': frozenset({
        'band', 'bor', 'bxor', 'bnot', 'lshift', 'rshift', 'arshift', 'rol', 'ror',
    }),
}

# --- scratch-vector reuse (vector_alloc_in_loop) ---------------------------
#
# A `vector()` built inside a loop can be hoisted out and reused with :set()
# ONLY if the object never survives the iteration. Everything below is about
# proving that. Two whitelists, both deliberately tiny: if a name is not in
# here we assume the callee keeps the reference, because guessing wrong here
# means a live object silently mutating under the engine's feet.

# Methods you may call ON the scratch vector itself. They either read it or
# mutate it in place and return self - either way the vector stays ours.
VECTOR_SELF_METHODS = frozenset({
    'set', 'add', 'sub', 'mul', 'div', 'mad', 'invert', 'normalize',
    'magnitude', 'distance_to', 'distance_to_sqr',
    'distance_to_xz', 'distance_to_xz_sqr',
    'dotproduct', 'crossproduct', 'similar', 'getH', 'getP', 'abs',
})

# Methods that take a vector as an ARGUMENT and copy the three floats out
# instead of keeping the object. Passing the scratch to one of these is not
# an escape. Keep this list short and only add a name you can point at in the
# engine source - a Lua class with a method of the same name that stores its
# argument would silently break, so `set_position` and friends stay out until
# something in the corpus actually needs them.
VECTOR_ARG_SAFE_METHODS = frozenset({
    'distance_to', 'distance_to_sqr', 'distance_to_xz', 'distance_to_xz_sqr',
    'play_at_pos',
})

# Debug/logging function patterns
DEBUG_FUNCTIONS = frozenset({
    'print', 'printf', 'printe', 'printd', 'log',
    'log1', 'log2', 'log3',
    'DebugLog', 'debug_log', 'trace', 'dump',
})

# functions that have direct replacement patterns (not cached)
DIRECT_REPLACEMENT_FUNCS = frozenset({
    'table.insert', 'table.getn', 'string.len',
})

# Index-style accesses that are worth caching when repeated (property reads,
# not function calls). `db.actor` and `db.storage` are property accesses that
# resolve through metatable lookups in the engine - caching the reference in
# a local once is a real win in hot code. The repeated-call analysis treats
# entries here equivalently to function calls in expensive_calls
EXPENSIVE_INDEXES = frozenset({'db.actor'})

# ---------------------------------------------------------------------------
# LuaJIT 2.0 trace-abort awareness (I-013)
#
# Everything in here was MEASURED, not recalled: lab/tools/nyi_probe.py runs
# each construct in a hot loop under lupa.luajit20 (the same 2.0 VM the game
# ships) with jit.attach's trace hook attached, and records whether a trace
# stopped or aborted and why. The full table lives in
# lab/reports/luajit20-nyi.md. Do NOT "correct" this from the LuaJIT wiki's
# NYI page - that page documents 2.1 and is wrong for our target.
#
# Why we care: a trace that aborts means the enclosing loop/function runs in
# the interpreter. ALAO's transforms measure 1.00-1.03x on a compiled trace
# and 1.05-1.63x interpreted, and the ones it does not ship yet split
# violently by mode. So the mode decides which rewrite is worth applying.

# Called anywhere -> the trace aborts. `oerr` in the probe named the builtin.
LUAJIT20_NYI_FUNCS = {
    # base
    'pairs': 'NYIFF: pairs',
    'next': 'NYIFF: next',
    'unpack': 'NYIFF: unpack',
    'error': 'NYIFF: error',
    'newproxy': 'NYIFF: newproxy',
    'loadstring': 'NYIFF: loadstring',
    'dofile': 'NYIFF: dofile',
    'collectgarbage': 'NYIFF: collectgarbage',
    'print': 'NYIFF: print',
    # string - sub/byte/len DO compile, the pattern-matching half does not
    'string.format': 'NYIFF: string.format',
    'string.find': 'NYIFF: string.find',
    'string.match': 'NYIFF: string.match',
    'string.gmatch': 'NYIFF: string.gmatch',
    'string.gfind': 'NYIFF: string.gfind',
    'string.gsub': 'NYIFF: string.gsub',
    'string.rep': 'NYIFF: string.rep',
    'string.upper': 'NYIFF: string.upper',
    'string.lower': 'NYIFF: string.lower',
    'string.reverse': 'NYIFF: string.reverse',
    'string.char': 'NYIFF: string.char',
    # table - insert/remove are conditional, see LUAJIT20_NYI_VARIANTS
    'table.concat': 'NYIFF: table.concat',
    'table.sort': 'NYIFF: table.sort',
    'table.foreach': 'NYIFF: table.foreach',
    'table.foreachi': 'NYIFF: table.foreachi',
    # math - everything else in math compiles, including random()
    'math.fmod': 'NYIFF: math.fmod',
    'math.randomseed': 'NYIFF: math.randomseed',
    # os / io
    'os.clock': 'NYIFF: os.clock',
    'os.time': 'NYIFF: os.time',
    'os.date': 'NYIFF: os.date',
    'os.getenv': 'NYIFF: os.getenv',
    'io.open': 'NYIFF: io.open',
    'io.lines': 'NYIFF: io.lines',
    # coroutines
    'coroutine.create': 'NYIFF: coroutine.create',
    'coroutine.resume': 'NYIFF: coroutine.resume',
    'coroutine.yield': 'NYIFF: coroutine.yield',
    'coroutine.wrap': 'NYIFF: coroutine.wrap',
}

# Fastfuncs that DO compile in their common shape and abort only in another.
# name -> (predicate on the arg count that means "this call aborts", reason)
LUAJIT20_NYI_VARIANTS = {
    # table.insert(t, v) compiles; the 3-arg positional insert does not
    'table.insert': (lambda argc: argc >= 3, 'NYIFFU: table.insert (positional form)'),
    # table.remove(t) compiles; table.remove(t, pos) does not
    'table.remove': (lambda argc: argc >= 2, 'NYIFFU: table.remove (positional form)'),
}

# `tostring(number)` compiles, `tostring(table)` aborts NYIFFU. We cannot know
# the runtime type from the AST, so tostring is left out of both tables above
# and only counted when it feeds a concat (which aborts anyway).

# Bytecodes LuaJIT 2.0 cannot record at all.
LUAJIT20_NYI_BYTECODE = {
    'concat': 'NYIBC: BC_CAT (string concatenation)',
    'closure_in_loop': 'NYIBC: BC_FNEW (closure creation)',
}

# Engine API. LuaJIT aborts with NYICF on ANY C function that is not one of
# its own fastfuncs, and every function the Anomaly engine registers through
# LuaBind is exactly that. Confirmed by probing a non-fastfunc C function
# directly: abort code 13, `oerr` a bare function pointer.
#
# These are the module-style namespaces. Method calls on engine userdata are
# handled by ENGINE_NYI_METHODS.
ENGINE_NYI_NAMESPACES = frozenset({
    'level', 'game', 'alife', 'device', 'relation_registry', 'game_graph',
    'actor_stats', 'main_menu', 'utils_xml', 'ui_events',
})

# Bare engine globals (C functions) that abort a trace when called.
ENGINE_NYI_GLOBALS = frozenset({
    'time_global', 'alife', 'get_console', 'get_hud', 'device',
    'level_object_by_id', 'system_ini', 'game_ini', 'create_ini_file',
    'alife_object', 'alife_create', 'alife_release',
    'get_safe_sound_object', 'sound_object', 'vector', 'vector2',
    'CScriptXmlInit', 'GetARGB', 'GetFontLetterica16Russian',
})

# Method names that only exist on engine userdata. A `:name()` invoke with one
# of these is an engine C call. Deliberately conservative: an unknown method
# name is assumed to be Lua (and therefore compilable), so the classifier
# under-reports rather than over-reports "interpreted".
ENGINE_NYI_METHODS = frozenset({
    'position', 'direction', 'health', 'set_health', 'id', 'section',
    'clsid', 'name', 'alive', 'parent', 'level_vertex_id', 'game_vertex_id',
    'object', 'best_enemy', 'best_danger', 'best_item', 'active_item',
    'active_detector', 'active_slot', 'item_in_slot', 'get_enemy',
    'get_current_outfit', 'character_community', 'character_rank',
    'inventory_for_each', 'iterate_inventory', 'give_info_portion',
    'has_info', 'disable_info_portion', 'transfer_item', 'transfer_money',
    'r_string', 'r_float', 'r_u32', 'r_s32', 'r_bool', 'r_line',
    'line_count', 'section_exists', 'line_exists',
    'distance_to', 'distance_to_sqr', 'distance_to_xz', 'distance_to_center',
    'set', 'add', 'sub', 'mul', 'div', 'normalize', 'magnitude',
    'execute', 'get_float', 'get_integer', 'get_bool',
    'story_object', 'story_id', 'clear_abuse', 'accessible',
})

# Abort codes 6 (LINNER, inner loop in root trace) and 7 (LUNROLL) are trace
# shaping, not NYI, and are deliberately absent from all of the above.

# I-001: below this many iterations the hoisted counter is not worth it - the
# `#t` boundary search is O(log n), so at 5 iterations the rewrite measures
# 1.06x (G2 wants 1.15x) and only clears the bar from ~20 on. Only applied when
# the trip count is a literal we can read; anything dynamic is assumed long.
APPEND_LOOP_MIN_ITERATIONS = 20

# --- string_concat_in_loop: when the table.concat rewrite is actually a win ---
#
# `s = s .. x` in a loop is O(n^2) in theory, so "always rewrite it to
# table.concat" reads like a free win. It isn't. table.concat has setup cost:
# you allocate a parts table, grow it, then allocate the result buffer. For a
# short accumulation that costs MORE than just concatenating a handful of small
# strings, and the rewrite is a measured *regression*.
#
# Measured 2026-09-11 (agent-I039) under lupa.luajit20 with the section-2
# protocol from lab/docs/beam-ideas.md: collectgarbage('collect') before every
# timed run, jit.off(f, true) applied to the chunk itself for the interpreted
# mode, __sink to defeat DCE, best of 9, total work held constant across the
# sweep. Speedup = time(original) / time(rewrite):
#
#   iters:      3     5    10    20    30    50   68   100   200   1000
#   interp   0.45  0.53  0.61  0.82  1.16  1.12 1.31  1.66  5.47  11.99
#   (JIT)    0.60  0.62  0.85  1.23  1.32  2.49 1.32  2.65  8.79  21.46
#
# **The interpreted row is the gate. The JIT row is recorded for completeness
# and is not used in any decision here.** agent-I013 classified all 205 corpus
# sites of this pattern (run 20260911-114408-i013-rerun): 0 sit in a compiled
# body - 120 interpreted, 82 mixed - because BC_CAT is NYI on LuaJIT 2.0.4, so a
# loop containing `..` never compiles no matter how hot it gets. table.concat is
# NYI too, so the rewritten loop is interpreted as well. The JIT column
# describes a machine state that no site of this pattern is ever in.
#
# Those are for the counter-based rewrite ALAO emits. Do not carry the number
# over to the older `p[#p+1]` shape, which ALAO stopped emitting in b4726fe and
# which breaks even around K=100 interpreted rather than ~30 - if you see "~100"
# quoted as this pattern's crossover, it is describing that dead shape.
#
# 30 is where the interpreted row first clears G2's 1.15x bar on both harnesses
# (1.16x and 1.28x at K=30; K=20 fails both at 0.82x and 1.01x).
#
# Independently reproduced by agent-I003's tools/microbench.py at best-of-25 on
# the same protocol: 0.44/0.47 at K=3, 0.97/1.01 at K=20, 1.47/1.28 at K=30,
# 3.00/2.77 at K=100 (jit/interp). Same breakeven, same sign everywhere. Their
# large-K ratios run hotter than mine because their parts are 8 chars rather
# than ~4, so the O(n^2) memcpy in the original arm bites harder - which is the
# whole point below.
#
# Which is to say: "string_concat_in_loop is 8.69x", as the beam had it, was
# never a property of the transform. It was a property of the loop length the
# original measurement happened to use - somewhere in the low hundreds. The two
# sweeps bracket that crossing differently (I-003 puts it at K=100-200, I-039 at
# K=200-500) and on a shared machine neither can place it tighter, so "low
# hundreds" is the honest statement. Quoting a single number for this transform
# is a category error whichever bracket is right; that is why this comment is a
# curve.
#
# Hence: only rewrite when the loop plausibly runs at least this many times. We
# can only *prove* the count for a numeric `for` with literal bounds; for
# pairs/ipairs/expression-bounded loops the count is unknown and the finding is
# still emitted (report-only unless the user opts in), because the enabled GAMMA
# corpus says those are overwhelmingly 3-10 element UI lists.
STRING_CONCAT_BREAKEVEN_ITERS = 30

# Functions/properties that can return nil - calling methods on these without
# nil checks can cause CTD (crash to desktop)
# Format: full_name -> description of when it returns nil
NIL_RETURNING_FUNCTIONS = {
    # level functions
    'level.object_by_id': 'object is offline or does not exist',
    'level.get_target_obj': 'nothing under crosshair',
    'level.get_target_element': 'nothing under crosshair',
    'level.get_target_pos': 'nothing under crosshair',
    'level.vertex_position': 'invalid vertex ID',
    'level.get_view_entity': 'no view entity set',
    
    # alife functions
    'alife': 'called from main menu or during loading',
    'alife().object': 'object does not exist in simulation',
    'alife():object': 'object does not exist in simulation',
    'alife().story_object': 'no object with that story_id',
    'alife():story_object': 'no object with that story_id',
    'alife().actor': 'actor not spawned yet',
    'alife():actor': 'actor not spawned yet',
    
    # game object methods that can return nil
    ':parent': 'object has no parent (not in inventory)',
    ':best_enemy': 'no enemy detected',
    ':best_item': 'no item of interest found',
    ':best_danger': 'no danger detected',
    ':best_weapon': 'no weapon available',
    ':best_cover': 'no cover available',
    ':active_item': 'no weapon/item currently equipped',
    ':active_detector': 'no detector currently active',
    ':object': 'item not in inventory or index out of bounds',
    ':get_enemy': 'no current enemy target',
    ':get_corpse': 'no corpse being investigated',
    ':get_current_outfit': 'no outfit equipped',
    ':item_in_slot': 'slot is empty',
    ':get_helicopter': 'not a helicopter or no helicopter',
    ':get_car': 'not in a vehicle',
    ':get_campfire': 'not a campfire zone',
    ':get_artefact': 'not an artefact',
    ':get_physics_shell': 'object has no physics shell',
    ':spawn_ini': 'no spawn ini defined',
    ':motivation_action_manager': 'not an NPC with action manager',
    ':get_current_holder': 'not in a vehicle/turret',
    ':get_old_holder': 'was not in a vehicle/turret',
    ':memory_position': 'object never seen',
    ':get_dest_enemy': 'no destination enemy',
    ':bone_id': 'bone name does not exist',
    ':bone_position': 'bone does not exist',
    ':bone_direction': 'bone does not exist',
    
    # common patterns
    'db.actor': 'called from main menu or during loading',
    'db.storage': 'storage not initialized',  # db.storage[id] can be nil
    
    # story object helpers
    'get_story_object': 'no object with that story_id',
    'get_object_by_name': 'object not found or offline',
}

# Method patterns that indicate the variable is being nil-checked
# These patterns mean the variable is safe to use after the check
NIL_CHECK_PATTERNS = {
    'if {var} then',
    'if {var} and',
    'if not {var} then return',
    'if not {var} then return end',
    'if {var} == nil then return',
    'if {var} == nil then return end', 
    'if {var} ~= nil then',
    '{var} and {var}:',
    '{var} and {var}.',
}

# Callback parameters that are guaranteed non-nil by the engine
# Format: (callback_name, param_index) - 0-indexed
SAFE_CALLBACK_PARAMS = {
    'actor_on_item_take': {0},      # item
    'actor_on_item_drop': {0},      # item
    'actor_on_item_use': {0},       # item
    'actor_on_trade': {0, 1},       # item, sell_buy  
    'npc_on_death_callback': {0, 1}, # npc, killer
    'monster_on_death_callback': {0, 1}, # monster, killer
    'npc_on_hit_callback': {0},     # npc
    'monster_on_hit_callback': {0}, # monster
    'on_before_hit': {0, 1, 2},     # obj, shit, bone_id
    'physic_object_on_hit_callback': {0}, # obj
    'actor_on_before_death': {0, 1}, # who, flags
    'save_state': {0},              # m_data
    'load_state': {0},              # m_data
}


@dataclass
class Scope:
    """Represents a variable scope (function, loop, block)."""
    name: str
    start_line: int
    end_line: int = -1
    parent: Optional['Scope'] = None
    scope_type: str = 'block'  # 'function', 'loop', 'block'
    is_hot_callback: bool = False

    # for function scopes: the AST node (Function/LocalFunction/Method/AnonymousFunction).
    # used by ast_transformer to locate the function body's actual start position
    # when inserting cache declarations, instead of fragile paren-counting on the
    # declaration line (which breaks for anonymous functions passed as call args, as an examply).
    node: Optional[Any] = None

    # variables declared in this scope
    locals: Set[str] = field(default_factory=set)

    # cached globals in this scope
    cached_globals: Set[str] = field(default_factory=set)

    # function aliases: local_name -> canonical_name (e.g., "tinsert" -> "table.insert")
    func_aliases: Dict[str, str] = field(default_factory=dict)

    def __hash__(self):
        # use object's actual id for hashing
        return id(self)

    def __eq__(self, other):
        if isinstance(other, Scope):
            return self is other
        return False


@dataclass
class CallInfo:
    """Information about a function call.

    `if_chain_path` is the sequence of (if_id, branch_index) tuples - one per
    enclosing if-chain - that must all be entered to reach this call. It
    powers nested branch-aware counting. An empty tuple means the call runs
    unconditionally (within whatever function/loop scope it lives in).
    """
    full_name: str          # "table.insert", "db.actor", "pairs"
    module: Optional[str]   # "table", "db", None
    func: str               # "insert", "actor", "pairs"
    args: List[Any]         # AST nodes of arguments
    line: int
    node: Node
    scope: Scope
    in_loop: bool = False
    loop_depth: int = 0
    if_chain_path: Tuple[Tuple[int, int], ...] = ()
    # when the call went through a local alias (`local tinsert = table.insert`
    # then `tinsert(t, v)`), this is the alias name as written. full_name is
    # already the canonical one. None for a direct call.
    alias_name: Optional[str] = None


@dataclass
class IndexInfo:
    """Information about a property access (e.g. `db.actor`).

    Mirrors CallInfo enough that _analyze_repeated_calls_in_scope and
    _edit_repeated_calls can iterate either kind through the same code paths.
    Only single-level dot-access is recorded (Index(Name, Name)) - that's the
    only shape we know how to safely cache as `local x = base.field`.
    """
    full_name: str          # "db.actor"
    module: str             # "db"
    func: str               # "actor"  (named `func` for duck-compat with CallInfo)
    line: int
    node: Node
    scope: Scope
    in_loop: bool = False
    loop_depth: int = 0
    if_chain_path: Tuple[Tuple[int, int], ...] = ()


@dataclass
class AssignInfo:
    """Information about an assignment."""
    target: str             # variable name
    value_type: str         # 'call', 'index', 'concat', 'literal', 'other'
    value_repr: str         # string representation
    line: int
    node: Node
    scope: Scope
    is_local: bool = False
    in_loop: bool = False


@dataclass
class ConcatInfo:
    """Information about string concatenation."""
    target: Optional[str]   # variable being assigned to
    left_var: Optional[str]  # left operand if it's a variable
    line: int
    scope: Scope
    in_loop: bool = False
    loop_depth: int = 0
    loop_scope: Optional[Scope] = None  # the innermost loop scope
    right_expr: Optional[str] = None    # string repr of right side of concat


@dataclass
class NilSourceInfo:
    """Information about a variable assigned from a nil-returning function."""
    var_name: str           # variable name
    source_call: str        # the call that might return nil (e.g. "level.object_by_id(id)")
    source_func: str        # just the function name (e.g. "level.object_by_id")
    assign_line: int        # line where assignment happened
    scope: Scope            # scope of the variable
    is_local: bool          # whether it's a local variable
    is_guarded: bool = False  # whether a nil check was found after assignment


@dataclass 
class NilAccessInfo:
    """Information about accessing a potentially nil variable."""
    var_name: str           # the variable being accessed
    access_type: str        # 'method' or 'index'
    access_call: str        # full call (e.g. "obj:section()")
    access_line: int
    nil_source: NilSourceInfo  # the nil source info
    is_safe_to_fix: bool = False  # whether this can be auto-fixed


@dataclass
class DeadCodeInfo:
    """Information about dead/unreachable code."""
    dead_type: str          # 'after_return', 'after_break', 'if_false', 'while_false', 'unused_local_var', 'unused_local_func'
    start_line: int
    end_line: int
    scope_name: str
    description: str
    is_safe_to_remove: bool = False  # True only for 100% safe cases
    code_preview: str = ""
    node: Optional[Node] = None


@dataclass
class LocalVarInfo:
    """Information about a local variable for dead code analysis."""
    name: str
    assign_line: int
    scope: Scope
    is_read: bool = False       # has the variable been read?
    is_function: bool = False   # is it a local function?
    # how many times the name was actually read. is_read is just (read_count > 0),
    # kept as-is because a lot of code reads it. The count is what tells us
    # whether rewriting N uses away would orphan the local (see I-038).
    read_count: int = 0
    read_lines: List[int] = field(default_factory=list)
    is_loop_var: bool = False   # is it a for loop variable?
    is_param: bool = False      # is it a function parameter?


@dataclass
class PerFrameCallbackInfo:
    """Information about a per-frame callback function for performance analysis."""
    name: str
    start_line: int
    end_line: int
    scope: Scope
    # Collected during analysis pass
    expensive_calls: List[CallInfo] = field(default_factory=list)
    loop_count: int = 0
    uncached_globals: List[str] = field(default_factory=list)


@dataclass
class DistanceComparisonInfo:
    """Information about distance_to() used in comparison (can be optimized to distance_to_sqr())."""
    line: int
    source_obj: str          # the object calling distance_to (e.g., "pos", "actor:position()")
    target_obj: str          # the argument to distance_to (e.g., "target_pos")
    comparison_op: str       # '<', '<=', '>', '>='
    threshold_value: float   # the numeric threshold (e.g., 10)
    threshold_node: Node     # the AST node for the threshold (for replacement)
    full_node: Node          # the full comparison node
    invoke_node: Node        # the distance_to invoke node


@dataclass
class NyiSiteInfo:
    """One construct LuaJIT 2.0 cannot record into a trace (I-013)."""
    kind: str               # 'fastfunc' | 'variant' | 'bytecode' | 'cfunc'
    name: str               # 'pairs', '..', 'level.object_by_id', ...
    reason: str             # the abort as the probe reported it
    line: int
    scope: Scope
    in_loop: bool = False
    if_chain_path: Tuple[Tuple[int, int], ...] = ()


@dataclass
class JitModeInfo:
    """How a single function body behaves under LuaJIT 2.0's trace compiler."""
    mode: str               # 'compiled' | 'mixed' | 'interpreted'
    func_name: str
    start_line: int
    sites: List[NyiSiteInfo] = field(default_factory=list)

    @property
    def reasons(self) -> List[str]:
        """Distinct abort reasons, most common first."""
        counts = defaultdict(int)
        for s in self.sites:
            counts[s.reason] += 1
        return [r for r, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]

    @property
    def lines(self) -> List[int]:
        return sorted({s.line for s in self.sites})


@dataclass
class VectorAllocationInfo:
    """Information about vector() allocation in a loop."""
    line: int
    call_node: Node
    loop_depth: int
    scope: Scope
    in_per_frame_callback: bool = False


class ASTAnalyzer:
    """AST-based Lua code analyzer."""

    def __init__(self, cache_threshold: int = 4, experimental: bool = False):
        self.cache_threshold = cache_threshold
        self.experimental = experimental
        self.reset()

    def reset(self):
        """Reset analyzer state for reuse."""
        self.findings: List[Finding] = []
        self.scopes: List[Scope] = []
        self.current_scope: Optional[Scope] = None
        self.global_scope: Optional[Scope] = None
        self.calls: List[CallInfo] = []
        self.indexes: List[IndexInfo] = []
        # Index nodes that are the callee of a Call/Invoke - they should NOT
        # be recorded as standalone property reads (the corresponding Call /
        # Invoke already accounts for them).
        self._suppress_indexes: Set[int] = set()
        self.assigns: List[AssignInfo] = []
        self.concats: List[ConcatInfo] = []
        self.global_writes: List[Tuple[str, int]] = []
        
        self.nil_sources: Dict[Tuple[int, str], NilSourceInfo] = {}
        self.nil_accesses: List[NilAccessInfo] = []
        self.nil_guards: Set[Tuple[str, int]] = set()
        
        self.dead_code: List[DeadCodeInfo] = []
        self.local_vars: Dict[Tuple[int, str], LocalVarInfo] = {}
        self.local_funcs: Dict[Tuple[int, str], LocalVarInfo] = {}
        self.callback_registrations: Set[str] = set()
        self.per_frame_callbacks: List[PerFrameCallbackInfo] = []
        # I-013: constructs LuaJIT 2.0 cannot trace, and the per-function
        # verdict derived from them. `jit_modes` is keyed by id(function scope)
        # so any later pass can ask "what mode is the body I'm editing in?".
        self.nyi_sites: List[NyiSiteInfo] = []
        self.jit_modes: Dict[int, JitModeInfo] = {}
        self._in_concat: bool = False
        self.distance_comparisons: List[DistanceComparisonInfo] = []
        self.vector_allocations: List[VectorAllocationInfo] = []
        self.assignment_target_ids: Set[int] = set()

        self.source_lines: List[str] = []
        self.source: str = ""
        self.file_path: Optional[Path] = None

        self.loop_depth: int = 0
        self.function_depth: int = 0

        # Stack of (if_id, branch_index) ancestors. Pushed when we enter a
        # branch body, popped when we leave. Calls/indexes record a snapshot
        # of this stack so _count_calls_branch_aware can walk the nested
        # if-chain tree and compute the true max-coexisting-call count.
        self.if_chain_stack: List[Tuple[int, int]] = []

        # cleared per-run so a re-used analyzer can't see a stale tree
        self._ast_tree: Optional[Node] = None

        # why the last analyze_file() bailed out, if it did:
        # (kind, message) with kind in {'encoding', 'parse'}. None on success.
        # analyze_file() still returns [] in those cases (callers depend on
        # that), this is just the out-channel so the CLI can tell "clean file"
        # apart from "could not read/parse it".
        self.last_error: Optional[Tuple[str, str]] = None

    def analyze_file(self, file_path: Path) -> List[Finding]:
        """Analyze a Lua file and return findings."""
        self.reset()
        self.file_path = file_path
        self.last_error = None

        try:
            encoding = detect_file_encoding(file_path)
            self.source = file_path.read_text(encoding=encoding)
            self._file_encoding = encoding
        except Exception as e:
            self.last_error = ('encoding', f'{type(e).__name__}: {e}')
            return []

        self.source_lines = self.source.splitlines()

        try:
            # suppress ANTLR lexer error output during parse
            old_stderr = sys.stderr
            sys.stderr = io.StringIO()
            try:
                tree = ast.parse(self.source)
            finally:
                sys.stderr = old_stderr
        except Exception as e:
            # parse error, skip
            self.last_error = ('parse', f'{type(e).__name__}: {e}')
            return []

        # store AST tree for dead code analysis
        self._ast_tree = tree

        # create global scope
        self.global_scope = Scope(
            name='<global>',
            start_line=1,
            end_line=len(self.source_lines),
            scope_type='global',
        )
        self.current_scope = self.global_scope
        self.scopes.append(self.global_scope)

        # walk AST
        self._visit(tree)

        # analyze collected data
        self._analyze_patterns()

        return self.findings

    def _get_line(self, node: Node) -> int:
        """Extract line number from node."""
        ft = getattr(node, 'first_token', None)
        if ft:
            s = str(ft)
            if ',' in s:
                parts = s.rsplit(',', 1)
                if len(parts) == 2:
                    line_col = parts[1].rstrip(']')
                    if ':' in line_col:
                        try:
                            return int(line_col.split(':')[0])
                        except ValueError:
                            pass
        return 0

    def _get_node_source(self, node: Node) -> str:
        """Get source text for a node (approximate)."""
        line = self._get_line(node)
        if 0 < line <= len(self.source_lines):
            return self.source_lines[line - 1].strip()
        return ""

    def _node_to_string(self, node: Node) -> str:
        """Convert an AST node to its string representation."""
        if isinstance(node, Name):
            return node.id
        elif isinstance(node, Number):
            return str(node.n)
        elif isinstance(node, String):
            s = node.s
            if isinstance(s, bytes):
                s = s.decode('utf-8', errors='replace')
            # Escape special characters for Lua string literal
            # Must re-escape because luaparser stores decoded values
            escaped = s.replace('\\', '\\\\')  # backslash first!
            escaped = escaped.replace('\a', '\\a')  # bell
            escaped = escaped.replace('\b', '\\b')  # backspace
            escaped = escaped.replace('\f', '\\f')  # form feed
            escaped = escaped.replace('\n', '\\n')  # newline
            escaped = escaped.replace('\r', '\\r')  # carriage return
            escaped = escaped.replace('\t', '\\t')  # tab
            escaped = escaped.replace('\v', '\\v')  # vertical tab
            escaped = escaped.replace('\0', '\\0')  # null
            # Choose quote style and escape the chosen quote
            if '"' in escaped and "'" not in escaped:
                escaped = escaped.replace("'", "\\'")
                return f"'{escaped}'"
            else:
                escaped = escaped.replace('"', '\\"')
                return f'"{escaped}"'
        elif isinstance(node, (TrueExpr,)):
            return "true"
        elif isinstance(node, (FalseExpr,)):
            return "false"
        elif isinstance(node, (Nil,)):
            return "nil"
        elif isinstance(node, Index):
            value = self._node_to_string(node.value)
            idx = self._node_to_string(node.idx)
            # determine bracket vs dot notation:
            # - dot notation (t.field): idx.first_token is None
            # - bracket notation (t[key]): idx.first_token has a value
            idx_token = getattr(node.idx, 'first_token', None)
            if idx_token is not None and str(idx_token) != 'None':
                # bracket notation: t[key]
                return f"{value}[{idx}]"
            else:
                # dot notation: t.field
                return f"{value}.{idx}"
        elif isinstance(node, Call):
            func = self._node_to_string(node.func)
            args = ", ".join(self._node_to_string(a) for a in node.args)
            return f"{func}({args})"
        elif isinstance(node, Invoke):
            source = self._node_to_string(node.source)
            func = self._node_to_string(node.func)
            args = ", ".join(self._node_to_string(a) for a in node.args)
            return f"{source}:{func}({args})"
        elif isinstance(node, ULengthOP):
            return f"#{self._node_to_string(node.operand)}"
        elif isinstance(node, UMinusOp):
            return f"-{self._node_to_string(node.operand)}"
        elif isinstance(node, ULNotOp):
            return f"not {self._node_to_string(node.operand)}"
        elif isinstance(node, UBNotOp):
            return f"~{self._node_to_string(node.operand)}"
        elif isinstance(node, Concat):
            left = self._node_to_string(node.left)
            right = self._node_to_string(node.right)
            return f"{left} .. {right}"
        elif isinstance(node, OrLoOp):
            left = self._node_to_string(node.left)
            right = self._node_to_string(node.right)
            return f"({left} or {right})"
        elif isinstance(node, AndLoOp):
            left = self._node_to_string(node.left)
            right = self._node_to_string(node.right)
            return f"({left} and {right})"
        elif isinstance(node, AddOp):
            left = self._node_to_string(node.left)
            right = self._node_to_string(node.right)
            return f"{left} + {right}"
        elif isinstance(node, SubOp):
            left = self._node_to_string(node.left)
            right = self._node_to_string(node.right)
            return f"{left} - {right}"
        elif isinstance(node, MultOp):
            left = self._node_to_string(node.left)
            right = self._node_to_string(node.right)
            return f"{left} * {right}"
        elif isinstance(node, FloatDivOp):
            left = self._node_to_string(node.left)
            right = self._node_to_string(node.right)
            return f"{left} / {right}"
        elif isinstance(node, ModOp):
            left = self._node_to_string(node.left)
            right = self._node_to_string(node.right)
            return f"{left} % {right}"
        elif isinstance(node, ExpoOp):
            left = self._node_to_string(node.left)
            right = self._node_to_string(node.right)
            return f"{left} ^ {right}"
        elif isinstance(node, EqToOp):
            left = self._node_to_string(node.left)
            right = self._node_to_string(node.right)
            return f"{left} == {right}"
        elif isinstance(node, NotEqToOp):
            left = self._node_to_string(node.left)
            right = self._node_to_string(node.right)
            return f"{left} ~= {right}"
        elif isinstance(node, LessThanOp):
            left = self._node_to_string(node.left)
            right = self._node_to_string(node.right)
            return f"{left} < {right}"
        elif isinstance(node, GreaterThanOp):
            left = self._node_to_string(node.left)
            right = self._node_to_string(node.right)
            return f"{left} > {right}"
        elif isinstance(node, LessOrEqThanOp):
            left = self._node_to_string(node.left)
            right = self._node_to_string(node.right)
            return f"{left} <= {right}"
        elif isinstance(node, GreaterOrEqThanOp):
            left = self._node_to_string(node.left)
            right = self._node_to_string(node.right)
            return f"{left} >= {right}"
        elif isinstance(node, Table):
            return "{...}"
        else:
            return f"<{type(node).__name__}>"

    def _get_call_name(self, node: Call) -> Tuple[Optional[str], str, str]:
        """Get module, function, and full name from a Call node."""
        func = node.func

        if isinstance(func, Name):
            # bare function: pairs(), time_global()
            return None, func.id, func.id
        elif isinstance(func, Index):
            # module.func: table.insert(), db.actor
            if isinstance(func.value, Name) and isinstance(func.idx, Name):
                module = func.value.id
                fn = func.idx.id
                return module, fn, f"{module}.{fn}"

        return None, "", ""

    def _enter_scope(self, name: str, line: int, scope_type: str = 'block', is_hot: bool = False, node: Optional[Any] = None):
        """Enter a new scope."""
        new_scope = Scope(
            name=name,
            start_line=line,
            parent=self.current_scope,
            scope_type=scope_type,
            is_hot_callback=is_hot or (self.current_scope and self.current_scope.is_hot_callback),
            node=node,
        )

        # inherit cached globals from parent
        if self.current_scope:
            new_scope.cached_globals = set(self.current_scope.cached_globals)
            # inherit function aliases from parent scope
            new_scope.func_aliases = dict(self.current_scope.func_aliases)

        self.scopes.append(new_scope)
        self.current_scope = new_scope
        return new_scope

    def _exit_scope(self, end_line: int):
        """Exit current scope."""
        if self.current_scope:
            self.current_scope.end_line = end_line
            self.current_scope = self.current_scope.parent

    def _is_cached(self, name: str) -> bool:
        """Check if a global is cached in current scope chain."""
        scope = self.current_scope
        while scope:
            if name in scope.cached_globals or name in scope.locals:
                return True
            scope = scope.parent
        return False

    def _resolve_alias(self, name: str) -> Optional[str]:
        """
        Resolve a function alias to its canonical name.
        
        If 'name' is an alias for a stdlib function (e.g., 'tinsert' -> 'table.insert'),
        returns the canonical name. Otherwise returns None.
        """
        scope = self.current_scope
        while scope:
            if name in scope.func_aliases:
                return scope.func_aliases[name]
            scope = scope.parent
        return None

    def _visit(self, node: Node):
        """Visit a node and dispatch to specific handler."""
        if node is None:
            return

        handler = getattr(self, f'_visit_{type(node).__name__}', None)
        if handler:
            handler(node)
        else:
            self._visit_children(node)

    def _visit_children(self, node: Node):
        """Visit all children of a node."""
        try:
            node_dict = vars(node) if hasattr(node, '__dict__') else {}
        except TypeError:
            node_dict = {}

        for key, value in node_dict.items():
            if key.startswith('_'):
                continue
            if isinstance(value, Node):
                self._visit(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, Node):
                        self._visit(item)

    def _visit_Chunk(self, node: Chunk):
        self._visit(node.body)

    def _visit_Block(self, node: Block):
        for stmt in node.body:
            self._visit(stmt)

    def _visit_Function(self, node: Function):
        """Handle global function definition."""
        line = self._get_line(node)
        func_name = self._node_to_string(node.name) if node.name else '<anon>'

        is_hot = func_name in HOT_CALLBACKS
        is_per_frame = _is_per_frame_callback_name(func_name)

        self.function_depth += 1
        self._enter_scope(func_name, line, 'function', is_hot, node=node)

        # Track per-frame callback for performance analysis
        pf_info = None
        if is_per_frame:
            pf_info = PerFrameCallbackInfo(
                name=func_name,
                start_line=line,
                end_line=-1,  # Will be set on scope exit
                scope=self.current_scope,
            )
            self.per_frame_callbacks.append(pf_info)

        # register parameters as locals
        if hasattr(node, 'args') and node.args:
            for arg in node.args:
                if isinstance(arg, Name):
                    self.current_scope.locals.add(arg.id)

        self._visit(node.body)

        end_line = self._get_end_line(node)
        
        # Update end_line for per-frame callback
        if pf_info is not None:
            pf_info.end_line = end_line

        self._exit_scope(end_line)
        self.function_depth -= 1

    def _visit_LocalFunction(self, node: LocalFunction):
        """Handle local function definition."""
        line = self._get_line(node)
        func_name = node.name.id if isinstance(node.name, Name) else '<anon>'

        # register function name in parent scope (before entering function scope)
        if self.current_scope:
            self.current_scope.locals.add(func_name)
            
            # Mark function name as assignment target
            if isinstance(node.name, Name):
                self.assignment_target_ids.add(id(node.name))
                
                # Track for unused function detection (skip _ prefixed)
                if not func_name.startswith('_'):
                    key = (id(self.current_scope), func_name)
                    self.local_funcs[key] = LocalVarInfo(
                        name=func_name,
                        assign_line=line,
                        scope=self.current_scope,
                        is_read=False,
                        is_function=True,
                    )

        is_hot = func_name in HOT_CALLBACKS

        self.function_depth += 1
        self._enter_scope(func_name, line, 'function', is_hot, node=node)

        if hasattr(node, 'args') and node.args:
            for arg in node.args:
                if isinstance(arg, Name):
                    self.current_scope.locals.add(arg.id)

        self._visit(node.body)

        end_line = self._get_end_line(node)
        self._exit_scope(end_line)
        self.function_depth -= 1

    def _visit_AnonymousFunction(self, node: AnonymousFunction):
        """Handle anonymous function expression (e.g. `tbl.x = function(...) ... end`).

        Without this, scope tracking never enters such bodies, so references
        inside them (db.actor, etc.) are attributed to the enclosing module
        scope. _edit_repeated_calls would then hoist a cache to file top,
        where db.actor is nil at script-load time.
        """
        line = self._get_line(node)

        # I-013: BC_FNEW is NYI, so building a closure aborts the enclosing
        # trace. Not only in a loop: a per-frame body is itself the hot thing
        # LuaJIT tries to trace (the engine calls it from C, so there is no
        # Lua loop around it), and a closure anywhere in the body kills that
        # trace. Confirmed on gunslinger_controller.script:541 by
        # lab/tools/nyi_crosscheck.py, which saw abort 5:49 (BC_FNEW) on a
        # body whose closure is not inside any loop.
        if self.function_depth > 0:
            self._record_nyi('bytecode', 'function()',
                             LUAJIT20_NYI_BYTECODE['closure_in_loop'], line)

        was_in_concat = self._in_concat
        self._in_concat = False
        self.function_depth += 1
        self._enter_scope('<anon>', line, 'function', is_hot=False, node=node)

        if hasattr(node, 'args') and node.args:
            for arg in node.args:
                if isinstance(arg, Name):
                    self.current_scope.locals.add(arg.id)

        self._visit(node.body)

        end_line = self._get_end_line(node)
        self._exit_scope(end_line)
        self.function_depth -= 1
        self._in_concat = was_in_concat

    def _visit_Method(self, node: Method):
        """Handle method definition."""
        line = self._get_line(node)

        # get method name
        if isinstance(node.name, Index):
            func_name = self._node_to_string(node.name)
        else:
            func_name = self._node_to_string(node.name) if node.name else '<method>'

        is_hot = func_name in HOT_CALLBACKS

        # I-010: every binder and UI class does its per-frame work in an
        # `:update` / `:Update` method, and those were previously invisible -
        # PER_FRAME_CALLBACKS listed four engine callback names and caught
        # roughly a quarter of the real per-frame bodies in the corpus.
        method_name = func_name.rsplit('.', 1)[-1]
        is_per_frame = (method_name in PER_FRAME_METHOD_NAMES
                        or _is_per_frame_callback_name(method_name))
        if is_per_frame:
            owner = self._node_to_string(node.source) if getattr(node, 'source', None) else ''
            display_name = f'{owner}:{method_name}' if owner else method_name
        else:
            display_name = func_name

        self.function_depth += 1
        self._enter_scope(func_name, line, 'function', is_hot, node=node)

        pf_info = None
        if is_per_frame:
            pf_info = PerFrameCallbackInfo(
                name=display_name,
                start_line=line,
                end_line=-1,
                scope=self.current_scope,
            )
            self.per_frame_callbacks.append(pf_info)

        # 'self' is implicit first param
        self.current_scope.locals.add('self')

        if hasattr(node, 'args') and node.args:
            for arg in node.args:
                if isinstance(arg, Name):
                    self.current_scope.locals.add(arg.id)

        self._visit(node.body)

        end_line = self._get_end_line(node)
        if pf_info is not None:
            pf_info.end_line = end_line
        self._exit_scope(end_line)
        self.function_depth -= 1

    def _get_end_line(self, node: Node) -> int:
        """Try to get end line of a node."""
        lt = getattr(node, 'last_token', None)
        if lt:
            s = str(lt)
            if ',' in s:
                parts = s.rsplit(',', 1)
                if len(parts) == 2:
                    line_col = parts[1].rstrip(']')
                    if ':' in line_col:
                        try:
                            return int(line_col.split(':')[0])
                        except ValueError:
                            pass
        return self._get_line(node)

    def _iter_children(self, node):
        """Iterate over all child nodes of an AST node."""
        if node is None:
            return
        
        # Handle lists
        if isinstance(node, list):
            for item in node:
                yield item
            return
        
        # For AST nodes, iterate over known child attributes
        child_attrs = [
            'body', 'test', 'orelse', 'targets', 'values', 'iter',
            'func', 'args', 'value', 'idx', 'key', 'left', 'right',
            'operand', 'fields', 'keys', 'source', 'step', 'start', 'stop',
        ]
        
        for attr in child_attrs:
            child = getattr(node, attr, None)
            if child is not None:
                if isinstance(child, list):
                    for item in child:
                        yield item
                else:
                    yield child

    def _visit_Forin(self, node: Forin):
        """Handle for-in loop."""
        line = self._get_line(node)

        # visit iterator expression first (outside loop scope)
        for iter_expr in node.iter:
            self._visit(iter_expr)

        self.loop_depth += 1
        self._enter_scope('<forin>', line, 'loop')

        # loop variables are local to loop
        for target in node.targets:
            if isinstance(target, Name):
                var_name = target.id
                self.current_scope.locals.add(var_name)
                
                # Mark as assignment target (not a read)
                self.assignment_target_ids.add(id(target))
                
                # Track for unused variable detection (skip _ prefixed)
                if not var_name.startswith('_'):
                    key = (id(self.current_scope), var_name)
                    self.local_vars[key] = LocalVarInfo(
                        name=var_name,
                        assign_line=line,
                        scope=self.current_scope,
                        is_read=False,
                        is_function=False,
                        is_loop_var=True,
                    )

        self._visit(node.body)

        end_line = self._get_end_line(node)
        self._exit_scope(end_line)
        self.loop_depth -= 1

    def _visit_Fornum(self, node: Fornum):
        """Handle numeric for loop."""
        line = self._get_line(node)

        # visit range expressions first
        self._visit(node.start)
        self._visit(node.stop)
        if node.step:
            self._visit(node.step)

        self.loop_depth += 1
        self._enter_scope('<fornum>', line, 'loop')

        if isinstance(node.target, Name):
            var_name = node.target.id
            self.current_scope.locals.add(var_name)
            
            # Mark as assignment target (not a read)
            self.assignment_target_ids.add(id(node.target))
            
            # Track for unused variable detection (skip _ prefixed)
            if not var_name.startswith('_'):
                key = (id(self.current_scope), var_name)
                self.local_vars[key] = LocalVarInfo(
                    name=var_name,
                    assign_line=line,
                    scope=self.current_scope,
                    is_read=False,
                    is_function=False,
                    is_loop_var=True,
                )

        self._visit(node.body)

        end_line = self._get_end_line(node)
        self._exit_scope(end_line)
        self.loop_depth -= 1

    def _visit_While(self, node: While):
        """Handle while loop."""
        line = self._get_line(node)

        self._visit(node.test)

        self.loop_depth += 1
        self._enter_scope('<while>', line, 'loop')
        self._visit(node.body)
        end_line = self._get_end_line(node)
        self._exit_scope(end_line)
        self.loop_depth -= 1

    def _visit_Repeat(self, node: Repeat):
        """Handle repeat-until loop."""
        line = self._get_line(node)

        self.loop_depth += 1
        self._enter_scope('<repeat>', line, 'loop')
        self._visit(node.body)
        self._visit(node.test)
        end_line = self._get_end_line(node)
        self._exit_scope(end_line)
        self.loop_depth -= 1

    def _visit_If(self, node: If):
        """Handle if statement.

        In Lua every if/elseif/else body is its own scope, so we open a fresh
        block scope around each. Without this, a `local x = ...` inside an
        if-body bleeds into the enclosing function's locals tracking and can
        clobber nil-source tracking when the same name is reassigned in
        another branch - see C2 in the audit notes.
        """
        if_id = id(node)

        # test runs in the parent scope (it can read outer locals but
        # `local`s declared inside it would still belong to the parent).
        # The test is also OUTSIDE this if-chain's branches in the
        # if_chain_stack sense - so we don't push for the test.
        self._visit(node.test)

        # main body - branch 0, fresh block scope, push onto chain stack
        body_line = self._get_line(node.body) if node.body is not None else self._get_line(node)
        body_end = self._get_end_line(node.body) if node.body is not None else self._get_end_line(node)
        self.if_chain_stack.append((if_id, 0))
        self._enter_scope('<if-body>', body_line, 'block')
        self._visit(node.body)
        self._exit_scope(body_end)
        self.if_chain_stack.pop()

        # elseif/else chain - keeps the same if_id, increments branch index
        if node.orelse:
            self._visit_orelse(node.orelse, if_id, 1)

    def _visit_orelse(self, node, if_id, branch_idx):
        """Helper to visit elseif/else with branch + scope tracking."""
        if isinstance(node, ElseIf):
            # elseif: test and body share a scope (elseif test only runs after
            # all prior branches have failed, so it's effectively in the same
            # control-flow position as the body that follows it).
            line = self._get_line(node)
            end = self._get_end_line(node)
            self.if_chain_stack.append((if_id, branch_idx))
            self._enter_scope('<elseif-body>', line, 'block')
            self._visit(node.test)
            self._visit(node.body)
            self._exit_scope(end)
            self.if_chain_stack.pop()

            if node.orelse:
                self._visit_orelse(node.orelse, if_id, branch_idx + 1)
        elif isinstance(node, Block):
            # else block - branch index -1
            line = self._get_line(node)
            end = self._get_end_line(node)
            self.if_chain_stack.append((if_id, -1))
            self._enter_scope('<else-body>', line, 'block')
            self._visit(node)
            self._exit_scope(end)
            self.if_chain_stack.pop()
        else:
            # defensive fallback
            line = self._get_line(node)
            end = self._get_end_line(node)
            self.if_chain_stack.append((if_id, -1))
            self._enter_scope('<else-body>', line, 'block')
            self._visit(node)
            self._exit_scope(end)
            self.if_chain_stack.pop()

    def _visit_ElseIf(self, node: ElseIf):
        """Handle elseif clause - this is called from _visit_orelse."""
        # already handled by _visit_orelse
        pass

    def _visit_Do(self, node):
        """Handle `do ... end` block - its body is a fresh local scope."""
        line = self._get_line(node)
        end_line = self._get_end_line(node)
        self._enter_scope('<do>', line, 'block')
        body = getattr(node, 'body', None)
        if body is not None:
            self._visit(body)
        self._exit_scope(end_line)

    def _visit_LocalAssign(self, node: LocalAssign):
        """Handle local assignment."""
        line = self._get_line(node)

        # register targets as locals and track for unused variable detection
        for target in node.targets:
            if isinstance(target, Name):
                var_name = target.id
                self.current_scope.locals.add(var_name)
                
                # Mark this Name node as an assignment target (not a read)
                self.assignment_target_ids.add(id(target))
                
                # Track for unused variable detection (skip _ prefixed)
                if not var_name.startswith('_'):
                    key = (id(self.current_scope), var_name)
                    self.local_vars[key] = LocalVarInfo(
                        name=var_name,
                        assign_line=line,
                        scope=self.current_scope,
                        is_read=False,
                        is_function=False,
                    )

        # check for caching pattern: local xyz = module.func
        is_new_alias = False
        if len(node.targets) == 1 and len(node.values) == 1:
            target = node.targets[0]
            value = node.values[0]

            if isinstance(target, Name):
                target_name = target.id

                # check if caching a module.func
                if isinstance(value, Index):
                    if isinstance(value.value, Name) and isinstance(value.idx, Name):
                        module = value.value.id
                        func = value.idx.id
                        full_name = f"{module}.{func}"

                        if module in CACHEABLE_MODULE_FUNCS:
                            self.current_scope.cached_globals.add(full_name)
                            # Record alias mapping: target_name -> canonical function name
                            self.current_scope.func_aliases[target_name] = full_name
                            is_new_alias = True

                # check if caching a bare global
                elif isinstance(value, Name):
                    if value.id in CACHEABLE_BARE_GLOBALS:
                        self.current_scope.cached_globals.add(value.id)
                        # Record alias mapping for bare globals too
                        self.current_scope.func_aliases[target_name] = value.id
                        is_new_alias = True
                
                # If NOT creating a new alias, invalidate any existing alias with this name
                # (e.g., local f = table.insert; local f = other_func)
                if not is_new_alias:
                    if target_name in self.current_scope.func_aliases:
                        del self.current_scope.func_aliases[target_name]

                # record assignment info
                self._record_assignment(target_name, value, line, is_local=True)

        # visit values
        for value in node.values:
            self._visit(value)

    def _visit_Assign(self, node: Assign):
        """Handle assignment."""
        line = self._get_line(node)

        # Mark Name targets as assignment targets (not reads)
        for target in node.targets:
            if isinstance(target, Name):
                self.assignment_target_ids.add(id(target))
                
                # Invalidate alias if this variable was an alias
                # (reassigning breaks the alias relationship)
                target_name = target.id
                self._invalidate_alias(target_name)

        # check for global writes
        for target in node.targets:
            if isinstance(target, Name):
                target_name = target.id
                # it's a global write if not in any scope's locals
                if not self._is_in_locals(target_name):
                    self.global_writes.append((target_name, line))

                if len(node.values) == 1:
                    self._record_assignment(target_name, node.values[0], line, is_local=False)

        # visit targets (for calls inside index expressions like db.storage[npc:id()])
        for target in node.targets:
            self._visit(target)

        # visit values
        for value in node.values:
            self._visit(value)
    
    def _invalidate_alias(self, var_name: str):
        """Remove alias mapping when a variable is reassigned."""
        scope = self.current_scope
        while scope:
            if var_name in scope.func_aliases:
                del scope.func_aliases[var_name]
                return  # Only remove from innermost scope where it exists
            scope = scope.parent

    def _is_in_locals(self, name: str) -> bool:
        """Check if name is in any scope's locals."""
        scope = self.current_scope
        while scope:
            if name in scope.locals:
                return True
            scope = scope.parent
        return False

    def _record_assignment(self, target: str, value: Node, line: int, is_local: bool):
        """Record an assignment for analysis."""
        if isinstance(value, Call):
            value_type = 'call'
            value_repr = self._node_to_string(value)
        elif isinstance(value, Index):
            value_type = 'index'
            value_repr = self._node_to_string(value)
        elif isinstance(value, Concat):
            value_type = 'concat'
            value_repr = self._node_to_string(value)

            # record concat info
            left_var = None
            if isinstance(value.left, Name):
                left_var = value.left.id
            
            # get the right side expression
            right_expr = self._node_to_string(value.right)
            
            # find innermost loop scope
            loop_scope = None
            if self.loop_depth > 0:
                # walk up scopes to find loop
                s = self.current_scope
                while s:
                    if s.scope_type == 'loop':
                        loop_scope = s
                        break
                    s = s.parent

            self.concats.append(ConcatInfo(
                target=target,
                left_var=left_var,
                line=line,
                scope=self.current_scope,
                in_loop=self.loop_depth > 0,
                loop_depth=self.loop_depth,
                loop_scope=loop_scope,
                right_expr=right_expr,
            ))
        elif isinstance(value, (Number, String, TrueExpr, FalseExpr, Nil)):
            value_type = 'literal'
            value_repr = self._node_to_string(value)
        else:
            value_type = 'other'
            value_repr = self._node_to_string(value)

        self.assigns.append(AssignInfo(
            target=target,
            value_type=value_type,
            value_repr=value_repr,
            line=line,
            node=value,
            scope=self.current_scope,
            is_local=is_local,
            in_loop=self.loop_depth > 0,
        ))
        
        # Track nil-returning function assignments
        self._track_nil_source(target, value, value_repr, line, is_local)

    def _find_defining_scope(self, var_name: str) -> Optional[Scope]:
        """Find the scope that owns this variable (walks up the scope chain).

        Returns None if the variable is not declared anywhere in the current
        chain (i.e. it's a global). This matters for nil-tracking: when a
        function parameter or outer local is reassigned inside a nested block,
        the nil-source must be keyed under the *defining* scope, otherwise it
        evaporates when the inner block exits and later access sites don't
        find it.
        """
        scope = self.current_scope
        while scope:
            if var_name in scope.locals:
                return scope
            scope = scope.parent
        return None

    def _track_nil_source(self, target: str, value: Node, value_repr: str, line: int, is_local: bool):
        """Track if a variable is assigned from a nil-returning function."""
        source_func = None

        # Check if it's a call to a nil-returning function
        if isinstance(value, Call):
            # get the function name
            _, _, full_name = self._get_call_name(value)
            if full_name and full_name in NIL_RETURNING_FUNCTIONS:
                source_func = full_name

        # Check for method call (Invoke) - e.g., obj:parent()
        elif isinstance(value, Invoke):
            method_name = value.func.id if isinstance(value.func, Name) else ''
            method_pattern = f':{method_name}'
            if method_pattern in NIL_RETURNING_FUNCTIONS:
                source_func = method_pattern

        # Check for index access - e.g., db.actor, alife():object(id)
        elif isinstance(value, Index):
            full_name = self._node_to_string(value)
            # check direct matches like db.actor
            if full_name in NIL_RETURNING_FUNCTIONS:
                source_func = full_name

        # Determine the scope under which to register/clear the nil source.
        #  - LocalAssign (`local x = ...`) introduces a brand-new local in the
        #    current scope, so we use current_scope.
        #  - Plain Assign (`x = ...`) targets an already-declared variable:
        #    walk up to find which scope owns it. If it's a global (no scope),
        #    we skip nil-tracking entirely - the analyzer doesn't reason about
        #    cross-call global state.
        if is_local:
            owning_scope = self.current_scope
        else:
            owning_scope = self._find_defining_scope(target)
            if owning_scope is None:
                return  # global write - out of scope for this analysis

        key = (id(owning_scope), target)

        if source_func:
            self.nil_sources[key] = NilSourceInfo(
                var_name=target,
                source_call=value_repr,
                source_func=source_func,
                assign_line=line,
                scope=owning_scope,
                is_local=is_local,
                is_guarded=False,
            )
        else:
            # variable reassigned from a non-nil source - drop the tracking entry
            if key in self.nil_sources:
                del self.nil_sources[key]

    def _check_nil_access(self, source_node: Node, source_str: str, full_call: str, line: int, access_type: str):
        """Check if we're accessing a potentially nil variable."""
        # only check simple variable names for now
        if not isinstance(source_node, Name):
            return
        
        var_name = source_node.id
        
        # check if this variable is from a nil-returning function in an enclosing scope
        nil_source = self._find_nil_source(var_name)
        if not nil_source:
            return
        
        # check if there's a nil guard before this access
        if self._has_nil_guard(var_name, nil_source.assign_line, line):
            nil_source.is_guarded = True
            return
        
        # determine if this is safe to auto-fix
        # Safe if: assignment is on previous line, this is the only usage before any branch
        is_safe = self._is_safe_nil_fix(nil_source, line)
        
        self.nil_accesses.append(NilAccessInfo(
            var_name=var_name,
            access_type=access_type,
            access_call=full_call,
            access_line=line,
            nil_source=nil_source,
            is_safe_to_fix=is_safe,
        ))

    def _find_nil_source(self, var_name: str) -> Optional[NilSourceInfo]:
        """Find nil source for a variable in current or enclosing scopes."""
        # key is (scope_id, var_name)
        scope = self.current_scope
        while scope:
            key = (id(scope), var_name)
            if key in self.nil_sources:
                return self.nil_sources[key]
            scope = scope.parent
        return None

    @staticmethod
    def _strip_line_comments_and_strings(text: str) -> str:
        """Strip comment and string content from a line so regex only matches real code."""
        result = []
        i = 0
        while i < len(text):
            c = text[i]
            # line comment
            if c == '-' and i + 1 < len(text) and text[i + 1] == '-':
                break
            # string literal
            elif c in ('"', "'"):
                quote = c
                result.append(c)
                i += 1
                while i < len(text):
                    sc = text[i]
                    if sc == '\\':
                        result.append(' ')
                        i += 1
                        if i < len(text):
                            result.append(' ')
                            i += 1
                        continue
                    if sc == quote:
                        result.append(sc)
                        i += 1
                        break
                    result.append(' ')
                    i += 1
                continue
            # long string [[...]]
            elif c == '[' and i + 1 < len(text) and text[i + 1] in ('[', '='):
                eq_count = 0
                j = i + 1
                while j < len(text) and text[j] == '=':
                    eq_count += 1
                    j += 1
                if j < len(text) and text[j] == '[':
                    close = ']' + '=' * eq_count + ']'
                    end = text.find(close, j + 1)
                    if end != -1:
                        result.append(' ' * (end + len(close) - i))
                        i = end + len(close)
                        continue
                result.append(c)
                i += 1
                continue
            else:
                result.append(c)
                i += 1
        return ''.join(result)

    def _has_nil_guard(self, var_name: str, assign_line: int, access_line: int) -> bool:
        """Check if there's a nil guard between assignment and access."""
        if assign_line >= access_line:
            return False
        
        var_escaped = re.escape(var_name)
        
        guard_patterns = [
            rf'\bif\s+{var_escaped}\s+then\b',
            rf'\bif\s+{var_escaped}\s+and\b',
            rf'\bif\s+not\s+{var_escaped}\s+then\b',
            rf'\bif\s+{var_escaped}\s*~=\s*nil\b',
            rf'\bif\s+{var_escaped}\s*==\s*nil\s+then\s+return\b',
            rf'\b{var_escaped}\s+and\s+{var_escaped}[:\.]',
            rf'\bif\s*\(\s*{var_escaped}\s*\)\s*then\b',
        ]
        
        combined_pattern = re.compile('|'.join(guard_patterns), re.IGNORECASE)
        
        for line_num in range(assign_line, access_line):
            if line_num <= 0 or line_num > len(self.source_lines):
                continue
            line_text = self.source_lines[line_num - 1]
            
            # only match against actual code, not comments or string contents
            cleaned = self._strip_line_comments_and_strings(line_text)
            if combined_pattern.search(cleaned):
                return True
        
        return False

    def _is_safe_nil_fix(self, nil_source: NilSourceInfo, access_line: int) -> bool:
        """
        Determine if a nil access is safe to auto-fix.
        
        Safe conditions:
        1. Access is on the line immediately after assignment
        2. It's a local variable (not global)
        3. Assignment and access are in the same scope
        4. No complex control flow between them
        5. Access line is NOT a local declaration (would break scope if wrapped)
        6. Access line is NOT a control flow statement (if/for/while - too complex)
        """
        # must be immediately after (next line)
        if access_line != nil_source.assign_line + 1:
            return False
        
        # must be local
        if not nil_source.is_local:
            return False
        
        # must be in same scope
        if self.current_scope != nil_source.scope:
            return False
        
        # check that the line between is not a control flow statement
        if nil_source.assign_line <= 0 or nil_source.assign_line > len(self.source_lines):
            return False
        
        # check access line content
        if access_line > 0 and access_line <= len(self.source_lines):
            access_text = self.source_lines[access_line - 1].strip()
            
            # CRITICAL: access line must NOT be a local declaration
            if access_text.startswith('local '):
                return False
            
            # CRITICAL: access line must NOT be control flow (too complex to wrap)
            control_keywords = ('if ', 'if(', 'for ', 'while ', 'repeat', 'function ', 'function(')
            if any(access_text.startswith(kw) for kw in control_keywords):
                return False
            
        return True

    def _visit_Call(self, node: Call):
        """Handle function call."""
        line = self._get_line(node)
        module, func, full_name = self._get_call_name(node)

        # Check if this is an aliased stdlib call
        # e.g., if 'local tinsert = table.insert' was declared,
        # then 'tinsert(t, v)' should be recognized as 'table.insert(t, v)'
        alias_name = None
        if full_name and module is None:
            # This is a bare function call - check if it's an alias
            canonical = self._resolve_alias(full_name)
            if canonical:
                alias_name = full_name
                full_name = canonical
                # Also update module/func if it's a module.func pattern
                if '.' in canonical:
                    module, func = canonical.split('.', 1)

        if full_name:
            self.calls.append(CallInfo(
                alias_name=alias_name,
                full_name=full_name,
                module=module,
                func=func,
                args=node.args,
                line=line,
                node=node,
                scope=self.current_scope,
                in_loop=self.loop_depth > 0,
                loop_depth=self.loop_depth,
                if_chain_path=tuple(self.if_chain_stack),
            ))

            # I-013: does this call abort a LuaJIT 2.0 trace?
            self._record_nyi_call(full_name, module, len(node.args), line)

            # Track RegisterScriptCallback for unused variable/function detection
            if full_name == 'RegisterScriptCallback' and len(node.args) >= 2:
                callback_func = self._node_to_string(node.args[1])
                if callback_func:
                    self.callback_registrations.add(callback_func)
            
            # Track vector() allocations in loops
            if full_name == 'vector' and self.loop_depth > 0:
                # check if we're inside a per-frame callback
                in_per_frame = False
                scope = self.current_scope
                while scope:
                    if scope.is_hot_callback:
                        in_per_frame = True
                        break
                    scope = scope.parent
                
                self.vector_allocations.append(VectorAllocationInfo(
                    line=line,
                    call_node=node,
                    loop_depth=self.loop_depth,
                    scope=self.current_scope,
                    in_per_frame_callback=in_per_frame,
                ))

        # the Index that names the function being called is *not* a standalone
        # property read - record it under the Call we just emitted, not again
        # via _visit_Index. This affects only the outermost Index attached to
        # node.func; nested Indexes (the `a.b` in `a.b.c()`) are still real
        # property reads and must be recorded normally.
        if isinstance(node.func, Index):
            self._suppress_indexes.add(id(node.func))

        # visit children
        self._visit(node.func)
        for arg in node.args:
            self._visit(arg)

    def _visit_Invoke(self, node: Invoke):
        """Handle method call (obj:method())."""
        line = self._get_line(node)

        # record as call
        source = self._node_to_string(node.source)
        func = node.func.id if isinstance(node.func, Name) else self._node_to_string(node.func)
        full_name = f"{source}:{func}"

        self.calls.append(CallInfo(
            full_name=full_name,
            module=source,
            func=func,
            args=node.args,
            line=line,
            node=node,
            scope=self.current_scope,
            in_loop=self.loop_depth > 0,
            loop_depth=self.loop_depth,
            if_chain_path=tuple(self.if_chain_stack),
        ))

        # I-013: `obj:method()` on engine userdata is a C call and aborts the
        # trace. We can only go on the method name, so ENGINE_NYI_METHODS is
        # deliberately a whitelist: an unrecognized method is assumed to be
        # plain Lua, which makes the classifier under-report "interpreted".
        if func in ENGINE_NYI_METHODS:
            self._record_nyi('cfunc', full_name,
                             'NYICF: :%s() (engine C function)' % func, line)
        elif source in ENGINE_NYI_NAMESPACES:
            self._record_nyi('cfunc', full_name,
                             'NYICF: %s (engine C function)' % full_name, line)

        # Check for potential nil access
        self._check_nil_access(node.source, source, full_name, line, 'method')

        # source of an invoke (e.g. `db.actor` in `db.actor:method()`) is the
        # receiver - the Invoke itself records the full `db.actor:method`
        # signature, so don't double-count `db.actor` as a standalone read.
        if isinstance(node.source, Index):
            self._suppress_indexes.add(id(node.source))

        self._visit(node.source)
        for arg in node.args:
            self._visit(arg)

    # --- I-013: recording constructs LuaJIT 2.0 cannot trace ---------------

    def _record_nyi(self, kind: str, name: str, reason: str, line: int):
        self.nyi_sites.append(NyiSiteInfo(
            kind=kind,
            name=name,
            reason=reason,
            line=line,
            scope=self.current_scope,
            in_loop=self.loop_depth > 0,
            if_chain_path=tuple(self.if_chain_stack),
        ))

    def _record_nyi_call(self, full_name: str, module: Optional[str],
                         argc: int, line: int):
        """Classify one call site against the measured NYI tables."""
        reason = LUAJIT20_NYI_FUNCS.get(full_name)
        if reason:
            self._record_nyi('fastfunc', full_name, reason, line)
            return
        variant = LUAJIT20_NYI_VARIANTS.get(full_name)
        if variant:
            aborts, vreason = variant
            if aborts(argc):
                self._record_nyi('variant', full_name, vreason, line)
            return
        # engine C functions: `level.foo(...)`, `alife()`, `time_global()`
        if module and module in ENGINE_NYI_NAMESPACES:
            self._record_nyi('cfunc', full_name,
                             'NYICF: %s (engine C function)' % full_name, line)
            return
        if module is None and full_name in ENGINE_NYI_GLOBALS:
            self._record_nyi('cfunc', full_name,
                             'NYICF: %s (engine C function)' % full_name, line)

    def _visit_Concat(self, node: Concat):
        """Handle concatenation operator."""
        line = self._get_line(node)

        # BC_CAT is NYI in LuaJIT 2.0 - measured, see lab/reports/luajit20-nyi.md.
        # Even `'a' .. n` with two operands aborts the trace, so every concat
        # anywhere in a body makes that body interpreted. `a .. b .. c` is a
        # nest of Concat nodes but one BC_CAT, so only the outermost counts.
        if not self._in_concat:
            self._record_nyi('bytecode', '..',
                             LUAJIT20_NYI_BYTECODE['concat'], line)

        left_var = None
        if isinstance(node.left, Name):
            left_var = node.left.id

        # only interesting if we're in a loop
        if self.loop_depth > 0:
            self.concats.append(ConcatInfo(
                target=None,  # no assignment context here
                left_var=left_var,
                line=line,
                scope=self.current_scope,
                in_loop=True,
                loop_depth=self.loop_depth,
            ))

        was_in_concat = self._in_concat
        self._in_concat = True
        self._visit(node.left)
        self._visit(node.right)
        self._in_concat = was_in_concat

    # visitor pass-through for other nodes
    def _visit_Index(self, node: Index):
        self._visit(node.value)
        # Distinguish bracket vs. dot notation. For dot access (t.field), the
        # idx is a synthetic Name representing a field literal - visiting it
        # would falsely register the field name as a variable read and hide
        # genuinely-unused locals that happen to share the field name.
        # For bracket access (t[expr]), idx is a real expression that must be
        # visited (it can read locals: t[my_var], t[foo() + 1], etc.).
        idx_token = getattr(node.idx, 'first_token', None)
        is_bracket = idx_token is not None and str(idx_token) != 'None'
        if is_bracket:
            self._visit(node.idx)

        # Record property reads for repeated-access caching (e.g. `db.actor`).
        # Only single-level dot accesses where both sides are plain Names -
        # those are the only shape `_edit_repeated_calls` knows how to cache
        # as `local x = base.field`.
        if id(node) in self._suppress_indexes:
            return
        if is_bracket:
            return
        if not isinstance(node.value, Name) or not isinstance(node.idx, Name):
            return

        full_name = f"{node.value.id}.{node.idx.id}"
        if full_name not in EXPENSIVE_INDEXES:
            return

        self.indexes.append(IndexInfo(
            full_name=full_name,
            module=node.value.id,
            func=node.idx.id,
            line=self._get_line(node),
            node=node,
            scope=self.current_scope,
            in_loop=self.loop_depth > 0,
            loop_depth=self.loop_depth,
            if_chain_path=tuple(self.if_chain_stack),
        ))

    def _visit_Table(self, node: Table):
        for field in node.fields:
            self._visit(field)

    def _visit_Field(self, node: Field):
        if node.key:
            self._visit(node.key)
        self._visit(node.value)

    def _visit_Return(self, node: Return):
        for val in node.values:
            self._visit(val)

    # binary ops
    def _visit_AddOp(self, node): self._visit(node.left); self._visit(node.right)
    def _visit_SubOp(self, node): self._visit(node.left); self._visit(node.right)
    def _visit_MultOp(self, node): self._visit(node.left); self._visit(node.right)
    def _visit_FloatDivOp(self, node): self._visit(node.left); self._visit(node.right)
    def _visit_ModOp(self, node): self._visit(node.left); self._visit(node.right)
    def _visit_ExpoOp(self, node): self._visit(node.left); self._visit(node.right)
    def _visit_AndLoOp(self, node): self._visit(node.left); self._visit(node.right)
    def _visit_OrLoOp(self, node): self._visit(node.left); self._visit(node.right)
    def _visit_EqToOp(self, node): self._visit(node.left); self._visit(node.right)
    def _visit_NotEqToOp(self, node): self._visit(node.left); self._visit(node.right)
    
    def _visit_LessThanOp(self, node):
        self._check_distance_comparison(node, '<')
        self._visit(node.left)
        self._visit(node.right)
    
    def _visit_GreaterThanOp(self, node):
        self._check_distance_comparison(node, '>')
        self._visit(node.left)
        self._visit(node.right)
    
    def _visit_LessOrEqThanOp(self, node):
        self._check_distance_comparison(node, '<=')
        self._visit(node.left)
        self._visit(node.right)
    
    def _visit_GreaterOrEqThanOp(self, node):
        self._check_distance_comparison(node, '>=')
        self._visit(node.left)
        self._visit(node.right)
    
    def _check_distance_comparison(self, node, op: str):
        """Check if this comparison involves distance_to() that could use distance_to_sqr()."""
        # Pattern: obj:distance_to(target) < N  or  N > obj:distance_to(target)
        invoke_node = None
        threshold_node = None
        
        # check left side for distance_to invoke
        if isinstance(node.left, Invoke):
            func_name = node.left.func.id if isinstance(node.left.func, Name) else None
            if func_name == 'distance_to':
                invoke_node = node.left
                threshold_node = node.right
        
        # check right side for distance_to invoke (reversed comparison)
        if invoke_node is None and isinstance(node.right, Invoke):
            func_name = node.right.func.id if isinstance(node.right.func, Name) else None
            if func_name == 'distance_to':
                invoke_node = node.right
                threshold_node = node.left
                # Reverse the operator for analysis
                op = {'<': '>', '>': '<', '<=': '>=', '>=': '<='}[op]
        
        if invoke_node is None:
            return
        
        # check if threshold is a numeric literal
        if not isinstance(threshold_node, Number):
            return
        
        threshold_value = threshold_node.n
        
        # extract source and target
        source_obj = self._node_to_string(invoke_node.source)
        target_obj = self._node_to_string(invoke_node.args[0]) if invoke_node.args else ""
        
        self.distance_comparisons.append(DistanceComparisonInfo(
            line=self._get_line(node),
            source_obj=source_obj,
            target_obj=target_obj,
            comparison_op=op,
            threshold_value=threshold_value,
            threshold_node=threshold_node,
            full_node=node,
            invoke_node=invoke_node,
        ))

    # unary ops
    def _visit_UMinusOp(self, node): self._visit(node.operand)
    def _visit_UBNotOp(self, node): self._visit(node.operand)
    def _visit_ULNotOp(self, node): self._visit(node.operand)
    def _visit_ULengthOP(self, node): self._visit(node.operand)

    # terminal nodes - no children
    def _visit_Name(self, node):
        """Handle Name node - track variable reads for unused detection."""
        # Only count as read if NOT an assignment target
        if id(node) not in self.assignment_target_ids:
            var_name = node.id
            # Find which scope this variable belongs to (walk up scope chain)
            scope = self.current_scope
            while scope:
                key = (id(scope), var_name)
                if key in self.local_vars:
                    self.local_vars[key].is_read = True
                    self.local_vars[key].read_count += 1
                    break
                if key in self.local_funcs:
                    self.local_funcs[key].is_read = True
                    self.local_funcs[key].read_count += 1
                    break
                # Check if it's in this scope's locals (even if not tracked)
                if var_name in scope.locals:
                    break  # Found the scope, but might not be tracked (e.g., _ prefixed)
                scope = scope.parent
    
    def _visit_Number(self, node): pass
    def _visit_String(self, node): pass
    def _visit_Nil(self, node): pass
    def _visit_TrueExpr(self, node): pass
    def _visit_FalseExpr(self, node): pass
    def _visit_SemiColon(self, node): pass
    def _visit_Comment(self, node): pass
    def _visit_Break(self, node): pass


    # PATTERN ANALYSIS

    def _analyze_patterns(self):
        """Analyze collected data and generate findings."""
        # must run first: later passes read self.jit_modes
        self._analyze_trace_aborts()
        self._analyze_append_loop()
        self._analyze_table_insert()
        self._analyze_deprecated_funcs()
        self._analyze_math_pow()
        self._analyze_pow_operator()
        self._analyze_string_literal_concat()
        self._analyze_string_find_plain()
        self._analyze_redundant_not_eq()
        self._analyze_uncached_globals()
        self._analyze_repeated_calls_in_scope()
        self._analyze_string_concat_in_loop()
        self._analyze_debug_statements()
        self._analyze_global_writes()
        self._analyze_nil_access()
        self._analyze_dead_code()
        self._analyze_per_frame_callbacks()
        self._analyze_distance_to_comparisons()
        self._analyze_vector_allocations_in_loops()

    def _find_local_var_info(self, scope: Optional[Scope], name: str) -> Optional[LocalVarInfo]:
        """Walk up the scope chain for the LocalVarInfo a name resolves to."""
        while scope is not None:
            info = self.local_vars.get((id(scope), name))
            if info is not None:
                return info
            if name in scope.locals:
                return None  # declared here but untracked (e.g. _ prefixed)
            scope = scope.parent
        return None

    def _aliases_that_would_be_orphaned(self, calls: List[CallInfo]) -> Set[int]:
        """Which alias locals would end up unused if every call in `calls` was rewritten?

        I-038: `local tinsert = table.insert` + a single `tinsert(t, v)` used to
        become `local tinsert = table.insert` + `t[#t+1] = v`, i.e. --fix created
        a brand new `unused_local_variable` finding out of thin air. We refuse to
        rewrite the last surviving use of an alias instead.

        Returns the set of id(LocalVarInfo) that the rewrite would orphan.

        MERGE CONTRACT: pass the *union* of every pass that rewrites the call
        away, not just one pass's candidates. The count is per alias, so two
        passes that each decline in isolation can still orphan an alias between
        them. Concretely, if an `append_loop_counter`-style pass claims the
        in-loop appends and this one declines the flat ones, an alias whose uses
        are *all* in loops dies anyway - on the enabled GAMMA corpus that is
        `350- Ledge Grabbing - Demonized/.../demonized_ledge_grabbing.script:919`,
        the one alias of four whose only use is inside a loop.
        """
        per_alias: Dict[int, Tuple[LocalVarInfo, int]] = {}
        for call in calls:
            if not call.alias_name:
                continue
            info = self._find_local_var_info(call.scope, call.alias_name)
            if info is None or info.is_function:
                continue
            prev = per_alias.get(id(info))
            per_alias[id(info)] = (info, (prev[1] if prev else 0) + 1)
        return {
            key for key, (info, rewritten) in per_alias.items()
            if rewritten >= info.read_count
        }

    # ------------------------------------------------------------------
    # I-013: which bodies actually run on a compiled trace
    # ------------------------------------------------------------------

    def _analyze_trace_aborts(self):
        """Classify every function body as compiled / mixed / interpreted.

        The rule, straight off the measurements in lab/reports/luajit20-nyi.md:

        * a construct in LUAJIT20_NYI_* aborts trace recording, so the loop or
          function containing it runs in the interpreter;
        * a site on the unconditional path (`if_chain_path` empty) aborts every
          time, so the body is `interpreted`;
        * a site that only exists inside an `if` branch aborts on some paths
          and not others -> `mixed`: there is a compiled path, but LuaJIT will
          blacklist the bytecode once it has aborted enough times;
        * no sites at all -> `compiled`.

        A site belongs to the nearest enclosing FUNCTION scope. Sites inside a
        nested closure belong to that closure, not to us - the closure gets its
        own trace, and its own verdict.

        Results land in `self.jit_modes`, keyed by `id(function_scope)`, plus a
        `jit_mode` finding for each per-frame body that is not fully compiled.
        This generation is report-only: nothing here changes what --fix does.
        """
        sites_by_func: Dict[int, List[NyiSiteInfo]] = defaultdict(list)
        for site in self.nyi_sites:
            func_scope = self._find_function_scope(site.scope)
            if func_scope is None:
                continue  # module level; not a hot body, nobody asks about it
            sites_by_func[id(func_scope)].append(site)

        for scope in self.scopes:
            if scope.scope_type != 'function':
                continue
            sites = sites_by_func.get(id(scope), [])
            if not sites:
                mode = 'compiled'
            elif any(not s.if_chain_path for s in sites):
                mode = 'interpreted'
            else:
                mode = 'mixed'
            self.jit_modes[id(scope)] = JitModeInfo(
                mode=mode,
                func_name=scope.name,
                start_line=scope.start_line,
                sites=sites,
            )

        # report the per-frame bodies that cannot be compiled - those are the
        # ones where the choice of transform actually changes with the mode
        for cb in self.per_frame_callbacks:
            info = self.jit_modes.get(id(cb.scope))
            if info is None or info.mode == 'compiled':
                continue
            reasons = info.reasons
            self.findings.append(Finding(
                pattern_name='jit_mode',
                severity='RED',  # informational: never auto-fixed
                line_num=cb.start_line,
                message=(
                    f'Per-frame body {cb.name} runs {info.mode} under LuaJIT 2.0 '
                    f'({len(info.sites)} trace-aborting construct(s)): '
                    + ', '.join(reasons[:3])
                ),
                details={
                    'jit_mode': info.mode,
                    'callback_name': cb.name,
                    'abort_reasons': reasons,
                    'abort_lines': info.lines,
                    'abort_count': len(info.sites),
                    'start_line': cb.start_line,
                    'end_line': cb.end_line,
                },
                source_line=self._get_source_line(cb.start_line),
            ))

    def _jit_mode_for_scope(self, scope: Optional[Scope]) -> str:
        """Mode of the function body enclosing `scope` ('unknown' at module level)."""
        func_scope = self._find_function_scope(scope) if scope else None
        if func_scope is None:
            return 'unknown'
        info = self.jit_modes.get(id(func_scope))
        return info.mode if info else 'unknown'

    def _analyze_table_insert(self):
        """Find table.insert(t, v) that can be t[#t+1] = v."""
        candidates = [
            c for c in self.calls
            if c.full_name == 'table.insert' and len(c.args) == 2
        ]
        orphaned = self._aliases_that_would_be_orphaned(candidates)

        for call in candidates:
            # the counter rewrite (I-001) already owns this call
            if id(call.node) in getattr(self, 'append_loop_claimed', ()):
                continue
            if call.alias_name:
                info = self._find_local_var_info(call.scope, call.alias_name)
                if info is not None and id(info) in orphaned:
                    # rewriting this would leave a dead `local alias = table.insert`
                    continue
            # 2-arg form: table.insert(t, v)
            table_name = self._node_to_string(call.args[0])
            value = self._node_to_string(call.args[1])

            self.findings.append(Finding(
                pattern_name='table_insert_append',
                severity='GREEN',
                line_num=call.line,
                message=f'table.insert({table_name}, v) -> {table_name}[#{table_name}+1] = v',
                details={
                    'table': table_name,
                    'value': value,
                    'full_match': f'table.insert({table_name}, {value})',
                    'node': call.node,
                    # I-002/I-013: table.insert(t, v) compiles fine on a
                    # trace, so this rewrite is worth ~1.00x there and
                    # 1.2-1.5x in the interpreter. The mode says which.
                    'jit_mode': self._jit_mode_for_scope(call.scope),
                },
                source_line=self._get_source_line(call.line),
            ))

    # I-001: counter-based append. Anchor node types and the small allow-list of
    # expressions that can never evaluate to nil in Lua 5.1 (they either produce
    # a value or raise). Anything outside this list might be nil, and appending
    # nil is exactly what makes a hoisted counter diverge from `#t+1`.
    _APPEND_LOOP_NODES = (Fornum, Forin, While, Repeat)
    _NEVER_NIL_NODES = (
        Number, String, Table, TrueExpr, FalseExpr, AnonymousFunction,
        Concat, AriOp, RelOp, BitOp, ULengthOP, UMinusOp, UBNotOp, ULNotOp,
    )

    def _analyze_append_loop(self):
        """Find append-only local tables in loops that can use a hoisted counter.

        `t[#t+1] = v` re-runs the array-boundary search on every single append.
        A counter doesn't: ~13x faster with the JIT on, ~3x interpreted. That's
        the biggest single win on the board - but only if nothing else can touch
        the table while the loop runs, because a stale count silently corrupts
        it and nobody notices for hours.

        So the proof here is deliberately paranoid. The table has to be a local
        declared with a table constructor in the *same block* as the loop, every
        single mention of it from the declaration to the end of the loop has to
        be an append, and nothing may rebind or capture the name. One occurrence
        we can't explain and the whole candidate is dropped.
        """
        self.append_loop_claimed = set()
        tree = getattr(self, '_ast_tree', None)
        if tree is None:
            return

        # `local table_insert = table.insert` is everywhere in mod code, and
        # ALAO's own table_insert_append rewrites those into `t[#t+1] = v`.
        # If we didn't resolve the alias here, pass 1 would produce our shape
        # and pass 2 would rewrite it - i.e. --fix would stop being a fixpoint.
        self._table_insert_call_ids = {
            id(c.node) for c in self.calls
            if c.full_name == 'table.insert' and len(c.args) == 2
        }

        for stmts in self._iter_stmt_lists(tree):
            for i, stmt in enumerate(stmts):
                if not isinstance(stmt, self._APPEND_LOOP_NODES):
                    continue
                self._append_loop_for(stmts, i, stmt)

    def _iter_stmt_lists(self, tree):
        """Yield every statement list in the file (i.e. every Block body)."""
        for node in ast.walk(tree):
            if isinstance(node, Block) and isinstance(node.body, list):
                yield node.body

    def _append_loop_for(self, stmts, loop_index, loop):
        """Try to prove a counter rewrite for every table declared before `loop`."""
        # candidate tables: `local t = {...}` earlier in this same block. A later
        # re-declaration of the same name kills the candidate - we'd have no idea
        # which binding the loop body is talking about
        decls = {}
        for j in range(loop_index):
            s = stmts[j]
            if not isinstance(s, LocalAssign):
                continue
            for t in (s.targets or []):
                if isinstance(t, Name):
                    decls.pop(t.id, None)
            if (len(s.targets or []) == 1 and len(s.values or []) == 1
                    and isinstance(s.targets[0], Name)
                    and isinstance(s.values[0], Table)):
                ctor = s.values[0]
                # a keyed field ({[2]=x} or {a=1}) or any hole means #t is not a
                # reliable starting point, so we can't seed the counter
                if all(f.key is None for f in (ctor.fields or [])):
                    decls[s.targets[0].id] = (j, len(ctor.fields or []) == 0)

        if not decls:
            return

        # `#t` is an O(log n) boundary search, so the counter only pays off once
        # the table gets long: measured 1.06x at 5 iterations (below G2's 1.15x
        # bar) but 1.6x at 20, 3.7x at 100 and ~10x at 2000. If the loop bound
        # is a literal we can read, and it's short, there is nothing to win and
        # we leave the code alone. Everything else (for-in, dynamic bounds,
        # while/repeat) could run long, so it stays in.
        if self._literal_trip_count_below(loop, APPEND_LOOP_MIN_ITERATIONS):
            return

        # the loop header is evaluated outside the body; if it reads the table
        # (e.g. `while #t < 10 do`) the count is load-bearing and we bail
        header = [getattr(loop, a, None) for a in ('start', 'stop', 'step', 'test', 'iter')]

        for name, (decl_index, empty_ctor) in decls.items():
            sites = []
            if not self._scan_node(header, name, sites, False) or sites:
                continue
            # statements between the declaration and the loop may only append
            pre_sites = []
            if not self._scan_stmts(stmts[decl_index + 1:loop_index], name, pre_sites, False):
                continue
            # ...and then the loop itself
            loop_sites = []
            if not self._scan_stmts([loop], name, loop_sites, False):
                continue
            if not loop_sites:
                continue

            # GREEN needs every appended value to be provably non-nil. Append a
            # nil and `#t` stops growing while a counter marches on, so the two
            # forms genuinely diverge - see the nil case in the tests
            all_non_nil = all(isinstance(v, self._NEVER_NIL_NODES) for _, _, v in loop_sites)
            # Organizer decision (gen-1, 2026-09-11): YELLOW unconditionally. The
            # rewrite never regresses (1.05x floor at 5 iters, ~10x at 2000) but
            # 0 of the 254 corpus sites are per-frame and this is the one
            # transform whose failure mode is a table that silently miscounts
            # for hours. Users who want it ask for --fix-yellow; plain --fix
            # stays conservative. all_non_nil is still recorded for the report.
            severity = 'YELLOW'

            seed = '0' if (empty_ctor and not pre_sites) else '#%s' % name

            # Nothing is claimed away from table_insert_append here any more:
            # under plain --fix the ordinary t[#t+1] rewrite still happens, and
            # under --fix-yellow the transformer lets the counter win (and keeps
            # the I-038 alias-orphan guard honest, see _resolve_counter_claims).

            self.findings.append(Finding(
                pattern_name='append_loop_counter',
                severity=severity,
                line_num=self._get_line(loop),
                message=('Append-only table %s in loop: hoist a counter '
                         '(local n = %s; n = n + 1; %s[n] = v)' % (name, seed, name)),
                details={
                    'table': name,
                    'seed': seed,
                    'loop_node': loop,
                    'sites': loop_sites,
                    'site_count': len(loop_sites),
                    'all_non_nil': all_non_nil,
                    'file_names': self._all_identifiers(),
                    'scope': self._enclosing_function_scope(self._get_line(loop)),
                },
                source_line=self._get_source_line(self._get_line(loop)),
            ))

    @staticmethod
    def _literal_trip_count_below(loop, minimum):
        """True when `loop` is a numeric for whose trip count we can read off
        the source and it is under `minimum`. Anything we can't read returns
        False - we only skip loops we can prove are short."""
        if not isinstance(loop, Fornum):
            return False
        start, stop, step = loop.start, loop.stop, getattr(loop, 'step', None)
        if not (isinstance(start, Number) and isinstance(stop, Number)):
            return False
        s_val = 1
        if isinstance(step, Number):
            s_val = step.n
        elif step is not None and not isinstance(step, int):
            return False
        try:
            if s_val == 0:
                return False
            trips = int((stop.n - start.n) / s_val) + 1
        except Exception:
            return False
        return 0 <= trips < minimum

    def _all_identifiers(self):
        """Every identifier used anywhere in the file - the set a new counter
        name has to dodge. Coarser than real scope resolution, on purpose."""
        cached = getattr(self, '_cached_identifiers', None)
        if cached is not None:
            return cached
        names = set()
        tree = getattr(self, '_ast_tree', None)
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, Name):
                    names.add(node.id)
        self._cached_identifiers = names
        return names

    def _enclosing_function_scope(self, line):
        """Innermost recorded function scope containing `line`, if any."""
        best = None
        for s in self.scopes:
            if s.scope_type != 'function':
                continue
            end = s.end_line if s.end_line and s.end_line > 0 else line
            if s.start_line <= line <= end:
                if best is None or s.start_line > best.start_line:
                    best = s
        return best

    def _classify_append(self, stmt, name):
        """Is `stmt` an append to `name`? Returns (kind, value_node) or None.

        Only statement position counts. `x = table.insert(t, v)` as an
        expression is not something we can turn into two statements.
        """
        if isinstance(stmt, LocalAssign):
            return None
        if isinstance(stmt, Assign) and len(stmt.targets or []) == 1 and len(stmt.values or []) == 1:
            tgt = stmt.targets[0]
            if (isinstance(tgt, Index) and isinstance(tgt.value, Name)
                    and tgt.value.id == name and isinstance(tgt.idx, AddOp)):
                left, right = tgt.idx.left, tgt.idx.right
                for a, b in ((left, right), (right, left)):
                    if (isinstance(a, ULengthOP) and isinstance(a.operand, Name)
                            and a.operand.id == name
                            and isinstance(b, Number) and b.n == 1):
                        return ('index', stmt.values[0])
            return None
        if isinstance(stmt, Call) and len(stmt.args or []) == 2:
            if not (isinstance(stmt.args[0], Name) and stmt.args[0].id == name):
                return None
            func = stmt.func
            direct = (isinstance(func, Index) and isinstance(func.value, Name)
                      and func.value.id == 'table' and isinstance(func.idx, Name)
                      and func.idx.id == 'insert')
            aliased = id(stmt) in getattr(self, '_table_insert_call_ids', ())
            if direct or aliased:
                return ('insert', stmt.args[1])
        return None

    def _scan_stmts(self, stmts, name, sites, in_func):
        """Walk a statement list. False the moment we see a mention of `name`
        that isn't an append we can rewrite."""
        for s in stmts:
            app = self._classify_append(s, name)
            if app is not None:
                kind, value = app
                if in_func:
                    # an append from inside a closure can run after the loop
                    return False
                # the value must not read the table either (`t[#t+1] = #t`)
                if not self._scan_node(value, name, sites, in_func):
                    return False
                sites.append((kind, s, value))
                continue
            if not self._scan_node(s, name, sites, in_func):
                return False
        return True

    def _scan_node(self, node, name, sites, in_func):
        """Structural walk. Any bare occurrence of `name` fails - which also
        takes care of rebinding, since a binding target is a Name node too."""
        if node is None:
            return True
        if isinstance(node, list):
            for item in node:
                if not self._scan_node(item, name, sites, in_func):
                    return False
            return True
        if not isinstance(node, Node):
            return True
        if isinstance(node, Name):
            return node.id != name
        if isinstance(node, Block):
            return self._scan_stmts(node.body or [], name, sites, in_func)
        if isinstance(node, (Function, LocalFunction, Method, AnonymousFunction)):
            in_func = True
        for key, child in vars(node).items():
            if key.startswith('_') or key == 'comments':
                continue
            if not self._scan_node(child, name, sites, in_func):
                return False
        return True

    def _analyze_deprecated_funcs(self):
        """Find deprecated functions: table.getn, string.len."""
        for call in self.calls:
            if call.full_name == 'table.getn' and len(call.args) == 1:
                arg = self._node_to_string(call.args[0])
                self.findings.append(Finding(
                    pattern_name='table_getn',
                    severity='GREEN',
                    line_num=call.line,
                    message=f'table.getn({arg}) -> #{arg}',
                    details={
                        'table': arg,
                        'full_match': f'table.getn({arg})',
                        'node': call.node,
                    },
                    source_line=self._get_source_line(call.line),
                ))

            elif call.full_name == 'string.len' and len(call.args) == 1:
                arg = self._node_to_string(call.args[0])
                self.findings.append(Finding(
                    pattern_name='string_len',
                    severity='GREEN',
                    line_num=call.line,
                    message=f'string.len({arg}) -> #{arg}',
                    details={
                        'string': arg,
                        'full_match': f'string.len({arg})',
                        'node': call.node,
                    },
                    source_line=self._get_source_line(call.line),
                ))

    def _analyze_math_pow(self):
        """Find math.pow that can be simplified."""
        for call in self.calls:
            if call.full_name == 'math.pow' and len(call.args) == 2:
                base = self._node_to_string(call.args[0])
                exp_node = call.args[1]

                # check for simple cases
                if isinstance(exp_node, Number):
                    exp = exp_node.n
                    full_match = f'math.pow({base}, {exp})'

                    if exp == 0.5:
                        self.findings.append(Finding(
                            pattern_name='math_pow_simple',
                            severity='GREEN',
                            line_num=call.line,
                            message=f'{full_match} -> {base}^0.5',
                            details={
                                'base': base,
                                'exponent': exp,
                                'type': 'sqrt',
                                'is_simple': True,
                                'full_match': full_match,
                                'node': call.node,
                            },
                            source_line=self._get_source_line(call.line),
                        ))
                    elif exp in (2, 3, 4) and self._is_simple_expr(call.args[0]):
                        replacement = '*'.join([base] * int(exp))
                        self.findings.append(Finding(
                            pattern_name='math_pow_simple',
                            severity='GREEN',
                            line_num=call.line,
                            message=f'{full_match} -> {replacement}',
                            details={
                                'base': base,
                                'exponent': int(exp),
                                'type': 'power',
                                'is_simple': True,
                                'full_match': full_match,
                                'node': call.node,
                            },
                            source_line=self._get_source_line(call.line),
                        ))

    def _is_simple_expr(self, node: Node) -> bool:
        """Check if node is a simple expression (safe to repeat)."""
        return isinstance(node, (Name, Number))

    def _analyze_string_literal_concat(self):
        """Find `"a" .. "b" .. "c"` chains where every leaf is a String literal.

        These can be combined at compile time into a single string literal,
        eliminating the runtime concat allocation. Walk the AST top-down: when
        we hit a Concat whose entire subtree is all-Strings, emit a finding
        for it and *don't* recurse into the children (their Concats are
        subsumed by the outer one we're rewriting).
        """
        if not getattr(self, '_ast_tree', None):
            return

        def all_string_leaves(node, out):
            """Return True iff every leaf of this Concat tree is a String."""
            if isinstance(node, Concat):
                return all_string_leaves(node.left, out) and all_string_leaves(node.right, out)
            if isinstance(node, String):
                out.append(node)
                return True
            return False

        def walk(node):
            if isinstance(node, Concat):
                leaves = []
                if all_string_leaves(node, leaves) and len(leaves) >= 2:
                    self._emit_string_literal_concat_finding(node, leaves)
                    return  # children subsumed - don't recurse
            for child in self._iter_children(node):
                walk(child)

        walk(self._ast_tree)

    def _emit_string_literal_concat_finding(self, concat_node, leaves):
        """Emit a finding for an all-literal Concat tree.

        Stores a list of (start, end) spans for each String leaf along with
        the decoded text. The transformer reuses the source text of the
        first leaf as the new literal, splicing in the combined contents.
        """
        # decode each String into the concatenated value. luaparser keeps
        # the decoded form on `.s` (without quotes/escape processing of the
        # source - bytes/str depending on encoding).
        parts = []
        for leaf in leaves:
            s = leaf.s
            if isinstance(s, bytes):
                s = s.decode('utf-8', errors='replace')
            parts.append(s)
        combined_value = ''.join(parts)

        line = self._get_line(concat_node)
        # produce a Lua literal for the combined value. Use _node_to_string
        # which already escapes properly.
        # Build a synthetic String AST node would be overkill; just call
        # the same escape routine indirectly by constructing a fake node.
        # Easiest: do the escape inline (mirrors _node_to_string).
        escaped = combined_value.replace('\\', '\\\\')
        escaped = escaped.replace('\a', '\\a')
        escaped = escaped.replace('\b', '\\b')
        escaped = escaped.replace('\f', '\\f')
        escaped = escaped.replace('\n', '\\n')
        escaped = escaped.replace('\r', '\\r')
        escaped = escaped.replace('\t', '\\t')
        escaped = escaped.replace('\v', '\\v')
        escaped = escaped.replace('\0', '\\0')
        if '"' in escaped and "'" not in escaped:
            escaped_lit = "'" + escaped.replace("'", "\\'") + "'"
        else:
            escaped_lit = '"' + escaped.replace('"', '\\"') + '"'

        self.findings.append(Finding(
            pattern_name='string_literal_concat',
            severity='GREEN',
            line_num=line,
            message=f'fold {len(leaves)} string literals into one',
            details={
                'parts_count': len(leaves),
                'combined': escaped_lit,
                'node': concat_node,
            },
            source_line=self._get_source_line(line),
        ))

    # Lua-pattern metacharacters. If a needle contains none of these, it's a
    # plain text search and `string.find(s, needle, 1, true)` is much faster
    # than the default pattern interpretation.
    _LUA_PATTERN_META = frozenset('^$().%[]*+-?')

    def _analyze_string_find_plain(self):
        """Find `string.find(s, "literal", ...)` calls where the needle is a
        plain string (no pattern metacharacters). Adding `, 1, true` (or
        `, true` if `init` is already given) skips pattern compilation. Only
        rewrite the 2-arg and 3-arg forms - with 4+ args the user has
        already supplied a `plain` flag explicitly.
        """
        for call in self.calls:
            if call.full_name != 'string.find':
                continue
            n_args = len(call.args)
            if n_args < 2 or n_args > 3:
                continue
            needle = call.args[1]
            if not isinstance(needle, String):
                continue
            s = needle.s
            if isinstance(s, bytes):
                s = s.decode('utf-8', errors='replace')
            if any(ch in self._LUA_PATTERN_META for ch in s):
                continue

            self.findings.append(Finding(
                pattern_name='string_find_plain',
                severity='GREEN',
                line_num=call.line,
                message=f'string.find with plain literal "{s[:30]}" - add plain=true',
                details={
                    'n_args': n_args,
                    'node': call.node,
                },
                source_line=self._get_source_line(call.line),
            ))

    def _analyze_redundant_not_eq(self):
        """Find `not (a == b)` / `not (a ~= b)` and rewrite to `a ~= b` / `a == b`.

        Pure boolean identity - works for any operand types Lua supports
        (no NaN trap like comparison operators have, since `==` already
        handles NaN consistently).

        Skips patterns where the operand is anything other than EqToOp /
        NotEqToOp (e.g. `not (a < b)` is *not* equivalent to `a >= b` for
        floats with NaN, so we leave inequalities alone).
        """
        if not getattr(self, '_ast_tree', None):
            return
        for node in ast.walk(self._ast_tree):
            if not isinstance(node, ULNotOp):
                continue
            op = node.operand
            if isinstance(op, EqToOp):
                new_op = '~='
                shape = '== '
            elif isinstance(op, NotEqToOp):
                new_op = '=='
                shape = '~= '
            else:
                continue

            self.findings.append(Finding(
                pattern_name='redundant_not_eq',
                severity='GREEN',
                line_num=self._get_line(node),
                message=f'not (a {shape.strip()} b) -> a {new_op} b',
                details={
                    'new_op': new_op,
                    'left_node': op.left,
                    'right_node': op.right,
                    'outer_node': node,
                },
                source_line=self._get_source_line(self._get_line(node)),
            ))

    def _analyze_pow_operator(self):
        """Find `x ^ 2` / `x ^ 3` patterns rewriteable to `x*x` / `x*x*x`.

        In LuaJIT 2.0 the `^` operator with an integer exponent dispatches
        through a generic pow VM helper, while `x*x` compiles to a single
        MUL bytecode. Same shape as the math.pow optimization, but the
        operator form was previously not detected.
        """
        if not getattr(self, '_ast_tree', None):
            return
        for node in ast.walk(self._ast_tree):
            if not isinstance(node, ExpoOp):
                continue
            right = node.right
            if not isinstance(right, Number):
                continue
            exp = right.n
            if exp not in (2, 3) or not self._is_simple_expr(node.left):
                continue
            base = self._node_to_string(node.left)
            replacement = '*'.join([base] * int(exp))
            full_match = f'{base}^{int(exp)}'
            self.findings.append(Finding(
                pattern_name='pow_op_simple',
                severity='GREEN',
                line_num=self._get_line(node),
                message=f'{full_match} -> {replacement}',
                details={
                    'base': base,
                    'exponent': int(exp),
                    'replacement': replacement,
                    'full_match': full_match,
                    'node': node,
                },
                source_line=self._get_source_line(self._get_line(node)),
            ))
    
    def _count_calls_branch_aware(self, calls: List) -> int:
        """Maximum number of calls executable on any single control-flow path.

        Each item carries its full ancestor `if_chain_path` (sequence of
        (if_id, branch_index) tuples). The algorithm walks that tree depth by
        depth: at each level, items whose path ends here count directly, and
        items whose path reaches deeper are partitioned by next-step
        (if_id, branch). For each if-chain encountered we sum (across the
        chain) the *max* across its branches - only one branch executes. The
        recursion handles arbitrarily nested if-chains correctly.
        """

        def count_at(items, depth: int) -> int:
            if not items:
                return 0

            direct = 0
            # if_id -> branch_idx -> list of items still descending
            nested: Dict[int, Dict[int, list]] = {}

            for item in items:
                path = item.if_chain_path
                if len(path) <= depth:
                    direct += 1
                else:
                    if_id, branch_idx = path[depth]
                    chain = nested.setdefault(if_id, {})
                    chain.setdefault(branch_idx, []).append(item)

            total = direct
            for if_id, branches in nested.items():
                # exactly one branch of this if-chain runs - take the worst case
                max_in_chain = 0
                for branch_idx, branch_items in branches.items():
                    branch_count = count_at(branch_items, depth + 1)
                    if branch_count > max_in_chain:
                        max_in_chain = branch_count
                total += max_in_chain
            return total

        return count_at(calls, 0)

    def _analyze_uncached_globals(self):
        """Find frequently used globals that should be cached."""
        # count calls by full_name, grouped by function scope
        scope_calls: Dict[Scope, Dict[str, List[CallInfo]]] = defaultdict(lambda: defaultdict(list))

        for call in self.calls:
            # find enclosing function scope
            func_scope = self._find_function_scope(call.scope)
            if func_scope:
                scope_calls[func_scope][call.full_name].append(call)

        # check each function
        for func_scope, calls_by_name in scope_calls.items():
            globals_to_cache = {}

            for name, calls in calls_by_name.items():
                # skip if already cached or has direct replacement
                if name in DIRECT_REPLACEMENT_FUNCS:
                    continue
                if name in func_scope.cached_globals:
                    continue

                # check if it's a cacheable global
                is_bare = name in CACHEABLE_BARE_GLOBALS
                is_module_func = False

                if '.' in name:
                    module, func = name.split('.', 1)
                    if module in CACHEABLE_MODULE_FUNCS and func in CACHEABLE_MODULE_FUNCS[module]:
                        is_module_func = True

                if not is_bare and not is_module_func:
                    continue

                # threshold: configurable (default 4), hot callbacks use threshold-1
                # with --experimental, use branch-aware counting
                threshold = self.cache_threshold - 1 if func_scope.is_hot_callback else self.cache_threshold
                call_count = self._count_calls_branch_aware(calls)
                if call_count >= threshold:
                    globals_to_cache[name] = calls

            if globals_to_cache:
                # skip global scope - only cache inside actual functions
                if func_scope.name == '<global>' or func_scope.scope_type == 'global':
                    continue

                # create summary finding for this function
                example_lines = []
                for name, calls in list(globals_to_cache.items())[:5]:
                    for c in calls[:2]:
                        example_lines.append(f"L{c.line}: {name}")

                self.findings.append(Finding(
                    pattern_name='uncached_globals_summary',
                    severity='GREEN',
                    line_num=func_scope.start_line,
                    message=f'Cache {len(globals_to_cache)} globals in {func_scope.name}',
                    details={
                        'globals': {n: len(c) for n, c in globals_to_cache.items()},
                        'globals_info': globals_to_cache,  # name -> list of CallInfo with nodes
                        'function': func_scope.name,
                        'is_hot': func_scope.is_hot_callback,
                        'scope': func_scope,
                        # I-013: caching a global is an interpreter win
                        # (1.23x) and a no-op on a compiled trace (1.00x).
                        'jit_mode': (self.jit_modes[id(func_scope)].mode
                                     if id(func_scope) in self.jit_modes else 'unknown'),
                    },
                    source_line='\n'.join(example_lines),
                ))

    def _find_function_scope(self, scope: Scope) -> Optional[Scope]:
        """Find the enclosing function scope, or None if at module level.

        Must NOT fall back to global_scope: callers use the returned scope as
        the insertion target for cache decls, and inserting at module scope
        captures runtime-only values (db.actor, alife()) at script-load time
        when they are still nil.
        """
        while scope:
            if scope.scope_type == 'function':
                return scope
            scope = scope.parent
        return None

    def _has_unwarned_chained_use(self, func_scope: Scope, name: str) -> bool:
        """Is `name()`'s result used through a chain we do NOT already warn about?

        `alife():object(id)` is in NIL_RETURNING_FUNCTIONS so it's flagged either
        way; `alife():create(x)` is not, so caching it into a local would be the
        only reason a warning appears. See I-038.
        """
        receiver = f'{name}()'
        for call in self.calls:
            if call.module != receiver:
                continue
            if self._find_function_scope(call.scope) is not func_scope:
                continue
            if call.full_name not in NIL_RETURNING_FUNCTIONS:
                return True
        return False

    # `x = time_global()`, with or without the `local`. Used to spot the
    # self-timing shape below.
    _TG_ASSIGN_RE = re.compile(r'(?:local\s+)?([A-Za-z_][A-Za-z0-9_.]*)\s*=\s*time_global\s*\(\s*\)')
    _TG_RETURN_RE = re.compile(r'(^|[^\w.:])return\b')

    def _time_global_cacheable(self, func_scope: Scope, calls: List) -> bool:
        """Is hoisting `local tg = time_global()` to the top of this body safe?

        Three shapes say no:

        1. a `while` or `repeat` anywhere in this body. Those are the only
           loops whose trip count can depend on the clock, and a hoisted read
           turns `while time_global() - t0 < 100 do ... end` into an infinite
           loop. (On the real engine that loop already hangs, since
           dwTimeGlobal is a per-frame stamp - but we are not betting the
           rewrite on that.) The whole body is disqualified, not just the reads
           under the loop, because the read in the condition belongs to the
           function scope and a read in the body can feed the condition.
           Numeric and generic `for` loops terminate whatever the clock says,
           so a read inside one is fine and in fact the best case: the hoist
           saves one call per iteration.
        2. an early `return` above the first read, *and* a call layout that
           makes the transformer hoist the declaration to the top of the body
           (a read inside a loop, or reads in different branches - see
           `_edit_repeated_calls`). Then the bail-out path would make a call
           the original did not. When every read sits in the same block the
           declaration lands on the first read's own line, nothing above it
           changes, and the guard does not apply - that is 42% of the
           candidates on GAMMA, so the distinction is worth making.
        3. self-timing: `local t0 = time_global()` ... `time_global() - t0`,
           i.e. "how long did this take". Folding the two reads into one turns
           the answer into a constant 0. The engine already answers 0 for this
           (that is what `time_global_async()` is for), so the code is measuring
           nothing either way - but proving that is not our job here, so leave
           the body alone. Order matters: the assignment must come *before* the
           subtraction. The other way round is the throttle idiom
           (`if time_global() - last > 250 then last = time_global()`), which is
           exactly what we want to fix.

        2 and 3 are line-based, like the other heuristics in here. They only
        ever drop candidates.
        """
        start = func_scope.start_line
        end = func_scope.end_line if func_scope.end_line and func_scope.end_line > 0 else start
        first_call_line = min(c.line for c in calls)

        # (1) a while/repeat anywhere in this body (not in a nested closure -
        # that closure gets its own hoist decision)
        for scope in self.scopes:
            if scope.scope_type != 'loop' or scope.name not in ('<while>', '<repeat>'):
                continue
            if self._find_function_scope(scope) is func_scope:
                return False

        # (2) an early return above the first read, only when the decl gets
        # hoisted to the top of the body anyway
        hoist_likely = any(c.in_loop or c.if_chain_path != calls[0].if_chain_path
                           for c in calls)
        if hoist_likely:
            for ln in range(start + 1, first_call_line):
                code = self._get_source_line(ln).split('--', 1)[0]
                if self._TG_RETURN_RE.search(code):
                    return False

        # (3) self-timing: assignment from the clock, subtracted from a later read
        assign_line: Dict[str, int] = {}
        for ln in range(start, end + 1):
            code = self._get_source_line(ln).split('--', 1)[0]
            for m in self._TG_ASSIGN_RE.finditer(code):
                assign_line.setdefault(m.group(1), ln)
        if assign_line:
            for ln in range(start, end + 1):
                code = self._get_source_line(ln).split('--', 1)[0]
                if 'time_global' not in code:
                    continue
                for var, aline in assign_line.items():
                    if aline >= ln:
                        continue
                    pat = (r'time_global\s*\(\s*\)\s*-\s*' + re.escape(var) + r'\b'
                           r'|\b' + re.escape(var) + r'\s*-\s*time_global\s*\(')
                    if re.search(pat, code):
                        return False
        return True

    def _analyze_repeated_calls_in_scope(self):
        """Find repeated expensive calls within function scope."""
        # expensive function calls (need parens) to track
        # NOTE: time_global() IS included since I-040, with its own threshold
        # and its own safety guards - see just below. It used to be excluded on
        # the grounds that "it returns a different value each call", which is
        # not true within one frame: it is the render device's per-frame stamp.
        # NOTE: level.object_by_id() is NOT auto-fixed because different IDs give
        # different objects, and even same IDs can change if object is destroyed
        # NOTE: `db.actor` is a *property*, not a function - it's tracked via
        # self.indexes (see EXPENSIVE_INDEXES) and folded into the same buckets
        # below.
        expensive_calls = {'alife', 'system_ini', 'game_ini', 'getFS',
                           'device', 'get_console', 'get_hud', 'level.name'}

        # I-040: time_global() is the top trace killer in per-frame bodies
        # (186 abort sites). It is safe to cache *within one body* because it
        # returns `Device.dwTimeGlobal`, the render device's per-frame time
        # stamp, which the engine writes once per frame in FrameMove - that is
        # also why the engine ships a separate `time_global_async()` for code
        # that wants a sub-frame clock. Nothing a Lua body can do advances the
        # frame, so every call inside one invocation returns the same number.
        # It gets its own threshold of 2 (not cache_threshold) because each
        # call is an engine C call, not an index: on the corpus every single
        # body that calls it is `interpreted` or `mixed`, never `compiled`.
        expensive_calls.add('time_global')
        # NOTE: `time_global_async()` is deliberately NOT cacheable - it is the
        # asynchronous clock and does change between two reads. 0 corpus uses.

        # method calls that are safe to cache (immutable object properties)
        # based on X-Ray engine source analysis:
        # - :section() returns stored NameSection member (xr_object.h:155)
        # - :id() returns stored Props.net_ID member (xr_object.h:98)
        # - :clsid() returns stored m_script_clsid member (GameObject.h:257)
        # - :story_id() returns m_story_id set once from config (xrServer_Objects_ALife.cpp:375)
        cacheable_methods = {'section', 'id', 'clsid', 'story_id'}

        # group by function scope. Both CallInfo and IndexInfo carry the same
        # set of fields the downstream code depends on (line, node, scope,
        # parent_if_node, branch_index), so we can mix them in one bucket.
        scope_calls: Dict[Scope, Dict[str, List]] = defaultdict(lambda: defaultdict(list))

        for call in self.calls:
            # Only argument-less calls are cacheable. `alife()` and
            # `alife(l08_yantar)` are not the same call, and folding them into
            # one `local sim = alife()` silently drops the argument - which is
            # exactly what --fix did to operacia_monolith.script (I-038).
            if call.args:
                continue

            if call.full_name in expensive_calls:
                func_scope = self._find_function_scope(call.scope)
                if func_scope:
                    scope_calls[func_scope][call.full_name].append(call)

            # track cacheable method calls on objects (:section(), :id(), :clsid())
            if call.func in cacheable_methods and ':' in call.full_name:
                func_scope = self._find_function_scope(call.scope)
                if func_scope:
                    key = f"{call.full_name}()"
                    scope_calls[func_scope][key].append(call)

        # Property-style accesses (db.actor, etc.). These flow through the
        # same bucket as calls so the threshold check, branch-aware count,
        # and finding emission below all work uniformly.
        for idx in self.indexes:
            if idx.full_name in EXPENSIVE_INDEXES:
                func_scope = self._find_function_scope(idx.scope)
                if func_scope:
                    scope_calls[func_scope][idx.full_name].append(idx)

        for func_scope, calls_by_name in scope_calls.items():
            for name, calls in calls_by_name.items():
                if name == 'time_global':
                    threshold = TIME_GLOBAL_CACHE_THRESHOLD
                else:
                    threshold = self.cache_threshold - 1 if func_scope.is_hot_callback else self.cache_threshold
                call_count = self._count_calls_branch_aware(calls)

                if call_count >= threshold:
                    if name == 'time_global' and not self._time_global_cacheable(func_scope, calls):
                        continue
                    # I-038: caching a call that may return nil turns a hidden
                    # hazard into a local that the nil pass then flags at every
                    # use, so --fix manufactured brand new potential_nil_access
                    # findings (10 on GAMMA, all from `alife()`).
                    # `alife():object(id)` is fine - that chain is in
                    # NIL_RETURNING_FUNCTIONS, so it's flagged before and after
                    # the rewrite. `alife():create(x)` is not in the table, so
                    # only the rewritten `local sim = alife(); sim:create(x)`
                    # gets flagged, and --fix ends up creating work for itself.
                    # Both shapes are equally nil-unsafe; the analyzer just
                    # can't see the direct one. Until it can, don't cache a
                    # nil-returning call whose result is used in a way we
                    # wouldn't have warned about anyway.
                    if isinstance(calls[0], CallInfo) and name in NIL_RETURNING_FUNCTIONS:
                        if self._has_unwarned_chained_use(func_scope, name):
                            continue

                    # suggest caching
                    severity = 'GREEN'

                    if name == 'db.actor':
                        suggestion = 'local actor = db.actor'
                    elif name == 'time_global':
                        suggestion = 'local tg = time_global()'
                    elif name == 'alife':
                        suggestion = 'local sim = alife()'
                    elif name == 'system_ini':
                        suggestion = 'local ini = system_ini()'
                    elif name == 'device':
                        suggestion = 'local dev = device()'
                    elif name == 'get_console':
                        suggestion = 'local console = get_console()'
                    elif name == 'get_hud':
                        suggestion = 'local hud = get_hud()'
                    elif name == 'level.name':
                        suggestion = 'local level_name = level.name()'
                    else:
                        suggestion = f'Cache {name} result'

                    self.findings.append(Finding(
                        pattern_name=f'repeated_{name.replace(".", "_").replace(":", "_")}',
                        severity=severity,
                        line_num=calls[0].line,
                        message=f'{name} called {len(calls)}x in {func_scope.name}',
                        details={
                            'count': len(calls),
                            'function': func_scope.name,
                            'is_hot': func_scope.is_hot_callback,
                            'suggestion': suggestion,
                            'lines': [c.line for c in calls],
                            'calls': calls,  # list of CallInfo with nodes
                            'scope': func_scope,
                            'original_call': name,  # preserve original like "self.object:id()"
                        },
                        source_line=suggestion,
                    ))

    # `for i = <lit>, <lit> [, <lit>] do` - the only loop shape whose trip count
    # we can read straight off the source. Everything else (pairs, ipairs,
    # while, expression bounds) returns None = unknown.
    _LITERAL_FORNUM_RE = re.compile(
        r'^\s*for\s+\w+\s*=\s*(-?\d+)\s*,\s*(-?\d+)\s*(?:,\s*(-?\d+)\s*)?do\b'
    )

    def _literal_loop_iterations(self, loop_start_line) -> Optional[int]:
        """Trip count of a numeric for with literal bounds, else None."""
        if not loop_start_line:
            return None
        line = self._get_source_line(loop_start_line)
        if not line:
            return None
        m = self._LITERAL_FORNUM_RE.match(line)
        if not m:
            return None
        start, stop = int(m.group(1)), int(m.group(2))
        step = int(m.group(3)) if m.group(3) else 1
        if step == 0:
            return None
        n = (stop - start) // step + 1
        return max(0, n)

    def _analyze_string_concat_in_loop(self):
        """Find string concatenation patterns in loops."""
        # find self-concatenation: s = s .. x
        loop_concats: Dict[Tuple[Scope, str], List[ConcatInfo]] = defaultdict(list)

        for concat in self.concats:
            if concat.in_loop and concat.target and concat.left_var:
                if concat.target == concat.left_var:
                    # self concat: s = s .. x
                    key = (concat.scope, concat.target)
                    loop_concats[key].append(concat)

        for (scope, var), concats in loop_concats.items():
            if len(concats) >= 1:
                concat_info = concats[0]
                loop_scope = concat_info.loop_scope
                
                # check if variable is initialized to empty string before loop
                init_line = None
                is_safe = False
                
                # SAFETY: don't auto-fix nested loops (loop_depth > 1) because we can't
                # reliably determine which loop's end to place table.concat after
                if loop_scope and concat_info.loop_depth == 1:
                    # look for var = "" or var = '' IMMEDIATELY before the loop
                    # must be: within 3 lines, NOT inside any loop, and must be local declaration
                    empty_strings = ('""', "''", '[[]]')
                    for assign in self.assigns:
                        if (assign.target == var and
                            assign.value_type == 'literal' and
                            assign.value_repr in empty_strings and
                            assign.line < loop_scope.start_line and
                            assign.line >= loop_scope.start_line - 3 and
                            not assign.in_loop and
                            assign.is_local and
                            # SAFETY: the loop's enclosing scope must match the
                            # init's scope. If the loop is nested deeper (e.g.
                            # inside an `if` between init and loop), inserting
                            # `local var = table.concat(...)` after the loop's
                            # `end` lands in the wrong scope and breaks code
                            # that uses the variable past that scope.
                            loop_scope.parent is assign.scope):
                            init_line = assign.line
                            is_safe = True
                            break

                # SAFETY/PERF: if the loop is a numeric `for` with literal
                # bounds we know exactly how many times it runs. Below the
                # measured breakeven the table.concat rewrite is SLOWER than
                # the naive concat (see STRING_CONCAT_BREAKEVEN_ITERS), so
                # don't offer it as a fix.
                iter_bound = None
                if loop_scope:
                    iter_bound = self._literal_loop_iterations(loop_scope.start_line)
                    if iter_bound is not None and iter_bound < STRING_CONCAT_BREAKEVEN_ITERS:
                        is_safe = False

                self.findings.append(Finding(
                    pattern_name='string_concat_in_loop',
                    severity='YELLOW',
                    line_num=concat_info.line,
                    message=f'String concat in loop: {var} = {var} .. x',
                    details={
                        'variable': var,
                        'count': len(concats),
                        'loop_depth': concat_info.loop_depth,
                        'suggestion': 'Use table.insert() + table.concat()',
                        'right_expr': concat_info.right_expr,
                        'loop_start': loop_scope.start_line if loop_scope else None,
                        'loop_end': loop_scope.end_line if loop_scope else None,
                        'init_line': init_line,
                        'is_safe': is_safe,
                        'iter_bound': iter_bound,
                        'concat_lines': [c.line for c in concats],
                    },
                    source_line=self._get_source_line(concat_info.line),
                ))

    def _analyze_debug_statements(self):
        """Find debug/logging statements."""
        for call in self.calls:
            func_name = call.func
            # exclude math.log - it's mathematical logarithm, not logging
            if call.full_name and call.full_name.startswith('math.'):
                continue
            if func_name in DEBUG_FUNCTIONS:
                self.findings.append(Finding(
                    pattern_name='debug_statement',
                    severity='DEBUG',
                    line_num=call.line,
                    message=f'Debug call: {func_name}()',
                    details={
                        'function': func_name,
                        'node': call.node,
                    },
                    source_line=self._get_source_line(call.line),
                ))

    def _analyze_global_writes(self):
        """Track global variable writes."""
        for name, line in self.global_writes:
            # skip common patterns that are intentional
            if name.startswith('_') or name.isupper():
                continue

            self.findings.append(Finding(
                pattern_name='global_write',
                severity='RED',
                line_num=line,
                message=f'Global write: {name}',
                details={
                    'variable': name,
                },
                source_line=self._get_source_line(line),
            ))

    def _analyze_nil_access(self):
        """Generate findings for potential nil access patterns."""
        for access in self.nil_accesses:
            nil_source = access.nil_source
            reason = NIL_RETURNING_FUNCTIONS.get(nil_source.source_func, 'may return nil')
            
            # determine severity based on whether it's safe to fix
            if access.is_safe_to_fix:
                severity = 'YELLOW'  # can be auto-fixed with --fix-nil
                message = (f"Potential nil access: '{access.var_name}' from {nil_source.source_func}() "
                          f"used without nil check (auto-fixable)")
            else:
                severity = 'YELLOW'  # warning only, needs manual review
                message = (f"Potential nil access: '{access.var_name}' from {nil_source.source_func}() "
                          f"used without nil check")
            
            self.findings.append(Finding(
                pattern_name='potential_nil_access',
                severity=severity,
                line_num=access.access_line,
                message=message,
                details={
                    'var_name': access.var_name,
                    'source_func': nil_source.source_func,
                    'source_call': nil_source.source_call,
                    'assign_line': nil_source.assign_line,
                    'access_call': access.access_call,
                    'access_type': access.access_type,
                    'is_safe_to_fix': access.is_safe_to_fix,
                    'is_local': nil_source.is_local,
                    'reason': reason,
                },
                source_line=self._get_source_line(access.access_line),
            ))

    def _analyze_dead_code(self):
        """Analyze for dead/unreachable code patterns."""
        if not hasattr(self, '_ast_tree') or self._ast_tree is None:
            return
        
        # Phase 1: 100% safe patterns (auto-fixable)
        self._detect_code_after_return()
        self._detect_code_after_break()
        self._detect_if_false_blocks()
        self._detect_while_false_loops()
        self._detect_unnecessary_else()
        self._detect_constant_conditions()
        
        # Phase 2: Warning patterns (not auto-fixable)
        self._detect_unused_local_vars()
        self._detect_unused_local_funcs()

    def _detect_code_after_return(self):
        """Detect unreachable code after unconditional return statements."""
        self._walk_for_dead_after_terminator(Return, 'return')

    def _detect_code_after_break(self):
        """Detect unreachable code after break statements in loops."""
        self._walk_for_dead_after_terminator(Break, 'break')

    def _walk_for_dead_after_terminator(self, terminator_type, terminator_name: str):
        """Walk AST to find dead code after terminators (return/break)."""
        
        def check_block(block_body: List[Node], scope_name: str, in_loop: bool = False):
            """Check a block for dead code after terminators."""
            if not block_body:
                return
            
            for i, stmt in enumerate(block_body):
                # check if this is a terminator
                is_terminator = isinstance(stmt, terminator_type)
                
                # for break, only count as terminator if we're in a loop
                if isinstance(stmt, Break) and not in_loop:
                    continue
                
                if is_terminator and i < len(block_body) - 1:
                    # there are statements after the terminator
                    dead_start = i + 1
                    dead_stmts = block_body[dead_start:]
                    
                    # filter out comments and semicolons
                    real_dead = [s for s in dead_stmts 
                                if not isinstance(s, (Comment, SemiColon))]
                    
                    if real_dead:
                        first_dead = real_dead[0]
                        last_dead = real_dead[-1]
                        start_line = self._get_line(first_dead)
                        end_line = self._get_end_line(last_dead) or start_line
                        
                        # get code preview
                        preview_lines = []
                        for ln in range(start_line, min(start_line + 3, end_line + 1)):
                            if 0 < ln <= len(self.source_lines):
                                preview_lines.append(self.source_lines[ln - 1].rstrip())
                        code_preview = '\n'.join(preview_lines)
                        if end_line > start_line + 2:
                            code_preview += '\n...'
                        
                        self.dead_code.append(DeadCodeInfo(
                            dead_type=f'after_{terminator_name}',
                            start_line=start_line,
                            end_line=end_line,
                            scope_name=scope_name,
                            description=f'Unreachable code after {terminator_name}',
                            is_safe_to_remove=True,
                            code_preview=code_preview,
                            node=first_dead,
                        ))
                        
                        self.findings.append(Finding(
                            pattern_name=f'dead_code_after_{terminator_name}',
                            severity='GREEN',  # safe to auto-fix
                            line_num=start_line,
                            message=f'Unreachable code after {terminator_name} statement (lines {start_line}-{end_line})',
                            details={
                                'dead_type': f'after_{terminator_name}',
                                'start_line': start_line,
                                'end_line': end_line,
                                'scope_name': scope_name,
                                'is_safe_to_remove': True,
                                'dead_stmt_count': len(real_dead),
                            },
                            source_line=self._get_source_line(start_line),
                        ))
                
                # recurse into nested structures
                if isinstance(stmt, (Function, LocalFunction, Method)):
                    if hasattr(stmt, 'body') and stmt.body:
                        body = stmt.body.body if isinstance(stmt.body, Block) else [stmt.body]
                        func_name = self._get_func_name(stmt)
                        check_block(body, func_name, False)
                
                elif isinstance(stmt, If):
                    if hasattr(stmt, 'body') and stmt.body:
                        body = stmt.body.body if isinstance(stmt.body, Block) else [stmt.body]
                        check_block(body, scope_name, in_loop)
                    if hasattr(stmt, 'orelse') and stmt.orelse:
                        if isinstance(stmt.orelse, Block):
                            check_block(stmt.orelse.body, scope_name, in_loop)
                        elif isinstance(stmt.orelse, (If, ElseIf)):
                            check_block([stmt.orelse], scope_name, in_loop)
                
                elif isinstance(stmt, ElseIf):
                    if hasattr(stmt, 'body') and stmt.body:
                        body = stmt.body.body if isinstance(stmt.body, Block) else [stmt.body]
                        check_block(body, scope_name, in_loop)
                    if hasattr(stmt, 'orelse') and stmt.orelse:
                        if isinstance(stmt.orelse, Block):
                            check_block(stmt.orelse.body, scope_name, in_loop)
                        elif isinstance(stmt.orelse, (If, ElseIf)):
                            check_block([stmt.orelse], scope_name, in_loop)
                
                elif isinstance(stmt, (While, Repeat)):
                    if hasattr(stmt, 'body') and stmt.body:
                        body = stmt.body.body if isinstance(stmt.body, Block) else [stmt.body]
                        check_block(body, scope_name, True)  # now in a loop
                
                elif isinstance(stmt, (Fornum, Forin)):
                    if hasattr(stmt, 'body') and stmt.body:
                        body = stmt.body.body if isinstance(stmt.body, Block) else [stmt.body]
                        check_block(body, scope_name, True)  # now in a loop
        
        # start from the root
        if hasattr(self._ast_tree, 'body') and self._ast_tree.body:
            body = self._ast_tree.body.body if isinstance(self._ast_tree.body, Block) else [self._ast_tree.body]
            check_block(body, '<global>', False)

    def _get_func_name(self, node: Node) -> str:
        """Get function name from function node."""
        if isinstance(node, Function):
            return self._node_to_string(node.name) if node.name else '<anon>'
        elif isinstance(node, LocalFunction):
            return node.name.id if isinstance(node.name, Name) else '<anon>'
        elif isinstance(node, Method):
            source = self._node_to_string(node.source)
            method = node.name.id if isinstance(node.name, Name) else ""
            return f"{source}:{method}"
        return '<unknown>'

    def _detect_if_false_blocks(self):
        """Detect 'if false then ... end' blocks."""
        self._walk_for_false_conditions(If, 'if_false')

    def _detect_while_false_loops(self):
        """Detect 'while false do ... end' loops."""
        self._walk_for_false_conditions(While, 'while_false')

    def _walk_for_false_conditions(self, node_type, dead_type: str):
        """Walk AST to find if/while with literal false conditions."""

        def is_literal_false(node: Node) -> bool:
            """Check if node is literal false or nil."""
            return isinstance(node, (FalseExpr, Nil))

        # single flat walk - O(n) instead of O(n²)
        for node in ast.walk(self._ast_tree):
            if isinstance(node, node_type):
                if hasattr(node, 'test') and is_literal_false(node.test):
                    # CRITICAL: only auto-remove `if false then ... end` when
                    # there are no elseif/else branches. With branches, the
                    # if-chain still has reachable code (`if false then ...
                    # elseif cond then BAR end` - BAR runs when cond is true)
                    # and removing the entire chain drops live behaviour.
                    if node_type is If and getattr(node, 'orelse', None) is not None:
                        continue

                    start_line = self._get_line(node)
                    end_line = self._get_end_line(node) or start_line
                    
                    # get code preview
                    preview_lines = []
                    for ln in range(start_line, min(start_line + 3, end_line + 1)):
                        if 0 < ln <= len(self.source_lines):
                            preview_lines.append(self.source_lines[ln - 1].rstrip())
                    code_preview = '\n'.join(preview_lines)
                    if end_line > start_line + 2:
                        code_preview += '\n...'
                    
                    type_name = 'if' if node_type == If else 'while'
                    
                    self.dead_code.append(DeadCodeInfo(
                        dead_type=dead_type,
                        start_line=start_line,
                        end_line=end_line,
                        scope_name='<unknown>',
                        description=f'{type_name} false block (never executes)',
                        is_safe_to_remove=True,
                        code_preview=code_preview,
                        node=node,
                    ))
                    
                    self.findings.append(Finding(
                        pattern_name=f'dead_code_{dead_type}',
                        severity='GREEN',  # safe to auto-fix
                        line_num=start_line,
                        message=f'Dead code: {type_name} false (lines {start_line}-{end_line})',
                        details={
                            'dead_type': dead_type,
                            'start_line': start_line,
                            'end_line': end_line,
                            'scope_name': '<unknown>',
                            'is_safe_to_remove': True,
                        },
                        source_line=self._get_source_line(start_line),
                    ))

    def _detect_unnecessary_else(self):
        """Detect unnecessary else blocks after if blocks that always return/break.
        
        Pattern:
            if condition then
                return x
            else            -- This else is unnecessary
                return y
            end
        
        Can be simplified to:
            if condition then
                return x
            end
            return y
        """
        from luaparser.astnodes import If, ElseIf, Block, Return, Break
        
        def block_always_terminates(body) -> bool:
            """Check if a block always ends with return or break."""
            if not body:
                return False
            
            # Get the body list
            if isinstance(body, Block):
                stmts = body.body if hasattr(body, 'body') else []
            elif isinstance(body, list):
                stmts = body
            else:
                return False
            
            if not stmts:
                return False
            
            # Check if last statement is return or break
            last_stmt = stmts[-1] if stmts else None
            return isinstance(last_stmt, (Return, Break))
        
        def check_if_node(node: If):
            """Check an if node for unnecessary else."""
            if not node.orelse:
                return  # No else clause
            
            # Check if the if body always terminates
            if not block_always_terminates(node.body):
                return  # If body doesn't always terminate, else is needed
            
            # Get else clause info
            orelse = node.orelse
            
            # Handle elseif chain - check if ALL branches terminate
            if isinstance(orelse, ElseIf):
                # For elseif, we need more complex analysis
                # Skip for now - just handle simple if/else
                return
            
            # Simple else block
            if isinstance(orelse, Block):
                else_start = self._get_line(orelse)
                if_start = self._get_line(node)
                
                # Find the 'else' keyword line (should be just before the else block content)
                # The else block starts at the 'else' keyword
                else_keyword_line = else_start
                
                # Look backwards from else block to find 'else' keyword
                for search_line in range(else_start, if_start, -1):
                    line_text = self._get_source_line(search_line)
                    if line_text and line_text.strip().lower() == 'else':
                        else_keyword_line = search_line
                        break
                
                self.findings.append(Finding(
                    pattern_name='unnecessary_else',
                    severity='YELLOW',  # style suggestion, not auto-fix for now
                    line_num=else_keyword_line,
                    message=f'Unnecessary else after return/break - code can be simplified',
                    details={
                        'if_line': if_start,
                        'else_line': else_keyword_line,
                        'suggestion': 'Remove else and dedent the else body',
                    },
                    source_line=self._get_source_line(else_keyword_line),
                ))
        
        def walk(node):
            """Walk AST looking for if statements."""
            if isinstance(node, If):
                check_if_node(node)
            
            # Recurse into children
            for child in self._iter_children(node):
                walk(child)
        
        walk(self._ast_tree)

    def _detect_constant_conditions(self):
        """Detect if/while statements with constant conditions.
        
        Patterns detected:
        - if 1 then (always true)
        - if 0 then (always false in Lua? No - 0 is truthy!)
        - if nil then (always false)
        - if "string" then (always true)
        - while true do (intentional infinite loop - skip)
        - while 1 do (always true)
        
        Note: In Lua, only nil and false are falsy. 0, "", etc are truthy.
        """
        from luaparser.astnodes import If, While, TrueExpr, FalseExpr, Nil, Number, String
        
        def is_constant_truthy(node) -> tuple:
            """Check if a node is a constant truthy/falsy value.
            Returns (is_constant, is_truthy, description)
            """
            # nil is always falsy
            if isinstance(node, Nil):
                return (True, False, 'nil')
            
            # false is always falsy
            if isinstance(node, FalseExpr):
                return (True, False, 'false')
            
            # true is always truthy (but usually intentional)
            if isinstance(node, TrueExpr):
                return (True, True, 'true')
            
            # Numbers are always truthy (including 0!)
            if isinstance(node, Number):
                val = node.n if hasattr(node, 'n') else '?'
                return (True, True, f'number {val}')
            
            # Strings are always truthy (including "")
            if isinstance(node, String):
                return (True, True, 'string literal')
            
            return (False, None, None)
        
        def check_condition(node, node_type: str, line: int):
            """Check a condition node."""
            is_const, is_truthy, desc = is_constant_truthy(node)

            if not is_const:
                return

            # Skip "while true do" - this is intentional infinite loop pattern
            if node_type == 'while' and isinstance(node, TrueExpr):
                return

            # Literal nil/false are already handled by _detect_if_false_blocks /
            # _detect_while_false_loops (which emit dead_code_* patterns). Skipping
            # them here avoids duplicate findings AND ensures the auto-fix path
            # (gated on pattern.startswith('dead_code_')) actually runs for them.
            if isinstance(node, (FalseExpr, Nil)):
                return

            # All remaining cases are "always truthy" - the body always runs and the
            # else (if any) is dead. We do not currently auto-remove this case
            # because removing only the else branch is a more involved AST rewrite.
            if node_type == 'if':
                msg = f'Constant condition: if {desc} (always true, else branch is dead code)'
            else:
                msg = f'Constant condition: while {desc} (infinite loop)'
            severity = 'YELLOW'

            self.findings.append(Finding(
                pattern_name='constant_condition',
                severity=severity,
                line_num=line,
                message=msg,
                details={
                    'node_type': node_type,
                    'condition_desc': desc,
                    'is_truthy': True,
                },
                source_line=self._get_source_line(line),
            ))
        
        def walk(node):
            """Walk AST looking for if/while statements."""
            if isinstance(node, If):
                line = self._get_line(node)
                check_condition(node.test, 'if', line)
            elif isinstance(node, While):
                line = self._get_line(node)
                check_condition(node.test, 'while', line)
            
            # Recurse into children
            for child in self._iter_children(node):
                walk(child)
        
        walk(self._ast_tree)

    def _detect_unused_local_vars(self):
        """Detect local variables that are assigned but never read (Phase 2 - warning only).
        
        Uses scope-aware tracking from the visitor pass - variables are keyed by
        (scope_id, var_name) to correctly handle shadowing.
        """
        # Report unused locals from scope-aware tracking
        for (scope_id, name), info in self.local_vars.items():
            # Skip functions (handled separately) and loop vars
            if info.is_function or info.is_loop_var:
                continue
                
            if not info.is_read:
                # Check if it's used as callback (RegisterScriptCallback)
                if name in self.callback_registrations:
                    continue
                
                # Get scope name for better error message
                scope_name = info.scope.name if info.scope else '<unknown>'
                
                self.findings.append(Finding(
                    pattern_name='unused_local_variable',
                    severity='YELLOW',  # warning only, don't auto-fix
                    line_num=info.assign_line,
                    message=f"Local variable '{name}' is assigned but never used in {scope_name}",
                    details={
                        'var_name': name,
                        'assign_line': info.assign_line,
                        'scope_name': scope_name,
                        'is_safe_to_remove': False,  # not safe - might be intentional
                    },
                    source_line=self._get_source_line(info.assign_line),
                ))

    def _detect_unused_local_funcs(self):
        """Detect local functions that are never called (Phase 2 - warning only).
        
        Uses scope-aware tracking from the visitor pass - functions are keyed by
        (scope_id, func_name) to correctly handle shadowing.
        """
        # Report unused local functions from scope-aware tracking
        for (scope_id, name), info in self.local_funcs.items():
            if not info.is_read:
                # Check if it's used as callback (RegisterScriptCallback)
                if name in self.callback_registrations:
                    continue
                
                # Check if it's a known callback name
                if name in HOT_CALLBACKS or name in SAFE_CALLBACK_PARAMS:
                    continue
                
                # Get scope name for better error message
                scope_name = info.scope.name if info.scope else '<unknown>'
                
                self.findings.append(Finding(
                    pattern_name='unused_local_function',
                    severity='YELLOW',  # warning only
                    line_num=info.assign_line,
                    message=f"Local function '{name}' appears to be unused in {scope_name}",
                    details={
                        'func_name': name,
                        'assign_line': info.assign_line,
                        'scope_name': scope_name,
                        'is_safe_to_remove': False,  # not safe - might be callback
                    },
                    source_line=self._get_source_line(info.assign_line),
                ))

    def _analyze_per_frame_callbacks(self):
        """Analyze per-frame callbacks and flag them with performance info.
        
        These callbacks run every frame and deserve extra attention:
        - actor_on_update
        - npc_on_update
        - monster_on_update
        - physic_object_on_update
        - etc
        """
        # Expensive calls that should be avoided or cached in per-frame callbacks
        EXPENSIVE_CALLS = frozenset({
            'pairs', 'ipairs', 'string.find', 'string.match', 'string.gmatch',
            'string.gsub', 'string.format', 'table.sort', 'table.concat',
            'io.open', 'io.read', 'io.write', 'os.execute',
            'alife', 'alife_object', 'level.object_by_id', 'simulation_objects',
            'get_story_object', 'get_object_by_name',
        })
        
        for callback_info in self.per_frame_callbacks:
            scope = callback_info.scope
            scope_id = id(scope)

            # `_is_descendant_of(s)` returns True iff s == scope or any ancestor
            # of s is scope. The previous implementation only checked the call's
            # immediate scope and its direct parent, so anything inside a nested
            # if/loop/block (which is exactly where the cost lives) was invisible.
            def _is_in_callback(s):
                cur = s
                while cur is not None:
                    if id(cur) == scope_id:
                        return True
                    # stop at the next enclosing FUNCTION boundary - calls
                    # inside nested closures belong to those closures, not us
                    if cur is not s and cur.scope_type == 'function':
                        return False
                    cur = cur.parent
                return False

            # gather statistics about the callback
            calls_in_scope = [c for c in self.calls if c.scope and _is_in_callback(c.scope)]

            # count loops anywhere inside the callback (not just direct children)
            loop_count = sum(
                1 for s in self.scopes
                if s.scope_type == 'loop' and s is not scope and _is_in_callback(s)
            )
            
            # find expensive calls
            expensive_calls = []
            for call in calls_in_scope:
                if call.full_name in EXPENSIVE_CALLS or call.func in EXPENSIVE_CALLS:
                    expensive_calls.append(f"{call.full_name} (line {call.line})")
            
            # find uncached globals used multiple times
            global_usage = defaultdict(list)
            for call in calls_in_scope:
                if call.full_name in CACHEABLE_BARE_GLOBALS or \
                   (call.module and call.module in CACHEABLE_MODULE_FUNCS):
                    global_usage[call.full_name].append(call.line)
            
            uncached_globals = []
            for name, lines in global_usage.items():
                if len(lines) >= 2 and name not in scope.cached_globals:
                    uncached_globals.append(f"{name} ({len(lines)}x)")
            
            # build message
            issues = []
            if loop_count > 0:
                issues.append(f"{loop_count} loop(s)")
            if expensive_calls:
                issues.append(f"{len(expensive_calls)} expensive call(s)")
            if uncached_globals:
                issues.append(f"{len(uncached_globals)} uncached global(s)")
            
            # determine severity based on issues found
            if expensive_calls or loop_count > 0:
                severity = 'RED'
            elif uncached_globals:
                severity = 'YELLOW'
            else:
                severity = 'DEBUG'
            
            # I-013: say whether the body can even be JIT-compiled. On a
            # compiled trace ALAO's caching transforms are worth ~nothing; in
            # the interpreter they are worth 1.05-1.63x. The mode is the
            # difference between "worth doing" and "noise".
            jit_info = self.jit_modes.get(id(scope))
            if jit_info and jit_info.mode != 'compiled':
                issues.append(f"{jit_info.mode} (LuaJIT 2.0)")

            # always report per-frame callbacks
            message = f"Per-frame callback: {callback_info.name} (lines {callback_info.start_line}-{callback_info.end_line})"
            if issues:
                message += f" - {', '.join(issues)}"
            
            self.findings.append(Finding(
                pattern_name='per_frame_callback',
                severity=severity,
                line_num=callback_info.start_line,
                message=message,
                details={
                    'callback_name': callback_info.name,
                    'start_line': callback_info.start_line,
                    'end_line': callback_info.end_line,
                    'loop_count': loop_count,
                    'expensive_calls': expensive_calls,
                    'uncached_globals': uncached_globals,
                    'total_calls': len(calls_in_scope),
                    'jit_mode': jit_info.mode if jit_info else 'unknown',
                    'jit_abort_reasons': jit_info.reasons if jit_info else [],
                    'jit_abort_lines': jit_info.lines if jit_info else [],
                },
                source_line=self._get_source_line(callback_info.start_line),
            ))

    # @TODO: Check distance_to() implementation in xray source code more thoroughly
    def _analyze_distance_to_comparisons(self):
        """
        Find distance_to() calls in comparisons that can use distance_to_sqr() instead.
        Should replace compared value with its square too.
        
        Pattern: pos:distance_to(target) < 10
        Optimized: pos:distance_to_sqr(target) < 100  -- (10^2, avoids sqrt)
        
        This is auto-fixable and provides performance improvement
        since distance_to() requires a square root operation.
        """
        for comp in self.distance_comparisons:
            squared_threshold = comp.threshold_value ** 2
            
            # format the squared value nicely
            if squared_threshold == int(squared_threshold):
                squared_str = str(int(squared_threshold))
            else:
                squared_str = f"{squared_threshold:.6g}"
            
            original = f"{comp.source_obj}:distance_to({comp.target_obj}) {comp.comparison_op} {comp.threshold_value}"
            optimized = f"{comp.source_obj}:distance_to_sqr({comp.target_obj}) {comp.comparison_op} {squared_str}"
            
            self.findings.append(Finding(
                pattern_name='distance_to_comparison',
                severity='GREEN',  # Auto-fixable
                line_num=comp.line,
                message=f'Use distance_to_sqr() to avoid sqrt: {original} -> {optimized}',
                details={
                    'source_obj': comp.source_obj,
                    'target_obj': comp.target_obj,
                    'comparison_op': comp.comparison_op,
                    'original_threshold': comp.threshold_value,
                    'squared_threshold': squared_threshold,
                    'squared_threshold_str': squared_str,
                    'invoke_node': comp.invoke_node,
                    'threshold_node': comp.threshold_node,
                    'full_node': comp.full_node,
                },
                source_line=self._get_source_line(comp.line),
            ))

    def _analyze_vector_allocations_in_loops(self):
        """
        Find vector() allocations inside loops.
        
        Each vector() call allocates memory that must be garbage collected.
        In loops, especially in per-frame callbacks, this can cause significant
        GC pressure and frame drops.
        
        Solution: Pre-allocate vectors at module level and reuse with :set()
        
        Example:
            -- Bad: allocates every iteration
            for i = 1, 100 do
                local pos = vector():set(x, y, z)
            end
            
            -- Good: reuse pre-allocated vector
            local temp_vec = vector()  -- at module level
            for i = 1, 100 do
                temp_vec:set(x, y, z)
            end
        
        Most of them are NOT auto-fixable: the vector has to die with the
        iteration. `_find_reusable_scratch_vectors()` does that proof; the
        ones that pass come out YELLOW with `is_safe_to_fix` and everything
        the transformer needs to hoist them, the rest stay RED and are just
        reported like before.
        """
        reusable = self._find_reusable_scratch_vectors()

        for alloc in self.vector_allocations:
            info = reusable.get(id(alloc.call_node))

            context = ""
            if alloc.in_per_frame_callback:
                context = " in per-frame callback"
            if alloc.loop_depth > 1:
                context += f" (nested {alloc.loop_depth} loops deep)"

            details = {
                'loop_depth': alloc.loop_depth,
                'in_per_frame_callback': alloc.in_per_frame_callback,
                'node': alloc.call_node,
                'is_safe_to_fix': False,
            }

            if info is not None:
                severity = 'YELLOW'
                message = (f"vector() allocation in loop{context} - hoist one scratch vector "
                           f"above the loop and reuse it with :set()")
                details.update(info)
                details['is_safe_to_fix'] = True
            else:
                severity = 'RED'
                message = f"vector() allocation in loop{context} - pre-allocate and reuse with :set()"

            self.findings.append(Finding(
                pattern_name='vector_alloc_in_loop',
                severity=severity,
                line_num=alloc.line,
                message=message,
                details=details,
                source_line=self._get_source_line(alloc.line),
            ))

    # --- scratch-vector escape analysis ------------------------------------

    @staticmethod
    def _child_nodes(node):
        """Yield the AST children of a node, skipping token/comment bookkeeping."""
        for field_name in list(node.__dict__):
            if field_name.startswith('_') or field_name in (
                    'first_token', 'last_token', 'comments'):
                continue
            value = getattr(node, field_name, None)
            if isinstance(value, Node):
                yield value
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, Node):
                        yield item

    @classmethod
    def _iter_subtree(cls, node):
        """Every node in the subtree rooted at `node`, `node` included."""
        stack = [node]
        while stack:
            cur = stack.pop()
            yield cur
            stack.extend(cls._child_nodes(cur))

    @staticmethod
    def _is_bare_vector_call(node) -> bool:
        """True for a literal `vector()` with no arguments."""
        return (isinstance(node, Call)
                and isinstance(node.func, Name)
                and node.func.id == 'vector'
                and not node.args)

    def _find_reusable_scratch_vectors(self) -> Dict[int, Dict[str, Any]]:
        """Find `vector():set(...)` sites in loops whose result cannot escape.

        Returns `{id(vector_call_node): details}` for the safe ones. Anything
        not in the map is assumed to escape - that's the whole safety story
        here, so the walk only ever *adds* a site it has positively proved.

        The three shapes we accept:
          1. `vector():set(...)` as a statement on its own (result thrown away)
          2. `vector():set(...)` passed straight to a method in
             VECTOR_ARG_SAFE_METHODS, which copies the floats out
          3. `local p = vector():set(...)` where every later mention of `p`
             inside the same loop body is a field read, a call to one of
             VECTOR_SELF_METHODS on `p`, or an argument to one of
             VECTOR_ARG_SAFE_METHODS - and none of them is inside a nested
             function (that would capture it as an upvalue).

        Everything else - a store into a table field, a global, `self.x`, a
        return, a plain function call, a closure, an identity comparison -
        falls through and stays RED.
        """
        tree = getattr(self, '_ast_tree', None)
        # the main visitor already told us whether this file has any vector()
        # inside a loop at all - and almost none of them do, so bail before
        # paying for two full-tree walks
        if tree is None or not self.vector_allocations:
            return {}

        found: Dict[int, Dict[str, Any]] = {}

        # every node that is written to, so `p.x = 1` / `p = q` can be told
        # apart from a read of the same expression
        assign_targets: Set[int] = set()
        for node in self._iter_subtree(tree):
            if isinstance(node, (Assign, LocalAssign)):
                for tgt in (node.targets or []):
                    assign_targets.add(id(tgt))

        def walk(node, parent, loop_stack, blocked):
            """loop_stack holds the enclosing loops *of the current function*.

            `blocked` means we're inside a closure that is itself written
            inside a loop - the hoisted declaration would land in that outer
            loop and we'd be right back where we started, so we skip those.
            """
            if isinstance(node, Invoke) and self._is_bare_vector_call(node.source) \
                    and isinstance(node.func, Name) and node.func.id == 'set' \
                    and loop_stack and not blocked:
                info = self._classify_scratch_use(node, parent, loop_stack, assign_targets)
                if info is not None:
                    found[id(node.source)] = info

            # a function body starts a fresh loop context: a vector built in a
            # closure inside a loop is allocated per call of the closure, and
            # hoisting past the closure boundary would change its lifetime
            if isinstance(node, (Function, LocalFunction, Method, AnonymousFunction)):
                inner_stack = []
                inner_blocked = blocked or bool(loop_stack)
            elif isinstance(node, (Fornum, Forin, While, Repeat)):
                inner_stack = loop_stack + [node]
                inner_blocked = blocked
            else:
                inner_stack = loop_stack
                inner_blocked = blocked

            for child in self._child_nodes(node):
                if isinstance(node, (Fornum, Forin, While, Repeat)):
                    # only the body is "inside" the loop; the iterator
                    # expression / test runs outside it
                    child_stack = inner_stack if child is getattr(node, 'body', None) else loop_stack
                else:
                    child_stack = inner_stack
                walk(child, node, child_stack, inner_blocked)

        walk(tree, None, [], False)
        return found

    def _classify_scratch_use(self, invoke, parent, loop_stack,
                              assign_targets: Set[int]) -> Optional[Dict[str, Any]]:
        """Decide whether `invoke` (a `vector():set(...)`) escapes its iteration."""
        outer_loop = loop_stack[0]
        inner_loop = loop_stack[-1]

        ok = False

        # shape 1: bare expression statement - nobody holds the result
        if isinstance(parent, Block):
            ok = True

        # shape 2: handed to a method that copies the floats out
        elif isinstance(parent, Invoke) and any(a is invoke for a in (parent.args or [])):
            fname = parent.func.id if isinstance(parent.func, Name) else None
            ok = fname in VECTOR_ARG_SAFE_METHODS

        # shape 3: bound to a local whose every use stays in the iteration
        elif isinstance(parent, LocalAssign):
            targets = parent.targets or []
            values = parent.values or []
            if len(targets) == 1 and len(values) == 1 and values[0] is invoke \
                    and isinstance(targets[0], Name):
                ok = self._alias_stays_in_iteration(
                    targets[0].id, targets[0], inner_loop, assign_targets)

        if not ok:
            return None

        return {
            'vector_call_node': invoke.source,
            'invoke_node': invoke,
            'hoist_line': getattr(outer_loop, 'line', None),
        }

    def _alias_stays_in_iteration(self, name: str, decl_target, loop_node,
                                  assign_targets: Set[int]) -> bool:
        """True if every mention of `name` inside `loop_node` is a safe read.

        Scans the whole loop body rather than doing real scope resolution, so
        an unrelated outer variable with the same name can only make us say
        no. That's the direction we want to be wrong in.
        """
        roots = [loop_node.body]
        if isinstance(loop_node, Repeat):
            # `until` can still see body locals
            roots.append(loop_node.test)

        # parent + "is this inside a nested function" for every node we visit
        def scan(node, parent, in_closure) -> bool:
            if isinstance(node, Name) and node.id == name and node is not decl_target:
                if in_closure:
                    return False            # captured as an upvalue
                if id(node) in assign_targets:
                    return False            # reassigned - we lose track of it
                if isinstance(parent, Index) and parent.value is node:
                    # p.x / p["x"] - a read unless the whole index is written to
                    return id(parent) not in assign_targets
                if isinstance(parent, Invoke):
                    if parent.source is node:
                        fname = parent.func.id if isinstance(parent.func, Name) else None
                        return fname in VECTOR_SELF_METHODS
                    if any(a is node for a in (parent.args or [])):
                        fname = parent.func.id if isinstance(parent.func, Name) else None
                        return fname in VECTOR_ARG_SAFE_METHODS
                return False

            nested = in_closure or isinstance(
                node, (Function, LocalFunction, Method, AnonymousFunction))
            return all(scan(child, node, nested) for child in self._child_nodes(node))

        return all(scan(root, loop_node, False) for root in roots)

    def _get_source_line(self, line_num: int) -> str:
        """Get source line by number."""
        if 0 < line_num <= len(self.source_lines):
            return self.source_lines[line_num - 1].rstrip()
        return ""


def analyze_file(file_path: Path, cache_threshold: int = 4, experimental: bool = False) -> List[Finding]:
    """Convenience function to analyze a file."""
    analyzer = ASTAnalyzer(cache_threshold=cache_threshold, experimental=experimental)
    return analyzer.analyze_file(file_path)
