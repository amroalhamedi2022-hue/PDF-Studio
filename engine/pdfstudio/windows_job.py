"""Windows Job Object confinement for a spawned PDF worker.

Limits committed process memory and kills the process tree when the last job
handle closes. It is NOT a security sandbox: no filesystem or token isolation.
Only standard-library ctypes is used; unsupported/failing Windows APIs fail
closed. Call assign() BEFORE signaling the worker's start gate.
"""
from __future__ import annotations

import ctypes
import os


class WindowsJobError(RuntimeError):
    """A worker could not be placed under enforceable Windows limits."""


class WindowsJob:
    def __init__(self, memory_bytes: int):
        if os.name != "nt":
            raise WindowsJobError("Windows Job Objects require Windows")
        if not isinstance(memory_bytes, int) or isinstance(memory_bytes, bool) or memory_bytes < 1:
            raise ValueError("memory_bytes must be positive")
        from ctypes import wintypes

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        self._kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self._kernel.SetInformationJobObject.argtypes = (
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        self._kernel.SetInformationJobObject.restype = wintypes.BOOL
        self._kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        self._kernel.OpenProcess.restype = wintypes.HANDLE
        self._kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        self._kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self._kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        self._kernel.CloseHandle.restype = wintypes.BOOL

        self._handle = self._kernel.CreateJobObjectW(None, None)
        if not self._handle:
            raise WindowsJobError("CreateJobObjectW failed")
        try:
            limits = ExtendedLimits()
            # PROCESS_MEMORY (0x100), KILL_ON_JOB_CLOSE (0x2000).
            limits.BasicLimitInformation.LimitFlags = 0x100 | 0x2000
            limits.ProcessMemoryLimit = memory_bytes
            if not self._kernel.SetInformationJobObject(
                    self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise WindowsJobError("SetInformationJobObject failed")
        except BaseException:
            self.close()
            raise

    def assign(self, pid: int) -> None:
        if not self._handle or not isinstance(pid, int) or pid < 1:
            raise WindowsJobError("invalid worker process for Job Object")
        # PROCESS_SET_QUOTA | PROCESS_TERMINATE: required for assignment.
        process = self._kernel.OpenProcess(0x0100 | 0x0001, False, pid)
        if not process:
            raise WindowsJobError("OpenProcess for Job Object failed")
        try:
            if not self._kernel.AssignProcessToJobObject(self._handle, process):
                raise WindowsJobError("AssignProcessToJobObject failed")
        finally:
            self._kernel.CloseHandle(process)

    def close(self) -> None:
        if getattr(self, "_handle", None):
            self._kernel.CloseHandle(self._handle)
            self._handle = None
