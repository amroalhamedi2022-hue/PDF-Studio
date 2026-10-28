"""Real Windows Job Object integration tests; no user PDFs or external tools."""
import ctypes
import multiprocessing as mp
import os
import time
import unittest

from pdfstudio.windows_job import WindowsJob, WindowsJobError


def _wait_for_parent_gate(gate):
    gate.wait(30)


@unittest.skipUnless(os.name == "nt", "Win32 Job Objects require Windows")
class WindowsJobTests(unittest.TestCase):
    def test_assign_and_kill_on_close(self):
        from ctypes import wintypes

        ctx = mp.get_context("spawn")
        gate = ctx.Event()
        process = ctx.Process(target=_wait_for_parent_gate, args=(gate,))
        job = WindowsJob(768 * 1024 * 1024)
        try:
            process.start()
            job.assign(process.pid)
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.IsProcessInJob.argtypes = (
                wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL))
            kernel.IsProcessInJob.restype = wintypes.BOOL
            kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
            handle = kernel.OpenProcess(0x1000, False, process.pid)
            self.assertTrue(handle)
            try:
                belongs = wintypes.BOOL()
                self.assertTrue(kernel.IsProcessInJob(handle, job._handle, ctypes.byref(belongs)))
                self.assertTrue(belongs.value)
            finally:
                kernel.CloseHandle(handle)
            # No gate release: closing the Job Object must terminate its member.
            job.close()
            process.join(timeout=5)
            self.assertFalse(process.is_alive(), "job close did not terminate worker")
        finally:
            gate.set()
            job.close()
            if process.pid is not None:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=2)
            process.close()

    def test_bad_limits_rejected(self):
        for limit in (0, -1, True):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                WindowsJob(limit)


if __name__ == "__main__":
    unittest.main()
