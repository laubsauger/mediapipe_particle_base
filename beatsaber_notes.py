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
    'color_red', 'color_green', 'color_blue',
    'cut_x', 'cut_y', 'cut_angle', 'cut_angle_deg',
    'state_active', 'state_hit', 'state_missed',
    'age',
    'time_to_hit',
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

    # Filter out HIT notes — they should disappear instantly when the
    # saber slashes through them. Missed notes stay in the renderable
    # set so they continue flying past the camera.
    active = [n for n in snapshot.get('active_notes', [])
              if n.state != 'hit']
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

    # Side-locking: when Mirrorsides is on, red notes are forced to
    # the user's left-hand side and blue to the right. With a mirrored
    # webcam, MediaPipe `left_wrist` lands on screen-right (selfie
    # convention), so RED on screen-right and BLUE on screen-left.
    # The beatmap may otherwise place a red note in the right half of
    # the playfield — which would force the user to cross arms.
    mirror_sides = bool(getattr(parent().par, 'Mirrorsides',
                                type('p', (), {'eval': lambda s: 1})()).eval())

    for i, note in enumerate(active):
        chans['id'][i]    = float(note.id)
        # Push red into the screen-right half (>= 0.5) and blue into
        # the left half (< 0.5) when mirror-sides is on. We preserve
        # the WITHIN-side variation by mapping the original x ∈ [0,1]
        # into [0.5,1] for red and [0,0.5] for blue.
        nx = float(note.x)
        if mirror_sides:
            if note.color == 'red':
                nx = 0.5 + 0.5 * nx     # always >= 0.5
            else:                        # blue
                nx = 0.5 * nx           # always < 0.5
        chans['x'][i]     = nx
        chans['y'][i]     = float(note.y)
        chans['z'][i]     = float(note.z)
        chans['size'][i]  = float(note.size)

        # HDR neon values (>1) so a Bloom TOP downstream can pick up
        # the brightest channels and produce the saturated glow pass.
        # Without HDR, RGB clamps at 1.0 and bloom can't differentiate
        # the lit cube from a regular pixel. Pixel format on render_scene
        # must also be a float format (rgba16float / rgba32float) for
        # values >1 to survive into the composite.
        chans['color_red'][i]   = 2.4 if note.color == 'red'  else 0.0
        chans['color_green'][i] = 0.0
        chans['color_blue'][i]  = 2.4 if note.color == 'blue' else 0.0

        # Cut direction components, plus a scalar angle.
        if _CUT_VECTORS is not None:
            cv = _CUT_VECTORS.get(note.cut, (0.0, 0.0, 0.0))
        else:
            cv = (0.0, 0.0, 0.0)
        chans['cut_x'][i] = float(cv[0])
        chans['cut_y'][i] = float(cv[1])
        ang_rad = math.atan2(cv[1], cv[0]) if (cv[0] or cv[1]) else 0.0
        chans['cut_angle'][i]     = ang_rad
        chans['cut_angle_deg'][i] = math.degrees(ang_rad)

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

        # Time-to-hit: seconds until the note crosses the hit plane.
        # Used by a hit-indicator overlay to pulse just before contact.
        chans['time_to_hit'][i] = float(note.time - song_time)

    return
