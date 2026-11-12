// language: C++17, header-only, target: Windows 11, MSVC
// *standard column-major view-projection math — matches UE4/Source/Unity default conventions*
// The view matrix layout follows the convention most engines dump — row 0..3 in memory is
// the first row of the matrix as the engine stores it. Some engines are column-major —
// transpose the input before calling if yours is.
#pragma once

#include <cmath>
#include <cstdint>

namespace esp {

struct Vec2 {
    float x = 0, y = 0;
    bool valid() const { return !(std::isnan(x) || std::isnan(y) ||
                                   std::isinf(x) || std::isinf(y)); }
};

struct Vec3 {
    float x = 0, y = 0, z = 0;
    Vec3 operator+(const Vec3& o) const { return {x + o.x, y + o.y, z + o.z}; }
    Vec3 operator-(const Vec3& o) const { return {x - o.x, y - o.y, z - o.z}; }
    Vec3 operator*(float s) const { return {x * s, y * s, z * s}; }
    float dot(const Vec3& o) const { return x * o.x + y * o.y + z * o.z; }
    float length() const { return std::sqrt(x * x + y * y + z * z); }
    Vec3 normalize() const {
        float l = length();
        return l > 1e-6f ? Vec3{x / l, y / l, z / l} : Vec3{};
    }
    float distance(const Vec3& o) const { return (o - *this).length(); }
};

// 4x4 matrix in row-major memory layout (engines typically dump this way).
// Element access: m[row][col].
struct Mat4 {
    float m[4][4];
};

// World-to-screen transform.
//
// view_proj:       the composed view*projection matrix (4x4) for the local camera this frame.
// world:           world-space position of the entity being drawn.
// screen_w/h:      backbuffer dimensions in pixels.
// out:             screen coordinates (only valid if the function returns true).
//
// Returns false if the point is behind the camera (w <= 0), clipped off-screen, or produces
// a NaN — caller skips drawing those entities.
inline bool WorldToScreen(const Mat4& view_proj, const Vec3& world,
                           float screen_w, float screen_h, Vec2& out) {
    // clip = M * [x, y, z, 1]
    float clip_x = view_proj.m[0][0] * world.x + view_proj.m[0][1] * world.y +
                   view_proj.m[0][2] * world.z + view_proj.m[0][3];
    float clip_y = view_proj.m[1][0] * world.x + view_proj.m[1][1] * world.y +
                   view_proj.m[1][2] * world.z + view_proj.m[1][3];
    float clip_w = view_proj.m[3][0] * world.x + view_proj.m[3][1] * world.y +
                   view_proj.m[3][2] * world.z + view_proj.m[3][3];

    if (clip_w < 0.001f) return false;

    float ndc_x = clip_x / clip_w;
    float ndc_y = clip_y / clip_w;

    // NDC -> screen. Y flipped because DirectX screen Y grows downward.
    out.x = (screen_w * 0.5f) + (ndc_x * screen_w * 0.5f);
    out.y = (screen_h * 0.5f) - (ndc_y * screen_h * 0.5f);

    if (!out.valid()) return false;
    if (out.x < -1000 || out.x > screen_w + 1000) return false;
    if (out.y < -1000 || out.y > screen_h + 1000) return false;
    return true;
}

// Convenience: bounding box corners for an entity at `origin` with half-size (hw, hh, hd).
// Returns true + fills `min` / `max` screen-space extents if at least one corner projects.
// Use this for drawing 2D boxes around 3D entities.
inline bool WorldBoundingBox(const Mat4& vp, const Vec3& origin,
                              float hw, float hh, float hd,
                              float screen_w, float screen_h,
                              Vec2& min_out, Vec2& max_out) {
    Vec3 corners[8] = {
        {origin.x - hw, origin.y - hh, origin.z - hd},
        {origin.x + hw, origin.y - hh, origin.z - hd},
        {origin.x - hw, origin.y + hh, origin.z - hd},
        {origin.x + hw, origin.y + hh, origin.z - hd},
        {origin.x - hw, origin.y - hh, origin.z + hd},
        {origin.x + hw, origin.y - hh, origin.z + hd},
        {origin.x - hw, origin.y + hh, origin.z + hd},
        {origin.x + hw, origin.y + hh, origin.z + hd},
    };
    bool any = false;
    float min_x = 1e9f, min_y = 1e9f, max_x = -1e9f, max_y = -1e9f;
    for (int i = 0; i < 8; i++) {
        Vec2 s;
        if (!WorldToScreen(vp, corners[i], screen_w, screen_h, s)) continue;
        any = true;
        if (s.x < min_x) min_x = s.x;
        if (s.y < min_y) min_y = s.y;
        if (s.x > max_x) max_x = s.x;
        if (s.y > max_y) max_y = s.y;
    }
    if (!any) return false;
    min_out = {min_x, min_y};
    max_out = {max_x, max_y};
    return true;
}

// FOV angle between the local view direction and the vector to a target.
// Returns degrees. Smaller = target more directly in front of the camera.
inline float AngleFov(const Vec3& view_forward, const Vec3& eye_pos, const Vec3& target) {
    Vec3 to_target = (target - eye_pos).normalize();
    Vec3 fwd = view_forward.normalize();
    float d = fwd.dot(to_target);
    if (d > 1.0f) d = 1.0f;
    if (d < -1.0f) d = -1.0f;
    return std::acos(d) * (180.0f / 3.14159265f);
}

} // namespace esp
