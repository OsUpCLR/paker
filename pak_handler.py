# -*- coding: utf-8 -*-
"""High-level .pak archive handler (TPAK v7).

Thin layer on top of the low-level ``tpak`` module: open a pak, list entries,
extract, add files/folders, delete, rename, test and repack. No GUI code here,
so the handler can be used by the GUI, the CLI and other tools.
"""
import hashlib
import os

import tpak


class PakError(Exception):
    """Any user-facing archive error."""


def human_size(n):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return "%d B" % n


class PakArchive:
    """Editable view of a .pak file.

    Changes are kept in memory (added/renamed data, deleted names) and are
    written by :meth:`save`. The source file is never modified in place until
    save (and a .bak copy is made by default).
    """

    def __init__(self):
        self.reader = None
        self.path = None
        self.edits = {}          # name -> bytes or callable() -> bytes
        self.deleted = set()     # lower-cased names
        self.order = []

    # ------------------------------------------------------------------ state
    @property
    def opened(self):
        return self.reader is not None or bool(self.edits)

    @property
    def dirty(self):
        return bool(self.edits or self.deleted)

    def close(self):
        if self.reader is not None:
            self.reader.close()
        self.reader = None
        self.path = None
        self.edits = {}
        self.deleted = set()
        self.order = []

    def open(self, path):
        self.close()
        try:
            self.reader = tpak.PakReader(path)
        except Exception as exc:
            raise PakError(str(exc))
        self.path = os.path.abspath(path)
        self.order = list(self.reader.order)
        return self

    def new(self):
        self.close()

    # ---------------------------------------------------------------- listing
    def entries(self):
        """List of dicts: name, size, packed, method, parts, edited."""
        out = []
        seen = set()
        if self.reader is not None:
            for f in self.reader.files:
                low = (f["name"] or "").lower()
                if low in self.deleted:
                    continue
                seen.add(low)
                packed = self.reader.packed_size(f)
                out.append({
                    "name": f["name"],
                    "size": f["size"],
                    "packed": packed,
                    "method": "Deflate" if packed < f["size"] else "Store",
                    "parts": f["n_chunks"],
                    "edited": False,
                })
        for name, value in self.edits.items():
            low = name.lower()
            if low in self.deleted or low in seen:
                continue
            if callable(value):
                try:
                    size = len(value())
                except Exception:
                    size = 0
            else:
                size = len(value)
            out.append({"name": name, "size": size, "packed": size,
                        "method": "New", "parts": 1, "edited": True})
        out.sort(key=lambda e: (e["name"] or "").lower())
        return out

    def folders(self):
        """Set of folder paths (backslash-separated) inside the archive."""
        res = set()
        for e in self.entries():
            parts = (e["name"] or "").replace("/", "\\").split("\\")[:-1]
            for i in range(1, len(parts) + 1):
                res.add("\\".join(parts[:i]))
        return res

    # ------------------------------------------------------------------ read
    def read(self, name):
        f = self.reader.entry(name) if self.reader is not None else None
        if f is None:
            value = self.edits.get(name)
            if value is None:
                raise PakError("no such entry: %s" % name)
            return value() if callable(value) else value
        return self.reader.read(f)

    def extract(self, names, dest, *, progress=None, cancel=None, flat=False):
        """Extract the given entry names (or all when names is None)."""
        names = [n for n in (names or [n["name"] for n in self.entries()])]
        os.makedirs(dest, exist_ok=True)
        done = 0
        stream = []
        for name in names:
            if cancel and cancel():
                raise tpak.Cancelled()
            value = self.edits.get(name)
            if value is not None:
                data = value() if callable(value) else value
                rel = os.path.basename(name) if flat else name
                try:
                    out = tpak.dest_path(dest, rel)
                except ValueError:
                    continue          # unsafe name (.. / absolute) - skip it
                os.makedirs(os.path.dirname(out) or dest, exist_ok=True)
                with open(out, "wb") as fh:
                    fh.write(data)
                done += 1
                if progress:
                    progress(done, len(names), name)
            elif self.reader is not None:
                f = self.reader.entry(name)
                if f is not None:
                    stream.append(f)
        if stream:
            self.reader.extract_entries(stream, dest, progress=None,
                                        cancel=cancel, flat=flat)
            done += len(stream)
        return done

    # --------------------------------------------------------------- changes
    def add_bytes(self, name, data):
        name = name.replace("/", "\\")
        self.deleted.discard(name.lower())
        self.edits[name] = data
        if name not in self.order:
            self.order.append(name)

    def add_file(self, path, arcname=None):
        arcname = (arcname or os.path.basename(path)).replace("/", "\\")
        self.add_bytes(arcname, (lambda p: lambda: open(p, "rb").read())(path))

    def add_dir(self, folder, prefix=""):
        folder = os.path.abspath(folder)
        added = 0
        for dp, _dn, fs in os.walk(folder):
            for f in sorted(fs):
                full = os.path.join(dp, f)
                rel = os.path.relpath(full, folder).replace(os.sep, "/")
                name = (prefix.rstrip("\\/") + "\\" + rel.replace("/", "\\")).lstrip("\\")
                self.add_file(full, name)
                added += 1
        return added

    def delete(self, names):
        for n in names:
            low = n.lower()
            self.deleted.add(low)
            for key in [k for k in self.edits if k.lower() == low]:
                del self.edits[key]

    def rename(self, old, new):
        new = new.replace("/", "\\")
        data = self.read(old)
        low = old.lower()
        self.deleted.add(low)
        for key in [k for k in self.edits if k.lower() == low]:
            del self.edits[key]
        self.add_bytes(new, data)

    # ------------------------------------------------------------------ save
    def save(self, out=None, *, backup=True, progress=None, cancel=None):
        out = os.path.abspath(out or self.path)
        if not out:
            raise PakError("no destination path")
        try:
            tpak.save_stream(self.reader, out, edits=self.edits,
                             deleted=self.deleted, order=self.order,
                             progress=progress, cancel=cancel, backup=backup)
        except tpak.Cancelled:
            raise
        except Exception as exc:
            raise PakError(str(exc))
        # reopen from the saved file, edits are now part of the archive
        self.open(out)
        return out

    @staticmethod
    def create_from_dir(src, out, progress=None, cancel=None):
        tpak.build_dir(src, out, progress=progress, cancel=cancel)
        return out

    # ---------------------------------------------------------------- verify
    def test(self, progress=None, cancel=None):
        """Read every entry chunk-by-chunk and compare sizes. Returns errors."""
        if self.reader is None:
            raise PakError("nothing is open")
        bad = []
        files = list(self.reader.files)
        for i, f in enumerate(files, 1):
            if cancel and cancel():
                break
            try:
                size = sum(len(c) for c in self.reader.iter_chunks(f, cancel=cancel))
                if size != f["size"]:
                    bad.append("%s: %d != %d" % (f["name"], size, f["size"]))
            except tpak.Cancelled:
                break
            except Exception as exc:
                bad.append("%s: %s" % (f["name"], exc))
            if progress:
                progress(i, len(files), f["name"] or "")
        return bad

    def verify_repack(self):
        """Rebuild in memory and compare sha256 of every entry."""
        if self.reader is None:
            raise PakError("nothing is open")
        import io
        import tempfile
        bad = []
        fd, tmp = tempfile.mkstemp(suffix=".pak")
        os.close(fd)
        try:
            tpak.save_stream(self.reader, tmp, order=self.order, backup=False)
            r2 = tpak.PakReader(tmp)
            try:
                for f in self.reader.files:
                    a = hashlib.sha256(self.reader.read(f)).digest()
                    f2 = r2.entry(f["name"])
                    if f2 is None:
                        bad.append("%s: missing" % f["name"])
                        continue
                    b = hashlib.sha256(r2.read(f2)).digest()
                    if a != b:
                        bad.append("%s: sha differs" % f["name"])
            finally:
                r2.close()
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        return bad
