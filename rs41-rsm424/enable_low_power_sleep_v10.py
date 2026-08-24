#!/usr/bin/env python3
"""Patch RSM424 for stable-clock low-power idle between 5-minute Horus packets.

v10 deliberately does NOT switch SYSCLK away from the proven 80 MHz HSE/PLL
clock tree. v9's 2 MHz clock transition caused the logical millis() clock to run
far faster than wall time on hardware. v10 keeps HSE/PLL and the corrected
80 MHz SysTick configuration untouched, suspends SysTick only for the actual
sleep, disables HSI so the RPM411 MCO stops, and uses TIM2 from the known
80 MHz APB1 timer clock as the one-shot wake source.

The CPU sleeps with the main regulator. This saves less MCU clock power than v9
intended, but it preserves deterministic wall-clock time and still removes CPU
run current plus the continuously clocked RPM411 during the long idle interval.
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
// v10 stable-clock low-power idle
//
// Keep the proven 24 MHz HSE -> PLL -> 80 MHz clock tree untouched. TIM2 is
// clocked from APB1 at the real 80 MHz rate (APB1 divider = 1 in clock_override).
// HSI is used only as RPM411 MCO and can be stopped during the long idle.
static constexpr uint32_t NFW_LP_TIM2_CLOCK_HZ = 80000000UL;
static constexpr uint32_t NFW_LP_TIMER_HZ = 1000UL;
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

  // TIM2 is shared with the PTU code. schedulerLoop() has returned, so no PTU
  // transaction is active here. Remember IRQ state and restore it after wake.
  const bool tim2IrqWasEnabled = NVIC_GetEnableIRQ(TIM2_IRQn) != 0U;
  const uint32_t oldScr = SCB->SCR;

  RCC->APB1ENR1 |= RCC_APB1ENR1_TIM2EN;
#ifdef RCC_APB1SMENR1_TIM2SMEN
  RCC->APB1SMENR1 |= RCC_APB1SMENR1_TIM2SMEN;
#endif
  TIM2->CR1 = 0;
  TIM2->CR2 = 0;
  TIM2->SMCR = 0;
  TIM2->DIER = 0;
  TIM2->CCER = 0;
  TIM2->PSC = (NFW_LP_TIM2_CLOCK_HZ / NFW_LP_TIMER_HZ) - 1UL; // 80 MHz -> 1 kHz
  TIM2->ARR = sleepMs - 1UL;
  TIM2->CNT = 0;
  TIM2->EGR = TIM_EGR_UG;              // latch PSC immediately
  TIM2->SR = 0;                        // discard UG's update flag

  // Let a disabled TIM2 interrupt become pending and turn into a Cortex event.
  // This avoids replacing STM32duino's HardwareTimer-owned TIM2 ISR.
  NVIC_DisableIRQ(TIM2_IRQn);
  NVIC_ClearPendingIRQ(TIM2_IRQn);
  SCB->SCR |= SCB_SCR_SEVONPEND_Msk;
  TIM2->DIER = TIM_DIER_UIE;
  TIM2->CR1 = TIM_CR1_OPM | TIM_CR1_CEN;

  // HSI is not SYSCLK on RSM424; it only supplies the RPM411 MCO. Stop it while
  // SysTick is still running: HAL_RCC_OscConfig() uses HAL_GetTick() for its
  // HSI transition timeout, so the timeout source must never be frozen here.
  if (!nfwLpSetHsi(false)) {
    TIM2->CR1 &= ~TIM_CR1_CEN;
    TIM2->DIER = 0;
    TIM2->SR = 0;
    NVIC_ClearPendingIRQ(TIM2_IRQn);
    if (tim2IrqWasEnabled) NVIC_EnableIRQ(TIM2_IRQn);
    SCB->SCR = oldScr;
    return false;
  }

  // Freeze Arduino/HAL logical time only for the actual CPU sleep interval.
  HAL_SuspendTick();

  // Clear stale event state then enter ordinary SLEEP. SYSCLK remains 80 MHz,
  // but the Cortex core is stopped until TIM2 produces the wake event.
  __SEV();
  __WFE();
  __DSB();
  HAL_PWR_EnterSLEEPMode(PWR_MAINREGULATOR_ON, PWR_SLEEPENTRY_WFE);
  __ISB();

  const bool timerFired = (TIM2->SR & TIM_SR_UIF) != 0;

  TIM2->CR1 &= ~TIM_CR1_CEN;
  TIM2->DIER = 0;
  TIM2->SR = 0;
  NVIC_ClearPendingIRQ(TIM2_IRQn);
  SCB->SCR = oldScr;
  if (tim2IrqWasEnabled) NVIC_EnableIRQ(TIM2_IRQn);

  // SysTick interrupts were disabled for the TIM2 sleep interval. Advance the
  // HAL/Arduino tick by exactly the interval represented by the 80 MHz TIM2.
  if (timerFired) {
    uwTick += sleepMs;
    nfwLpSleepCount++;
    nfwLpSleptMs += sleepMs;
  }
  HAL_ResumeTick();

  // Restore RPM411 MCO with a live HAL tick so oscillator timeout handling is
  // safe even if HSI unexpectedly fails to become ready.
  if (!nfwLpSetHsi(true)) {
    // HSE/PLL/SysTick are still intact, so keep the MCU alive and let normal
    // diagnostics expose the pressure failure rather than corrupting wall time.
  }

  return timerFired;
}

uint32_t nfwLpSecondsToNextHorusSlot() {
  const uint32_t period = (uint32_t)horusV3TimeSyncSeconds;
  if (period < 5UL) return 0UL;
  const uint32_t now = dailyGpsUtcNowSeconds();
  const uint32_t phase = now % period;
  return (phase == 0UL) ? 0UL : (period - phase);
}

void nfwLowPowerIdleBetweenPackets() {
  // Initial acquisition and the once-per-day relock always remain fully awake.
  if (!dailyGpsHasLock || !dailyGpsSleeping || dailyGpsRelockActive) return;

  const uint32_t toSlot = nfwLpSecondsToNextHorusSlot();
  if (toSlot <= NFW_LP_TX_GUARD_SECONDS) return;

  uint32_t sleepMs = (toSlot - NFW_LP_TX_GUARD_SECONDS) * 1000UL;
  if (sleepMs > NFW_LP_MAX_SLEEP_MS) sleepMs = NFW_LP_MAX_SLEEP_MS;

  // Do not sleep across the deliberate 24-hour GPS wake.
  const unsigned long gpsElapsed = (unsigned long)(millis() - dailyGpsLastLockMs);
  if (gpsElapsed < DAILY_GPS_RELOCK_MS) {
    const unsigned long gpsRemain = DAILY_GPS_RELOCK_MS - gpsElapsed;
    if (gpsRemain <= 3000UL) return;
    const uint32_t gpsSafeSleep = (uint32_t)(gpsRemain - 2000UL);
    if (sleepMs > gpsSafeSleep) sleepMs = gpsSafeSleep;
  }

  if (sleepMs < 2000UL) return;

  if (!nfwLpAnnounced && xdataPortMode == 1) {
    xdataSerial.println(F("[pwr]: v10 stable-clock sleep armed: 80MHz clock retained, HSI/RPM411 paused"));
    nfwLpAnnounced = true;
  }

  if (!nfwLpSleepMs(sleepMs)) return;

  // Refresh pressure/PTU after the long idle once inside the final pre-TX guard.
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
replace_one(old, new, "final loop stable-clock low-power wrapper")

path.write_text(src, encoding="utf-8")

summary_path = Path("/out/build-summary.json")
if summary_path.exists():
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["mcu_idle_policy"] = "v10 stable 80MHz HSE/PLL sleep between 300-second Horus slots"
    summary["mcu_idle_sysclk_hz"] = 80000000
    summary["mcu_active_guard_before_tx_seconds"] = 30
    summary["mcu_idle_timer"] = "TIM2 one-shot, known 80MHz APB1 -> 1kHz, SEVONPEND/WFE"
    summary["mcu_idle_sysclk_switching"] = False
    summary["mcu_idle_hse_pll_retained"] = True
    summary["mcu_idle_hsi_off"] = True
    summary["mcu_idle_rpm411_mco_paused"] = True
    summary["mcu_idle_advances_hal_tick"] = True
    summary["mcu_idle_owns_tim2_isr"] = False
    summary["hsi_transitions_use_live_hal_tick"] = True
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

print("Applied RSM424 v10 stable-clock low-power sleep")
