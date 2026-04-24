"""
bootstrap_velocity_controller.py
================================

One-shot builder for the sensing side of the velocity experiment. Drop this
script into a Text DAT at the *project root* (same level as the .toe, next
to any existing painting_controller), then right-click the DAT -> Run Script.

What it does
------------
1. Creates (or finds) a Base COMP named `velocity_controller`.
2. Inside it, creates three Text DATs synced to the external .py files that
   live in the project folder next to this bootstrap:
       velocity_logic          ← velocity_logic.py
       velocity_script_chop    ← velocity_script_chop.py
       install_velocity_params ← install_velocity_params.py
3. Builds the sensing network:
       in1 (In CHOP) ─► select1 (Select CHOP) ─► script1 (Script CHOP)
                                              ─► lag1 (Lag CHOP) ─► out1 (Out CHOP)
4. Wires script1's Callbacks DAT to velocity_script_chop.
5. Runs install_velocity_params to install the two custom pages (Sensing,
   Renderer) with all defaults.
6. Binds lag1's Lag 1 / Lag 2 to `parent().par.Blendtime` via expressions.
7. Sets the Select CHOP pattern to the default landmark channels.

Idempotent: re-running is safe. Existing nodes are reused and reconfigured;
custom pars never get their values reset.

Limitations
-----------
Does NOT build the particle_renderer TOX. POP attribute wiring is too fiddly
to bootstrap reliably from a script — follow the POP recipe in
velocity_controller_setup.md for the renderer side. The renderer-page pars
ARE installed here so a renderer TOX can reference them as soon as you
build it.
"""

import os

COMP_NAME = 'velocity_controller'

# Default Select CHOP pattern — matches velocity_logic.LANDMARKS and its
# optional visibility channels. Edit after bootstrap if your upstream names
# differ.
DEFAULT_SELECT_PATTERN = (
    'left_wrist:* right_wrist:* '
    'left_ankle:* right_ankle:* '
    'nose:*'
)

# External Python source files — resolved relative to the project .toe.
# Bootstrap is written next to them in the docs, so that's where it looks.
FILES = {
    'velocity_logic':          'velocity_logic.py',
    'velocity_script_chop':    'velocity_script_chop.py',
    'install_velocity_params': 'install_velocity_params.py',
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_or_create(owner, op_type, name):
    """Return existing child by name, or create one of the given type."""
    existing = owner.op(name)
    if existing is not None:
        return existing
    return owner.create(op_type, name)


def _sync_text_dat(comp, name, relpath):
    """
    Create (or adopt) a Text DAT inside `comp` and point it at an external
    file at `relpath` (resolved relative to the .toe). `syncfile=True` makes
    the DAT mirror the on-disk source — edits to velocity_logic.py on disk
    show up after reload, and nobody has to remember to paste updates into
    both places.
    """
    dat = _find_or_create(comp, textDAT, name)
    dat.par.file = relpath

    # Best-effort set of the sync/load pars across TD builds. Par names have
    # drifted a little between versions; we set whatever exists and ignore
    # the rest.
    for p_name, p_val in (('syncfile', True), ('loadonstart', True)):
        if hasattr(dat.par, p_name):
            try:
                setattr(dat.par, p_name, p_val)
            except Exception:
                pass

    # Pulse a load so the DAT pulls file contents *now*, not on next
    # project open. Try known pulse par names in order.
    for pulse_name in ('loadpulse', 'loadonstartpulse',
                       'forceloadpulse', 'reload'):
        p = getattr(dat.par, pulse_name, None)
        if p is not None:
            try:
                p.pulse()
                break
            except Exception:
                continue

    return dat


def _connect(src_op, dst_op, src_index=0, dst_index=0):
    """Connect src_op's output to dst_op's input. Idempotent."""
    # Check if already connected
    for c in dst_op.inputConnectors[dst_index].connections:
        if c.owner is src_op:
            return
    src_op.outputConnectors[src_index].connect(dst_op.inputConnectors[dst_index])


def _set_expr(par, expr):
    """Set a par to EXPRESSION mode with the given expression."""
    par.expr = expr
    par.mode = ParMode.EXPRESSION


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build():
    # Where does this bootstrap live? We need project-relative paths for the
    # Text DATs' File par to work when reopened on another machine.
    # Sanity-check that the .py siblings exist on disk.
    toe_dir = project.folder  # absolute path to the .toe's folder
    missing = [p for p in FILES.values()
               if not os.path.exists(os.path.join(toe_dir, p))]
    if missing:
        debug(f"bootstrap: expected Python source files at "
              f"{toe_dir} but missing: {missing}")
        debug("Place velocity_logic.py, velocity_script_chop.py and "
              "install_velocity_params.py next to the .toe before running.")
        return None

    # 1. Base COMP ----------------------------------------------------------
    parent_comp = parent()  # Bootstrap runs from a DAT at project root.
    comp = _find_or_create(parent_comp, baseCOMP, COMP_NAME)
    comp.color = (0.10, 0.30, 0.55)  # distinctive tint so it pops visually
    comp.nodeWidth = 150
    comp.nodeHeight = 150

    # 2. Synced Text DATs ---------------------------------------------------
    dat_logic   = _sync_text_dat(comp, 'velocity_logic',
                                 FILES['velocity_logic'])
    dat_script  = _sync_text_dat(comp, 'velocity_script_chop',
                                 FILES['velocity_script_chop'])
    dat_install = _sync_text_dat(comp, 'install_velocity_params',
                                 FILES['install_velocity_params'])

    # Arrange the DATs on the left side.
    for i, d in enumerate([dat_logic, dat_script, dat_install]):
        d.nodeX = -600
        d.nodeY = 300 - i * 150
        d.nodeWidth = 180
        d.nodeHeight = 100

    # 3. In CHOP → Select → Script → Lag → Out CHOP -------------------------
    in_chop     = _find_or_create(comp, inCHOP,     'in1')
    select_chop = _find_or_create(comp, selectCHOP, 'select1')
    script_chop = _find_or_create(comp, scriptCHOP, 'script1')
    lag_chop    = _find_or_create(comp, lagCHOP,    'lag1')
    out_chop    = _find_or_create(comp, outCHOP,    'out1')

    # Layout along x so flow reads left-to-right.
    for i, n in enumerate([in_chop, select_chop, script_chop, lag_chop, out_chop]):
        n.nodeX = -200 + i * 200
        n.nodeY = 0

    # Connect them.
    _connect(in_chop,     select_chop)
    _connect(select_chop, script_chop)
    _connect(script_chop, lag_chop)
    _connect(lag_chop,    out_chop)

    # 4. Select CHOP pattern ------------------------------------------------
    # Only set the pattern if it's still the default empty string — don't
    # clobber a manually-tuned pattern on re-run.
    if not select_chop.par.channames.eval().strip():
        select_chop.par.channames = DEFAULT_SELECT_PATTERN

    # 5. Script CHOP callbacks DAT ------------------------------------------
    # The callbacks par wants a reference to the DAT by name, relative to
    # the Script CHOP. Since both live in the same COMP, bare name works.
    script_chop.par.callbacks = dat_script.name

    # Make sure the Script CHOP is not in Time Slice mode; the callback
    # emits a single sample per cook and lets the Lag CHOP do smoothing.
    # (The callback also enforces this, but setting it here prevents a
    # first-cook warning.)
    try:
        script_chop.par.timeslice = False
    except Exception:
        pass

    # 6. Install custom pars ------------------------------------------------
    # Prefer running the installer via the synced DAT (parent() inside the
    # DAT resolves to `comp`, which is exactly what install_velocity_params
    # expects). If the DAT's contents haven't populated yet due to version
    # differences, fall back to exec-ing the file from disk with parent()
    # monkey-patched to return `comp`.
    if dat_install.text and 'appendCustomPage' in dat_install.text:
        dat_install.run()
    else:
        src_path = os.path.join(toe_dir, FILES['install_velocity_params'])
        with open(src_path, 'r') as f:
            src = f.read()
        exec(src, {
            '__name__': '__bootstrap__',
            # The installer uses parent() without arguments to reach the COMP
            # it's being installed into. Provide a shim that returns `comp`.
            'parent': lambda *a, **k: comp,
            'op': op,
            'me': me,
        })

    # 7. Bind Lag CHOP smoothing to parent.Blendtime ------------------------
    # Custom pars now exist (step 6 just installed them). Wire the Lag CHOP
    # to read them as expressions so a single knob on the Base COMP
    # controls downstream smoothing.
    _set_expr(lag_chop.par.lag1, 'parent().par.Blendtime')
    _set_expr(lag_chop.par.lag2, 'parent().par.Blendtime')

    # 8. Place the Base COMP itself so it doesn't land on top of existing
    #    painting_controller. If painting_controller exists, drop velocity
    #    a row below it.
    painting = parent_comp.op('painting_controller')
    if painting is not None:
        comp.nodeX = painting.nodeX
        comp.nodeY = painting.nodeY - 200
    else:
        # Just leave TD's default placement alone if we're on a fresh canvas.
        pass

    debug(f"bootstrap: {COMP_NAME} built. "
          f"DATs synced to {toe_dir}. "
          f"Custom pars on pages: "
          f"{[p.name for p in comp.customPages]}")
    debug(f"bootstrap: wire your MediaPipe pose CHOP into "
          f"{comp.path}/in1 and you should see channels on {comp.path}/out1.")
    return comp


# Execute at script-run time.
build()
