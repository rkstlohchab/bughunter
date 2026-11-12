// language: C++17, file: socket_server.cpp, platform: Android NDK
// Abstract UNIX socket server inside the game process. Accepts newline-
// terminated JSON requests from the PC side (via adb forward), dispatches
// to memory_scanner. One request at a time, one connection (adb forward
// pipes one TCP session through).

#include "common.h"

#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <errno.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <cstddef>

#include <string>
#include <vector>
#include <sstream>

// forward from memory_scanner.cpp
std::string handle_request(const std::string& line);

static const char* ABSTRACT_NAME = "gamelab_mem";

static int make_abstract_socket() {
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) {
        LOGE("socket() failed: %s", strerror(errno));
        return -1;
    }
    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    // Abstract namespace: leading null byte
    addr.sun_path[0] = '\0';
    strncpy(addr.sun_path + 1, ABSTRACT_NAME, sizeof(addr.sun_path) - 2);
    socklen_t len = offsetof(sockaddr_un, sun_path) + 1 + strlen(ABSTRACT_NAME);
    if (bind(fd, reinterpret_cast<sockaddr*>(&addr), len) < 0) {
        LOGE("bind failed: %s", strerror(errno));
        close(fd); return -1;
    }
    if (listen(fd, 2) < 0) {
        LOGE("listen failed: %s", strerror(errno));
        close(fd); return -1;
    }
    LOGI("abstract socket listening: @%s", ABSTRACT_NAME);
    return fd;
}

static std::string read_line(int fd) {
    std::string out;
    char ch;
    while (true) {
        ssize_t n = recv(fd, &ch, 1, 0);
        if (n <= 0) return {};
        if (ch == '\n') break;
        out.push_back(ch);
        if (out.size() > 65536) break;
    }
    return out;
}

static bool write_all(int fd, const char* buf, size_t len) {
    while (len > 0) {
        ssize_t n = send(fd, buf, len, MSG_NOSIGNAL);
        if (n <= 0) return false;
        buf += n; len -= n;
    }
    return true;
}

void start_socket_server() {
    int srv = make_abstract_socket();
    if (srv < 0) return;
    while (true) {
        int cfd = accept(srv, nullptr, nullptr);
        if (cfd < 0) continue;
        LOGI("client connected");
        while (true) {
            std::string line = read_line(cfd);
            if (line.empty()) break;
            std::string reply = handle_request(line);
            reply.push_back('\n');
            if (!write_all(cfd, reply.data(), reply.size())) break;
        }
        close(cfd);
        LOGI("client closed");
    }
}
