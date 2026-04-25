# beatsaber_ui_top.py
# ===================
# Script TOP callback — renders the UI overlay (score, combo, multiplier,
# accuracy, event flashes, scrolling event log) as an RGBA image.
# Composites over the main render downstream.
#
# Uses PIL (comes bundled with TD's Python). If PIL is missing for any
# reason, the callback degrades to a magenta-tinted "PIL MISSING" buffer
# instead of being silently transparent — so you can SEE that the
# callback is firing but PIL needs installing.
#
# Paste into a Text DAT called `beatsaber_ui_top` inside the
# beatsaber_renderer COMP, and attach as the Callbacks DAT of a
# Script TOP called `ui_top`.
#
# Reads from a parent par `Controller` → pointer to beatsaber_controller
# COMP, same as beatsaber_saber_sop.py. Or falls back to the relative
# path ../beatsaber_controller/game_tick.
#
# Debugging "nothing renders":
#   - Look at the textport on first cook for a one-time startup print
#     showing PIL availability + target resolution.
#   - Right-click the Script TOP ▸ Open Viewer. The image we draw will
#     show up there even if the downstream Composite TOP isn't wired.
#   - If you see a magenta "PIL MISSING" tint, install PIL via TD's
#     Python: pip install Pillow --break-system-packages (or whatever
#     your TD install uses).
#   - If the viewer is solid black, the Script TOP's pixel format may
#     not accept our float32 buffer. Set the Script TOP's "Pixel
#     Format" to RGBA32Float on the Common page.
#   - If the viewer is the wrong size, set Resolution → "Custom
#     Resolution" with width/height of your choice (we draw at whatever
#     scriptOp.width / scriptOp.height are).

import numpy as np

try:
    from PIL import Image, ImageDraw, ImageFont
    _HAVE_PIL = True
except Exception as _pil_err:
    _HAVE_PIL = False
    _pil_err_msg = str(_pil_err)
else:
    _pil_err_msg = ''

# One-time startup announcement to the textport so the user can
# confirm the callback is wired and see what PIL/numpy state we're in.
_STARTUP_LOGGED = False


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

# Event flash tints — full-screen colour blasts that fade over
# FLASH_FADE_S seconds. Tuple is (R, G, B, A_at_peak); the rendered
# alpha is A_at_peak * (1 - age/FLASH_FADE_S), so the flash is full
# strength on the cook the event fires and ramps to 0 over 0.3s.
FLASH_HIT      = (0, 255, 120, 110)     # green (kept subtle so it doesn't block gameplay view)
FLASH_BAD      = (255, 80, 80, 150)     # red (more prominent — it's feedback you need to see)
FLASH_MISS     = (255, 255, 255, 70)    # dim white flash
FLASH_FADE_S   = 0.30                   # seconds for a flash to fade to fully transparent

# Persistent per-event-kind timestamps so each flash fades independently
# (a hit followed by a miss within 300 ms shows BOTH fades layered).
FLASH_KEY_HIT_T  = 'beatsaber_flash_hit_t'
FLASH_KEY_BAD_T  = 'beatsaber_flash_bad_t'
FLASH_KEY_MISS_T = 'beatsaber_flash_miss_t'


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


def _emit_buffer(scriptOp, buf):
    """copyNumpyArray with friendly diagnostics on failure."""
    try:
        scriptOp.copyNumpyArray(buf)
    except Exception as e:
        # The most common cause is the TOP's pixel format not matching
        # the buffer dtype. Log once so the user can see what to do.
        try:
            debug(f"beatsaber_ui_top: copyNumpyArray failed ({e}). "
                  f"Set the Script TOP's pixel format to RGBA32Float "
                  f"on the Common page.")
        except Exception:
            pass


def onCook(scriptOp):
    global _STARTUP_LOGGED

    # Use the Script TOP's actual configured resolution so we draw at
    # whatever the user set. Falls back to 1920x1080 if width/height
    # haven't been set (Resolution = "Custom" not enabled, etc).
    W = int(getattr(scriptOp, 'width', 0))  or DEFAULT_W
    H = int(getattr(scriptOp, 'height', 0)) or DEFAULT_H

    # Layout was authored at 1920x1080 — scale every pixel position by
    # the target resolution so the same script works at 1280x720,
    # 3840x2160, etc.
    sx = W / float(DEFAULT_W)
    sy = H / float(DEFAULT_H)

    if not _STARTUP_LOGGED:
        _STARTUP_LOGGED = True
        try:
            debug(f"beatsaber_ui_top: cooking. PIL={_HAVE_PIL} "
                  f"(error: {_pil_err_msg!r}); resolution={W}x{H}; "
                  f"controller={_controller_comp().path if _controller_comp() else 'NONE'}")
        except Exception:
            pass

    if not _HAVE_PIL:
        # Transparent buffer (no visible tint — keeps the rendered
        # scene clean). PIL absence is reported via the textport
        # warning above plus a periodic re-warning below.
        global _PIL_WARN_COUNT
        try:
            _PIL_WARN_COUNT
        except NameError:
            _PIL_WARN_COUNT = 0
        # Re-warn every 600 cooks (~10s at 60 fps) so the user
        # eventually notices in the textport that PIL needs installing.
        if (_PIL_WARN_COUNT % 600) == 0:
            try:
                debug(f"beatsaber_ui_top: PIL is not importable — UI overlay disabled. "
                      f"Install Pillow into TD's Python: "
                      f"`pip install Pillow --break-system-packages` "
                      f"(in the Python that TD uses; check Edit ▸ Preferences ▸ Python). "
                      f"Underlying import error: {_pil_err_msg!r}")
            except Exception:
                pass
        _PIL_WARN_COUNT += 1
        _emit_buffer(scriptOp, np.zeros((H, W, 4), dtype=np.float32))
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
    # Per-kind timestamps with a 0.3 s linear fade from full alpha to 0.
    # Each kind's last-event time is held in renderer-COMP storage so
    # the fade survives across cooks. A hit followed quickly by a miss
    # shows both fading independently and additively (Composite TOP
    # 'over' downstream handles layering with the scene).
    last_hit_t  = me_comp.fetch(FLASH_KEY_HIT_T,  -1.0)
    last_bad_t  = me_comp.fetch(FLASH_KEY_BAD_T,  -1.0)
    last_miss_t = me_comp.fetch(FLASH_KEY_MISS_T, -1.0)
    if hit_flash > 0.5:
        last_hit_t = song_time
        me_comp.store(FLASH_KEY_HIT_T, last_hit_t)
    if bad_flash > 0.5:
        last_bad_t = song_time
        me_comp.store(FLASH_KEY_BAD_T, last_bad_t)
    if miss_flash > 0.5:
        last_miss_t = song_time
        me_comp.store(FLASH_KEY_MISS_T, last_miss_t)

    def _draw_fading_flash(last_t, base_color):
        """Draw a full-screen tinted rect whose alpha ramps from
        base_color[3] at t=last_t down to 0 over FLASH_FADE_S seconds."""
        if last_t < 0:
            return
        age = song_time - last_t
        if age < 0 or age >= FLASH_FADE_S:
            return
        fade = 1.0 - (age / FLASH_FADE_S)
        a = int(base_color[3] * fade)
        if a <= 0:
            return
        draw.rectangle([0, 0, W, H],
                       fill=(base_color[0], base_color[1], base_color[2], a))

    # Misses first (dimmest), then bad cuts, then hits — so a hit
    # within the same cook visibly stacks on top of any older miss
    # tint. PIL alpha-blends each rectangle additively.
    _draw_fading_flash(last_miss_t, FLASH_MISS)
    _draw_fading_flash(last_bad_t,  FLASH_BAD)
    _draw_fading_flash(last_hit_t,  FLASH_HIT)

    # Helper that scales (x, y) tuples authored at the 1920x1080 reference.
    def _xy(p):
        return (int(p[0] * sx), int(p[1] * sy))

    def _font_scaled(base):
        return _font(max(8, int(base * min(sx, sy))))

    # --- Score readout (top-right) ------------------------------------------
    score_str = f"{score:,}"
    score_font = _font_scaled(SCORE_SIZE)
    bbox = draw.textbbox((0, 0), score_str, font=score_font)
    w_score = bbox[2] - bbox[0]
    sx_anchor, sy_anchor = _xy(SCORE_ANCHOR)
    draw.text((sx_anchor - w_score, sy_anchor),
              score_str, font=score_font, fill=SCORE_COLOR)

    # --- Combo + multiplier --------------------------------------------------
    combo_str = f"{combo}  x{mult}"  # plain ASCII × so PIL default font draws it
    combo_font = _font_scaled(COMBO_SIZE)
    bbox = draw.textbbox((0, 0), combo_str, font=combo_font)
    w_combo = bbox[2] - bbox[0]
    cx_anchor, cy_anchor = _xy(COMBO_ANCHOR)
    draw.text((cx_anchor - w_combo, cy_anchor),
              combo_str, font=combo_font, fill=COMBO_COLOR)

    # --- Accuracy ------------------------------------------------------------
    acc_str = f"{accuracy * 100:.1f}%"
    acc_font = _font_scaled(ACC_SIZE)
    bbox = draw.textbbox((0, 0), acc_str, font=acc_font)
    w_acc = bbox[2] - bbox[0]
    ax_anchor, ay_anchor = _xy(ACC_ANCHOR)
    draw.text((ax_anchor - w_acc, ay_anchor),
              acc_str, font=acc_font, fill=ACC_COLOR)

    # --- Song time (top-left) ------------------------------------------------
    minutes = int(song_time // 60)
    seconds = song_time - minutes * 60
    time_str = f"{minutes:02d}:{seconds:05.2f}"
    time_font = _font_scaled(TIME_SIZE)
    draw.text(_xy(TIME_ANCHOR), time_str, font=time_font, fill=TIME_COLOR)

    # --- Event log (left side, scrolling, no background) --------------------
    # Render the most recent EVENTLOG_MAX_ROWS rows top-down. No filled
    # background — the text is drawn directly with per-row alpha so the
    # game render stays visible behind it. Newest entry at the top of
    # the visible window so the eye lands on the most recent event first.
    if eventlog:
        log_font = _font_scaled(EVENTLOG_FONT_SIZE)
        log_x = int(EVENTLOG_X * sx)
        log_y0 = int(EVENTLOG_Y_TOP * sy)
        log_dy = int(EVENTLOG_ROW_HEIGHT * sy)
        visible = list(reversed(eventlog[-EVENTLOG_MAX_ROWS:]))
        for i, entry in enumerate(visible):
            body, color = _format_row(entry)
            y = log_y0 + i * log_dy
            # Older entries fade slightly so the most recent stands out.
            age_fade = 1.0 - (i / max(1, len(visible))) * 0.55
            faded_color = (color[0], color[1], color[2],
                           int(color[3] * age_fade))
            draw.text((log_x, y), body, font=log_font, fill=faded_color)

    # --- Convert PIL RGBA → numpy float32 expected by Script TOP -------------
    # PIL is row-major top-down; TD TOPs are row-major bottom-up, so flip.
    buf = np.asarray(img, dtype=np.uint8)[::-1, :, :].astype(np.float32) / 255.0
    _emit_buffer(scriptOp, buf)
    return
