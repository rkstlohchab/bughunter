// language: Objective-C++, file: memory_scanner.mm, target: iOS arm64
// Reads BGMI (UE4) memory inside the game process. Same algorithm as
// the Android Zygisk module — UE4's GWorld layout is platform-agnostic.
// Only the entry point (how we got a thread inside the game) differs.
//
// Also hosts the JSON dispatcher — one request-line in, one reply-line
// out, same wire protocol as the Android build so the PC-side reader
// is identical.

#import "common.h"

#import <Foundation/Foundation.h>
#import <mach/mach.h>
#import <mach/mach_vm.h>
#import <mach-o/dyld.h>
#import <mach-o/getsect.h>
#include <string>
#include <mutex>
#include <vector>
#include <cstdio>
#include <cstring>

// ---------------------------------------------------------------------
// Image base resolution via dyld
// ---------------------------------------------------------------------

uintptr_t gl_image_base(const char* name_suffix) {
    uint32_t n = _dyld_image_count();
    size_t sl = strlen(name_suffix);
    for (uint32_t i = 0; i < n; i++) {
        const char* p = _dyld_get_image_name(i);
        if (!p) continue;
        size_t pl = strlen(p);
        if (pl >= sl && strcmp(p + pl - sl, name_suffix) == 0) {
            return (uintptr_t)_dyld_get_image_vmaddr_slide(i)
                   + (uintptr_t)_dyld_get_image_header(i);
        }
    }
    // Fallback: executable image (index 0 by Apple convention)
    const struct mach_header* h = _dyld_get_image_header(0);
    return (uintptr_t)h;
}

// ---------------------------------------------------------------------
// In-process mem access (we ARE the game process; no task_for_pid needed)
// ---------------------------------------------------------------------

bool gl_read(uintptr_t addr, void* out, size_t len) {
    if (!addr || !len) return false;
    // Direct memcpy works for pages that are PROT_READ. Protected pages
    // (anti-debug, dyld trampolines) would need mach_vm_protect, but
    // UE4 game data pages are vanilla RW.
    std::memcpy(out, (const void*)addr, len);
    return true;
}

bool gl_write(uintptr_t addr, const void* in, size_t len) {
    if (!addr || !len) return false;
    kern_return_t kr = mach_vm_protect(
        mach_task_self(), (mach_vm_address_t)(addr & ~0x3FFFULL),
        (mach_vm_size_t)((len + 0x3FFF) & ~0x3FFFULL),
        FALSE, VM_PROT_READ | VM_PROT_WRITE | VM_PROT_COPY);
    if (kr != KERN_SUCCESS) return false;
    std::memcpy((void*)addr, in, len);
    return true;
}

// ---------------------------------------------------------------------
// Offset table — populated by the PC side via the "set_offsets" cmd
// ---------------------------------------------------------------------

struct IosOffsets {
    std::string module_suffix = "BGMIM";            // suffix of exec image name
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
    int bones[BONE_COUNT] = {};
};
static IosOffsets g_offs;
static std::mutex g_mu;

// Hand-rolled minimal JSON — avoid external deps in the dylib.
static long long gl_j_int(const std::string& s, const char* key, long long def) {
    std::string pat = std::string("\"") + key + "\":";
    size_t p = s.find(pat);
    if (p == std::string::npos) return def;
    p += pat.size();
    while (p < s.size() && (s[p] == ' ' || s[p] == '\t')) ++p;
    return strtoll(s.c_str() + p, nullptr, 10);
}

static std::string gl_j_str(const std::string& s) {
    std::string o; o.reserve(s.size() + 2);
    o.push_back('"');
    for (char c : s) {
        if (c == '"' || c == '\\') { o.push_back('\\'); o.push_back(c); }
        else if (c == '\n') { o += "\\n"; }
        else if ((unsigned char)c >= 0x20) o.push_back(c);
    }
    o.push_back('"');
    return o;
}

static std::string gl_j_vec3(const Vec3& v) {
    char b[80];
    snprintf(b, sizeof(b), "[%.3f,%.3f,%.3f]", v.x, v.y, v.z);
    return b;
}

// ---------------------------------------------------------------------
// Bone walker (UE4 BGMI shape)
// ---------------------------------------------------------------------

static void gl_read_bones(uintptr_t pawn, std::vector<Vec3>& out) {
    IosOffsets o; { std::lock_guard<std::mutex> g(g_mu); o = g_offs; }
    if (!o.Mesh || !o.BoneArray) return;
    uintptr_t mesh = 0;
    if (!gl_read(pawn + o.Mesh, &mesh, 8) || !mesh) return;
    uintptr_t bone_array = 0;
    if (!gl_read(mesh + o.BoneArray, &bone_array, 8) || !bone_array) return;
    for (int i = 0; i < BONE_COUNT; i++) {
        int bid = o.bones[i];
        Vec3 bv{};
        // FTransform = FQuat(16) + FVector(12) + FVector_scale(12) = 48 bytes
        // Location is at +16.
        if (gl_read(bone_array + bid * 48 + 16, &bv, 12)) out.push_back(bv);
        else out.push_back({0, 0, 0});
    }
}

static bool gl_fill_pawn(uintptr_t pawn, EntitySnapshot& e) {
    IosOffsets o; { std::lock_guard<std::mutex> g(g_mu); o = g_offs; }
    if (!pawn) return false;
    int32_t hp = 0, team = 0;
    gl_read(pawn + o.Health, &hp, 4);
    if (o.TeamIndex) gl_read(pawn + o.TeamIndex, &team, 4);
    uintptr_t root = 0;
    gl_read(pawn + o.RootComponent, &root, 8);
    Vec3 pos = {};
    if (root) gl_read(root + o.RelativeLocation, &pos, 12);
    e.id = (uint32_t)(pawn & 0xFFFFFFFF);
    e.hp = hp; e.team = team;
    e.pos = pos; e.feet = pos;
    e.head = {pos.x, pos.y, pos.z + 85.0f};
    e.center = {pos.x, pos.y, pos.z + 40.0f};
    gl_read_bones(pawn, e.bones);
    if (!e.bones.empty()) e.head = e.bones[0];
    return true;
}

static std::vector<EntitySnapshot> gl_iter_players(void) {
    IosOffsets o; { std::lock_guard<std::mutex> g(g_mu); o = g_offs; }
    std::vector<EntitySnapshot> out;
    uintptr_t base = gl_image_base(o.module_suffix.c_str());
    if (!base) return out;
    uintptr_t gworld = 0;
    gl_read(base + o.UWorld, &gworld, 8);
    if (!gworld) return out;
    uintptr_t level = 0;
    gl_read(gworld + o.PersistentLevel, &level, 8);
    if (!level) return out;
    uintptr_t actors = 0;
    int32_t count = 0;
    gl_read(level + o.Actors, &actors, 8);
    gl_read(level + o.ActorsNum, &count, 4);
    if (count < 0 || count > 2000) return out;
    for (int i = 0; i < count; i++) {
        uintptr_t a = 0;
        if (!gl_read(actors + i * 8, &a, 8) || !a) continue;
        EntitySnapshot e;
        if (!gl_fill_pawn(a, e)) continue;
        if (e.hp <= 0) continue;
        out.push_back(std::move(e));
    }
    return out;
}

static bool gl_read_view_matrix(float out[16]) {
    IosOffsets o; { std::lock_guard<std::mutex> g(g_mu); o = g_offs; }
    uintptr_t base = gl_image_base(o.module_suffix.c_str());
    if (!base) return false;
    uintptr_t gworld = 0; gl_read(base + o.UWorld, &gworld, 8);
    if (!gworld) return false;
    uintptr_t gi = 0; gl_read(gworld + o.OwningGameInstance, &gi, 8);
    if (!gi) return false;
    uintptr_t lps = 0; gl_read(gi + o.LocalPlayers, &lps, 8);
    if (!lps) return false;
    uintptr_t lp = 0; gl_read(lps, &lp, 8);
    if (!lp) return false;
    uintptr_t pc = 0; gl_read(lp + o.PlayerController, &pc, 8);
    if (!pc) return false;
    uintptr_t cm = 0; gl_read(pc + o.PlayerCameraManager, &cm, 8);
    if (!cm) return false;
    // POV = Loc(12) + Rot(12) + FOV(4) = 28 bytes. Ship raw; PC side
    // rebuilds a view-proj matrix from pos+rot+fov identically to the
    // Android build so the overlay math is unified.
    float pov[7] = {};
    gl_read(cm + o.CameraCache_POV, pov, 28);
    for (int i = 0; i < 7; i++) out[i] = pov[i];
    for (int i = 7; i < 16; i++) out[i] = 0.0f;
    return true;
}

// ---------------------------------------------------------------------
// Dispatch
// ---------------------------------------------------------------------

static std::string gl_reply_players(void) {
    auto ents = gl_iter_players();
    std::string s = "{\"players\":[";
    for (size_t i = 0; i < ents.size(); i++) {
        const auto& e = ents[i];
        if (i) s += ",";
        s += "{\"id\":" + std::to_string(e.id);
        s += ",\"team\":" + std::to_string(e.team);
        s += ",\"hp\":" + std::to_string(e.hp);
        s += ",\"head\":" + gl_j_vec3(e.head);
        s += ",\"feet\":" + gl_j_vec3(e.feet);
        s += ",\"center\":" + gl_j_vec3(e.center);
        s += ",\"name\":" + gl_j_str(e.name);
        s += ",\"visible\":false,\"bones\":[";
        for (size_t b = 0; b < e.bones.size(); b++) {
            if (b) s += ",";
            s += gl_j_vec3(e.bones[b]);
        }
        s += "]}";
    }
    s += "]}";
    return s;
}

static std::string gl_reply_view(void) {
    float mat[16] = {};
    gl_read_view_matrix(mat);
    std::string s = "{\"matrix\":[";
    char buf[32];
    for (int i = 0; i < 16; i++) {
        if (i) s += ",";
        snprintf(buf, sizeof(buf), "%.6f", mat[i]);
        s += buf;
    }
    s += "]}";
    return s;
}

static std::string gl_reply_set_offsets(const std::string& line) {
    std::lock_guard<std::mutex> g(g_mu);
    auto& o = g_offs;
    o.UWorld             = gl_j_int(line, "UWorld", o.UWorld);
    o.OwningGameInstance = gl_j_int(line, "OwningGameInstance", o.OwningGameInstance);
    o.LocalPlayers       = gl_j_int(line, "LocalPlayers", o.LocalPlayers);
    o.PlayerController   = gl_j_int(line, "PlayerController", o.PlayerController);
    o.AcknowledgedPawn   = gl_j_int(line, "AcknowledgedPawn", o.AcknowledgedPawn);
    o.PlayerCameraManager= gl_j_int(line, "PlayerCameraManager", o.PlayerCameraManager);
    o.CameraCache_POV    = gl_j_int(line, "CameraCache_POV", o.CameraCache_POV);
    o.PersistentLevel    = gl_j_int(line, "PersistentLevel", o.PersistentLevel);
    o.Actors             = gl_j_int(line, "Actors", o.Actors);
    o.ActorsNum          = gl_j_int(line, "ActorsNum", o.ActorsNum);
    o.RootComponent      = gl_j_int(line, "RootComponent", o.RootComponent);
    o.RelativeLocation   = gl_j_int(line, "RelativeLocation", o.RelativeLocation);
    o.Health             = gl_j_int(line, "Health", o.Health);
    o.TeamIndex          = gl_j_int(line, "TeamIndex", o.TeamIndex);
    o.PlayerName         = gl_j_int(line, "PlayerName", o.PlayerName);
    o.Mesh               = gl_j_int(line, "Mesh", o.Mesh);
    o.BoneArray          = gl_j_int(line, "BoneArray", o.BoneArray);
    for (int i = 0; i < BONE_COUNT; i++) {
        char key[32]; snprintf(key, sizeof(key), "bone_%d", i);
        o.bones[i] = (int)gl_j_int(line, key, o.bones[i]);
    }
    return "{\"ok\":1}";
}

static std::string gl_reply_aim(const std::string& line) {
    // Expects {"cmd":"aim","dx":<float>,"dy":<float>}
    float dx = 0.0f, dy = 0.0f;
    size_t pos;
    pos = line.find("\"dx\":"); if (pos != std::string::npos)
        dx = strtof(line.c_str() + pos + 5, nullptr);
    pos = line.find("\"dy\":"); if (pos != std::string::npos)
        dy = strtof(line.c_str() + pos + 5, nullptr);
    gl_inject_touch_delta(dx, dy);
    return "{\"ok\":1}";
}

static std::string gl_reply_tap(const std::string& line) {
    float x = 0.0f, y = 0.0f;
    size_t pos;
    pos = line.find("\"x\":"); if (pos != std::string::npos)
        x = strtof(line.c_str() + pos + 4, nullptr);
    pos = line.find("\"y\":"); if (pos != std::string::npos)
        y = strtof(line.c_str() + pos + 4, nullptr);
    gl_inject_tap(x, y);
    return "{\"ok\":1}";
}

std::string gl_handle_request(const std::string& line) {
    if (line.find("\"cmd\":\"players\"") != std::string::npos)     return gl_reply_players();
    if (line.find("\"cmd\":\"view\"")    != std::string::npos)     return gl_reply_view();
    if (line.find("\"cmd\":\"set_offsets\"") != std::string::npos) return gl_reply_set_offsets(line);
    if (line.find("\"cmd\":\"aim\"")     != std::string::npos)     return gl_reply_aim(line);
    if (line.find("\"cmd\":\"tap\"")     != std::string::npos)     return gl_reply_tap(line);
    if (line.find("\"cmd\":\"ping\"")    != std::string::npos)     return "{\"pong\":1}";
    return "{\"error\":\"unknown\"}";
}
