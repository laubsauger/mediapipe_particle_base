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
        't_last':   0.0,    # wall time the last cycle completed
        'in_trans': False,  # currently mid-swap?
        't_start':  0.0,    # wall time the current transition started
        'target':   0,      # which logo we're settled on (0/1)
        'primed':   False,  # t_last seeded to first real clock?
    }


def _smoothstep(a, b, x):
    if b <= a:
        return 0.0 if x < a else 1.0
    t = max(0.0, min(1.0, (x - a) / (b - a)))
    return t * t * (3.0 - 2.0 * t)


def step(state, now, cycle_time, switch_dur, enabled=True):
    """Advance the cycler. Returns (blend, morph, state).

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
        s.setdefault('target', s.get('index', 0))   # which logo we're settled on

    switch_dur = max(1e-3, switch_dur)
    target = s.get('target', 0)

    if not enabled:
        s['in_trans'] = False
        return float(target), 0.0, s

    if not s['in_trans']:
        if (now - s['t_last']) >= max(0.0, cycle_time):
            s['in_trans'] = True
            s['t_start'] = now
        return float(target), 0.0, s

    p = (now - s['t_start']) / switch_dur          # 0..1 across the swap
    if p >= 1.0:
        s['target'] = 1 - target                   # now settled on the other logo
        s['in_trans'] = False
        s['t_last'] = now
        return float(s['target']), 0.0, s

    e = _smoothstep(0.0, 1.0, p)                    # eased ramp old→new
    dest = 1 - target
    blend = target + (dest - target) * e           # 0→1 or 1→0
    sp = math.sin(p * math.pi)
    morph = sp * sp                                # peak mid (trap release + FX)
    return blend, morph, s


if __name__ == '__main__':
    st = fresh_state()
    dt = 1.0 / 60.0
    t = 1000.0   # large clock → priming must prevent an instant swap
    # First call primes; settled on logo 0 (blend 0), no morph.
    bl, mo, st = step(st, t, cycle_time=2.0, switch_dur=1.0)
    assert bl == 0.0 and mo == 0.0 and not st['in_trans'], (bl, mo)

    # Run ~1.9s — still holding on logo 0.
    for _ in range(int(1.9 / dt)):
        t += dt
        bl, mo, st = step(st, t, 2.0, 1.0)
    assert not st['in_trans'] and bl == 0.0, st

    # Cross 2s → blend ramps 0→1 (cross-dissolve), morph bumps, settles at 1.
    peak_morph = 0.0
    blends = []
    for i in range(int(1.2 / dt)):
        t += dt
        bl, mo, st = step(st, t, 2.0, 1.0)
        peak_morph = max(peak_morph, mo)
        blends.append(bl)
    assert peak_morph > 0.95, ("morph should peak ~1", peak_morph)
    # blend is monotonic-ish 0→1 and ends at 1 (settled on the other logo)
    assert blends[-1] == 1.0 and st['target'] == 1, ("settle on logo 1", blends[-1], st)
    assert max(blends) <= 1.0 and min(blends) >= 0.0
    # mid-ramp it actually crossed ~0.5 (real positional dissolve, not a jump)
    assert any(0.3 < b < 0.7 for b in blends), "blend should pass through the middle"
    assert not st['in_trans'] and mo == 0.0

    # next swap ramps back 1→0
    t += 2.0
    for i in range(int(1.2 / dt)):
        t += dt
        bl, mo, st = step(st, t, 2.0, 1.0)
    assert bl == 0.0 and st['target'] == 0, ("settle back on logo 0", bl, st)

    # disabled → held, morph 0
    st2 = fresh_state()
    bl2, mo2, st2 = step(st2, 5000.0, 2.0, 1.0, enabled=False)
    assert mo2 == 0.0
    print("OK — logo_cycle: blend cross-dissolves 0↔1 through the middle "
          "(positional morph), morph bump peaks mid, settles + holds.")
