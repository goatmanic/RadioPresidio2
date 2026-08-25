#!/usr/bin/env python3
from pathlib import Path
import struct


def read_u32(path: str) -> int:
    data = Path(path).read_bytes()
    if len(data) != 4:
        raise SystemExit(f"Unexpected size for {path}: {len(data)}")
    return struct.unpack("<I", data)[0]

magic = read_u32("X0551026-ramlog-magic.bin")
version = read_u32("X0551026-ramlog-version.bin")
if magic != 0x4E46574C:
    raise SystemExit(
        f"RAM-log magic mismatch: got 0x{magic:08x}, expected 0x4e46574c (NFWL). "
        "The connected probe is not running this diagnostic build, or the symbol file does not match it."
    )
if version != 3:
    raise SystemExit(f"RAM-log version mismatch: got {version}, expected 3")

ring = Path("X0551026-ramlog-ring.bin").read_bytes()
# ABI v3 deliberately derives the ring size from the exact ELF/symbol-driven dump
# instead of baking one size into the chronology logic. Guard the expected v6 size
# so a stale or corrupt symbol file still fails loudly.
if len(ring) != 16384:
    raise SystemExit(f"Unexpected RAM ring size: {len(ring)}; expected 16384 for NFWL v3")

total = read_u32("X0551026-ramlog-total.bin")
write = read_u32("X0551026-ramlog-write.bin") % len(ring)
omitted = read_u32("X0551026-ramlog-nfw-omitted.bin")

if total < len(ring):
    chronological = ring[:total]
else:
    chronological = ring[write:] + ring[:write]

Path("X0551026-ramlog-chronological.bin").write_bytes(chronological)

print("RAM-log identity = NFWL v3")
print(f"RAM-log ring size = {len(ring)} bytes")
print(f"nfwRamLogTotal = {total}")
print(f"nfwRamLogWrite = {write}")
print(f"bulk $NFW frames omitted from ring = {omitted}")
print(f"human-log bytes recovered = {len(chronological)}")
print("--- chronological XDATA human-readable RAM log ---")

out = []
for b in chronological:
    if b in (9, 10, 13) or 32 <= b <= 126:
        out.append(chr(b))
    else:
        out.append(f"\\x{b:02x}")
text = "".join(out)
print(text)
Path("X0551026-ramlog.txt").write_text(text, encoding="utf-8")
