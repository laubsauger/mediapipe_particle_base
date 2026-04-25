# beatsaber_ui_top.py
# ===================
# Script TOP callback — renders the UI overlay (score, combo, multiplier,
# accuracy, event flashes) as an RGBA image. Composites over the main
# render downstream.
#
# Uses PIL (comes bundled with TD's Python). If PIL is missing for any
# reason, the callback degrades gracefully to an empty transparent TOP.
#
# Paste into a Text DAT called `beatsaber_ui_top` inside the
# beatsaber_renderer COMP, and attach as the Callbacks DAT of a
# Script TOP called `ui_top`.
#
# Reads from a parent par `Controller` → pointer to beatsaber_controller
# COMP, same as beatsaber_saber_sop.py. Or falls back to the relative
# path ../beatsaber_controller/game_tick.

import numpy as np

try:
    from PIL import Image, ImageDraw, ImageFont
    _HAVE_PIL = True
except Exception:
    _HAVE_PIL = False


# ---------------------------------------------------------------------------
# Layout constants. Tune to match your target resolution.
# ---------------------------------------------------------------------------
DEFAULT_W = 1920
DEFAULT_H = 1080

# Score panel — top-right corner.
SCORE_ANCHOR   = (1870, 30)        # top-right of text baseline
SCORE_SIZE     = 72
SCORE_COLOR    = (255, 255, 255, 230)

# Combo readout — below score.
COMBO_ANCHOR   = (1870, 120)
COMBO_SIZE     = 48
COMBO_COLOR    = (255, 220, 100, 230)

# Accuracy — below combo.
ACC_ANCHOR     = (1870, 180)
ACC_SIZE       = 32
ACC_COLOR      = (180, 220, 255, 200)

# Song time — top-left.
TIME_ANCHOR    = (50, 30)
TIME_SIZE      = 40
TIME_COLOR     = (200, 200, 200, 220)

# Event log — left side, scrolls top-down. No background fill — text
# overlays the render directly. The log persists across cooks via
# parent().store; we render the most recent EVENTLOG_MAX_ROWS entries.
EVENTLOG_X            = 50            # left margin
EVENTLOG_Y_TOP        = 240           # below song-time
EVENTLOG_ROW_HEIGHT   = 26
EVENTLOG_FONT_SIZE    = 20
EVENTLOG_MAX_ROWS     = 22            # how many rows visible at once
EVENTLOG_BUFFER_LIMIT = 100           # cap stored history (memory bound)
# Per-event-kind text colors. Hits are green tinted by quality.
EVENTLOG_BAD_COLOR    = (255, 100, 100, 230)
EVENTLOG_MISS_COLOR   = (170, 170, 170, 180)
# Saber side glyphs / colors — small accent in front of each row.
EVENTLOG_LEFT_COLOR   = (255, 90, 110, 230)   # red saber
EVENTLOG_RIGHT_COLOR  = (110, 160, 255, 230)  # blue saber

EVENTLOG_STORAGE_KEY  = 'beatsaber_eventlog'
EVENTLOG_RESET_KEY    = 'beatsaber_eventlog_prev_time'

# Event flash tints — full-screen single-frame colour blasts.
FLASH_HIT      = (0, 255, 120, 100)     # green (kept subtle so it doesn't block gameplay view)
FLASH_BAD      = (255, 80, 80, 140)     # red (more prominent — it's feedback you need to see)
FLASH_MISS     = (255, 255, 255, 60)    # dim white flash


def _controller_comp():
    """Resolve the beatsaber_controller COMP via the renderer's
    `Controller` par, falling back to a sibling relative path."""
    comp = parent()
    par = getattr(comp.par, 'Controller', None)
    if par is not None:
        ctrl = par.eval()
        if ctrl is not None:
            return ctrl
    return comp.op('../beatsaber_controller')


def _controller_tick_op():
    ctrl = _controller_comp()
    if ctrl is None:
        return parent().op('../beatsaber_controller/game_tick')
    tick = ctrl.op('game_tick')
    if tick is not None:
        return tick
    return parent().op('../beatsaber_controller/game_tick')


def _read(chop, name, default=0.0):
    if chop is None:
        return default
    c = chop[name]
    if c is None:
        return default
    try:
        return float(c[0])
    except Exception:
        return default


def _quality_color(q):
    """Map a quality value 0..1 to a green tint. High quality = bright
    green; low quality = dim olive. Always returns 4-tuple RGBA."""
    q = max(0.0, min(1.0, float(q)))
    g = int(160 + 95 * q)        # 160..255 green channel
    r = int(80 - 60 * q)         # 80..20 — desaturate as quality rises
    b = int(120 - 80 * q)        # 120..40
    a = int(180 + 50 * q)        # 180..230 alpha
    return (r, g, b, a)


def _harvest_events(snapshot, song_time, eventlog):
    """Append rows for any events fired this cook to the eventlog list,
    in (song_time, kind, side, payload) form. Mutates eventlog in place."""
    if snapshot is None:
        return
    events = snapshot.get('events', {})
    for h in events.get('hits', []):
        # h = {note_id, saber, quality, hit_point, points, combo_after, multiplier}
        eventlog.append({
            'song_time': song_time,
            'kind':      'HIT',
            'side':      h.get('saber', ''),
            'quality':   float(h.get('quality', 0.0)),
            'mult':      int(h.get('multiplier', 1)),
            'reason':    None,
        })
    for bc in events.get('bad_cuts', []):
        # bc = {note_id, saber, reason, angle_error, swing_speed}
        eventlog.append({
            'song_time': song_time,
            'kind':      'BAD',
            'side':      bc.get('saber', ''),
            'quality':   0.0,
            'mult':      1,
            # Reason codes from hit_detection: "bad_color" / "bad_direction".
            'reason':    bc.get('reason', '?'),
        })
    for note_id in events.get('misses', []):
        eventlog.append({
            'song_time': song_time,
            'kind':      'MISS',
            'side':      '',
            'quality':   0.0,
            'mult':      1,
            'reason':    None,
        })
    # Cap stored history.
    if len(eventlog) > EVENTLOG_BUFFER_LIMIT:
        del eventlog[: len(eventlog) - EVENTLOG_BUFFER_LIMIT]


def _format_row(entry):
    """Format one log row into (timestamp_str, body_str, body_color)."""
    t = entry['song_time']
    minutes = int(t // 60)
    seconds = t - minutes * 60
    ts = f"{minutes:02d}:{seconds:05.2f}"
    side = entry['side']
    side_glyph = ' L ' if side == 'left' else (' R ' if side == 'right' else '   ')
    if entry['kind'] == 'HIT':
        body = f"{ts} {side_glyph} HIT  q={entry['quality']:.2f}"
        if entry['mult'] > 1:
            body += f"  ×{entry['mult']}"
        return body, _quality_color(entry['quality'])
    if entry['kind'] == 'BAD':
        # Compress reason: "bad_direction" → "wrong dir", "bad_color" → "wrong color"
        r = entry['reason'] or ''
        short = ('wrong dir' if 'direction' in r
                 else 'wrong color' if 'color' in r
                 else r)
        body = f"{ts} {side_glyph} BAD  {short}"
        return body, EVENTLOG_BAD_COLOR
    # MISS
    body = f"{ts}     MISS"
    return body, EVENTLOG_MISS_COLOR


def _font(size):
    """Try a few common fonts; fall back to PIL default if none work.
    TD ships on macOS/Windows/Linux with differing paths, so we try all."""
    candidates = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "C:\\Windows\\Fonts\\segoeui.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def onSetupParameters(scriptOp):
    return


def onCook(scriptOp):
    W = DEFAULT_W
    H = DEFAULT_H

    if not _HAVE_PIL:
        # Fall back to a transparent buffer so rendering doesn't explode.
        scriptOp.copyNumpyArray(np.zeros((H, W, 4), dtype=np.float32))
        return

    tick = _controller_tick_op()
    score      = int(_read(tick, 'score', 0))
    combo      = int(_read(tick, 'combo', 0))
    mult       = int(_read(tick, 'multiplier', 1))
    accuracy   = _read(tick, 'accuracy', 0.0)
    song_time  = _read(tick, 'song_time', 0.0)
    hit_flash  = _read(tick, 'hit_this_frame', 0.0)
    bad_flash  = _read(tick, 'bad_cut_this_frame', 0.0)
    miss_flash = _read(tick, 'miss_this_frame', 0.0)

    # --- Event log update ----------------------------------------------------
    # Pull the latest game snapshot directly from the controller's storage
    # (set by beatsaber_game_tick.onCook each cook). Then walk the per-cook
    # event lists and append rows to our persistent log.
    #
    # Storage lives on the renderer COMP (not the controller) so multiple
    # renderers could each maintain independent log views of the same game.
    me_comp   = parent()
    eventlog  = me_comp.fetch(EVENTLOG_STORAGE_KEY, [])
    prev_time = me_comp.fetch(EVENTLOG_RESET_KEY, 0.0)

    # Reset detection: if song_time jumped backward by more than ~2s, the
    # game has been reset (or looped). Clear the log so the next session
    # starts with a clean panel.
    if prev_time - song_time > 2.0:
        eventlog = []
    me_comp.store(EVENTLOG_RESET_KEY, song_time)

    ctrl = _controller_comp()
    snapshot = None
    if ctrl is not None:
        try:
            snapshot = ctrl.fetch('beatsaber_snapshot', None)
        except Exception:
            snapshot = None
    _harvest_events(snapshot, song_time, eventlog)
    me_comp.store(EVENTLOG_STORAGE_KEY, eventlog)

    # --- PIL canvas ----------------------------------------------------------
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # --- Event flashes (drawn first, underneath text) ------------------------
    # Each is a full-screen tinted rect with an alpha proportional to the flag.
    # Since hit_this_frame is boolean per-cook (1 or 0), this is binary — tap
    # a Lag TOP downstream if you want the flash to fade over multiple frames.
    if miss_flash > 0.5:
        draw.rectangle([0, 0, W, H], fill=FLASH_MISS)
    if bad_flash > 0.5:
        draw.rectangle([0, 0, W, H], fill=FLASH_BAD)
    if hit_flash > 0.5:
        draw.rectangle([0, 0, W, H], fill=FLASH_HIT)

    # --- Score readout (top-right) ------------------------------------------
    score_str = f"{score:,}"
    score_font = _font(SCORE_SIZE)
    bbox = draw.textbbox((0, 0), score_str, font=score_font)
    w_score = bbox[2] - bbox[0]
    draw.text((SCORE_ANCHOR[0] - w_score, SCORE_ANCHOR[1]),
              score_str, font=score_font, fill=SCORE_COLOR)

    # --- Combo + multiplier --------------------------------------------------
    combo_str = f"{combo}  ×{mult}"
    combo_font = _font(COMBO_SIZE)
    bbox = draw.textbbox((0, 0), combo_str, font=combo_font)
    w_combo = bbox[2] - bbox[0]
    draw.text((COMBO_ANCHOR[0] - w_combo, COMBO_ANCHOR[1]),
              combo_str, font=combo_font, fill=COMBO_COLOR)

    # --- Accuracy ------------------------------------------------------------
    acc_str = f"{accuracy * 100:.1f}%"
    acc_font = _font(ACC_SIZE)
    bbox = draw.textbbox((0, 0), acc_str, font=acc_font)
    w_acc = bbox[2] - bbox[0]
    draw.text((ACC_ANCHOR[0] - w_acc, ACC_ANCHOR[1]),
              acc_str, font=acc_font, fill=ACC_COLOR)

    # --- Song time (top-left) ------------------------------------------------
    minutes = int(song_time // 60)
    seconds = song_time - minutes * 60
    time_str = f"{minutes:02d}:{seconds:05.2f}"
    time_font = _font(TIME_SIZE)
    draw.text(TIME_ANCHOR, time_str, font=time_font, fill=TIME_COLOR)

    # --- Event log (left side, scrolling, no background) --------------------
    # Render the most recent EVENTLOG_MAX_ROWS rows top-down. No filled
    # background — the text is drawn directly with per-row alpha so the
    # game render stays visible behind it. Newest entry at the top of
    # the visible window so the eye lands on the most recent event first.
    if eventlog:
        log_font = _font(EVENTLOG_FONT_SIZE)
        visible = list(reversed(eventlog[-EVENTLOG_MAX_ROWS:]))
        for i, entry in enumerate(visible):
            body, color = _format_row(entry)
            y = EVENTLOG_Y_TOP + i * EVENTLOG_ROW_HEIGHT
            # Older entries fade slightly so the most recent stands out.
            age_fade = 1.0 - (i / max(1, len(visible))) * 0.55
            faded_color = (color[0], color[1], color[2],
                           int(color[3] * age_fade))
            draw.text((EVENTLOG_X, y), body, font=log_font, fill=faded_color)

    # --- Convert PIL RGBA → numpy float32 expected by Script TOP -------------
    # PIL is row-major top-down; TD TOPs are row-major bottom-up, so flip.
    buf = np.asarray(img, dtype=np.uint8)[::-1, :, :].astype(np.float32) / 255.0
    scriptOp.copyNumpyArray(buf)
    return
