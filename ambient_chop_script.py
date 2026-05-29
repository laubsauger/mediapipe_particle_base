# ambient_chop_script.py
# ======================
# Script CHOP callback. Emits a "constant particle soup": a population of
# emitter points scattered through the whole bounds volume that births
# particles every cook regardless of performer motion. Merged alongside the
# movement-driven `emitters_pop` (via `merge_emitters` Merge POP) into the
# same `particle1` Particle POP, so the soup is advected by the exact same
# force chain (velocity field + curl noise + bounds_reflect) — i.e. it gets
# *displaced* whenever a limb sweeps through it, then drifts on curl noise
# when things are still.
#
# Synced to the Callbacks DAT `ambient_chop_script_cb` of a Script CHOP
# named `ambient_chop` inside velocity_controller. No inputs.
#
# Output channels (identical schema to emitters_chop_script so the same
# CHOP-to-POP attribute rows work):
#   P0,P1,P2  → point position (random within the bounds box each cook)
#   v0,v1,v2  → tiny initial velocity (~0; the field/curl own the motion)
#   w         → birth weight: 1 on the points chosen to spawn this cook, else 0
#   id        → sentinel landmark index 5 ("soup"); color_attr tints Lid>=5
#               with the neutral soup base instead of a per-limb palette color.
#
# Birth-rate control:
#   Particle POP births int(w) particles per input point per cook, so a sub-1
#   per-point rate rounds to zero. To get a controllable soup rate independent
#   of how many scatter points we keep, we mark exactly `k` random points with
#   w=1 each cook, where k comes from a fractional accumulator:
#       births_this_cook = Ambientrate (pts/s) * dt
#   carrying the fractional remainder across cooks. Positions are re-randomised
#   every cook so the soup spawns uniformly through the volume over time.

SOUP_ID = 100  # sentinel Lid for ambient particles; sits above all multi-person
               # movement Lids (max = MAX_PERSONS*5-1 = 19) so they never collide.


def _par(name, default):
    p = getattr(parent().par, name, None)
    if p is None:
        return default
    try:
        return p.eval()
    except Exception:
        return default


def scatter_points(n, box, rng):
    """Return n (x,y,z) tuples uniformly inside the axis-aligned box.
    box = (xmin, ymin, zmin, xmax, ymax, zmax). Pure — unit-testable."""
    xmin, ymin, zmin, xmax, ymax, zmax = box
    pts = []
    for _ in range(n):
        pts.append((
            rng.uniform(xmin, xmax),
            rng.uniform(ymin, ymax),
            rng.uniform(zmin, zmax),
        ))
    return pts


def births_this_cook(rate, dt, accum):
    """Fractional-accumulator birth count. Returns (k, new_accum).
    Carries the sub-1 remainder so the long-run average is rate*dt."""
    accum += max(0.0, rate) * max(0.0, dt)
    k = int(accum)
    accum -= k
    return k, accum


def clump_weight(x, y, scale, t):
    """Smooth, slowly-drifting 0..1 'clump' field from summed sines (cheap,
    no numpy). Births are biased toward high values → the soup clusters into
    soft clumps instead of spreading evenly. `scale` = clump frequency, `t` =
    drift phase. Pure — unit-testable."""
    import math as _m
    v = (_m.sin((x * scale + t) * 3.1) * _m.cos((y * scale - t * 0.7) * 3.7)
         + 0.5 * _m.sin((x * scale * 2.3 - t * 1.3) * 2.1 + y * scale * 4.9))
    return max(0.0, min(1.0, 0.5 + 0.32 * v))


import random as _rnd
_RNG = _rnd.Random()


def onCook(scriptOp):
    scriptOp.clear()
    if scriptOp.isTimeSlice:
        scriptOp.isTimeSlice = False

    # Force a per-cook dependency. A Script CHOP with no time-varying input
    # does NOT recook every frame — TD only cooks it once and serves the
    # cached output thereafter (the classic Script-CHOP gotcha). That freezes
    # the scatter into fixed emission points, so the soup reads as a couple
    # dozen stationary "squirt guns" instead of a re-scattering cloud. Touch
    # an always-cooking sibling (lag1, driven by the time-sliced pose input)
    # so TD marks us dirty every frame and re-scatters. Mirrors how
    # emitters_chop_script stays live by reading lag1.
    try:
        _dep = op('lag1')
        if _dep is not None and _dep.numChans:
            _ = float(_dep[0][0])  # read a sample → registers the dependency
    except Exception:
        pass

    n = max(0, int(_par('Ambientpoints', 200)))
    scriptOp.numSamples = n
    scriptOp.rate = me.time.rate
    chan_names = ['P0', 'P1', 'P2', 'v0', 'v1', 'v2', 'w', 'id']
    chans = {nm: scriptOp.appendChan(nm) for nm in chan_names}
    if n == 0:
        return

    box = (
        float(_par('Boundsminx', 0.0)), float(_par('Boundsminy', 0.0)),
        float(_par('Boundsminz', -0.15)),
        float(_par('Boundsmaxx', 16 / 9)), float(_par('Boundsmaxy', 1.0)),
        float(_par('Boundsmaxz', 0.15)),
    )

    _RNG.seed(int(absTime.frame))
    pts = scatter_points(n, box, _RNG)

    rate = float(_par('Ambientrate', 2000.0))
    dt = 1.0 / max(1e-6, me.time.rate)
    accum = float(parent().fetch('Ambientaccum', 0.0))
    k, accum = births_this_cook(rate, dt, accum)
    parent().store('Ambientaccum', accum)

    # Choose k point indices to fire this cook, biased toward clump-high
    # regions so the soup clusters (instead of uniform density). Expected total
    # stays ≈ k via p_i = k·w_i / Σw. Soupclumpamt 0 = even, 1 = strong clumps.
    k = min(k, n)
    clump_scale = float(_par('Soupclumpscale', 2.0))
    clump_amt = max(0.0, min(1.0, float(_par('Soupclumpamt', 0.6))))
    tt = absTime.seconds * 0.05
    ws = []
    for (x, y, z) in pts:
        c = clump_weight(x, y, clump_scale, tt)
        ws.append((1.0 - clump_amt) + clump_amt * (c * 2.0))
    sw = sum(ws) or 1.0
    fire = set()
    if k > 0:
        for i in range(n):
            if _RNG.random() < (k * ws[i] / sw):
                fire.add(i)

    for i, (x, y, z) in enumerate(pts):
        chans['P0'][i] = x
        chans['P1'][i] = y
        chans['P2'][i] = z
        # Near-zero launch: the field + curl own soup motion.
        chans['v0'][i] = 0.0
        chans['v1'][i] = 0.0
        chans['v2'][i] = 0.0
        chans['w'][i] = 1.0 if i in fire else 0.0
        chans['id'][i] = float(SOUP_ID)
    return


if __name__ == '__main__':
    import random
    rng = random.Random(0)
    box = (0.0, 0.0, -0.15, 16 / 9, 1.0, 0.15)
    pts = scatter_points(500, box, rng)
    assert len(pts) == 500
    for (x, y, z) in pts:
        assert box[0] <= x <= box[3], x
        assert box[1] <= y <= box[4], y
        assert box[2] <= z <= box[5], z
    # Accumulator: long-run average births ≈ rate*dt.
    acc, total, N = 0.0, 0, 100000
    rate, dt = 2000.0, 1 / 60.0
    for _ in range(N):
        k, acc = births_this_cook(rate, dt, acc)
        total += k
    avg = total / N
    expected = rate * dt
    assert abs(avg - expected) < 0.05, (avg, expected)
    assert 0.0 <= acc < 1.0, acc
    print("OK — ambient_chop_script: scatter in-bounds, "
          "birth accumulator avg=%.3f (expected %.3f)" % (avg, expected))
