# mask_state_resolve.py
# =====================
# Script CHOP callback for `mask_state_resolve` inside particle_system.
#
# Purpose: collapse the dual API for mask state into ONE 4-channel CHOP the
# shader uniforms bind to.
#
# Input source priority:
#   1. `in_mask_state` CHOP input — if connected AND any of the four expected
#      channels (amt / trans / hueoffset / burstcolor) are present, use them.
#      Missing channels fall back to the matching parent par.
#   2. Parent pars on the particle_system COMP (Maskamt / Masktrans /
#      Maskhueoffset / Maskburstcolor) — always read as the fallback.
#
# Output (always exactly these 4 channels, in this order):
#   amt        : 0..1 mask-attractor gate
#   trans      : 0..1 swap-shockwave envelope (also drives the morph/trap-release)
#   hueoffset  : radians, PERSISTENT accumulator (no bounce-back across swaps)
#   burstcolor : HDR multiplier during a swap (paired with trans envelope)
#
# Design intent: the rest of the wiring (logo cycler, depth merger, OSC
# remote, audio-reactive amt, etc) lives EXTERNALLY in mask_controller /
# user's own glue. This Script CHOP just normalises whatever the user wires
# in, so the shaders see one consistent contract.

CHANS = ('amt', 'trans', 'hueoffset', 'burstcolor')
PAR_FALLBACK = {
    'amt':        'Maskamt',
    'trans':      'Masktrans',
    'hueoffset':  'Maskhueoffset',
    'burstcolor': 'Maskburstcolor',
}


def _read_chan(src_chop, name, default):
    if src_chop is None:
        return default
    c = src_chop[name]
    if c is None:
        return default
    try:
        v = float(c[0])
    except Exception:
        return default
    # NaN/Inf guard — TD lookups occasionally emit NaN on first cook
    return v if (v == v and v not in (float('inf'), float('-inf'))) else default


def onCook(scriptOp):
    scriptOp.clear()
    scriptOp.numSamples = 1
    scriptOp.rate = me.time.rate

    comp = parent()

    # Source CHOP (input 0). May be None if nothing wired.
    src = scriptOp.inputs[0] if len(scriptOp.inputs) >= 1 else None

    for chan in CHANS:
        par_name = PAR_FALLBACK[chan]
        # Default = parent par (always exists; installer guarantees this)
        try:
            default = float(getattr(comp.par, par_name).eval())
        except Exception:
            default = 0.0
        v = _read_chan(src, chan, default)
        scriptOp.appendChan(chan)[0] = v
    return
