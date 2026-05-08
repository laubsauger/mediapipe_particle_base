// bounds_reflect.glsl
// ===================
// GLSL POP compute shader. Integrates external force (fieldforce + NoiseCurl)
// into PartVel, applies per-cook velocity damping, then reflects particles
// off the inside of an axis-aligned 3D box.
//
// Why we do force integration here: TD's Particle POP does NOT automatically
// apply a feedback `PartForce` attribute to `PartVel`. The integrator only
// handles position (P += PartVel * dt) and built-in initial conditions
// (initvelocity / damping / mass). Any custom force field has to be folded
// into PartVel manually before the feedback target reads it. We do that here.
//
// Pipeline placement:
//   add_to_force → bounds_reflect → force_null
//
// On the GLSL POP:
//   Attribute Class      : Point
//   Output Attributes    : "PartVel"
//   Initialize Output    : On
//   Vectors 1 page:
//     uBoxMin     (vec3)  ← (parent().par.Boundsminx, Boundsminy, Boundsminz)
//     uBoxMax     (vec3)  ← (parent().par.Boundsmaxx, Boundsmaxy, Boundsmaxz)
//   Vectors 2 page:
//     uBounce     (float) ← parent().par.Boundsbounce
//     uMargin     (float) ← parent().par.Boundsmargin
//   Vectors 3 page (NEW — add via Vectors par on the GLSL POP):
//     uForceScale (float) ← parent().par.Forcescale  (per-cook force gain;
//                            try 0.02 — multiplies PartForce when added to
//                            PartVel; smaller = gentler push)
//     uDamping    (float) ← parent().par.Velocitydamping (0..1; fraction of
//                            velocity REMOVED per cook; 0=keep, 1=stop. Set
//                            Particle POP's own damping to 0 to avoid stacking.)
//     uMaxSpeed   (float) ← parent().par.Maxspeed (clamp on |vel| to keep
//                            integration stable; try 8.0 in box-units/sec)

uniform vec3  uBoxMin;
uniform vec3  uBoxMax;
uniform float uBounce;
uniform float uMargin;
uniform float uForceScale;
uniform float uDamping;
uniform float uMaxSpeed;

void main()
{
    uint id = TDIndex();
    if (id >= TDNumElements()) return;

    vec3 pos   = TDIn_P().xyz;
    vec3 vel   = TDIn_PartVel().xyz;
    vec3 force = TDIn_PartForce().xyz;

    // NaN/Inf guard. NaN P fed into instancing transforms or texture lookups
    // can crash the Vulkan device outright. Clamp to zero here so a single
    // bad cook can't poison the simulation.
    if (any(isnan(vel))   || any(isinf(vel)))   vel   = vec3(0.0);
    if (any(isnan(force)) || any(isinf(force))) force = vec3(0.0);
    if (any(isnan(pos))   || any(isinf(pos))) {
        vel = vec3(0.0);
        PartVel[id] = vel;
        return;
    }

    // ---- Force integration -------------------------------------------------
    // PartVel += force * uForceScale  (treat uForceScale as dt * gain)
    // Then per-cook damping: vel *= (1 - uDamping). uDamping=0 keeps all,
    // uDamping=1 zeroes velocity each cook.
    vel += force * uForceScale;
    vel *= max(0.0, 1.0 - uDamping);

    // Speed clamp. Without this, an emitter staring straight at a particle
    // for a few cooks can compound force into runaway velocity. Cap so the
    // bounds-reflect step doesn't have to work miracles.
    float spd = length(vel);
    if (spd > uMaxSpeed) vel *= (uMaxSpeed / spd);

    // ---- Wall reflection ---------------------------------------------------
    vec3 boxMin = uBoxMin + vec3(uMargin);
    vec3 boxMax = uBoxMax - vec3(uMargin);

    if (pos.x < boxMin.x && vel.x < 0.0) vel.x = -vel.x * uBounce;
    else if (pos.x > boxMax.x && vel.x > 0.0) vel.x = -vel.x * uBounce;

    if (pos.y < boxMin.y && vel.y < 0.0) vel.y = -vel.y * uBounce;
    else if (pos.y > boxMax.y && vel.y > 0.0) vel.y = -vel.y * uBounce;

    if (pos.z < boxMin.z && vel.z < 0.0) vel.z = -vel.z * uBounce;
    else if (pos.z > boxMax.z && vel.z > 0.0) vel.z = -vel.z * uBounce;

    PartVel[id] = vel;
}
