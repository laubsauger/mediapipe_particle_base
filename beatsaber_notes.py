# beatsaber_notes.py
# ==================
# Script CHOP callback for active-notes output.
#
# Reads the game snapshot from parent COMP storage (populated by
# beatsaber_game_tick) and emits one CHOP sample per currently active
# note, so downstream instancing can render the blocks.
#
# Output channels (one per sample, one sample per active note):
#   id              monotonically increasing note id
#   x, y, z         current world position (z animates from spawn→0)
#   size            cube edge length (UV)
#   color_red       1.0 if red, 0.0 if blue
#   color_blue      1.0 if blue, 0.0 if red
#   cut_x, cut_y    required cut direction unit vector (z always 0)
#   cut_angle       same direction as radians (atan2(cut_y, cut_x))
#   state_active    1.0 while "spawned" (flying, not hit yet)
#                   0.0 if hit/missed (for fade-out animation)
#   state_hit       1.0 if hit this tick (transient)
#   state_missed    1.0 if missed this tick (transient)
#   age             seconds since spawn (for per-note VFX timing)
#
# If no notes are active, emits 0 samples (the channel definitions are
# still present so downstream schemas don't change shape).
#
# Paste into a Text DAT `beatsaber_notes` inside the beatsaber_controller
# COMP, and attach as the Callbacks DAT of a Script CHOP called
# `notes_chop`. The Script CHOP needs NO INPUTS — it reads purely from
# parent storage.

import math
import os
import sys


# Mirror the path setup from beatsaber_game_tick so the CUT_VECTORS import
# works even if this op cooks before the tick op on the first frame.
def _ensure_beatsaber_on_path():
    try:
        pf = project.folder
    except Exception:
        return
    if pf and pf not in sys.path:
        sys.path.insert(0, pf)


_ensure_beatsaber_on_path()

try:
    from beatsaber.beatmap import CUT_VECTORS as _CUT_VECTORS
except Exception:
    _CUT_VECTORS = None


CHANNEL_NAMES = (
    'id',
    'x', 'y', 'z',
    'size',
    'color_red', 'color_blue',
    'cut_x', 'cut_y', 'cut_angle',
    'state_active', 'state_hit', 'state_missed',
    'age',
)


def onCook(scriptOp):
    scriptOp.clear()
    if scriptOp.isTimeSlice:
        scriptOp.isTimeSlice = False

    snapshot = parent().fetch('beatsaber_snapshot', None)

    # Build the output buffer even if empty, so the channel layout is stable.
    chans = {name: scriptOp.appendChan(name) for name in CHANNEL_NAMES}
    scriptOp.rate = me.time.rate

    if snapshot is None:
        scriptOp.numSamples = 0
        return

    active = snapshot.get('active_notes', [])
    n = len(active)
    if n == 0:
        scriptOp.numSamples = 0
        return

    # Allocate N samples. appendChan above allocated with numSamples=1;
    # the only safe way to resize is to clear, set numSamples, re-append.
    scriptOp.clear()
    scriptOp.numSamples = n
    chans = {name: scriptOp.appendChan(name) for name in CHANNEL_NAMES}

    song_time = snapshot.get('song_time', 0.0)

    for i, note in enumerate(active):
        chans['id'][i]    = float(note.id)
        chans['x'][i]     = float(note.x)
        chans['y'][i]     = float(note.y)
        chans['z'][i]     = float(note.z)
        chans['size'][i]  = float(note.size)

        chans['color_red'][i]  = 1.0 if note.color == 'red' else 0.0
        chans['color_blue'][i] = 1.0 if note.color == 'blue' else 0.0

        # Cut direction components, plus a scalar angle.
        if _CUT_VECTORS is not None:
            cv = _CUT_VECTORS.get(note.cut, (0.0, 0.0, 0.0))
        else:
            cv = (0.0, 0.0, 0.0)
        chans['cut_x'][i] = float(cv[0])
        chans['cut_y'][i] = float(cv[1])
        chans['cut_angle'][i] = math.atan2(cv[1], cv[0]) if (cv[0] or cv[1]) else 0.0

        # State flags. Most notes will be "spawned" (active); hit/missed
        # notes linger briefly in the cleanup window for VFX.
        chans['state_active'][i] = 1.0 if note.state == 'spawned' else 0.0
        chans['state_hit'][i]    = 1.0 if note.state == 'hit'     else 0.0
        chans['state_missed'][i] = 1.0 if note.state == 'missed'  else 0.0

        # Age since spawn (seconds).
        if note.spawn_time is not None:
            chans['age'][i] = float(max(0.0, song_time - note.spawn_time))
        else:
            chans['age'][i] = 0.0

    return
