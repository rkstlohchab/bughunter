// language: C++, file: common.h, platform: Android NDK (arm64 / armv7)
#pragma once

#include <android/log.h>
#include <cstdio>
#include <cstdint>
#include <string>
#include <vector>

#define LOG_TAG "gamelab_mem"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO,  LOG_TAG, __VA_ARGS__)
#define LOGW(...) __android_log_print(ANDROID_LOG_WARN,  LOG_TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, __VA_ARGS__)

struct BoneWorld { float x, y, z; };
struct EntitySnapshot {
    uint32_t id;
    int32_t  team;
    int32_t  hp;
    float    pos[3];
    float    head[3];
    float    feet[3];
    float    center[3];
    std::vector<BoneWorld> bones;
    std::string name;
    std::string weapon;
    bool visible = false;
};

uintptr_t module_base(const char* name);
bool read_mem(uintptr_t addr, void* buf, size_t size);
bool write_mem(uintptr_t addr, const void* buf, size_t size);
