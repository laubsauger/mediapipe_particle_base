// depth_field.frag
// ================
// GLSL TOP — turns a depth map (single-channel; 1.0 = closest to camera) into
// a 2D/3D FLOW FIELD that augments / replaces the pose-driven velocity field.
//
// Replaces the older "wall repel" interpretation: depth is no longer a discrete
// per-particle push force. Instead the output of this shader is the SAME
// format as `velocity_field.frag` (RGB = velocity contribution, A = weight)
// and gets ADDED to the pose-driven field via a Composite TOP, so:
//   - field_sample reads pose + depth combined → bounds_reflect integrates
//     both as one force layer.
//   - depth-only operation works automatically when no pose data flows (turn
//     the pose Fieldforce down).
//   - Multi-player is free — depth covers every body in frame, not just the
//     single MediaPipe skeleton.
//   - 3D info comes "for free": depth value at uv → particle z slab → splat
//     drives particles forward/back when bodies push toward/away from camera.
//
// Inputs:
//   sTD2DInputs[0] = depth map (current frame).  R = depth (0..1).
//   sTD2DInputs[1] = depth_prev (Feedback TOP wired to one frame back).
//                    Used for temporal delta: motion = d - d_prev.
//
// Output:
//   RGB = velocity vector contribution (vx, vy, vz). XY drives flow in screen
//         space; Z is mapped from depth value so particles get pushed in z
//         where bodies are present.
//   A   = presence/weight (smoothstep on depth; used as gaussian weight,
//         matches velocity_field's contract so field_sample treats it the same).
//
// Pipeline:
//        in_depth ──┬─→ depth_prev_fb (Feedback TOP) ──→ sTD2DInputs[1]
//                   └─────────────────────────────────→ sTD2DInputs[0]
//   → depth_field (this) → field_combine Composite[Add] ← velocity_field
//                                                        ↓
//                                                   field_sample
//                                                        ↓
//                                                   bounds_reflect (uses
//                                                   `fieldforce` attribute
//                                                   as before)
//
// Pars (parent particle_system COMP, Depth page):
//   Depthfieldgain   overall scalar on the field contribution
//   Depthtempgain    weight of TEMPORAL motion (d - d_prev)
//   Depthspatgain    weight of SPATIAL gradient (-∇d, points outward from
//                    body silhouette into open space)
//   Depthpresence    smoothstep threshold below which depth contributes
//                    nothing (gates sensor floor / background noise)
//   Depthgradstep    uv step distance for ∇d sampling (larger = broader push)
//   Depthzgain       how much the depth value drives vz (3D push). 0 = flat.

out vec4 fragColor;

uniform float uFieldgain;
uniform float uTempgain;
uniform float uSpatgain;
uniform float uPresence;
uniform float uGradstep;
uniform float uZgain;

float lumaR(sampler2D t, vec2 uv) { return texture(t, uv).r; }

void main()
{
    vec2 uv = vUV.st;

    // 1. Current + previous-frame depth.
    float d      = lumaR(sTD2DInputs[0], uv);
    float d_prev = lumaR(sTD2DInputs[1], uv);

    // 2. Smooth presence gate so background noise (depth ~ 0) doesn't move
    //    anything. Acts as the weight `A` in the output so velocity_field's
    //    contract (gaussian weight in alpha) is preserved.
    float presence = smoothstep(uPresence, uPresence + 0.18, d);
    if (presence <= 0.0) {
        fragColor = vec4(0.0);
        return;
    }

    // 3. Spatial gradient: -∇d points from high (body) into low (open soup).
    //    This drives particles to FLOW AROUND the body silhouette — like the
    //    body parts the soup as it moves through.
    float s = max(uGradstep, 1.0 / float(textureSize(sTD2DInputs[0], 0).x));
    vec2 grad = vec2(
        lumaR(sTD2DInputs[0], uv + vec2(s, 0.0)) - lumaR(sTD2DInputs[0], uv - vec2(s, 0.0)),
        lumaR(sTD2DInputs[0], uv + vec2(0.0, s)) - lumaR(sTD2DInputs[0], uv - vec2(0.0, s))
    );
    vec2 spat = -grad * uSpatgain;

    // 4. Temporal delta: dd / dt = d - d_prev (per frame). Where depth is
    //    INCREASING (body moving toward camera or into frame), shove particles
    //    outward along -∇d with extra weight. Where depth is DECREASING (body
    //    moving away), shove particles toward the body to "fill in" the
    //    vacated space (sucked-along feel). Both effects = motion-aware flow.
    float dt = d - d_prev;
    vec2 temp = -grad * dt * uTempgain;

    // 5. Z (3D) component — depth value drives vz so particles get pushed in
    //    the z slab where bodies are present. Sign: positive depth (body
    //    close) → -z direction (toward camera). Particles read the body as a
    //    3D mass.
    float vz = -d * uZgain;

    vec3 flow = vec3((spat + temp) * uFieldgain, vz * uFieldgain);

    // 6. Output: matches velocity_field's contract — RGB = velocity, A = weight.
    //    Field_sample sums weighted contributions via the Composite TOP that
    //    ADDS depth_field + velocity_field, then divides by total weight at
    //    bounds_reflect's force integration step.
    fragColor = vec4(flow, presence);
}
