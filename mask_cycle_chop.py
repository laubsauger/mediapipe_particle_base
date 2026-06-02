# mask_cycle_chop.py
# ==================
# Script CHOP callback that wraps `mask_cycle.step()` per cook. Lives inside
# /project1/mask_controller as the callback DAT for the `cycle` Script CHOP.
#
# Reads parent pars (on mask_controller):
#   Maskcycle      (toggle)
#   Maskcycletime  (float, s)
#   Maskswitchdur  (float, s)
#   Maskhuestep    (float, rad per swap)
#
# Outputs three channels: `blend`, `morph`, `hueoffset`.
#   blend     → drives switch_mask Switch TOP index (Blend=ON, cross-dissolves)
#   morph     → goes into out_state as `trans` (shader uMasktrans / uMaskmorph)
#   hueoffset → goes into out_state as `hueoffset` (shader uMaskhueoffset)
#
# State is stored on the COMP via parent().store / parent().fetch so it survives
# cook-to-cook. Wall clock comes from absTime.seconds (decoupled from any
# specific audio clock — swap easily if needed).

STORAGE_KEY = 'mask_cycle_state'


def onCook(scriptOp):
    scriptOp.clear()
    if scriptOp.isTimeSlice:
        scriptOp.isTimeSlice = False

    comp = parent()
    par = comp.par

    enabled    = bool(par.Maskcycle.eval())     if hasattr(par, 'Maskcycle') else True
    cycle_time = float(par.Maskcycletime.eval())if hasattr(par, 'Maskcycletime') else 12.0
    switch_dur = float(par.Maskswitchdur.eval())if hasattr(par, 'Maskswitchdur') else 1.5
    hue_step   = float(par.Maskhuestep.eval())  if hasattr(par, 'Maskhuestep')   else 2.4

    cycle = mod.mask_cycle
    state = comp.fetch(STORAGE_KEY, None)
    if not isinstance(state, dict):
        state = cycle.fresh_state()
    blend, morph, hueoff, state = cycle.step(
        state, absTime.seconds, cycle_time, switch_dur,
        enabled=enabled, hue_step=hue_step,
    )
    comp.store(STORAGE_KEY, state)

    scriptOp.numSamples = 1
    scriptOp.rate = me.time.rate
    scriptOp.appendChan('blend')[0]     = blend
    scriptOp.appendChan('morph')[0]     = morph
    scriptOp.appendChan('hueoffset')[0] = hueoff
    # swap STYLE for the active transition (0 explode / 1 dissolve), randomised
    # per swap → bounds_reflect picks explosion vs gentle gather.
    scriptOp.appendChan('variant')[0]   = float(state.get('variant', 0.0))
    return
