// language: C++17, file: main.cpp, platform: Android NDK (arm64)
// Zygisk entry. Hooks into zygote; after app specialization, if the
// running package is one of our targets, spawn the socket server thread.

#include "zygisk.h"
#include "common.h"
#include <jni.h>
#include <pthread.h>
#include <string.h>
#include <string>
#include <unistd.h>

// Forward
void start_socket_server();   // socket_server.cpp

namespace {

bool is_target_package(const char* name) {
    if (!name) return false;
    static const char* targets[] = {
        "com.pubg.imobile",
        "com.tencent.ig",
        "com.activision.callofduty.shooter",
        "com.garena.game.codm",
        "com.dts.freefireth",
        "com.dts.freefiremax",
        nullptr
    };
    for (int i = 0; targets[i]; ++i) {
        if (strcmp(name, targets[i]) == 0) return true;
    }
    return false;
}

class GamelabMem : public zygisk::ModuleBase {
public:
    void onLoad(zygisk::Api* api, JNIEnv* env) override {
        api_ = api;
        env_ = env;
    }

    void preAppSpecialize(zygisk::AppSpecializeArgs* args) override {
        if (!args) return;
        const char* name = env_->GetStringUTFChars(args->nice_name, nullptr);
        target_matched_ = is_target_package(name);
        env_->ReleaseStringUTFChars(args->nice_name, name);
        if (!target_matched_ && api_) {
            api_->setOption(zygisk::Api::DLCLOSE_MODULE_LIBRARY);
        }
    }

    void postAppSpecialize(const zygisk::AppSpecializeArgs*) override {
        if (!target_matched_) return;
        LOGI("gamelab_mem loaded in target process");
        pthread_t t;
        pthread_create(&t, nullptr, &GamelabMem::thread_main, nullptr);
        pthread_detach(t);
    }

private:
    static void* thread_main(void*) {
        start_socket_server();
        return nullptr;
    }
    zygisk::Api* api_ = nullptr;
    JNIEnv*      env_ = nullptr;
    bool         target_matched_ = false;
};

}  // namespace

REGISTER_ZYGISK_MODULE(GamelabMem)
