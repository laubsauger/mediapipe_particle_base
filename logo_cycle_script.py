# logo_cycle_script.py
# =====================
# Script CHOP callback wrapping the pure `logo_cycle` logic. Outputs:
#   index : 0/1   → drives switch_logo's input index (which logo shows)
#   trans : 0..1  → swap shockwave envelope (bounds_reflect repels soup on it)
#
# Synced to the Callbacks DAT of a Script CHOP `logo_cycle` inside
# velocity_controller. Reads lag1 to register a per-frame cook dependency
# (the no-input Script-CHOP gotcha).

import sys

try:
    if hasattr(mod, 'logo_cycle'):
        lc = mod.logo_cycle
    else:
        if project.folder not in sys.path:
            sys.path.append(project.folder)
        import logo_cycle as lc
except Exception as e:
    lc = None
    debug('logo_cycle_script: import failed: %s' % e)


def _par(name, default):
    p = getattr(parent().par, name, None)
    if p is None:
        return default
    try:
        return p.eval()
    except Exception:
        return default


def onCook(scriptOp):
    scriptOp.clear()
    if scriptOp.isTimeSlice:
        scriptOp.isTimeSlice = False

    # per-frame cook dependency
    try:
        dep = op('lag1')
        if dep is not None and dep.numChans:
            _ = float(dep[0][0])
    except Exception:
        pass

    scriptOp.numSamples = 1
    cb = scriptOp.appendChan('blend')   # fractional switch index (cross-dissolve)
    cm = scriptOp.appendChan('morph')   # 0..1 mid-transition (trap release + FX)

    if lc is None:
        cb[0] = 0.0
        cm[0] = 0.0
        return

    st = parent().fetch('logo_cycle_state', None)
    if not isinstance(st, dict):
        st = lc.fresh_state()

    enabled = bool(_par('Logocycle', True))
    cycle_time = float(_par('Logocycletime', 12.0))
    switch_dur = float(_par('Logoswitchdur', 1.5))
    now = float(absTime.seconds)

    blend, morph, st = lc.step(st, now, cycle_time, switch_dur, enabled)
    parent().store('logo_cycle_state', st)

    cb[0] = float(blend)
    cm[0] = float(morph)
    return
