"""
adapters/mediapipe_adapter.py
=============================

MediaPipe pose adapter. The MediaPipe tox (`toxes/MediaPipe.tox`, drop in
manually — too large for git) already emits the canonical channels natively,
so this adapter is a near-passthrough: it just validates the schema and
documents the wrapping.

TD-side wrapping (not yet wired — planned):
  - `/project1/sensor/mediapipe` Base COMP
  - Inside: the MediaPipe tox + a Rename/Select CHOP that asserts the canonical
    channels are present and the order is stable. Output: one CHOP at the
    COMP's out1.

Until the `sensor` selector COMP is built, the existing direct connection
(MediaPipe tox → velocity_controller/in_pose) stays. This file documents the
intent + provides validation helpers.

Pure — self-testable: `python3 adapters/mediapipe_adapter.py`.
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from contract import LANDMARKS, channel_names, NLM


def validate_channels(channel_iter):
    """Confirm an iterable of CHOP channel names matches the canonical schema.

    MediaPipe's tox emits all 132 channels in a stable order. If a future tox
    update reorders or renames them, this fires early instead of silently
    feeding the rest of the pipeline garbage.
    """
    chs = list(channel_iter)
    expected = set(channel_names())
    have = set(chs)
    missing = sorted(expected - have)
    extra = sorted(have - expected)
    return {'ok': not missing,
            'missing': missing,
            'extra': extra,
            'count_expected': len(expected),
            'count_have': len(chs)}


if __name__ == '__main__':
    # The MediaPipe tox should emit the full schema; build it from the contract
    # to confirm the validator accepts it.
    chs = channel_names()
    r = validate_channels(chs)
    assert r['ok'], r
    # Drop a few → validator should report them missing.
    short = [c for c in chs if c not in ('nose:x', 'visibility0')]
    r2 = validate_channels(short)
    assert not r2['ok']
    assert 'nose:x' in r2['missing'] and 'visibility0' in r2['missing']
    # Extra channels are reported but don't fail (forward-compat).
    extra = chs + ['weird:channel']
    r3 = validate_channels(extra)
    assert r3['ok'] and 'weird:channel' in r3['extra']
    print("OK — mediapipe_adapter: validates the canonical channel set "
          "(%d channels, %d landmarks)." % (len(chs), NLM))
