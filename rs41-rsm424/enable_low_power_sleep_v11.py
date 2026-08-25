#!/usr/bin/env python3
"""Patch RSM424 for fixed-width stable-clock low-power idle between 5-minute Horus packets.

v10 correctly stopped switching SYSCLK, but programmed TIM2's 16-bit PSC register
with 79999 for an intended 80 MHz -> 1 kHz timer base. Only the low 16 bits were
retained (14463), so the physical timer ran at about 5530.97 Hz. A requested
200-second sleep therefore lasted about 36.16 real seconds while the firmware
still credited 200 seconds to uwTick.

v11 keeps the proven 80 MHz HSE/PLL system clock, uses a legal 16-bit TIM2
prescaler of 39999 for a 2 kHz timer base, and represents each millisecond as
exactly two TIM2 counts. A compile-time assertion prevents this class of
prescaler-width error from recurring.
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
// v11 stable-clock low-power idle
//
// Keep the proven 24 MHz HSE -> PLL -> 80 MHz clock tree untouched. TIM2 is
// clocked from APB1 at the real 80 MHz rate (APB1 divider = 1 in clock_override).
// TIM2's PSC field is only 16 bits. v10 incorrectly attempted PSC=79999, which
// truncated to 14463 and made a nominal 1 kHz timer run at ~5.531 kHz. v11 uses
// PSC=39999 for an exact 2 kHz base and two timer counts per millisecond.
static constexpr uint32_t NFW_LP_TIM2_CLOCK_HZ = 80000000UL;
static constexpr uint32_t NFW_LP_TIMER_HZ = 2000UL;
static constexpr uint32_t NFW_LP_TICKS_PER_MS = NFW_LP_TIMER_HZ / 1000UL;
static constexpr uint32_t NFW_LP_TIM2_PSC = (NFW_LP_TIM2_CLOCK_HZ / NFW_LP_TIMER_HZ) - 1UL;
static_assert((NFW_LP_TIMER_HZ % 1000UL) == 0UL, "low-power timer must have an integer number of ticks per ms");
static_assert(NFW_LP_TIM2_PSC <= 0xFFFFUL, "TIM2 PSC exceeds the hardware 16-bit field");
static_assert(NFW_LP_TIM2_PSC == 39999UL, "unexpected TIM2 prescaler");
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

  const uint32_t timerTicks = sleepMs * NFW_LP_TICKS_PER_MS;
  TIM2->PSC = NFW_LP_TIM2_PSC;       // 39999: 80 MHz / 40000 = 2 kHz
  TIM2->ARR = timerTicks - 1UL;       // two timer counts per requested millisecond
  TIM2->CNT = 0;
  TIM2->EGR = TIM_EGR_UG;
  TIM2->SR = 0;

  // Verify what the peripheral actually retained before trusting it as a clock.
  if ((TIM2->PSC & 0xFFFFUL) != NFW_LP_TIM2_PSC || TIM2->ARR != timerTicks - 1UL) {
    return false;
  }

  NVIC_DisableIRQ(TIM2_IRQn);
  NVIC_ClearPendingIRQ(TIM2_IRQn);
  SCB->SCR |= SCB_SCR_SEVONPEND_Msk;
  TIM2->DIER = TIM_DIER_UIE;
  TIM2->CR1 = TIM_CR1_OPM | TIM_CR1_CEN;

  // HSI is not SYSCLK on RSM424; it supplies the RPM411 MCO. Stop it before
  // suspending SysTick so HAL's oscillator timeout code always has a live tick.
  if (!nfwLpSetHsi(false)) {
    TIM2->CR1 &= ~TIM_CR1_CEN;
    TIM2->DIER = 0;
    TIM2->SR = 0;
    NVIC_ClearPendingIRQ(TIM2_IRQn);
    if (tim2IrqWasEnabled) NVIC_EnableIRQ(TIM2_IRQn);
    SCB->SCR = oldScr;
    return false;
  }

  if (nfwLpSleepCount == 0UL && xdataPortMode == 1) {
    xdataSerial.print(F("[pwr]: v11 TIM2 psc="));
    xdataSerial.print((uint32_t)TIM2->PSC);
    xdataSerial.print(F(" ticks="));
    xdataSerial.print(timerTicks);
    xdataSerial.print(F(" req="));
    xdataSerial.print(sleepMs);
    xdataSerial.println(F("ms"));
    xdataSerial.flush();
  }

  HAL_SuspendTick();

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

  if (timerFired) {
    uwTick += sleepMs;
    nfwLpSleepCount++;
    nfwLpSleptMs += sleepMs;
  }
  HAL_ResumeTick();

  // Restore RPM411 MCO only after SysTick is live again, so HAL timeout logic
  // cannot hang if the HSI oscillator ever fails to become ready.
  (void)nfwLpSetHsi(true);

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
  if (!dailyGpsHasLock || !dailyGpsSleeping || dailyGpsRelockActive) return;

  const uint32_t toSlot = nfwLpSecondsToNextHorusSlot();
  if (toSlot <= NFW_LP_TX_GUARD_SECONDS) return;

  uint32_t sleepMs = (toSlot - NFW_LP_TX_GUARD_SECONDS) * 1000UL;
  if (sleepMs > NFW_LP_MAX_SLEEP_MS) sleepMs = NFW_LP_MAX_SLEEP_MS;

  const unsigned long gpsElapsed = (unsigned long)(millis() - dailyGpsLastLockMs);
  if (gpsElapsed < DAILY_GPS_RELOCK_MS) {
    const unsigned long gpsRemain = DAILY_GPS_RELOCK_MS - gpsElapsed;
    if (gpsRemain <= 3000UL) return;
    const uint32_t gpsSafeSleep = (uint32_t)(gpsRemain - 2000UL);
    if (sleepMs > gpsSafeSleep) sleepMs = gpsSafeSleep;
  }

  if (sleepMs < 2000UL) return;

  if (!nfwLpAnnounced && xdataPortMode == 1) {
    xdataSerial.println(F("[pwr]: v11 fixed-width timer sleep armed: 80MHz SYSCLK, TIM2 2kHz, HSI/RPM411 paused"));
    nfwLpAnnounced = true;
  }

  if (!nfwLpSleepMs(sleepMs)) return;

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
replace_one(old, new, "final loop v11 fixed-width low-power wrapper")

path.write_text(src, encoding="utf-8")

summary_path = Path("/out/build-summary.json")
if summary_path.exists():
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["mcu_idle_policy"] = "v11 stable 80MHz SYSCLK, TIM2 legal 16-bit PSC, 2kHz sleep timer"
    summary["mcu_idle_sysclk_hz"] = 80000000
    summary["mcu_idle_timer_hz"] = 2000
    summary["mcu_idle_timer_psc"] = 39999
    summary["mcu_idle_timer_ticks_per_ms"] = 2
    summary["mcu_idle_timer_psc_width_bits"] = 16
    summary["mcu_active_guard_before_tx_seconds"] = 30
    summary["mcu_idle_sysclk_switching"] = False
    summary["mcu_idle_hse_pll_retained"] = True
    summary["mcu_idle_hsi_off"] = True
    summary["mcu_idle_rpm411_mco_paused"] = True
    summary["mcu_idle_advances_hal_tick"] = True
    summary["mcu_idle_owns_tim2_isr"] = False
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

print("Applied RSM424 v11 fixed-width 2kHz TIM2 low-power sleep")
