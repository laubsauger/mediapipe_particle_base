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
# Wavefront emission
# ------------------
# For each landmark we emit N = `Spawncount` sub-emitter points spread
# along a line PERPENDICULAR to the limb's xy velocity vector. The spread
# width scales with speed — at rest the N sub-emitters collapse to the
# landmark's single point (no spread); at full whip they form a wide
# perpendicular line. Combined with a reduced `StartPartvel` (via
# `Spawnvelscale`), you get a wall of particles launching together in
# the velocity direction instead of a single-point trickle.
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

    burst_gain        = float(_par('Burstgain',       1.0))
    spawn_count       = max(1, int(_par('Spawncount',       12)))
    spawn_spread_max  = float(_par('Spawnspread',      0.08))
    spawn_spread_ref  = max(1e-4, float(_par('Spawnspreadref',  2.0)))
    spawn_vel_scale   = float(_par('Spawnvelscale',    0.3))
    # Angular fan: each sub-emitter's StartPartvel gets an outward
    # perpendicular kick scaled by its t offset. 0 = parallel wavefront
    # (all particles fly the same direction), higher = fanned/curving
    # edges. Scaled by limb speed so at rest there's no fan regardless
    # of this setting. Typical 0.2 = mild curve, 0.5+ = pronounced cone.
    spawn_vel_fan     = float(_par('Spawnvelfan',      0.25))

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

    # Avoid division-by-zero in the t=[-0.5..0.5] spread formula when
    # Spawncount == 1 (fall back to single point at landmark centre).
    spread_divisor = max(spawn_count - 1, 1)

    idx = 0
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

        # Per-sub-emitter weight. We do NOT divide by spawn_count — Particle
        # POP reads `int(w)` per input point per frame, and a divided
        # fractional w (e.g., 3.3 / 12 ≈ 0.28) rounds to 0 → zero particles
        # ever spawn. Instead each sub-emitter independently emits int(w)
        # per frame. Total density scales with spawn_count: more sub-emitters
        # = denser wavefront. Tune density via spawn_count + Burstgain (spike
        # intensity) + Lifemax (how long particles stay alive).
        w_per = (em + burst_gain * bu) * vi

        # Perpendicular direction to the xy velocity (for the spread line).
        # 2D perpendicular of (vx, vy) is (-vy, vx); we normalise it.
        vmag_xy = math.sqrt(vx * vx + vy * vy)
        if vmag_xy > 1e-4:
            perp_x = -vy / vmag_xy
            perp_y =  vx / vmag_xy
        else:
            perp_x = 0.0
            perp_y = 0.0

        # Spread width scales from 0 at rest to spawn_spread_max at
        # Spawnspreadref speed — so gentle motion stays near-point,
        # fast motion opens into a wavefront. Clamped at the max.
        spread_scale = min(vmag_xy / spawn_spread_ref, 1.0)
        half_spread  = spawn_spread_max * spread_scale * 0.5

        # Base velocity (shared by all sub-emitters before the fan is added)
        base_svx = vx * spawn_vel_scale
        base_svy = vy * spawn_vel_scale
        svz      = vz * spawn_vel_scale

        for k in range(spawn_count):
            # t ∈ [-0.5, +0.5] across the spread line
            t = (k / spread_divisor) - 0.5

            # Position offset: perpendicular to velocity, scales with speed.
            off_x = perp_x * 2.0 * half_spread * t
            off_y = perp_y * 2.0 * half_spread * t

            # Velocity fan: edge sub-emitters get a perpendicular kick
            # proportional to their t offset and the limb's xy speed.
            # Center (t=0) stays parallel to limb velocity.
            fan_vx = perp_x * t * spawn_vel_fan * vmag_xy * spawn_vel_scale
            fan_vy = perp_y * t * spawn_vel_fan * vmag_xy * spawn_vel_scale

            chans['P0'][idx] = x + off_x
            chans['P1'][idx] = y + off_y
            chans['P2'][idx] = z
            chans['v0'][idx] = base_svx + fan_vx
            chans['v1'][idx] = base_svy + fan_vy
            chans['v2'][idx] = svz
            chans['w'][idx]  = w_per
            # id stays the landmark index so per-limb colouring still works
            # across all sub-emitters of the same limb.
            chans['id'][idx] = float(lm_i)
            idx += 1

    return
