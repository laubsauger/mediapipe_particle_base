# beatsaber_slashes.py
# ====================
# Script CHOP callback — rolling history of GAME-JUDGED events for the
# bottom-of-screen slash log. We do NOT infer swings from burst /
# velocity heuristics anymore — every entry corresponds to a real
# `events.hits / .bad_cuts / .misses` record produced by the game
# tick. That way the user only sees what the game actually counted.
#
# Output channels (one sample per recent event, oldest → newest):
#   id              monotonic
#   side            0=left, 1=right
#   color_red       2.4 if left (red saber), 0 if right (HDR neon)
#   color_green     0
#   color_blue      2.4 if right, 0 if left
#   cut_angle_deg   degrees CCW from +x for the required cut direction
#   hit             1 = good hit, 0 = bad/miss
#   bad             1 = bad cut (wrong direction or color)
#   miss            1 = miss
#   age             seconds since logged

import math


CHANNEL_NAMES = (
    'id', 'side',
    'color_red', 'color_green', 'color_blue',
    'cut_angle_deg',
    'hit', 'bad', 'miss',
    'age',
)

LIFE         = 5.0    # seconds the log keeps an entry visible
MAX_LOG      = 8

CUT_TO_ANGLE = {
    'up':    90.0,    'down':  -90.0,
    'left':  180.0,   'right':   0.0,
    'up_left':  135.0, 'up_right':   45.0,
    'down_left': -135.0, 'down_right': -45.0,
    'any':       0.0,
}


def onCook(scriptOp):
    scriptOp.clear()
    if scriptOp.isTimeSlice:
        scriptOp.isTimeSlice = False

    comp = parent()
    state    = comp.fetch('beatsaber_slash_log', None) or []
    next_id  = comp.fetch('beatsaber_slash_next_id', 0)
    now      = float(absTime.seconds)

    # 1. Pull this cook's game events and append them.
    events = comp.fetch('beatsaber_last_events', []) or []
    for ev in events:
        side_str = ev.get('saber', 'left')
        is_red   = (side_str == 'left')
        kind     = ev.get('kind', 'miss')
        cut_dir  = ev.get('cut', 'any')
        ang      = CUT_TO_ANGLE.get(cut_dir, 0.0)
        state.append({
            'id':         next_id,
            'side':       0 if is_red else 1,
            'cr':         2.4 if is_red else 0.0,
            'cg':         0.0,
            'cb':         2.4 if not is_red else 0.0,
            'ang_deg':    ang,
            'hit':        1.0 if kind == 'hit'  else 0.0,
            'bad':        1.0 if kind == 'bad'  else 0.0,
            'miss':       1.0 if kind == 'miss' else 0.0,
            't_detect':   now,
        })
        next_id += 1
    comp.store('beatsaber_slash_next_id', next_id)

    # 2. Trim by age + cap at MAX_LOG.
    state = [s for s in state if (now - s['t_detect']) < LIFE]
    if len(state) > MAX_LOG:
        state = state[-MAX_LOG:]
    comp.store('beatsaber_slash_log', state)

    # 3. Emit channels.
    n = len(state)
    chans = {name: scriptOp.appendChan(name) for name in CHANNEL_NAMES}
    if n == 0:
        scriptOp.numSamples = 0
        return
    scriptOp.clear()
    scriptOp.numSamples = n
    chans = {name: scriptOp.appendChan(name) for name in CHANNEL_NAMES}
    for i, s in enumerate(state):
        chans['id'][i]            = float(s['id'])
        chans['side'][i]          = float(s['side'])
        chans['color_red'][i]     = s['cr']
        chans['color_green'][i]   = s['cg']
        chans['color_blue'][i]    = s['cb']
        chans['cut_angle_deg'][i] = s['ang_deg']
        chans['hit'][i]           = s['hit']
        chans['bad'][i]           = s['bad']
        chans['miss'][i]          = s['miss']
        chans['age'][i]           = max(0.0, now - s['t_detect'])
    return
