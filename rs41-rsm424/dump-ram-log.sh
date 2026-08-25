#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
SYM="$HERE/RAMLOG-SYMBOLS.txt"
[[ -r "$SYM" ]] || { echo "ERROR: missing $SYM" >&2; exit 1; }
command -v openocd >/dev/null || { echo 'ERROR: openocd not found' >&2; exit 1; }

sym_addr() { awk -v n="$1" '$NF==n {print "0x"$1; exit}' "$SYM"; }
sym_size() { awk -v n="$1" '$NF==n {print "0x"$2; exit}' "$SYM"; }

RING_ADDR="$(sym_addr nfwRamLog)"
RING_SIZE="$(sym_size nfwRamLog)"
TOTAL_ADDR="$(sym_addr nfwRamLogTotal)"
WRITE_ADDR="$(sym_addr nfwRamLogWrite)"
OMIT_ADDR="$(sym_addr nfwRamLogNfwFramesOmitted)"
MAGIC_ADDR="$(sym_addr nfwRamLogMagic)"
VERSION_ADDR="$(sym_addr nfwRamLogVersion)"

for v in RING_ADDR RING_SIZE TOTAL_ADDR WRITE_ADDR OMIT_ADDR MAGIC_ADDR VERSION_ADDR; do
  [[ "${!v}" =~ ^0x[0-9a-fA-F]+$ ]] || { echo "ERROR: could not resolve $v from RAMLOG-SYMBOLS.txt" >&2; exit 1; }
done

printf 'RAM log ring: %s size %s\n' "$RING_ADDR" "$RING_SIZE"
printf 'Metadata: magic=%s version=%s total=%s write=%s omitted-NFW=%s\n' \
  "$MAGIC_ADDR" "$VERSION_ADDR" "$TOTAL_ADDR" "$WRITE_ADDR" "$OMIT_ADDR"

openocd \
  -f interface/stlink.cfg \
  -c "transport select hla_swd" \
  -f target/stm32l4x.cfg \
  -c "adapter speed 50; init; halt 3000; dump_image X0551026-ramlog-magic.bin $MAGIC_ADDR 4; dump_image X0551026-ramlog-version.bin $VERSION_ADDR 4; dump_image X0551026-ramlog-ring.bin $RING_ADDR $RING_SIZE; dump_image X0551026-ramlog-total.bin $TOTAL_ADDR 4; dump_image X0551026-ramlog-write.bin $WRITE_ADDR 4; dump_image X0551026-ramlog-nfw-omitted.bin $OMIT_ADDR 4; dump_image X0551026-sram-full.bin 0x20000000 0xA000; resume; shutdown"

python3 "$HERE/decode_ram_log.py"
