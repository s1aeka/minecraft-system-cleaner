import ctypes
import os
import queue
import shutil
import threading
from collections import deque
from ctypes import wintypes
from typing import Callable

import customtkinter as ctk
import psutil
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure


BYTES_PER_MB = 1024**2
BYTES_PER_GB = 1024**3
SYSTEM_TEMP = r"C:\Windows\Temp"
CARD_COLOR = "#1E1E2E"
WINDOW_COLOR = "#11111B"
ACCENT_GREEN = "#22C55E"
ACCENT_RED = "#EF4444"
TEXT_COLOR = "#CDD6F4"
MUTED_COLOR = "#A6ADC8"


class RecycleBinInfo(ctypes.Structure):
    """Structure used by SHQueryRecycleBinW to measure the Recycle Bin."""

    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("i64Size", ctypes.c_longlong),
        ("i64NumItems", ctypes.c_longlong),
    ]


class App(ctk.CTk):
    """Windows system monitor with real-time charts and Temp cleanup."""

    def __init__(self) -> None:
        super().__init__()

        self.title("PC System Health & Cleaner")
        self.geometry("900x700")
        self.minsize(900, 700)
        self.configure(fg_color=WINDOW_COLOR)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._closing = False
        self._cleaning = False
        self._refreshing_processes = False
        self._metrics_after_id: str | None = None
        self._log_queue: queue.Queue[str] = queue.Queue()
        self.cpu_history: deque[float] = deque(maxlen=30)
        self.ram_history: deque[float] = deque(maxlen=30)

        self.tabs = ctk.CTkTabview(
            self,
            fg_color=WINDOW_COLOR,
            segmented_button_selected_color="#313244",
            segmented_button_selected_hover_color="#45475A",
            segmented_button_unselected_color=CARD_COLOR,
            segmented_button_unselected_hover_color="#313244",
            text_color=TEXT_COLOR,
        )
        self.tabs.pack(fill="both", expand=True, padx=20, pady=20)
        self.dashboard_tab = self.tabs.add("⚡ Дашборд")
        self.processes_tab = self.tabs.add("📊 Процессы")
        self.cleaner_tab = self.tabs.add("🧹 Очистка")

        self._build_dashboard()
        self._build_processes()
        self._build_cleaner()

        self.after(100, self._drain_log_queue)
        self.refresh_processes()
        self._update_realtime_metrics()

    def _build_dashboard(self) -> None:
        tab = self.dashboard_tab
        for column in range(3):
            tab.grid_columnconfigure(column, weight=1, uniform="metric_cards")
        tab.grid_rowconfigure(1, weight=1)

        self.cpu_card, self.cpu_value, self.cpu_progress, _ = (
            self._create_metric_card(tab, 0, "ЦП", "—", "Загрузка процессора")
        )
        self.ram_card, self.ram_value, self.ram_progress, _ = (
            self._create_metric_card(tab, 1, "ОЗУ", "—", "Использование памяти")
        )
        (
            self.disk_card,
            self.disk_value,
            self.disk_progress,
            self.disk_detail,
        ) = self._create_metric_card(tab, 2, "Диск C:", "—", "Занято места")

        chart_card = ctk.CTkFrame(
            tab, fg_color=CARD_COLOR, corner_radius=15, border_width=1,
            border_color="#313244",
        )
        chart_card.grid(
            row=1, column=0, columnspan=3, sticky="nsew", padx=5, pady=(18, 5)
        )
        chart_card.grid_columnconfigure(0, weight=1)
        chart_card.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            chart_card,
            text="Нагрузка системы — последние 30 секунд",
            text_color=TEXT_COLOR,
            font=ctk.CTkFont(size=17, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(14, 4))

        self.figure = Figure(figsize=(8, 3.2), dpi=100, facecolor=CARD_COLOR)
        self.chart_axes = self.figure.add_subplot(111)
        self._style_chart()
        (self.cpu_line,) = self.chart_axes.plot(
            [], [], color=ACCENT_GREEN, linewidth=2, label="ЦП"
        )
        (self.ram_line,) = self.chart_axes.plot(
            [], [], color="#89B4FA", linewidth=2, label="ОЗУ"
        )
        self.chart_axes.legend(
            loc="upper left",
            frameon=False,
            labelcolor=TEXT_COLOR,
            facecolor=CARD_COLOR,
        )
        self.figure.tight_layout(pad=1.5)
        self.chart_canvas = FigureCanvasTkAgg(self.figure, master=chart_card)
        self.chart_canvas.get_tk_widget().grid(
            row=1, column=0, sticky="nsew", padx=10, pady=(0, 12)
        )
        self.chart_canvas.draw()

        self.refresh_button = ctk.CTkButton(
            tab,
            text="Обновить",
            command=self._refresh_metrics_now,
            corner_radius=10,
            fg_color="#313244",
            hover_color="#45475A",
            text_color=TEXT_COLOR,
        )
        self.refresh_button.grid(row=2, column=0, columnspan=3, pady=(12, 8))

    def _create_metric_card(
        self,
        parent: ctk.CTkFrame,
        column: int,
        title: str,
        value: str,
        detail: str,
    ) -> tuple[ctk.CTkFrame, ctk.CTkLabel, ctk.CTkProgressBar, ctk.CTkLabel]:
        card = ctk.CTkFrame(
            parent,
            fg_color=CARD_COLOR,
            corner_radius=15,
            border_width=1,
            border_color="#313244",
        )
        card.grid(row=0, column=column, sticky="nsew", padx=5, pady=5)
        ctk.CTkLabel(
            card,
            text=title,
            text_color=MUTED_COLOR,
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(14, 3))
        value_label = ctk.CTkLabel(
            card,
            text=value,
            text_color=TEXT_COLOR,
            font=ctk.CTkFont(size=26, weight="bold"),
        )
        value_label.pack(anchor="w", padx=16, pady=(1, 0))
        detail_label = ctk.CTkLabel(
            card, text=detail, text_color=MUTED_COLOR, font=ctk.CTkFont(size=12)
        )
        detail_label.pack(anchor="w", padx=16, pady=(0, 8))
        progress = ctk.CTkProgressBar(
            card,
            height=8,
            corner_radius=4,
            fg_color="#313244",
            progress_color=ACCENT_GREEN,
        )
        progress.pack(fill="x", padx=16, pady=(0, 15))
        progress.set(0)
        return card, value_label, progress, detail_label

    def _style_chart(self) -> None:
        axes = self.chart_axes
        axes.set_facecolor(CARD_COLOR)
        axes.set_ylim(0, 100)
        axes.set_xlim(-29, 0)
        axes.set_ylabel("Использование (%)", color=MUTED_COLOR)
        axes.set_xlabel("Секунд назад", color=MUTED_COLOR)
        axes.tick_params(colors=MUTED_COLOR)
        axes.set_xticks([-29, -20, -10, 0])
        axes.set_xticklabels(["30", "20", "10", "сейчас"])
        for spine in axes.spines.values():
            spine.set_color("#45475A")
        axes.grid(True, color="#45475A", alpha=0.45, linewidth=0.7)

    def _build_processes(self) -> None:
        tab = self.processes_tab
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        heading = ctk.CTkFrame(
            tab, fg_color=CARD_COLOR, corner_radius=15, border_width=1,
            border_color="#313244",
        )
        heading.grid(row=0, column=0, sticky="ew", padx=5, pady=(5, 12))
        heading.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            heading,
            text="Процессы с наибольшим потреблением памяти",
            text_color=TEXT_COLOR,
            font=ctk.CTkFont(size=16, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=16, pady=14)
        self.refresh_processes_button = ctk.CTkButton(
            heading,
            text="🔄 Обновить список",
            command=self.refresh_processes,
            corner_radius=10,
            fg_color="#313244",
            hover_color="#45475A",
            text_color=TEXT_COLOR,
        )
        self.refresh_processes_button.grid(row=0, column=1, padx=14, pady=10)

        self.process_list = ctk.CTkScrollableFrame(
            tab, fg_color=CARD_COLOR, corner_radius=15
        )
        self.process_list.grid(row=1, column=0, sticky="nsew", padx=5, pady=(0, 5))
        self.process_list.grid_columnconfigure(0, weight=1)
        self.process_list.grid_columnconfigure(1, weight=0)
        self._show_process_message("Собираю данные о процессах...")

    def _build_cleaner(self) -> None:
        tab = self.cleaner_tab
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)

        options_card = ctk.CTkFrame(
            tab,
            fg_color=CARD_COLOR,
            corner_radius=15,
            border_width=1,
            border_color="#313244",
        )
        options_card.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        ctk.CTkLabel(
            options_card,
            text="Что очистить",
            text_color=TEXT_COLOR,
            font=ctk.CTkFont(size=17, weight="bold"),
        ).pack(anchor="w", padx=18, pady=(14, 6))

        self.clean_temp_var = ctk.BooleanVar(value=True)
        self.clean_recycle_bin_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(
            options_card,
            text="Временные файлы (Temp)",
            variable=self.clean_temp_var,
            onvalue=True,
            offvalue=False,
            progress_color=ACCENT_GREEN,
            button_color=TEXT_COLOR,
            button_hover_color="#BAC2DE",
            text_color=TEXT_COLOR,
        ).pack(anchor="w", padx=18, pady=8)
        ctk.CTkSwitch(
            options_card,
            text="Очистить Корзину",
            variable=self.clean_recycle_bin_var,
            onvalue=True,
            offvalue=False,
            progress_color=ACCENT_GREEN,
            button_color=TEXT_COLOR,
            button_hover_color="#BAC2DE",
            text_color=TEXT_COLOR,
        ).pack(anchor="w", padx=18, pady=(8, 16))

        self.clean_button = ctk.CTkButton(
            tab,
            text="🧹 Запустить очистку",
            command=self.start_cleanup,
            corner_radius=12,
            height=46,
            fg_color=ACCENT_RED,
            hover_color="#DC2626",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=16, weight="bold"),
        )
        self.clean_button.grid(row=1, column=0, sticky="ew", padx=5, pady=12)

        self.cleaner_log = ctk.CTkTextbox(
            tab,
            wrap="word",
            fg_color=CARD_COLOR,
            corner_radius=15,
            text_color=TEXT_COLOR,
            border_width=1,
            border_color="#313244",
        )
        self.cleaner_log.grid(row=2, column=0, sticky="nsew", padx=5, pady=(0, 5))
        self._log("Готово. Выберите элементы для очистки.")

    def _update_realtime_metrics(self) -> None:
        """Sample CPU/RAM and refresh the dashboard once per second."""
        if self._closing:
            return
        self._metrics_after_id = None

        try:
            cpu_percent = psutil.cpu_percent(interval=None)
            cpu_percent = max(0.0, min(cpu_percent, 100.0))
            self.cpu_history.append(cpu_percent)
            self.cpu_value.configure(text=f"{cpu_percent:.0f}%")
            self._set_progress(self.cpu_progress, cpu_percent)
        except (psutil.Error, OSError) as error:
            self.cpu_value.configure(text="Ошибка")
            self._log(f"Ошибка чтения ЦП: {error}")

        try:
            memory = psutil.virtual_memory()
            ram_percent = max(0.0, min(float(memory.percent), 100.0))
            self.ram_history.append(ram_percent)
            self.ram_value.configure(text=f"{ram_percent:.0f}%")
            self._set_progress(self.ram_progress, ram_percent)
        except (psutil.Error, OSError) as error:
            self.ram_value.configure(text="Ошибка")
            self._log(f"Ошибка чтения ОЗУ: {error}")

        try:
            disk = psutil.disk_usage("C:\\")
            disk_percent = max(0.0, min(float(disk.percent), 100.0))
            self.disk_value.configure(
                text=f"{disk.free / BYTES_PER_GB:.1f} ГБ свободно"
            )
            self._set_progress(self.disk_progress, disk_percent)
            self.disk_detail.configure(
                text=f"Занято {disk.used / BYTES_PER_GB:.1f} ГБ из "
                f"{disk.total / BYTES_PER_GB:.1f} ГБ ({disk_percent:.0f}%)"
            )
        except (psutil.Error, OSError) as error:
            self.disk_value.configure(text="Ошибка")
            self._log(f"Ошибка чтения диска C: {error}")

        self._redraw_history_chart()
        self._metrics_after_id = self.after(1000, self._update_realtime_metrics)

    def _refresh_metrics_now(self) -> None:
        if self._metrics_after_id is not None:
            self.after_cancel(self._metrics_after_id)
            self._metrics_after_id = None
        self._update_realtime_metrics()

    @staticmethod
    def _set_progress(progress: ctk.CTkProgressBar, percentage: float) -> None:
        progress.set(percentage / 100)
        progress.configure(
            progress_color=ACCENT_RED if percentage > 85 else ACCENT_GREEN
        )

    def _redraw_history_chart(self) -> None:
        count = max(len(self.cpu_history), len(self.ram_history))
        if count == 0:
            return
        x_values = list(range(-count + 1, 1))
        self.cpu_line.set_data(x_values, list(self.cpu_history))
        self.ram_line.set_data(x_values, list(self.ram_history))
        self.chart_axes.set_xlim(-29, 0)
        self.chart_canvas.draw_idle()

    def refresh_processes(self) -> None:
        if self._refreshing_processes:
            return
        self._refreshing_processes = True
        self.refresh_processes_button.configure(state="disabled")
        self._show_process_message("Собираю данные о процессах...")
        threading.Thread(target=self._load_processes, daemon=True).start()

    def _load_processes(self) -> None:
        processes: list[tuple[str, int, int]] = []
        errors: list[str] = []
        try:
            for process in psutil.process_iter(["name", "memory_info"]):
                try:
                    info = process.info
                    memory_info = info.get("memory_info")
                    if memory_info is not None:
                        processes.append(
                            (
                                info.get("name") or "Неизвестный процесс",
                                process.pid,
                                memory_info.rss,
                            )
                        )
                except (
                    psutil.NoSuchProcess,
                    psutil.AccessDenied,
                    psutil.ZombieProcess,
                ):
                    continue
            processes.sort(key=lambda process_info: process_info[2], reverse=True)
        except psutil.Error as error:
            errors.append(str(error))
        self._dispatch(
            lambda: self._display_processes(
                processes[:10], "; ".join(errors) or None
            )
        )

    def _display_processes(
        self, processes: list[tuple[str, int, int]], error: str | None
    ) -> None:
        for widget in self.process_list.winfo_children():
            widget.destroy()

        if error:
            self._show_process_message(f"Ошибка получения процессов: {error}")
        elif not processes:
            self._show_process_message("Нет доступных данных о процессах.")
        else:
            for column, text in enumerate(("Имя процесса", "PID  |  Память")):
                ctk.CTkLabel(
                    self.process_list,
                    text=text,
                    text_color=MUTED_COLOR,
                    font=ctk.CTkFont(weight="bold"),
                ).grid(row=0, column=column, sticky="w", padx=12, pady=10)
            for row, (name, pid, rss) in enumerate(processes, start=1):
                ctk.CTkLabel(
                    self.process_list, text=name, text_color=TEXT_COLOR, anchor="w"
                ).grid(row=row, column=0, sticky="ew", padx=12, pady=8)
                ctk.CTkLabel(
                    self.process_list,
                    text=f"{pid}  |  {rss / BYTES_PER_MB:.1f} МБ",
                    text_color=TEXT_COLOR,
                ).grid(row=row, column=1, sticky="e", padx=12, pady=8)
        self._refreshing_processes = False
        self.refresh_processes_button.configure(state="normal")

    def _show_process_message(self, message: str) -> None:
        for widget in self.process_list.winfo_children():
            widget.destroy()
        ctk.CTkLabel(
            self.process_list, text=message, text_color=MUTED_COLOR, anchor="w"
        ).grid(row=0, column=0, sticky="ew", padx=12, pady=12)

    def start_cleanup(self) -> None:
        if self._cleaning:
            return
        clean_temp = self.clean_temp_var.get()
        clean_recycle_bin = self.clean_recycle_bin_var.get()
        if not clean_temp and not clean_recycle_bin:
            self._log("Выберите хотя бы один вариант очистки.")
            return
        self._cleaning = True
        self.clean_button.configure(state="disabled")
        self._log("Начата очистка.")
        threading.Thread(
            target=self._run_cleanup,
            args=(clean_temp, clean_recycle_bin),
            daemon=True,
        ).start()

    def _run_cleanup(self, clean_temp: bool, clean_recycle_bin: bool) -> None:
        total_freed = 0
        recycle_bin_size_unknown = False

        if clean_temp:
            temp_paths = self._get_temp_paths()
            for path in temp_paths:
                self._queue_log(f"Сканируемый путь: {path}")
            for path in temp_paths:
                try:
                    total_freed += self._clean_temp_directory(path)
                except (PermissionError, OSError) as error:
                    self._queue_log(f"Не удалось обработать папку {path}: {error}")

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
                    raise OSError(result, "SHEmptyRecycleBinW завершился с ошибкой.")
                size_after = self._query_recycle_bin_size()
                if size_before is not None and size_after is not None:
                    total_freed += max(0, size_before - size_after)
                    self._queue_log("Корзина очищена.")
                else:
                    recycle_bin_size_unknown = True
                    self._queue_log(
                        "Корзина очищена; размер удаленных данных не удалось измерить."
                    )
            except (OSError, AttributeError) as error:
                self._queue_log(f"Ошибка очистки Корзины: {error}")

        if recycle_bin_size_unknown:
            self._queue_log(
                "Итог включает только освобожденное место, которое удалось измерить."
            )
        self._queue_log(
            f"Итого освобождено{' (измерено)' if recycle_bin_size_unknown else ''}: "
            f"{self._format_size(total_freed)} "
            f"({total_freed / BYTES_PER_MB:.2f} МБ)."
        )
        self._dispatch(self._cleanup_finished)

    @staticmethod
    def _get_temp_paths() -> list[str]:
        """Return the requested user Temp and Windows Temp paths once each."""
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

    def _clean_temp_directory(self, path: str) -> int:
        """Delete accessible files and emptied subfolders, preserving the Temp root."""
        freed_bytes = 0
        if not os.path.isdir(path):
            self._queue_log(f"Папка недоступна или не существует: {path}")
            return 0

        def walk_error(error: OSError) -> None:
            error_path = getattr(error, "filename", None) or path
            self._queue_log(f"Пропущен (заблокирован): {error_path}")

        for root, directories, files in os.walk(
            path, topdown=False, onerror=walk_error, followlinks=False
        ):
            for filename in files:
                file_path = os.path.join(root, filename)
                try:
                    file_size = os.path.getsize(file_path)
                    os.remove(file_path)
                    freed_bytes += file_size
                    self._queue_log(f"Удален файл: {file_path}")
                except (PermissionError, OSError):
                    self._queue_log(f"Пропущен (заблокирован): {file_path}")

            for directory in directories:
                directory_path = os.path.join(root, directory)
                try:
                    if os.path.islink(directory_path):
                        os.remove(directory_path)
                        self._queue_log(f"Удалена ссылка: {directory_path}")
                    elif not os.path.exists(directory_path):
                        continue
                    elif os.path.isdir(directory_path) and not os.listdir(directory_path):
                        shutil.rmtree(directory_path)
                        self._queue_log(f"Удалена папка: {directory_path}")
                    else:
                        self._queue_log(
                            f"Пропущена непустая папка: {directory_path}"
                        )
                except (PermissionError, OSError):
                    self._queue_log(
                        f"Пропущен (заблокирован): {directory_path}"
                    )
        return freed_bytes

    @staticmethod
    def _query_recycle_bin_size() -> int | None:
        """Return the current Recycle Bin byte count when the Windows API allows."""
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
            return f"{size / BYTES_PER_GB:.2f} ГБ"
        return f"{size / BYTES_PER_MB:.2f} МБ"

    def _queue_log(self, message: str) -> None:
        self._log_queue.put(message)

    def _drain_log_queue(self) -> None:
        if self._closing:
            return
        while True:
            try:
                message = self._log_queue.get_nowait()
            except queue.Empty:
                break
            self._log(message)
        self.after(100, self._drain_log_queue)

    def _log(self, message: str) -> None:
        self.cleaner_log.insert("end", f"{message}\n")
        self.cleaner_log.see("end")

    def _cleanup_finished(self) -> None:
        self._cleaning = False
        self.clean_button.configure(state="normal")

    def _dispatch(self, callback: Callable[[], None]) -> None:
        if not self._closing:
            self.after(0, callback)

    def _on_close(self) -> None:
        self._closing = True
        if self._metrics_after_id is not None:
            self.after_cancel(self._metrics_after_id)
        self.destroy()


if __name__ == "__main__":
    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")
    app = App()
    app.mainloop()
