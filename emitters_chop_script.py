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
# Why this shape? CHOP to POP recognises the `P[0] P[1] P[2]` bracket
# convention and rebuilds them as a single vec3 `P` point attribute (same for
# `v[...]`). Everything else (scalar `w`, int `id`) becomes a plain per-point
# attribute. One sample per landmark → one point per landmark.
#
# Output:
#   numSamples = N  (one per landmark)
#   channels:
#     P[0], P[1], P[2]   point position (P[2] = 0; we stay in 2D)
#     v[0], v[1], v[2]   initial velocity (v[2] = 0)
#     w                  spawn weight = (emit + Burstgain * burst) * visible
#     id                 landmark index, useful for per-limb coloring

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


def onCook(scriptOp):
    scriptOp.clear()
    # Time Slice off — we emit the current-frame landmark snapshot. Any
    # temporal smoothing is already done by lag1 upstream.
    if scriptOp.isTimeSlice:
        scriptOp.isTimeSlice = False

    src = op('lag1')
    lms = _landmark_list()
    n = len(lms)
    if src is None or n == 0:
        # Emit an empty CHOP so the downstream CHOP-to-POP doesn't error.
        scriptOp.numSamples = 0
        return

    try:
        burst_gain = float(parent().par.Burstgain.eval())
    except Exception:
        burst_gain = 1.0

    # IMPORTANT: set numSamples BEFORE appending channels. Channels inherit
    # their sample count from the op when created; appending after changing
    # numSamples leaves the new channels short.
    scriptOp.numSamples = n
    scriptOp.rate = me.time.rate

    chan_names = ['P[0]', 'P[1]', 'P[2]',
                  'v[0]', 'v[1]', 'v[2]',
                  'w', 'id']
    chans = {name: scriptOp.appendChan(name) for name in chan_names}

    for i, lm in enumerate(lms):
        x  = _read(src, f'{lm}:x')
        y  = _read(src, f'{lm}:y')
        vx = _read(src, f'{lm}:vx')
        vy = _read(src, f'{lm}:vy')
        em = _read(src, f'{lm}:emit')
        bu = _read(src, f'{lm}:burst')
        vi = _read(src, f'{lm}:visible')

        chans['P[0]'][i] = x
        chans['P[1]'][i] = y
        chans['P[2]'][i] = 0.0
        chans['v[0]'][i] = vx
        chans['v[1]'][i] = vy
        chans['v[2]'][i] = 0.0
        # Gate by visibility so dropped limbs don't spawn from their last
        # known position.
        chans['w'][i]    = (em + burst_gain * bu) * vi
        chans['id'][i]   = float(i)

    return
