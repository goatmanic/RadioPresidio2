#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SUMS="$HERE/SHA256SUMS"

command -v openocd >/dev/null || {
    printf 'ERROR: openocd is not installed or not in PATH.\n' >&2
    exit 1
}

BIN="$(find "$HERE" -maxdepth 1 -type f -name 'X0551026-X0852387-RSM424-AK5Z-433900-HorusV3*.bin' -print -quit)"
[[ -n "$BIN" && -r "$BIN" ]] || {
    printf 'ERROR: matching X0551026/X0852387 firmware binary not found in %s\n' "$HERE" >&2
    exit 1
}
[[ -r "$SUMS" ]] || {
    printf 'ERROR: checksum file not found: %s\n' "$SUMS" >&2
    exit 1
}

EXPECTED="$(awk -v name="$(basename -- "$BIN")" '$2 == name || $2 == "out/" name {print $1; exit}' "$SUMS")"
[[ "$EXPECTED" =~ ^[0-9a-fA-F]{64}$ ]] || {
    printf 'ERROR: no checksum for %s in %s.\n' "$(basename -- "$BIN")" "$SUMS" >&2
    exit 1
}
ACTUAL="$(sha256sum -- "$BIN" | awk '{print $1}')"
[[ "$ACTUAL" == "$EXPECTED" ]] || {
    printf 'ERROR: firmware checksum mismatch.\nExpected: %s\nActual:   %s\n' "$EXPECTED" "$ACTUAL" >&2
    exit 1
}

OPENOCD=(
    openocd
    -f interface/stlink.cfg
    -c "transport select hla_swd"
    -f target/stm32l4x.cfg
)

probe_target() {
    "${OPENOCD[@]}" \
      -c 'adapter speed 50; init; halt 3000; flash probe 0; echo {=== OPTR ===}; echo [capture "mdw 0x40022020 1"]; echo {=== WRITE PROTECTION ===}; echo [capture "stm32l4x wrp_info 0"]; shutdown' \
      2>&1
}

printf 'Firmware: %s\n' "$(basename -- "$BIN")"
printf 'Checksum OK: %s\n' "$ACTUAL"
printf 'Checking STM32L412 protection and boot state...\n'

INFO="$(probe_target)" || {
    printf '%s\n' "$INFO" >&2
    printf 'ERROR: could not identify the target. Check SWDIO, SWCLK, GND, target power and NRST.\n' >&2
    exit 1
}
printf '%s\n' "$INFO"

if grep -q 'RDP level 2' <<<"$INFO"; then
    printf 'ERROR: RDP level 2 is permanent; this MCU cannot be unlocked.\n' >&2
    exit 1
fi

if grep -q 'RDP level 1' <<<"$INFO"; then
    printf '\nRDP level 1 detected. Setting RDP=0xAA and clearing WRP.\n'
    set +e
    "${OPENOCD[@]}" \
      -c 'adapter speed 50; init; halt 3000; flash probe 0; stm32l4x unlock 0; flash protect 0 0 last off; stm32l4x option_load 0; shutdown'
    set -e
    sleep 3
    INFO="$(probe_target)" || true
    printf '%s\n' "$INFO"
fi

if ! grep -q 'RDP level 0' <<<"$INFO"; then
    printf 'ERROR: RDP level 0 could not be confirmed; refusing to program.\n' >&2
    exit 2
fi

if grep -q '^protected areas:' <<<"$INFO"; then
    printf '\nClearing remaining write protection...\n'
    set +e
    "${OPENOCD[@]}" \
      -c 'adapter speed 50; init; halt 3000; flash probe 0; flash protect 0 0 last off; stm32l4x option_load 0; shutdown'
    set -e
    sleep 3
    INFO="$(probe_target)" || true
    printf '%s\n' "$INFO"
    if grep -q '^protected areas:' <<<"$INFO"; then
        printf 'ERROR: write protection remains active; refusing to program.\n' >&2
        exit 2
    fi
fi

# RSM424 must boot application flash, not STM32 system ROM.  nSWBOOT0 is OPTR bit 26.
# Clear only that bit; preserve every other option byte (including RDP and nBOOT0/nBOOT1).
OPTR_HEX="$(sed -n 's/^0x40022020: \([0-9a-fA-F]\{8\}\).*/\1/p' <<<"$INFO" | tail -1)"
if [[ "$OPTR_HEX" =~ ^[0-9a-fA-F]{8}$ ]] && (( (16#$OPTR_HEX & 0x04000000) != 0 )); then
    printf '\nnSWBOOT0 is set; forcing software boot selection from main flash.\n'
    set +e
    "${OPENOCD[@]}" \
      -c 'adapter speed 50; init; halt 3000; flash probe 0; stm32l4x option_write 0 0x20 0x00000000 0x04000000; stm32l4x option_load 0; shutdown'
    set -e
    sleep 3
    INFO="$(probe_target)" || true
    printf '%s\n' "$INFO"
    OPTR_HEX="$(sed -n 's/^0x40022020: \([0-9a-fA-F]\{8\}\).*/\1/p' <<<"$INFO" | tail -1)"
    if [[ ! "$OPTR_HEX" =~ ^[0-9a-fA-F]{8}$ ]] || (( (16#$OPTR_HEX & 0x04000000) != 0 )); then
        printf 'ERROR: nSWBOOT0 remained set; refusing to flash until main-flash boot is configured.\n' >&2
        exit 2
    fi
fi

printf '\nProtection/boot checks passed. Programming X0551026 / boom X0852387...\n'
"${OPENOCD[@]}" \
  -c "adapter speed 50; program {$BIN} verify reset exit 0x08000000"

printf '\nVerifying application execution...\n'
RUNINFO="$("${OPENOCD[@]}" -c 'adapter speed 50; init; reset run; sleep 3000; halt 3000; echo [capture "reg pc"]; resume; shutdown' 2>&1 || true)"
printf '%s\n' "$RUNINFO"
if ! grep -Eq 'pc \(/32\): 0x080[0-1][0-9a-fA-F]{4}' <<<"$RUNINFO"; then
    printf 'WARNING: could not confirm PC in application flash after reset; inspect the PC/OPTR output above.\n' >&2
fi
