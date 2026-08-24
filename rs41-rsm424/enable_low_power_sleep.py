#!/usr/bin/env python3
"""Patch the RSM424 build for long low-power idle intervals between Horus packets.

This intentionally uses STM32L412 low-power SLEEP, not STOP2. The five-minute
scheduler and 24-hour GPS relock both use millis(); using low-power sleep lets a
TIM2 one-shot keep running from an accurately-defined 2 MHz MSI clock. The CPU,
HSE/PLL and HSI are quiescent for most of each five-minute interval, while wake
timing remains deterministic.

TIM2 is already serviced by STM32duino's HardwareTimer wrapper, so this patch does
not define or replace TIM2_IRQHandler. Instead, TIM2's update request is allowed to
become pending while its NVIC line remains disabled. Cortex-M4 SEVONPEND converts
that new pending state into a WFE wake event. After wake the firmware reads UIF,
clears the timer/pending state itself, and never enters the core-owned ISR.
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
// v9 fixed-site low-power idle
//
// TIM2 is also used by PTU ring-oscillator capture. That path completely
// reprograms TIM2 for every measurement, so it is safe to borrow the peripheral
// here only after schedulerLoop() has returned and no measurement is running.
static constexpr uint32_t NFW_LP_CLOCK_HZ = 2000000UL;
static constexpr uint32_t NFW_LP_TIMER_HZ = 1000UL;
static constexpr uint32_t NFW_LP_TX_GUARD_SECONDS = 30UL;
static constexpr uint32_t NFW_LP_MAX_SLEEP_MS = 240000UL;
static uint32_t nfwLpSleepCount = 0;
static uint64_t nfwLpSleptMs = 0;
static bool nfwLpAnnounced = false;

extern "C" void SystemClock_Config(void);
// TIM2_IRQHandler is intentionally owned by STM32duino HardwareTimer. v9 wakes
// with SEVONPEND/WFE and never installs a competing TIM2_IRQHandler(void).

bool nfwLpClockDownTo2MHz() {
  RCC_OscInitTypeDef osc = {};
  RCC_ClkInitTypeDef clk = {};

  // MSI range 5 = 2 MHz, the maximum frequency allowed in STM32L4 low-power run.
  osc.OscillatorType = RCC_OSCILLATORTYPE_MSI;
  osc.MSIState = RCC_MSI_ON;
  osc.MSIClockRange = RCC_MSIRANGE_5;
  osc.MSICalibrationValue = RCC_MSICALIBRATION_DEFAULT;
  osc.PLL.PLLState = RCC_PLL_NONE;
  if (HAL_RCC_OscConfig(&osc) != HAL_OK) return false;

  clk.ClockType = RCC_CLOCKTYPE_SYSCLK | RCC_CLOCKTYPE_HCLK |
                  RCC_CLOCKTYPE_PCLK1 | RCC_CLOCKTYPE_PCLK2;
  clk.SYSCLKSource = RCC_SYSCLKSOURCE_MSI;
  clk.AHBCLKDivider = RCC_SYSCLK_DIV1;
  clk.APB1CLKDivider = RCC_HCLK_DIV1;
  clk.APB2CLKDivider = RCC_HCLK_DIV1;
  if (HAL_RCC_ClockConfig(&clk, FLASH_LATENCY_0) != HAL_OK) return false;

  SystemCoreClock = NFW_LP_CLOCK_HZ;
  SysTick_Config(SystemCoreClock / 1000UL);

  // The normal clock tree keeps HSE/PLL for the 80 MHz CPU and HSI for the
  // RPM411 MCO. Once SYSCLK is on MSI, both high-frequency sources can stop.
  RCC_OscInitTypeDef off = {};
  off.OscillatorType = RCC_OSCILLATORTYPE_HSE | RCC_OSCILLATORTYPE_HSI;
  off.HSEState = RCC_HSE_OFF;
  off.HSIState = RCC_HSI_OFF;
  off.PLL.PLLState = RCC_PLL_OFF;
  if (HAL_RCC_OscConfig(&off) != HAL_OK) return false;

  return true;
}

void nfwLpRestore80MHz() {
  // clock_override.cpp restores the exact RSM424 24 MHz HSE -> PLL -> 80 MHz
  // configuration and explicitly re-enables HSI for the RPM411 MCO source.
  SystemClock_Config();
  if (__HAL_RCC_GET_SYSCLK_SOURCE() != RCC_SYSCLKSOURCE_STATUS_MSI) {
    __HAL_RCC_MSI_DISABLE();
  }
}

bool nfwLpSleepMs(uint32_t sleepMs) {
  if (sleepMs < 2UL || sleepMs > NFW_LP_MAX_SLEEP_MS) return false;

  xdataSerial.flush();
  gpsSerial.flush();
  radioDisableTx();
  bothLedOff();

  if (!nfwLpClockDownTo2MHz()) {
    nfwLpRestore80MHz();
    return false;
  }

  // TIM2 kernel clock = 2 MHz because APB1 divider is 1. Divide by 2000 for
  // a 1 kHz timebase. TIM2 is 32 bit, so the 240,000 ms maximum fits directly.
  RCC->APB1ENR1 |= RCC_APB1ENR1_TIM2EN;
#ifdef RCC_APB1SMENR1_TIM2SMEN
  RCC->APB1SMENR1 |= RCC_APB1SMENR1_TIM2SMEN;
#endif
  TIM2->CR1 = 0;
  TIM2->CR2 = 0;
  TIM2->SMCR = 0;
  TIM2->DIER = 0;
  TIM2->CCER = 0;
  TIM2->PSC = (NFW_LP_CLOCK_HZ / NFW_LP_TIMER_HZ) - 1UL;
  TIM2->ARR = sleepMs - 1UL;
  TIM2->CNT = 0;
  TIM2->EGR = TIM_EGR_UG;
  TIM2->SR = 0;

  // Do not let the core-owned TIM2_IRQHandler execute. With SEVONPEND set,
  // a disabled IRQ that transitions to pending still creates an event and wakes
  // WFE. UIF tells us unambiguously whether our timer caused the wake.
  NVIC_DisableIRQ(TIM2_IRQn);
  NVIC_ClearPendingIRQ(TIM2_IRQn);
  SCB->SCR |= SCB_SCR_SEVONPEND_Msk;
  TIM2->DIER = TIM_DIER_UIE;
  TIM2->CR1 = TIM_CR1_OPM | TIM_CR1_CEN;

  HAL_SuspendTick();
  HAL_PWREx_EnableLowPowerRunMode();
  __HAL_FLASH_SLEEP_POWERDOWN_ENABLE();

  // Clear any stale event before the real sleep. HAL's WFE path also follows
  // the SEV/WFE/WFE pattern, but doing it here makes the event state explicit.
  __SEV();
  __WFE();
  __DSB();
  HAL_PWR_EnterSLEEPMode(PWR_LOWPOWERREGULATOR_ON, PWR_SLEEPENTRY_WFE);
  __ISB();

  const bool timerFired = (TIM2->SR & TIM_SR_UIF) != 0;

  __HAL_FLASH_SLEEP_POWERDOWN_DISABLE();
  (void)HAL_PWREx_DisableLowPowerRunMode();

  TIM2->CR1 &= ~TIM_CR1_CEN;
  TIM2->DIER = 0;
  TIM2->SR = 0;
  NVIC_ClearPendingIRQ(TIM2_IRQn);
  SCB->SCR &= ~SCB_SCR_SEVONPEND_Msk;

  nfwLpRestore80MHz();

  // SysTick did not run while asleep. Advance logical Arduino/HAL time only
  // when TIM2 actually reached the requested one-shot. An unrelated event wake
  // simply returns to the scheduler without manufacturing elapsed time.
  if (timerFired) {
    uwTick += sleepMs;
    nfwLpSleepCount++;
    nfwLpSleptMs += sleepMs;
  }
  HAL_ResumeTick();

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
  // Initial acquisition and the once-per-day relock always stay fully awake.
  if (!dailyGpsHasLock || !dailyGpsSleeping || dailyGpsRelockActive) return;

  const uint32_t toSlot = nfwLpSecondsToNextHorusSlot();
  if (toSlot <= NFW_LP_TX_GUARD_SECONDS) return;

  uint32_t sleepMs = (toSlot - NFW_LP_TX_GUARD_SECONDS) * 1000UL;
  if (sleepMs > NFW_LP_MAX_SLEEP_MS) sleepMs = NFW_LP_MAX_SLEEP_MS;

  // Never sleep across the deliberate 24-hour GPS wake. Leave 2 seconds for
  // gpsHandler() to run before the relock due point.
  const unsigned long gpsElapsed = (unsigned long)(millis() - dailyGpsLastLockMs);
  if (gpsElapsed < DAILY_GPS_RELOCK_MS) {
    const unsigned long gpsRemain = DAILY_GPS_RELOCK_MS - gpsElapsed;
    if (gpsRemain <= 3000UL) return;
    const uint32_t gpsSafeSleep = (uint32_t)(gpsRemain - 2000UL);
    if (sleepMs > gpsSafeSleep) sleepMs = gpsSafeSleep;
  }

  if (sleepMs < 2000UL) return;

  if (!nfwLpAnnounced && xdataPortMode == 1) {
    xdataSerial.println(F("[pwr]: v9 low-power idle armed: 2MHz LP-sleep between 5-minute TX slots"));
    nfwLpAnnounced = true;
  }

  if (!nfwLpSleepMs(sleepMs)) return;

  // Refresh environmental data after the long sleep when we have entered the
  // final pre-TX guard, rather than running those acquisition paths all interval.
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
replace_one(old, new, "final loop low-power wrapper")

path.write_text(src, encoding="utf-8")

summary_path = Path("/out/build-summary.json")
if summary_path.exists():
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["mcu_idle_policy"] = "STM32L412 2MHz MSI low-power sleep between 300-second Horus slots"
    summary["mcu_idle_clock_hz"] = 2000000
    summary["mcu_active_guard_before_tx_seconds"] = 30
    summary["mcu_idle_timer"] = "TIM2 one-shot at 1kHz, SEVONPEND/WFE event wake"
    summary["mcu_idle_hse_pll_off"] = True
    summary["mcu_idle_hsi_off"] = True
    summary["mcu_idle_advances_hal_tick"] = True
    summary["mcu_idle_owns_tim2_isr"] = False
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

print("Applied RSM424 v9 2MHz low-power sleep with TIM2 event wake")
