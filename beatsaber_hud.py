# beatsaber_hud.py
# ================
# Pure-Python helpers used by the renderer's HUD Text TOPs to format
# strings from the game state. Lives as a synced Text DAT inside the
# renderer COMP (`beatsaber_hud`) and gets called via expressions on
# Text TOP `text` parameters, e.g.
#
#     mod('beatsaber_hud').score_text()
#
# No PIL, no image rendering — TD's native Text TOP handles all glyph
# rasterization. This file is just data plumbing + formatting + the
# event-log accumulator state machine.
#
# Why a helper module instead of inline expressions
# -------------------------------------------------
# Text TOP expressions for these HUD elements can be long and brittle
# (e.g. you have to walk to the controller via the renderer's
# `Controller` par each time). Putting the path-walking + formatting
# in one place keeps every Text TOP's expression a one-liner and lets
# us evolve the format without touching the renderer's network.
#
# Event log state
# ---------------
# `event_log_text()` reads the latest game snapshot via the renderer
# COMP's `Controller` par (which points at beatsaber_controller),
# walks this cook's per-event lists, appends formatted rows to a list
# stored on the renderer COMP via `fetch/store`, and returns the
# joined multi-line text. Auto-clears when `song_time` jumps
# backward (game reset / loop wraparound).

import math


# Storage keys on the renderer COMP — namespaced so they don't collide
# with anything else the renderer might fetch/store.
_EVENTLOG_KEY        = 'beatsaber_eventlog'
_EVENTLOG_PREVTIME   = 'beatsaber_eventlog_prev_time'
_EVENTLOG_MAX_ROWS   = 22         # how many rows to render in the HUD
_EVENTLOG_BUFFER_CAP = 100        # how many rows we keep in memory


def _renderer():
    """Resolve the renderer COMP — `me` is this Text DAT, its parent
    COMP is the renderer. Note `me.parent()` is called as a function
    (not the bare attribute, which returns a `td.ParentShortcut`
    helper that doesn't expose `.par`). Returns None if anything is
    misconfigured."""
    try:
        return me.parent()
    except Exception:
        return None


def _controller():
    """Walk `Controller` par on the renderer COMP to the controller
    Base COMP. Returns None if the par isn't set."""
    r = _renderer()
    if r is None:
        return None
    par = getattr(r.par, 'Controller', None)
    if par is None:
        return None
    try:
        return par.eval()
    except Exception:
        return None


def _game_tick():
    """The game_tick CHOP whose channels we read for HUD numbers."""
    c = _controller()
    if c is None:
        return None
    return c.op('game_tick')


def _chan(name, default=0.0):
    """Read a channel value from game_tick by name. Defensive against
    every layer being None on the first cook before everything is
    wired up."""
    gt = _game_tick()
    if gt is None:
        return default
    ch = gt[name]
    if ch is None:
        return default
    try:
        v = float(ch[0])
        if not math.isfinite(v):
            return default
        return v
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Text formatters — call from a Text TOP `text` expression.
# ---------------------------------------------------------------------------

def score_text():
    """Big top-right score readout."""
    return f"{int(_chan('score')):,}"


def combo_text():
    """`<combo>  x<multiplier>` — fits comfortably below the score."""
    return f"{int(_chan('combo'))}  x{int(_chan('multiplier'))}"


def accuracy_text():
    """Percentage with one decimal."""
    return f"{_chan('accuracy') * 100:.1f}%"


def song_time_text():
    """Top-left mm:ss.cc clock."""
    t = _chan('song_time')
    minutes = int(t // 60)
    seconds = t - minutes * 60
    return f"{minutes:02d}:{seconds:05.2f}"


def hits_misses_text():
    """Compact `Hh / Mm / Bb` summary, optional small readout."""
    return (f"{int(_chan('hits'))}h "
            f"{int(_chan('misses'))}m "
            f"{int(_chan('bad_cuts'))}b")


# ---------------------------------------------------------------------------
# Event-log accumulator — stateful, called by the event-log Text TOP.
# ---------------------------------------------------------------------------

def _harvest_events(snapshot, song_time, eventlog):
    """Append rows for any events fired this cook to the eventlog list,
    in dict form. Mutates eventlog in place."""
    if snapshot is None:
        return
    events = snapshot.get('events', {}) or {}
    for h in events.get('hits', []):
        eventlog.append({
            'song_time': song_time,
            'kind':      'HIT',
            'side':      h.get('saber', ''),
            'quality':   float(h.get('quality', 0.0)),
            'mult':      int(h.get('multiplier', 1)),
            'reason':    None,
        })
    for bc in events.get('bad_cuts', []):
        eventlog.append({
            'song_time': song_time,
            'kind':      'BAD',
            'side':      bc.get('saber', ''),
            'quality':   0.0,
            'mult':      1,
            'reason':    bc.get('reason', '?'),
        })
    for _ in events.get('misses', []):
        eventlog.append({
            'song_time': song_time,
            'kind':      'MISS',
            'side':      '',
            'quality':   0.0,
            'mult':      1,
            'reason':    None,
        })
    if len(eventlog) > _EVENTLOG_BUFFER_CAP:
        del eventlog[: len(eventlog) - _EVENTLOG_BUFFER_CAP]


def _format_row(entry):
    """One log row → display string. Plain ASCII so the default font
    renders consistently across platforms."""
    t  = entry['song_time']
    minutes = int(t // 60)
    seconds = t - minutes * 60
    ts   = f"{minutes:02d}:{seconds:05.2f}"
    side = entry['side']
    side_glyph = ' L ' if side == 'left' else (' R ' if side == 'right' else '   ')
    if entry['kind'] == 'HIT':
        suffix = f"  x{entry['mult']}" if entry['mult'] > 1 else ''
        return f"{ts} {side_glyph} HIT  q={entry['quality']:.2f}{suffix}"
    if entry['kind'] == 'BAD':
        r = entry['reason'] or ''
        short = ('wrong dir'   if 'direction' in r
                 else 'wrong color' if 'color' in r
                 else r)
        return f"{ts} {side_glyph} BAD  {short}"
    return f"{ts}     MISS"


def event_log_text():
    """Maintain the persistent log on the renderer COMP and return the
    joined multi-line text for the event-log Text TOP. Newest entry
    on top so the eye lands on the most recent event first."""
    r = _renderer()
    if r is None:
        return ""
    snapshot = None
    ctrl = _controller()
    if ctrl is not None:
        try:
            snapshot = ctrl.fetch('beatsaber_snapshot', None)
        except Exception:
            snapshot = None
    song_time = float(snapshot.get('song_time', 0.0)) if snapshot else 0.0

    eventlog  = r.fetch(_EVENTLOG_KEY,      [])
    prev_time = r.fetch(_EVENTLOG_PREVTIME, 0.0)
    if prev_time - song_time > 2.0:
        # song_time jumped backward → game reset / loop wraparound.
        eventlog = []
    r.store(_EVENTLOG_PREVTIME, song_time)

    _harvest_events(snapshot, song_time, eventlog)
    r.store(_EVENTLOG_KEY, eventlog)

    # Newest entry on top.
    rows = [_format_row(e) for e in eventlog[-_EVENTLOG_MAX_ROWS:]]
    rows.reverse()
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Self-test (run with `python3 beatsaber_hud.py`).
# ---------------------------------------------------------------------------
# We can't exercise the TD-only paths (`me`, `op`, `parent` etc.) from
# a plain interpreter, but we CAN unit-test the pure-data helpers:
# event harvesting, row formatting, the buffer cap, the reset on
# backwards-time-jump. These are the bits most likely to regress.

if __name__ == "__main__":
    log = []
    snap = {
        "events": {
            "hits": [
                {"note_id": 1, "saber": "left",  "quality": 0.91, "multiplier": 2},
                {"note_id": 2, "saber": "right", "quality": 0.55, "multiplier": 1},
            ],
            "bad_cuts": [
                {"note_id": 3, "saber": "left",  "reason": "bad_direction"},
            ],
            "misses": [4, 5],
        },
    }
    _harvest_events(snap, song_time=12.34, eventlog=log)
    print(f"log has {len(log)} entries:")
    for e in log:
        print(" ", _format_row(e))
    assert len(log) == 5

    big = []
    for i in range(150):
        _harvest_events({"events": {"misses": [i]}}, float(i), big)
    print(f"\nafter 150 misses, log capped at {len(big)} (limit={_EVENTLOG_BUFFER_CAP})")
    assert len(big) == _EVENTLOG_BUFFER_CAP

    # Format-row sanity for each kind.
    print("\nformat samples:")
    print(" ", _format_row({'song_time':1.0,  'kind':'HIT', 'side':'left',
                            'quality':1.0, 'mult':4, 'reason':None}))
    print(" ", _format_row({'song_time':2.0,  'kind':'BAD', 'side':'right',
                            'quality':0.0, 'mult':1, 'reason':'bad_color'}))
    print(" ", _format_row({'song_time':3.0,  'kind':'MISS','side':'',
                            'quality':0.0, 'mult':1, 'reason':None}))

    print("\nOK")
