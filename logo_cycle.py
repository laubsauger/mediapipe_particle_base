# logo_cycle.py
# =============
# Pure-Python logic for cycling between the two logos wired into `switch_logo`,
# with a "push-away" shockwave at each swap. No TD imports; self-testable:
# `python3 logo_cycle.py`.
#
# Drives a Script CHOP (`logo_cycle`) that outputs two channels:
#   index : 0 / 1   → switch_logo's input index (which logo is showing)
#   trans : 0..1    → transition envelope (a smooth 0→1→0 pulse during a swap)
#
# During a swap the envelope rises; bounds_reflect uses it to briefly REPEL the
# soup from the logo (attract → repel as trans→1) and free the trap, so the
# particles blow outward, then reform onto the NEW logo as trans falls. The
# index flips at the envelope PEAK (trans≈1, particles most scattered) so the
# swap is hidden inside the shockwave.

import math


def fresh_state():
    return {
        't_last':    0.0,   # wall time the last cycle completed
        'in_trans':  False, # currently mid-swap?
        't_start':   0.0,   # wall time the current transition started
        'target':    0,     # which logo we're settled on (0/1)
        'primed':    False, # t_last seeded to first real clock?
        # Persistent hue accumulator: each swap adds `hue_step` (ramps smoothly
        # IN SYNC with the field morph), then HOLDS at the new value. Soupevolve
        # continues drifting from that new baseline. So a swap shifts colour
        # permanently — no bounce-back to the original.
        'hue_accum': 0.0,
        'hue_start': 0.0,   # accum value at the start of the current transition
    }


def _smoothstep(a, b, x):
    if b <= a:
        return 0.0 if x < a else 1.0
    t = max(0.0, min(1.0, (x - a) / (b - a)))
    return t * t * (3.0 - 2.0 * t)


def step(state, now, cycle_time, switch_dur, enabled=True, hue_step=2.4):
    """Advance the cycler. Returns (blend, morph, hueoffset, state).

    POSITIONAL MORPH (not an alpha blend of the render): `blend` (0..1) is a
    fractional Switch index — with the Switch TOP's Blend ON it cross-dissolves
    the two logo IMAGES, so the attractor GRADIENT FIELD smoothly morphs from the
    old shape to the new one. Attract stays ON throughout, so particles settled
    on the old shape MIGRATE — they lerp positions to their new homes as the
    field morphs. `morph` (0..1, Hann bump, peak mid-transition) releases the
    trap so they flow freely, and drives the visuals (colour burst + glow).

    `blend` holds at 0 or 1 between swaps (cycle_time), ramps (smoothstep over
    switch_dur) to the other end during a swap, then holds there. Pure.
    """
    s = dict(state)
    if not s.get('primed'):
        s['t_last'] = now
        s['primed'] = True
        s.setdefault('target', s.get('index', 0))
        s.setdefault('hue_accum', 0.0)
        s.setdefault('hue_start', 0.0)

    switch_dur = max(1e-3, switch_dur)
    target = s.get('target', 0)
    accum = s.get('hue_accum', 0.0)

    if not enabled:
        s['in_trans'] = False
        return float(target), 0.0, accum, s

    if not s['in_trans']:
        if (now - s['t_last']) >= max(0.0, cycle_time):
            s['in_trans'] = True
            s['t_start'] = now
            s['hue_start'] = accum                 # remember where the hue was
        return float(target), 0.0, accum, s

    p = (now - s['t_start']) / switch_dur          # 0..1 across the swap
    if p >= 1.0:
        s['target']    = 1 - target                # now settled on the other logo
        s['hue_accum'] = s['hue_start'] + hue_step # commit the new hue baseline
        s['in_trans']  = False
        s['t_last']    = now
        return float(s['target']), 0.0, s['hue_accum'], s

    e = _smoothstep(0.0, 1.0, p)                   # eased ramp old→new
    dest = 1 - target
    blend = target + (dest - target) * e           # 0→1 or 1→0
    sp = math.sin(p * math.pi)
    morph = sp * sp                                # peak mid (trap release + FX)
    hueoff = s['hue_start'] + e * hue_step         # hue ramps IN SYNC with morph
    return blend, morph, hueoff, s


if __name__ == '__main__':
    st = fresh_state()
    dt = 1.0 / 60.0
    t = 1000.0
    bl, mo, hu, st = step(st, t, cycle_time=2.0, switch_dur=1.0, hue_step=2.4)
    assert bl == 0.0 and mo == 0.0 and hu == 0.0, (bl, mo, hu)

    for _ in range(int(1.9 / dt)):
        t += dt
        bl, mo, hu, st = step(st, t, 2.0, 1.0, hue_step=2.4)
    assert not st['in_trans'] and bl == 0.0 and hu == 0.0, st

    # First swap: blend 0→1, morph bumps, hue ramps 0 → 2.4 IN SYNC.
    peak_morph = 0.0; blends = []; hues = []
    for i in range(int(1.2 / dt)):
        t += dt
        bl, mo, hu, st = step(st, t, 2.0, 1.0, hue_step=2.4)
        peak_morph = max(peak_morph, mo)
        blends.append(bl); hues.append(hu)
    assert peak_morph > 0.95
    assert blends[-1] == 1.0 and st['target'] == 1
    assert any(0.3 < b < 0.7 for b in blends), "blend passes through the middle"
    assert abs(hues[-1] - 2.4) < 1e-6, ("hue lands at +hue_step and STAYS", hues[-1])
    # mid-transition hue is partway between 0 and 2.4 (synced with blend ramp)
    assert any(0.5 < h < 2.0 for h in hues), "hue ramps smoothly"

    # Hold between swaps: hue stays at 2.4 (no bounce-back).
    held = []
    for _ in range(int(1.5 / dt)):
        t += dt
        bl, mo, hu, st = step(st, t, 2.0, 1.0, hue_step=2.4)
        held.append(hu)
    assert all(abs(h - 2.4) < 1e-6 for h in held), "hue HOLDS at new baseline (not temporary)"

    # Second swap: hue accumulates 2.4 → 4.8 (run past the full ramp).
    for i in range(int(1.7 / dt)):
        t += dt
        bl, mo, hu, st = step(st, t, 2.0, 1.0, hue_step=2.4)
    assert abs(hu - 4.8) < 1e-5, ("each swap adds hue_step (no bounce)", hu)
    assert st['target'] == 0 and not st['in_trans']

    st2 = fresh_state()
    bl2, mo2, hu2, st2 = step(st2, 5000.0, 2.0, 1.0, enabled=False)
    assert mo2 == 0.0 and hu2 == 0.0
    print("OK — logo_cycle: blend cross-dissolves (positional morph); hue ramps "
          "in sync and STAYS on new baseline; each swap accumulates hue_step.")
