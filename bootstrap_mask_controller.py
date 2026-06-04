"""
bootstrap_mask_controller.py
============================

Builds the `/project1/mask_controller` Base COMP from scratch (idempotent —
safe to re-run; will not overwrite existing children).

This COMP holds all mask SOURCE selection + blending + cycling + state
generation that USED to live inside particle_system. particle_system now
takes a SINGLE `in_mask` (TOP) + a `in_mask_state` (CHOP, 4 channels) and
all the upstream logic is here, so swapping logo sources or merging in a
realsense depth-derived mask becomes a wiring change OUTSIDE the particle
system.

Connectors
----------
Inputs  (in TOP / in CHOP):
  [0] in_mask_a  TOP — first mask source (e.g. logo A)
  [1] in_mask_b  TOP — second mask source (e.g. logo B)
  [2] in_depth   TOP — depth-derived mask (or a placeholder until realsense
                       is wired)
  [0] in_pose    CHOP — pose data (visibility channels) for standby fade

Outputs (out TOP / out CHOP):
  [0] out_mask   TOP — the final composed mask, fed into particle_system/in_mask
  [0] out_state  CHOP — 4 channels (amt, trans, hueoffset, burstcolor) fed into
                       particle_system/in_mask_state

Pars (Mask page)
----------------
  Maskmode       Off / Standby / Always
  Maskcycle      toggle, auto-cycle between in_mask_a / in_mask_b
  Maskcycletime  seconds a source holds before the next swap
  Maskswitchdur  swap duration (cross-dissolve seconds)
  Maskhuestep    radians added to the persistent hue accumulator per swap
  Maskfade       standby crossfade seconds
  Maskburstcolor HDR multiplier on the swap-time glow (paired with `morph`)
  Maskdepthmix   0=logos only, 1=depth only; intermediate blends both
  Maskvisfloor   per-joint visibility floor for presence detection
  Maskminjoints  how many joints must clear the floor to count as "present"
  Maskpulse      toggle — periodic branding pulse in standby
  Maskpulseinterval / Maskpulsehold / Maskpulsefade  the pulse timing (seconds)

Usage
-----
Run this script in TouchDesigner (textport, or paste into a Text DAT and Run
Script). Re-running is safe — it only creates missing children.
"""

import os

ROOT = op('/project1')

# -------------------------------------------------------------------- COMP
mc = ROOT.op('mask_controller')
if mc is None:
    mc = ROOT.create(baseCOMP, 'mask_controller')
    mc.nodeX, mc.nodeY = -800, 0
    print('+ created mask_controller')
else:
    print('= mask_controller already exists, keeping')

# -------------------------------------------------------------------- pars
def _page(name):
    for pg in mc.customPages:
        if pg.name == name:
            return pg
    return mc.appendCustomPage(name)


def _ensure_menu(page, name, label, items, default):
    if getattr(mc.par, name, None) is not None:
        return
    page.appendMenu(name, label=label)
    p = getattr(mc.par, name)
    p.menuNames  = list(items)
    p.menuLabels = list(items)
    p.default = default; p.val = default


def _ensure_float(page, name, label, default, lo, hi, cmin=True, cmax=False):
    if getattr(mc.par, name, None) is not None:
        return
    page.appendFloat(name, label=label)
    p = getattr(mc.par, name)
    p.default = default; p.val = default
    p.normMin = lo; p.normMax = hi
    p.clampMin = cmin; p.clampMax = cmax


def _ensure_toggle(page, name, label, default):
    if getattr(mc.par, name, None) is not None:
        return
    page.appendToggle(name, label=label)
    p = getattr(mc.par, name)
    p.default = default; p.val = default


pg = _page('Mask')
_ensure_menu (pg, 'Maskmode',      'Mode', ['Off','Standby','Always'], 'Standby')
_ensure_toggle(pg, 'Maskcycle',    'Auto Cycle Sources', True)
_ensure_float(pg, 'Maskcycletime', 'Cycle Time (s)',   12.0, 0.0, 60.0,  cmax=False)
_ensure_float(pg, 'Maskswitchdur', 'Switch Duration (s)', 1.5, 0.05, 8.0, cmax=False)
_ensure_float(pg, 'Maskhuestep',   'Hue Step / Swap (rad)', 0.0, -6.28, 6.28)
_ensure_float(pg, 'Maskfade',      'Standby Fade (s)',  1.5, 0.0, 6.0,  cmax=False)
_ensure_float(pg, 'Maskburstcolor','Swap Burst Color',  1.0, 0.0, 4.0,  cmax=False)
_ensure_float(pg, 'Maskdepthmix',  'Depth Mix (0=logos, 1=depth)', 0.0, 0.0, 1.0)
# Presence detection (device-robust standby — count confidently-visible joints).
_ensure_float(pg, 'Maskvisfloor',   'Visibility Floor',       0.5, 0.0, 1.0)
_ensure_float(pg, 'Maskminjoints',  'Min Joints Present',     3,   1.0, 10.0)
# Optional periodic branding pulse in standby (logo fades in every N seconds,
# pushing through even when someone is present).
_ensure_toggle(pg, 'Maskpulse',         'Standby Logo Pulse', False)
_ensure_float(pg, 'Maskpulseinterval',  'Logo Pulse Interval (s)', 30.0, 4.0, 120.0, cmax=False)
_ensure_float(pg, 'Maskpulsehold',      'Logo Pulse Hold (s)',      6.0, 0.0, 30.0,  cmax=False)
_ensure_float(pg, 'Maskpulsefade',      'Logo Pulse Fade (s)',      3.0, 0.2, 15.0,  cmax=False)


# -------------------------------------------------------------------- inputs
def _ensure_in(typecls, name, order, x, y):
    o = mc.op(name)
    if o is None:
        o = mc.create(typecls, name)
    o.nodeX, o.nodeY = x, y
    if hasattr(o.par, 'connectorder'):
        o.par.connectorder = order
    return o

in_mask_a = _ensure_in(inTOP,  'in_mask_a', 0, -700, 200)
in_mask_b = _ensure_in(inTOP,  'in_mask_b', 1, -700, 100)
in_depth  = _ensure_in(inTOP,  'in_depth',  2, -700,   0)
in_pose   = _ensure_in(inCHOP, 'in_pose',   0, -700, -200)


# -------------------------------------------------------------------- switch_mask
sw = mc.op('switch_mask')
if sw is None:
    sw = mc.create(switchTOP, 'switch_mask')
sw.nodeX, sw.nodeY = -450, 150
sw.par.blend = True
sw.inputConnectors[0].connect(in_mask_a)
sw.inputConnectors[1].connect(in_mask_b)


# -------------------------------------------------------------------- depth threshold + merge
dt = mc.op('depth_threshold')
if dt is None:
    dt = mc.create(levelTOP, 'depth_threshold')
dt.nodeX, dt.nodeY = -450, -50
dt.inputConnectors[0].connect(in_depth)
# Pars: black/white levels for depth slicing (user adjusts to clip the
# subject volume). Clamp True so values outside [0,1] don't poison the merge.
try:
    dt.par.blacklevel = 0.15
    dt.par.whitelevel = 0.6
    dt.par.clamp = True
except Exception as e:
    print(f'  depth_threshold par set fail: {e}')

# Composite TOP merging switch_mask (RGB) with depth_threshold (RGB) — Add for
# now; the Crossfade par on switch_mask drives the per-frame blend index.
mm = mc.op('mask_merge')
if mm is None:
    mm = mc.create(crossTOP, 'mask_merge')
mm.nodeX, mm.nodeY = -250, 100
mm.inputConnectors[0].connect(sw)
mm.inputConnectors[1].connect(dt)
# Crossfade par bound to Maskdepthmix (0 = sw, 1 = dt). Convention check:
# crossTOP `cross` par 0..1 (0=input0, 1=input1) — matches.
mm.par.cross.expr = "parent().par.Maskdepthmix"


# -------------------------------------------------------------------- out_mask
om = mc.op('out_mask')
if om is None:
    om = mc.create(outTOP, 'out_mask')
om.nodeX, om.nodeY = -50, 100
if hasattr(om.par, 'connectorder'):
    om.par.connectorder = 0
om.inputConnectors[0].connect(mm)


# -------------------------------------------------------------------- cycle Script CHOP + cb
def _ensure_text_dat(name, file_rel, x, y):
    d = mc.op(name)
    if d is None:
        d = mc.create(textDAT, name)
    d.nodeX, d.nodeY = x, y
    d.par.file = file_rel
    d.par.syncfile = True
    return d

cycle_cb = _ensure_text_dat('cycle_cb', 'mask_cycle_chop.py', -450, -200)
cycle_cb.par.loadonstartpulse.pulse()

cycle = mc.op('cycle')
if cycle is None:
    cycle = mc.create(scriptCHOP, 'cycle')
cycle.nodeX, cycle.nodeY = -300, -200
cycle.par.callbacks = cycle_cb.path


# -------------------------------------------------------------------- standby Script CHOP + cb
standby_cb = _ensure_text_dat('standby_cb', 'mask_standby.py', -450, -300)
standby_cb.par.loadonstartpulse.pulse()

standby = mc.op('standby')
if standby is None:
    standby = mc.create(scriptCHOP, 'standby')
standby.nodeX, standby.nodeY = -300, -300
standby.par.callbacks = standby_cb.path
# Standby reads `in_pose` via op('in_pose') — no input wire needed; but reading
# `in_pose`'s channels gives it a per-frame cook dependency. Wire it anyway so
# the dependency is structural and visible in the network.
standby.inputConnectors[0].connect(in_pose)


# -------------------------------------------------------------------- Module DAT references
# Make mask_cycle module importable as `mod.mask_cycle` from the callbacks.
# Convention in this project: a Text DAT inside the COMP with par.file pointing
# at the .py file gets auto-imported under that name via TD's `mod` shortcut.
mod_dat = _ensure_text_dat('mask_cycle', 'mask_cycle.py', -600, -250)
mod_dat.par.loadonstartpulse.pulse()


# -------------------------------------------------------------------- switch_mask blend index expression
# switch_mask's `index` reads `cycle`'s `blend` channel.
try:
    sw.par.index.expr = "op('cycle')['blend'][0]"
except Exception as e:
    print(f'  switch_mask index expr fail: {e}')


# -------------------------------------------------------------------- state assembly → out_state
# 4-channel CHOP (amt, trans, hueoffset, burstcolor). Build via:
#   - standby['amt']     (already produces `amt`)
#   - cycle['morph']     (rename to `trans` via Rename CHOP)
#   - cycle['hueoffset'] (passthrough)
#   - parent par Maskburstcolor + cycle['morph'] = burstcolor (envelope ×
#     intensity); for simplicity we emit raw Maskburstcolor and let downstream
#     multiply by `trans`.
# Pattern: a Select CHOP for `morph`+`hueoffset`, a Rename CHOP for morph→trans,
# then Merge CHOPs together with standby + a Constant CHOP for burstcolor.

# select trans+hueoff from cycle
sel = mc.op('sel_cycle')
if sel is None: sel = mc.create(selectCHOP, 'sel_cycle')
sel.nodeX, sel.nodeY = -150, -200
sel.par.chop = cycle.path
sel.par.channames = 'morph hueoffset'

# rename morph → trans
ren = mc.op('ren_cycle')
if ren is None: ren = mc.create(renameCHOP, 'ren_cycle')
ren.nodeX, ren.nodeY = 0, -200
ren.inputConnectors[0].connect(sel)
ren.par.renamefrom = 'morph'
ren.par.renameto = 'trans'

# constant burstcolor (par-bound)
burst = mc.op('const_burstcolor')
if burst is None: burst = mc.create(constantCHOP, 'const_burstcolor')
burst.nodeX, burst.nodeY = 0, -350
# Constant CHOP pars: name0 / value0
try:
    burst.par.name0  = 'burstcolor'
    burst.par.value0.expr = "parent().par.Maskburstcolor"
except Exception as e:
    print(f'  burstcolor const fail: {e}')

# merge: standby + renamed cycle + burst
mrg = mc.op('merge_state')
if mrg is None: mrg = mc.create(mergeCHOP, 'merge_state')
mrg.nodeX, mrg.nodeY = 150, -250
mrg.inputConnectors[0].connect(standby)
mrg.inputConnectors[1].connect(ren)
mrg.inputConnectors[2].connect(burst)
# Duplicate-channel handling: error (we expect no overlap; standby→amt,
# ren→trans+hueoffset, burst→burstcolor → all unique).
try:
    mrg.par.duplicate = 'error'
except Exception: pass

# out_state
os_out = mc.op('out_state')
if os_out is None: os_out = mc.create(outCHOP, 'out_state')
os_out.nodeX, os_out.nodeY = 320, -250
if hasattr(os_out.par, 'connectorder'):
    os_out.par.connectorder = 0
os_out.inputConnectors[0].connect(mrg)

print('\nmask_controller built. children:', len(list(mc.children)))
for c in mc.children:
    print(f'  {c.name:18s} [{c.type}]')
