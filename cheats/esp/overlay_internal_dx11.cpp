// language: C++17, file: overlay_internal_dx11.cpp, target: Windows 11, MSVC
// *internal DX11 overlay via Present hook — uses MinHook (public) + ImGui (public).
//  injects into target process as a DLL; DllMain spawns a thread that scans the
//  SwapChain vtable for Present, hooks via MinHook, renders ImGui draw list from
//  inside the game's Present. Exits cleanly when F11 is held.*
//
// Build (from a VS Developer Command Prompt):
//   cl /std:c++17 /LD /EHsc /MD overlay_internal_dx11.cpp /Fe:esp.dll ^
//      /I<minhook>\include /I<imgui>\include ^
//      <minhook>\lib\libMinHook.x64.lib ^
//      imgui.lib imgui_impl_win32.lib imgui_impl_dx11.lib ^
//      d3d11.lib dxgi.lib user32.lib gdi32.lib
//
// Inject via any mapper/loader of your choice. The process scope and whether this
// survives a given AC is your work (see docs/anticheat_methodology.md) — this file
// is the renderer layer.

#include <windows.h>
#include <d3d11.h>
#include <dxgi.h>
#include <wrl/client.h>
#include <atomic>
#include <cstdio>
#include <mutex>
#include <vector>

#include <MinHook.h>
#include <imgui.h>
#include <imgui_impl_win32.h>
#include <imgui_impl_dx11.h>

#include "world_to_screen.h"
#include "game_config.h"

#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "dxgi.lib")

using Microsoft::WRL::ComPtr;
using esp::Vec2;
using esp::Vec3;
using esp::Mat4;

// -------------------- globals --------------------

static HWND                 g_hwnd                = nullptr;
static ID3D11Device*        g_device              = nullptr;
static ID3D11DeviceContext* g_context             = nullptr;
static ID3D11RenderTargetView* g_rtv              = nullptr;
static WNDPROC              g_orig_wndproc        = nullptr;
static std::atomic<bool>    g_initialized{false};
static std::atomic<bool>    g_should_exit{false};
static uintptr_t            g_module_base         = 0;  // target game module base

// Present signature (IDXGISwapChain::Present)
using PresentFn = HRESULT (STDMETHODCALLTYPE*)(IDXGISwapChain*, UINT, UINT);
static PresentFn g_orig_present = nullptr;

// -------------------- forward --------------------

LRESULT CALLBACK HookedWndProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp);
HRESULT STDMETHODCALLTYPE HookedPresent(IDXGISwapChain* sc, UINT sync, UINT flags);
static void InitDevice(IDXGISwapChain* sc);
static void RenderESP();
extern IMGUI_IMPL_API LRESULT ImGui_ImplWin32_WndProcHandler(HWND, UINT, WPARAM, LPARAM);

// -------------------- memory helpers --------------------

template <typename T>
static T ReadMem(uintptr_t addr) {
    T v{};
    __try {
        if (addr < 0x10000) return v;
        v = *reinterpret_cast<T*>(addr);
    } __except (EXCEPTION_EXECUTE_HANDLER) {}
    return v;
}

static uintptr_t FollowPtr(uintptr_t base, uintptr_t rva) {
    return ReadMem<uintptr_t>(base + rva);
}

static uintptr_t GetModuleBaseByName(const wchar_t* name) {
    return reinterpret_cast<uintptr_t>(GetModuleHandleW(name));
}

// -------------------- SwapChain vtable scan --------------------

// Create a throwaway device + swapchain, pull the Present pointer out of the vtable.
// This is the canonical "dummy swapchain" trick for finding the real engine's Present
// index 8 in the IDXGISwapChain vtable without symbols. Only runs once at init.
static PresentFn FindPresentPointer() {
    HWND tmp = CreateWindowW(L"STATIC", L"dummy", WS_OVERLAPPEDWINDOW,
                              0, 0, 2, 2, nullptr, nullptr, nullptr, nullptr);
    if (!tmp) return nullptr;

    DXGI_SWAP_CHAIN_DESC desc{};
    desc.BufferCount = 1;
    desc.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    desc.BufferDesc.RefreshRate = { 60, 1 };
    desc.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    desc.OutputWindow = tmp;
    desc.SampleDesc.Count = 1;
    desc.Windowed = TRUE;
    desc.SwapEffect = DXGI_SWAP_EFFECT_DISCARD;

    D3D_FEATURE_LEVEL fl = D3D_FEATURE_LEVEL_11_0;
    ComPtr<IDXGISwapChain> sc;
    ComPtr<ID3D11Device> dev;
    ComPtr<ID3D11DeviceContext> ctx;

    HRESULT hr = D3D11CreateDeviceAndSwapChain(
        nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0,
        &fl, 1, D3D11_SDK_VERSION, &desc,
        sc.GetAddressOf(), dev.GetAddressOf(), nullptr, ctx.GetAddressOf());

    DestroyWindow(tmp);
    if (FAILED(hr) || !sc) return nullptr;

    void** vtable = *reinterpret_cast<void***>(sc.Get());
    // vtable indices: 0..2 = IUnknown, 3..7 = IDXGIObject + IDXGIDeviceSubObject, 8 = Present
    return reinterpret_cast<PresentFn>(vtable[8]);
}

// -------------------- init / render --------------------

static void InitDevice(IDXGISwapChain* sc) {
    if (g_initialized.load()) return;

    if (FAILED(sc->GetDevice(__uuidof(ID3D11Device), reinterpret_cast<void**>(&g_device)))) return;
    g_device->GetImmediateContext(&g_context);

    ID3D11Texture2D* backbuf = nullptr;
    if (FAILED(sc->GetBuffer(0, __uuidof(ID3D11Texture2D), reinterpret_cast<void**>(&backbuf)))) return;
    g_device->CreateRenderTargetView(backbuf, nullptr, &g_rtv);
    backbuf->Release();

    DXGI_SWAP_CHAIN_DESC desc{};
    sc->GetDesc(&desc);
    g_hwnd = desc.OutputWindow;

    g_orig_wndproc = reinterpret_cast<WNDPROC>(
        SetWindowLongPtrW(g_hwnd, GWLP_WNDPROC,
                          reinterpret_cast<LONG_PTR>(HookedWndProc)));

    IMGUI_CHECKVERSION();
    ImGui::CreateContext();
    ImGuiIO& io = ImGui::GetIO();
    io.IniFilename = nullptr;
    io.ConfigFlags |= ImGuiConfigFlags_NavEnableKeyboard;
    ImGui_ImplWin32_Init(g_hwnd);
    ImGui_ImplDX11_Init(g_device, g_context);

    g_module_base = GetModuleBaseByName(esp::g_game.engine_module.c_str());
    g_initialized = true;
}

LRESULT CALLBACK HookedWndProc(HWND h, UINT msg, WPARAM wp, LPARAM lp) {
    if (ImGui_ImplWin32_WndProcHandler(h, msg, wp, lp)) {}
    if (msg == WM_KEYDOWN && wp == VK_F11) g_should_exit = true;
    return CallWindowProcW(g_orig_wndproc, h, msg, wp, lp);
}

HRESULT STDMETHODCALLTYPE HookedPresent(IDXGISwapChain* sc, UINT sync, UINT flags) {
    if (!g_initialized.load()) InitDevice(sc);
    if (g_initialized.load()) {
        ImGui_ImplDX11_NewFrame();
        ImGui_ImplWin32_NewFrame();
        ImGui::NewFrame();
        RenderESP();
        ImGui::EndFrame();
        ImGui::Render();
        g_context->OMSetRenderTargets(1, &g_rtv, nullptr);
        ImGui_ImplDX11_RenderDrawData(ImGui::GetDrawData());
    }
    return g_orig_present(sc, sync, flags);
}

// -------------------- the ESP itself --------------------

struct EntityView {
    Vec3   pos;
    Vec3   local_eye;
    float  health;
    int    team;
    bool   alive;
    char   name[64];
    float  distance;
};

static bool ReadEntity(uintptr_t ent_base, EntityView& out, int local_team, const Vec3& local_eye) {
    if (!ent_base) return false;
    auto& g = esp::g_game;
    out.pos    = ReadMem<Vec3>(ent_base + g.pos_offset);
    out.health = static_cast<float>(ReadMem<int32_t>(ent_base + g.health_offset));
    out.team   = ReadMem<int32_t>(ent_base + g.team_offset);
    out.alive  = ReadMem<uint8_t>(ent_base + g.alive_offset) != 0;
    std::memcpy(out.name, reinterpret_cast<void*>(ent_base + g.name_offset), 63);
    out.name[63] = 0;
    out.local_eye = local_eye;
    out.distance = local_eye.distance(out.pos);
    if (!out.alive) return false;
    if (g.team_check && out.team == local_team) return false;
    if (out.distance > g.max_render_distance) return false;
    return true;
}

static void RenderESP() {
    if (g_should_exit.load()) return;
    if (!g_module_base) return;

    ImGuiIO& io = ImGui::GetIO();
    const float SW = io.DisplaySize.x;
    const float SH = io.DisplaySize.y;

    auto& g = esp::g_game;

    // read view matrix from module base
    Mat4 vp = ReadMem<Mat4>(g_module_base + g.view_matrix_offset);

    // local player + eye
    uintptr_t lp = FollowPtr(g_module_base, g.local_player_ptr);
    if (!lp) return;
    int local_team = ReadMem<int32_t>(lp + g.local_team_offset);
    Vec3 local_eye = ReadMem<Vec3>(lp + g.local_eye_pos_offset);

    // entity list
    uintptr_t list = FollowPtr(g_module_base, g.entity_list_ptr);
    if (!list) return;

    ImDrawList* dl = ImGui::GetBackgroundDrawList();

    for (uint32_t i = 0; i < g.max_entities; i++) {
        uintptr_t ent = ReadMem<uintptr_t>(list + i * g.entity_stride);
        if (!ent) continue;

        EntityView ev{};
        if (!ReadEntity(ent, ev, local_team, local_eye)) continue;

        // bounding box: project the two vertical extents of a ~180cm humanoid.
        // Game-unit scale varies — tune hw/hh/hd for your target.
        const float hw = 25.0f, hh = 90.0f, hd = 25.0f;
        Vec2 bmin, bmax;
        if (!esp::WorldBoundingBox(vp, ev.pos, hw, hh, hd, SW, SH, bmin, bmax))
            continue;

        ImU32 color = IM_COL32(255, 60, 60, 255);  // enemy
        if (!g.team_check) {
            color = (ev.team == local_team) ? IM_COL32(60, 255, 60, 255)
                                              : IM_COL32(255, 60, 60, 255);
        }

        if (g.draw_boxes) {
            dl->AddRect({bmin.x, bmin.y}, {bmax.x, bmax.y}, color, 0.0f, 0, 1.5f);
        }
        if (g.draw_health) {
            float frac = ev.health / 100.0f;
            if (frac < 0) frac = 0; if (frac > 1) frac = 1;
            float bar_h = bmax.y - bmin.y;
            float bar_w = 3.0f;
            dl->AddRectFilled({bmin.x - 6, bmin.y + bar_h * (1 - frac)},
                               {bmin.x - 6 + bar_w, bmax.y},
                               IM_COL32(60, 255, 60, 220));
            dl->AddRect({bmin.x - 6, bmin.y}, {bmin.x - 6 + bar_w, bmax.y},
                         IM_COL32(0, 0, 0, 220));
        }
        if (g.draw_names) {
            dl->AddText({bmax.x + 4, bmin.y}, color, ev.name);
        }
        if (g.draw_distance) {
            char buf[32];
            snprintf(buf, sizeof(buf), "%.0fm", ev.distance);
            dl->AddText({bmax.x + 4, bmin.y + 14}, IM_COL32(200, 200, 200, 255), buf);
        }
        if (g.draw_lines) {
            Vec2 center{(bmin.x + bmax.x) * 0.5f, bmax.y};
            dl->AddLine({SW * 0.5f, SH}, {center.x, center.y}, color, 1.0f);
        }
    }

    // minimal menu (toggle with INSERT)
    static bool show_menu = false;
    if (ImGui::IsKeyPressed(ImGuiKey_Insert, false)) show_menu = !show_menu;
    if (show_menu) {
        ImGui::Begin("ESP", &show_menu);
        ImGui::Checkbox("Boxes",    &g.draw_boxes);
        ImGui::Checkbox("Names",    &g.draw_names);
        ImGui::Checkbox("Health",   &g.draw_health);
        ImGui::Checkbox("Distance", &g.draw_distance);
        ImGui::Checkbox("Lines",    &g.draw_lines);
        ImGui::Checkbox("Team check", &g.team_check);
        ImGui::SliderFloat("Max dist", &g.max_render_distance, 50.0f, 2000.0f);
        ImGui::End();
    }
}

// -------------------- DllMain --------------------

static DWORD WINAPI SetupThread(LPVOID) {
    // Wait for the game to finish bootstrapping its graphics device.
    Sleep(5000);

    if (MH_Initialize() != MH_OK) return 1;

    PresentFn target = FindPresentPointer();
    if (!target) { MH_Uninitialize(); return 2; }

    if (MH_CreateHook(reinterpret_cast<void*>(target),
                      reinterpret_cast<void*>(&HookedPresent),
                      reinterpret_cast<void**>(&g_orig_present)) != MH_OK) {
        MH_Uninitialize();
        return 3;
    }
    MH_EnableHook(MH_ALL_HOOKS);

    // Live until F11 held.
    while (!g_should_exit.load()) Sleep(100);

    MH_DisableHook(MH_ALL_HOOKS);
    MH_Uninitialize();

    if (g_initialized.load()) {
        ImGui_ImplDX11_Shutdown();
        ImGui_ImplWin32_Shutdown();
        ImGui::DestroyContext();
        if (g_rtv)     g_rtv->Release();
        if (g_context) g_context->Release();
        if (g_device)  g_device->Release();
        if (g_orig_wndproc && g_hwnd) {
            SetWindowLongPtrW(g_hwnd, GWLP_WNDPROC,
                               reinterpret_cast<LONG_PTR>(g_orig_wndproc));
        }
    }

    FreeLibraryAndExitThread(reinterpret_cast<HMODULE>(GetModuleHandleW(L"esp.dll")), 0);
    return 0;
}

BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(inst);
        CreateThread(nullptr, 0, SetupThread, nullptr, 0, nullptr);
    }
    return TRUE;
}
