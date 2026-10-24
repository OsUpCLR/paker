# -*- coding: utf-8 -*-
"""Настройки окна: папка игры, геометрия, последние каталоги.

Хранятся в %LOCALAPPDATA%\\TPAK_Manager\\settings.json, чтобы portable-копия
рядом с exe оставалась неизменной (и не ломалась при обновлении).
"""
import json
import os
import tempfile

APP = "TPAK Manager"


def directory():
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") \
        or os.path.expanduser("~")
    folder = os.path.join(base, "TPAK_Manager")
    try:
        os.makedirs(folder, exist_ok=True)
        probe = os.path.join(folder, ".write-test")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("1")
        os.remove(probe)
        return folder
    except Exception:
        fallback = os.path.join(tempfile.gettempdir(), "TPAK_Manager")
        os.makedirs(fallback, exist_ok=True)
        return fallback


def path():
    return os.path.join(directory(), "settings.json")


def load():
    try:
        with open(path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save(data):
    try:
        with open(path(), "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
    except Exception:
        pass


def get(key, default=None):
    return load().get(key, default)


def set(key, value):
    data = load()
    data[key] = value
    save(data)
