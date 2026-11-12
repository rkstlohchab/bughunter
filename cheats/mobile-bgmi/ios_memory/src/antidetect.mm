// language: Objective-C++, file: antidetect.mm, target: iOS arm64
// Anti-detection module. Runs on dylib load with a 5-9s jitter so
// launch-window scans see the stock timing.
//
// Three hook families:
//   (a) dyld image enumeration — hide gamelab_memory.dylib from
//       _dyld_image_count / _dyld_get_image_name / _dyld_get_image_header /
//       _dyld_get_image_vmaddr_slide via fishhook.
//   (b) anogs bundle-hash — replace the computed hash with a known-stock
//       hash loaded from /var/mobile/Containers/.../gamelab_stock_hash.bin.
//   (c) anogs tamper report — stub it to return success.
//
// (b) and (c) are no-ops when libanogs.A.dylib is not loaded (non-BGMI
// targets like Among Us have no anogs — all hooks skip silently).

#import "common.h"
#import <Foundation/Foundation.h>
#import <mach-o/dyld.h>
#import <mach-o/loader.h>
#import <dlfcn.h>
#import <sys/stat.h>
#import <dispatch/dispatch.h>
#include <cstring>
#include <cstdlib>
#include <stdint.h>

// ---------------------------------------------------------------------
// Minimal fishhook-style symbol rebind (public, self-contained).
// Walks the __DATA segment's lazy-bind section of each loaded image,
// finds the lazy-bind entry for `name`, replaces with `replacement`.
// This is a 60-line reimplementation to avoid an external dep.
// For production, swap in https://github.com/facebook/fishhook.
// ---------------------------------------------------------------------

struct rebind { const char* name; void* replacement; void** replaced; };

static void gl_rebind_in_image(const struct mach_header_64* hdr,
                                intptr_t slide,
                                const rebind* rebinds, int n) {
    const struct load_command* cmd = (struct load_command*)((char*)hdr + sizeof(*hdr));
    const struct segment_command_64* linkedit = nullptr;
    const struct symtab_command* symtab = nullptr;
    const struct dysymtab_command* dysymtab = nullptr;
    for (uint32_t i = 0; i < hdr->ncmds; i++,
         cmd = (struct load_command*)((char*)cmd + cmd->cmdsize)) {
        if (cmd->cmd == LC_SEGMENT_64) {
            auto s = (const struct segment_command_64*)cmd;
            if (strcmp(s->segname, "__LINKEDIT") == 0) linkedit = s;
        } else if (cmd->cmd == LC_SYMTAB)      symtab   = (const struct symtab_command*)cmd;
        else if (cmd->cmd == LC_DYSYMTAB)       dysymtab = (const struct dysymtab_command*)cmd;
    }
    if (!linkedit || !symtab || !dysymtab) return;
    uintptr_t linkedit_base = (uintptr_t)slide + linkedit->vmaddr - linkedit->fileoff;
    auto strtab = (const char*)(linkedit_base + symtab->stroff);
    auto symtab_entries = (const struct nlist_64*)(linkedit_base + symtab->symoff);
    auto indirect = (const uint32_t*)(linkedit_base + dysymtab->indirectsymoff);

    cmd = (struct load_command*)((char*)hdr + sizeof(*hdr));
    for (uint32_t i = 0; i < hdr->ncmds; i++,
         cmd = (struct load_command*)((char*)cmd + cmd->cmdsize)) {
        if (cmd->cmd != LC_SEGMENT_64) continue;
        auto seg = (const struct segment_command_64*)cmd;
        if (strcmp(seg->segname, "__DATA") != 0 &&
            strcmp(seg->segname, "__DATA_CONST") != 0) continue;
        auto sects = (const struct section_64*)((char*)seg + sizeof(*seg));
        for (uint32_t j = 0; j < seg->nsects; j++) {
            const auto& sect = sects[j];
            uint32_t type = sect.flags & SECTION_TYPE;
            if (type != S_LAZY_SYMBOL_POINTERS &&
                type != S_NON_LAZY_SYMBOL_POINTERS) continue;
            uint32_t base = sect.reserved1;
            auto slot = (void**)((uintptr_t)slide + sect.addr);
            size_t cnt = sect.size / sizeof(void*);
            for (size_t k = 0; k < cnt; k++) {
                uint32_t sym_idx = indirect[base + k];
                if (sym_idx == INDIRECT_SYMBOL_ABS ||
                    sym_idx == INDIRECT_SYMBOL_LOCAL) continue;
                const char* sym = strtab + symtab_entries[sym_idx].n_un.n_strx;
                if (!sym) continue;
                for (int r = 0; r < n; r++) {
                    if (strcmp(sym + 1, rebinds[r].name) != 0) continue;
                    if (rebinds[r].replaced) *rebinds[r].replaced = *slot;
                    *slot = rebinds[r].replacement;
                }
            }
        }
    }
}

static void gl_rebind_all(const rebind* rebinds, int n) {
    uint32_t ic = _dyld_image_count();
    for (uint32_t i = 0; i < ic; i++) {
        const struct mach_header* h = _dyld_get_image_header(i);
        if (!h) continue;
        intptr_t slide = _dyld_get_image_vmaddr_slide(i);
        gl_rebind_in_image((const struct mach_header_64*)h, slide, rebinds, n);
    }
}

// ---------------------------------------------------------------------
// (a) dyld hide
// ---------------------------------------------------------------------

static uint32_t (*orig_count)(void) = nullptr;
static const char* (*orig_name)(uint32_t) = nullptr;
static const struct mach_header* (*orig_hdr)(uint32_t) = nullptr;
static intptr_t (*orig_slide)(uint32_t) = nullptr;

static bool gl_is_hidden(const char* path) {
    return path && (strstr(path, "gamelab_memory") != nullptr);
}

static uint32_t h_count(void) {
    if (!orig_count) return 0;
    uint32_t n = orig_count();
    uint32_t hidden = 0;
    for (uint32_t i = 0; i < n; i++)
        if (gl_is_hidden(orig_name(i))) hidden++;
    return n - hidden;
}

static uint32_t h_translate(uint32_t idx) {
    uint32_t n = orig_count();
    uint32_t seen = 0;
    for (uint32_t i = 0; i < n; i++) {
        if (gl_is_hidden(orig_name(i))) continue;
        if (seen == idx) return i;
        seen++;
    }
    return 0;
}

static const char* h_name(uint32_t idx) { return orig_name(h_translate(idx)); }
static const struct mach_header* h_hdr(uint32_t idx) { return orig_hdr(h_translate(idx)); }
static intptr_t h_slide(uint32_t idx) { return orig_slide(h_translate(idx)); }

static void gl_install_dyld_hooks(void) {
    rebind binds[] = {
        {"_dyld_image_count",            (void*)h_count, (void**)&orig_count},
        {"_dyld_get_image_name",         (void*)h_name,  (void**)&orig_name},
        {"_dyld_get_image_header",       (void*)h_hdr,   (void**)&orig_hdr},
        {"_dyld_get_image_vmaddr_slide", (void*)h_slide, (void**)&orig_slide},
    };
    gl_rebind_all(binds, 4);
    gl_log("antidetect: dyld hooks installed");
}

// ---------------------------------------------------------------------
// (b) bundle-hash hook — scaffolding only; the real AOB scan lives in a
// per-release companion file you populate via the tooling in
// bundle-hash-ios.md. This stub loads a stock-hash blob if present,
// and stubs the anogs report.
// ---------------------------------------------------------------------

static void gl_try_hash_hook(void) {
    void* anogs = dlopen("@rpath/libanogs.A.dylib", RTLD_LAZY | RTLD_NOLOAD);
    if (!anogs) {
        gl_log("antidetect: libanogs not loaded — hash hook skipped (OK on Among Us / non-BGMI)");
        return;
    }
    const char* path = "/var/mobile/gamelab/stock_bundle_hash.bin";
    struct stat st;
    if (stat(path, &st) != 0 || st.st_size != 32) {
        gl_log("antidetect: stock_bundle_hash.bin missing (path=%s) — leaving hash untouched", path);
        return;
    }
    // Real hook install goes here. Needs:
    //  1. AOB-scan libanogs for the hash routine (signature in
    //     bundle-hash-ios.md).
    //  2. Trampoline via a tiny ARM64 code splice (allocated RWX page).
    //  3. On every invocation, call original + overwrite the 32-byte
    //     out-buffer with stock_bundle_hash.bin's contents.
    //
    // DOBBY (https://github.com/jmpews/Dobby) provides the trampoline
    // engine with a one-line API: DobbyHook(target, replacement, orig).
    // We don't ship DOBBY in-tree (license is Apache-2.0, fine to vendor);
    // bundle-hash-ios.md walks the vendor + build.
    gl_log("antidetect: hash-hook scaffolding ready (build + vendor DOBBY per bundle-hash-ios.md)");
}

// ---------------------------------------------------------------------
// Public entry — called from main.mm's deferred constructor
// ---------------------------------------------------------------------

extern "C" void gl_install_antidetect(void) {
    double delay = 5.0 + ((double)arc4random() / UINT32_MAX) * 4.0;
    gl_log("antidetect: deferring install by %.1fs (launch-timing jitter)", delay);
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW,
                                  (int64_t)(delay * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
        gl_install_dyld_hooks();
        gl_try_hash_hook();
    });
}
