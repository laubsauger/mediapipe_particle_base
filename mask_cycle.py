# mask_cycle.py
# =============
# Pure-Python logic for cycling between two SOURCE masks (e.g. logo A / logo B,
# or logo / depth-derived silhouette) with a positional cross-dissolve and a
# "push-away" swap shockwave. No TD imports; self-testable:
# `python3 mask_cycle.py`.
#
# Lives in /project1/mask_controller as the cycler that drives `switch_mask`
# (a Switch TOP with Blend=ON) and the state CHOP feeding particle_system's
# `in_mask_state` input.
#
# Outputs per tick — returned by `step(...)`:
#   blend     : 0..1  fractional Switch TOP index (smoothly cross-dissolves
#                     image A → image B). With the Switch's Blend par ON, the
#                     IMAGE itself morphs, so the attractor GRADIENT FIELD
#                     smoothly transitions and particles settled on the old
#                     shape MIGRATE to the new shape's homes.
#   morph     : 0..1  Hann bump, peaks mid-swap. Drives the trap-release
#                     (so particles flow freely during the migration) and the
#                     visual HDR burst.
#   hueoffset : rad   PERSISTENT accumulator. Each completed swap adds
#                     `hue_step` and HOLDS there — no bounce-back.
#
# `blend` holds at 0 or 1 between swaps; ramps (smoothstep over `switch_dur`)
# to the other end during a swap, then settles.

import math


def fresh_state():
    return {
        't_last':    0.0,
        'in_trans':  False,
        't_start':   0.0,
        'target':    0,
        'primed':    False,
        'hue_accum': 0.0,
        'hue_start': 0.0,
    }


def _smoothstep(a, b, x):
    if b <= a:
        return 0.0 if x < a else 1.0
    t = max(0.0, min(1.0, (x - a) / (b - a)))
    return t * t * (3.0 - 2.0 * t)


def step(state, now, cycle_time, switch_dur, enabled=True, hue_step=2.4):
    """Advance the cycler. Returns (blend, morph, hueoffset, state)."""
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
            s['hue_start'] = accum
        return float(target), 0.0, accum, s

    p = (now - s['t_start']) / switch_dur
    if p >= 1.0:
        s['target']    = 1 - target
        s['hue_accum'] = s['hue_start'] + hue_step
        s['in_trans']  = False
        s['t_last']    = now
        return float(s['target']), 0.0, s['hue_accum'], s

    e = _smoothstep(0.0, 1.0, p)
    dest = 1 - target
    blend = target + (dest - target) * e
    sp = math.sin(p * math.pi)
    morph = sp * sp
    hueoff = s['hue_start'] + e * hue_step
    return blend, morph, hueoff, s


if __name__ == '__main__':
    st = fresh_state()
    dt = 1.0 / 60.0
    t = 1000.0
    bl, mo, hu, st = step(st, t, cycle_time=2.0, switch_dur=1.0, hue_step=2.4)
    assert bl == 0.0 and mo == 0.0 and hu == 0.0

    for _ in range(int(1.9 / dt)):
        t += dt
        bl, mo, hu, st = step(st, t, 2.0, 1.0, hue_step=2.4)
    assert not st['in_trans'] and bl == 0.0 and hu == 0.0

    peak_morph = 0.0; blends = []; hues = []
    for i in range(int(1.2 / dt)):
        t += dt
        bl, mo, hu, st = step(st, t, 2.0, 1.0, hue_step=2.4)
        peak_morph = max(peak_morph, mo)
        blends.append(bl); hues.append(hu)
    assert peak_morph > 0.95
    assert blends[-1] == 1.0 and st['target'] == 1
    assert any(0.3 < b < 0.7 for b in blends)
    assert abs(hues[-1] - 2.4) < 1e-6

    held = []
    for _ in range(int(1.5 / dt)):
        t += dt
        bl, mo, hu, st = step(st, t, 2.0, 1.0, hue_step=2.4)
        held.append(hu)
    assert all(abs(h - 2.4) < 1e-6 for h in held)

    for i in range(int(1.7 / dt)):
        t += dt
        bl, mo, hu, st = step(st, t, 2.0, 1.0, hue_step=2.4)
    assert abs(hu - 4.8) < 1e-5
    assert st['target'] == 0 and not st['in_trans']

    st2 = fresh_state()
    bl2, mo2, hu2, st2 = step(st2, 5000.0, 2.0, 1.0, enabled=False)
    assert mo2 == 0.0 and hu2 == 0.0
    print("OK — mask_cycle: positional cross-dissolve + persistent hue accumulator.")
