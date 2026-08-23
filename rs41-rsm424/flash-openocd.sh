#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HERE/X0551026-X0852387-RSM424-AK5Z-433900-HorusV3.bin"
SUMS="$HERE/SHA256SUMS"

command -v openocd >/dev/null || {
    printf 'ERROR: openocd is not installed or not in PATH.\n' >&2
    exit 1
}

[[ -r "$BIN" ]] || {
    printf 'ERROR: firmware not found: %s\n' "$BIN" >&2
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
    printf 'ERROR: firmware checksum mismatch.\nExpected: %s\nActual:   %s\n' \
        "$EXPECTED" "$ACTUAL" >&2
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
      -c 'adapter speed 50; init; halt 3000; flash probe 0; echo {=== WRITE PROTECTION ===}; echo [capture "stm32l4x wrp_info 0"]; shutdown' \
      2>&1
}

printf 'Checksum OK: %s\n' "$ACTUAL"
printf 'Checking STM32L412 protection state...\n'

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
    printf '\nRDP level 1 detected. Programming RDP=0xAA, clearing WRP, and reloading option bytes.\n'
    printf 'The option-byte reload intentionally resets and mass-erases the MCU; a final SWD error during this step can be normal.\n'

    set +e
    "${OPENOCD[@]}" \
      -c 'adapter speed 50; init; halt 3000; flash probe 0; stm32l4x unlock 0; flash protect 0 0 last off; stm32l4x option_load 0; shutdown'
    UNLOCK_RC=$?
    set -e

    printf 'Unlock/reload OpenOCD exit status: %d (the reset can make this nonzero).\n' "$UNLOCK_RC"
    sleep 3

    INFO="$(probe_target)" || true
    printf '%s\n' "$INFO"

    if ! grep -q 'RDP level 0' <<<"$INFO"; then
        cat >&2 <<'MSG'
ERROR: the MCU still does not report RDP level 0 after OBL_LAUNCH.
Remove ALL target power (batteries and the ST-Link 3.3-V wire), wait 10 seconds,
restore power, and run this same script again. Do not program until RDP level 0
has been confirmed.
MSG
        exit 2
    fi
fi

if ! grep -q 'RDP level 0' <<<"$INFO"; then
    printf 'ERROR: could not confirm RDP level 0; refusing to program.\n' >&2
    exit 1
fi

if grep -q '^protected areas:' <<<"$INFO"; then
    printf '\nWrite protection is still active. Clearing it and reloading option bytes...\n'
    set +e
    "${OPENOCD[@]}" \
      -c 'adapter speed 50; init; halt 3000; flash probe 0; flash protect 0 0 last off; stm32l4x option_load 0; shutdown'
    WRP_RC=$?
    set -e
    printf 'WRP clear/reload OpenOCD exit status: %d.\n' "$WRP_RC"
    sleep 3
    INFO="$(probe_target)" || true
    printf '%s\n' "$INFO"
    if grep -q '^protected areas:' <<<"$INFO" || ! grep -q 'RDP level 0' <<<"$INFO"; then
        printf 'ERROR: protection did not clear; refusing to program.\n' >&2
        exit 2
    fi
fi

printf '\nProtection checks passed. Programming X0551026 / boom X0852387...\n'
"${OPENOCD[@]}" \
  -c "adapter speed 50; program {$BIN} verify reset exit 0x08000000"
