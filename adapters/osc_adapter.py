"""
adapters/osc_adapter.py
=======================

STUB — generic OSC adapter. Lets ANY external source push canonical pose data
over the network. Useful for prototyping, a Python ML script, or routing from
software TD doesn't natively integrate.

OSC address convention (the producer sends these messages):

    /pose/<lm>/x          <float 0..1>          normalised image x
    /pose/<lm>/y          <float 0..1>          normalised image y
    /pose/<lm>/z          <float -1..1>         hip-relative depth
    /pose/visibility/<n>  <float 0..1>          per-landmark confidence

where `<lm>` is one of the 33 canonical names (see `contract.NAMES`) and `<n>`
is the integer MediaPipe index 0..32.

TD-side wrapping (planned):
  - `/project1/sensor/osc` Base COMP
  - Inside: OSC In CHOP listening on a configurable port; a Rename CHOP turns
    the slash-separated OSC addresses into the colon-separated canonical
    channel names (`/pose/left_wrist/x` → `left_wrist:x`, etc.); the COMP's
    output matches the contract.

For producers in any language, the message rate should be ≥ 30 Hz (downstream
emit/burst can lag at lower rates). Confidence is REQUIRED (without it the
visibility-gating doesn't know what to trust); default to 1.0 for fully-visible.
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from contract import NAMES, INDEX_OF


def osc_address_for(channel_name):
    """Inverse: given a canonical channel name (e.g. `left_wrist:x` or
    `visibility15`), return the OSC address pattern an external producer should
    send to. Useful for documenting/testing the OSC contract."""
    if channel_name.startswith('visibility'):
        idx = int(channel_name[len('visibility'):])
        return '/pose/visibility/%d' % idx
    lm, _, suffix = channel_name.partition(':')
    if lm in INDEX_OF and suffix in ('x', 'y', 'z'):
        return '/pose/%s/%s' % (lm, suffix)
    raise ValueError('not a canonical channel: %r' % channel_name)


def canonical_for(osc_address):
    """Inverse of osc_address_for — what canonical channel name an OSC
    address maps to. Returns None if the address isn't part of the contract."""
    parts = osc_address.strip('/').split('/')
    if len(parts) == 3 and parts[0] == 'pose' and parts[1] in INDEX_OF and parts[2] in ('x', 'y', 'z'):
        return '%s:%s' % (parts[1], parts[2])
    if len(parts) == 3 and parts[0] == 'pose' and parts[1] == 'visibility' and parts[2].isdigit():
        return 'visibility%s' % parts[2]
    return None


if __name__ == '__main__':
    assert osc_address_for('left_wrist:x') == '/pose/left_wrist/x'
    assert osc_address_for('visibility15') == '/pose/visibility/15'
    assert canonical_for('/pose/left_wrist/x') == 'left_wrist:x'
    assert canonical_for('/pose/visibility/15') == 'visibility15'
    assert canonical_for('/random/thing') is None
    # round-trip the full schema
    for n in NAMES:
        for s in ('x', 'y', 'z'):
            ch = '%s:%s' % (n, s)
            assert canonical_for(osc_address_for(ch)) == ch
    print("OK — osc_adapter: address ↔ canonical-channel round-trip for the "
          "full schema.")
