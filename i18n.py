# -*- coding: utf-8 -*-
"""Localization for TPAK Packer.

Russian strings are the source language; the English translation is used when
the OS user language is not Russian. Language is detected automatically at
startup (Windows UI language, then the C locale).
"""
import ctypes
import locale
import os

RU = "ru"
EN = "en"

TRANSLATIONS = {
    "Архив: %s\nФайлов: %d\nРазмер: %s\nУпаковано: %s\nВерсия формата: %s\nИзменения: %s":
        "Archive: %s\nFiles: %d\nSize: %s\nPacked: %s\nFormat version: %s\nChanges: %s",
    "%s %s\n\nУпаковщик и распаковщик .pak Star Conflict (TPAK v7).\nДвойной клик — просмотр файла, Del — удалить, F2 — переименовать.":
        "%s %s\n\nPacker and unpacker for Star Conflict .pak files (TPAK v7).\nDouble-click to preview, Del to delete, F2 to rename.",
    "  (есть изменения)": "  (modified)",
    "%s %s\n\nУпаковщик и распаковщик .pak Star Conflict (TPAK v7).\n":
        "%s %s\n\nPacker and unpacker for Star Conflict .pak files (TPAK v7).\n",
    "(новый архив)": "(new archive)",
    "(новый)": "(new)",
    "<архив>": "<archive>",
    "Архив не открыт": "No archive is open",
    "Архив:": "Archive:",
    "Архив: %s\nФайлов: %d\nРазмер: %s\nУпаковано: %s\n":
        "Archive: %s\nFiles: %d\nSize: %s\nPacked: %s\n",
    "В корень": "Go to root",
    "Версия формата: %s\nИзменения: %s": "Format version: %s\nChanges: %s",
    "Вид": "View",
    "Выберите одну запись": "Select a single entry",
    "Выделить всё": "Select all",
    "Выход": "Exit",
    "Готов": "Ready",
    "Двойной клик — просмотр файла, Del — удалить, F2 — переименовать.":
        "Double-click to preview, Del to delete, F2 to rename.",
    "Добавить папку": "Add folder",
    "Добавить папку…": "Add folder…",
    "Добавить файлы": "Add files",
    "Добавить файлы…": "Add files…",
    "Есть несохранённые изменения. Выйти?":
        "There are unsaved changes. Quit anyway?",
    "Есть несохранённые изменения. Новый архив?":
        "There are unsaved changes. Create a new archive?",
    "Извлечение": "Extracting",
    "Извлечь в папку…": "Extract to folder…",
    "Извлечь сюда": "Extract here",
    "Извлечь…": "Extract…",
    "Имя": "Name",
    "Команды": "Commands",
    "Куда извлечь": "Where to extract",
    "Метод": "Method",
    "На уровень вверх": "Go up one level",
    "Найти…": "Find…",
    "Не удалось открыть пак:\n%s": "Cannot open the archive:\n%s",
    "Не удалось прочитать:\n%s": "Cannot read the entry:\n%s",
    "Не удалось сохранить:\n%s": "Cannot save the archive:\n%s",
    "Новое имя:": "New name:",
    "Новый .pak": "New .pak",
    "О программе": "About",
    "Обновить": "Refresh",
    "Открыть .pak": "Open .pak",
    "Открыть…": "Open…",
    "Отмена": "Cancel",
    "Ошибка извлечения:\n%s": "Extraction failed:\n%s",
    "Ошибка: %s": "Error: %s",
    "Папка для упаковки": "Folder to pack",
    "Переименовать": "Rename",
    "Пересборка пройдена: все файлы совпадают.":
        "Repack check passed: all files match.",
    "Поиск:": "Search:",
    "Правка": "Edit",
    "Проблем: %d\n\n%s": "Problems: %d\n\n%s",
    "Проверка не удалась:\n%s": "Check failed:\n%s",
    "Проверка пересборкой": "Repack check",
    "Просмотр: %s": "Preview: %s",
    "Размер": "Size",
    "Расхождений: %d\n\n%s": "Mismatches: %d\n\n%s",
    "Свойства": "Properties",
    "Сжатие": "Ratio",
    "Сначала откройте или создайте .pak": "Open or create a .pak first",
    "Снять выделение": "Clear selection",
    "Создание пака": "Creating archive",
    "Создать из папки…": "Create from folder…",
    "Создать пустой…": "Create empty…",
    "Сохранение": "Saving",
    "Сохранить": "Save",
    "Сохранить .pak": "Save .pak",
    "Сохранить как…": "Save as…",
    "Текст": "Text",
    "Тест не выполнен:\n%s": "Test failed:\n%s",
    "Тест пройден: пак читается целиком.": "Test passed: the archive reads fine.",
    "Тест целостности": "Integrity test",
    "Удалить": "Delete",
    "Удалить записей: %d?": "Delete %d entries?",
    "Упаковано": "Packed",
    "Файл": "File",
    "Файлов: %d | размер: %s | упаковано: %s | выбрано: %d%s":
        "Files: %d | size: %s | packed: %s | selected: %d%s",
    "Частей": "Parts",
    "добавлено файлов: %d": "files added: %d",
    "есть": "yes",
    "извлечение отменено": "extraction cancelled",
    "извлечено файлов: %d в %s": "files extracted: %d to %s",
    "нет": "no",
    "открыт %s": "opened %s",
    "создание отменено": "creation cancelled",
    "сохранение отменено, файл не изменён": "save cancelled, file unchanged",
    "сохранено: %s": "saved: %s",
}


def detect_language():
    """Return ``ru`` for a Russian OS UI language, otherwise ``en``."""
    forced = os.environ.get("PAKER_LANG", "").strip().lower()
    if forced in (RU, EN):
        return forced
    try:
        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        if (langid & 0x3FF) == 0x19:          # LANG_RUSSIAN
            return RU
    except Exception:
        pass
    try:
        code = (locale.getdefaultlocale()[0] or "")
    except Exception:
        code = ""
    return RU if code.lower().startswith("ru") else EN


LANGUAGE = detect_language()


def L(text):
    """Translate a UI string for the detected OS language."""
    if LANGUAGE == RU:
        return text
    return TRANSLATIONS.get(text, text)
