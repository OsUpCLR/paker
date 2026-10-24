# -*- coding: utf-8 -*-
"""Tests for the .pak handler and a GUI smoke test (no archive needed)."""
import os
import shutil
import tempfile
import unittest

import tpak
from pak_handler import PakArchive, PakError, human_size


class HandlerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="paker_")
        self.src = os.path.join(self.dir, "src")
        os.makedirs(os.path.join(self.src, "models", "ships", "x"))
        with open(os.path.join(self.src, "models", "ships", "x", "a.mdl-msh000"), "wb") as fh:
            fh.write(b"A" * 5000)
        with open(os.path.join(self.src, "readme.txt"), "wb") as fh:
            fh.write(b"hello paker")
        self.pak = os.path.join(self.dir, "test.pak")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _build(self):
        PakArchive.create_from_dir(self.src, self.pak)
        return PakArchive().open(self.pak)

    def test_create_and_list(self):
        h = self._build()
        try:
            names = sorted(e["name"] for e in h.entries())
            self.assertEqual(names, ["models\\ships\\x\\a.mdl-msh000", "readme.txt"])
            self.assertIn("models\\ships\\x", h.folders())
            self.assertFalse(h.dirty)
        finally:
            h.close()

    def test_read_and_extract(self):
        h = self._build()
        try:
            self.assertEqual(h.read("readme.txt"), b"hello paker")
            dest = os.path.join(self.dir, "out")
            n = h.extract(["readme.txt"], dest)
            self.assertEqual(n, 1)
            with open(os.path.join(dest, "readme.txt"), "rb") as fh:
                self.assertEqual(fh.read(), b"hello paker")
            self.assertEqual(h.extract(None, os.path.join(self.dir, "all")), 2)
        finally:
            h.close()

    def test_add_delete_rename_save(self):
        h = self._build()
        try:
            h.add_bytes("new\\file.bin", b"12345")
            h.rename("readme.txt", "docs\\readme.txt")
            h.delete(["models\\ships\\x\\a.mdl-msh000"])
            self.assertTrue(h.dirty)
            h.save()
            h.open(self.pak)
            names = sorted(e["name"] for e in h.entries())
            self.assertEqual(names, ["docs\\readme.txt", "new\\file.bin"])
            self.assertEqual(h.read("new\\file.bin"), b"12345")
            self.assertEqual(h.read("docs\\readme.txt"), b"hello paker")
        finally:
            h.close()

    def test_test_and_verify(self):
        h = self._build()
        try:
            self.assertEqual(h.test(), [])
            self.assertEqual(h.verify_repack(), [])
        finally:
            h.close()

    def test_dest_path_blocks_traversal(self):
        h = self._build()
        try:
            h.add_bytes("..\\..\\evil.txt", b"x")
            dest = os.path.join(self.dir, "safe")
            h.extract(["..\\..\\evil.txt"], dest)
            self.assertFalse(os.path.exists(os.path.join(self.dir, "evil.txt")))
        finally:
            h.close()

    def test_human_size(self):
        self.assertEqual(human_size(0), "0 B")
        self.assertTrue(human_size(2048).endswith("KiB"))


class GuiSmokeTests(unittest.TestCase):
    def test_app_lists_archive(self):
        import pak_gui
        d = tempfile.mkdtemp(prefix="paker_gui_")
        try:
            src = os.path.join(d, "src")
            os.makedirs(src)
            with open(os.path.join(src, "a.txt"), "wb") as fh:
                fh.write(b"abc")
            pak = os.path.join(d, "g.pak")
            PakArchive.create_from_dir(src, pak)
            app = pak_gui.App()
            app.withdraw()
            try:
                app._open(pak)
                app.update()
                self.assertEqual(len(app.list.get_children()), 1)
                self.assertIn("a.txt", app._names.values())
            finally:
                app.destroy()
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
