"""
adapters/orbbec_adapter.py
==========================

STUB — Orbbec Body Tracking SDK adapter (21-joint skeleton). Same shape as
`kinect_adapter`: map Orbbec's joints into the canonical schema, project 3D →
image-plane, normalise, mirror, fill missing canonical joints with blanks.

Orbbec joint names (typical from the Body Tracking SDK):

    head, neck,
    shoulder_left/right, elbow_left/right, hand_left/right,
    spine_chest, spine_navel,
    hip_left/right, knee_left/right, foot_left/right

Fewer joints than Kinect/MediaPipe — fewer mappable items. Everything else
stays at `(0,0,0,0)`. See `docs/sensor_contract.md` for the contract; the
mapping pattern is identical to `kinect_adapter.to_canonical`.
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from contract import NAMES, mirror_x, blank_sample


ORBBEC_TO_CANONICAL = {
    'head':           'nose',
    'shoulder_left':  'left_shoulder',
    'shoulder_right': 'right_shoulder',
    'elbow_left':     'left_elbow',
    'elbow_right':    'right_elbow',
    'hand_left':      'left_wrist',     # closest equivalent
    'hand_right':     'right_wrist',
    'hip_left':       'left_hip',
    'hip_right':      'right_hip',
    'knee_left':      'left_knee',
    'knee_right':     'right_knee',
    'foot_left':      'left_foot_index',
    'foot_right':     'right_foot_index',
}


def to_canonical(orbbec_joints, image_width, image_height,
                 mirror=True, project_3d_to_image=None):
    """Same signature as kinect_adapter.to_canonical — see that file's docstring
    for the full coordinate transform notes."""
    out = {n: blank_sample(n) for n in NAMES}
    if not orbbec_joints:
        return out
    lh = orbbec_joints.get('hip_left')
    rh = orbbec_joints.get('hip_right')
    hip_z = ((lh[2] + rh[2]) * 0.5) if (lh and rh) else 0.0
    for ob_name, canon in ORBBEC_TO_CANONICAL.items():
        j = orbbec_joints.get(ob_name)
        if not j:
            continue
        xm, ym, zm, conf = j
        if project_3d_to_image is None:
            x_norm, y_norm = xm, ym
        else:
            px, py = project_3d_to_image(xm, ym, zm)
            x_norm = px / max(1.0, image_width)
            y_norm = py / max(1.0, image_height)
        if mirror:
            x_norm = mirror_x(x_norm)
        out[canon] = (x_norm, y_norm, zm - hip_z, max(0.0, min(1.0, conf)))
    return out


if __name__ == '__main__':
    fake = {
        'hip_left':  (0.45, 0.6, 0.0, 0.9),
        'hip_right': (0.55, 0.6, 0.0, 0.9),
        'head':      (0.50, 0.2, 0.05, 0.95),
    }
    out = to_canonical(fake, 1280, 720, mirror=False)
    assert out['nose'][:2] == (0.50, 0.20) and out['nose'][3] == 0.95
    assert out['left_wrist'] == (0.0, 0.0, 0.0, 0.0)   # not provided → blank
    print("OK — orbbec_adapter: 21-joint stub mapping with blanks "
          "for missing canonical landmarks.")
