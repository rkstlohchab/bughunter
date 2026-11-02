"""Tiny target process for the mac_memory_mcp tests: owns a known int32 and answers on stdin."""
import ctypes, sys
buf = (ctypes.c_int32 * 4)(987654321, 5, 6, 7)
msg = (ctypes.c_char * 32)(*b"BHUNT_MARKER_STRING\0")
print(ctypes.addressof(buf), ctypes.addressof(msg), flush=True)
for line in sys.stdin:
    if line.strip() == "get":
        print(buf[0], flush=True)
    elif line.strip() == "quit":
        break
