// language: C++17, file: memory_scanner.cpp, platform: Android NDK
// Reads BGMI (UE4) memory inside the game process. Resolves GWorld →
// PersistentLevel → Actors, walks APawn for HP/team/pos/bones, replies
// to the PC over the socket in JSON.
//
// Offsets live in /sdcard/gamelab/bgmi.offsets.json, pulled via the
// socket "pull_offsets" command on first client connect. Offsets drift
// per BGMI update — PC-side GameLab maintains the current set.

#include "common.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdarg>
#include <sys/mman.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <dlfcn.h>
#include <link.h>
#include <string>
#include <vector>
#include <sstream>
#include <mutex>
#include <unordered_map>

// -------------------------------------------------------------
// Module base resolution via /proc/self/maps
// -------------------------------------------------------------

uintptr_t module_base(const char* name) {
    static std::mutex mu;
    static std::unordered_map<std::string, uintptr_t> cache;
    std::lock_guard<std::mutex> g(mu);
    auto it = cache.find(name);
    if (it != cache.end()) return it->second;
    FILE* f = fopen("/proc/self/maps", "r");
    if (!f) return 0;
    uintptr_t base = 0;
    char line[512];
    while (fgets(line, sizeof(line), f)) {
        if (strstr(line, name) && strstr(line, "r--p")) {
            uintptr_t a = 0, b = 0;
            if (sscanf(line, "%lx-%lx", &a, &b) == 2) { base = a; break; }
        }
    }
    fclose(f);
    cache[name] = base;
    return base;
}

bool read_mem(uintptr_t addr, void* buf, size_t size) {
    if (!addr || !size) return false;
    // Inside our own process, memcpy works for readable mappings.
    // For unreadable pages (guard, PROT_NONE), use mprotect first — but
    // UE4's game data is normal RW, so direct copy is fine.
    memcpy(buf, reinterpret_cast<void*>(addr), size);
    return true;
}

bool write_mem(uintptr_t addr, const void* buf, size_t size) {
    if (!addr || !size) return false;
    size_t pagesize = static_cast<size_t>(sysconf(_SC_PAGESIZE));
    uintptr_t page = addr & ~(pagesize - 1);
    size_t span = ((addr + size + pagesize - 1) & ~(pagesize - 1)) - page;
    mprotect(reinterpret_cast<void*>(page), span,
             PROT_READ | PROT_WRITE | PROT_EXEC);
    memcpy(reinterpret_cast<void*>(addr), buf, size);
    return true;
}

// -------------------------------------------------------------
// Offset table — populated by the PC side on client connect.
// -------------------------------------------------------------

struct Offsets {
    uintptr_t UWorld = 0;
    uintptr_t OwningGameInstance = 0;
    uintptr_t LocalPlayers = 0;
    uintptr_t PlayerController = 0;
    uintptr_t AcknowledgedPawn = 0;
    uintptr_t PlayerCameraManager = 0;
    uintptr_t CameraCache_POV = 0;
    uintptr_t PersistentLevel = 0;
    uintptr_t Actors = 0;
    uintptr_t ActorsNum = 0;
    uintptr_t RootComponent = 0;
    uintptr_t RelativeLocation = 0;
    uintptr_t Health = 0;
    uintptr_t TeamIndex = 0;
    uintptr_t PlayerName = 0;
    uintptr_t Mesh = 0;
    uintptr_t BoneArray = 0;
    int bone_head = 0, bone_neck = 1, bone_pelvis = 2;
    int bones[17] = {0};
    std::string module_name = "libUE4.so";
};
static Offsets g_offsets;
static std::mutex g_off_mu;

static long parse_long(const std::string& s, size_t& pos) {
    char* end = nullptr;
    long v = strtoll(s.c_str() + pos, &end, 10);
    pos = end - s.c_str();
    return v;
}

// Tiny hand-rolled JSON writer / reader — avoid an external dep in the .so.
static std::string j_str(const std::string& s) {
    std::string o; o.reserve(s.size() + 2);
    o.push_back('"');
    for (char c : s) {
        if (c == '"' || c == '\\') { o.push_back('\\'); o.push_back(c); }
        else if (c == '\n') { o += "\\n"; }
        else if (c >= 0x20) o.push_back(c);
    }
    o.push_back('"');
    return o;
}

static std::string j_vec3(const float v[3]) {
    char b[80];
    snprintf(b, sizeof(b), "[%.3f,%.3f,%.3f]", v[0], v[1], v[2]);
    return b;
}

static std::string json_quote_find(const std::string& s, const char* key) {
    std::string pat = std::string("\"") + key + "\":";
    auto p = s.find(pat);
    if (p == std::string::npos) return {};
    p += pat.size();
    while (p < s.size() && (s[p] == ' ' || s[p] == '\t')) ++p;
    return s.substr(p);
}

static long long j_get_int(const std::string& s, const char* key, long long def) {
    auto v = json_quote_find(s, key);
    if (v.empty()) return def;
    return strtoll(v.c_str(), nullptr, 10);
}

// -------------------------------------------------------------
// Entity-list walker (UE4 BGMI shape)
// -------------------------------------------------------------

struct BoneFrame { float x, y, z; };

static void read_bones(uintptr_t pawn, std::vector<BoneWorld>& out) {
    Offsets off;
    { std::lock_guard<std::mutex> g(g_off_mu); off = g_offsets; }
    if (!off.Mesh || !off.BoneArray) return;
    uintptr_t mesh = 0;
    if (!read_mem(pawn + off.Mesh, &mesh, 8) || !mesh) return;
    uintptr_t bone_array = 0;
    if (!read_mem(mesh + off.BoneArray, &bone_array, 8) || !bone_array) return;
    for (int i = 0; i < 17; ++i) {
        int bid = off.bones[i];
        BoneFrame bf{};
        // UE4 FTransform = FQuat(16) + FVector(12) + FVector_scale(12) = 48 bytes
        // Location is at +16.
        if (read_mem(bone_array + bid * 48 + 16, &bf, 12)) {
            out.push_back({bf.x, bf.y, bf.z});
        } else {
            out.push_back({0, 0, 0});
        }
    }
}

static bool read_pawn(uintptr_t pawn, EntitySnapshot& out) {
    Offsets off;
    { std::lock_guard<std::mutex> g(g_off_mu); off = g_offsets; }
    if (!pawn) return false;
    int32_t hp = 0, team = 0;
    read_mem(pawn + off.Health, &hp, 4);
    if (off.TeamIndex) read_mem(pawn + off.TeamIndex, &team, 4);
    uintptr_t root = 0;
    read_mem(pawn + off.RootComponent, &root, 8);
    float pos[3] = {0, 0, 0};
    if (root) read_mem(root + off.RelativeLocation, pos, 12);
    out.id = static_cast<uint32_t>(pawn & 0xFFFFFFFF);
    out.hp = hp; out.team = team;
    out.pos[0] = pos[0]; out.pos[1] = pos[1]; out.pos[2] = pos[2];
    out.feet[0] = pos[0]; out.feet[1] = pos[1]; out.feet[2] = pos[2];
    out.head[0] = pos[0]; out.head[1] = pos[1]; out.head[2] = pos[2] + 85.0f;
    out.center[0] = pos[0]; out.center[1] = pos[1]; out.center[2] = pos[2] + 40.0f;
    read_bones(pawn, out.bones);
    if (!out.bones.empty()) {
        out.head[0] = out.bones[0].x;
        out.head[1] = out.bones[0].y;
        out.head[2] = out.bones[0].z;
    }
    return true;
}

static std::vector<EntitySnapshot> iter_players() {
    Offsets off;
    { std::lock_guard<std::mutex> g(g_off_mu); off = g_offsets; }
    std::vector<EntitySnapshot> out;
    uintptr_t base = module_base(off.module_name.c_str());
    if (!base) return out;
    uintptr_t gworld = 0;
    read_mem(base + off.UWorld, &gworld, 8);
    if (!gworld) return out;
    uintptr_t level = 0;
    read_mem(gworld + off.PersistentLevel, &level, 8);
    if (!level) return out;
    uintptr_t actors = 0;
    int32_t count = 0;
    read_mem(level + off.Actors, &actors, 8);
    read_mem(level + off.ActorsNum, &count, 4);
    if (count < 0 || count > 2000) return out;
    for (int i = 0; i < count; ++i) {
        uintptr_t a = 0;
        if (!read_mem(actors + i * 8, &a, 8) || !a) continue;
        EntitySnapshot e{};
        if (!read_pawn(a, e)) continue;
        if (e.hp <= 0) continue;
        out.push_back(std::move(e));
    }
    return out;
}

static bool read_view_matrix(float out[16]) {
    Offsets off;
    { std::lock_guard<std::mutex> g(g_off_mu); off = g_offsets; }
    uintptr_t base = module_base(off.module_name.c_str());
    if (!base) return false;
    uintptr_t gworld = 0; read_mem(base + off.UWorld, &gworld, 8);
    if (!gworld) return false;
    uintptr_t gi = 0; read_mem(gworld + off.OwningGameInstance, &gi, 8);
    if (!gi) return false;
    uintptr_t lps = 0; read_mem(gi + off.LocalPlayers, &lps, 8);
    if (!lps) return false;
    uintptr_t lp = 0; read_mem(lps, &lp, 8);
    if (!lp) return false;
    uintptr_t pc = 0; read_mem(lp + off.PlayerController, &pc, 8);
    if (!pc) return false;
    uintptr_t cm = 0; read_mem(pc + off.PlayerCameraManager, &cm, 8);
    if (!cm) return false;
    // POV layout: Location(12) + Rotation(12) + FOV(4) = 28 bytes
    float pov[7] = {0};
    read_mem(cm + off.CameraCache_POV, pov, 28);
    // Return a flat 16-float placeholder — the PC-side walker rebuilds a
    // view-proj matrix from camera pos + rotator + FOV for consistency
    // with the PC path.
    out[0]  = pov[0]; out[1]  = pov[1]; out[2]  = pov[2];
    out[3]  = pov[3]; out[4]  = pov[4]; out[5]  = pov[5]; out[6] = pov[6];
    for (int i = 7; i < 16; ++i) out[i] = 0.0f;
    return true;
}

// -------------------------------------------------------------
// Dispatch
// -------------------------------------------------------------

static std::string reply_players() {
    auto ents = iter_players();
    std::string s = "{\"players\":[";
    for (size_t i = 0; i < ents.size(); ++i) {
        const auto& e = ents[i];
        if (i) s += ",";
        s += "{\"id\":" + std::to_string(e.id);
        s += ",\"team\":" + std::to_string(e.team);
        s += ",\"hp\":" + std::to_string(e.hp);
        s += ",\"head\":" + j_vec3(e.head);
        s += ",\"feet\":" + j_vec3(e.feet);
        s += ",\"center\":" + j_vec3(e.center);
        s += ",\"name\":" + j_str(e.name);
        s += ",\"visible\":false";
        s += ",\"bones\":[";
        for (size_t b = 0; b < e.bones.size(); ++b) {
            if (b) s += ",";
            char buf[80];
            snprintf(buf, sizeof(buf), "[%.3f,%.3f,%.3f]",
                     e.bones[b].x, e.bones[b].y, e.bones[b].z);
            s += buf;
        }
        s += "]}";
    }
    s += "]}";
    return s;
}

static std::string reply_view() {
    float mat[16] = {0};
    read_view_matrix(mat);
    std::string s = "{\"matrix\":[";
    char buf[32];
    for (int i = 0; i < 16; ++i) {
        if (i) s += ",";
        snprintf(buf, sizeof(buf), "%.6f", mat[i]);
        s += buf;
    }
    s += "]}";
    return s;
}

static std::string reply_module(const std::string& line) {
    auto nv = json_quote_find(line, "name");
    // name is JSON-quoted; strip minimal
    std::string name;
    if (!nv.empty() && nv[0] == '"') {
        auto end = nv.find('"', 1);
        if (end != std::string::npos) name = nv.substr(1, end - 1);
    }
    uintptr_t base = module_base(name.empty() ? g_offsets.module_name.c_str() : name.c_str());
    return "{\"base\":" + std::to_string(base) + "}";
}

static std::string reply_set_offsets(const std::string& line) {
    std::lock_guard<std::mutex> g(g_off_mu);
    auto& o = g_offsets;
    o.UWorld             = j_get_int(line, "UWorld", o.UWorld);
    o.OwningGameInstance = j_get_int(line, "OwningGameInstance", o.OwningGameInstance);
    o.LocalPlayers       = j_get_int(line, "LocalPlayers", o.LocalPlayers);
    o.PlayerController   = j_get_int(line, "PlayerController", o.PlayerController);
    o.AcknowledgedPawn   = j_get_int(line, "AcknowledgedPawn", o.AcknowledgedPawn);
    o.PlayerCameraManager= j_get_int(line, "PlayerCameraManager", o.PlayerCameraManager);
    o.CameraCache_POV    = j_get_int(line, "CameraCache_POV", o.CameraCache_POV);
    o.PersistentLevel    = j_get_int(line, "PersistentLevel", o.PersistentLevel);
    o.Actors             = j_get_int(line, "Actors", o.Actors);
    o.ActorsNum          = j_get_int(line, "ActorsNum", o.ActorsNum);
    o.RootComponent      = j_get_int(line, "RootComponent", o.RootComponent);
    o.RelativeLocation   = j_get_int(line, "RelativeLocation", o.RelativeLocation);
    o.Health             = j_get_int(line, "Health", o.Health);
    o.TeamIndex          = j_get_int(line, "TeamIndex", o.TeamIndex);
    o.PlayerName         = j_get_int(line, "PlayerName", o.PlayerName);
    o.Mesh               = j_get_int(line, "Mesh", o.Mesh);
    o.BoneArray          = j_get_int(line, "BoneArray", o.BoneArray);
    for (int i = 0; i < 17; ++i) {
        char key[32]; snprintf(key, sizeof(key), "bone_%d", i);
        o.bones[i] = static_cast<int>(j_get_int(line, key, o.bones[i]));
    }
    return "{\"ok\":1}";
}

static std::string reply_ping() { return "{\"pong\":1}"; }

std::string handle_request(const std::string& line) {
    if (line.find("\"cmd\":\"players\"") != std::string::npos) return reply_players();
    if (line.find("\"cmd\":\"view\"")    != std::string::npos) return reply_view();
    if (line.find("\"cmd\":\"module\"")  != std::string::npos) return reply_module(line);
    if (line.find("\"cmd\":\"set_offsets\"") != std::string::npos) return reply_set_offsets(line);
    if (line.find("\"cmd\":\"ping\"")    != std::string::npos) return reply_ping();
    return "{\"error\":\"unknown cmd\"}";
}
