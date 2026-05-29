"""
adapters/kinect_adapter.py
==========================

STUB — Kinect Azure body-tracking adapter. Maps Kinect's 32-joint skeleton
into the canonical schema (`adapters/contract.py`). Not wired yet — when you
have Kinect input flowing, fill in `to_canonical()` and build the matching
TD COMP at `/project1/sensor/kinect`.

Kinect Azure → MediaPipe Pose mapping (the obvious joints):

    Kinect joint            → canonical name
    ------------------------------------------------
    head                    → nose                 (approximate)
    nose                    → nose                 (Kinect has it now)
    ear_left/right          → left_ear / right_ear
    eye_left/right          → left_eye / right_eye
    shoulder_left/right     → left_shoulder / right_shoulder
    elbow_left/right        → left_elbow / right_elbow
    wrist_left/right        → left_wrist / right_wrist
    hand_left/right         → left_index / right_index   (close enough)
    hip_left/right          → left_hip / right_hip
    knee_left/right         → left_knee / right_knee
    ankle_left/right        → left_ankle / right_ankle
    foot_left/right         → left_foot_index / right_foot_index

Joints Kinect tracks that have NO MediaPipe counterpart (spine_chest,
spine_navel, neck, clavicle_left/right, etc.) — drop.
Joints MediaPipe has but Kinect lacks (eye_inner/outer, mouth_left/right,
pinky/thumb/index detail, heel) — emit `(0,0,0,0)` via contract.blank_sample().

Coordinate transform:
  Kinect output = 3D metres in the depth-camera frame.
  Project to the camera image plane (use the SDK's intrinsics) → pixel (px, py).
  Normalise:  x = px / image_width,  y = py / image_height.
  Mirror x if the source isn't selfie-cammed: x = 1 - x.
  z = (joint_z_metres - hip_midpoint_z_metres) → keep raw, downstream tames it.
  visibility = clamp(kinect_confidence, 0, 1).
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from contract import LANDMARKS, INDEX_OF, NAMES, mirror_x, blank_sample


# Direct name → canonical-name map (the ones Kinect can supply).
KINECT_TO_CANONICAL = {
    'head':            'nose',       # head joint approximates nose for our use
    'ear_left':        'left_ear',
    'ear_right':       'right_ear',
    'eye_left':        'left_eye',
    'eye_right':       'right_eye',
    'shoulder_left':   'left_shoulder',
    'shoulder_right':  'right_shoulder',
    'elbow_left':      'left_elbow',
    'elbow_right':     'right_elbow',
    'wrist_left':      'left_wrist',
    'wrist_right':     'right_wrist',
    'hand_left':       'left_index',
    'hand_right':      'right_index',
    'hip_left':        'left_hip',
    'hip_right':       'right_hip',
    'knee_left':       'left_knee',
    'knee_right':      'right_knee',
    'ankle_left':      'left_ankle',
    'ankle_right':     'right_ankle',
    'foot_left':       'left_foot_index',
    'foot_right':      'right_foot_index',
}


def to_canonical(kinect_joints, image_width, image_height,
                 mirror=True, project_3d_to_image=None):
    """Map a dict of Kinect joints into canonical {name: (x, y, z, vis)}.

    kinect_joints : {kinect_name: (x_m, y_m, z_m, confidence)} — 3D metres.
    project_3d_to_image : optional callable (x_m, y_m, z_m) → (px, py).
                          REQUIRED for a real Kinect feed; tests pass an
                          identity stub.
    Returns: {canonical_name: (x_norm, y_norm, z_rel, visibility)} for all 33
             canonical landmarks (missing ones get blank_sample).
    """
    out = {n: blank_sample(n) for n in NAMES}
    if not kinect_joints:
        return out

    # find a hip midpoint for relative z (Kinect-z is camera-relative; ours is
    # hip-relative to match MediaPipe).
    lh = kinect_joints.get('hip_left')
    rh = kinect_joints.get('hip_right')
    hip_z = 0.0
    if lh and rh:
        hip_z = (lh[2] + rh[2]) * 0.5

    for kname, canon in KINECT_TO_CANONICAL.items():
        j = kinect_joints.get(kname)
        if not j:
            continue
        xm, ym, zm, conf = j
        if project_3d_to_image is None:
            # tests / no SDK — assume already normalised
            x_norm, y_norm = xm, ym
        else:
            px, py = project_3d_to_image(xm, ym, zm)
            x_norm = px / max(1.0, image_width)
            y_norm = py / max(1.0, image_height)
        if mirror:
            x_norm = mirror_x(x_norm)
        z_rel = zm - hip_z
        vis = max(0.0, min(1.0, conf))
        out[canon] = (x_norm, y_norm, z_rel, vis)
    return out


if __name__ == '__main__':
    # Synthetic two-joint Kinect input (pre-normalised for the test).
    fake = {
        'hip_left':       (0.42, 0.50, 0.0, 0.95),
        'hip_right':      (0.58, 0.50, 0.0, 0.92),
        'wrist_left':     (0.30, 0.40, 0.10, 0.80),
    }
    canon = to_canonical(fake, 1280, 720, mirror=False)
    # mapped joints come through
    assert canon['left_hip'] == (0.42, 0.50, 0.0, 0.95)
    # left_wrist: z relative to hip midpoint (0.0) → 0.10 stays
    assert canon['left_wrist'][:2] == (0.30, 0.40)
    assert canon['left_wrist'][3] == 0.80
    # unmapped joints stay blank
    assert canon['nose'] == (0.0, 0.0, 0.0, 0.0)
    # mirror flips x
    canon_m = to_canonical(fake, 1280, 720, mirror=True)
    assert abs(canon_m['left_hip'][0] - (1.0 - 0.42)) < 1e-9
    print("OK — kinect_adapter: stub mapping + mirror + blank-fill for "
          "%d canonical joints." % len(NAMES))
