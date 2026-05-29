# emitters_chop_script.py
# =======================
# Script CHOP callback. Reshapes the sensing-side Lag CHOP output into a
# CHOP with N SAMPLES (one per landmark) and channels named so that the
# downstream CHOP-to-POP converter reconstructs vec3 attributes.
#
# Synced to the Callbacks DAT `emitters_chop_script_cb` of the Script CHOP
# `emitters_chop_script` inside velocity_controller. Follow that Script CHOP
# with a stock `CHOP to POP` op named `emitters_pop` — its output is the birth
# source plugged into the `particle1` Particle POP. (There is no "Source POP"
# in TD; Particle POP is the hub.)
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
#     id            → int landmark index (same for all N sub-emitters of a
#                     given limb, for per-limb color). The CHOP-to-POP maps
#                     this `id` channel to the POP attribute `Lid` (`id`/`Id`
#                     collide with TD point-identifier names); `color_attr`
#                     reads `Lid`.

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
    import math as _math
    c = _find_chan(chop, name)
    if c is None or len(c) == 0:
        return default
    try:
        v = float(c[0])
    except (TypeError, ValueError):
        return default
    return v if _math.isfinite(v) else default


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


import random as _rnd
# Per-cook RNG, reseeded every onCook so sub-emitters get fresh random
# positions each frame (avoids the regular brush look from a fixed
# scatter table). The seed is rotated by frame so adjacent frames
# produce different-but-correlated patterns rather than perfectly
# decorrelated noise that would flicker.
_RNG = _rnd.Random()


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
        scriptOp.numSamples = 0
        return

    # MULTI-PERSON: spawn from every active person's limbs. Lid encodes both
    # person and limb (Lid = p*n_lm + lm_i) so color_attr can derive per-person
    # tint from Lid/n_lm and per-limb palette from Lid%n_lm. Absent persons
    # contribute zero w (no births) so the count stays bounded.
    try:
        bl = mod.body_logic
        persons = bl.MAX_PERSONS
    except Exception:
        bl = None
        persons = 1

    def _read_p(p, lm, suffix, default=0.0):
        """Per-person channel read — uses the centralised name resolver in
        body_logic so the legacy fallback for p=0 is defined exactly ONCE."""
        if bl is None:
            return default
        return bl.read_person_chan(src, p, lm, suffix, default)

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
    # Speed-INDEPENDENT angular jitter (radians) on each particle's launch
    # direction. The fan above scales with limb speed, so slight motion gets no
    # spread and reads as one tight stream; this baseline jitter spreads the
    # launch directions so even slow motion sheds as a soft spray. 0 = off.
    spawn_ang_jitter  = float(_par('Spawnangjitter',     0.45))

    # Skip persons with zero visibility across ALL their landmarks → don't waste
    # samples on bodies that aren't tracked (single-person scene = 75% saving).
    active_persons = []
    for p in range(persons):
        for lm in lms:
            if _read_p(p, lm, 'visible', 0.0) > 0.0:
                active_persons.append(p)
                break
    if not active_persons:
        active_persons = [0]   # always emit person-0 row so the POP stays alive
    total = len(active_persons) * n_lm * spawn_count

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

    # Re-seed every cook so the spawn pattern doesn't repeat (no brush
    # look). Using the absFrame as the seed gives reproducibility for
    # debugging while still rolling forward each frame.
    _RNG.seed(int(absTime.frame))

    idx = 0
    # Mediapipe gives landmarks in normalized selfie-cam UV:
    #   x, y ∈ [0, 1]   (1:1 aspect)
    #   z   ≈ [-1, +1]  (monocular depth, noisy)
    # The bounds box is now aspect-correct (e.g. x ∈ [0, 16/9]) and z is
    # a thin slab. We REMAP rather than clamp so the visible box fills
    # with motion across its full extent — particularly the right half
    # which would otherwise be empty because mediapipe's x stops at 1.0.
    bz_min = float(_par('Boundsminz', -0.05))
    bz_max = float(_par('Boundsmaxz',  0.05))
    bx_min = float(_par('Boundsminx',  0.0))
    bx_max = float(_par('Boundsmaxx',  16/9))
    by_min = float(_par('Boundsminy',  0.0))
    by_max = float(_par('Boundsmaxy',  1.0))

    bx_w = bx_max - bx_min
    by_w = by_max - by_min
    bz_c = (bz_min + bz_max) * 0.5
    bz_h = (bz_max - bz_min) * 0.5
    margin = 0.005

    def _clamp(v, lo, hi):
        return max(lo, min(hi, v))

    for p in range(persons):
      for lm_i, lm in enumerate(lms):
        x  = _read_p(p, lm, 'x')
        y  = _read_p(p, lm, 'y')
        z  = _read_p(p, lm, 'z')
        vx = _read_p(p, lm, 'vx')
        vy = _read_p(p, lm, 'vy')
        vz = _read_p(p, lm, 'vz')
        em = _read_p(p, lm, 'emit')
        bu = _read_p(p, lm, 'burst')
        vi = _read_p(p, lm, 'visible')

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
            # Fresh per-spawn random offset within velocity-aligned
            # rectangle. Re-rolled every cook so the cloud looks
            # organically jittery rather than a stamped brush pattern.
            rel_along = _RNG.uniform(-0.5, 0.5)
            rel_perp  = _RNG.uniform(-0.5, 0.5)

            # Scale to actual extents along each local axis.
            local_along = rel_along * 2.0 * half_along
            local_perp  = rel_perp  * 2.0 * half_perp

            # Rotate local offset into world coords.
            off_x = along_x * local_along + perp_x * local_perp
            off_y = along_y * local_along + perp_y * local_perp

            # Linear remap from mediapipe UV [0,1]² to bounds-box xy.
            # x stretches with the aspect ratio — fills the new wider box.
            wx = bx_min + (x + off_x) * bx_w
            wy = by_min + (y + off_y) * by_w
            # Mediapipe z is unreliable: monocular depth, narrow range,
            # often biased one way (e.g. user always closer/farther than
            # camera reference). Using it directly bunches spawns in a
            # thin slice. Better: random z within the slab on each spawn.
            # The field force still owns z motion through Zforceweight.
            wz = _RNG.uniform(bz_min + margin, bz_max - margin)
            chans['P0'][idx] = _clamp(wx, bx_min + margin, bx_max - margin)
            chans['P1'][idx] = _clamp(wy, by_min + margin, by_max - margin)
            chans['P2'][idx] = _clamp(wz, bz_min + margin, bz_max - margin)
            # Launch velocity: the limb's motion vector (proportional to limb
            # speed), rotated by a random angle within a FORWARD cone — so the
            # spray always sheds roughly in the direction of motion, never
            # sideways or backward. Moving up → particles go up-ish, not down.
            # The cone half-angle = baseline jitter (Spawnangjitter) widened by
            # speed (Spawnvelfan × speed_factor) for a broader fast-swipe plume,
            # hard-capped at ~1 rad (±57°) so it can never reverse direction.
            cone  = min(spawn_ang_jitter + spawn_vel_fan * speed_factor, 1.0)
            theta = _RNG.uniform(-1.0, 1.0) * cone
            ct = math.cos(theta)
            st = math.sin(theta)
            chans['v0'][idx] = base_svx * ct - base_svy * st
            chans['v1'][idx] = base_svx * st + base_svy * ct
            chans['v2'][idx] = svz
            chans['w'][idx]  = w_per
            # id encodes BOTH person and limb: Lid = p*n_lm + lm_i.
            # color_attr derives per-limb palette by `lid % n_lm` and per-person
            # tint by `lid / n_lm` (each person wears a distinct hue shift).
            chans['id'][idx] = float(p * n_lm + lm_i)
            idx += 1

    return
