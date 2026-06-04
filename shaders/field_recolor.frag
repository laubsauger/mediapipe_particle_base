// field_recolor.frag
// ==================
// GLSL TOP. The flow field (field_out / out_field) stores RGB = the raw 3D
// velocity vector, so when it's composited into the scene it shows as arbitrary
// velocity-direction COLOURS — bright green/yellow on fast hand motion. The
// environment wants blue/purple only, so here we throw away the velocity HUE
// and re-map the field's ENERGY (vector magnitude) through a blue→purple ramp
// matching the particle palette. Output intensity tracks energy so it still
// glows where the field is active, just in-palette.
//
// Input 0: out_field (RGBA, RGB = velocity, A = gaussian weight).
// Uniforms (Vectors page):
//   uGain  (float) ← how hot the field reads (energy → 0..1)
//   uColLo (vec3)  ← low-energy colour  (deep blue)
//   uColHi (vec3)  ← high-energy colour (violet / magenta)

uniform float uGain;
uniform vec3  uColLo;
uniform vec3  uColHi;

out vec4 fragColor;

void main()
{
    vec4  f = texture(sTD2DInputs[0], vUV.st);
    // energy = velocity-vector magnitude ONLY (the actual swoosh). Do NOT use the
    // alpha/gaussian-weight — it's broad across the frame and made the whole thing
    // a solid purple fill that bled through the pipeline.
    float e = clamp(length(f.rgb) * uGain, 0.0, 1.0);
    vec3  c = mix(uColLo, uColHi, e);          // in-palette blue → purple
    // Composited ADDITIVELY now: the field is its OWN coloured swoosh glow that
    // adds over the particles — black where there's no field (adds nothing),
    // bright palette colour where the swoosh is active. Keeps the swooshes
    // visible AND in-palette (vs the multiply, which only tinted existing
    // particles and lost the swoosh glow).
    fragColor = vec4(c * e, e);
}
