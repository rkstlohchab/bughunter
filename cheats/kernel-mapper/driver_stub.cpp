// language: C++, file: driver_stub.cpp, target: Windows 11 kernel (WDK)
// Minimal unsigned kernel driver designed to be manual-mapped into pool
// WITHOUT a DRIVER_OBJECT entry. Reads game-process memory, publishes to
// usermode via a shared physical page.
//
// Loaded by mapper.cpp via BYOVD arbitrary-write primitive.

#include <ntifs.h>
#include <ntddk.h>

// -----------------------------------------------------------------------
// Shared snapshot block the usermode overlay reads.
// -----------------------------------------------------------------------

#pragma pack(push, 1)
struct GLSharedEntity {
    UINT32 id;
    UINT32 team;
    INT32  hp;
    float  head_x, head_y, head_z;
};
struct GLSharedBlock {
    UINT32 magic;              // 'GLSH'
    UINT32 version;
    UINT32 tick;
    UINT32 count;
    float  view_matrix[16];
    GLSharedEntity entities[64];
};
#pragma pack(pop)

static PVOID g_shared_va = nullptr;        // kernel VA of shared block
static GLSharedBlock* g_shared = nullptr;
static PETHREAD g_worker_thread = nullptr;
static volatile LONG g_stop = 0;

// -----------------------------------------------------------------------
// Pointer walk — fill in per-game. Fed from a resolved offsets table.
// -----------------------------------------------------------------------

typedef NTSTATUS (*pMmCopyVirtualMemory)(PEPROCESS SourceProcess,
                                          PVOID SourceAddress,
                                          PEPROCESS TargetProcess,
                                          PVOID TargetAddress,
                                          SIZE_T BufferSize,
                                          KPROCESSOR_MODE PreviousMode,
                                          PSIZE_T ReturnSize);
static pMmCopyVirtualMemory g_MmCopy = nullptr;

static NTSTATUS attach_and_read(PEPROCESS proc, PVOID src, PVOID dst, SIZE_T size) {
    SIZE_T n = 0;
    return g_MmCopy(proc, src, PsGetCurrentProcess(), dst, size,
                    KernelMode, &n);
}

// -----------------------------------------------------------------------
// Worker thread — polls the game, publishes to shared page.
// -----------------------------------------------------------------------

static VOID worker(PVOID ctx) {
    UNREFERENCED_PARAMETER(ctx);
    LARGE_INTEGER interval; interval.QuadPart = -50000;   // 5ms
    while (!InterlockedCompareExchange(&g_stop, 0, 0)) {
        if (g_shared) {
            g_shared->tick++;
            // ... read game memory via attach_and_read(game_process, ...),
            // fill g_shared->entities[], g_shared->view_matrix, g_shared->count ...
        }
        KeDelayExecutionThread(KernelMode, FALSE, &interval);
    }
    PsTerminateSystemThread(STATUS_SUCCESS);
}

// -----------------------------------------------------------------------
// Entry — called from mapper via hijacked pointer, no DRIVER_OBJECT.
// -----------------------------------------------------------------------

NTSTATUS DriverEntry(PDRIVER_OBJECT /*DriverObject*/, PUNICODE_STRING /*RegistryPath*/) {
    // Resolve MmCopyVirtualMemory by name (replace with hash-based lookup
    // for sig-scan resistance).
    UNICODE_STRING name = RTL_CONSTANT_STRING(L"MmCopyVirtualMemory");
    g_MmCopy = (pMmCopyVirtualMemory)MmGetSystemRoutineAddress(&name);
    if (!g_MmCopy) return STATUS_NOT_FOUND;

    // Allocate shared block — tag disguised.
    g_shared_va = ExAllocatePool2(POOL_FLAG_NON_PAGED,
                                   sizeof(GLSharedBlock),
                                   'MmDl' /* disguised tag */);
    if (!g_shared_va) return STATUS_INSUFFICIENT_RESOURCES;
    g_shared = (GLSharedBlock*)g_shared_va;
    RtlZeroMemory(g_shared, sizeof(*g_shared));
    g_shared->magic = 'HSLG';  // 'GLSH' little-endian
    g_shared->version = 1;

    // Spawn worker
    HANDLE h = nullptr;
    NTSTATUS s = PsCreateSystemThread(&h, THREAD_ALL_ACCESS, nullptr,
                                       nullptr, nullptr, worker, nullptr);
    if (!NT_SUCCESS(s)) {
        ExFreePool(g_shared_va);
        g_shared_va = nullptr;
        return s;
    }
    ObReferenceObjectByHandle(h, THREAD_ALL_ACCESS, *PsThreadType,
                               KernelMode, (PVOID*)&g_worker_thread, nullptr);
    ZwClose(h);

    // (Mapper patches the DriverEntry return site to avoid returning
    // through the hijacked pointer — PatchGuard safe, see README.)
    return STATUS_SUCCESS;
}

// -----------------------------------------------------------------------
// Deinit — triggered from usermode via a magic VMCALL / MSR write / cold
// pointer the mapper plants. Omitted here; see README.
// -----------------------------------------------------------------------

VOID DriverUnload() {
    InterlockedExchange(&g_stop, 1);
    if (g_worker_thread) {
        KeWaitForSingleObject(g_worker_thread, Executive, KernelMode, FALSE, nullptr);
        ObDereferenceObject(g_worker_thread);
        g_worker_thread = nullptr;
    }
    if (g_shared_va) {
        ExFreePool(g_shared_va);
        g_shared_va = nullptr;
    }
}
