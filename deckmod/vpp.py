"""Read and rebuild Saints Row 2 packfiles (.vpp_pc, version 4).

Layout (little-endian):
  0x000  u32 signature 0x51890ACE, u32 version 4, ... (opaque header bytes)
  0x154  s32 file count, container size, index length, names length, extensions length,
         then 5 more header values
  0x800  index: one 28-byte entry per file
           name offset, extension offset, unknown, data offset, size, compressed size (-1), unknown
         followed (each 0x800-aligned) by the names table, the extensions table and the file data.

Several values in this format have no documented meaning, so the rebuilder never
guesses: it learns every layout rule (alignment, padding, string sharing, header
values, the unknown per-entry field) from the game's own packfile, and refuses to
write anything unless rebuilding the untouched original reproduces it byte for byte.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

SIGNATURE = 0x51890ACE
FIELDS_OFS = 0x154
INDEX_OFS = 0x800
SECTOR = 0x800
ENTRY = struct.Struct("<IIIIIiI")


class VppError(Exception):
    pass


def _align(n: int, a: int) -> int:
    return (n + a - 1) // a * a if a > 1 else n


def _cstr(buf: bytes, start: int, end: int) -> str:
    stop = buf.find(b"\0", start, end)
    if stop == -1:
        raise VppError("unterminated string in packfile")
    return buf[start:stop].decode("latin-1")


@dataclass
class Entry:
    name: str
    ext: str
    name_ofs: int
    ext_ofs: int
    unk08: int
    offset: int
    size: int
    csize: int
    unk18: int

    @property
    def full(self) -> str:
        return f"{self.name}.{self.ext}" if self.ext else self.name


def _unk08_candidates(e: Entry) -> dict[str, int]:
    return {
        "crc_name_lower": zlib.crc32(e.name.lower().encode("latin-1")),
        "crc_full_lower": zlib.crc32(e.full.lower().encode("latin-1")),
        "crc_name": zlib.crc32(e.name.encode("latin-1")),
        "crc_full": zlib.crc32(e.full.encode("latin-1")),
        "size": e.size,
        "offset": e.offset,
    }


class Packfile:
    def __init__(self, data: bytes):
        self.data = data
        if len(data) < INDEX_OFS or struct.unpack_from("<I", data, 0)[0] != SIGNATURE:
            raise VppError("not a Saints Row packfile")
        if struct.unpack_from("<I", data, 4)[0] != 4:
            raise VppError("unsupported packfile version (only Saints Row 2 is supported)")
        self.fields = list(struct.unpack_from("<10i", data, FIELDS_OFS))
        n, container, len_index, len_names, len_exts = self.fields[:5]
        if container != len(data):
            raise VppError("packfile size doesn't match its header")
        if n < 0 or n * ENTRY.size > len_index:
            raise VppError("corrupt packfile index")

        self.names_start = _align(INDEX_OFS + len_index, SECTOR)
        self.exts_start = _align(self.names_start + len_names, SECTOR)
        self.data_start = _align(self.exts_start + len_exts, SECTOR)
        names_end, exts_end = self.names_start + len_names, self.exts_start + len_exts

        self.entries: list[Entry] = []
        for i in range(n):
            no, eo, u8, ofs, size, csize, u18 = ENTRY.unpack_from(data, INDEX_OFS + i * ENTRY.size)
            if csize != -1:
                raise VppError("compressed packfiles are not supported")
            if self.data_start + ofs + size > len(data):
                raise VppError("packfile entry points outside the file")
            self.entries.append(Entry(
                _cstr(data, self.names_start + no, names_end), _cstr(data, self.exts_start + eo, exts_end),
                no, eo, u8, ofs, size, csize, u18,
            ))
        self._learn_layout()

    # ------------------------------------------------------------ layout rules

    def _learn_layout(self) -> None:
        es = self.entries
        self.sorted_index = [e.full.lower() for e in es] == sorted(e.full.lower() for e in es)
        self.share_names = len({e.name_ofs for e in es}) < len(es)
        self.share_exts = len({e.ext_ofs for e in es}) < len(es)

        offsets = [e.offset for e in es if e.size]
        align = SECTOR
        while align > 1 and any(o % align for o in offsets):
            align //= 2
        self.data_align = align
        last_end = max((e.offset + e.size for e in es), default=0)
        tail = len(self.data) - (self.data_start + last_end)
        if tail == 0:
            self.end_align = 1
        elif len(self.data) % SECTOR == 0 and tail < SECTOR:
            self.end_align = SECTOR
        else:
            raise VppError("unexpected data after the last file in the packfile")

        raw = self._raw_lengths(self.entries, self._tables(self.entries)[0], self._tables(self.entries)[1])
        self.len_padded = {}
        for key, idx in (("index", 2), ("names", 3), ("exts", 4)):
            if self.fields[idx] == raw[key]:
                self.len_padded[key] = False
            elif self.fields[idx] == _align(raw[key], SECTOR):
                self.len_padded[key] = True
            else:
                raise VppError(f"can't work out the packfile's {key} table length")

        derived = self._derived_values(len(self.data), last_end, len(es))
        self.extra_rules = []
        for v in self.fields[5:]:
            rule = next((k for k, dv in derived.items() if dv == v and v not in (0, -1)), None)
            self.extra_rules.append(rule or ("const", v))

        self.unk08_rule = None
        if es:
            if len({e.unk08 for e in es}) == 1:
                self.unk08_rule = ("const", es[0].unk08)
            else:
                for key in _unk08_candidates(es[0]):
                    if all(_unk08_candidates(e)[key] == e.unk08 for e in es):
                        self.unk08_rule = key
                        break
        self.unk18_default = es[0].unk18 if es and len({e.unk18 for e in es}) == 1 else 0

    def _derived_values(self, total: int, last_end: int, count: int) -> dict:
        return {
            "data_raw": last_end, "data_padded": _align(last_end, self.end_align),
            "data_section": total - self.data_start, "total": total, "count": count,
        }

    @staticmethod
    def _tables(entries: list[Entry]):
        return [e.name for e in entries], [e.ext for e in entries]

    def _raw_lengths(self, entries, names, exts) -> dict:
        return {
            "index": len(entries) * ENTRY.size,
            "names": self._strtab_len(entries, "name_ofs", names),
            "exts": self._strtab_len(entries, "ext_ofs", exts),
        }

    @staticmethod
    def _strtab_len(entries, attr, strings) -> int:
        seen, total = set(), 0
        for e, s in sorted(zip(entries, strings), key=lambda t: getattr(t[0], attr)):
            o = getattr(e, attr)
            if o not in seen:
                seen.add(o)
                total = max(total, o + len(s.encode("latin-1")) + 1)
        return total

    # ------------------------------------------------------------ writing

    def _unk08_for(self, e: Entry, original: Entry | None) -> int:
        rule = self.unk08_rule
        if isinstance(rule, tuple):
            return rule[1]
        if rule:
            return _unk08_candidates(e)[rule]
        if original is not None:
            return original.unk08
        raise VppError(f"can't add new file '{e.full}' to this packfile (unknown index format)")

    def _build(self, replace: dict[str, tuple[str, bytes]]) -> bytes:
        """``replace`` maps lower-case 'name.ext' -> (original-case filename, contents)."""
        by_key = {e.full.lower(): e for e in self.entries}
        bodies: dict[str, bytes] = {}
        entries: list[tuple[Entry, Entry | None]] = []
        for e in self.entries:
            key = e.full.lower()
            new = Entry(**vars(e))
            if key in replace:
                bodies[key] = replace[key][1]
                new.size = len(bodies[key])
            else:
                bodies[key] = self.data[self.data_start + e.offset:self.data_start + e.offset + e.size]
            entries.append((new, e))
        for key, (fname, body) in replace.items():
            if key in by_key:
                continue
            name, _, ext = fname.rpartition(".") if "." in fname else (fname, "", "")
            new = Entry(name, ext, -1, -1, 0, 0, len(body), -1, self.unk18_default)
            bodies[key] = body
            entries.append((new, None))

        # String tables: keep original strings at their original offsets, append new ones.
        def strtab(attr, getter, share):
            buf = bytearray()
            known: dict[str, int] = {}
            for e, orig in sorted((t for t in entries if t[1] is not None), key=lambda t: getattr(t[1], attr)):
                o = getattr(orig, attr)
                if o >= len(buf):
                    buf.extend(b"\0" * (o - len(buf)))
                    buf.extend(getter(e).encode("latin-1") + b"\0")
                known.setdefault(getter(e), o)
            for e, orig in entries:
                if orig is None:
                    s = getter(e)
                    if share and s in known:
                        setattr(e, attr, known[s])
                    else:
                        setattr(e, attr, len(buf))
                        known.setdefault(s, len(buf))
                        buf.extend(s.encode("latin-1") + b"\0")
            return bytes(buf)

        names = strtab("name_ofs", lambda e: e.name, self.share_names)
        exts = strtab("ext_ofs", lambda e: e.ext, self.share_exts)

        # Data: original files keep their original order, new files go at the end.
        order = sorted((t for t in entries if t[1] is not None), key=lambda t: t[1].offset)
        order += [t for t in entries if t[1] is None]
        data = bytearray()
        for e, _orig in order:
            if e.size:
                data.extend(b"\0" * (_align(len(data), self.data_align) - len(data)))
            e.offset = len(data)
            data.extend(bodies[e.full.lower()])
        last_end = len(data)

        index_entries = [e for e, _ in entries]
        if self.sorted_index:
            index_entries.sort(key=lambda e: e.full.lower())
        originals = {id(e): o for e, o in entries}
        index = b"".join(ENTRY.pack(e.name_ofs, e.ext_ofs, self._unk08_for(e, originals[id(e)]),
                                    e.offset, e.size, -1, e.unk18) for e in index_entries)

        names_start = _align(INDEX_OFS + len(index), SECTOR)
        exts_start = _align(names_start + len(names), SECTOR)
        data_start = _align(exts_start + len(exts), SECTOR)
        if data_start != self.data_start and not replace:
            raise VppError("packfile table layout not understood")
        total = _align(data_start + last_end, self.end_align)

        out = bytearray(total)
        out[:INDEX_OFS] = self.data[:INDEX_OFS]
        out[INDEX_OFS:INDEX_OFS + len(index)] = index
        out[names_start:names_start + len(names)] = names
        out[exts_start:exts_start + len(exts)] = exts
        out[data_start:data_start + last_end] = data

        self_data_start = self.data_start
        self.data_start = data_start  # derived values are relative to the new layout
        try:
            derived = self._derived_values(total, last_end, len(index_entries))
        finally:
            self.data_start = self_data_start
        lens = [len(index), len(names), len(exts)]
        lens = [_align(v, SECTOR) if self.len_padded[k] else v for k, v in zip(("index", "names", "exts"), lens)]
        fields = [len(index_entries), total, *lens]
        for rule in self.extra_rules:
            fields.append(rule[1] if isinstance(rule, tuple) else derived[rule])
        struct.pack_into("<10i", out, FIELDS_OFS, *fields)
        return bytes(out)

    def verify(self) -> None:
        if self._build({}) != self.data:
            raise VppError("this packfile's layout isn't fully understood, so it was left untouched")

    def rebuild(self, files: dict[str, bytes]) -> bytes:
        """Return a new packfile with ``files`` (filename -> contents) added or replaced."""
        self.verify()
        replace = {name.lower(): (name, body) for name, body in files.items()}
        return self._build(replace)

    def names(self) -> list[str]:
        return [e.full for e in self.entries]

    def read(self, name: str) -> bytes:
        for e in self.entries:
            if e.full.lower() == name.lower():
                return self.data[self.data_start + e.offset:self.data_start + e.offset + e.size]
        raise KeyError(name)
