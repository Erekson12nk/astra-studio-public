from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk

FLASH_ALL = 3
FLASH_UNTIL_SEEN = 12

# Палитра «киберпанк»: жёлтый неон на почти чёрном, голубой — успех, розовый — ошибка.
BG = "#07070c"
PANEL = "#10111a"
PANEL2 = "#171927"
LINE = "#2b2f4a"
TEXT = "#e9fbff"
DIM = "#7f8db5"
ACCENT = "#fcee0a"
ACCENT2 = "#fff65c"
SOFT = "#0f3640"
OK = "#00f0ff"
BAD = "#ff2a6d"
WARN = "#ff9e1b"
WARN_BG = "#33210a"
STAGE = "#000000"
WHITE = "#ffffff"
ON_ACCENT = "#07070c"      # текст на кнопке акцентного цвета

UI_FONT = ("Bahnschrift", 9)
SMALL_FONT = ("Bahnschrift SemiBold", 9)
MICRO_FONT = ("Bahnschrift SemiBold", 7)
BIG_FONT = ("Bahnschrift SemiBold", 14)
MONO_FONT = ("Consolas", 8)

BASE_THEME = "default"

SURFACES = (("", BG), ("Panel", PANEL))


def dark_frame(window):
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()
        dwm = ctypes.windll.dwmapi.DwmSetWindowAttribute
        want = ctypes.c_int(1)
        for attribute in (20, 19):
            if dwm(hwnd, attribute, ctypes.byref(want),
                   ctypes.sizeof(want)) == 0:
                break
        else:
            return False
        shade = ctypes.c_int(int(BG[5:7], 16) << 16 | int(BG[3:5], 16) << 8
                             | int(BG[1:3], 16))
        for attribute in (35, 34):
            dwm(hwnd, attribute, ctypes.byref(shade), ctypes.sizeof(shade))
        return True
    except Exception:
        return False


def attention(window, sound=True) -> bool:
    if sys.platform != "win32":
        return False
    done = False
    try:
        import ctypes
        from ctypes import wintypes

        class Flash(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("hwnd", wintypes.HWND),
                        ("dwFlags", ctypes.c_uint), ("uCount", ctypes.c_uint),
                        ("dwTimeout", ctypes.c_uint)]

        hwnd = ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()
        blink = Flash(ctypes.sizeof(Flash), hwnd, FLASH_ALL | FLASH_UNTIL_SEEN, 0, 0)
        done = bool(ctypes.windll.user32.FlashWindowEx(ctypes.byref(blink)))
    except Exception:
        done = False
    if sound:
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_OK)
        except Exception:
            pass
    return done


def watching(window) -> bool:
    try:
        return bool(window.focus_displayof())
    except Exception:
        return True


def apply(root):
    root.configure(background=BG)
    dark_frame(root)
    style = ttk.Style(root)
    try:
        style.theme_use(BASE_THEME)
    except tk.TclError:
        pass

    style.configure(".", background=BG, foreground=TEXT, fieldbackground=PANEL2,
                    bordercolor=LINE, lightcolor=LINE, darkcolor=LINE,
                    troughcolor=PANEL2, focuscolor=BG, font=UI_FONT,
                    borderwidth=0)
    style.configure("Head.TLabel", background=BG, foreground=TEXT, font=BIG_FONT)
    style.configure("Micro.TLabel", background=BG, foreground=DIM, font=MICRO_FONT)
    for prefix, back in SURFACES:
        name = (prefix + ".") if prefix else ""
        style.configure(name + "TFrame", background=back)
        style.configure(name + "TLabel", background=back, foreground=TEXT, font=UI_FONT)
        style.configure(prefix + "Dim.TLabel", background=back, foreground=DIM)
        style.configure(prefix + "Ok.TLabel", background=back, foreground=OK)
        style.configure(prefix + "Bad.TLabel", background=back, foreground=BAD)
        style.configure(prefix + "Warn.TLabel", background=back, foreground=WARN)
        style.configure(prefix + "Accent.TLabel", background=back, foreground=ACCENT2)

    style.configure("TButton", background=PANEL2, foreground=TEXT, bordercolor=LINE,
                    lightcolor=PANEL2, darkcolor=PANEL2, focuscolor=PANEL2,
                    relief="flat", padding=(10, 5))
    style.map("TButton",
              background=[("pressed", SOFT), ("active", SOFT), ("disabled", PANEL)],
              foreground=[("disabled", DIM)],
              lightcolor=[("active", SOFT)], darkcolor=[("active", SOFT)])

    style.configure("Accent.TButton", background=ACCENT, foreground=ON_ACCENT,
                    bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT)
    style.map("Accent.TButton",
              background=[("pressed", ACCENT), ("active", ACCENT2), ("disabled", PANEL2)],
              foreground=[("disabled", DIM)],
              lightcolor=[("active", ACCENT2)], darkcolor=[("active", ACCENT2)])

    style.configure("TEntry", fieldbackground=PANEL2, foreground=TEXT, bordercolor=LINE,
                    lightcolor=LINE, darkcolor=LINE, insertcolor=TEXT, padding=4)
    style.map("TEntry",
              bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)],
              fieldbackground=[("disabled", PANEL), ("readonly", PANEL)],
              foreground=[("disabled", DIM), ("readonly", DIM)])

    style.configure("TSpinbox", fieldbackground=PANEL2, foreground=TEXT, background=PANEL2,
                    bordercolor=LINE, arrowcolor=DIM, lightcolor=LINE, darkcolor=LINE,
                    insertcolor=TEXT, padding=4)
    style.map("TSpinbox",
              bordercolor=[("focus", ACCENT)],
              arrowcolor=[("disabled", LINE), ("active", TEXT)],
              fieldbackground=[("disabled", PANEL), ("readonly", PANEL2)],
              background=[("disabled", PANEL)],
              foreground=[("disabled", DIM)])

    style.configure("TCombobox", fieldbackground=PANEL2, background=PANEL2, foreground=TEXT,
                    bordercolor=LINE, arrowcolor=DIM, lightcolor=LINE, darkcolor=LINE,
                    padding=4)
    style.map("TCombobox", bordercolor=[("focus", ACCENT)],
              fieldbackground=[("disabled", PANEL), ("readonly", PANEL2)],
              background=[("disabled", PANEL), ("readonly", PANEL2)],
              foreground=[("disabled", DIM), ("readonly", TEXT)],
              selectbackground=[("readonly", PANEL2), ("!focus", PANEL2)],
              selectforeground=[("readonly", TEXT), ("!focus", TEXT)],
              arrowcolor=[("disabled", LINE), ("active", TEXT)])
    root.option_add("*TCombobox*Listbox.background", PANEL2)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", ON_ACCENT)

    for widget in ("TCheckbutton", "TRadiobutton"):
        style.configure(widget, background=PANEL, foreground=TEXT,
                        indicatorcolor=PANEL2, focuscolor=PANEL)
        style.map(widget,
                  background=[("active", PANEL)],
                  indicatorcolor=[("selected", ACCENT), ("pressed", ACCENT2)],
                  foreground=[("disabled", DIM)])

    style.configure("Horizontal.TScale", background=ACCENT, troughcolor=PANEL2,
                    bordercolor=PANEL, lightcolor=ACCENT, darkcolor=ACCENT, borderwidth=0)
    style.map("Horizontal.TScale", background=[("active", ACCENT2), ("disabled", LINE)])

    style.configure("TProgressbar", background=ACCENT, troughcolor=PANEL2,
                    bordercolor=PANEL2, lightcolor=ACCENT, darkcolor=ACCENT)
    style.configure("Thin.Horizontal.TProgressbar", background=ACCENT, troughcolor=PANEL2,
                    bordercolor=PANEL2, lightcolor=ACCENT, darkcolor=ACCENT, thickness=4)

    style.configure("TScrollbar", background=PANEL2, troughcolor=PANEL, bordercolor=PANEL,
                    arrowcolor=DIM, lightcolor=PANEL2, darkcolor=PANEL2)
    style.map("TScrollbar", background=[("active", LINE)])

    style.configure("Treeview", background=PANEL2, fieldbackground=PANEL2,
                    foreground=TEXT, bordercolor=LINE, rowheight=22)
    style.configure("Treeview.Heading", background=PANEL, foreground=DIM,
                    font=SMALL_FONT, relief="flat")
    style.map("Treeview.Heading", background=[("active", PANEL)])
    style.map("Treeview", background=[("selected", SOFT)],
              foreground=[("selected", TEXT)])

    style.configure("TNotebook", background=BG, bordercolor=LINE,
                    lightcolor=BG, darkcolor=BG, tabmargins=(2, 4, 2, 0))
    style.configure("TNotebook.Tab", background=PANEL, foreground=DIM,
                    lightcolor=PANEL, darkcolor=PANEL, bordercolor=LINE,
                    padding=(14, 6))
    style.map("TNotebook.Tab",
              background=[("selected", PANEL2), ("active", PANEL2)],
              foreground=[("selected", TEXT), ("active", TEXT)],
              lightcolor=[("selected", PANEL2)],
              expand=[("selected", (0, 0, 0, 0))])

    style.configure("TLabelframe", background=BG, foreground=DIM,
                    bordercolor=LINE, lightcolor=BG, darkcolor=BG)
    style.configure("TLabelframe.Label", background=BG, foreground=DIM)
    style.configure("Panel.TLabelframe", background=PANEL, foreground=DIM,
                    bordercolor=PANEL, lightcolor=PANEL, darkcolor=PANEL,
                    borderwidth=0, padding=10)
    style.configure("Panel.TLabelframe.Label", background=PANEL, foreground=DIM,
                    font=SMALL_FONT)
    style.configure("TPanedwindow", background=BG)
    style.configure("TSeparator", background=LINE)
    return style


def round_rect(canvas, x1, y1, x2, y2, radius, **kwargs):
    r = max(1, min(int(radius), int((x2 - x1) / 2), int((y2 - y1) / 2)))
    points = [
        x1 + r, y1, x1 + r, y1, x2 - r, y1, x2 - r, y1, x2, y1,
        x2, y1 + r, x2, y1 + r, x2, y2 - r, x2, y2 - r, x2, y2,
        x2 - r, y2, x2 - r, y2, x1 + r, y2, x1 + r, y2, x1, y2,
        x1, y2 - r, x1, y2 - r, x1, y1 + r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)
