// bounds_reflect.glsl
// ===================
// GLSL POP compute shader. Reflects particles off the inside of an
// axis-aligned 3D box.
//
// Pipeline placement:
//   add_to_force → bounds_reflect → force_null
//   (replaces the old field1+mathmix1 death-cull bounds.)
//
// On the GLSL POP:
//   Attribute Class      : Point
//   Output Attributes    : "PartVel"     (only velocity — modifying P here
//                                         conflicts with Particle POP's own
//                                         integration step inside the
//                                         feedback loop and zeros particle
//                                         spawning. Letting integration
//                                         move particles back inside on
//                                         the next step is fine and feels
//                                         softer/fluid-like with damping.)
//   Initialize Output    : On (lets us read the input copy via TDIn_*)
//   Vectors 1 page:
//     uBoxMin (vec3)  ← (parent().par.Boundsminx, Boundsminy, Boundsminz)
//     uBoxMax (vec3)  ← (parent().par.Boundsmaxx, Boundsmaxy, Boundsmaxz)
//   Vectors 2 page:
//     uBounce (float) ← parent().par.Boundsbounce
//     uMargin (float) ← parent().par.Boundsmargin
//
// TD GLSL POP output convention (per TD docs):
//   attrName[id] = value;
// where attrName is the SSBO of the same name as the attribute.
// Output SSBO names collide with local var names, so we use suffix _w.

uniform vec3  uBoxMin;
uniform vec3  uBoxMax;
uniform float uBounce;
uniform float uMargin;

void main()
{
    uint id = TDIndex();
    if (id >= TDNumElements()) return;

    vec3 pos = TDIn_P().xyz;
    vec3 vel = TDIn_PartVel().xyz;

    vec3 boxMin = uBoxMin + vec3(uMargin);
    vec3 boxMax = uBoxMax - vec3(uMargin);

    // Reflect outgoing velocity at each wall the particle has crossed.
    // Don't touch position — Particle POP integrates that next step.
    if (pos.x < boxMin.x && vel.x < 0.0) vel.x = -vel.x * uBounce;
    else if (pos.x > boxMax.x && vel.x > 0.0) vel.x = -vel.x * uBounce;

    if (pos.y < boxMin.y && vel.y < 0.0) vel.y = -vel.y * uBounce;
    else if (pos.y > boxMax.y && vel.y > 0.0) vel.y = -vel.y * uBounce;

    if (pos.z < boxMin.z && vel.z < 0.0) vel.z = -vel.z * uBounce;
    else if (pos.z > boxMax.z && vel.z > 0.0) vel.z = -vel.z * uBounce;

    PartVel[id] = vel;
}
