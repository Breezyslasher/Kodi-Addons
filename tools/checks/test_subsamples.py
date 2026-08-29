"""Rewriting whole-sample encryption as explicit subsamples.

Built against a synthetic fragment shaped like a real one: the auxiliary IVs
at the head of the mdat, saio pointing at them from the moof, and trun's data
offset measured from the start of the moof.
"""
import os, struct, sys
SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SP + "/stubs"); sys.argv = ["x", "1", ""]
sys.path.insert(0, os.path.join(SP, "..", "..", "plugin.video.youtubetv"))
from lib import mp4

def box(kind, body):
    return struct.pack(">I", len(body) + 8) + kind + body

SAMPLES = [100, 120, 140]
IV_SIZE = 8
IVS = [bytes([i + 1]) * IV_SIZE for i in range(len(SAMPLES))]

def build():
    trun_body = (b"\x00" + (0x000201).to_bytes(3, "big")
                 + len(SAMPLES).to_bytes(4, "big")
                 + b"\x00\x00\x00\x00"
                 + b"".join(s.to_bytes(4, "big") for s in SAMPLES))
    saiz_body = (b"\x00" + (0).to_bytes(3, "big") + bytes([IV_SIZE])
                 + len(SAMPLES).to_bytes(4, "big"))
    saio_body = (b"\x00" + (0).to_bytes(3, "big") + (1).to_bytes(4, "big")
                 + b"\x00\x00\x00\x00")
    tfhd = box(b"tfhd", b"\x00\x00\x00\x00" + (1).to_bytes(4, "big"))
    tfdt = box(b"tfdt", b"\x00\x00\x00\x00" + (0).to_bytes(4, "big"))

    def assemble(data_offset, aux_offset):
        t = trun_body[:8] + struct.pack(">I", data_offset) + trun_body[12:]
        o = saio_body[:8] + struct.pack(">I", aux_offset)
        traf = box(b"traf", tfhd + tfdt + box(b"trun", t)
                   + box(b"saiz", saiz_body) + box(b"saio", o))
        return box(b"moof", box(b"mfhd", b"\x00\x00\x00\x00"
                                + (1).to_bytes(4, "big")) + traf)

    moof = assemble(0, 0)
    aux_offset = len(moof) + 8
    data_offset = aux_offset + len(SAMPLES) * IV_SIZE
    moof = assemble(data_offset, aux_offset)
    mdat = b"".join(IVS) + b"".join(bytes([0xAA]) * n for n in SAMPLES)
    return moof + box(b"mdat", mdat)

frag = build()
fails = 0
def check(label, got, want):
    global fails
    ok = got == want
    if not ok: fails += 1
    print("  %-52s -> %-24s %s" % (label, str(got)[:24],
                                   "" if ok else "EXPECTED %s" % (want,)))

moof, _ = mp4.find_box(frag, [b"moof"])
saiz, _ = mp4.find_box(frag, [b"moof", b"traf", b"saiz"])
check("synthetic fragment has whole-sample aux", mp4._saiz_sizes(frag, saiz)[0], IV_SIZE)
trun, _ = mp4.find_box(frag, [b"moof", b"traf", b"trun"])
check("trun carries the sample sizes", mp4._trun_sample_sizes(frag, trun), SAMPLES)
before_samples = frag[moof + mp4._trun_data_offset(frag, trun)[1]:]

out, why = mp4.explicit_subsamples(frag)
check("it gives no reason to decline", why, "")
check("it was rewritten", out != frag, True)
check("saiz is gone", mp4.find_box(out, [b"moof", b"traf", b"saiz"])[0], None)
check("saio is gone", mp4.find_box(out, [b"moof", b"traf", b"saio"])[0], None)

senc, senc_size = mp4.find_box(out, [b"moof", b"traf", b"senc"])
check("senc exists", senc is not None, True)
check("senc says subsample data follows",
      int.from_bytes(out[senc + 9:senc + 12], "big"), 2)
check("senc counts every sample",
      int.from_bytes(out[senc + 12:senc + 16], "big"), len(SAMPLES))

at = senc + 16
for i, size in enumerate(SAMPLES):
    iv = out[at:at + IV_SIZE]; at += IV_SIZE
    subs = int.from_bytes(out[at:at + 2], "big"); at += 2
    clear = int.from_bytes(out[at:at + 2], "big"); at += 2
    cipher = int.from_bytes(out[at:at + 4], "big"); at += 4
    check("sample %d: iv preserved" % i, iv, IVS[i])
    check("sample %d: 1 subsample, 0 clear, all cipher" % i,
          (subs, clear, cipher), (1, 0, size))
check("senc is exactly consumed", at, senc + senc_size)

new_moof, _ = mp4.find_box(out, [b"moof"])
new_trun, _ = mp4.find_box(out, [b"moof", b"traf", b"trun"])
check("the samples trun points at are byte-identical",
      out[new_moof + mp4._trun_data_offset(out, new_trun)[1]:], before_samples)

def walks(data):
    pos = 0
    while pos + 8 <= len(data):
        size = int.from_bytes(data[pos:pos + 4], "big")
        if size < 8 or pos + size > len(data):
            return False
        pos += size
    return pos == len(data)
check("top-level boxes walk cleanly", walks(out), True)
tr, tr_size = mp4.find_box(out, [b"moof", b"traf"])
check("traf's children walk cleanly", walks(out[tr + 8:tr + tr_size]), True)
again, why2 = mp4.explicit_subsamples(out)
check("running it twice changes nothing", again, out)
check("and says why", why2, "it already carries a senc")


# Live AC-3: every sample the same length, said once in tfhd, and trun
# carrying no per-sample sizes at all. This is what "0 sample sizes for 156
# encrypted samples" was, and the whole reason live audio was noise on ISA 21.
def build_tfhd_default():
    SIZE = 768
    n = 4
    ivs = [bytes([i + 9]) * IV_SIZE for i in range(n)]
    # tfhd: track_ID + default-sample-duration + default-sample-size
    tfhd = box(b"tfhd", b"\x00" + (0x000018).to_bytes(3, "big")
               + (1).to_bytes(4, "big")
               + (1024).to_bytes(4, "big")          # default_sample_duration
               + SIZE.to_bytes(4, "big"))           # default_sample_size
    tfdt = box(b"tfdt", b"\x00\x00\x00\x00" + (0).to_bytes(4, "big"))
    # trun: data-offset only. No sample sizes at all.
    trun_body = (b"\x00" + (0x000001).to_bytes(3, "big")
                 + n.to_bytes(4, "big") + b"\x00\x00\x00\x00")
    saiz_body = (b"\x00" + (0).to_bytes(3, "big") + bytes([IV_SIZE])
                 + n.to_bytes(4, "big"))
    saio_body = (b"\x00" + (0).to_bytes(3, "big") + (1).to_bytes(4, "big")
                 + b"\x00\x00\x00\x00")

    def assemble(data_offset, aux_offset):
        t = trun_body[:8] + struct.pack(">I", data_offset)
        o = saio_body[:8] + struct.pack(">I", aux_offset)
        traf = box(b"traf", tfhd + tfdt + box(b"trun", t)
                   + box(b"saiz", saiz_body) + box(b"saio", o))
        return box(b"moof", box(b"mfhd", b"\x00\x00\x00\x00"
                                + (1).to_bytes(4, "big")) + traf)

    moof = assemble(0, 0)
    aux_offset = len(moof) + 8
    moof = assemble(aux_offset + n * IV_SIZE, aux_offset)
    mdat = b"".join(ivs) + b"".join(bytes([0xBB]) * SIZE for _ in range(n))
    return moof + box(b"mdat", mdat), n, SIZE, ivs

live, n, SIZE, ivs = build_tfhd_default()
tfhd, _ = mp4.find_box(live, [b"moof", b"traf", b"tfhd"])
check("tfhd names a default sample size", mp4._tfhd_default_sample_size(live, tfhd), SIZE)
trun2, _ = mp4.find_box(live, [b"moof", b"traf", b"trun"])
check("trun names none", mp4._trun_sample_sizes(live, trun2), [])
out2, why3 = mp4.explicit_subsamples(live)
check("it is rewritten anyway", why3, "")
senc2, senc2_size = mp4.find_box(out2, [b"moof", b"traf", b"senc"])
check("senc counts every sample",
      int.from_bytes(out2[senc2 + 12:senc2 + 16], "big"), n)
at2 = senc2 + 16
for i in range(n):
    iv = out2[at2:at2 + IV_SIZE]; at2 += IV_SIZE
    subs = int.from_bytes(out2[at2:at2 + 2], "big"); at2 += 2
    clear = int.from_bytes(out2[at2:at2 + 2], "big"); at2 += 2
    cipher = int.from_bytes(out2[at2:at2 + 4], "big"); at2 += 4
    check("live sample %d uses the tfhd default" % i,
          (iv, subs, clear, cipher), (ivs[i], 1, 0, SIZE))
check("live senc is exactly consumed", at2, senc2 + senc2_size)
m2, _ = mp4.find_box(out2, [b"moof"])
t2, _ = mp4.find_box(out2, [b"moof", b"traf", b"trun"])
check("live samples are byte-identical",
      out2[m2 + mp4._trun_data_offset(out2, t2)[1]:],
      live[mp4.find_box(live, [b"moof"])[0] + mp4._trun_data_offset(live, trun2)[1]:])
check("live boxes walk cleanly", walks(out2), True)

print("failures:", fails)
sys.exit(1 if fails else 0)
