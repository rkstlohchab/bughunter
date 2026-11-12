// language: Objective-C++, file: main.mm, target: iOS arm64 dylib
// Entry point — the dylib's __attribute__((constructor)) runs during
// dyld's LC_LOAD_DYLIB resolution, before BGMI's main() is called.
// We spawn the socket-server thread + touch-injector hook immediately.

#import <Foundation/Foundation.h>
#if !GAMELAB_MACOS_BUILD
#  import <UIKit/UIKit.h>
#endif
#import <pthread.h>
#include "common.h"

#include <stdio.h>
#include <stdarg.h>
#include <os/log.h>

void gl_log(const char* fmt, ...) {
    char buf[1024];
    va_list ap; va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    // Visible under Console.app / `idevicesyslog` when the device is
    // paired. Categorized so it doesn't look like a crash report.
    os_log(os_log_create("sh.gamelab", "memory"), "%{public}s", buf);
}

static void* socket_thread_main(void*) {
    gl_log("gamelab_memory: socket thread starting on localhost:%d", GL_PORT);
    gl_start_socket_thread();
    return nullptr;
}

extern "C" void gl_install_antidetect(void);

__attribute__((constructor))
static void gl_init(void) {
    static bool started = false;
    if (started) return;
    started = true;

    gl_log("gamelab_memory: dylib loaded, bundle=%s",
           [[[NSBundle mainBundle] bundleIdentifier] UTF8String]);

    // Anti-detection: deferred 5-9s so launch-window timing stays stock.
    // Safe to call on Among Us / any non-anogs game — the hash hook is
    // a silent no-op there.
    gl_install_antidetect();

    pthread_t t;
    pthread_create(&t, nullptr, socket_thread_main, nullptr);
    pthread_detach(t);

    // Touch injector is lazy — only arms itself after the first
    // combat.start command over the socket. See touch_injector.mm.
}
