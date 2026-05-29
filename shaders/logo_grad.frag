// logo_grad.frag
// =============
// GLSL TOP — turns the logo image into a force + mask field for the particle
// soup. Fragment shader sampling its inputs (safe; no GLSL-POP sampler).
//
// Input 0 (sTD2DInputs[0]) = null_logo      — SHARP logo (crisp mask + snap)
// Input 1 (sTD2DInputs[1]) = logo_blur      — heavily BLURRED logo (broad reach)
//
// Output, sampled by the logo lookups at each particle's Puv → `logodata`:
//   RGB = attractor gradient (toward the bright shape). Two scales summed:
//     • ∇(blurred luma) — a smooth, FAR-reaching slope so particles are pulled
//       in from across the frame (the blur radius = how far the reach extends).
//     • ∇(sharp luma)   — steep only at the logo's own edges, for the final
//       snap onto the exact shape. Both → 0 on the bright plateau, so particles
//       SETTLE on the logo instead of overshooting.
//   A   = sharp luma (the mask) — used to brighten soup sitting on the shape.
//
// uGradamp scales the whole gradient (overall pull magnitude before Logoattract).

out vec4 fragColor;

uniform float uGradamp;

float lumaS(vec2 uv) { return dot(texture(sTD2DInputs[0], uv).rgb, vec3(0.299, 0.587, 0.114)); }
float lumaB(vec2 uv) { return dot(texture(sTD2DInputs[1], uv).rgb, vec3(0.299, 0.587, 0.114)); }

void main()
{
    vec2 uv  = vUV.st;
    vec2 pxS = 1.0 / vec2(textureSize(sTD2DInputs[0], 0));
    vec2 sS  = pxS * 2.0;
    // Broad gradient uses a WIDE fixed uv step (not 1-2 px): on a heavily
    // blurred logo this captures the smooth slope toward the bright mass over a
    // large span, so the pull reaches far across the frame (the blur radius =
    // Logoreach sets how far the blurred mass — and thus the slope — extends).
    const float bstep = 0.04;   // ~51 px at 1280 wide

    vec2 gB = vec2(lumaB(uv + vec2(bstep, 0.0)) - lumaB(uv - vec2(bstep, 0.0)),
                   lumaB(uv + vec2(0.0, bstep)) - lumaB(uv - vec2(0.0, bstep)));
    // Sharp gradient — steep only at the logo edges (the close-range snap).
    vec2 gS = vec2(lumaS(uv + vec2(sS.x, 0.0)) - lumaS(uv - vec2(sS.x, 0.0)),
                   lumaS(uv + vec2(0.0, sS.y)) - lumaS(uv - vec2(0.0, sS.y)));

    // SHARP-dominant gradient: gS (local, points to the NEAREST bright feature)
    // dominates so particles coat the shape's EDGES and distribute across the
    // letters, instead of all sliding to the global centroid. gB is a gentle
    // medium-range assist only — too much of it collapses everything to the
    // middle (the wordmark's center of mass).
    // Broad-dominant gradient — gB does the long-range gather, a SMALL gS term
    // gives just enough local definition for the shape to read once particles
    // are inside (without strongly tracing line-art / clinging to edges). Mask
    // uses the SHARP luma so the trap and vessel-charge keep the shape sharp.
    vec2 grad = (gB * 0.55) * uGradamp;   // pure broad pull — particles slip into the shape, no edge tracing
    float mask = lumaS(uv);
    fragColor = vec4(grad, 0.0, mask);
}
