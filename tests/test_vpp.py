import struct
import unittest

from deckmod.vpp import Packfile, VppError


def align(n, a):
    return (n + a - 1) // a * a


def make_vpp(files, data_align=2048, share_names=True, padded_lens=False, end_align=2048, unk08=None):
    """Independent writer for a v4 packfile, used to exercise the reader/rebuilder."""
    names, exts, name_ofs, ext_ofs = bytearray(), bytearray(), {}, {}
    entries, data = [], bytearray()
    for fname, body in files:
        name, ext = fname.rsplit(".", 1)
        if name not in name_ofs or not share_names:
            name_ofs[name] = len(names)
            names += name.encode() + b"\0"
        if ext not in ext_ofs:
            ext_ofs[ext] = len(exts)
            exts += ext.encode() + b"\0"
        data += b"\0" * (align(len(data), data_align) - len(data))
        entries.append((name_ofs[name], ext_ofs[ext], unk08(fname) if unk08 else 0, len(data), len(body), -1, 0))
        data += body
    index = b"".join(struct.pack("<IIIIIiI", *e) for e in entries)
    ns = align(0x800 + len(index), 0x800)
    es = align(ns + len(names), 0x800)
    ds = align(es + len(exts), 0x800)
    total = align(ds + len(data), end_align)
    out = bytearray(total)
    struct.pack_into("<II", out, 0, 0x51890ACE, 4)
    out[8:8 + 9] = b"patch.vpp"
    lens = [len(index), len(names), len(exts)]
    if padded_lens:
        lens = [align(v, 0x800) for v in lens]
    struct.pack_into("<10i", out, 0x154, len(entries), total, *lens, len(data), -1, 0, 0, 0)
    out[0x800:0x800 + len(index)] = index
    out[ns:ns + len(names)] = names
    out[es:es + len(exts)] = exts
    out[ds:ds + len(data)] = data
    return bytes(out)


FILES = [("always_loaded_char.g_peg_pc", b"A" * 3000), ("cmbod_human.cmesh_pc", b"B" * 17),
         ("cmbod_human.g_cmesh_pc", b"C" * 5000), ("weapons.xtbl", b"<xtbl/>")]


class VppTests(unittest.TestCase):
    def check_variant(self, **kw):
        orig = make_vpp(FILES, **kw)
        pf = Packfile(orig)
        pf.verify()
        new = pf.rebuild({"weapons.xtbl": b"<modded/>", "customization_items.xtbl": b"new file" * 100})
        again = Packfile(new)
        again.verify()
        self.assertEqual(again.read("weapons.xtbl"), b"<modded/>")
        self.assertEqual(again.read("customization_items.xtbl"), b"new file" * 100)
        self.assertEqual(again.read("cmbod_human.g_cmesh_pc"), b"C" * 5000)
        self.assertEqual(len(again.entries), 5)
        # Rebuilding the same mod set from the vanilla file is deterministic.
        self.assertEqual(Packfile(orig).rebuild({"weapons.xtbl": b"<modded/>",
                                                 "customization_items.xtbl": b"new file" * 100}), new)

    def test_variants(self):
        self.check_variant()
        self.check_variant(data_align=16, share_names=False)
        self.check_variant(data_align=1, padded_lens=True, end_align=1)

    def test_rejects_garbage_and_compressed(self):
        with self.assertRaises(VppError):
            Packfile(b"\0" * 4096)
        bad = bytearray(make_vpp(FILES))
        struct.pack_into("<i", bad, 0x800 + 20, 100)  # compressed size on first entry
        with self.assertRaises(VppError):
            Packfile(bytes(bad))

    def test_unknown_index_field_blocks_new_files_only(self):
        weird = iter([7, 1234, 99, 42])
        pf = Packfile(make_vpp(FILES, unk08=lambda f: next(weird)))
        self.assertEqual(Packfile(pf.rebuild({"weapons.xtbl": b"x"})).read("weapons.xtbl"), b"x")
        with self.assertRaises(VppError):
            pf.rebuild({"brand_new.xtbl": b"x"})


if __name__ == "__main__":
    unittest.main()
