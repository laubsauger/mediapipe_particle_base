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
        'index':    0,      # which logo (0/1)
        'swapped':  False,  # has the index flipped this transition yet?
        'primed':   False,  # t_last seeded to first real clock?
    }


def _smoothstep(a, b, x):
    if b <= a:
        return 0.0 if x < a else 1.0
    t = max(0.0, min(1.0, (x - a) / (b - a)))
    return t * t * (3.0 - 2.0 * t)


def step(state, now, cycle_time, switch_dur, enabled=True):
    """Advance the cycler. Returns (index, attmul, pushmul, trans, state).

    Two clean phases make ONE organic out-then-in motion (not a suck-blast-suck):
      • SHED   (p<0.5): attract OFF, an outward PUSH pulse blows the soup off
                        the old shape (pushmul = sin(2πp): 0→1→0).
      • GATHER (p≥0.5): push OFF, attract fades IN (attmul = smoothstep) so the
                        scattered soup settles onto the NEW shape. Index flips at
                        the midpoint, when the soup is most dispersed (hidden).
      • trans  : a smooth Hann bump (peak at the swap) driving the VISUALS only
                 (logo-glow fade + colour burst + glow-up).

    now/cycle_time/switch_dur as before. enabled False → held, all 0/attract.
    Pure — unit-testable.
    """
    s = dict(state)
    if not s.get('primed'):
        s['t_last'] = now
        s['primed'] = True

    switch_dur = max(1e-3, switch_dur)

    if not enabled:
        s['in_trans'] = False
        return s['index'], 1.0, 0.0, 0.0, s     # full attract, no push

    if not s['in_trans']:
        if (now - s['t_last']) >= max(0.0, cycle_time):
            s['in_trans'] = True
            s['t_start'] = now
            s['swapped'] = False
        return s['index'], 1.0, 0.0, 0.0, s

    p = (now - s['t_start']) / switch_dur          # 0..1 across the swap
    if p >= 1.0:
        s['in_trans'] = False
        s['t_last'] = now
        return s['index'], 1.0, 0.0, 0.0, s

    if p >= 0.5 and not s['swapped']:              # flip at the dispersed midpoint
        s['index'] = 1 - s['index']
        s['swapped'] = True

    if p < 0.5:                                    # SHED: push out, no attract
        attmul = 0.0
        pushmul = math.sin(p * 2.0 * math.pi)      # 0→1→0 over the shed half
        if pushmul < 0.0:
            pushmul = 0.0
    else:                                          # GATHER: attract fades in
        attmul = _smoothstep(0.5, 1.0, p)
        pushmul = 0.0

    sp = math.sin(p * math.pi)
    trans = sp * sp                                # smooth visual bump (peak mid)
    return s['index'], attmul, pushmul, trans, s


if __name__ == '__main__':
    st = fresh_state()
    dt = 1.0 / 60.0
    t = 1000.0   # large clock → priming must prevent an instant swap
    # First call primes; no transition (full attract, no push).
    idx, att, push, tr, st = step(st, t, cycle_time=2.0, switch_dur=1.0)
    assert idx == 0 and att == 1.0 and push == 0.0 and tr == 0.0, (idx, att, push, tr)

    # Run ~1.9s — still before cycle_time, held at full attract.
    for _ in range(int(1.9 / dt)):
        t += dt
        idx, att, push, tr, st = step(st, t, 2.0, 1.0)
    assert not st['in_trans'] and idx == 0 and att == 1.0, st

    # Cross 2s → transition. Track phases: SHED (push pulse, att 0) before the
    # flip; GATHER (att fades in, push 0) after.
    peak_push = 0.0
    peak_trans = 0.0
    flip_idx_seen = False
    att_after_flip = []
    push_after_flip = []
    for i in range(int(1.2 / dt)):
        t += dt
        idx, att, push, tr, st = step(st, t, 2.0, 1.0)
        peak_push = max(peak_push, push)
        peak_trans = max(peak_trans, tr)
        if idx == 1:
            flip_idx_seen = True
            att_after_flip.append(att)
            push_after_flip.append(push)
    assert peak_push > 0.95, ("shed push should peak ~1", peak_push)
    assert peak_trans > 0.95, ("visual bump should peak ~1", peak_trans)
    assert flip_idx_seen, "index should have flipped to 1"
    # after the flip we GATHER: attract climbs to 1, push stays 0
    assert max(att_after_flip) > 0.95, ("attract should fade in", max(att_after_flip))
    assert max(push_after_flip) < 1e-6, ("no push after flip", max(push_after_flip))
    # ends back at full attract, no transition
    assert not st['in_trans'] and att == 1.0 and push == 0.0, (st, att, push)

    # disabled → held, full attract, no push/trans
    st2 = fresh_state()
    _, att2, push2, tr2, st2 = step(st2, 5000.0, 2.0, 1.0, enabled=False)
    assert att2 == 1.0 and push2 == 0.0 and tr2 == 0.0
    print("OK — logo_cycle: prime guards swap; SHED (push pulse, no attract) "
          "then GATHER (attract fades in, no push); flip at dispersed midpoint.")
