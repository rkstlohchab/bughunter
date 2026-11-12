LOCAL_PATH := $(call my-dir)

include $(CLEAR_VARS)

LOCAL_MODULE := gamelab_mem
LOCAL_CPPFLAGS := -std=c++17 -Wall -Wextra -fvisibility=hidden -O2 -DNDEBUG
LOCAL_LDFLAGS := -Wl,--exclude-libs,ALL -s
LOCAL_LDLIBS := -llog
LOCAL_SRC_FILES := \
    main.cpp \
    socket_server.cpp \
    memory_scanner.cpp

include $(BUILD_SHARED_LIBRARY)
