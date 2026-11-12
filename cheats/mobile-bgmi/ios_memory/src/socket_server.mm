// language: Objective-C++, file: socket_server.mm, target: iOS arm64 dylib
// Localhost TCP server inside the sandbox. Accepts newline-terminated
// JSON requests from the Mac via usbmuxd (`iproxy -u <udid> 65510 65510`),
// dispatches to memory_scanner / touch_injector.
//
// iOS sandbox note: abstract UNIX sockets (`AF_UNIX` with leading NUL)
// are not permitted inside app sandboxes. We use AF_INET + 127.0.0.1,
// which usbmuxd bridges to the Mac — identical UX to `adb forward`.

#import "common.h"

#import <Foundation/Foundation.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <errno.h>
#include <string.h>
#include <fcntl.h>
#include <string>

static int gl_make_listen_socket() {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) { gl_log("socket() failed: %s", strerror(errno)); return -1; }

    int one = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port   = htons(GL_PORT);
    inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr);
    if (bind(fd, (sockaddr*)&addr, sizeof(addr)) < 0) {
        gl_log("bind(127.0.0.1:%d) failed: %s", GL_PORT, strerror(errno));
        close(fd); return -1;
    }
    if (listen(fd, 2) < 0) {
        gl_log("listen failed: %s", strerror(errno));
        close(fd); return -1;
    }
    gl_log("socket listening on 127.0.0.1:%d", GL_PORT);
    return fd;
}

static std::string gl_read_line(int fd) {
    std::string out; char ch;
    while (true) {
        ssize_t n = recv(fd, &ch, 1, 0);
        if (n <= 0) return {};
        if (ch == '\n') break;
        out.push_back(ch);
        if (out.size() > 65536) break;
    }
    return out;
}

static bool gl_send_all(int fd, const char* buf, size_t len) {
    while (len) {
        ssize_t n = send(fd, buf, len, 0);
        if (n <= 0) return false;
        buf += n; len -= n;
    }
    return true;
}

void gl_start_socket_thread(void) {
    int srv = gl_make_listen_socket();
    if (srv < 0) return;

    while (true) {
        int cfd = accept(srv, nullptr, nullptr);
        if (cfd < 0) { usleep(50000); continue; }
        gl_log("client connected");
        int one = 1;
        setsockopt(cfd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));

        while (true) {
            std::string line = gl_read_line(cfd);
            if (line.empty()) break;
            std::string reply = gl_handle_request(line);
            reply.push_back('\n');
            if (!gl_send_all(cfd, reply.data(), reply.size())) break;
        }
        close(cfd);
        gl_log("client disconnected");
    }
}
