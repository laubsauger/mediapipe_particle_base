"""
install_painting_params.py
==========================

One-shot installer for the `painting_controller` Base COMP's custom
parameter page. Run this once, from a Text DAT *inside* the Base COMP:

    Right-click DAT -> Run Script

It creates (or leaves intact) every parameter the Script CHOP callback
reads. Idempotent: re-running it won't duplicate pars or reset values
that you've already tuned.

After it finishes, you can delete this DAT — the pars live on the COMP
itself, independent of this script.
"""

comp = parent()  # The Base COMP this DAT lives in.

# Find or create the Painting page.
page = None
for p in comp.customPages:
    if p.name == 'Painting':
        page = p
        break
if page is None:
    page = comp.appendCustomPage('Painting')


def _has(name):
    return getattr(comp.par, name, None) is not None


def add_float(name, label, default, rmin, rmax,
              clamp_min=True, clamp_max=True):
    if _has(name):
        return
    pg = page.appendFloat(name, label=label)
    p = pg[0]
    p.default = default
    p.val = default
    p.normMin = rmin
    p.normMax = rmax
    p.clampMin = clamp_min
    p.clampMax = clamp_max


def add_toggle(name, label, default):
    if _has(name):
        return
    pg = page.appendToggle(name, label=label)
    p = pg[0]
    p.default = 1 if default else 0
    p.val = p.default


def add_menu(name, label, items, labels, default):
    if _has(name):
        return
    pg = page.appendMenu(name, label=label)
    p = pg[0]
    p.menuNames = items
    p.menuLabels = labels
    p.default = default
    p.val = default


add_float('Bordertop',           'Border Top',           0.15, 0.0, 0.5)
add_float('Borderbottom',        'Border Bottom',        0.15, 0.0, 0.5)
add_float('Borderleft',          'Border Left',          0.15, 0.0, 0.5)
add_float('Borderright',         'Border Right',         0.15, 0.0, 0.5)
add_float('Visibilitythreshold', 'Visibility Threshold', 0.5,  0.0, 1.0)

add_menu('Wristmode', 'Wrist Logic',
         ['or', 'and'],
         ['Either wrist (OR)', 'Both wrists (AND)'],
         'or')

add_toggle('Blendscene', 'Analog Scene Crossfade', True)

# Aspect pars: 0 disables correction. Don't clamp the max so you can type
# any ratio; normMax 4 is just the slider range.
add_float('Sourceaspect', 'Source Aspect (w/h, 0=off)',
          0.0, 0.0, 4.0, clamp_max=False)
add_float('Viewaspect',   'View Aspect (w/h, 0=off)',
          0.0, 0.0, 4.0, clamp_max=False)

add_float('Blendtime', 'Blend Time (s)', 0.75, 0.0, 3.0, clamp_max=False)

print("painting_controller: Painting page installed ({} params).".format(
    len([pr for pr in comp.customPars if pr.page.name == 'Painting'])))
