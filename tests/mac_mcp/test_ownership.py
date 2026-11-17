"""The standalone CE panel cooperates with the backend's OS ownership lock."""
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('ownership_mcp', Path(__file__).resolve().parents[2] / 'src-tauri/resources/mac_memory_mcp.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class OwnershipTests(unittest.TestCase):
    def test_failed_attach_releases_lease_and_closes_partial_backend(self):
        class Backend:
            closed = 0
            def open(self, pid):
                raise m.MemError('open failed')
            def close(self):
                self.closed += 1
        backend = Backend()
        session = m.Session()
        pid = os.getpid() + 1000000
        with patch.object(m, 'make_backend', return_value=backend):
            with self.assertRaisesRegex(m.MemError, 'open failed'):
                session.attach(pid)
        self.assertEqual(backend.closed, 1)
        lease = m.ProcessLease(pid)
        lease.close()
        session.detach()

    def test_detach_releases_lease_even_if_backend_close_fails(self):
        class Backend:
            def open(self, pid):
                pass
            def close(self):
                raise m.MemError('close failed')
        session = m.Session()
        pid = os.getpid() + 1000000
        with patch.object(m, 'make_backend', return_value=Backend()):
            session.attach(pid)
        with self.assertRaisesRegex(m.MemError, 'busy'):
            m.ProcessLease(pid)
        with self.assertRaisesRegex(m.MemError, 'close failed'):
            session.detach()
        lease = m.ProcessLease(pid)
        lease.close()
        self.assertIsNone(session.backend)
        self.assertIsNone(session.lease)
