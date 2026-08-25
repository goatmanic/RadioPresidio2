#!/usr/bin/env python3
"""Make the RSM424 LEDs dark except during actual RF transmission.

This diagnostic/flight build prioritizes low idle power. CONFIG.h's normal status
LED machinery is disabled, and the LED helper functions are made dark-safe so
startup/calibration/status calls cannot light the LEDs. The Si4032 TX gate itself
then drives the green LED for exactly the interval between radioEnableTx() and
radioDisableTx().
"""
from pathlib import Path
import re
import sys

if len(sys.argv) != 3:
    raise SystemExit(f"Usage: {sys.argv[0]} CONFIG.h rs41-nfw_sonde-firmware.ino")

cfg_path = Path(sys.argv[1])
ino_path = Path(sys.argv[2])

cfg = cfg_path.read_text(encoding="utf-8")
pattern = r"^(\s*(?:(?:constexpr|const|static)\s+)*bool\s+ledStatusEnable\s*=\s*)(true|false)(\s*;)"
cfg, n = re.subn(pattern, r"\g<1>false\g<3>", cfg, count=1, flags=re.MULTILINE)
if n != 1:
    raise SystemExit(f"Expected one ledStatusEnable setting, got {n}")
cfg_path.write_text(cfg, encoding="utf-8")
# build_config.py/inject_factory_rh_pressure_cal.py maintain a packaged audit copy
# under /out. Refresh it here after the final CONFIG mutation so CONFIG.h shipped
# in the artifact is byte-for-byte the one consumed by the compiler.
out_cfg = Path("/out/CONFIG.h")
if out_cfg.parent.is_dir():
    out_cfg.write_text(cfg, encoding="utf-8")

src = ino_path.read_text(encoding="utf-8")

old_leds = '''void redLed() {
  digitalWrite(RED_LED_PIN, LOW);
  digitalWrite(GREEN_LED_PIN, HIGH);
}

void greenLed() {
  digitalWrite(RED_LED_PIN, HIGH);
  digitalWrite(GREEN_LED_PIN, LOW);
}

void orangeLed() {
  digitalWrite(RED_LED_PIN, LOW);
  digitalWrite(GREEN_LED_PIN, LOW);
}
'''
new_leds = '''// Low-power diagnostic build: normal status/startup LED calls stay dark.
// The green LED is driven directly by radioEnableTx()/radioDisableTx() below,
// so it indicates only the actual RF transmit interval.
void redLed() {
  if (!ledStatusEnable) {
    digitalWrite(RED_LED_PIN, HIGH);
    digitalWrite(GREEN_LED_PIN, HIGH);
    return;
  }
  digitalWrite(RED_LED_PIN, LOW);
  digitalWrite(GREEN_LED_PIN, HIGH);
}

void greenLed() {
  if (!ledStatusEnable) {
    digitalWrite(RED_LED_PIN, HIGH);
    digitalWrite(GREEN_LED_PIN, HIGH);
    return;
  }
  digitalWrite(RED_LED_PIN, HIGH);
  digitalWrite(GREEN_LED_PIN, LOW);
}

void orangeLed() {
  if (!ledStatusEnable) {
    digitalWrite(RED_LED_PIN, HIGH);
    digitalWrite(GREEN_LED_PIN, HIGH);
    return;
  }
  digitalWrite(RED_LED_PIN, LOW);
  digitalWrite(GREEN_LED_PIN, LOW);
}
'''
if src.count(old_leds) != 1:
    raise SystemExit(f"Expected one LED helper block, got {src.count(old_leds)}")
src = src.replace(old_leds, new_leds, 1)

old_tx = '''void radioEnableTx() {
  // Modified to set the PLL and Crystal enable bits to high. Not sure if this makes much difference.
  writeRegister(0x07, 0x4B);
}
'''
new_tx = '''void radioEnableTx() {
  // TX-only LED policy: with normal status LEDs disabled, light green only while
  // the Si4032 is actually keyed. RSM4x4 LEDs are active-low.
  if (!ledStatusEnable) {
    digitalWrite(RED_LED_PIN, HIGH);
    digitalWrite(GREEN_LED_PIN, LOW);
  }
  // Modified to set the PLL and Crystal enable bits to high. Not sure if this makes much difference.
  writeRegister(0x07, 0x4B);
}
'''
if src.count(old_tx) != 1:
    raise SystemExit(f"Expected one radioEnableTx function, got {src.count(old_tx)}")
src = src.replace(old_tx, new_tx, 1)

old_disable = '''void radioDisableTx() {
  writeRegister(0x07, 0x40);
}
'''
new_disable = '''void radioDisableTx() {
  writeRegister(0x07, 0x40);
  if (!ledStatusEnable) {
    digitalWrite(RED_LED_PIN, HIGH);
    digitalWrite(GREEN_LED_PIN, HIGH);
  }
}
'''
if src.count(old_disable) != 1:
    raise SystemExit(f"Expected one radioDisableTx function, got {src.count(old_disable)}")
src = src.replace(old_disable, new_disable, 1)

ino_path.write_text(src, encoding="utf-8")
print("Applied low-power TX-only LED policy: dark except while radio TX is keyed")
