// language: Objective-C++ header, target: iOS arm64 (no JIT, no task_for_pid).
// Shared types for the in-process memory ESP + touch injector dylib.
#pragma once

#import <Foundation/Foundation.h>
#include <cstdint>
#include <string>
#include <vector>
#include <mach/mach.h>
#include <mach/mach_vm.h>

#define GL_PORT 65510

// Canonical bone IDs — MUST match the Android Zygisk module and the PC
// game_lab_core.frame.SKELETON_EDGES so the universal overlay draws
// identically regardless of which device produced the frame.
enum : int {
    BONE_HEAD = 0, BONE_NECK, BONE_PELVIS,
    BONE_SPINE_UP, BONE_SPINE_LO,
    BONE_SHOULDER_L, BONE_ELBOW_L, BONE_HAND_L,
    BONE_SHOULDER_R, BONE_ELBOW_R, BONE_HAND_R,
    BONE_HIP_L, BONE_KNEE_L, BONE_FOOT_L,
    BONE_HIP_R, BONE_KNEE_R, BONE_FOOT_R,
    BONE_COUNT,
};

struct Vec3 { float x, y, z; };

struct EntitySnapshot {
    uint32_t id = 0;
    int32_t  team = 0;
    int32_t  hp = 0;
    Vec3     pos  = {};
    Vec3     head = {};
    Vec3     feet = {};
    Vec3     center = {};
    std::vector<Vec3> bones;
    std::string name;
    std::string weapon;
    bool visible = false;
};

// Resolve the Mach-O image base for `name` (suffix match on the image
// path, e.g. "UE4Game.app/BGMIM"). 0 on miss.
uintptr_t gl_image_base(const char* name_suffix);

// In-process memory reads/writes — no task_for_pid needed, we ARE the
// process. mach_vm_protect-aware so protected pages can be flipped
// RW before write.
bool gl_read(uintptr_t addr, void* out, size_t len);
bool gl_write(uintptr_t addr, const void* in, size_t len);

// Entry points from main.mm
void gl_start_socket_thread(void);
void gl_start_touch_injector(void);

// Dispatcher — one JSON request-line in, one JSON reply-line out.
std::string gl_handle_request(const std::string& line);

// Touch injector — called from the socket thread when the PC side sends
// an aim-delta. dx/dy are mapped to synthetic UTouch events.
void gl_inject_touch_delta(float dx, float dy);
void gl_inject_tap(float x, float y);
void gl_log(const char* fmt, ...);
