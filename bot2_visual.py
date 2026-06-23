"""
bot2_visual.py - AMS2 Visual GUI Bot

Panel visual para automatizar botones de Automobilista 2 mediante reconocimiento
de imagen. No toca el bot original de C:\\Gonky_Server\\bot.
"""

from __future__ import annotations

import atexit
import ctypes
import json
import logging
import os
import queue
import re
import shutil
import random
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    import sv_ttk as _sv_ttk
    _HAS_SV_TTK = True
except ImportError:
    _sv_ttk = None  # type: ignore[assignment]
    _HAS_SV_TTK = False

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

try:
    from PIL import ImageGrab
    import pyautogui
    import win32api
    import win32con
    import win32gui
    import win32process
    import cv2
    import numpy as np
except ImportError as exc:
    raise SystemExit(
        "Faltan dependencias. Ejecuta instalar_dependencias.bat en C:\\Gonky_Server\\bot2"
    ) from exc


if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
else:
    BASE_DIR = Path(__file__).resolve().parent
    RESOURCE_DIR = BASE_DIR

IMG_DIR = BASE_DIR / "img"
if getattr(sys, "frozen", False) and not IMG_DIR.exists() and (RESOURCE_DIR / "img").exists():
    try:
        shutil.copytree(RESOURCE_DIR / "img", IMG_DIR)
    except OSError:
        pass
if not IMG_DIR.exists() and (RESOURCE_DIR / "img").exists():
    IMG_DIR = RESOURCE_DIR / "img"
LOG_DIR = BASE_DIR / "logs"
CONFIG_FILE = BASE_DIR / "config.json"
LOCK_FILE = BASE_DIR / "bot2_visual.lock"


DEFAULT_CONFIG = {
    "window_title": "Automobilista 2",
    "interval_seconds": 5,
    "confidence": 0.80,
    # False = pulsa sin traer la ventana al frente (basta con que sea visible en pantalla)
    "focus_window": False,
    "focus_each_loop": False,
    "require_focus_before_click": False,
    "require_window_visible": True,
    "skip_focus_check": True,
    "dev_mode": False,
    "validate_ams2_capture": True,
    "focus_backoff_seconds": 10,
    "focus_log_interval_seconds": 30,
    "crash_window_titles": ["Crash Report", "BugSplat"],
    "move_away_after_click": True,
    "click_hold_seconds": 0.06,
    "click_retry_interval_seconds": 8,
    "default_wait_gone_timeout": 60,
    "buttons": {
        "vote_start": {
            "enabled": True,
            "images": ["vote_start.png", "vote_start2.png"],
            "block_images": [],
            "color_fallback": "red_top_right",
            "appear_delay_seconds": 15,
            "appear_delay_jitter_seconds": 5,
            "wait_gone_timeout_seconds": 60,
            "retry_while_visible": True,
            "post_click_cooldown_seconds": 30,
        },
        "next": {
            "enabled": True,
            "images": ["next.png"],
            "appear_delay_seconds": 3,
            "appear_delay_jitter_seconds": 3,
            "wait_gone_timeout_seconds": 60,
            "retry_while_visible": True,
        },
        "return_lobby": {
            "enabled": True,
            "images": ["return_lobby.png", "return_lobby2.png"],
            "appear_delay_seconds": 8,
            "appear_delay_jitter_seconds": 4,
            "wait_gone_timeout_seconds": 60,
            "retry_while_visible": True,
        },
        "ready": {
            "enabled": False,
            "images": ["ready.png"],
            "appear_delay_seconds": 3,
            "appear_delay_jitter_seconds": 2,
            "wait_gone_timeout_seconds": 30,
            "retry_while_visible": True,
        },
    },
}


def ensure_config() -> dict:
    if not CONFIG_FILE.exists():
        save_json(CONFIG_FILE, DEFAULT_CONFIG)
        return json.loads(json.dumps(DEFAULT_CONFIG))

    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, json.JSONDecodeError):
        backup = CONFIG_FILE.with_suffix(f".broken-{int(time.time())}.json")
        try:
            CONFIG_FILE.replace(backup)
        except OSError:
            pass
        save_json(CONFIG_FILE, DEFAULT_CONFIG)
        return json.loads(json.dumps(DEFAULT_CONFIG))

    merged = merge_defaults(loaded, DEFAULT_CONFIG)
    if merged != loaded:
        save_json(CONFIG_FILE, merged)
    return merged


def merge_defaults(current: dict, default: dict) -> dict:
    result = json.loads(json.dumps(default))

    def merge(dst: dict, src: dict) -> None:
        for key, value in src.items():
            if isinstance(value, dict) and isinstance(dst.get(key), dict):
                merge(dst[key], value)
            else:
                dst[key] = value

    merge(result, current)
    return result


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def sanitize_name(value: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip().lower())
    clean = re.sub(r"_+", "_", clean).strip("_")
    return clean or "boton"


def unique_path(directory: Path, file_name: str) -> Path:
    candidate = directory / file_name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    idx = 2
    while True:
        next_candidate = directory / f"{stem}_{idx}{suffix}"
        if not next_candidate.exists():
            return next_candidate
        idx += 1


def process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_lock() -> None:
    if LOCK_FILE.exists():
        try:
            locked_pid = int((LOCK_FILE.read_text(encoding="ascii") or "0").strip())
        except (OSError, ValueError):
            locked_pid = 0
        if process_exists(locked_pid):
            raise RuntimeError(f"Ya hay una instancia de bot2 abierta (PID {locked_pid}).")
        try:
            LOCK_FILE.unlink()
        except OSError as exc:
            raise RuntimeError("Hay un bot2_visual.lock bloqueado. Borralo manualmente si no hay bot abierto.") from exc

    try:
        LOCK_FILE.write_text(str(os.getpid()), encoding="ascii")
    except FileExistsError as exc:
        raise RuntimeError("Ya hay una instancia de bot2 abierta.") from exc


def cleanup_lock() -> None:
    try:
        if LOCK_FILE.exists() and LOCK_FILE.read_text(encoding="ascii").strip() == str(os.getpid()):
            LOCK_FILE.unlink()
    except OSError:
        pass


atexit.register(cleanup_lock)


class QueueLogHandler(logging.Handler):
    def __init__(self, log_queue: queue.Queue[str]) -> None:
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record: logging.LogRecord) -> None:
        self.log_queue.put(self.format(record))


@dataclass
class ButtonRuntime:
    key: str
    first_seen_at: float | None = None
    effective_delay: float = 0.0
    clicked_until_gone: bool = False
    last_location: object | None = None
    last_click_at: float | None = None


@dataclass
class BotState:
    running: bool = False
    stop_event: threading.Event = field(default_factory=threading.Event)
    worker: threading.Thread | None = None


class VisualBotCore:
    def __init__(self, config: dict, logger: logging.Logger) -> None:
        self.config = config
        self.log = logger
        # Runtime state por ventana: {hwnd_or_None: {button_key: ButtonRuntime}}
        # None = modo pantalla completa
        self.window_runtime: dict[int | None, dict[str, ButtonRuntime]] = {}
        # Backoff y warnings por ventana
        self.focus_failed_until: dict[int | None, float] = {}
        self.last_focus_warning_at: dict[int | None, float] = {}
        self.window_was_found: dict[int | None, bool] = {}
        self.last_crash_warning_at = 0.0
        self.last_locate_warning_at: dict[str, float] = {}
        self.last_capture_warning_at = 0.0
        self.last_vote_cooldown_log_at = 0.0
        self.last_no_window_warning_at = 0.0
        pyautogui.FAILSAFE = False

    def update_config(self, config: dict) -> None:
        self.config = config

    def _runtime_for(self, hwnd: int | None) -> dict[str, ButtonRuntime]:
        if hwnd not in self.window_runtime:
            self.window_runtime[hwnd] = {}
        return self.window_runtime[hwnd]

    def force_left_up(self) -> None:
        try:
            pyautogui.mouseUp(button="left")
        finally:
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

    def _click_abs(self, x: int, y: int, hold_seconds: float) -> None:
        """Click en coordenadas de pantalla absolutas, compatible con monitores en X negativa."""
        ctypes.windll.user32.SetCursorPos(int(x), int(y))
        time.sleep(0.05)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(hold_seconds)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

    def _get_title_parts(self) -> list[str]:
        """Parsea window_title como lista de términos (coma como separador). Vacío = pantalla completa."""
        raw = self.config.get("window_title", "")
        if isinstance(raw, list):
            return [t.strip().lower() for t in raw if t.strip()]
        return [t.strip().lower() for t in str(raw).split(",") if t.strip()]

    def find_windows(self) -> list[int]:
        """Devuelve todos los hwnds visibles que coincidan con algún título configurado.
        Lista vacía = modo pantalla completa (sin restricción de ventana)."""
        title_parts = self._get_title_parts()
        if not title_parts:
            return []

        require_visible = self.config.get("require_window_visible", True)
        matches: list[int] = []

        def collect(hwnd: int, _extra: object) -> None:
            if require_visible and not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd).lower()
            if title and any(part in title for part in title_parts):
                matches.append(hwnd)

        win32gui.EnumWindows(collect, None)
        return matches

    def find_window(self) -> int | None:
        """Primera ventana coincidente, o None (sin restricción / no encontrada)."""
        windows = self.find_windows()
        return windows[0] if windows else None

    def list_windows(self) -> list[tuple[int, str]]:
        results: list[tuple[int, str]] = []

        def collect(hwnd: int, _extra: object) -> None:
            title = win32gui.GetWindowText(hwnd)
            if title:
                results.append((hwnd, title))

        win32gui.EnumWindows(collect, None)
        return sorted(results, key=lambda x: x[1].lower())

    def find_crash_window(self) -> tuple[int, str] | None:
        title_parts = [
            str(part).lower()
            for part in self.config.get("crash_window_titles", [])
            if str(part).strip()
        ]
        matches: list[tuple[int, str]] = []

        def collect(hwnd: int, _extra: object) -> None:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            lower_title = title.lower()
            if title and any(part in lower_title for part in title_parts):
                matches.append((hwnd, title))

        win32gui.EnumWindows(collect, None)
        return matches[0] if matches else None

    def crash_dialog_visible(self) -> bool:
        crash = self.find_crash_window()
        if not crash:
            return False

        now = time.time()
        interval = float(self.config.get("focus_log_interval_seconds", 30))
        if now - self.last_crash_warning_at >= interval:
            self.log.warning("Crash window detected: '%s'. Bot paused.", crash[1])
            self.last_crash_warning_at = now
        return True

    def _force_foreground(self, hwnd: int) -> None:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)

    def _dev_log(self, msg: str, *args) -> None:
        if self.config.get("dev_mode", False):
            self.log.info(msg, *args)

    def bring_to_front(self, hwnd: int | None = None) -> bool:
        """Intenta traer la ventana al frente. Si hwnd es None (pantalla completa) devuelve True directamente."""
        if hwnd is None:
            return True

        if self.crash_dialog_visible():
            return False

        now = time.time()
        if now < self.focus_failed_until.get(hwnd, 0.0):
            return False

        log_interval = float(self.config.get("focus_log_interval_seconds", 30))
        backoff = float(self.config.get("focus_backoff_seconds", 10))
        skip_focus_check = self.config.get("skip_focus_check", False)

        try:
            title = win32gui.GetWindowText(hwnd)
        except Exception:
            title = ""

        if not title:
            if self.window_was_found.get(hwnd, False):
                self.log.warning("Window lost (hwnd=%d).", hwnd)
                self.window_was_found[hwnd] = False
            self.focus_failed_until[hwnd] = now + backoff
            return False

        if not self.window_was_found.get(hwnd, False):
            self.log.info("Window detected: '%s' (hwnd=%d).", title, hwnd)
            self.window_was_found[hwnd] = True

        if win32gui.GetForegroundWindow() == hwnd:
            return True

        try:
            self._force_foreground(hwnd)
            time.sleep(0.5)
        except Exception as exc:
            if now - self.last_focus_warning_at.get(hwnd, 0.0) >= log_interval:
                self.log.warning("Could not bring window to front (hwnd=%d): %s", hwnd, exc)
                self.last_focus_warning_at[hwnd] = now
            self.focus_failed_until[hwnd] = now + backoff
            return False

        actual_fg = win32gui.GetForegroundWindow()
        if actual_fg != hwnd and now - self.last_focus_warning_at.get(hwnd, 0.0) >= log_interval:
            self._dev_log("Focus no obtenido: ventana=hwnd%d, activo=hwnd%d", hwnd, actual_fg)
            self.last_focus_warning_at[hwnd] = now

        if not skip_focus_check and win32gui.GetForegroundWindow() != hwnd:
            if now - self.last_focus_warning_at.get(hwnd, 0.0) >= log_interval:
                self.log.warning(
                    "Window (hwnd=%d) did not stay in foreground (foreground=%d). "
                    "If the game is fullscreen, enable 'Ignore focus check'.",
                    hwnd, win32gui.GetForegroundWindow(),
                )
                self.last_focus_warning_at[hwnd] = now
            self.focus_failed_until[hwnd] = now + backoff
            return False
        return True

    def enabled_buttons(self) -> dict:
        return {
            key: cfg for key, cfg in self.config.get("buttons", {}).items()
            if cfg.get("enabled", False)
        }

    def ams2_region(self, hwnd: int | None = None) -> tuple[int, int, int, int]:
        if hwnd is not None:
            try:
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                return left, top, max(1, right - left), max(1, bottom - top)
            except Exception:
                pass
        screen = pyautogui.size()
        return 0, 0, screen.width, screen.height

    def screenshot_region(self, region: tuple[int, int, int, int]):
        left, top, width, height = region
        try:
            return ImageGrab.grab(
                bbox=(left, top, left + width, top + height),
                all_screens=True,
            )
        except TypeError:
            return ImageGrab.grab(bbox=(left, top, left + width, top + height))
        except Exception:
            return pyautogui.screenshot(region=region)

    def ams2_capture_looks_current(self, image) -> bool:
        arr = np.array(image)
        if arr.size == 0 or arr.ndim < 3:
            return False

        height, width = arr.shape[:2]
        if height < 120 or width < 300:
            return False

        # Comprueba que la barra de título tenga píxeles claros.
        # Con ventana no-activa la barra es más oscura; usamos umbral bajo (0.04).
        title_strip = arr[:min(45, height), :, :3]
        bright = (
            (title_strip[:, :, 0] > 170)
            & (title_strip[:, :, 1] > 170)
            & (title_strip[:, :, 2] > 170)
        ).mean()
        return bright > 0.04

    def screenshot_ams2_region(self, hwnd: int | None = None):
        left, top, width, height = self.ams2_region(hwnd)
        image = self.screenshot_region((left, top, width, height))
        # En modo pantalla completa (hwnd=None) no validamos la captura
        if hwnd is not None and self.config.get("validate_ams2_capture", True):
            if not self.ams2_capture_looks_current(image):
                now = time.time()
                interval = float(self.config.get("focus_log_interval_seconds", 30))
                if now - self.last_capture_warning_at >= interval:
                    self.log.warning(
                        "Window capture (hwnd=%d) does not look correct. "
                        "If using virtual desktops, keep the window on the active desktop.",
                        hwnd,
                    )
                    self.last_capture_warning_at = now
                return None
        return left, top, width, height, image

    def locate_template_in_ams2(self, image_path: Path, confidence: float, _log_key: str = "", hwnd: int | None = None):
        try:
            captured = self.screenshot_ams2_region(hwnd)
        except Exception:
            return None
        if not captured:
            return None
        left, top, width, height, screenshot = captured

        haystack = np.array(screenshot)
        needle = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if haystack.size == 0 or needle is None or needle.size == 0:
            return None

        haystack_gray = cv2.cvtColor(haystack[:, :, :3], cv2.COLOR_RGB2GRAY)
        needle_gray = cv2.cvtColor(needle, cv2.COLOR_BGR2GRAY)
        needle_h, needle_w = needle_gray.shape[:2]
        if needle_h > haystack_gray.shape[0] or needle_w > haystack_gray.shape[1]:
            return None

        result = cv2.matchTemplate(haystack_gray, needle_gray, cv2.TM_CCOEFF_NORMED)
        _, max_value, _, max_location = cv2.minMaxLoc(result)
        if max_value < confidence:
            return None
        if _log_key == "vote_start":
            self._dev_log(
                "[vote_start] template=%s conf=%.3f en win=(%d,%d,%dx%d)",
                image_path.name, max_value, left, top, width, height,
            )

        x, y = max_location
        return (left + int(x), top + int(y), int(needle_w), int(needle_h))

    def is_vote_start_lobby_location(self, loc, hwnd: int | None = None) -> bool:
        left, top, width, height = self.ams2_region(hwnd)
        x, y, w, h = loc
        rel_x = int(x) - left
        rel_y = int(y) - top

        need_x = int(width * 0.65)
        need_y_min = int(height * 0.05)
        need_y_max = int(height * 0.30)
        passed = (
            rel_x >= need_x
            and need_y_min <= rel_y <= need_y_max
            and 50 <= int(w) <= 300
            and 15 <= int(h) <= 80
        )
        if not passed:
            self._dev_log(
                "[vote_start] lobby_check RECHAZADO: rel=(%d,%d) wh=(%d,%d) "
                "need_x>=%d need_y=%d-%d",
                rel_x, rel_y, w, h, need_x, need_y_min, need_y_max,
            )
        return passed

    def locate_button(self, key: str, use_fallback: bool = True, hwnd: int | None = None):
        button_cfg = self.config.get("buttons", {}).get(key, {})
        confidence = float(self.config.get("confidence", 0.80))
        for name in button_cfg.get("block_images", []):
            path = IMG_DIR / name
            if not path.exists():
                continue
            loc = self.locate_template_in_ams2(path, confidence, hwnd=hwnd)
            if loc:
                if key == "vote_start" and not self.is_vote_start_lobby_location(loc, hwnd):
                    continue
                self._runtime_for(hwnd).setdefault(key, ButtonRuntime(key)).last_location = None
                return None

        for name in button_cfg.get("images", []):
            path = IMG_DIR / name
            if not path.exists():
                continue
            try:
                loc = self.locate_template_in_ams2(path, confidence, _log_key=key, hwnd=hwnd)
                if loc:
                    if key == "vote_start" and not self.is_vote_start_lobby_location(loc, hwnd):
                        continue
                    self._runtime_for(hwnd).setdefault(key, ButtonRuntime(key)).last_location = loc
                    return loc
            except Exception as exc:
                image_not_found = getattr(pyautogui, "ImageNotFoundException", None)
                if image_not_found is not None and isinstance(exc, image_not_found):
                    loc = self.locate_template_in_ams2(path, confidence, _log_key=key, hwnd=hwnd)
                    if loc:
                        if key == "vote_start" and not self.is_vote_start_lobby_location(loc, hwnd):
                            continue
                        self._runtime_for(hwnd).setdefault(key, ButtonRuntime(key)).last_location = loc
                        return loc
                    continue

                now = time.time()
                warn_key = f"{key}/{name}"
                interval = float(self.config.get("focus_log_interval_seconds", 30))
                if now - self.last_locate_warning_at.get(warn_key, 0.0) >= interval:
                    self.log.warning("Error searching image %s: %s", warn_key, exc)
                    self.last_locate_warning_at[warn_key] = now

        fallback = button_cfg.get("color_fallback")
        if use_fallback and fallback == "red_top_right":
            loc = self.locate_red_top_right_button(hwnd)
            if loc:
                if key == "vote_start":
                    if not self.is_vote_start_lobby_location(loc, hwnd):
                        self._dev_log("[vote_start] color_fallback: loc rechazada por lobby_check")
                        return None
                self._runtime_for(hwnd).setdefault(key, ButtonRuntime(key)).last_location = loc
                return loc
        return None

    def locate_red_top_right_button(self, hwnd: int | None = None):
        try:
            captured = self.screenshot_ams2_region(hwnd)
        except Exception:
            return None
        if not captured:
            return None
        left, top, width, height, image = captured

        arr = np.array(image)
        if arr.size == 0:
            return None

        rgb = arr[:, :, :3]
        r = rgb[:, :, 0].astype(np.int16)
        g = rgb[:, :, 1].astype(np.int16)
        b = rgb[:, :, 2].astype(np.int16)
        red_mask = (r >= 150) & (g <= 90) & (b <= 90) & ((r - g) >= 70) & ((r - b) >= 70)
        mask = red_mask.astype("uint8") * 255

        y_start = max(0, int(height * 0.08))
        y_limit = max(y_start + 1, int(height * 0.24))
        x_start = max(0, int(width * 0.70))
        roi = mask[y_start:y_limit, x_start:]
        if roi.size == 0:
            return None

        kernel = np.ones((5, 9), np.uint8)
        roi = cv2.morphologyEx(roi, cv2.MORPH_CLOSE, kernel, iterations=2)
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(roi, 8)

        best = None
        for label in range(1, num_labels):
            x, y, w, h, area = stats[label]
            if area < 300 or w < 35 or h < 12:
                continue
            if w > 175 or h > 42 or area > 6500:
                continue
            score = area + (x * 0.4)
            if best is None or score > best[0]:
                best = (score, x, y, w, h, area)

        if best is None:
            return None

        _, x, y, w, h, _ = best
        blob_left = left + x_start + int(x)
        blob_top = top + y_start + int(y)
        cx = blob_left + int(w) // 2
        cy = blob_top + int(h) // 2
        self._dev_log(
            "[color_fallback] blob en roi=(%d,%d) wh=(%d,%d) -> centro screen=(%d,%d)",
            x, y, w, h, cx, cy,
        )
        return (blob_left, blob_top, int(w), int(h))

    def is_visible(self, key: str, hwnd: int | None = None) -> bool:
        return self.locate_button(key, hwnd=hwnd) is not None

    def vote_start_cooldown_active(self, hwnd: int | None = None) -> bool:
        vote_cfg = self.config.get("buttons", {}).get("vote_start", {})
        cooldown = max(0.0, float(vote_cfg.get("post_click_cooldown_seconds", 0)))
        if not cooldown:
            return False
        vote_rt = self._runtime_for(hwnd).get("vote_start")
        if not vote_rt or not vote_rt.last_click_at:
            return False
        return time.time() - vote_rt.last_click_at < cooldown

    def click_button(self, key: str, hwnd: int | None = None) -> bool:
        if self.crash_dialog_visible():
            return False

        if key != "vote_start" and self.vote_start_cooldown_active(hwnd):
            now = time.time()
            if now - self.last_vote_cooldown_log_at >= 20:
                self.log.info("Click %s blocked: vote_start on cooldown.", key)
                self.last_vote_cooldown_log_at = now
            return False

        if self.config.get("focus_window", False):
            focused = self.bring_to_front(hwnd)
            if self.config.get("require_focus_before_click", False) and not focused:
                return False

        loc = self.locate_button(key, hwnd=hwnd)
        if not loc:
            return False

        x, y = pyautogui.center(loc)
        self.force_left_up()
        time.sleep(0.05)
        try:
            win32api.SetCursorPos((x, y))
        except Exception:
            pass
        time.sleep(0.10)
        self._click_abs(x, y, float(self.config.get("click_hold_seconds", 0.06)))
        time.sleep(0.05)
        self.force_left_up()

        if self.config.get("move_away_after_click", True):
            try:
                wl, wt, ww, wh = self.ams2_region(hwnd)
                win32api.SetCursorPos((wl + ww // 2, wt + wh // 2))
            except Exception:
                pass

        win_left, win_top, win_w, win_h = self.ams2_region(hwnd)
        win_label = f"hwnd={hwnd}" if hwnd else "pantalla"
        self.log.info(">>> CLICK: %s @ (%d,%d) [%s]", key, x, y, win_label)
        self._runtime_for(hwnd).setdefault(key, ButtonRuntime(key)).last_click_at = time.time()
        self._dev_log(
            "    detalle: win_rel=(%d,%d) loc_wh=(%d,%d)",
            x - win_left, y - win_top, loc[2], loc[3],
        )
        return True

    def click_and_wait_gone(self, key: str, stop_event: threading.Event, hwnd: int | None = None) -> bool:
        cfg = self.config.get("buttons", {}).get(key, {})
        timeout = int(cfg.get("wait_gone_timeout_seconds", self.config.get("default_wait_gone_timeout", 60)))
        retry_while_visible = bool(cfg.get("retry_while_visible", False))
        retry_interval = max(1.0, float(self.config.get("click_retry_interval_seconds", 8)))
        if not self.click_button(key, hwnd):
            return False

        self.log.info("Waiting for '%s' to disappear...", key)
        deadline = time.time() + timeout
        next_retry_at = time.time() + retry_interval
        while time.time() < deadline and not stop_event.is_set():
            time.sleep(1)
            if not self.locate_button(key, use_fallback=False, hwnd=hwnd):
                self.log.info("'%s' disappeared.", key)
                return True
            if retry_while_visible and time.time() >= next_retry_at:
                self.log.info("'%s' still visible; retrying click.", key)
                self.click_button(key, hwnd)
                next_retry_at = time.time() + retry_interval

        self.log.warning("'%s' still visible after %ss.", key, timeout)
        return False

    def test_detection(self) -> list[tuple[str, bool, int | None]]:
        windows = self.find_windows()
        if not windows:
            windows = [None]  # pantalla completa

        results = []
        for hwnd in windows:
            if hwnd is not None and self.config.get("focus_window", False):
                self.bring_to_front(hwnd)
            for key in self.config.get("buttons", {}):
                results.append((key, self.is_visible(key, hwnd), hwnd))
        return results

    def wait_visible_delay(self, key: str, rt: ButtonRuntime, stop_event: threading.Event, hwnd: int | None) -> bool:
        deadline = rt.first_seen_at + rt.effective_delay
        while time.time() < deadline and not stop_event.is_set():
            time.sleep(min(1.0, max(0.0, deadline - time.time())))
            if stop_event.is_set():
                return False
            if not self.is_visible(key, hwnd):
                self.log.info("'%s' disappeared before click; cancelled.", key)
                rt.first_seen_at = None
                rt.clicked_until_gone = False
                return False
        return not stop_event.is_set() and self.is_visible(key, hwnd)

    def _process_window(self, hwnd: int | None, stop_event: threading.Event) -> None:
        runtime = self._runtime_for(hwnd)

        for key, cfg in self.enabled_buttons().items():
            if stop_event.is_set():
                break

            rt = runtime.setdefault(key, ButtonRuntime(key))
            now = time.time()
            cooldown = max(0.0, float(cfg.get("post_click_cooldown_seconds", 0)))
            if rt.last_click_at and cooldown and now - rt.last_click_at < cooldown:
                continue

            vote_rt = runtime.get("vote_start")
            vote_start_pending = bool(vote_rt and (vote_rt.first_seen_at is not None or vote_rt.clicked_until_gone))
            if key == "return_lobby" and vote_start_pending:
                rt.first_seen_at = None
                rt.clicked_until_gone = False
                continue
            if key != "vote_start" and self.vote_start_cooldown_active(hwnd):
                rt.first_seen_at = None
                rt.clicked_until_gone = False
                continue

            visible = self.is_visible(key, hwnd)
            if not visible:
                rt.first_seen_at = None
                rt.clicked_until_gone = False
                continue

            delay = float(cfg.get("appear_delay_seconds", 0))
            if rt.first_seen_at is None:
                jitter = random.uniform(0, float(cfg.get("appear_delay_jitter_seconds", 0)))
                rt.first_seen_at = now
                rt.effective_delay = delay + jitter
                win_label = f"hwnd={hwnd}" if hwnd else "pantalla"
                self.log.info(
                    "%s visible [%s]. Waiting %.1fs before click (base=%.0fs jitter=+%.1fs).",
                    key, win_label, rt.effective_delay, delay, jitter,
                )
                if not self.wait_visible_delay(key, rt, stop_event, hwnd):
                    continue

            if rt.clicked_until_gone:
                continue

            if now - rt.first_seen_at < rt.effective_delay:
                continue

            rt.clicked_until_gone = True
            gone = self.click_and_wait_gone(key, stop_event, hwnd)
            if gone:
                rt.clicked_until_gone = False
                rt.first_seen_at = None
            elif cfg.get("retry_while_visible", False):
                rt.clicked_until_gone = False
                retry_interval = max(1.0, float(self.config.get("click_retry_interval_seconds", 8)))
                rt.first_seen_at = time.time() - rt.effective_delay + retry_interval

    def loop(self, stop_event: threading.Event) -> None:
        interval = float(self.config.get("interval_seconds", 5))
        self.window_runtime.clear()
        self.force_left_up()
        self.log.info("Bot running.")

        while not stop_event.is_set():
            if self.crash_dialog_visible():
                stop_event.wait(interval)
                continue

            title_parts = self._get_title_parts()
            if not title_parts:
                # Pantalla completa: un único "slot" sin hwnd
                windows: list[int | None] = [None]
            else:
                found = self.find_windows()
                if not found:
                    now = time.time()
                    log_interval = float(self.config.get("focus_log_interval_seconds", 30))
                    if now - self.last_no_window_warning_at >= log_interval:
                        self.log.warning(
                            "No windows found with title: %s. "
                            "Use 'List windows' to see exact titles.",
                            ", ".join(f"'{t}'" for t in title_parts),
                        )
                        self.last_no_window_warning_at = now
                    stop_event.wait(interval)
                    continue
                windows = found  # type: ignore[assignment]

            if self.config.get("focus_window", False) and self.config.get("focus_each_loop", False):
                for hwnd in windows:
                    if hwnd is not None:
                        self.bring_to_front(hwnd)

            for hwnd in windows:
                if stop_event.is_set():
                    break
                self._process_window(hwnd, stop_event)

            stop_event.wait(interval)

        self.force_left_up()
        self.log.info("Bot stopped.")


class Bot2App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Gonky AMS2 Bot")
        self.geometry("920x720")
        self.minsize(820, 600)

        if _HAS_SV_TTK:
            _sv_ttk.set_theme("dark")

        for _icon_name in ("gonky.ico", "gk_real.ico", "gk.ico"):
            _icon = BASE_DIR / _icon_name
            if _icon.exists():
                try:
                    self.iconbitmap(str(_icon))
                except Exception:
                    continue
                break

        self.after(100, self._apply_title_bar_color)

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.config_data = ensure_config()
        self.state = BotState()
        self.logger = self.build_logger()
        self.core = VisualBotCore(self.config_data, self.logger)

        self.vars: dict[str, tk.Variable] = {}
        self.button_vars: dict[str, tk.BooleanVar] = {}
        self.status_var = tk.StringVar(value="Bot stopped")

        self.create_widgets()
        self.load_config_to_ui()
        self.after(200, self.drain_logs)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _apply_title_bar_color(self) -> None:
        try:
            hwnd = ctypes.windll.user32.GetAncestor(self.winfo_id(), 2)
            # DWMWA_CAPTION_COLOR = 35 (Windows 11+), COLORREF = 0x00BBGGRR
            # Dark red RGB(160, 0, 0) → COLORREF = 0x000000A0
            color = ctypes.c_int(0x000000A0)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 35, ctypes.byref(color), ctypes.sizeof(color)
            )
        except Exception:
            pass

    def build_logger(self) -> logging.Logger:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger("bot2")
        logger.setLevel(logging.INFO)
        logger.handlers.clear()

        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
        queue_handler = QueueLogHandler(self.log_queue)
        queue_handler.setFormatter(formatter)
        logger.addHandler(queue_handler)

        file_name = LOG_DIR / f"bot2_{datetime.now().strftime('%Y%m%d')}.log"
        file_handler = logging.FileHandler(file_name, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        return logger

    def create_widgets(self) -> None:
        main = ttk.Frame(self, padding=12)
        main.pack(fill=tk.BOTH, expand=True)
        main.columnconfigure(0, weight=1)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(4, weight=1)

        title = ttk.Label(main, text="Gonky AMS2 Bot", font=("Segoe UI", 20, "bold"))
        title.grid(row=0, column=0, sticky="w")

        self.status_label = ttk.Label(main, textvariable=self.status_var, font=("Segoe UI", 11, "bold"), foreground="#f87171")
        self.status_label.grid(row=0, column=1, sticky="e")

        controls = ttk.LabelFrame(main, text="Control", padding=(10, 8))
        controls.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 8))
        for idx in range(4):
            controls.columnconfigure(idx, weight=1)

        ttk.Button(controls, text="Start", command=self.start_bot, style="Accent.TButton").grid(row=0, column=0, padx=4, pady=(0, 4), sticky="ew")
        ttk.Button(controls, text="Stop", command=self.stop_bot).grid(row=0, column=1, padx=4, pady=(0, 4), sticky="ew")
        ttk.Button(controls, text="Test detection", command=self.test_detection).grid(row=0, column=2, padx=4, pady=(0, 4), sticky="ew")
        ttk.Button(controls, text="Save config", command=self.save_ui_config).grid(row=0, column=3, padx=4, pady=(0, 4), sticky="ew")
        ttk.Button(controls, text="Open images", command=lambda: os.startfile(IMG_DIR)).grid(row=1, column=0, padx=4, pady=(4, 0), sticky="ew")
        ttk.Button(controls, text="Capture region", command=self.capture_region_dialog).grid(row=1, column=1, padx=4, pady=(4, 0), sticky="ew")
        ttk.Button(controls, text="Add button", command=self.add_button_dialog).grid(row=1, column=2, padx=4, pady=(4, 0), sticky="ew")
        ttk.Button(controls, text="List windows", command=self.list_windows_to_log).grid(row=1, column=3, padx=4, pady=(4, 0), sticky="ew")

        settings = ttk.LabelFrame(main, text="Settings", padding=10)
        settings.grid(row=2, column=0, sticky="nsew", padx=(0, 6), pady=8)
        settings.columnconfigure(1, weight=1)

        self.vars["window_title"] = tk.StringVar()
        self.vars["interval_seconds"] = tk.DoubleVar()
        self.vars["confidence"] = tk.DoubleVar()
        self.vars["focus_window"] = tk.BooleanVar()
        self.vars["move_away_after_click"] = tk.BooleanVar()
        self.vars["click_hold_seconds"] = tk.DoubleVar()
        self.vars["click_retry_interval_seconds"] = tk.DoubleVar()
        self.vars["require_window_visible"] = tk.BooleanVar()
        self.vars["skip_focus_check"] = tk.BooleanVar()
        self.vars["dev_mode"] = tk.BooleanVar()

        rows = [
            ("Window(s)  [comma=multiple, empty=fullscreen]", "window_title"),
            ("Check interval", "interval_seconds"),
            ("Image confidence", "confidence"),
            ("Click duration", "click_hold_seconds"),
            ("Click retry", "click_retry_interval_seconds"),
        ]
        for row, (label, key) in enumerate(rows):
            ttk.Label(settings, text=label).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Entry(settings, textvariable=self.vars[key]).grid(row=row, column=1, sticky="ew", pady=3)

        ttk.Checkbutton(settings, text="Bring window to front before clicking (usually not needed)", variable=self.vars["focus_window"]).grid(row=5, column=0, columnspan=2, sticky="w", pady=3)
        ttk.Checkbutton(settings, text="Move cursor after click", variable=self.vars["move_away_after_click"]).grid(row=6, column=0, columnspan=2, sticky="w", pady=3)
        ttk.Checkbutton(settings, text="Only visible windows (disable if fullscreen)", variable=self.vars["require_window_visible"]).grid(row=7, column=0, columnspan=2, sticky="w", pady=3)
        ttk.Checkbutton(settings, text="Ignore focus check (exclusive fullscreen)", variable=self.vars["skip_focus_check"]).grid(row=8, column=0, columnspan=2, sticky="w", pady=3)
        ttk.Checkbutton(settings, text="Dev mode (detailed log)", variable=self.vars["dev_mode"]).grid(row=9, column=0, columnspan=2, sticky="w", pady=3)

        buttons = ttk.LabelFrame(main, text="Watched buttons", padding=10)
        buttons.grid(row=2, column=1, sticky="nsew", padx=(6, 0), pady=8)
        buttons.columnconfigure(0, weight=0)
        buttons.columnconfigure(1, weight=1)
        buttons.columnconfigure(2, weight=0)
        buttons.columnconfigure(3, weight=0)
        buttons.columnconfigure(4, weight=0)

        self.buttons_frame = buttons

        log_frame = ttk.LabelFrame(main, text="Log", padding=8)
        log_frame.grid(row=4, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.log_text = tk.Text(
            log_frame, height=16, wrap=tk.WORD, state=tk.DISABLED,
            bg="#0d1117", fg="#c9d1d9", insertbackground="#c9d1d9",
            font=("Consolas", 9), relief=tk.FLAT, borderwidth=0, padx=6, pady=4,
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.tag_configure("INFO", foreground="#8b949e")
        self.log_text.tag_configure("WARNING", foreground="#d29922")
        self.log_text.tag_configure("ERROR", foreground="#f85149")
        self.log_text.tag_configure("CRITICAL", foreground="#ff7b72")

    def load_config_to_ui(self) -> None:
        self.vars["window_title"].set(self.config_data.get("window_title", ""))
        self.vars["interval_seconds"].set(float(self.config_data.get("interval_seconds", 5)))
        self.vars["confidence"].set(float(self.config_data.get("confidence", 0.80)))
        self.vars["focus_window"].set(bool(self.config_data.get("focus_window", False)))
        self.vars["move_away_after_click"].set(bool(self.config_data.get("move_away_after_click", True)))
        self.vars["click_hold_seconds"].set(float(self.config_data.get("click_hold_seconds", 0.06)))
        self.vars["click_retry_interval_seconds"].set(float(self.config_data.get("click_retry_interval_seconds", 8)))
        self.vars["require_window_visible"].set(bool(self.config_data.get("require_window_visible", True)))
        self.vars["skip_focus_check"].set(bool(self.config_data.get("skip_focus_check", False)))
        self.vars["dev_mode"].set(bool(self.config_data.get("dev_mode", False)))
        self.render_button_config()

    def render_button_config(self) -> None:
        for child in self.buttons_frame.winfo_children():
            child.destroy()

        _hf = ("Segoe UI", 9, "bold")
        ttk.Label(self.buttons_frame, text="A", font=_hf).grid(row=0, column=0, sticky="w", padx=(2, 4))
        ttk.Label(self.buttons_frame, text="Button", font=_hf).grid(row=0, column=1, sticky="w")
        ttk.Label(self.buttons_frame, text="Delay", font=_hf).grid(row=0, column=2, sticky="w", padx=(6, 0))
        ttk.Label(self.buttons_frame, text="Imgs", font=_hf).grid(row=0, column=3, sticky="w", padx=(6, 0))
        ttk.Separator(self.buttons_frame, orient="horizontal").grid(row=1, column=0, columnspan=5, sticky="ew", pady=(2, 4))

        self.button_vars.clear()
        self.button_delay_vars: dict[str, tk.DoubleVar] = {}
        for row, (key, cfg) in enumerate(self.config_data.get("buttons", {}).items(), start=2):
            enabled_var = tk.BooleanVar(value=bool(cfg.get("enabled", False)))
            delay_var = tk.DoubleVar(value=float(cfg.get("appear_delay_seconds", 0)))
            self.button_vars[key] = enabled_var
            self.button_delay_vars[key] = delay_var

            ttk.Checkbutton(self.buttons_frame, variable=enabled_var).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Label(self.buttons_frame, text=key).grid(row=row, column=1, sticky="w", pady=3)
            ttk.Entry(self.buttons_frame, textvariable=delay_var, width=8).grid(row=row, column=2, sticky="w", pady=3)
            image_count = len(cfg.get("images", []))
            ttk.Label(self.buttons_frame, text=str(image_count)).grid(row=row, column=3, sticky="w", pady=3)
            ttk.Button(self.buttons_frame, text="Remove", command=lambda item=key: self.remove_button(item)).grid(row=row, column=4, sticky="e", pady=3)

    def collect_ui_config(self) -> dict:
        data = merge_defaults(self.config_data, DEFAULT_CONFIG)
        # Permite cadena vacía (pantalla completa) o varios títulos separados por coma
        data["window_title"] = self.vars["window_title"].get().strip()
        data["interval_seconds"] = max(0.5, float(self.vars["interval_seconds"].get()))
        data["confidence"] = min(0.99, max(0.40, float(self.vars["confidence"].get())))
        data["focus_window"] = bool(self.vars["focus_window"].get())
        data["move_away_after_click"] = bool(self.vars["move_away_after_click"].get())
        data["click_hold_seconds"] = max(0.01, float(self.vars["click_hold_seconds"].get()))
        data["click_retry_interval_seconds"] = max(1.0, float(self.vars["click_retry_interval_seconds"].get()))
        data["require_window_visible"] = bool(self.vars["require_window_visible"].get())
        data["skip_focus_check"] = bool(self.vars["skip_focus_check"].get())
        data["dev_mode"] = bool(self.vars["dev_mode"].get())

        for key, cfg in data.get("buttons", {}).items():
            if key in self.button_vars:
                cfg["enabled"] = bool(self.button_vars[key].get())
            if key in self.button_delay_vars:
                cfg["appear_delay_seconds"] = max(0, float(self.button_delay_vars[key].get()))

        return data

    def save_ui_config(self) -> None:
        try:
            self.config_data = self.collect_ui_config()
            save_json(CONFIG_FILE, self.config_data)
            self.core.update_config(self.config_data)
            self.logger.info("Config saved: %s", CONFIG_FILE)
        except Exception as exc:
            messagebox.showerror("Config", f"Could not save config:\n{exc}")

    def start_bot(self) -> None:
        if self.state.running:
            self.logger.info("Bot is already running.")
            return
        self.save_ui_config()
        self.state.stop_event.clear()
        self.state.worker = threading.Thread(target=self.worker_entry, name="bot2-worker", daemon=True)
        self.state.running = True
        self.set_status("Bot running")
        self.state.worker.start()

    def set_status(self, value: str) -> None:
        lower = value.lower()
        if "running" in lower:
            color = "#4ade80"
        elif "stopping" in lower or "testing" in lower:
            color = "#fbbf24"
        else:
            color = "#f87171"

        def _apply() -> None:
            self.status_var.set(value)
            self.status_label.configure(foreground=color)

        if threading.current_thread() is threading.main_thread():
            _apply()
            return
        self.after(0, _apply)

    def worker_entry(self) -> None:
        try:
            self.core.loop(self.state.stop_event)
        except Exception as exc:
            self.logger.exception("Error en bot: %s", exc)
        finally:
            self.state.running = False
            self.set_status("Bot stopped")

    def list_windows_to_log(self) -> None:
        windows = self.core.list_windows()
        self.logger.info("=== Visible windows (%d) ===", len(windows))
        for hwnd, title in windows:
            self.logger.info("  [%d] %s", hwnd, title)
        self.logger.info("=== End of list. Copy the title (or part) into the Window(s) field ===")

    def stop_bot(self) -> None:
        if not self.state.running:
            self.set_status("Bot stopped")
            return
        self.state.stop_event.set()
        self.set_status("Stopping...")

    def test_detection(self) -> None:
        self.save_ui_config()

        def run() -> None:
            self.set_status("Testing detection...")
            try:
                results = self.core.test_detection()
                for key, found, hwnd in results:
                    win_label = f"hwnd={hwnd}" if hwnd else "fullscreen"
                    self.logger.info("Detection %s [%s]: %s", key, win_label, "VISIBLE" if found else "not found")
            finally:
                self.set_status("Bot running" if self.state.running else "Bot stopped")

        threading.Thread(target=run, daemon=True).start()

    def add_button_dialog(self) -> None:
        top = tk.Toplevel(self)
        top.title("Add button")
        top.geometry("560x300")
        top.transient(self)
        top.grab_set()
        top.columnconfigure(1, weight=1)

        name_var = tk.StringVar()
        image_var = tk.StringVar()
        delay_var = tk.DoubleVar(value=3)
        wait_var = tk.DoubleVar(value=30)
        retry_var = tk.BooleanVar(value=True)
        enabled_var = tk.BooleanVar(value=True)

        def choose_image() -> None:
            selected = filedialog.askopenfilename(
                parent=top,
                title="Choose button image",
                filetypes=[
                    ("Images", "*.png *.jpg *.jpeg *.bmp"),
                    ("PNG", "*.png"),
                    ("JPG", "*.jpg *.jpeg"),
                    ("All files", "*.*"),
                ],
            )
            if selected:
                image_var.set(selected)
                if not name_var.get().strip():
                    name_var.set(Path(selected).stem)

        def add_button() -> None:
            try:
                raw_name = name_var.get().strip()
                image_path = Path(image_var.get().strip())
                if not raw_name:
                    messagebox.showwarning("Add button", "Enter a name for the button.", parent=top)
                    return
                if not image_path.exists() or not image_path.is_file():
                    messagebox.showwarning("Add button", "Choose a valid image.", parent=top)
                    return

                key = sanitize_name(raw_name)
                suffix = image_path.suffix.lower()
                if suffix not in {".png", ".jpg", ".jpeg", ".bmp"}:
                    suffix = ".png"

                IMG_DIR.mkdir(parents=True, exist_ok=True)
                target = unique_path(IMG_DIR, f"{key}{suffix}")
                shutil.copy2(image_path, target)

                self.config_data = self.collect_ui_config()
                buttons = self.config_data.setdefault("buttons", {})
                if key in buttons:
                    images = buttons[key].setdefault("images", [])
                    if target.name not in images:
                        images.append(target.name)
                    buttons[key]["enabled"] = bool(enabled_var.get())
                    buttons[key]["appear_delay_seconds"] = max(0, float(delay_var.get()))
                    buttons[key]["wait_gone_timeout_seconds"] = max(1, float(wait_var.get()))
                    buttons[key]["retry_while_visible"] = bool(retry_var.get())
                else:
                    buttons[key] = {
                        "enabled": bool(enabled_var.get()),
                        "images": [target.name],
                        "appear_delay_seconds": max(0, float(delay_var.get())),
                        "wait_gone_timeout_seconds": max(1, float(wait_var.get())),
                        "retry_while_visible": bool(retry_var.get()),
                    }

                save_json(CONFIG_FILE, self.config_data)
                self.core.update_config(self.config_data)
                self.render_button_config()
                self.logger.info("Button added: %s -> %s", key, target.name)
                top.destroy()
            except Exception as exc:
                messagebox.showerror("Add button", f"Could not add:\n{exc}", parent=top)

        ttk.Label(top, text="Name").grid(row=0, column=0, sticky="w", padx=12, pady=(14, 4))
        ttk.Entry(top, textvariable=name_var).grid(row=0, column=1, sticky="ew", padx=12, pady=(14, 4))

        ttk.Label(top, text="Image").grid(row=1, column=0, sticky="w", padx=12, pady=4)
        ttk.Entry(top, textvariable=image_var).grid(row=1, column=1, sticky="ew", padx=(12, 4), pady=4)
        ttk.Button(top, text="Choose...", command=choose_image).grid(row=1, column=2, sticky="ew", padx=(4, 12), pady=4)

        ttk.Label(top, text="Delay before click").grid(row=2, column=0, sticky="w", padx=12, pady=4)
        ttk.Entry(top, textvariable=delay_var, width=10).grid(row=2, column=1, sticky="w", padx=12, pady=4)

        ttk.Label(top, text="Wait for disappear").grid(row=3, column=0, sticky="w", padx=12, pady=4)
        ttk.Entry(top, textvariable=wait_var, width=10).grid(row=3, column=1, sticky="w", padx=12, pady=4)

        ttk.Checkbutton(top, text="Active", variable=enabled_var).grid(row=4, column=1, sticky="w", padx=12, pady=4)
        ttk.Checkbutton(top, text="Retry while still visible", variable=retry_var).grid(row=5, column=1, sticky="w", padx=12, pady=4)

        actions = ttk.Frame(top)
        actions.grid(row=6, column=0, columnspan=3, sticky="e", padx=12, pady=(16, 12))
        ttk.Button(actions, text="Cancel", command=top.destroy).pack(side=tk.RIGHT, padx=4)
        ttk.Button(actions, text="Add", command=add_button).pack(side=tk.RIGHT, padx=4)

    def remove_button(self, key: str) -> None:
        if key in DEFAULT_CONFIG.get("buttons", {}):
            if not messagebox.askyesno("Remove button", f"{key} is a built-in button. Disable it instead of deleting?"):
                return
            if key in self.button_vars:
                self.button_vars[key].set(False)
            self.save_ui_config()
            return

        if not messagebox.askyesno("Remove button", f"Remove '{key}' from configuration?"):
            return
        self.config_data = self.collect_ui_config()
        self.config_data.get("buttons", {}).pop(key, None)
        save_json(CONFIG_FILE, self.config_data)
        self.core.update_config(self.config_data)
        self.render_button_config()
        self.logger.info("Button removed: %s", key)

    def capture_region_dialog(self) -> None:
        top = tk.Toplevel(self)
        top.title("Capture region")
        top.geometry("420x180")
        top.transient(self)
        top.grab_set()

        name_var = tk.StringVar(value="return_lobby")
        ttk.Label(top, text="Button / image name:").pack(anchor="w", padx=12, pady=(12, 4))

        existing = list(self.config_data.get("buttons", {}).keys())
        combo = ttk.Combobox(top, textvariable=name_var, values=existing)
        combo.pack(fill="x", padx=12)

        ttk.Label(
            top,
            text="Click 'Select region': the panel minimizes,\n"
                 "drag a rectangle over the button in the game window\n"
                 "and release. The image is saved automatically.",
            justify="left",
        ).pack(anchor="w", padx=12, pady=8)

        def do_select() -> None:
            name = name_var.get().strip().replace(" ", "_")
            if not name:
                messagebox.showwarning("Capture region", "Enter a name.", parent=top)
                return
            top.destroy()
            self.iconify()
            self.after(300, lambda: self._open_region_selector(name))

        ttk.Button(top, text="Select region", command=do_select).pack(pady=4)

    def _open_region_selector(self, button_name: str) -> None:
        import PIL.ImageTk as ImageTk

        screenshot = pyautogui.screenshot()
        sw, sh = screenshot.size

        overlay = tk.Toplevel(self)
        overlay.overrideredirect(True)
        overlay.geometry(f"{sw}x{sh}+0+0")
        overlay.attributes("-topmost", True)

        from PIL import ImageEnhance
        dimmed = ImageEnhance.Brightness(screenshot).enhance(0.55)
        bg_photo = ImageTk.PhotoImage(dimmed)

        canvas = tk.Canvas(overlay, cursor="cross", highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True)
        canvas.create_image(0, 0, anchor="nw", image=bg_photo)
        canvas._bg_photo = bg_photo

        hint = canvas.create_text(
            sw // 2, 28,
            text=f"Draw rectangle over button  '{button_name}'  —  ESC to cancel",
            fill="white", font=("Segoe UI", 13, "bold"),
        )

        state: dict = {"x0": 0, "y0": 0, "rect": None}

        def on_press(e: tk.Event) -> None:
            state["x0"], state["y0"] = e.x, e.y
            if state["rect"]:
                canvas.delete(state["rect"])
            state["rect"] = canvas.create_rectangle(
                e.x, e.y, e.x, e.y,
                outline="#ff3c3c", width=2, dash=(4, 2),
            )

        def on_drag(e: tk.Event) -> None:
            if state["rect"]:
                canvas.coords(state["rect"], state["x0"], state["y0"], e.x, e.y)

        def on_release(e: tk.Event) -> None:
            x0, y0, x1, y1 = state["x0"], state["y0"], e.x, e.y
            overlay.destroy()
            self.deiconify()
            x0, x1 = sorted([x0, x1])
            y0, y1 = sorted([y0, y1])
            if x1 - x0 < 5 or y1 - y0 < 5:
                self.logger.warning("Region too small, cancelled.")
                return
            cropped = screenshot.crop((x0, y0, x1, y1))
            IMG_DIR.mkdir(parents=True, exist_ok=True)
            path = unique_path(IMG_DIR, f"{button_name}.png")
            cropped.save(path)
            self.config_data = self.collect_ui_config()
            buttons = self.config_data.setdefault("buttons", {})
            if button_name in buttons:
                images = buttons[button_name].setdefault("images", [])
                if path.name not in images:
                    images.append(path.name)
            else:
                buttons[button_name] = {
                    "enabled": True,
                    "images": [path.name],
                    "appear_delay_seconds": 3.0,
                    "wait_gone_timeout_seconds": 60,
                    "retry_while_visible": False,
                }
            save_json(CONFIG_FILE, self.config_data)
            self.core.update_config(self.config_data)
            self.render_button_config()
            self.logger.info("Image saved: %s (%dx%d px)", path, x1 - x0, y1 - y0)
            try:
                os.startfile(path)
            except OSError:
                pass

        def on_escape(_e: tk.Event) -> None:
            overlay.destroy()
            self.deiconify()
            self.logger.info("Capture cancelled.")

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        overlay.bind("<Escape>", on_escape)

    def drain_logs(self) -> None:
        while True:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            if "[WARNING]" in line:
                tag = "WARNING"
            elif "[ERROR]" in line:
                tag = "ERROR"
            elif "[CRITICAL]" in line:
                tag = "CRITICAL"
            else:
                tag = "INFO"
            self.log_text.configure(state=tk.NORMAL)
            self.log_text.insert(tk.END, line + "\n", tag)
            self.log_text.see(tk.END)
            self.log_text.configure(state=tk.DISABLED)
        self.after(200, self.drain_logs)

    def on_close(self) -> None:
        if self.state.running:
            if not messagebox.askyesno("Exit", "Bot is running. Stop it and close?"):
                return
            self.stop_bot()
            time.sleep(0.3)
        cleanup_lock()
        self.destroy()


def main() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    acquire_lock()
    app = Bot2App()
    app.mainloop()


if __name__ == "__main__":
    main()
