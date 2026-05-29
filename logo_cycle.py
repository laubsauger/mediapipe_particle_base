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


def step(state, now, cycle_time, switch_dur, enabled=True):
    """Advance the cycler. Returns (index, trans, state).

    now         : wall seconds (monotonic).
    cycle_time  : seconds a logo stays before the next swap starts.
    switch_dur  : seconds the swap shockwave lasts (envelope width).
    enabled     : False → no auto-cycle (index held, trans decays to 0).
    Pure — unit-testable.
    """
    s = dict(state)
    # Prime the clock on first call so a huge absTime doesn't trigger an
    # instant swap (mirrors the beatsaber wall-clock priming gotcha).
    if not s.get('primed'):
        s['t_last'] = now
        s['primed'] = True

    switch_dur = max(1e-3, switch_dur)

    if not enabled:
        s['in_trans'] = False
        return s['index'], 0.0, s

    if not s['in_trans']:
        if (now - s['t_last']) >= max(0.0, cycle_time):
            s['in_trans'] = True
            s['t_start'] = now
            s['swapped'] = False
        return s['index'], 0.0, s

    # mid-transition
    p = (now - s['t_start']) / switch_dur          # 0..1 across the swap
    if p >= 1.0:
        s['in_trans'] = False
        s['t_last'] = now
        return s['index'], 0.0, s

    # Hann window (sin²): 0→1→0 with ZERO slope at both ends, so the shockwave
    # eases in and out instead of snapping on/off (plain sin has max slope at
    # the ends → abrupt start/finish).
    sp = math.sin(p * math.pi)
    trans = sp * sp
    if p >= 0.5 and not s['swapped']:              # flip at the peak (hidden)
        s['index'] = 1 - s['index']
        s['swapped'] = True
    return s['index'], trans, s


if __name__ == '__main__':
    st = fresh_state()
    dt = 1.0 / 60.0
    t = 1000.0   # large clock → priming must prevent an instant swap
    # First call primes; no transition yet.
    idx, tr, st = step(st, t, cycle_time=2.0, switch_dur=1.0)
    assert idx == 0 and tr == 0.0 and not st['in_trans'], (idx, tr)

    # Run ~1.9s — still before cycle_time (2s), no transition.
    for _ in range(int(1.9 / dt)):
        t += dt
        idx, tr, st = step(st, t, 2.0, 1.0)
    assert not st['in_trans'] and idx == 0, st

    # Cross 2s → transition starts; collect the envelope + the index flip.
    peak = 0.0
    flipped_at = None
    saw_index1 = False
    for i in range(int(1.2 / dt)):
        t += dt
        idx, tr, st = step(st, t, 2.0, 1.0)
        peak = max(peak, tr)
        if idx == 1 and flipped_at is None:
            flipped_at = tr
        if idx == 1:
            saw_index1 = True
    assert peak > 0.95, ("envelope should peak ~1", peak)
    assert saw_index1, "index should have flipped to 1"
    # flip happens near the peak (trans high when it flips)
    assert flipped_at is not None and flipped_at > 0.8, ("flip near peak", flipped_at)
    # transition ends, envelope back to 0
    assert not st['in_trans'] and abs(tr) < 1e-6, (st, tr)

    # disabled → no transition, trans 0
    st2 = fresh_state()
    _, tr2, st2 = step(st2, 5000.0, 2.0, 1.0, enabled=False)
    assert tr2 == 0.0
    print("OK — logo_cycle: prime guards instant swap, envelope peaks ~1, "
          "index flips at the peak, resets clean.")
