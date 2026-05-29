# particle_system_reset.py
# ========================
# Parameter Execute DAT callback for the `Reset` pulse on particle_system.
# Wipes every piece of stored state inside velocity_controller (per-cook
# accumulators, smoothed positions, logo-cycle phase, body prev positions,
# logo fade amount) and force-recompiles the GLSL POPs so a stuck attribute
# stream / NaN cascade clears.
#
# Wired by a Parameter Execute DAT at /project1/particle_system/reset_exec:
#   par.op       = ../
#   par.pars     = Reset
#   par.onpulse  = True

def _safe(fn):
    try:
        fn()
    except Exception as e:
        debug('reset: %s -> %s' % (fn.__name__ if hasattr(fn,'__name__') else fn, e))


def onPulse(par):
    ps = par.owner.parent()
    vc = ps.op('velocity_controller')
    if vc is None:
        return

    # Drop all stored per-cook state on the COMP.
    for k in ['velocity_state', 'velocity_last_t',
              'logo_cycle_state', 'logoamt',
              'Ambientaccum',
              'body_prev_p0', 'body_prev_p1', 'body_prev_p2', 'body_prev_p3']:
        try:
            vc.unstore(k)
        except Exception:
            pass

    # Reload synced DATs that hold callbacks / shaders / modules.
    for nm in ['velocity_logic', 'body_logic',
               'velocity_script_cb', 'emitters_chop_script_cb',
               'ambient_chop_script_cb', 'body_tex_script_cb',
               'logo_amt_cb', 'logo_cycle_cb',
               'bounds_reflect_compute', 'color_attr_compute',
               'p_to_uv_compute', 'body_field_pixel',
               'body_viz_pixel', 'logo_grad_pixel']:
        o = vc.op(nm) or ps.op(nm)
        if o is not None and hasattr(o.par, 'loadonstartpulse'):
            try:
                o.par.loadonstartpulse.pulse()
                o.cook(force=True)
            except Exception:
                pass

    # Force-recook the heavy GLSL POPs so they recompile with the now-present
    # input attributes (PartVel / PartForce etc.) and don't sit on a stale
    # error from a transient pool reallocation.
    for nm in ['p_to_uv', 'c_p_to_uv', 'add_to_force', 'bounds_reflect',
               'color_attr', 'particle1']:
        o = vc.op(nm)
        if o is not None:
            try:
                o.cook(force=True)
            except Exception:
                pass

    debug('particle_system reset: state cleared + GLSL POPs recooked.')
