"""PyWebView launcher and Windows system API for the Minecraft-style monitor."""

import ctypes
import math
import os
import queue
import shutil
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable

import psutil
import webview


BYTES_PER_MB = 1024**2
BYTES_PER_GB = 1024**3
SYSTEM_TEMP = r"C:\Windows\Temp"


class RecycleBinInfo(ctypes.Structure):
    """Windows structure returned by SHQueryRecycleBinW."""

    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("i64Size", ctypes.c_longlong),
        ("i64NumItems", ctypes.c_longlong),
    ]


class SystemAPI:
    """Methods exposed to the WebView JavaScript frontend."""

    def __init__(self) -> None:
        self._window: webview.Window | None = None
        self._clean_lock = threading.Lock()
        self._clean_logs: queue.Queue[str] = queue.Queue()
        self._clean_running = False
        self._clean_result: dict[str, Any] | None = None

    def bind_window(self, window: webview.Window) -> None:
        """Keep the window reference available for the frontend bridge lifecycle."""
        self._window = window

    def get_stats(self) -> dict[str, Any]:
        """Return JSON-compatible CPU, memory, disk, and battery readings."""
        stats: dict[str, Any] = {}
        try:
            stats["cpu_percent"] = psutil.cpu_percent(interval=None)
        except (psutil.Error, OSError) as error:
            stats["cpu_error"] = str(error)

        try:
            memory = psutil.virtual_memory()
            stats["ram_percent"] = memory.percent
            stats["ram_used"] = memory.used
            stats["ram_total"] = memory.total
        except (psutil.Error, OSError) as error:
            stats["ram_error"] = str(error)

        try:
            disk = psutil.disk_usage("C:\\")
            stats["disk_percent"] = disk.percent
            stats["disk_used"] = disk.used
            stats["disk_free"] = disk.free
            stats["disk_total"] = disk.total
        except (psutil.Error, OSError) as error:
            stats["disk_error"] = str(error)

        try:
            battery = psutil.sensors_battery()
            if battery is None:
                stats["battery"] = None
            else:
                stats["battery"] = {
                    "percent": battery.percent,
                    "power_plugged": battery.power_plugged,
                    "seconds_left": int(battery.secsleft),
                }
        except (psutil.Error, OSError, NotImplementedError) as error:
            stats["battery_error"] = str(error)

        return stats

    def get_processes(self) -> list[dict[str, Any]]:
        """Return the ten processes using the most resident memory."""
        processes: list[dict[str, Any]] = []
        for process in psutil.process_iter(["name", "memory_info"]):
            try:
                info = process.info
                memory_info = info.get("memory_info")
                if memory_info is None:
                    continue
                processes.append(
                    {
                        "name": info.get("name") or "Unknown process",
                        "pid": process.pid,
                        "memory_bytes": memory_info.rss,
                        "memory_mb": round(memory_info.rss / BYTES_PER_MB, 1),
                        "create_time": process.create_time(),
                    }
                )
            except (
                psutil.NoSuchProcess,
                psutil.AccessDenied,
                psutil.ZombieProcess,
            ):
                continue
        processes.sort(key=lambda process: process["memory_bytes"], reverse=True)
        return processes[:10]

    def kill_process(
        self, pid: int, expected_create_time: float | None = None
    ) -> dict[str, Any]:
        """Terminate the selected process, refusing invalid, stale IDs and this app."""
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            return {"success": False, "error": "Invalid process ID (PID)."}
        if expected_create_time is not None and (
            isinstance(expected_create_time, bool)
            or not isinstance(expected_create_time, (int, float))
            or not math.isfinite(expected_create_time)
        ):
            return {"success": False, "error": "Invalid process creation timestamp."}
        if pid == os.getpid():
            return {
                "success": False,
                "error": "The application cannot terminate its own process.",
            }

        try:
            process = psutil.Process(pid)
            process_name = process.name()
            if expected_create_time is not None:
                actual_create_time = process.create_time()
                if abs(actual_create_time - expected_create_time) > 0.001:
                    return {
                        "success": False,
                        "error": (
                            f"PID {pid} now belongs to a different process. "
                            "Refresh the process list and try again."
                        ),
                    }
            process.kill()
            return {
                "success": True,
                "pid": pid,
                "name": process_name,
                "message": (
                    f"Process terminated successfully: {process_name} (PID {pid})."
                ),
            }
        except psutil.NoSuchProcess:
            return {
                "success": False,
                "error": f"Process with PID {pid} has already exited or does not exist.",
            }
        except psutil.AccessDenied:
            return {
                "success": False,
                "error": (
                    f"Access denied while terminating PID {pid}. "
                    "Some system processes require administrator privileges."
                ),
            }
        except psutil.Error as error:
            return {
                "success": False,
                "error": f"Could not terminate process PID {pid}: {error}",
            }
        except OSError as error:
            return {
                "success": False,
                "error": f"Windows error while terminating PID {pid}: {error}",
            }

    def flush_ram(self) -> dict[str, Any]:
        """Ask Windows to trim this application process's working set.

        This is a request to Windows for this process only, not a system-wide
        RAM purge. The operating system remains responsible for reclaiming pages.
        """
        try:
            process = psutil.Process()
            before_bytes = process.memory_info().rss

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GetCurrentProcess.argtypes = []
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            kernel32.SetProcessWorkingSetSize.argtypes = [
                wintypes.HANDLE,
                ctypes.c_size_t,
                ctypes.c_size_t,
            ]
            kernel32.SetProcessWorkingSetSize.restype = wintypes.BOOL

            current_process = kernel32.GetCurrentProcess()
            minimum_working_set = ctypes.c_size_t(-1).value
            maximum_working_set = ctypes.c_size_t(-1).value
            ctypes.set_last_error(0)
            succeeded = kernel32.SetProcessWorkingSetSize(
                current_process,
                minimum_working_set,
                maximum_working_set,
            )
            if not succeeded:
                raise ctypes.WinError(ctypes.get_last_error())

            after_bytes = process.memory_info().rss
            trimmed_bytes = max(0, before_bytes - after_bytes)
            return {
                "success": True,
                "before_bytes": before_bytes,
                "after_bytes": after_bytes,
                "trimmed_bytes": trimmed_bytes,
                "trimmed_mb": round(trimmed_bytes / BYTES_PER_MB, 2),
                "message": (
                    "Windows accepted a request to trim this application's working "
                    f"set. Resident memory changed from "
                    f"{before_bytes / BYTES_PER_MB:.2f} MB to "
                    f"{after_bytes / BYTES_PER_MB:.2f} MB "
                    f"(measured decrease: {trimmed_bytes / BYTES_PER_MB:.2f} MB). "
                    "This does not clear RAM used by other applications."
                ),
            }
        except (OSError, psutil.Error, AttributeError) as error:
            return {
                "success": False,
                "error": f"Could not trim this application's working set: {error}",
            }

    def start_clean(
        self, clean_temp: bool, clean_recycle_bin: bool
    ) -> dict[str, Any]:
        """Start a cleanup worker so the WebView remains responsive."""
        if not clean_temp and not clean_recycle_bin:
            return {
                "started": False,
                "error": "Select at least one cleanup option.",
            }
        with self._clean_lock:
            if self._clean_running:
                return {"started": False, "error": "Cleanup is already running."}
            self._drain_pending_logs()
            self._clean_result = None
            self._clean_running = True
        worker = threading.Thread(
            target=self._run_clean_worker,
            args=(clean_temp, clean_recycle_bin),
            name="system-cleaner",
            daemon=True,
        )
        worker.start()
        return {"started": True}

    def clean_system(
        self, clean_temp: bool = True, clean_recycle_bin: bool = False
    ) -> dict[str, Any]:
        """Clean the selected Temp folders and/or Windows Recycle Bin."""
        total_freed = 0
        errors: list[str] = []

        if clean_temp:
            temp_paths = self._get_temp_paths()
            for path in temp_paths:
                self._emit_log(f"Scanning path: {path}")
            for path in temp_paths:
                try:
                    total_freed += self._clean_temp_directory(path, self._emit_log)
                except (PermissionError, OSError) as error:
                    message = f"Could not process folder {path}: {error}"
                    errors.append(message)
                    self._emit_log(message)

        if clean_recycle_bin:
            try:
                size_before = self._query_recycle_bin_size()
                shell32 = ctypes.windll.shell32
                shell32.SHEmptyRecycleBinW.argtypes = [
                    wintypes.HWND,
                    wintypes.LPCWSTR,
                    wintypes.DWORD,
                ]
                shell32.SHEmptyRecycleBinW.restype = ctypes.c_long
                result = shell32.SHEmptyRecycleBinW(None, None, 7)
                if result != 0:
                    raise OSError(result, "SHEmptyRecycleBinW failed.")

                size_after = self._query_recycle_bin_size()
                if size_before is not None and size_after is not None:
                    total_freed += max(0, size_before - size_after)
                    self._emit_log("Recycle Bin cleared.")
                else:
                    self._emit_log(
                        "Recycle Bin cleared; the amount of data removed could not "
                        "be measured."
                    )
            except (OSError, AttributeError) as error:
                message = f"Could not empty the Recycle Bin: {error}"
                errors.append(message)
                self._emit_log(message)

        result = {
            "freed_bytes": total_freed,
            "freed_mb": round(total_freed / BYTES_PER_MB, 2),
            "freed_gb": round(total_freed / BYTES_PER_GB, 3),
            "errors": errors,
        }
        self._emit_log(
            "Total space freed: "
            f"{self._format_size(total_freed)} "
            f"({total_freed / BYTES_PER_MB:.2f} MB)."
        )
        return result

    def get_clean_updates(self) -> dict[str, Any]:
        """Drain queued log lines and report whether the worker has finished."""
        logs = self._drain_pending_logs()
        result = None
        with self._clean_lock:
            running = self._clean_running
            if not running and self._clean_result is not None:
                result = self._clean_result
                self._clean_result = None
        return {"logs": logs, "running": running, "result": result}

    def _run_clean_worker(self, clean_temp: bool, clean_recycle_bin: bool) -> None:
        try:
            result = self.clean_system(clean_temp, clean_recycle_bin)
        except Exception as error:
            self._emit_log(f"Critical cleanup error: {error}")
            result = {
                "freed_bytes": 0,
                "freed_mb": 0,
                "freed_gb": 0,
                "errors": [str(error)],
            }
        finally:
            with self._clean_lock:
                self._clean_running = False
                self._clean_result = result

    def _emit_log(self, message: str) -> None:
        self._clean_logs.put(message)

    def _drain_pending_logs(self) -> list[str]:
        messages: list[str] = []
        while True:
            try:
                messages.append(self._clean_logs.get_nowait())
            except queue.Empty:
                return messages

    @staticmethod
    def _get_temp_paths() -> list[str]:
        """Return the requested user Temp and Windows Temp paths without duplicates."""
        paths: list[str] = []
        user_temp = os.environ.get("TEMP")
        if user_temp:
            paths.append(os.path.abspath(user_temp))
        paths.append(SYSTEM_TEMP)

        unique_paths: list[str] = []
        seen: set[str] = set()
        for path in paths:
            normalized = os.path.normcase(os.path.abspath(path))
            if normalized not in seen:
                unique_paths.append(path)
                seen.add(normalized)
        return unique_paths

    @staticmethod
    def _clean_temp_directory(path: str, log: Callable[[str], None]) -> int:
        """Remove accessible files and emptied subdirectories, but not the root."""
        if not os.path.isdir(path):
            log(f"Folder is unavailable or does not exist: {path}")
            return 0

        freed_bytes = 0

        def on_walk_error(error: OSError) -> None:
            log(
                "Skipped (in use or access denied): "
                f"{getattr(error, 'filename', None) or path}"
            )

        # Bottom-up traversal lets successfully emptied child folders be removed.
        for root, directories, files in os.walk(
            path, topdown=False, onerror=on_walk_error, followlinks=False
        ):
            for filename in files:
                file_path = os.path.join(root, filename)
                try:
                    file_size = os.path.getsize(file_path)
                    os.remove(file_path)
                    freed_bytes += file_size
                    log(f"Deleted file: {file_path}")
                except (PermissionError, OSError):
                    log(f"Skipped (in use or access denied): {file_path}")

            for directory in directories:
                directory_path = os.path.join(root, directory)
                try:
                    if os.path.islink(directory_path):
                        os.remove(directory_path)
                        log(f"Removed symbolic link: {directory_path}")
                    elif not os.path.exists(directory_path):
                        continue
                    elif os.path.isdir(directory_path) and not os.listdir(directory_path):
                        shutil.rmtree(directory_path)
                        log(f"Removed empty folder: {directory_path}")
                    else:
                        log(f"Skipped non-empty folder: {directory_path}")
                except (PermissionError, OSError):
                    log(f"Skipped (in use or access denied): {directory_path}")

        return freed_bytes

    @staticmethod
    def _query_recycle_bin_size() -> int | None:
        """Return the current Recycle Bin size in bytes when the API is available."""
        try:
            shell32 = ctypes.windll.shell32
            shell32.SHQueryRecycleBinW.argtypes = [
                wintypes.LPCWSTR,
                ctypes.POINTER(RecycleBinInfo),
            ]
            shell32.SHQueryRecycleBinW.restype = ctypes.c_long
            info = RecycleBinInfo()
            info.cbSize = ctypes.sizeof(info)
            result = shell32.SHQueryRecycleBinW(None, ctypes.byref(info))
            return info.i64Size if result == 0 else None
        except (AttributeError, OSError):
            return None

    @staticmethod
    def _format_size(size: int) -> str:
        if size >= BYTES_PER_GB:
            return f"{size / BYTES_PER_GB:.2f} GB"
        return f"{size / BYTES_PER_MB:.2f} MB"


def main() -> None:
    """Create the PyWebView window and expose the system API to JavaScript."""
    api = SystemAPI()
    html_path = Path(__file__).with_name("index.html").resolve()
    window = webview.create_window(
        "MINECRAFT // SYSTEM HEALTH & MONITOR",
        html_path.as_uri(),
        js_api=api,
        width=1180,
        height=820,
        min_size=(960, 700),
        background_color="#171717",
    )
    api.bind_window(window)
    webview.start(debug=False)


if __name__ == "__main__":
    main()
