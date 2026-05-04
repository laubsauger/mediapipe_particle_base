# emitters_chop_script.py
# =======================
# Script CHOP callback. Reshapes the sensing-side Lag CHOP output into a
# CHOP with N SAMPLES (one per landmark) and channels named so that the
# downstream CHOP-to-POP converter reconstructs vec3 attributes.
#
# Paste into a Text DAT called `emitters_chop_script` inside velocity_controller.
# Attach as the Callbacks DAT of a Script CHOP called `emitters_chop`. Then
# follow the Script CHOP with a stock `CHOP to POP` op named `emitters_pop` —
# that's what the Source POP takes as input.
#
# TD channel-name note: CHOP channel names can't contain `[` or `]` —
# TD sanitises those to underscores on the way in, so `appendChan('P[0]')`
# gets stored as `P_0_` and downstream ops can't match a sensible pattern.
# We use bare `P0 P1 P2 / v0 v1 v2` instead and wire up explicit attribute
# rows on the CHOP-to-POP (see velocity_controller_setup.md).
#
# Emission shape — 2D velocity-aligned scatter
# --------------------------------------------
# For each landmark we emit N = `Spawncount` sub-emitter points scattered
# within a 2D region aligned with the limb's xy velocity. Two extents:
#
#   half_along = max(Spawnspreadmin, Spawnspread * speed_factor)
#       — grows with speed, producing a "streak" elongated along velocity
#   half_perp  = max(Spawnspreadmin, Spawnspread * Spawnperpratio * speed_factor)
#       — stays smaller (width of the streak) so fast motion produces
#         an elongated ellipse-ish region, not a square
#
# At rest (speed=0): both extents collapse to Spawnspreadmin → small
#   lump shape (circular-ish, matches the flow-field shader's rest shape).
# At full speed: ellipse with aspect ratio ~ 1:Spawnperpratio, aligned
#   with motion direction → streaky, matches the shader's velocity-
#   stretched kernel.
#
# Sub-emitter positions are pseudo-random within that rectangle, using
# a fixed seed so positions are STABLE per-sub-emitter-index across
# cooks (no chaotic jitter — same k always lands at the same relative
# position within the region, which translates as the region rotates
# with the limb direction).
#
# Output:
#   numSamples = N_landmarks × Spawncount
#   channels:
#     P0, P1, P2    → vec3 `P` attribute on the POP (position, 3D)
#     v0, v1, v2    → vec3 `v` attribute on the POP (initial velocity, 3D)
#                     scaled by Spawnvelscale so particles don't immediately
#                     fly off-screen — the velocity field does continued work
#     w             → float `w` attribute (spawn weight, same value on each
#                     sub-emitter of a limb — NOT divided across them,
#                     because Particle POP's int(w) would round fractional
#                     weights to zero. Consequence: total particles/sec per
#                     limb scales linearly with Spawncount, which is what
#                     you usually want for a wavefront.)
#     id            → int `id` attribute (landmark index; same for all N
#                     sub-emitters of a given limb, for per-limb color)

def _landmark_list():
    par = parent().par.Landmarks.eval() if hasattr(parent().par, 'Landmarks') else ''
    items = [s.strip() for s in str(par).replace(',', ' ').split() if s.strip()]
    if items:
        return items
    try:
        return list(mod.velocity_logic.LANDMARKS)
    except Exception:
        return ['left_wrist', 'right_wrist',
                'left_ankle', 'right_ankle', 'nose']


def _find_chan(chop, name):
    if chop is None:
        return None
    return chop[name]


def _read(chop, name, default=0.0):
    c = _find_chan(chop, name)
    if c is None:
        return default
    return float(c[0])


def _par(name, default):
    """Read a parent par with a safe fallback if it doesn't exist yet.
    Lets the script run even before install_velocity_params has added
    the wavefront pars on an existing COMP."""
    p = getattr(parent().par, name, None)
    if p is None:
        return default
    try:
        return p.eval()
    except Exception:
        return default


# Pseudo-random scatter positions for sub-emitters inside a unit square
# [-0.5, +0.5]^2. Fixed seed so the same sub-emitter index always lands
# at the same relative position — no per-frame jitter, just a stable
# organic-looking cloud that rotates/stretches with the limb velocity.
# 128 positions is plenty for the expected Spawncount range (up to ~40);
# higher counts simply reuse the same positions in index order.
import random as _rnd
_SCATTER_RNG = _rnd.Random(424242)
_SCATTER = [(_SCATTER_RNG.uniform(-0.5, 0.5),
             _SCATTER_RNG.uniform(-0.5, 0.5))
            for _ in range(128)]
del _rnd


def onCook(scriptOp):
    scriptOp.clear()
    # Time Slice off — we emit the current-frame landmark snapshot. Any
    # temporal smoothing is already done by lag1 upstream.
    if scriptOp.isTimeSlice:
        scriptOp.isTimeSlice = False

    src = op('lag1')
    lms = _landmark_list()
    n_lm = len(lms)
    if src is None or n_lm == 0:
        # Emit an empty CHOP so the downstream CHOP-to-POP doesn't error.
        scriptOp.numSamples = 0
        return

    burst_gain        = float(_par('Burstgain',         1.0))
    spawn_count       = max(1, int(_par('Spawncount',    12)))
    spawn_spread_max  = float(_par('Spawnspread',        0.08))
    spawn_spread_ref  = max(1e-4, float(_par('Spawnspreadref',  0.8)))
    # Minimum extent at rest — gives a small "lump" shape even when the
    # limb is stationary (matches the flow-field shader's gaussian-at-rest).
    spawn_spread_min  = float(_par('Spawnspreadmin',     0.02))
    # Perpendicular-axis ratio — how wide the emission region is relative
    # to the along-velocity axis at speed. 0 = pure along-velocity line,
    # 1 = square region, default 0.3 = visibly elongated streak.
    spawn_perp_ratio  = float(_par('Spawnperpratio',     0.3))
    spawn_vel_scale   = float(_par('Spawnvelscale',      0.15))
    # Zforceweight applies to BOTH the flowfield texture's vz (damps z
    # force) AND the newborn StartPartvel.z (damps z launch velocity).
    # Single knob so pure horizontal motion never produces z-axis
    # particle motion regardless of MediaPipe's depth-estimate noise.
    z_force_weight    = float(_par('Zforceweight',       0.05))
    # Angular fan: edge sub-emitters (perpendicular side of the region)
    # get an outward kick on their StartPartvel. 0 = parallel, higher =
    # fanned cone. Scaled by limb speed so at rest there's no fan.
    spawn_vel_fan     = float(_par('Spawnvelfan',        0.5))

    total = n_lm * spawn_count

    # IMPORTANT: set numSamples BEFORE appending channels. Channels inherit
    # their sample count from the op when created; appending after changing
    # numSamples leaves the new channels short.
    scriptOp.numSamples = total
    scriptOp.rate = me.time.rate

    chan_names = ['P0', 'P1', 'P2',
                  'v0', 'v1', 'v2',
                  'w', 'id']
    chans = {name: scriptOp.appendChan(name) for name in chan_names}

    import math

    n_scatter = len(_SCATTER)

    # Centering the scatter: uniform random with a fixed seed has nonzero
    # empirical mean for small Spawncount (e.g., Spawncount=12 with seed
    # 424242 gives mean ≈ (+0.066, -0.028) = ~7% of the scatter range).
    # That creates a consistent directional bias on both position and
    # fan velocity — every spawn gets offset the same way, every cook.
    # Subtract the mean of the actually-used subset so the distribution
    # is guaranteed zero-mean regardless of Spawncount.
    _used = _SCATTER[:spawn_count] if spawn_count <= n_scatter else _SCATTER
    _mean_along = sum(p[0] for p in _used) / len(_used)
    _mean_perp  = sum(p[1] for p in _used) / len(_used)

    idx = 0
    # Bounds for spawn-position clamp. Particles must spawn INSIDE the
    # reflective box, otherwise PartVel-based motion immediately bounces
    # them around the spawn point and they look stuck. Read via _par with
    # safe defaults so the script keeps running on COMPs without these
    # custom pars yet.
    bz_min = float(_par('Boundsminz', -0.5))
    bz_max = float(_par('Boundsmaxz',  0.5))
    bx_min = float(_par('Boundsminx',  0.0))
    bx_max = float(_par('Boundsmaxx',  1.0))
    by_min = float(_par('Boundsminy',  0.0))
    by_max = float(_par('Boundsmaxy',  1.0))
    # Inset margin so spawns aren't right on the wall
    margin = 0.02

    def _clamp(v, lo, hi):
        return max(lo, min(hi, v))

    for lm_i, lm in enumerate(lms):
        x  = _read(src, f'{lm}:x')
        y  = _read(src, f'{lm}:y')
        z  = _read(src, f'{lm}:z')
        vx = _read(src, f'{lm}:vx')
        vy = _read(src, f'{lm}:vy')
        vz = _read(src, f'{lm}:vz')
        em = _read(src, f'{lm}:emit')
        bu = _read(src, f'{lm}:burst')
        vi = _read(src, f'{lm}:visible')

        # Per-sub-emitter weight — same value for all sub-emitters of a
        # given limb (see header comment for why we don't divide).
        w_per = (em + burst_gain * bu) * vi

        # Velocity-aligned basis: `along` points in the direction of
        # limb motion (fallback to world x if still); `perp` is a 90°
        # rotation of `along`. Both are unit vectors.
        vmag_xy = math.sqrt(vx * vx + vy * vy)
        if vmag_xy > 1e-4:
            along_x = vx / vmag_xy
            along_y = vy / vmag_xy
            perp_x  = -along_y
            perp_y  =  along_x
        else:
            along_x, along_y = 1.0, 0.0
            perp_x,  perp_y  = 0.0, 1.0

        # Spread extents: along grows with speed, perp stays narrower.
        # At rest both collapse to spawn_spread_min (a small lump).
        speed_factor = min(vmag_xy / spawn_spread_ref, 1.0)
        half_along = max(spawn_spread_min,
                         spawn_spread_max * speed_factor)
        half_perp  = max(spawn_spread_min,
                         spawn_spread_max * spawn_perp_ratio * speed_factor)

        # Base velocity (limb direction, scaled down by Spawnvelscale).
        # z gets the additional Zforceweight multiplier because MediaPipe's
        # monocular depth estimate is noisy even during pure xy motion —
        # without this, newborn particles would inherit spurious z velocity
        # and drift forward/back on purely horizontal gestures.
        base_svx = vx * spawn_vel_scale
        base_svy = vy * spawn_vel_scale
        svz      = vz * spawn_vel_scale * z_force_weight

        for k in range(spawn_count):
            # Stable pseudo-random scatter position within a unit square
            # (both in [-0.5, 0.5]). Same k always maps to the same
            # (rel_along, rel_perp) — no cook-to-cook jitter.
            # Subtract empirical mean so the used subset has zero mean.
            raw_along, raw_perp = _SCATTER[k % n_scatter]
            rel_along = raw_along - _mean_along
            rel_perp  = raw_perp  - _mean_perp

            # Scale to actual extents along each local axis.
            local_along = rel_along * 2.0 * half_along
            local_perp  = rel_perp  * 2.0 * half_perp

            # Rotate local offset into world coords.
            off_x = along_x * local_along + perp_x * local_perp
            off_y = along_y * local_along + perp_y * local_perp

            # Velocity fan: edge sub-emitters (large |rel_perp|) get a
            # perpendicular kick. Center (rel_perp=0) stays parallel.
            # Scaled by limb speed, so at rest there's no fan.
            fan_vx = perp_x * rel_perp * spawn_vel_fan * vmag_xy * spawn_vel_scale
            fan_vy = perp_y * rel_perp * spawn_vel_fan * vmag_xy * spawn_vel_scale

            chans['P0'][idx] = _clamp(x + off_x, bx_min + margin, bx_max - margin)
            chans['P1'][idx] = _clamp(y + off_y, by_min + margin, by_max - margin)
            chans['P2'][idx] = _clamp(z,         bz_min + margin, bz_max - margin)
            chans['v0'][idx] = base_svx + fan_vx
            chans['v1'][idx] = base_svy + fan_vy
            chans['v2'][idx] = svz
            chans['w'][idx]  = w_per
            # id stays the landmark index so per-limb colouring still works
            # across all sub-emitters of the same limb.
            chans['id'][idx] = float(lm_i)
            idx += 1

    return
