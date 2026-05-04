# beatsaber_slices.py
# ===================
# Script CHOP callback — physics-driven slice-half particles spawned
# whenever the player lands a hit. Each hit produces TWO instances
# representing the two halves of the cut block, flying apart along
# the perpendicular of the cut direction with the saber's swing
# velocity baked in, plus gravity dragging them down.
#
# Output channels (one sample per active slice half):
#   id              monotonic index
#   x, y, z         current world position
#   sx, sy, sz      per-instance scale (each half is half the note)
#   rx, ry, rz      rotation degrees (tumbling as it falls)
#   color_red       1.0 (or HDR neon) when the parent note was red
#   color_green     0
#   color_blue      same for blue
#   alpha           1 → 0 over LIFE seconds (for fade-out)
#   age             seconds since spawn
#
# Reads `beatsaber_last_hits` storage from the parent COMP — populated
# by beatsaber_game_tick each cook with the events that fired this
# tick. We append two slice entries per hit, then advance physics on
# every active slice each cook.

import math


CHANNEL_NAMES = (
    'id',
    'x', 'y', 'z',
    'sx', 'sy', 'sz',
    'rx', 'ry', 'rz',
    'color_red', 'color_green', 'color_blue',
    'alpha', 'age',
)

LIFE = 1.4              # slices live this long (seconds)
GRAVITY = -1.6          # UV/sec² (negative = down in MediaPipe Y; sy-flip in
                        # geo COMP shows this as world-down on screen)
PUSH_PERP = 0.7         # outward push along perpendicular of cut direction
PUSH_FROM_SABER = 0.4   # how much of saber velocity to inherit
SPIN_RATE = 360.0       # degrees/sec rotational tumble


def _normalize_2d(x, y):
    m = math.sqrt(x * x + y * y)
    if m < 1e-6:
        return (0.0, 1.0)   # default upward
    return (x / m, y / m)


def _spawn_pair(state, hit, next_id):
    """Append two slice halves for a single hit. Returns the next id."""
    cx, cy = hit['cut_x'], hit['cut_y']
    cut_x, cut_y = _normalize_2d(cx, cy)
    # Perpendicular in 2D: rotate 90° CCW.
    perp_x, perp_y = -cut_y, cut_x

    # Half-size along the cut: each half is full size in the cut direction
    # and half-size perpendicular to it. Approximate with non-uniform
    # cube scale (sx/sy/sz). For simplicity we scale uniformly half.
    half_size = hit['size'] * 0.5

    # Saber velocity inheritance — the cut motion carries the halves.
    sv_x = hit['saber_vx'] * PUSH_FROM_SABER
    sv_y = hit['saber_vy'] * PUSH_FROM_SABER
    sv_z = hit['saber_vz'] * PUSH_FROM_SABER

    is_red = hit['color'] == 'red'
    cr = 2.4 if is_red else 0.0
    cb = 2.4 if not is_red else 0.0

    for sign in (+1.0, -1.0):
        vx = sv_x + sign * perp_x * PUSH_PERP
        vy = sv_y + sign * perp_y * PUSH_PERP
        vz = sv_z
        # Spawn position offset slightly so the two halves don't overlap.
        ox = sign * perp_x * (half_size * 0.5)
        oy = sign * perp_y * (half_size * 0.5)
        state.append({
            'id':      next_id,
            'x':       hit['x'] + ox,
            'y':       hit['y'] + oy,
            'z':       hit['z'],
            'vx':      vx, 'vy': vy, 'vz': vz,
            'sx':      half_size,
            'sy':      half_size * 1.5,  # taller along Y (slices are slabs)
            'sz':      half_size * 1.5,
            'rx':      0.0, 'ry': 0.0, 'rz': 0.0,
            # Tumble rate: random-ish from sign × perpendicular × spin.
            'wx':      sign * SPIN_RATE * 0.4,
            'wy':      sign * SPIN_RATE * 0.6,
            'wz':      sign * SPIN_RATE,
            'color_red':   cr,
            'color_green': 0.0,
            'color_blue':  cb,
            'age':     0.0,
        })
        next_id += 1
    return next_id


def _advance(state, dt):
    """Integrate physics + age. Drop dead slices."""
    alive = []
    for s in state:
        s['age'] += dt
        if s['age'] >= LIFE:
            continue
        # Gravity on vy (MediaPipe-Y down = positive due to sy-flip;
        # using negative GRAVITY means halves fall on screen).
        s['vy'] -= GRAVITY * dt   # GRAVITY is negative → vy becomes positive
        s['x']  += s['vx'] * dt
        s['y']  += s['vy'] * dt
        s['z']  += s['vz'] * dt
        s['rx'] += s['wx'] * dt
        s['ry'] += s['wy'] * dt
        s['rz'] += s['wz'] * dt
        alive.append(s)
    return alive


def onCook(scriptOp):
    scriptOp.clear()
    if scriptOp.isTimeSlice:
        scriptOp.isTimeSlice = False

    comp = parent()
    state = comp.fetch('beatsaber_slice_state', None)
    if state is None:
        state = []

    # dt — use absTime delta. We rely on absTime.frame to track per-cook delta.
    last_t = comp.fetch('beatsaber_slice_last_t', None)
    now    = float(absTime.seconds)
    if last_t is None:
        dt = 1.0 / 60.0
    else:
        dt = max(1e-4, now - last_t)
    comp.store('beatsaber_slice_last_t', now)

    # 1. Advance existing slices.
    state = _advance(state, dt)

    # 2. Spawn new slices from this cook's hit list.
    hits = comp.fetch('beatsaber_last_hits', []) or []
    next_id = comp.fetch('beatsaber_slice_next_id', 0) or 0
    for h in hits:
        next_id = _spawn_pair(state, h, next_id)
    comp.store('beatsaber_slice_next_id', next_id)
    comp.store('beatsaber_slice_state', state)

    # 3. Emit channels.
    chans = {name: scriptOp.appendChan(name) for name in CHANNEL_NAMES}
    n = len(state)
    if n == 0:
        scriptOp.numSamples = 0
        return
    scriptOp.clear()
    scriptOp.numSamples = n
    chans = {name: scriptOp.appendChan(name) for name in CHANNEL_NAMES}
    for i, s in enumerate(state):
        chans['id'][i]    = float(s['id'])
        chans['x'][i]     = s['x']
        chans['y'][i]     = s['y']
        chans['z'][i]     = s['z']
        chans['sx'][i]    = s['sx']
        chans['sy'][i]    = s['sy']
        chans['sz'][i]    = s['sz']
        chans['rx'][i]    = s['rx']
        chans['ry'][i]    = s['ry']
        chans['rz'][i]    = s['rz']
        chans['color_red'][i]   = s['color_red']
        chans['color_green'][i] = s['color_green']
        chans['color_blue'][i]  = s['color_blue']
        # Linear fade.
        chans['alpha'][i] = max(0.0, 1.0 - s['age'] / LIFE)
        chans['age'][i]   = s['age']
    return
