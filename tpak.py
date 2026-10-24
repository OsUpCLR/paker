#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scpak — самостоятельный распаковщик/упаковщик `.pak` (TPAK v7) Star Conflict.

Без внешних зависимостей (только стандартная библиотека) и без опоры на
`E:\\result\\pak.py`: весь формат реализован здесь заново по проверенной записи.

РАСКЛАДКА (проверено на 588 архивах, 106 879 записей):

    +0x00  u32 'TPAK' | version=7 | 0 | count | 0xFFFFFFE3
           u32 ulen, u32 clen; clen байт — deflate(имена), первые 4 байта XOR count
           выравнивание до 4
           count × u32 — индекс (порядок), обычно 0..count-1
           u32 csize; csize байт — deflate(таблица файлов), XOR (count+csize)
           выравнивание до 4
           u32 csize; u32 n_entries; csize байт — deflate(таблица чанков),
                                              XOR (count+csize+n_entries)
           выравнивание до 4  → начало данных чанков
    запись файла  (16 Б): size, field1, n_chunks, first_chunk
                          field1 = смещение payload'а имени в таблице имён
                          first_chunk = индекс первого чанка (проверено 588/588)
    запись чанка  (16 Б): coff (смещение внутри файла), ulen, doff, dlen
                          данные лежат по pdata+doff БЕЗ префикса;
                          если dlen == ulen — хранится без сжатия
    таблица имён: [u32 len][len байт XOR][0x00], ключ key_byte(j, len, rec)

API:
    p = read(path)                 # dict: version, count, files, chunks, names
    p = read(path, inflate=False)  # только таблицы (быстро, без данных)
    extract(path, dest)            # распаковать, вернуть число файлов
    build(src, out)                # упаковать каталог
    verify(path)                   # unpack -> pack -> unpack, сравнение sha256

CLI:
    python scpak.py info    <pak>
    python scpak.py list    <pak> [--limit N]
    python scpak.py unpack  <pak> <папка>
    python scpak.py pack    <папка> <pak>
    python scpak.py verify  <pak> [--work <папка>]
"""
import argparse
import hashlib
import json
import os
import shutil
import struct
import sys
import tempfile
import zlib

MAGIC = 0x4B415054          # 'TPAK'
VERSION = 7
SENTINEL = 0xFFFFFFE3
HDR = 20


# ------------------------------------------------------------ примитивы

def key_byte(pos, length, rec):
    """Ключевой байт шифра имён (формула из FUN_1400332a0)."""
    return ((pos & 0xFF) * 2 + (length & 0xFF) * 3 +
            ((length // 5) & 0xFF) * (-5) + rec) & 0xFF


def _xor4(data, key):
    b = bytearray(data)
    kb = struct.pack("<I", key & 0xFFFFFFFF)
    for i in range(min(4, len(b))):
        b[i] ^= kb[i]
    return bytes(b)


DEFLATE_LEVEL = 9           # уровень, которым сжимает игра (проверено на чанках)


def _deflate(data):
    c = zlib.compressobj(DEFLATE_LEVEL, zlib.DEFLATED, -15)
    return c.compress(bytes(data)) + c.flush()


def _inflate(data, want=None):
    d = zlib.decompressobj(-15)
    out = d.decompress(bytes(data), (want or (1 << 30)) + 1)
    if not d.eof:
        raise ValueError("deflate-поток не завершён")
    return out


def _align4(buf):
    while len(buf) % 4:
        buf += b"\x00"
    return buf


def _u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def _need(data, off, n, what):
    """Проверить, что в файле есть n байт по смещению off (иначе — понятная ошибка)."""
    if off < 0 or n < 0 or off + n > len(data):
        raise ValueError("пак обрезан: ожидались данные «%s» (%d байт по смещению %d, "
                         "а файл всего %d байт)" % (what, n, off, len(data)))


def index_of(files):
    """Таблица `index`: индексы файлов, отсортированные по имени.

    Именно так лежит во всех проверенных паках игры (`argsort` имён, а не 0..N-1).
    """
    return sorted(range(len(files)), key=lambda i: files[i][0])


def name_to_bytes(name):
    """Имя файла -> байты пака.

    ASCII и кириллица кодируются cp1251 (как в игре), Latin-1 — как Latin-1.
    Раньше здесь был жёсткий `encode("latin1")`, из-за чего русские имена
    роняли сохранение с UnicodeEncodeError.
    """
    try:
        return name.encode("cp1251")
    except UnicodeEncodeError:
        pass
    try:
        return name.encode("latin-1")
    except UnicodeEncodeError:
        raise ValueError(
            "имя %r нельзя записать в пак: поддерживаются ASCII, кириллица (cp1251) "
            "и Latin-1" % (name,))


def name_from_bytes(raw):
    """Байты имени -> строка. Байты >= 0x80 читаются как cp1251 (русские имена)."""
    if all(b < 0x80 for b in raw):
        return raw.decode("ascii")
    try:
        return raw.decode("cp1251")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def safe_parts(rel):
    """Разбить путь из пака на безопасные части (защита от записи вне папки).

    Отклоняет `..`, UNC-пути, буквы диска и абсолютные компоненты: иначе запись
    вида `..\\..\\evil.txt` вылезает за пределы каталога распаковки.
    """
    norm = (rel or "").replace("/", "\\")
    if norm.startswith("\\\\"):
        raise ValueError("имя %r: UNC-путь недопустим" % (rel,))
    parts = []
    for part in norm.split("\\"):
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError("имя %r выходит за папку распаковки" % (rel,))
        if part.endswith(":") or ("\\" not in part and os.path.isabs(part)):
            raise ValueError("имя %r содержит букву диска или абсолютный путь" % (rel,))
        parts.append(part)
    if not parts:
        raise ValueError("пустое имя записи")
    return parts


def dest_path(dest, rel):
    """Путь распаковки записи `rel` внутри `dest` (с проверкой безопасности)."""
    return os.path.join(dest, *safe_parts(rel))


# ------------------------------------------------------------ чтение

def read_tables(path):
    """Только заголовок и таблицы: данные чанков не читаются и не распаковываются.

    Файл читается позиционно, поэтому открытие 240-мегабайтного пака занимает
    миллисекунды и не поднимает память под всё содержимое.
    """
    size = os.path.getsize(path)
    if size < 24:
        raise ValueError("пак обрезан: файл всего %d байт, заголовок TPAK — не меньше 24"
                         % size)
    with open(path, "rb") as fh:
        head = fh.read(HDR)
        if _u32(head, 0) != MAGIC:
            raise ValueError("не TPAK: %r" % head[:4])
        version, _zero, count, sentinel = struct.unpack_from("<4I", head, 4)
        if version != VERSION:
            raise ValueError("неизвестная версия пака: %d (поддерживается %d)"
                             % (version, VERSION))
        if sentinel != SENTINEL:
            raise ValueError("неожиданный маркер 0x%08X" % sentinel)

        def _take(n, what):
            chunk = fh.read(n)
            if len(chunk) != n:
                raise ValueError("пак обрезан: ожидались данные «%s» (%d байт)" % (what, n))
            return chunk

        ulen, clen = struct.unpack("<II", _take(8, "длины таблицы имён"))
        names_raw = _inflate(_xor4(_take(clen, "таблица имён"), count), ulen)
        pos = (HDR + 8 + clen + 3) & ~3

        fh.seek(pos)
        index = list(struct.unpack("<%dI" % count, _take(count * 4, "индекс файлов")))
        pos += count * 4

        csize = struct.unpack("<I", _take(4, "размер таблицы файлов"))[0]
        fcomp = _take(csize, "таблица файлов")
        ftab = _inflate(_xor4(fcomp, count + csize), count * 16)
        pos = (pos + 4 + csize + 3) & ~3

        fh.seek(pos)
        csize, n_entries = struct.unpack("<II", _take(8, "заголовок таблицы чанков"))
        ccomp = _take(csize, "таблица чанков")
        ctab = _inflate(_xor4(ccomp, count + csize + n_entries), n_entries * 16)
        pdata = (pos + 8 + csize + 3) & ~3

    names = names_by_offset(names_raw)
    files = []
    for i in range(count):
        size, field1, nch, first = struct.unpack_from("<4I", ftab, i * 16)
        files.append(dict(index=i, size=size, field1=field1,
                          n_chunks=nch, first_chunk=first,
                          name=names.get(field1, "")))
    chunks = []
    for i in range(n_entries):
        coff, ul, doff, dlen = struct.unpack_from("<4I", ctab, i * 16)
        chunks.append(dict(coff=coff, ulen=ul, doff=doff, dlen=dlen, data=None))
    return dict(path=str(path), size=os.path.getsize(path), version=version,
                count=count, index=index, names_raw=names_raw, names=names,
                files=files, chunks=chunks, n_entries=n_entries, pdata=pdata)


def read(path, inflate=True):
    """Разобрать архив. inflate=False — только заголовок/таблицы (без данных чанков)."""
    tables = read_tables(path)
    chunks = []
    fh = open(path, "rb") if inflate else None
    try:
        for ch in tables["chunks"]:
            ch = dict(ch)
            if fh is not None:
                ch["data"] = _read_chunk(fh, tables["pdata"], ch)
            chunks.append(ch)
    finally:
        if fh is not None:
            fh.close()
    tables["chunks"] = chunks
    tables["path"] = path
    return tables


def _chunk_data(data, off, ulen, dlen):
    if dlen == 0:
        return b""
    if ulen and dlen == ulen:
        return data[off:off + dlen]
    best = None
    for delta in (0, 2, 1, 3):
        seg = data[off + delta:off + delta + dlen]
        try:
            out = _inflate(seg, ulen)
        except Exception:
            continue
        if ulen == 0 or len(out) == ulen:
            return out
        if best is None:
            best = out
    if best is not None:
        return best
    if ulen == 0:
        return data[off:off + dlen]
    raise ValueError("чанк не распаковался (doff=%d, ul=%d, dl=%d)" % (off, ulen, dlen))


def _read_chunk(fh, pdata, chunk):
    """Один чанк из открытого файла (без чтения всего пака в память)."""
    if chunk["dlen"] == 0:
        return b""
    fh.seek(pdata + chunk["doff"])
    raw = fh.read(chunk["dlen"] + 3)
    if chunk["ulen"] and chunk["dlen"] == chunk["ulen"]:
        return raw[:chunk["dlen"]]
    for delta in (0, 2, 1, 3):
        seg = raw[delta:delta + chunk["dlen"]]
        if len(seg) != chunk["dlen"]:
            continue
        try:
            out = _inflate(seg, chunk["ulen"])
        except Exception:
            continue
        if chunk["ulen"] == 0 or len(out) == chunk["ulen"]:
            return out
    raise ValueError("чанк не распаковался (doff=%d)" % chunk["doff"])


def names_by_offset(names_raw):
    out, i, rec = {}, 0, 0
    while i + 4 <= len(names_raw):
        n = _u32(names_raw, i)
        if not (0 < n <= 65536 and i + 4 + n <= len(names_raw)):
            break
        buf = bytearray(names_raw[i + 4:i + 4 + n])
        for j in range(n):
            buf[j] ^= key_byte(j, n, rec)
        out[i + 4] = name_from_bytes(bytes(buf))
        i += 4 + n + 1
        rec += 1
    return out


def file_data(pak, f):
    """Данные файла по записи (склейка его чанков)."""
    ch = pak["chunks"][f["first_chunk"]:f["first_chunk"] + f["n_chunks"]]
    blob = b"".join(c["data"] if c["data"] is not None else b"" for c in ch)
    if len(blob) != f["size"]:
        raise ValueError("файл %r: %d != %d" % (f["name"], len(blob), f["size"]))
    return blob


# ------------------------------------------------------------ распаковка

def extract(path, dest, progress=None):
    """Распаковать архив в каталог. Пишет `_pak.map` для точной пересборки.

    Все пути проверяются до записи: запись не может выйти за пределы `dest`
    (защита от `..\\..\\` и абсолютных имён).
    """
    pak = read(path, inflate=True)
    os.makedirs(dest, exist_ok=True)
    plan = []
    for f in pak["files"]:
        rel = f["name"] or ("file_%04d" % f["index"])
        plan.append((rel, dest_path(dest, rel), f))
    n = 0
    fmap = []
    for rel, out, f in plan:
        os.makedirs(os.path.dirname(out) or dest, exist_ok=True)
        blob = file_data(pak, f)
        with open(out, "wb") as fh:
            fh.write(blob)
        fmap.append({"rel": rel, "size": f["size"], "field1": f["field1"],
                     "chunks": [c["ulen"] for c in
                                pak["chunks"][f["first_chunk"]:f["first_chunk"] + f["n_chunks"]]]})
        n += 1
        if progress:
            progress(n, pak["count"], rel)
    with open(os.path.join(dest, "_pak.map"), "w", encoding="utf-8") as fh:
        json.dump({"version": pak["version"], "count": pak["count"],
                   "index": pak["index"], "files": fmap}, fh, ensure_ascii=False)
    return n


# ------------------------------------------------------------ упаковка

TFD_CHUNK = 1572864   # 1.5 МиБ — предельный размер одного чанка .tfd в паках игры


def tfd_levels(tfh, tfd_size=None):
    """Уровни `.tfd`, разобранные из таблицы `.tfh`: [(off, size, pitch), ...].

    Число записей берётся из смещения таблицы (`a = 8 + n*12`), а не из поля
    `mips`: у кубических текстур в таблице `6 * mips` записей (6 граней), и
    поле `mips` описывает только одну грань.

    Признак «уровень лежит в .tfd» определяется по непрерывной цепочке от 0 до
    `tfd_size`: у кубов мелкие уровни (1024 Б) случайно попадают в диапазон
    смещений `.tfh`, и старая проверка `off >= a` их теряла.
    """
    if not tfh or len(tfh) < 20:
        return []
    mips = _u32(tfh, 4) & 0x0F
    a = struct.unpack_from("<H", tfh, 8)[0]
    n = mips
    if a >= 8 and (a - 8) % 12 == 0 and (a - 8) // 12:
        n = (a - 8) // 12
    n = min(n, max(0, (len(tfh) - 8) // 12))
    entries = []
    for i in range(n):
        off, size, pitch = struct.unpack_from("<3I", tfh, 8 + i * 12)
        if size:
            entries.append((off, size, pitch))
    entries.sort()
    if tfd_size is not None:
        out, pos = [], 0
        for off, size, pitch in entries:
            if off == pos and pos + size <= tfd_size:
                out.append((off, size, pitch))
                pos += size
        if pos == tfd_size:
            return out
    # без размера .tfd — старая эвристика: уровень вне диапазона .tfh
    return [e for e in entries if not (e[0] >= a and e[0] + e[1] <= len(tfh))]


def _split_level(size, pitch):
    """Нарезать уровень мипа так же, как игра.

    n = ceil(size / 1.5 МиБ) чанков; строки (size/pitch) делятся поровну,
    остаток уходит в последний чанк. Проверено на 10 113 `.tfd` из 588 паков:
    например 4 МиБ / pitch 8192 -> 170+170+172 строки, 64 МиБ / pitch 16384 ->
    95*42 + 106 строк, кубическая грань 16 КиБ -> один чанк.
    """
    if pitch and size % pitch == 0:
        rows = size // pitch
        n = max(1, min(rows, -(-size // TFD_CHUNK)))
        rpc = rows // n
        return [rpc * pitch] * (n - 1) + [(rows - rpc * (n - 1)) * pitch]
    n = max(1, -(-size // TFD_CHUNK))
    base = size // n
    return [base] * (n - 1) + [size - base * (n - 1)]


def _tfd_chunks(tfd, tfh):
    """Разбить `.tfd` на чанки так же, как это делает игра.

    Без этого пересобранный пак расходится с оригиналом по числу чанков
    (и, значит, по всей раскладке данных).
    """
    levels = tfd_levels(tfh, len(tfd))
    got = [s for _o, s, _p in levels]
    if got and sum(got) == len(tfd):
        out, pos = [], 0
        for _off, size, pitch in levels:
            for s in _split_level(size, pitch):
                out.append(tfd[pos:pos + s])
                pos += s
        return out
    # таблицы нет или она не сходится — хотя бы правдоподобная нарезка по 1.5 МиБ
    sizes = _split_level(len(tfd), 0)
    out, pos = [], 0
    for s in sizes:
        out.append(tfd[pos:pos + s])
        pos += s
    return out


def build(src, out, order=None, index=None, progress=None):
    """Упаковать каталог в .pak.

    `order` — порядок файлов из `_pak.map`; таблица `index` всегда
    пересчитывается как argsort имён (параметр `index` оставлен для совместимости).
    """
    root = os.path.abspath(src)
    entries = []
    for dp, _dn, fs in os.walk(root):
        for f in sorted(fs):
            if f in ("_names.map", "_pak.map"):
                continue
            full = os.path.join(dp, f)
            rel = os.path.relpath(full, root).replace("\\", "/")
            entries.append((rel, open(full, "rb").read()))

    saved = None
    mp = os.path.join(root, "_pak.map")
    if os.path.isfile(mp):
        try:
            saved = json.load(open(mp, encoding="utf-8"))
        except Exception:
            saved = None
    smap = {}
    if saved:
        for rec in saved.get("files", []):
            smap[rec.get("rel", "").replace("/", "\\").lower()] = rec
        out_order = [x.replace("/", "\\").lower() for x in
                     (order or [r.get("rel", "") for r in saved.get("files", [])])]
    else:
        out_order = None

    entries.sort(key=lambda e: e[0])
    if out_order:
        rank = {k: i for i, k in enumerate(out_order)}
        entries.sort(key=lambda e: rank.get(e[0].replace("/", "\\").lower(), len(rank)))

    blobs = {rel.replace("/", "\\").lower(): data for rel, data in entries}

    files = []
    for rel, data in entries:
        key = rel.replace("/", "\\")
        low = key.lower()
        rec = smap.get(low)
        saved_chunks = rec.get("chunks") if rec else None
        if saved_chunks and len(saved_chunks) > 1 and sum(saved_chunks) == len(data):
            chunks, pos = [], 0
            for s in saved_chunks:
                chunks.append(data[pos:pos + s])
                pos += s
        elif low.endswith(".tfd"):
            chunks = _tfd_chunks(data, blobs.get(low[:-4] + ".tfh"))
        else:
            chunks = [data]
        files.append([key, chunks])

    count = len(files)
    names_raw = bytearray()
    offsets = []
    for rec, (key, _ch) in enumerate(files):
        offsets.append(len(names_raw) + 4)
        b = name_to_bytes(key)
        n = len(b)
        buf = bytearray(b)
        for j in range(n):
            buf[j] ^= key_byte(j, n, rec)
        names_raw += struct.pack("<I", n) + bytes(buf) + b"\x00"

    ctab, cdata = bytearray(), bytearray()
    for key, chunks in files:
        inner = 0
        for d in chunks:
            comp = _deflate(d)
            if len(comp) >= len(d):
                comp, ul, dl = d, len(d), len(d)
            else:
                ul, dl = len(d), len(comp)
            ctab += struct.pack("<4I", inner, ul, len(cdata), dl)
            cdata += comp
            inner += len(d)

    ftab = bytearray()
    first = 0
    for i, (key, chunks) in enumerate(files):
        total = sum(len(d) for d in chunks)
        ftab += struct.pack("<4I", total, offsets[i], len(chunks), first)
        first += len(chunks)

    idx = index_of(files)

    ncomp = _xor4(_deflate(bytes(names_raw)), count)
    fcomp_plain = _deflate(bytes(ftab))
    fcomp = _xor4(fcomp_plain, count + len(fcomp_plain))
    ccomp = _deflate(bytes(ctab))
    n_entries = len(ctab) // 16
    ccomp = _xor4(ccomp, count + len(ccomp) + n_entries)

    blob = bytearray()
    blob += struct.pack("<5I", MAGIC, VERSION, 0, count, SENTINEL)
    blob += struct.pack("<2I", len(names_raw), len(ncomp)) + ncomp
    blob = _align4(blob)
    blob += b"".join(struct.pack("<I", v) for v in idx)
    blob += struct.pack("<I", len(fcomp)) + fcomp
    blob = _align4(blob)
    blob += struct.pack("<2I", len(ccomp), n_entries) + ccomp
    blob = _align4(blob)
    blob += bytes(cdata)

    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    with open(out, "wb") as fh:
        fh.write(blob)
    return out


# ------------------------------------------------------------ проверка

def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for c in iter(lambda: fh.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def verify(path, work=None):
    """Круг: unpack -> pack -> unpack, побайтовое сравнение; возвращает отчёт."""
    own = work is None
    tmp = work or os.path.join(os.path.dirname(os.path.abspath(__file__)), "_verify_tmp")
    if own:
        shutil.rmtree(tmp, ignore_errors=True)
    a, b = os.path.join(tmp, "A"), os.path.join(tmp, "B")
    try:
        n1 = extract(path, a)
        rep = os.path.join(tmp, "repacked.pak")
        build(a, rep)
        n2 = extract(rep, b)
        fa = {os.path.relpath(os.path.join(dp, f), a): os.path.join(dp, f)
              for dp, _d, fs in os.walk(a) for f in fs if f not in ("_pak.map",)}
        fb = {os.path.relpath(os.path.join(dp, f), b): os.path.join(dp, f)
              for dp, _d, fs in os.walk(b) for f in fs if f not in ("_pak.map",)}
        same = diff = 0
        bad = []
        for rel in sorted(set(fa) | set(fb)):
            if rel in fa and rel in fb and _sha(fa[rel]) == _sha(fb[rel]):
                same += 1
            else:
                diff += 1
                bad.append(rel)
        return dict(pak=path, files_in=n1, files_out=n2, same=same, diff=diff,
                    bad=bad[:5], repacked=rep, work=tmp)
    finally:
        if own:
            shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------ CLI

def main(argv):
    ap = argparse.ArgumentParser(description="TPAK .pak: распаковка/упаковка (самостоятельно)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_info = sub.add_parser("info")
    p_info.add_argument("pak")
    p_list = sub.add_parser("list")
    p_list.add_argument("pak")
    p_list.add_argument("--limit", type=int, default=20)
    p_un = sub.add_parser("unpack")
    p_un.add_argument("pak")
    p_un.add_argument("dest")
    p_pk = sub.add_parser("pack")
    p_pk.add_argument("src")
    p_pk.add_argument("pak")
    p_ver = sub.add_parser("verify")
    p_ver.add_argument("pak")
    p_ver.add_argument("--work")
    a = ap.parse_args(argv)

    if a.cmd == "info":
        p = read(a.pak, inflate=False)
        names = [f["name"] for f in p["files"]]
        print("файл: %s" % a.pak)
        print("  версия %d, файлов %d, чанков %d, P/4-выравнивание: %d"
              % (p["version"], p["count"], p["n_entries"], p["pdata"] % 4))
        print("  имена: %d непустых из %d" % (sum(1 for n in names if n), len(names)))
        return 0
    if a.cmd == "list":
        p = read(a.pak, inflate=False)
        for f in p["files"][:a.limit]:
            print("%8d  чанков %3d  %s" % (f["size"], f["n_chunks"], f["name"]))
        print("... всего %d" % p["count"])
        return 0
    if a.cmd == "unpack":
        n = extract(a.pak, a.dest)
        print("распаковано %d файлов -> %s" % (n, a.dest))
        return 0
    if a.cmd == "pack":
        out = build(a.src, a.pak)
        print("собрано -> %s" % out)
        return 0
    if a.cmd == "verify":
        r = verify(a.pak, a.work)
        print(json.dumps({k: r[k] for k in ("files_in", "files_out", "same", "diff")},
                         ensure_ascii=False))
        return 0 if r["diff"] == 0 else 2
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


# ------------------------------------------------------------ правка (GUI)

def entries_from_dir(src):
    """Содержимое каталога как список (путь_в_паке, bytes)."""
    root = os.path.abspath(src)
    items = []
    for dp, _dn, fs in os.walk(root):
        for f in sorted(fs):
            if f in ("_pak.map", "_names.map"):
                continue
            full = os.path.join(dp, f)
            rel = os.path.relpath(full, root).replace("\\", "/")
            with open(full, "rb") as fh:
                items.append((rel, fh.read()))
    items.sort(key=lambda e: e[0])
    return items


def _write_pak(files, out, index=None):
    """Записать .pak из списка [путь, [чанки]]."""
    count = len(files)
    names_raw = bytearray()
    offsets = []
    for rec, (key, _ch) in enumerate(files):
        offsets.append(len(names_raw) + 4)
        b = name_to_bytes(key)
        n = len(b)
        buf = bytearray(b)
        for j in range(n):
            buf[j] ^= key_byte(j, n, rec)
        names_raw += struct.pack("<I", n) + bytes(buf) + b"\x00"

    ctab, cdata = bytearray(), bytearray()
    for key, chunks in files:
        inner = 0
        for d in chunks:
            comp = _deflate(d)
            if len(comp) >= len(d):
                comp, ul, dl = d, len(d), len(d)
            else:
                ul, dl = len(d), len(comp)
            ctab += struct.pack("<4I", inner, ul, len(cdata), dl)
            cdata += comp
            inner += len(d)

    ftab = bytearray()
    first = 0
    for i, (key, chunks) in enumerate(files):
        total = sum(len(d) for d in chunks)
        ftab += struct.pack("<4I", total, offsets[i], len(chunks), first)
        first += len(chunks)

    idx = index_of(files)

    ncomp = _xor4(_deflate(bytes(names_raw)), count)
    fcomp_plain = _deflate(bytes(ftab))
    fcomp = _xor4(fcomp_plain, count + len(fcomp_plain))
    ccomp = _deflate(bytes(ctab))
    n_entries = len(ctab) // 16
    ccomp = _xor4(ccomp, count + len(ccomp) + n_entries)

    blob = bytearray()
    blob += struct.pack("<5I", MAGIC, VERSION, 0, count, SENTINEL)
    blob += struct.pack("<2I", len(names_raw), len(ncomp)) + ncomp
    blob = _align4(blob)
    blob += b"".join(struct.pack("<I", v) for v in idx)
    blob += struct.pack("<I", len(fcomp)) + fcomp
    blob = _align4(blob)
    blob += struct.pack("<2I", len(ccomp), n_entries) + ccomp
    blob = _align4(blob)
    blob += bytes(cdata)

    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    with open(out, "wb") as fh:
        fh.write(blob)
    return out


def build_entries(items, out, index=None, order=None):
    """Собрать .pak из списка (путь, bytes). .tfd дробятся по соседнему .tfh.

    `order` — исходный порядок файлов в паке (список имён). Если он передан,
    файлы кладутся в том же порядке, что и в оригинале: тогда неизменённый пак
    пересобирается побайтово. Новые файлы дописываются по имени.
    """
    entries = [(rel.replace("\\", "/"), data) for rel, data in items]
    if order:
        rank = {n.replace("/", "\\").lower(): i for i, n in enumerate(order)}
        entries.sort(key=lambda e: (rank.get(e[0].replace("/", "\\").lower(), len(rank)),
                                    e[0]))
    else:
        entries.sort(key=lambda e: e[0])
    blobs = {rel.replace("/", "\\").lower(): data for rel, data in entries}
    files = []
    for rel, data in entries:
        key = rel.replace("/", "\\")
        low = key.lower()
        if low.endswith(".tfd") and (low[:-4] + ".tfh") in blobs:
            chunks = _tfd_chunks(data, blobs[low[:-4] + ".tfh"])
        else:
            chunks = [data]
        files.append([key, chunks])
    return _write_pak(files, out, index=index)


def save_pak(items, out, index=None, backup=True, order=None):
    """Сохранить .pak; при перезаписи сделать .bak рядом.

    `order` — исходный порядок файлов (см. `build_entries`): с ним неизменённый
    пак пересобирается побайтово.
    """
    bak = ""
    if backup and os.path.exists(out):
        bak = out + ".bak"
        if not os.path.exists(bak):
            import shutil as _sh
            _sh.copy2(out, bak)
    build_entries(items, out, index=index, order=order)
    return out, bak

# ------------------------------------------------------------ потоковая работа

class Cancelled(Exception):
    """Операция прервана пользователем (кнопка «Отмена»)."""


def _check(cancel):
    if cancel is not None and cancel():
        raise Cancelled("операция отменена")


class PakReader:
    """Потоковый доступ к паку: таблицы читаются один раз, данные — по требованию.

    В отличие от ``read(inflate=True)`` не держит в памяти всё содержимое пака:
    240-мегабайтный архив открывается мгновенно, чанки распаковываются по одному.
    Читатель можно использовать как контекстный менеджер.
    """

    def __init__(self, path):
        self.path = str(path)
        self.tables = read_tables(self.path)
        self.files = self.tables["files"]
        self.chunks = self.tables["chunks"]
        self.pdata = self.tables["pdata"]
        self.names = self.tables["names"]
        self.count = self.tables["count"]
        self.n_entries = self.tables["n_entries"]
        self.size = self.tables["size"]
        self.version = self.tables["version"]
        self._fh = None
        self._by_name = None

    # ---------------------------------------------------------------- служебное
    @property
    def order(self):
        return [f["name"] for f in self.files]

    def _file(self):
        if self._fh is None:
            self._fh = open(self.path, "rb")
        return self._fh

    def close(self):
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def entry(self, name):
        if self._by_name is None:
            self._by_name = {(f["name"] or "").replace("/", "\\").lower(): f
                             for f in self.files}
        return self._by_name.get((name or "").replace("/", "\\").lower())

    def chunks_of(self, f):
        return self.chunks[f["first_chunk"]:f["first_chunk"] + f["n_chunks"]]

    def packed_size(self, f):
        return sum(c["dlen"] for c in self.chunks_of(f))

    # ------------------------------------------------------------------ данные
    def iter_chunks(self, f, cancel=None):
        fh = self._file()
        for ch in self.chunks_of(f):
            _check(cancel)
            yield _read_chunk(fh, self.pdata, ch)

    def read(self, name, *, cancel=None, progress=None):
        f = name if isinstance(name, dict) else self.entry(name)
        if f is None:
            raise KeyError(name)
        parts, done = [], 0
        total = max(1, f["n_chunks"])
        for i, data in enumerate(self.iter_chunks(f, cancel), 1):
            parts.append(data)
            done += len(data)
            if progress:
                progress(i, total, done, f["size"])
        blob = b"".join(parts)
        if len(blob) != f["size"]:
            raise ValueError("файл %r: %d != %d" % (f["name"], len(blob), f["size"]))
        return blob

    def extract_entries(self, entries, dest, *, progress=None, cancel=None,
                        flat=False):
        """Распаковать записи потоково (файл целиком в память не берётся)."""
        os.makedirs(dest, exist_ok=True)
        done = 0
        for i, f in enumerate(entries, 1):
            _check(cancel)
            rel = f["name"] or ("file_%04d" % f["index"])
            out = dest_path(dest, os.path.basename(rel) if flat else rel)
            os.makedirs(os.path.dirname(out) or dest, exist_ok=True)
            with open(out, "wb") as fh:
                for data in self.iter_chunks(f, cancel):
                    fh.write(data)
            done += 1
            if progress:
                progress(i, len(entries), rel)
        return done


class _PakWriter:
    """Пошаговая запись пака.

    Сжатые чанки сразу уходят во временный файл рядом с результатом, в памяти
    остаются только таблицы (имена, файлы, чанки). Это и даёт потоковое
    сохранение: пак в 240 МБ пересобирается, не занимая столько же ОЗУ.
    """

    def __init__(self, out):
        self.out = os.path.abspath(out)
        os.makedirs(os.path.dirname(self.out) or ".", exist_ok=True)
        fd, self.tmp = tempfile.mkstemp(prefix=".tpak-", suffix=".tmp",
                                        dir=os.path.dirname(self.out) or ".")
        self._data = os.fdopen(fd, "wb")
        self.names = []
        self.names_raw = bytearray()
        self.offsets = []
        self.ftab = bytearray()
        self.ctab = bytearray()
        self.first = 0
        self.data_off = 0
        self.count = 0

    def add(self, key, chunks, cancel=None):
        rec = self.count
        self.offsets.append(len(self.names_raw) + 4)
        b = name_to_bytes(key)
        buf = bytearray(b)
        for j in range(len(b)):
            buf[j] ^= key_byte(j, len(b), rec)
        self.names_raw += struct.pack("<I", len(b)) + bytes(buf) + b"\x00"
        self.names.append(key)
        inner = total = nch = 0
        for chunk in chunks:
            _check(cancel)
            comp = _deflate(chunk)
            if len(comp) >= len(chunk):
                comp, ul, dl = chunk, len(chunk), len(chunk)
            else:
                ul, dl = len(chunk), len(comp)
            self.ctab += struct.pack("<4I", inner, ul, self.data_off, dl)
            self._data.write(comp)
            self.data_off += len(comp)
            inner += len(chunk)
            total += len(chunk)
            nch += 1
        self.ftab += struct.pack("<4I", total, self.offsets[rec], nch, self.first)
        self.first += nch
        self.count += 1

    def finish(self):
        self._data.close()
        try:
            count = self.count
            idx = sorted(range(count), key=lambda i: self.names[i])
            ncomp = _xor4(_deflate(bytes(self.names_raw)), count)
            fcomp_plain = _deflate(bytes(self.ftab))
            fcomp = _xor4(fcomp_plain, count + len(fcomp_plain))
            ccomp = _deflate(bytes(self.ctab))
            n_entries = len(self.ctab) // 16
            ccomp = _xor4(ccomp, count + len(ccomp) + n_entries)

            head = bytearray()
            head += struct.pack("<5I", MAGIC, VERSION, 0, count, SENTINEL)
            head += struct.pack("<2I", len(self.names_raw), len(ncomp)) + ncomp
            head = _align4(head)
            head += b"".join(struct.pack("<I", v) for v in idx)
            head += struct.pack("<I", len(fcomp)) + fcomp
            head = _align4(head)
            head += struct.pack("<2I", len(ccomp), n_entries) + ccomp
            head = _align4(head)

            with open(self.out, "wb") as fh:
                fh.write(head)
                with open(self.tmp, "rb") as src:
                    shutil.copyfileobj(src, fh, 1 << 20)
        finally:
            try:
                os.remove(self.tmp)
            except OSError:
                pass
        return self.out

    def abort(self):
        try:
            self._data.close()
        except Exception:
            pass
        try:
            os.remove(self.tmp)
        except OSError:
            pass


def save_stream(reader, out, *, edits=None, deleted=None, order=None,
                progress=None, cancel=None, backup=True):
    """Пересобрать пак, не загружая его в память.

    * ``reader`` — открытый :class:`PakReader` (None — пак создаётся с нуля);
    * ``edits`` — {имя: bytes} или {имя: функция, возвращающая bytes};
    * ``deleted`` — имена, которые надо убрать;
    * ``order`` — исходный порядок файлов (даёт побайтовую пересборку);
    * ``progress(done, total, name)`` — отчёт о ходе;
    * ``cancel()`` — если вернёт True, поднимется :class:`Cancelled`,
      а недописанный файл будет удалён.
    """
    edit_map = {}
    for key, value in (edits or {}).items():
        edit_map[(key or "").replace("/", "\\").lower()] = value
    del_set = {(n or "").replace("/", "\\").lower() for n in (deleted or ())}

    names, seen = [], set()
    base_order = order if order is not None else (reader.order if reader else [])
    for raw in base_order:
        key = raw.replace("/", "\\")
        low = key.lower()
        if low in del_set or low in seen:
            continue
        if low not in edit_map and (reader is None or reader.entry(key) is None):
            continue
        seen.add(low)
        names.append(key)
    for raw in (edits or ()):
        key = raw.replace("/", "\\")
        low = key.lower()
        if low in del_set or low in seen:
            continue
        seen.add(low)
        names.append(key)

    bak = ""
    if backup and os.path.exists(out):
        bak = out + ".bak"
        if not os.path.exists(bak):
            shutil.copy2(out, bak)

    writer = _PakWriter(out)
    try:
        for i, key in enumerate(names):
            _check(cancel)
            low = key.lower()
            if low in edit_map:
                value = edit_map[low]
                data = value() if callable(value) else value
                tfh = None
                if low.endswith(".tfd"):
                    tfh_value = edit_map.get(low[:-4] + ".tfh")
                    if tfh_value is not None:
                        tfh = tfh_value() if callable(tfh_value) else tfh_value
                chunks = _tfd_chunks(data, tfh) if tfh is not None else [data]
            else:
                f = reader.entry(key) if reader is not None else None
                if f is None:
                    continue
                chunks = reader.iter_chunks(f, cancel)
            writer.add(key, chunks, cancel)
            if progress:
                progress(i + 1, len(names), key)
        writer.finish()
    except BaseException:
        writer.abort()
        raise
    return out, bak


def build_dir(src, out, *, progress=None, cancel=None):
    """Собрать пак из каталога, читая файлы по одному (без полной загрузки)."""
    root = os.path.abspath(src)
    entries = []
    for dp, _dn, fs in os.walk(root):
        for f in sorted(fs):
            if f in ("_pak.map", "_names.map"):
                continue
            full = os.path.join(dp, f)
            entries.append((os.path.relpath(full, root).replace("\\", "/"), full))
    entries.sort(key=lambda e: e[0])

    writer = _PakWriter(out)
    try:
        for i, (rel, full) in enumerate(entries):
            _check(cancel)
            with open(full, "rb") as fh:
                data = fh.read()
            key = rel.replace("/", "\\")
            if key.lower().endswith(".tfd"):
                tfh_path = os.path.join(os.path.dirname(full),
                                        os.path.basename(full)[:-4] + ".tfh")
                tfh = None
                if os.path.isfile(tfh_path):
                    with open(tfh_path, "rb") as fh:
                        tfh = fh.read()
                chunks = _tfd_chunks(data, tfh) if tfh is not None else [data]
            else:
                chunks = [data]
            writer.add(key, chunks, cancel)
            if progress:
                progress(i + 1, len(entries), rel)
        writer.finish()
    except BaseException:
        writer.abort()
        raise
    return out
