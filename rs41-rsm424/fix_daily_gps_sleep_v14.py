#!/usr/bin/env python3
"""Harden the RSM424 one-fix-per-day M10 policy for actual GNSS inactivity.

This patch is applied AFTER enable_daily_gps_relock.py.

Hardware/telemetry testing of v13 showed continuously changing coordinates even
though the host-side daily sleep policy was intended to be armed. Two independent
issues are fixed here:

1. RS41-NFW has scheduler-side pre-TX GPS UART drains outside gpsHandler(). Those
   drains bypassed the dailyGpsSleeping gate and could publish new NAV-PVT data.
   Every such scheduler drain is now gated by dailyGpsUartAllowed().

2. The upstream m10ValSet() helper writes UBX-CFG-VALSET to RAM only (layer 0x01).
   u-blox M10 clears RAM in the PSMOO off state; PM configuration therefore has to
   be present in BBR as well. Daily PSMOO keys are now written to RAM+BBR (0x03).

On MCU/firmware boot, the first gpsHandler() pass sends a wake edge and explicitly
stores OPERATEMODE=FULL in RAM+BBR before normal initial acquisition. This prevents
a PSMOO state retained in BBR from an earlier run from trapping a newly flashed or
reset firmware in sleep.
"""

from pathlib import Path
import json
import sys

if len(sys.argv) != 2:
    raise SystemExit(f"Usage: {sys.argv[0]} rs41-nfw_sonde-firmware.ino")

path = Path(sys.argv[1])
src = path.read_text(encoding="utf-8")


def replace_one(old: str, new: str, label: str) -> None:
    global src
    n = src.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected exactly one match, got {n}")
    src = src.replace(old, new, 1)


# Add a layer-selectable VALSET helper without changing ordinary upstream RAM-only
# configuration calls. 0x03 = RAM (bit0) + BBR (bit1).
old = '''void m10ValSetU4(uint32_t key, uint32_t v) { uint8_t b[4] = { (uint8_t)v, (uint8_t)(v >> 8), (uint8_t)(v >> 16), (uint8_t)(v >> 24) }; m10ValSet(key, b, 4); }
'''
new = old + '''
// v14 daily-GPS persistent power-management helper. The M10 clears its RAM
// configuration in the PSMOO off state, so daily PM keys are written to both
// current RAM and battery-backed RAM. Ordinary NFW configuration remains RAM-only.
void m10DailyValSet(uint32_t key, const uint8_t* val, uint8_t valLen) {
  uint8_t p[16];
  p[0] = 0x00; p[1] = 0x03; p[2] = 0x00; p[3] = 0x00;  // version, layers=RAM|BBR
  p[4] = key & 0xFF; p[5] = (key >> 8) & 0xFF; p[6] = (key >> 16) & 0xFF; p[7] = (key >> 24) & 0xFF;
  for (uint8_t i = 0; i < valLen && i < 8; i++) p[8 + i] = val[i];
  sendUbx(0x06, 0x8A, p, 8 + valLen, true);
}
void m10DailyValSetU1(uint32_t key, uint8_t v)  { m10DailyValSet(key, &v, 1); }
void m10DailyValSetU2(uint32_t key, uint16_t v) { uint8_t b[2] = { (uint8_t)(v & 0xFF), (uint8_t)(v >> 8) }; m10DailyValSet(key, b, 2); }
void m10DailyValSetU4(uint32_t key, uint32_t v) { uint8_t b[4] = { (uint8_t)v, (uint8_t)(v >> 8), (uint8_t)(v >> 16), (uint8_t)(v >> 24) }; m10DailyValSet(key, b, 4); }
'''
replace_one(old, new, "layer-selectable M10 daily VALSET helpers")

# Extra state: boot PM normalization plus a clean UART-drain predicate usable from
# scheduler code that lives outside gpsHandler().
old = '''uint32_t dailyGpsLastUtcSeconds = 0;
#endif
'''
new = '''uint32_t dailyGpsLastUtcSeconds = 0;
bool dailyGpsBootPmPrepared = false;
unsigned long dailyGpsSleepArmMs = 0;
uint16_t dailyGpsSleepUnexpectedRx = 0;
bool dailyGpsSleepReasserted = false;
#endif

bool dailyGpsUartAllowed() {
#ifdef RSM4x4
  return !(dailyGpsHasLock && dailyGpsSleeping);
#else
  return true;
#endif
}
'''
replace_one(old, new, "v14 daily GPS runtime state")

# Replace the injected PSMOO helper with RAM+BBR writes. OPERATEMODE remains last,
# as required by u-blox. Capture NAKs so the RAM log tells us if this unit rejects
# any of the persistent configuration writes.
old = '''void m10SetDailyPsmoo() {
  m10SetContinuous();
  m10ValSetU4(0x40D00002UL, 0);                  // CFG-PM-POSUPDATEPERIOD: host wake only
  m10ValSetU4(0x40D00003UL, 0);                  // CFG-PM-ACQPERIOD: no autonomous retry
  m10ValSetU2(0x30D00005UL, 0);                  // CFG-PM-ONTIME: sleep immediately after fix
  m10ValSetU1(0x10D00008UL, 1);                  // CFG-PM-DONOTENTEROFF: acquire until fixed
  m10ValSetU1(0x10D00009UL, 0);                  // CFG-PM-WAITTIMEFIX: require normal position fix
  m10ValSetU1(0x10D0000AUL, 0);                  // CFG-PM-UPDATEEPH: no extra wake cycles
  m10ValSetU1(0x20D00001UL, 1);                  // CFG-PM-OPERATEMODE = PSMOO (send last)
  s_pmMode = 1;
}
'''
new = '''void m10SetDailyPsmoo() {
  m10SetContinuous();
  const uint16_t nakBefore = gpsCfgNakCount;
  m10DailyValSetU4(0x40D00002UL, 0);             // CFG-PM-POSUPDATEPERIOD: host wake only
  m10DailyValSetU4(0x40D00003UL, 0);             // CFG-PM-ACQPERIOD: no autonomous retry
  m10DailyValSetU2(0x30D00005UL, 0);             // CFG-PM-ONTIME: brief tracking then inactive
  m10DailyValSetU1(0x10D00008UL, 1);             // CFG-PM-DONOTENTEROFF: acquire until fixed
  m10DailyValSetU1(0x10D00009UL, 0);             // CFG-PM-WAITTIMEFIX: normal position fix
  m10DailyValSetU1(0x10D0000AUL, 0);             // CFG-PM-UPDATEEPH: no ephemeris wake cycles
  m10DailyValSetU1(0x20D00001UL, 1);             // CFG-PM-OPERATEMODE = PSMOO, MUST be last
  s_pmMode = 1;
  if (xdataPortMode == 1) {
    xdataSerial.print(F("[gps]: v14 PSMOO stored RAM+BBR; new NAKs="));
    xdataSerial.println((uint16_t)(gpsCfgNakCount - nakBefore));
  }
}
'''
replace_one(old, new, "persistent daily PSMOO helper")

# On every MCU boot, undo any retained PSMOO mode before initial acquisition. A
# UART edge wakes a receiver that was left inactive in BBR by a previous firmware
# run; then FULL is written to RAM+BBR. This does not touch the other Vaisala/NFW
# GNSS configuration keys.
old = '''void gpsHandler() {

#ifdef RSM4x4
  if (dailyGpsHasLock && dailyGpsSleeping) {
'''
new = '''void gpsHandler() {

#ifdef RSM4x4
  if (!dailyGpsBootPmPrepared) {
    gpsSerial.write((uint8_t)0xFF);               // UART RX edge wakes retained PSMOO
    gpsSerial.flush();
    delay(100);
    const uint16_t nakBefore = gpsCfgNakCount;
    m10DailyValSetU1(0x20D00001UL, 0);            // CFG-PM-OPERATEMODE = FULL in RAM+BBR
    s_pmMode = 0;
    while (gpsSerial.available()) gpsSerial.read();
    gps.resetParse();
    dailyGpsBootPmPrepared = true;
    if (xdataPortMode == 1) {
      xdataSerial.print(F("[gps]: v14 boot PM normalize FULL RAM+BBR; new NAKs="));
      xdataSerial.println((uint16_t)(gpsCfgNakCount - nakBefore));
    }
  }

  if (dailyGpsHasLock && dailyGpsSleeping) {
'''
replace_one(old, new, "v14 boot PM normalization")

# After the first/daily fix has been committed, clear any trailing receiver output
# after PSMOO is armed. From this point onward any later UART data is unexpected.
old = '''      dailyGpsLastUtcSeconds = fixUtc;
      dailyGpsLastLockMs = millis();
      dailyGpsRelockActive = false;
      dailyGpsSleeping = true;
      gpsStatus = 0;

      if (xdataPortMode == 1) {
        xdataSerial.print(F("[gps]: valid lock stored; M10 inactive for "));
'''
new = '''      dailyGpsLastUtcSeconds = fixUtc;
      dailyGpsLastLockMs = millis();
      dailyGpsRelockActive = false;
      dailyGpsSleepArmMs = millis();
      dailyGpsSleepUnexpectedRx = 0;
      dailyGpsSleepReasserted = false;
      delay(100);
      while (gpsSerial.available()) gpsSerial.read();
      gps.resetParse();
      dailyGpsSleeping = true;
      gpsStatus = 0;

      if (xdataPortMode == 1) {
        xdataSerial.print(F("[gps]: valid lock stored; M10 PSMOO RAM+BBR inactive for "));
'''
replace_one(old, new, "v14 sleep arm and UART cleanup")

# Harden the sleep gate: after the intentional post-arm cleanup there should be no
# streamed GNSS output. If bytes appear after 3 s, discard them and reassert the
# persistent PSMOO profile once. This does not publish a new fix.
old = '''  if (dailyGpsHasLock && dailyGpsSleeping) {
    if ((unsigned long)(millis() - dailyGpsLastLockMs) < DAILY_GPS_RELOCK_MS) {
      gpsStatus = 0;                               // deliberate GNSS inactive state
      return;                                      // absolutely no GPS UART traffic
    }
'''
new = '''  if (dailyGpsHasLock && dailyGpsSleeping) {
    if ((unsigned long)(millis() - dailyGpsLastLockMs) < DAILY_GPS_RELOCK_MS) {
      gpsStatus = 0;                               // deliberate GNSS inactive state
      if ((unsigned long)(millis() - dailyGpsSleepArmMs) > 3000UL && gpsSerial.available()) {
        uint16_t leaked = 0;
        while (gpsSerial.available()) { gpsSerial.read(); if (leaked < 65535) leaked++; }
        gps.resetParse();
        dailyGpsSleepUnexpectedRx = (uint16_t)min((uint32_t)65535,
                                                  (uint32_t)dailyGpsSleepUnexpectedRx + leaked);
        if (!dailyGpsSleepReasserted) {
          dailyGpsSleepReasserted = true;
          if (xdataPortMode == 1) {
            xdataSerial.print(F("[gps]: WARN unexpected RX while asleep bytes="));
            xdataSerial.print(leaked);
            xdataSerial.println(F("; reasserting PSMOO RAM+BBR"));
          }
          m10SetDailyPsmoo();
          delay(100);
          while (gpsSerial.available()) gpsSerial.read();
          gps.resetParse();
          dailyGpsSleepArmMs = millis();
        }
      }
      return;                                      // no normal GPS parsing/polling while asleep
    }
'''
replace_one(old, new, "v14 asleep RX leak enforcement")

# Scheduler leak 1: the quick pre-TX drain must not bypass daily sleep.
old = '''    } else if (gpsOperationMode != 0 && gpsAge > 2000UL) {
      while (gpsSerial.available()) gps.encode((uint8_t)gpsSerial.read());
      // Publish what the drain decoded. Without this, a schedule dense enough to keep
      // txImminent true continuously (e.g. 10 s Horus slots + ~5 s transmissions + an
      // APRS collision shifting the phase) froze every gps* global indefinitely while
      // the parser itself stayed perfectly up to date.
      gpsCommitReadings();
    }
'''
new = '''    } else if (gpsOperationMode != 0 && gpsAge > 2000UL && dailyGpsUartAllowed()) {
      while (gpsSerial.available()) gps.encode((uint8_t)gpsSerial.read());
      // Publish only when the daily-GPS policy allows UART servicing. While the M10
      // is in its 24-hour inactive interval this path must not consume or publish GNSS.
      gpsCommitReadings();
    }
'''
replace_one(old, new, "scheduler quick pre-TX GPS drain gate")

# Scheduler leak 2: GPS UART drain during the sub-2.5-second precision wait.
old = '''          if (gpsOperationMode != 0) { while (gpsSerial.available()) gps.encode((uint8_t)gpsSerial.read()); }
'''
new = '''          if (gpsOperationMode != 0 && dailyGpsUartAllowed()) { while (gpsSerial.available()) gps.encode((uint8_t)gpsSerial.read()); }
'''
replace_one(old, new, "scheduler precision-wait GPS drain gate")

# Scheduler leak 3: post-TX stale-backlog discard. Reading does not transmit to the
# M10, but keeping the UART entirely untouched during the sleep interval makes the
# policy auditable and prevents accidentally consuming evidence of a sleep failure.
old = '''        if (gpsOperationMode != 0) {
          while (gpsSerial.available()) gpsSerial.read();
          gps.resetParse();
        }
'''
new = '''        if (gpsOperationMode != 0 && dailyGpsUartAllowed()) {
          while (gpsSerial.available()) gpsSerial.read();
          gps.resetParse();
        }
'''
replace_one(old, new, "scheduler post-TX GPS backlog gate")

path.write_text(src, encoding="utf-8")

summary_path = Path("/out/build-summary.json")
if summary_path.exists():
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["gps_policy_version"] = "v14 true-sleep hardened"
    summary["gps_pm_config_layers"] = "RAM+BBR"
    summary["gps_scheduler_uart_drains_blocked_while_sleeping"] = True
    summary["gps_boot_pm_normalization"] = "FULL written to RAM+BBR before initial acquisition"
    summary["gps_sleep_uart_leak_reassertion"] = True
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

print("Applied v14 M10 true-sleep hardening: RAM+BBR PSMOO + all scheduler UART drains gated")
