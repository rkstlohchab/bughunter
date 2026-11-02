"""Protocol + scan-logic tests for src-tauri/resources/mac_memory_mcp.py.

Runs on Linux through the /proc backend (dev/test aid). The Mach backend is macOS-only and is not
exercised here. Run: python3 -m unittest discover -s tests/mac_mcp -v
"""
import json, os, subprocess, sys, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "..", "..", "src-tauri", "resources", "mac_memory_mcp.py")


class Rpc:
    def __init__(self):
        self.p = subprocess.Popen([sys.executable, "-u", SERVER], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.n = 0

    def call(self, method, params=None):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}}) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def tool(self, name, **args):
        r = self.call("tools/call", {"name": name, "arguments": args})["result"]
        return r["content"][0]["text"], r["isError"]

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=5)


class MacMcpTests(unittest.TestCase):
    def setUp(self):
        self.rpc = Rpc()
        self.target = subprocess.Popen([sys.executable, os.path.join(HERE, "target_proc.py")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.addr, self.str_addr = (int(x) for x in self.target.stdout.readline().split())

    def tearDown(self):
        self.target.kill()
        self.target.wait()
        self.target.stdin.close(); self.target.stdout.close()
        self.rpc.close()
        self.rpc.p.stdout.close()

    def target_value(self):
        self.target.stdin.write("get\n")
        self.target.stdin.flush()
        return int(self.target.stdout.readline())

    def attach(self):
        text, err = self.rpc.tool("attach_process", pid=self.target.pid)
        if err and "Operation not permitted" in text:
            self.skipTest("ptrace not permitted in this sandbox")
        self.assertFalse(err, text)

    def test_handshake_and_tool_surface(self):
        init = self.rpc.call("initialize", {})["result"]
        self.assertIn("tools", init["capabilities"])
        names = {t["name"] for t in self.rpc.call("tools/list")["result"]["tools"]}
        self.assertTrue({"attach_process", "scan_value", "next_scan", "write_memory", "freeze_value"} <= names)
        self.assertFalse(any("eval" in n or "exec" in n or "shell" in n or "lua" in n for n in names), names)
        self.assertEqual(self.rpc.call("nope")["error"]["code"], -32601)
        text, err = self.rpc.tool("evaluate_lua", code="return 1")
        self.assertTrue(err)

    def test_requires_attach_and_rejects_bad_pids(self):
        text, err = self.rpc.tool("read_memory", address=self.addr)
        self.assertTrue(err); self.assertIn("attach", text)
        for pid in (0, 1, self.rpc.p.pid):
            _t, err = self.rpc.tool("attach_process", pid=pid)
            self.assertTrue(err)

    def test_scan_narrow_write_and_freeze(self):
        self.attach()
        text, err = self.rpc.tool("scan_value", type="i32", value=987654321)
        self.assertFalse(err, text)
        results, _ = self.rpc.tool("get_scan_results", limit=500)
        self.assertIn(f"0x{self.addr:016x}", results)
        text, _ = self.rpc.tool("read_memory", address=self.addr, type="i32")
        self.assertTrue(text.startswith("987654321"))
        # write, verify the *target* observes it, and that next_scan sees the change
        text, err = self.rpc.tool("write_memory", address=self.addr, type="i32", value=111)
        self.assertFalse(err, text); self.assertIn("matches", text)
        self.assertEqual(self.target_value(), 111)
        text, _ = self.rpc.tool("next_scan", mode="exact", value=111)
        results, _ = self.rpc.tool("get_scan_results")
        self.assertIn(f"0x{self.addr:016x}", results)
        _t, _e = self.rpc.tool("next_scan", mode="unchanged")
        text, _ = self.rpc.tool("next_scan", mode="increased")
        self.assertIn("0 results", text)
        # freeze beats the target changing it back
        text, err = self.rpc.tool("freeze_value", address=self.addr, type="i32", value=777)
        self.assertFalse(err, text)
        self.rpc.tool("write_memory", address=self.addr, type="i32", value=1)
        time.sleep(0.3)
        self.assertEqual(self.target_value(), 777)
        listed, _ = self.rpc.tool("list_frozen")
        self.assertIn("0x", listed)
        self.rpc.tool("unfreeze_value", id=1)
        self.rpc.tool("write_memory", address=self.addr, type="i32", value=5)
        time.sleep(0.2)
        self.assertEqual(self.target_value(), 5)

    def test_string_and_wildcard_aob_scan(self):
        self.attach()
        self.rpc.tool("scan_value", type="string", value="BHUNT_MARKER_STRING")
        out, _ = self.rpc.tool("get_scan_results", limit=500)
        self.assertIn(f"0x{self.str_addr:016x}", out)
        self.rpc.tool("scan_value", type="bytes", value="42 48 55 4e 54 ?? 4d 41 52 4b")
        out, _ = self.rpc.tool("get_scan_results", limit=500)
        self.assertIn(f"0x{self.str_addr:016x}", out)

    def test_bad_input_is_an_error_not_a_crash(self):
        self.attach()
        for args in ({"type": "i8", "value": 9999}, {"type": "nope", "value": 1}, {"type": "bytes", "value": "zz"}):
            _t, err = self.rpc.tool("scan_value", **args)
            self.assertTrue(err, args)
        _t, err = self.rpc.tool("write_memory", address=self.addr, type="bytes", value="??")
        self.assertTrue(err)
        _t, err = self.rpc.tool("read_memory", address=self.addr, size=10**9)
        self.assertTrue(err)
        _t, err = self.rpc.tool("read_memory", address=0x10)
        self.assertTrue(err)
        self.assertEqual(self.rpc.tool("ping")[0], "pong")  # still alive

    def test_detach_clears_state(self):
        self.attach()
        self.rpc.tool("scan_value", type="i32", value=987654321)
        self.rpc.tool("detach_process")
        _t, err = self.rpc.tool("get_scan_results")
        self.assertTrue(err)


if __name__ == "__main__":
    unittest.main()
