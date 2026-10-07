# ⛏️ Minecraft System Health & Monitor

A retro-styled, gamified Windows system monitoring and cleaning utility built with **Python**, **PyWebView**, and **Three.js**. Features a dynamic 3D interactive block reacting in real-time to your CPU workload, custom SVG pixel-art graphics, and safe WinAPI cleanup tools.

![Application Showcase](https://raw.githubusercontent.com/s1aeka/minecraft-system-cleaner/main/showcase.png)

## 🚀 Key Features

- 📊 **Real-Time System Metrics:** Live tracking of CPU usage, physical RAM, C: Disk space, and battery status via `psutil`.
- 🧊 **3D Voxel Interactive Engine:** Built with **Three.js**. Features a grass/dirt block that accelerates its rotation speed and spawns particle effects based on real-time CPU load.
- 📜 **Resource-Consuming Mobs (Process Manager):** Displays the top 10 memory-heavy processes with custom SVG pixel-art mob icons and a safe **Kill Process** feature (`psutil.Process.kill`).
- 🧪 **Speed Potion (RAM Trim):** Leverages Windows API (`SetProcessWorkingSetSize`) to request an instant working set memory trim.
- 🧨 **TNT Cleanup Routine:** Safely purges temporary folders (`TEMP`) and empties the Windows Recycle Bin (`SHEmptyRecycleBinW`) in a non-blocking background thread with live log output.

## 🛠️ Tech Stack

- **Backend:** Python 3.14, `pywebview`, `psutil`, `ctypes` (WinAPI Integration)
- **Frontend:** HTML5, CSS3 (Pixel Art styling & 3D Inset UI), JavaScript (ES6+), Three.js (WebGL Canvas)

## ⚙️ Installation & Run

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/s1aeka/minecraft-system-cleaner.git](https://github.com/s1aeka/minecraft-system-cleaner.git)
   cd minecraft-system-cleaner

Non-Destructive Cleanup: Only cleans temporary cache folders and user-confirmed Recycle Bin items. Locked or critical system files are automatically skipped without crashing.

Protected Process Termination: Prevents terminating the application itself and handles Windows AccessDenied errors gracefully.