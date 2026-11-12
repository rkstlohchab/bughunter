// language: C++ header, minimal Zygisk API shim.
// Real file ships with topjohnwu/zygisk-module-sample — this is the
// compile-time interface. Use the upstream header for production builds.
#pragma once
#include <jni.h>

namespace zygisk {
    class Api;
    struct AppSpecializeArgs {
        jint  uid;
        jint  gid;
        jintArray gids;
        jstring nice_name;
        jstring app_data_dir;
    };
    class ModuleBase {
    public:
        virtual ~ModuleBase() = default;
        virtual void onLoad(Api* api, JNIEnv* env) = 0;
        virtual void preAppSpecialize(AppSpecializeArgs*) {}
        virtual void postAppSpecialize(const AppSpecializeArgs*) {}
    };
    class Api {
    public:
        enum Option : int { FORCE_DENYLIST_UNMOUNT = 0, DLCLOSE_MODULE_LIBRARY = 1 };
        virtual void setOption(Option) = 0;
    };
}

#define REGISTER_ZYGISK_MODULE(classname) \
    extern "C" [[gnu::visibility("default")]] \
    void zygisk_module_entry(zygisk::Api* api, JNIEnv* env) { \
        static classname inst; inst.onLoad(api, env); \
    }
