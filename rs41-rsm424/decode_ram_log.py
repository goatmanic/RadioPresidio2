#!/usr/bin/env python3
from pathlib import Path
import struct


def read_u32(path: str) -> int:
    data = Path(path).read_bytes()
    if len(data) != 4:
        raise SystemExit(f"Unexpected size for {path}: {len(data)}")
    return struct.unpack("<I", data)[0]

ring = Path("X0551026-ramlog-ring.bin").read_bytes()
if not ring:
    raise SystemExit("RAM ring dump is empty")

total = read_u32("X0551026-ramlog-total.bin")
write = read_u32("X0551026-ramlog-write.bin") % len(ring)
omitted = read_u32("X0551026-ramlog-nfw-omitted.bin")

if total < len(ring):
    used = min(total, len(ring))
    chronological = ring[:used]
else:
    chronological = ring[write:] + ring[:write]

Path("X0551026-ramlog-chronological.bin").write_bytes(chronological)

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
