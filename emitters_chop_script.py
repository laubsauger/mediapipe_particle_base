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
# Output:
#   numSamples = N  (one point per landmark)
#   channels:
#     P0, P1, P2    → vec3 `P` attribute on the POP (position, 3D)
#     v0, v1, v2    → vec3 `v` attribute on the POP (initial velocity, 3D)
#     w             → float `w` attribute (spawn weight)
#                     = (emit + Burstgain * burst) * visible
#     id            → int `id` attribute (landmark index, for per-limb color)

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

    chan_names = ['P0', 'P1', 'P2',
                  'v0', 'v1', 'v2',
                  'w', 'id']
    chans = {name: scriptOp.appendChan(name) for name in chan_names}

    for i, lm in enumerate(lms):
        x  = _read(src, f'{lm}:x')
        y  = _read(src, f'{lm}:y')
        z  = _read(src, f'{lm}:z')
        vx = _read(src, f'{lm}:vx')
        vy = _read(src, f'{lm}:vy')
        vz = _read(src, f'{lm}:vz')
        em = _read(src, f'{lm}:emit')
        bu = _read(src, f'{lm}:burst')
        vi = _read(src, f'{lm}:visible')

        # 3D position and initial velocity — particles get launched with
        # the limb's current vz as well, so forward/back motion actually
        # flings them in the z direction through the POP Advance.
        chans['P0'][i] = x
        chans['P1'][i] = y
        chans['P2'][i] = z
        chans['v0'][i] = vx
        chans['v1'][i] = vy
        chans['v2'][i] = vz
        # Gate by visibility so dropped limbs don't spawn from their last
        # known position.
        chans['w'][i]  = (em + burst_gain * bu) * vi
        chans['id'][i] = float(i)

    return
