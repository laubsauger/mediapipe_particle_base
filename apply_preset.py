# apply_preset.py
# ===============
# Parameter Execute DAT callback. Applies a Look preset (presets.py) to the
# velocity_controller COMP when the `Applypreset` pulse fires or the `Preset`
# menu changes.
#
# Setup: a Parameter Execute DAT whose Callbacks DAT is this file.
#   - par.op   = parent()  (the velocity_controller COMP)
#   - par.pars = "Preset Applypreset"
#   - Value Change On + Pulse On
#
# presets.py is imported from project.folder (pushed onto sys.path), so edits
# to the preset bundles flow in live (importlib.reload).

import sys


def _apply(par):
    f = project.folder
    if f not in sys.path:
        sys.path.append(f)
    try:
        import importlib
        import presets
        importlib.reload(presets)
        comp = par.owner
        n = presets.apply(comp, comp.par.Preset.eval())
        debug("preset '%s' applied (%d pars)" % (comp.par.Preset.eval(), n))
    except Exception as e:
        debug('apply_preset error:', e)


def onValueChange(par, prev):
    if par.name == 'Preset':
        _apply(par)
    return


def onPulse(par):
    if par.name == 'Applypreset':
        _apply(par)
    return
