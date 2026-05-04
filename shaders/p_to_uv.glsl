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
    vec3 box_size = max(uBoxMax - uBoxMin, vec3(1e-6));
    vec3 uv = (p - uBoxMin) / box_size;
    Puv[idx] = uv;
}
