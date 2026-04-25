# beatsaber_events.py
# ===================
# Script DAT callback for per-cook events (hits / misses / bad cuts / spawns).
#
# Reads the current-cook events from the parent COMP's stored snapshot
# and emits them as a table, one row per event. Useful for:
#   - Debug readout in the network editor
#   - Driving VFX triggers via DAT Execute hooks
#   - Logging to disk via File Out DAT
#   - UI notifications
#
# The table gets CLEARED AND REWRITTEN each cook, so it only shows the
# events that fired this specific frame. For a running log, attach a
# DAT Execute that copies rows to an accumulating Table DAT or writes
# them to disk.
#
# Columns:
#   event        one of: "spawned", "hit", "bad_cut", "miss"
#   song_time    seconds from song start when the event fired
#   note_id      numeric id of the note involved
#   saber        "left" / "right" (empty for spawn/miss events)
#   color        "red" / "blue" (the note's color)
#   cut          required cut direction (for hit/bad_cut)
#   quality      hit quality 0..1 (hits only)
#   angle_err    radians of cut-direction error (hit / bad_direction)
#   swing_speed  magnitude of saber tip velocity at hit
#   reason       "bad_color" / "bad_direction" for bad_cuts, else empty

HEADER = (
    'event', 'song_time', 'note_id', 'saber', 'color', 'cut',
    'quality', 'angle_err', 'swing_speed', 'reason',
)


def _cell(v):
    """Format one cell: floats to 3 decimals, None to empty string, else str."""
    if v is None:
        return ''
    if isinstance(v, float):
        return f'{v:.3f}'
    return str(v)


def onCook(scriptOp):
    scriptOp.clear()
    scriptOp.appendRow(list(HEADER))

    comp = parent()
    snapshot = comp.fetch('beatsaber_snapshot', None)
    if snapshot is None:
        return

    song_time = snapshot.get('song_time', 0.0)
    events = snapshot.get('events', {})

    # We need to look up each note's metadata (color, cut) by id. Build an
    # index from the active-notes list plus any recent hits/misses.
    notes_by_id = {}
    for n in snapshot.get('active_notes', []):
        notes_by_id[n.id] = n

    def _note_info(nid):
        n = notes_by_id.get(nid)
        if n is None:
            return ('', '')
        return (n.color, n.cut)

    # Spawned.
    for nid in events.get('spawned', []):
        color, cut = _note_info(nid)
        scriptOp.appendRow([
            _cell('spawned'),
            _cell(song_time),
            _cell(nid),
            _cell(''),
            _cell(color),
            _cell(cut),
            _cell(None),
            _cell(None),
            _cell(None),
            _cell(''),
        ])

    # Good hits.
    for h in events.get('hits', []):
        color, cut = _note_info(h.get('note_id'))
        scriptOp.appendRow([
            _cell('hit'),
            _cell(song_time),
            _cell(h.get('note_id')),
            _cell(h.get('saber')),
            _cell(color),
            _cell(cut),
            _cell(h.get('quality')),
            _cell(h.get('angle_error')),
            _cell(h.get('swing_speed')),
            _cell(''),
        ])

    # Bad cuts.
    for b in events.get('bad_cuts', []):
        color, cut = _note_info(b.get('note_id'))
        scriptOp.appendRow([
            _cell('bad_cut'),
            _cell(song_time),
            _cell(b.get('note_id')),
            _cell(b.get('saber')),
            _cell(color),
            _cell(cut),
            _cell(None),
            _cell(b.get('angle_error')),
            _cell(b.get('swing_speed')),
            _cell(b.get('reason')),
        ])

    # Misses.
    for nid in events.get('misses', []):
        color, cut = _note_info(nid)
        scriptOp.appendRow([
            _cell('miss'),
            _cell(song_time),
            _cell(nid),
            _cell(''),
            _cell(color),
            _cell(cut),
            _cell(None),
            _cell(None),
            _cell(None),
            _cell(''),
        ])

    return
