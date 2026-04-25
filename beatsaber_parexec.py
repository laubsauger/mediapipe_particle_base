# beatsaber_parexec.py
# ====================
# Parameter Execute DAT callback. Dispatches pulses from the Gameplay page
# (Start, Pause, Resume, Reset) to the Game singleton stored on the
# beatsaber_controller COMP.
#
# Setup:
#   - Create a Parameter Execute DAT inside beatsaber_controller.
#   - Its OPs par → leave empty (or point at parent()).
#   - Parameters par → `Start Pause Resume Reset`
#   - On Pulse toggle → On
#   - Attach this DAT as the Callbacks DAT.
#
# Extending: add more pulse pars to the Gameplay page and map them here.

STORAGE_KEY_GAME = 'beatsaber_game'


def _game():
    return parent().fetch(STORAGE_KEY_GAME, None)


def onPulse(par):
    """Called when any of the monitored pulse parameters is triggered."""
    g = _game()
    if g is None:
        debug(f"beatsaber_parexec: game not initialised yet "
              f"(pulse ignored: {par.name}).")
        return

    if par.name == 'Start':
        g.reset()
        g.start()
        debug("beatsaber: Start (map restart from t=0).")
    elif par.name == 'Pause':
        g.pause()
        debug("beatsaber: Paused.")
    elif par.name == 'Resume':
        g.resume()
        debug("beatsaber: Resumed.")
    elif par.name == 'Reset':
        g.reset()
        debug("beatsaber: Reset (cleared notes & score; not started).")
    else:
        debug(f"beatsaber_parexec: unknown pulse par {par.name!r}")


# The following are stubs required by TD's Parameter Execute DAT API.
# We only care about onPulse; the rest do nothing.
def onValueChange(par, prev): return
def onExpressionChange(par, val, prev): return
def onExportChange(par, val, prev): return
def onEnableChange(par, val, prev): return
def onModeChange(par, val, prev): return
