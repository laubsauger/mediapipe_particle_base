// p_to_uv.glsl
// ============
// Writes a Puv attribute = P scaled into [0,1] UV space, accounting for
// the box's aspect ratio. Lets field_sample (lookuptexturePOP) sample
// the velocity field at the correct location for any box width.
//
// Pipeline placement: between particle1 and field_sample.
//
// On the GLSL POP:
//   Attribute Class       : Point
//   Output Attributes     : ""        (don't modify P, PartVel, etc)
//   Create Attributes [0] : name=custom, customname=Puv, type=float, comps=3
//   Initialize Output     : On
//   Vectors:
//     uBoxMin (vec3) ← parent().par.Boundsmin*
//     uBoxMax (vec3) ← parent().par.Boundsmax*

uniform vec3 uBoxMin;
uniform vec3 uBoxMax;

void main()
{
    uint idx = TDIndex();
    if (idx >= TDNumElements()) return;

    vec3 p = TDIn_P().xyz;
    // NaN/Inf guard: if P is corrupted (e.g. MediaPipe full tracking loss
    // emits NaN landmarks → lag → emitters → POP P), substitute box centre
    // so the downstream lookuptexturePOP doesn't sample with NaN UVs and
    // hand a NaN velocity back into the integrator → Vulkan device crash.
    if (any(isnan(p)) || any(isinf(p))) p = (uBoxMin + uBoxMax) * 0.5;
    vec3 box_size = max(uBoxMax - uBoxMin, vec3(1e-6));
    vec3 uv = clamp((p - uBoxMin) / box_size, vec3(0.0), vec3(1.0));
    Puv[idx] = uv;
}
