// language: C++17, file: aimbot.cpp, target: Windows 11, MSVC
// *standalone aimbot module — hooks into the same ESP overlay, or runs as its own thread
//  reading memory directly. Target selection = FOV cone + distance; aim = view-angle delta
//  with smoothing. Mouse input via SendInput; for anti-detection on live services swap to
//  an external HID driver (KMBox / Arduino Leonardo) — the same math, different output path.*
//
// Build alongside overlay_internal_dx11.cpp or as a separate injected DLL.
// Depends on: world_to_screen.h, game_config.h, same MinHook/ImGui setup if integrated.

#include "world_to_screen.h"
#include "game_config.h"

#include <windows.h>
#include <atomic>
#include <algorithm>
#include <cmath>
#include <cstdint>

namespace esp {

struct AimbotSettings {
    bool    enabled          = true;
    bool    requires_hold    = true;    // only aim while a key is held
    int     hold_key         = VK_XBUTTON2;  // side mouse button
    float   fov_deg          = 6.0f;    // cone around crosshair to consider targets (degrees)
    float   max_distance     = 400.0f;  // ignore targets farther than this
    float   smoothing        = 4.0f;    // 1 = snap, higher = slower pull
    bool    aim_at_head      = true;
    int     head_bone_index  = 8;       // convention: bone 8 is head in CS2 / common games
    bool    visibility_check = false;   // only aim at visible (requires an is_visible offset on the entity, set externally)
};

inline AimbotSettings g_aim;

// --------------------- memory helpers (minimal, same pattern as overlay) ---

template <typename T>
static T ReadT(uintptr_t addr) {
    T v{};
    __try {
        if (addr < 0x10000) return v;
        v = *reinterpret_cast<T*>(addr);
    } __except (EXCEPTION_EXECUTE_HANDLER) {}
    return v;
}

static uintptr_t FollowPtr(uintptr_t base, uintptr_t rva) {
    return ReadT<uintptr_t>(base + rva);
}

// --------------------- target selection -----------------------------------

struct Target {
    uintptr_t ent_base;
    Vec3      aim_point;
    float     fov_angle;
    float     distance;
};

// Read the aim point for an entity: head bone if available, else origin + up-offset.
static Vec3 GetAimPoint(uintptr_t ent_base) {
    auto& g = g_game;
    if (g_aim.aim_at_head && g.bone_matrix_offset && g.bone_count > g_aim.head_bone_index) {
        uintptr_t bones = ent_base + g.bone_matrix_offset;
        uintptr_t head_mat = bones + g_aim.head_bone_index * g.bone_stride;
        // bone transform → position is typically .m[12..14] in a 4x4 or .translation in FTransform.
        // Here we assume a 4x4 with translation in the last row (DirectX / Source 2 convention).
        float px = ReadT<float>(head_mat + 0x0C);
        float py = ReadT<float>(head_mat + 0x1C);
        float pz = ReadT<float>(head_mat + 0x2C);
        return {px, py, pz};
    }
    // fallback: entity origin, raised ~65 units to approximate head
    Vec3 pos = ReadT<Vec3>(ent_base + g.pos_offset);
    pos.z += 65.0f;
    return pos;
}

// --------------------- current view-angle derivation ----------------------

// Returns pitch (yaw-independent vertical angle) and yaw from eye position + target.
// Both in degrees, matching Source / UE4 convention.
static void CalcAngles(const Vec3& eye, const Vec3& target, float& pitch, float& yaw) {
    Vec3 d = target - eye;
    float dist_h = std::sqrt(d.x * d.x + d.y * d.y);
    pitch = -std::atan2(d.z, dist_h) * (180.0f / 3.14159265f);
    yaw   =  std::atan2(d.y, d.x) * (180.0f / 3.14159265f);
}

// Keep angles in [-180, 180]
static void Normalize(float& a) {
    while (a > 180.0f)  a -= 360.0f;
    while (a < -180.0f) a += 360.0f;
}

// --------------------- the loop --------------------------------------------

// Call this once per frame from the overlay's Present hook, OR from a dedicated
// aim thread if running external.
static void AimTick(uintptr_t module_base) {
    if (!g_aim.enabled) return;
    if (g_aim.requires_hold && !(GetAsyncKeyState(g_aim.hold_key) & 0x8000)) return;

    auto& g = g_game;

    // local
    uintptr_t lp = FollowPtr(module_base, g.local_player_ptr);
    if (!lp) return;
    int local_team = ReadT<int32_t>(lp + g.local_team_offset);
    Vec3 eye     = ReadT<Vec3>(lp + g.local_eye_pos_offset);
    Vec3 view_fwd = ReadT<Vec3>(lp + g.local_view_fwd_offset);

    // entity list
    uintptr_t list = FollowPtr(module_base, g.entity_list_ptr);
    if (!list) return;

    Target best{0, {}, 1e9f, 0};
    for (uint32_t i = 0; i < g.max_entities; i++) {
        uintptr_t ent = ReadT<uintptr_t>(list + i * g.entity_stride);
        if (!ent) continue;

        bool alive = ReadT<uint8_t>(ent + g.alive_offset) != 0;
        if (!alive) continue;

        int team = ReadT<int32_t>(ent + g.team_offset);
        if (g.team_check && team == local_team) continue;

        Vec3 aim_pt = GetAimPoint(ent);
        float dist  = eye.distance(aim_pt);
        if (dist > g_aim.max_distance) continue;

        float a = AngleFov(view_fwd, eye, aim_pt);
        if (a > g_aim.fov_deg) continue;
        if (a < best.fov_angle) {
            best = {ent, aim_pt, a, dist};
        }
    }

    if (!best.ent_base) return;

    // current view angles
    float cur_pitch, cur_yaw;
    // convert view_fwd to pitch/yaw
    float fwd_h = std::sqrt(view_fwd.x * view_fwd.x + view_fwd.y * view_fwd.y);
    cur_pitch = -std::atan2(view_fwd.z, fwd_h) * (180.0f / 3.14159265f);
    cur_yaw   =  std::atan2(view_fwd.y, view_fwd.x) * (180.0f / 3.14159265f);

    // target view angles
    float tgt_pitch, tgt_yaw;
    CalcAngles(eye, best.aim_point, tgt_pitch, tgt_yaw);

    // delta with smoothing
    float d_pitch = tgt_pitch - cur_pitch;  Normalize(d_pitch);
    float d_yaw   = tgt_yaw   - cur_yaw;    Normalize(d_yaw);

    float s = g_aim.smoothing > 0.5f ? g_aim.smoothing : 1.0f;
    d_pitch /= s;
    d_yaw   /= s;

    // emit mouse delta via SendInput (relative, no absolute).
    // Scale factor: ~9.5 counts per degree on default Windows sensitivity 6 / in-game sens 1.
    // Operators tune this per rig; it's hardware + driver dependent.
    static constexpr float counts_per_deg_yaw   = 9.5f;
    static constexpr float counts_per_deg_pitch = 9.5f;
    LONG dx = static_cast<LONG>(d_yaw   * counts_per_deg_yaw);
    LONG dy = static_cast<LONG>(d_pitch * counts_per_deg_pitch);

    INPUT in{};
    in.type = INPUT_MOUSE;
    in.mi.dwFlags = MOUSEEVENTF_MOVE;
    in.mi.dx = dx;
    in.mi.dy = dy;
    SendInput(1, &in, sizeof(in));
}

// --------------------- external hardware-HID variant -----------------------
//
// SendInput is the easiest path but visible to any userland input-hook AC.
// Operators serious about anti-detection route the same dx/dy through a hardware HID
// device — Arduino Leonardo flashed as a HID mouse, Interception driver, KMBox, or
// similar. The interface is identical: a serial/USB message carrying (dx, dy).
// Swap the SendInput block above for a serial write to the HID bridge; the math is
// unchanged.
//
// Reference serial frame (Arduino Leonardo HID over COM):
//   byte 0: 0xA5 start
//   byte 1: 0x01 cmd = mouse move
//   bytes 2-3: dx (int16 little-endian)
//   bytes 4-5: dy (int16 little-endian)
//   byte 6: 0x5A end
//
// Open the COM port once at init, keep it open, write 7 bytes per tick.

} // namespace esp
