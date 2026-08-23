#!/usr/bin/env python3
"""Patch the pinned RSM424 NFW source for one-fix-per-day M10 operation.

Policy:
  * acquire a normal position/time fix after boot;
  * enter host-controlled M10 PSMOO with no autonomous periodic wake;
  * while asleep, gpsHandler performs zero GPS UART traffic;
  * after 86,400 s, send a NAV-PVT poll to wake the M10;
  * remain awake until a fresh valid position/time fix is observed;
  * return to PSMOO and repeat every 24 h from the successful relock;
  * Horus V3 UTC continues from the last GPS-set UTC using MCU millis().

This is deliberately RSM4x4/M10-only. It does not alter the RSM4x2 path.
"""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit(f"Usage: {sys.argv[0]} rs41-nfw_sonde-firmware.ino")

path = Path(sys.argv[1])
src = path.read_text(encoding="utf-8")


def replace_one(old: str, new: str, label: str) -> None:
    global src
    n = src.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected exactly one source match, got {n}")
    src = src.replace(old, new, 1)


# Runtime state. Keep the last valid coordinates/satellite count in the normal gps*
# globals while the receiver sleeps; only the wake/relock state is added here.
old = '''int8_t gpsStatus = 1;  // value sent as Horus V3 extra-sensor "gpspwr": = currentM10IntelligentMode on RSM4x4/4x5, = currentGPSPowerMode on RSM4x2/4x1
'''
new = old + '''
#ifdef RSM4x4
// Low-power fixed-site policy for this RSM424: obtain one good M10 position/time
// fix, let the receiver go inactive, then wake it only once every 24 hours for a
// fresh relock. Unsigned millis subtraction is wrap-safe because the interval is
// well below the ~49.7-day millis wrap period.
static constexpr unsigned long DAILY_GPS_RELOCK_MS = 86400000UL;
bool dailyGpsHasLock = false;
bool dailyGpsSleeping = false;
bool dailyGpsRelockActive = false;
unsigned long dailyGpsLastLockMs = 0;
unsigned long dailyGpsWakeMs = 0;
uint32_t dailyGpsLastUtcSeconds = 0;
#endif
'''
replace_one(old, new, "daily GPS runtime state")

# Extend the existing PM-mode cache and add a PSMOO profile with *no autonomous
# wake*. A UART RX edge (our NAV-PVT poll) is therefore the only normal wake source.
old = '''int8_t s_pmMode = -1;                            // cache: -1 unknown, 0 FULL, 2 PSMCT
void m10ResetPmCache() { s_pmMode = -1; }
'''
new = '''int8_t s_pmMode = -1;                            // cache: -1 unknown, 0 FULL, 1 PSMOO, 2 PSMCT
void m10ResetPmCache() { s_pmMode = -1; }
'''
replace_one(old, new, "M10 PM cache")

old = '''void m10SetContinuous() {
  if (s_pmMode == 0) return;                     // already continuous - don't re-send
  m10ValSetU1(0x20D00001UL, 0);                  // CFG-PM-OPERATEMODE = FULL
  s_pmMode = 0;
}

// Enter cyclic tracking. onTimeSec = CFG-PM-ONTIME, the time held in full tracking each cycle.
'''
new = '''void m10SetContinuous() {
  if (s_pmMode == 0) return;                     // already continuous - don't re-send
  m10ValSetU1(0x20D00001UL, 0);                  // CFG-PM-OPERATEMODE = FULL
  s_pmMode = 0;
}

// Host-controlled PSM ON/OFF for the 24-hour fixed-site policy. Both automatic
// search periods are zero, so the receiver remains inactive until traffic on its
// UART RX wakes it. DONOTENTEROFF=1 keeps it awake after that external wake until
// a real fix is obtained; ONTIME=0 lets it return inactive immediately after the
// valid fix. UPDATEEPH=0 prevents extra ephemeris-maintenance wakeups.
void m10SetDailyPsmoo() {
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

// Enter cyclic tracking. onTimeSec = CFG-PM-ONTIME, the time held in full tracking each cycle.
'''
replace_one(old, new, "M10 daily PSMOO helper")

# Keep packet UTC moving while GPS is inactive. The UTC base is refreshed at each
# successful initial/daily fix; between those fixes MCU millis supplies elapsed time.
marker = '''// Horus V3 mode - protocol and code provided by Mark VK5QI - big thanks for awesome work on code and the protocol!!!
int buildHorusV3Packet(char* uncoded_buffer){
'''
insert = '''// Horus V3 mode - protocol and code provided by Mark VK5QI - big thanks for awesome work on code and the protocol!!!
static uint32_t dailyGpsUtcNowSeconds() {
#ifdef RSM4x4
  if (dailyGpsHasLock) {
    uint32_t elapsed = (uint32_t)((millis() - dailyGpsLastLockMs) / 1000UL);
    return (dailyGpsLastUtcSeconds + elapsed) % 86400UL;
  }
#endif
  return (uint32_t)gpsHours * 3600UL + (uint32_t)gpsMinutes * 60UL + gpsSeconds;
}

int buildHorusV3Packet(char* uncoded_buffer){
'''
replace_one(marker, insert, "Horus UTC helper")

old = '''        .timeOfDaySeconds  = gpsHours*3600 + gpsMinutes*60 + gpsSeconds,
'''
new = '''        .timeOfDaySeconds  = dailyGpsUtcNowSeconds(),
'''
replace_one(old, new, "Horus V3 time-of-day")

# Crucial power-save gate: while asleep, return before GPSManagement and before
# every poll/write. At exactly 24 h, a NAV-PVT poll provides the M10 UART RX wake
# edge; after that normal readout proceeds but intelligent PM management remains
# bypassed so it cannot replace PSMOO with FULL/PSMCT.
old = '''void gpsHandler() {

  GPSManagement();

  if(rsm4x4) {
'''
new = '''void gpsHandler() {

#ifdef RSM4x4
  if (dailyGpsHasLock && dailyGpsSleeping) {
    if ((unsigned long)(millis() - dailyGpsLastLockMs) < DAILY_GPS_RELOCK_MS) {
      gpsStatus = 0;                               // deliberate GNSS inactive state
      return;                                      // absolutely no GPS UART traffic
    }

    dailyGpsSleeping = false;
    dailyGpsRelockActive = true;
    dailyGpsWakeMs = millis();
    gpsStatus = 1;
    if (xdataPortMode == 1) {
      xdataSerial.println(F("[gps]: 24-hour relock wake - M10 acquiring"));
    }

    // A byte arriving at the M10 UART RX is a PSMOO wake source. NAV-PVT is also
    // exactly the solution we need, so use it as the wake command. The next
    // gpsHandler pass performs the normal bounded read/poll loop.
    while (gpsSerial.available()) gpsSerial.read();
    sendUbxPoll(UbxGnss::CLS_NAV, UbxGnss::NAV_PVT);
    return;
  }

  // Before the very first lock, retain NFW's normal GPS management. Once the
  // daily policy is armed (including while reacquiring) it owns power state.
  if (!dailyGpsHasLock && !dailyGpsRelockActive) GPSManagement();
#else
  GPSManagement();
#endif

  if(rsm4x4) {
'''
replace_one(old, new, "gpsHandler daily sleep/wake gate")

# Arm PSMOO on the first genuinely fresh fix, and re-arm the 24-hour timer on
# each daily relock. A 2-second post-wake guard avoids accepting any retained
# solution emitted immediately on wake before the receiver has reacquired.
old = '''    gpsCommitReadings();

    if (xdataPortMode == 1) {
'''
new = '''    gpsCommitReadings();

#ifdef RSM4x4
    bool dailyFreshFix = gpsAltFresh && gps.gnssFixOK && gps.location.isValid() &&
                         gps.time.isValid() && gps.location.age() < 2000UL &&
                         gps.time.age() < 2000UL && gpsSats >= 4;
    if (dailyGpsRelockActive && (millis() - dailyGpsWakeMs) < 2000UL) {
      dailyFreshFix = false;
    }

    if (dailyFreshFix) {
      uint32_t fixUtc = (uint32_t)gpsHours * 3600UL +
                        (uint32_t)gpsMinutes * 60UL + gpsSeconds;

      if (!dailyGpsHasLock) {
        // Configure once. PSMOO retains the RAM-layer configuration across its
        // inactive periods, so subsequent daily wakes do not need reconfiguration.
        m10SetDailyPsmoo();
        dailyGpsHasLock = true;
      }

      dailyGpsLastUtcSeconds = fixUtc;
      dailyGpsLastLockMs = millis();
      dailyGpsRelockActive = false;
      dailyGpsSleeping = true;
      gpsStatus = 0;

      if (xdataPortMode == 1) {
        xdataSerial.print(F("[gps]: valid lock stored; M10 inactive for "));
        xdataSerial.print(DAILY_GPS_RELOCK_MS / 3600000UL);
        xdataSerial.println(F("h"));
      }
      return;                                       // skip stale/watchdog logic after intentional sleep
    }
#endif

    if (xdataPortMode == 1) {
'''
replace_one(old, new, "daily GPS lock capture")

path.write_text(src, encoding="utf-8")
print("Applied RSM424 daily GPS policy: initial fix, PSMOO, 24h UART wake/relock")
