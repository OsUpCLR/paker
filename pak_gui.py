# -*- coding: utf-8 -*-
"""TPAK Packer — Two-pane GUI for TPAK v7 .pak archives.

Run:  python pak_gui.py [archive.pak]
"""
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import tpak
from i18n import L
from pak_handler import PakArchive, PakError, human_size

APP = "TPAK Packer"
VERSION = "2.0"
FILETYPES = [("TPAK archive (*.pak)", "*.pak"), ("All files", "*.*")]


def res_path(*parts):
    """Path to a bundled resource (works in source and in a PyInstaller exe)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


class Progress(tk.Toplevel):
    """Modal progress window with a Cancel button."""

    def __init__(self, parent, title):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.cancelled = False
        self.label = ttk.Label(self, text="…", width=58, anchor="w")
        self.label.pack(padx=12, pady=(12, 4))
        self.bar = ttk.Progressbar(self, length=420, mode="determinate")
        self.bar.pack(padx=12, pady=4)
        ttk.Button(self, text=L("Отмена"), command=self._cancel).pack(pady=(4, 12))
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.update_idletasks()

    def _cancel(self):
        self.cancelled = True

    def set(self, i, total, text=""):
        self.bar["maximum"] = max(1, total)
        self.bar["value"] = i
        self.label.config(text=text[:70])
        self.update()
        return not self.cancelled

    def close(self):
        try:
            self.grab_release()
            self.destroy()
        except tk.TclError:
            pass


class Preview(tk.Toplevel):
    """Read-only text/hex preview of one archive entry."""

    def __init__(self, parent, name, data):
        super().__init__(parent)
        self.title(L("Просмотр: %s") % name)
        self.geometry("820x560")
        self.data = data
        self.mode = "text"
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=8, pady=6)
        ttk.Label(bar, text="%s — %s" % (name, human_size(len(data)))).pack(side="left")
        ttk.Button(bar, text="Hex", command=self._hex).pack(side="right")
        ttk.Button(bar, text=L("Текст"), command=self._text).pack(side="right", padx=4)
        self.text = tk.Text(self, wrap="none", font=("Consolas", 10))
        ys = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        xs = ttk.Scrollbar(self, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.text.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=(0, 8))
        ys.pack(side="right", fill="y", pady=(0, 8))
        xs.pack(side="bottom", fill="x")
        self._text()

    def _text(self):
        self.mode = "text"
        blob = self.data[:262144]
        try:
            body = blob.decode("utf-8")
        except UnicodeDecodeError:
            body = blob.decode("cp1251", "replace")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", body)

    def _hex(self):
        self.mode = "hex"
        blob = self.data[:131072]
        lines = []
        for off in range(0, len(blob), 16):
            chunk = blob[off:off + 16]
            hexs = " ".join("%02x" % b for b in chunk)
            asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            lines.append("%08x  %-47s  %s" % (off, hexs, asc))
        self.text.delete("1.0", "end")
        self.text.insert("1.0", "\n".join(lines))


class App(tk.Tk):
    def __init__(self, path=None):
        super().__init__()
        self.h = PakArchive()
        self.folder = ""
        self.filter = ""
        self.sort_col = "name"
        self.sort_rev = False
        self.path_var = tk.StringVar()
        self.status_var = tk.StringVar(value=L("Готов"))
        self.search_var = tk.StringVar()
        self.title(APP)
        self.geometry("1000x640")
        self._icons = {}
        try:
            ico = res_path("icons", "tpak.ico")
            if os.path.exists(ico):
                self.iconbitmap(ico)
        except Exception:
            pass
        for key, name in (("app", "app32.png"), ("folder", "folder32.png"),
                          ("folder16", "folder16.png")):
            try:
                self._icons[key] = tk.PhotoImage(file=res_path("icons", name))
            except Exception:
                pass
        self.minsize(720, 420)
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Control-o>", lambda e: self.open_pak())
        self.bind("<Control-e>", lambda e: self.extract())
        self.bind("<Delete>", lambda e: self.delete_entries())
        self.bind("<F2>", lambda e: self.rename_entry())
        self.bind("<F5>", lambda e: self.refresh())
        self.bind("<Control-f>", lambda e: self.search_entry.focus_set())
        if path:
            self.after(100, lambda: self._open(path))

    # ------------------------------------------------------------------ UI
    def _build(self):
        menubar = tk.Menu(self, tearoff=0)
        m = tk.Menu(menubar, tearoff=0)
        m.add_command(label=L("Открыть…"), accelerator="Ctrl+O", command=self.open_pak)
        m.add_command(label=L("Создать пустой…"), command=self.new_pak)
        m.add_command(label=L("Создать из папки…"), command=self.create_from_dir)
        m.add_separator()
        m.add_command(label=L("Сохранить"), command=self.save)
        m.add_command(label=L("Сохранить как…"), command=self.save_as)
        m.add_separator()
        m.add_command(label=L("Свойства"), command=self.properties)
        m.add_separator()
        m.add_command(label=L("Выход"), command=self._on_close)
        menubar.add_cascade(label=L("Файл"), menu=m)

        m = tk.Menu(menubar, tearoff=0)
        m.add_command(label=L("Выделить всё"), command=self.select_all)
        m.add_command(label=L("Снять выделение"), command=self.select_none)
        m.add_command(label=L("Найти…"), accelerator="Ctrl+F",
                      command=lambda: self.search_entry.focus_set())
        menubar.add_cascade(label=L("Правка"), menu=m)

        m = tk.Menu(menubar, tearoff=0)
        m.add_command(label=L("Извлечь в папку…"), accelerator="Ctrl+E", command=self.extract)
        m.add_command(label=L("Извлечь сюда"), command=self.extract_here)
        m.add_separator()
        m.add_command(label=L("Добавить файлы…"), command=self.add_files)
        m.add_command(label=L("Добавить папку…"), command=self.add_folder)
        m.add_separator()
        m.add_command(label=L("Удалить"), accelerator="Del", command=self.delete_entries)
        m.add_command(label=L("Переименовать"), accelerator="F2", command=self.rename_entry)
        m.add_separator()
        m.add_command(label=L("Тест целостности"), command=self.test_pak)
        m.add_command(label=L("Проверка пересборкой"), command=self.verify_pak)
        menubar.add_cascade(label=L("Команды"), menu=m)

        m = tk.Menu(menubar, tearoff=0)
        m.add_command(label=L("Обновить"), accelerator="F5", command=self.refresh)
        m.add_command(label=L("В корень"), command=self.go_root)
        m.add_command(label=L("На уровень вверх"), command=self.go_up)
        m.add_separator()
        m.add_command(label=L("О программе"), command=self.about)
        menubar.add_cascade(label=L("Вид"), menu=m)
        self.config(menu=menubar)

        top = ttk.Frame(self, padding=(6, 0))
        top.pack(fill="x")
        ttk.Label(top, text=L("Архив:")).pack(side="left")
        ttk.Entry(top, textvariable=self.path_var, state="readonly").pack(
            side="left", fill="x", expand=True, padx=6)
        ttk.Label(top, text=L("Поиск:")).pack(side="left")
        self.search_entry = ttk.Entry(top, textvariable=self.search_var, width=24)
        self.search_entry.pack(side="left", padx=(4, 0))
        self.search_var.trace_add("write", lambda *_: self.refresh())

        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=6, pady=6)

        left = ttk.Frame(paned)
        self.tree = ttk.Treeview(left, show="tree", selectmode="browse")
        ys = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ys.set)
        self.tree.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")
        self.tree.bind("<ButtonRelease-1>", self._tree_clicked)
        paned.add(left, weight=1)

        right = ttk.Frame(paned)
        cols = ("name", "size", "packed", "ratio", "method", "parts")
        self.list = ttk.Treeview(right, columns=cols, show="headings", selectmode="extended")
        self.list.heading("name", text=L("Имя"))
        for c, t in (("size", L("Размер")), ("packed", L("Упаковано")),
                     ("ratio", L("Сжатие")), ("method", L("Метод")), ("parts", L("Частей"))):
            self.list.heading(c, text=t)
            self.list.column(c, width=85, anchor="e" if c not in ("method",) else "w")
        self.list.heading("name", text=L("Имя"), command=lambda: self.sort_by("name"))
        self.list.column("name", width=300, anchor="w")
        ys2 = ttk.Scrollbar(right, orient="vertical", command=self.list.yview)
        self.list.configure(yscrollcommand=ys2.set)
        self.list.pack(side="left", fill="both", expand=True)
        ys2.pack(side="right", fill="y")
        self.list.bind("<Double-1>", self._activated)
        self.list.bind("<<TreeviewSelect>>", lambda e: self._update_status())
        self.list.bind("<Button-3>", self._context)
        paned.add(right, weight=4)

        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label=L("Извлечь…"), command=self.extract)
        self.menu.add_command(label=L("Добавить файлы…"), command=self.add_files)
        self.menu.add_command(label=L("Добавить папку…"), command=self.add_folder)
        self.menu.add_separator()
        self.menu.add_command(label=L("Удалить"), command=self.delete_entries)
        self.menu.add_command(label=L("Переименовать"), command=self.rename_entry)

        ttk.Label(self, textvariable=self.status_var, relief="sunken",
                  anchor="w", padding=(6, 3)).pack(fill="x", side="bottom")
        self._try_dnd()

    def _try_dnd(self):
        try:
            from tkinterdnd2 import DND_FILES  # type: ignore
        except Exception:
            return
        try:
            self.list.drop_target_register(DND_FILES)
            self.list.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            pass

    def _on_drop(self, event):
        paths = self.tk.splitlist(event.data)
        for p in paths:
            if os.path.isdir(p):
                self.h.add_dir(p, self.folder)
            else:
                self.h.add_file(p, self._arc(self.folder, os.path.basename(p)))
        self.refresh()
        self._say(L("добавлено файлов: %d") % len(paths))

    # --------------------------------------------------------------- helpers
    def _say(self, text):
        self.status_var.set(text)

    def _arc(self, folder, name):
        return (folder.rstrip("\\") + "\\" + name).lstrip("\\") if folder else name

    def _selected(self):
        return list(self.list.selection())

    def _entry_name(self, iid):
        return getattr(self, "_names", {}).get(iid)

    # ----------------------------------------------------------------- data
    def refresh(self, rebuild_tree=True):
        """Rebuild the file list; the folder tree only when asked (no flicker)."""
        self.folder = (self.folder or "").strip("\\")
        self.path_var.set(self.h.path or L("(новый архив)"))
        if not self.h.opened:
            self.list.delete(*self.list.get_children())
            self.tree.delete(*self.tree.get_children())
            self._update_status()
            return
        entries = self.h.entries()
        if rebuild_tree:
            self.tree.delete(*self.tree.get_children())
            self._fill_tree()
        self._fill_list(entries)
        self._update_status(None, entries)

    def _fill_tree(self):
        root_id = self.tree.insert("", "end", text=L("<архив>"), open=True)
        self.tree.insert(root_id, "end", text="..", tags=("up",))
        for name in sorted(self.h.folders()):
            parent, acc = root_id, ""
            for part in name.split("\\"):
                acc = (acc + "\\" + part).lstrip("\\")
                found = None
                for child in self.tree.get_children(parent):
                    if self.tree.item(child, "text") == part:
                        found = child
                        break
                if found is None:
                    found = self.tree.insert(parent, "end", text=part,
                                             image=self._icons.get("folder16"),
                                             tags=("dir:%s" % acc,))
                parent = found
        for name in sorted(self.h.folders()):
            node = self._tree_node(name)
            if node:
                self.tree.item(node, open=True)

    def _tree_node(self, folder):
        node = self.tree.get_children("")
        if not node:
            return None
        node = node[0]
        for part in folder.split("\\"):
            nxt = None
            for child in self.tree.get_children(node):
                if self.tree.item(child, "text") == part:
                    nxt = child
                    break
            if nxt is None:
                return None
            node = nxt
        return node

    def _fill_list(self, entries):
        self.list.delete(*self.list.get_children())
        self._names = {}
        self._dirs = {}
        needle = self.search_var.get().strip().lower()
        if not needle:
            children = set()
            for fold in self.h.folders():
                if self.folder:
                    if fold.lower().startswith(self.folder.lower() + "\\"):
                        rest = fold[len(self.folder) + 1:]
                        if rest:
                            children.add(rest.split("\\")[0])
                else:
                    children.add(fold.split("\\")[0])
            for d in sorted(children):
                full = (self.folder + "\\" + d).lstrip("\\") if self.folder else d
                iid = self.list.insert("", "end",
                                       values=(d + "\\", "<DIR>", "", "", "Folder", ""),
                                       tags=("diritem",))
                self._dirs[iid] = full
        for e in entries:
            name = e["name"] or ""
            d = name.rsplit("\\", 1)[0] if "\\" in name else ""
            if needle:
                if needle not in name.lower():
                    continue
                display = name
            elif d == self.folder:
                display = name.rsplit("\\", 1)[-1]
            else:
                continue
            iid = self.list.insert("", "end", values=(
                display, human_size(e["size"]), human_size(e["packed"]),
                ("%d%%" % (100 * e["packed"] // e["size"])) if e["size"] else "-",
                e["method"], e["parts"]))
            self._names[iid] = name
            if e["edited"]:
                self.list.item(iid, tags=("edited",))

    def _update_status(self, shown=None, entries=None):
        if not self.h.opened:
            self.status_var.set(L("Архив не открыт"))
            return
        entries = entries if entries is not None else self.h.entries()
        total = sum(e["size"] for e in entries)
        packed = sum(e["packed"] for e in entries)
        sel = len(self.list.selection()) if hasattr(self, "list") else 0
        self.status_var.set(L("Файлов: %d | размер: %s | упаковано: %s | выбрано: %d%s")
                            % (len(entries), human_size(total), human_size(packed), sel,
                               L("  (есть изменения)") if self.h.dirty else ""))

    def _tree_clicked(self, event):
        """Single click on a folder row navigates (deterministic, like archive manager)."""
        iid = self.tree.identify_row(event.y)
        if not iid:
            sel = self.tree.selection()
            iid = sel[0] if sel else None
        if not iid:
            return
        tags = self.tree.item(iid, "tags")
        if not tags:
            return
        if tags[0] == "up":
            self.go_up()
        elif tags[0].startswith("dir:"):
            self.folder = tags[0][4:]
            self.refresh(rebuild_tree=False)

    def go_root(self):
        self.folder = ""
        self.refresh(rebuild_tree=False)

    def go_up(self):
        if self.folder:
            self.folder = self.folder.rsplit("\\", 1)[0] if "\\" in self.folder else ""
            self.refresh(rebuild_tree=False)

    # ------------------------------------------------------------- commands
    def open_pak(self):
        path = filedialog.askopenfilename(title=L("Открыть .pak"), filetypes=FILETYPES)
        if path:
            self._open(path)

    def _open(self, path):
        try:
            self.h.open(path)
        except PakError as exc:
            messagebox.showerror(APP, L("Не удалось открыть пак:\n%s") % exc, parent=self)
            return
        self.title("%s — %s" % (os.path.basename(path), APP))
        self.folder = ""
        self.refresh()
        self._say(L("открыт %s") % os.path.basename(path))

    def new_pak(self):
        if self.h.dirty and not messagebox.askyesno(APP, L("Есть несохранённые изменения. Новый архив?")):
            return
        self.h.new()
        self.title(APP)
        self.refresh()

    def create_from_dir(self):
        src = filedialog.askdirectory(title=L("Папка для упаковки"))
        if not src:
            return
        out = filedialog.asksaveasfilename(title=L("Новый .pak"), defaultextension=".pak",
                                           filetypes=FILETYPES)
        if not out:
            return
        dlg = Progress(self, L("Создание пака"))
        try:
            PakArchive.create_from_dir(src, out,
                                       progress=lambda i, n, name: dlg.set(i, n, name),
                                       cancel=lambda: dlg.cancelled)
        except tpak.Cancelled:
            self._say(L("создание отменено"))
            return
        except Exception as exc:
            messagebox.showerror(APP, L("Ошибка: %s") % exc, parent=self)
            return
        finally:
            dlg.close()
        self._open(out)

    def add_files(self):
        if not self.h.opened:
            messagebox.showinfo(APP, L("Сначала откройте или создайте .pak"), parent=self)
            return
        paths = filedialog.askopenfilenames(title=L("Добавить файлы"))
        for p in paths:
            self.h.add_file(p, self._arc(self.folder, os.path.basename(p)))
        if paths:
            self.refresh()

    def add_folder(self):
        if not self.h.opened:
            messagebox.showinfo(APP, L("Сначала откройте или создайте .pak"), parent=self)
            return
        folder = filedialog.askdirectory(title=L("Добавить папку"))
        if folder:
            n = self.h.add_dir(folder, self.folder)
            self.refresh()
            self._say(L("добавлено файлов: %d") % n)

    def delete_entries(self):
        names = [self._entry_name(i) for i in self._selected()]
        if not names:
            return
        if not messagebox.askyesno(APP, L("Удалить записей: %d?") % len(names), parent=self):
            return
        self.h.delete(names)
        self.refresh()

    def rename_entry(self):
        sel = self._selected()
        if len(sel) != 1:
            messagebox.showinfo(APP, L("Выберите одну запись"), parent=self)
            return
        old = self._entry_name(sel[0])
        new = simpledialog.askstring(APP, L("Новое имя:"), initialvalue=old, parent=self)
        if not new or new == old:
            return
        self.h.rename(old, new)
        self.refresh()

    def extract(self):
        names = [self._entry_name(i) for i in self._selected()] or None
        if not self.h.opened:
            return
        dest = filedialog.askdirectory(title=L("Куда извлечь"))
        if not dest:
            return
        self._extract_to(names, dest)

    def extract_here(self):
        if not self.h.opened:
            return
        base = os.path.dirname(self.h.path) if self.h.path else os.getcwd()
        self._extract_to([self._entry_name(i) for i in self._selected()] or None, base)

    def _extract_to(self, names, dest):
        dlg = Progress(self, L("Извлечение"))
        try:
            n = self.h.extract(names, dest,
                               progress=lambda i, t, name: dlg.set(i, t, name),
                               cancel=lambda: dlg.cancelled)
        except tpak.Cancelled:
            self._say(L("извлечение отменено"))
            return
        except Exception as exc:
            messagebox.showerror(APP, L("Ошибка извлечения:\n%s") % exc, parent=self)
            return
        finally:
            dlg.close()
        self._say(L("извлечено файлов: %d в %s") % (n, dest))

    def test_pak(self):
        if not self.h.opened:
            return
        dlg = Progress(self, L("Тест целостности"))
        bad = []
        try:
            bad = self.h.test(progress=lambda i, n, name: dlg.set(i, n, name),
                              cancel=lambda: dlg.cancelled)
        except Exception as exc:
            messagebox.showerror(APP, L("Тест не выполнен:\n%s") % exc, parent=self)
            return
        finally:
            dlg.close()
        if bad:
            messagebox.showwarning(APP, L("Проблем: %d\n\n%s") % (len(bad), "\n".join(bad[:10])),
                                   parent=self)
        else:
            messagebox.showinfo(APP, L("Тест пройден: пак читается целиком."), parent=self)

    def verify_pak(self):
        if not self.h.opened:
            return
        try:
            bad = self.h.verify_repack()
        except Exception as exc:
            messagebox.showerror(APP, L("Проверка не удалась:\n%s") % exc, parent=self)
            return
        if bad:
            messagebox.showwarning(APP, L("Расхождений: %d\n\n%s") % (len(bad), "\n".join(bad[:10])),
                                   parent=self)
        else:
            messagebox.showinfo(APP, L("Пересборка пройдена: все файлы совпадают."), parent=self)

    def save(self):
        if not self.h.opened:
            return
        if not self.h.path:
            return self.save_as()
        self._save_to(self.h.path)

    def save_as(self):
        if not self.h.opened:
            return
        out = filedialog.asksaveasfilename(title=L("Сохранить .pak"), defaultextension=".pak",
                                           filetypes=FILETYPES,
                                           initialfile=os.path.basename(self.h.path or "new.pak"))
        if out:
            self._save_to(out)

    def _save_to(self, out):
        dlg = Progress(self, L("Сохранение"))
        try:
            self.h.save(out, progress=lambda i, n, name: dlg.set(i, n, name),
                        cancel=lambda: dlg.cancelled)
        except tpak.Cancelled:
            self._say(L("сохранение отменено, файл не изменён"))
            return
        except Exception as exc:
            messagebox.showerror(APP, L("Не удалось сохранить:\n%s") % exc, parent=self)
            return
        finally:
            dlg.close()
        self._open(out)
        self._say(L("сохранено: %s") % out)

    def properties(self):
        if not self.h.opened:
            return
        e = self.h.entries()
        text = (L("Архив: %s\nФайлов: %d\nРазмер: %s\nУпаковано: %s\nВерсия формата: %s\nИзменения: %s")
                % (self.h.path or L("(новый)"), len(e),
                   human_size(sum(x["size"] for x in e)),
                   human_size(sum(x["packed"] for x in e)),
                   getattr(self.h.reader, "version", "—"),
                   L("есть") if self.h.dirty else L("нет")))
        messagebox.showinfo(APP, text, parent=self)

    # ----------------------------------------------------------- list events
    def _activated(self, _event):
        sel = self._selected()
        if not sel:
            return
        if sel[0] in getattr(self, "_dirs", {}):
            self.folder = self._dirs[sel[0]]
            self.refresh()
            return
        name = self._entry_name(sel[0])
        if name is None:
            return
        try:
            data = self.h.read(name)
        except Exception as exc:
            messagebox.showerror(APP, L("Не удалось прочитать:\n%s") % exc, parent=self)
            return
        Preview(self, name, data)

    def _context(self, event):
        iid = self.list.identify_row(event.y)
        if iid:
            self.list.selection_set(iid)
            self.list.focus(iid)
            self.menu.tk_popup(event.x_root, event.y_root)

    def select_all(self):
        self.list.selection_set(self.list.get_children())

    def select_none(self):
        self.list.selection_remove(self.list.selection())

    def sort_by(self, col):
        self.sort_rev = not self.sort_rev if self.sort_col == col else False
        self.sort_col = col
        rows = [(self.list.set(i, col), i) for i in self.list.get_children()]
        try:
            rows.sort(key=lambda t: float(t[0].split()[0]), reverse=self.sort_rev)
        except Exception:
            rows.sort(key=lambda t: t[0].lower(), reverse=self.sort_rev)
        for idx, (_v, i) in enumerate(rows):
            self.list.move(i, "", idx)

    def _on_close(self):
        if self.h.dirty:
            if not messagebox.askyesno(APP, L("Есть несохранённые изменения. Выйти?"), parent=self):
                return
        try:
            self.h.close()
        except Exception:
            pass
        self.destroy()

    def about(self):
        messagebox.showinfo(APP, L("%s %s\n\nУпаковщик и распаковщик .pak (TPAK v7).\nДвойной клик — просмотр файла, Del — удалить, F2 — переименовать.")
                            % (APP, VERSION), parent=self)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    app = App(argv[0] if argv else None)
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
