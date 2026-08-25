#!/usr/bin/env python3
"""Patch RSM424 for conservative SysTick-driven CPU sleep between 5-minute Horus packets.

v9-v11 attempted to suspend SysTick and reconstruct elapsed wall time from a custom
TIM2 one-shot. Hardware testing showed that this path could manufacture logical
elapsed time. v12 removes the custom wall-clock reconstruction completely.

Policy:
  * keep the proven 24 MHz HSE -> PLL -> 80 MHz SYSCLK unchanged;
  * leave the normal 1 ms SysTick running at all times;
  * during long idle, stop HSI so the RPM411 MCO is paused;
  * repeatedly enter ordinary Cortex-M4 SLEEP with WFI;
  * SysTick wakes the core every millisecond, increments HAL/Arduino time normally,
    and the loop immediately sleeps again until the requested real millis() interval
    has actually elapsed;
  * never write uwTick and never use TIM2 as a wall clock.

This is intentionally less aggressive than a deep-sleep/LPTIM design, but timing
correctness is inherited from the same SysTick source already proven during normal
80 MHz operation. It still removes almost all CPU run duty during the long idle and
keeps the RPM411 external clock stopped between measurement windows.
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


old = '''void loop() {
  schedulerLoop();
}
'''
new = r'''#ifdef RSM4x4
// ---------------------------------------------------------------------------
// v12 conservative low-power idle
//
// Wall time is NEVER synthesized here. The normal, already-correct SysTick keeps
// running and millis() advances only from real SysTick interrupts. The Cortex-M4
// sleeps between those interrupts and immediately goes back to sleep until the
// requested interval has physically elapsed.
static constexpr uint32_t NFW_LP_TX_GUARD_SECONDS = 30UL;
static constexpr uint32_t NFW_LP_MAX_SLEEP_MS = 240000UL;
static uint32_t nfwLpSleepCount = 0;
static uint64_t nfwLpSleptMs = 0;
static bool nfwLpAnnounced = false;

bool nfwLpSetHsi(bool enable) {
  RCC_OscInitTypeDef osc = {};
  osc.OscillatorType = RCC_OSCILLATORTYPE_HSI;
  osc.HSIState = enable ? RCC_HSI_ON : RCC_HSI_OFF;
  osc.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
  osc.PLL.PLLState = RCC_PLL_NONE;
  return HAL_RCC_OscConfig(&osc) == HAL_OK;
}

bool nfwLpSleepMs(uint32_t sleepMs) {
  if (sleepMs < 2UL || sleepMs > NFW_LP_MAX_SLEEP_MS) return false;

  xdataSerial.flush();
  gpsSerial.flush();
  radioDisableTx();
  bothLedOff();

  // HSI is not SYSCLK on this RSM424. It supplies the RPM411 MCO only, so stop
  // it for the idle interval while SysTick/HSE/PLL remain completely untouched.
  if (!nfwLpSetHsi(false)) return false;

  const uint32_t oldScr = SCB->SCR;
  SCB->SCR &= ~(SCB_SCR_SLEEPDEEP_Msk | SCB_SCR_SLEEPONEXIT_Msk);

  const unsigned long startMs = millis();

  if (nfwLpSleepCount == 0UL && xdataPortMode == 1) {
    xdataSerial.print(F("[pwr]: v12 systick sleep start req="));
    xdataSerial.print(sleepMs);
    xdataSerial.print(F("ms tick="));
    xdataSerial.println(startMs);
    xdataSerial.flush();
  }

  // SysTick remains enabled. Every 1 ms interrupt advances HAL/Arduino time and
  // wakes WFI. Any unrelated interrupt may also wake us, but the elapsed-time
  // test simply sends the CPU back to sleep; no early wake can manufacture time.
  while ((unsigned long)(millis() - startMs) < sleepMs) {
    __DSB();
    __WFI();
    __ISB();
  }

  const unsigned long elapsedMs = (unsigned long)(millis() - startMs);
  SCB->SCR = oldScr;

  // Restore the RPM411 clock with the normal SysTick still live, so HAL oscillator
  // timeout handling remains safe even if HSI ever fails to restart.
  const bool hsiRestored = nfwLpSetHsi(true);

  nfwLpSleepCount++;
  nfwLpSleptMs += elapsedMs;

  if (nfwLpSleepCount == 1UL && xdataPortMode == 1) {
    xdataSerial.print(F("[pwr]: v12 systick sleep end elapsed="));
    xdataSerial.print(elapsedMs);
    xdataSerial.print(F("ms tick="));
    xdataSerial.println(millis());
  }

  return hsiRestored && elapsedMs >= sleepMs;
}

uint32_t nfwLpSecondsToNextHorusSlot() {
  const uint32_t period = (uint32_t)horusV3TimeSyncSeconds;
  if (period < 5UL) return 0UL;
  const uint32_t now = dailyGpsUtcNowSeconds();
  const uint32_t phase = now % period;
  return (phase == 0UL) ? 0UL : (period - phase);
}

void nfwLowPowerIdleBetweenPackets() {
  // Initial acquisition and the once-per-day relock remain fully awake.
  if (!dailyGpsHasLock || !dailyGpsSleeping || dailyGpsRelockActive) return;

  const uint32_t toSlot = nfwLpSecondsToNextHorusSlot();
  if (toSlot <= NFW_LP_TX_GUARD_SECONDS) return;

  uint32_t sleepMs = (toSlot - NFW_LP_TX_GUARD_SECONDS) * 1000UL;
  if (sleepMs > NFW_LP_MAX_SLEEP_MS) sleepMs = NFW_LP_MAX_SLEEP_MS;

  // Do not sleep across the deliberate 24-hour GPS relock point.
  const unsigned long gpsElapsed = (unsigned long)(millis() - dailyGpsLastLockMs);
  if (gpsElapsed < DAILY_GPS_RELOCK_MS) {
    const unsigned long gpsRemain = DAILY_GPS_RELOCK_MS - gpsElapsed;
    if (gpsRemain <= 3000UL) return;
    const uint32_t gpsSafeSleep = (uint32_t)(gpsRemain - 2000UL);
    if (sleepMs > gpsSafeSleep) sleepMs = gpsSafeSleep;
  }

  if (sleepMs < 2000UL) return;

  if (!nfwLpAnnounced && xdataPortMode == 1) {
    xdataSerial.println(F("[pwr]: v12 conservative sleep armed: SysTick wall clock, WFI CPU sleep, HSI/RPM411 paused"));
    nfwLpAnnounced = true;
  }

  if (!nfwLpSleepMs(sleepMs)) return;

  // Refresh pressure/PTU in the final pre-TX guard after the long idle.
  const uint32_t afterToSlot = nfwLpSecondsToNextHorusSlot();
  if (afterToSlot > 0UL && afterToSlot <= NFW_LP_TX_GUARD_SECONDS) {
    pressureHandler();
    sensorBoomHandler();
  }

  if (xdataPortMode == 1 && (nfwLpSleepCount == 1UL || (nfwLpSleepCount % 12UL) == 0UL)) {
    xdataSerial.print(F("[pwr]: lp-sleep count="));
    xdataSerial.print(nfwLpSleepCount);
    xdataSerial.print(F(" total="));
    xdataSerial.print((unsigned long)(nfwLpSleptMs / 1000ULL));
    xdataSerial.println(F("s"));
  }
}
#endif

void loop() {
  schedulerLoop();
#ifdef RSM4x4
  nfwLowPowerIdleBetweenPackets();
#endif
}
'''
replace_one(old, new, "final loop v12 SysTick low-power wrapper")

path.write_text(src, encoding="utf-8")

summary_path = Path("/out/build-summary.json")
if summary_path.exists():
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["mcu_idle_policy"] = "v12 SysTick-driven WFI sleep between 300-second Horus slots"
    summary["mcu_idle_sysclk_hz"] = 80000000
    summary["mcu_active_guard_before_tx_seconds"] = 30
    summary["mcu_idle_wall_clock"] = "normal 1ms SysTick / millis(), never suspended"
    summary["mcu_idle_custom_wall_timer"] = False
    summary["mcu_idle_manual_uwtick_adjustment"] = False
    summary["mcu_idle_sysclk_switching"] = False
    summary["mcu_idle_hse_pll_retained"] = True
    summary["mcu_idle_hsi_off"] = True
    summary["mcu_idle_rpm411_mco_paused"] = True
    summary["mcu_idle_wake_source"] = "SysTick plus any normal interrupt; elapsed millis() gate"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

print("Applied RSM424 v12 conservative SysTick/WFI low-power sleep")
