// sprite_disc.frag
// ================
// GLSL TOP — the particle sprite: a centered, soft round disc. Replaces the
// Ramp TOP radial (TD's radial normalization rendered it asymmetric / mostly
// white, so the textured quads read as lit rectangles). Fragment shader, no
// sampler dependency — crash-safe.
//
// particle_mat (additive Constant MAT) samples this as its color map at the
// quad uv. RGB falls off to 0 toward the edge so the quad corners add nothing
// (additive) → the quad reads as a ROUND mote, not a square. Solid-ish core,
// short falloff = crisp but round; the dark surround also keeps the additive
// pile-up from washing to white.
//
// d: 0 at center, 1.0 at an edge midpoint, ~1.414 at a corner. Disc fully
// black by d≈0.8 so even the corners (1.414) are safely 0.

out vec4 fragColor;

void main()
{
    vec2  p = vUV.st - 0.5;
    float d = length(p) * 2.0;
    float a = 1.0 - smoothstep(0.45, 0.80, d);   // solid core → crisp round edge
    fragColor = vec4(vec3(a), a);
}
