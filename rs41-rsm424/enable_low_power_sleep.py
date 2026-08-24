#!/usr/bin/env python3
"""Patch the RSM424 build for long low-power idle intervals between Horus packets.

This intentionally uses STM32L412 low-power SLEEP, not STOP2.  The reason is timing:
our five-minute scheduler and 24-hour GPS relock both use millis(), while the RSM424
has no known precision 32.768 kHz timebase exposed to NFW.  Low-power sleep lets a
TIM2 one-shot keep running from an accurately-defined 2 MHz MSI clock.  The CPU,
HSE/PLL and HSI are then quiescent for most of each five-minute interval, but wake
latency and scheduler timing remain deterministic.

Policy:
  * activate only after the M10 obtained its first valid fix and entered daily PSMOO;
  * never sleep while the daily GPS relock is active;
  * after a packet, stay fully active only during the final 30 s before the next
    300 s Horus slot;
  * use a 2 MHz MSI / low-power regulator sleep interval for the long idle part;
  * HSE, PLL and HSI are off during low-power sleep (HSI is the RPM411 MCO source,
    so the pressure daughterboard's external clock is naturally quiet while idle);
  * TIM2 is reused only while the sensor-boom capture code is idle.  The sensor code
    fully reprograms TIM2 on every measurement, so no persistent timer state is needed;
  * when TIM2 wakes the MCU, restore the stock 80 MHz HSE/PLL clock tree, advance
    HAL's millisecond tick by the intentionally slept duration, and refresh PTU/RPM411
    before the transmit guard window;
  * if any unexpected interrupt wakes the MCU before TIM2, do NOT advance millis().

The radio and LEDs remain in the v8 TX-only policy.  Startup calibration and initial
GPS acquisition run continuously at full speed exactly as before.
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


# Insert the low-power engine immediately before the final Arduino loop().  At this
# point all NFW functions plus the daily-GPS helper are already defined.
old = '''void loop() {
  schedulerLoop();
}
'''
new = r'''#ifdef RSM4x4
// ---------------------------------------------------------------------------
// v9 fixed-site low-power idle
//
// TIM2 is also used by the PTU ring-oscillator measurement code, but only while a
// sensor read is executing.  The PTU code completely reinitialises TIM2 each time,
// so it is safe to borrow TIM2 here after schedulerLoop() has returned.
static constexpr uint32_t NFW_LP_CLOCK_HZ = 2000000UL;
static constexpr uint32_t NFW_LP_TIMER_HZ = 1000UL;
static constexpr uint32_t NFW_LP_TX_GUARD_SECONDS = 30UL;
static constexpr uint32_t NFW_LP_MAX_SLEEP_MS = 240000UL;
static volatile bool nfwLpTimerFired = false;
static uint32_t nfwLpSleepCount = 0;
static uint64_t nfwLpSleptMs = 0;
static bool nfwLpAnnounced = false;

extern "C" void SystemClock_Config(void);

extern "C" void TIM2_IRQHandler(void) {
  if ((TIM2->SR & TIM_SR_UIF) && (TIM2->DIER & TIM_DIER_UIE)) {
    TIM2->SR &= ~TIM_SR_UIF;
    TIM2->DIER &= ~TIM_DIER_UIE;
    TIM2->CR1 &= ~TIM_CR1_CEN;
    nfwLpTimerFired = true;
  }
}

static bool nfwLpClockDownTo2MHz() {
  RCC_OscInitTypeDef osc = {};
  RCC_ClkInitTypeDef clk = {};

  // Bring up MSI at exactly the highest frequency allowed by STM32L4 low-power
  // run mode.  Range 5 is 2 MHz on STM32L412.
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

  // The normal v8 clock tree keeps both HSE/PLL (80 MHz SYSCLK) and HSI alive;
  // HSI also feeds MCO1/PA8 for RPM411.  Once SYSCLK is safely on MSI, remove
  // those high-frequency clocks during the long idle interval.
  RCC_OscInitTypeDef off = {};
  off.OscillatorType = RCC_OSCILLATORTYPE_HSE | RCC_OSCILLATORTYPE_HSI;
  off.HSEState = RCC_HSE_OFF;
  off.HSIState = RCC_HSI_OFF;
  off.PLL.PLLState = RCC_PLL_OFF;
  if (HAL_RCC_OscConfig(&off) != HAL_OK) return false;

  return true;
}

static void nfwLpRestore80MHz() {
  // The project's clock_override.cpp supplies the exact RSM424 24 MHz HSE ->
  // PLL -> 80 MHz configuration and explicitly re-enables HSI for RPM411 MCO.
  SystemClock_Config();

  // MSI was enabled solely for low-power run.  Once HSE/PLL is system clock,
  // remove the extra oscillator again.
  if (__HAL_RCC_GET_SYSCLK_SOURCE() != RCC_SYSCLKSOURCE_STATUS_MSI) {
    __HAL_RCC_MSI_DISABLE();
  }
}

static bool nfwLpSleepMs(uint32_t sleepMs) {
  if (sleepMs < 2UL || sleepMs > NFW_LP_MAX_SLEEP_MS) return false;

  // Finish pending output before changing peripheral clocks.  GPS is already in
  // PSMOO when this function is reachable, so there should be no outgoing GNSS
  // traffic, but flush both ports defensively.
  xdataSerial.flush();
  gpsSerial.flush();
  radioDisableTx();
  bothLedOff();

  if (!nfwLpClockDownTo2MHz()) {
    nfwLpRestore80MHz();
    return false;
  }

  // TIM2 kernel clock = APB1 timer clock = 2 MHz (APB1 divider is 1).  Divide
  // by 2000 for a 1 kHz millisecond one-shot.  TIM2 is 32 bit on STM32L412.
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
  TIM2->DIER = TIM_DIER_UIE;

  nfwLpTimerFired = false;
  NVIC_ClearPendingIRQ(TIM2_IRQn);
  NVIC_SetPriority(TIM2_IRQn, 3);
  NVIC_EnableIRQ(TIM2_IRQn);
  TIM2->CR1 = TIM_CR1_OPM | TIM_CR1_CEN;

  // Enter STM32L4 low-power run, then low-power sleep.  SysTick is suspended so
  // it cannot wake us every millisecond; TIM2 is the intended wake source.
  HAL_SuspendTick();
  HAL_PWREx_EnableLowPowerRunMode();
  __HAL_FLASH_SLEEP_POWERDOWN_ENABLE();
  __DSB();
  HAL_PWR_EnterSLEEPMode(PWR_LOWPOWERREGULATOR_ON, PWR_SLEEPENTRY_WFI);
  __ISB();

  // We wake still at 2 MHz in low-power run mode.  Return to the normal regulator
  // before asking the clock override to restore 80 MHz.
  __HAL_FLASH_SLEEP_POWERDOWN_DISABLE();
  (void)HAL_PWREx_DisableLowPowerRunMode();

  TIM2->CR1 &= ~TIM_CR1_CEN;
  TIM2->DIER = 0;
  TIM2->SR = 0;
  NVIC_DisableIRQ(TIM2_IRQn);
  NVIC_ClearPendingIRQ(TIM2_IRQn);

  nfwLpRestore80MHz();

  // SysTick did not run while asleep.  Only advance the HAL/Arduino millisecond
  // epoch if our one-shot really caused the wake; an unrelated interrupt may wake
  // early and must not make scheduler time jump by the full requested duration.
  if (nfwLpTimerFired) {
    uwTick += sleepMs;
    nfwLpSleepCount++;
    nfwLpSleptMs += sleepMs;
  }
  HAL_ResumeTick();

  return nfwLpTimerFired;
}

static uint32_t nfwLpSecondsToNextHorusSlot() {
  const uint32_t period = (uint32_t)horusV3TimeSyncSeconds;
  if (period < 5UL) return 0UL;
  const uint32_t now = dailyGpsUtcNowSeconds();
  const uint32_t phase = now % period;
  return (phase == 0UL) ? 0UL : (period - phase);
}

static void nfwLowPowerIdleBetweenPackets() {
  // Initial GNSS acquisition and every daily relock remain fully awake.  This is
  // important both for acquisition performance and because dailyGpsUtcNowSeconds
  // only becomes a stable free-running clock after the first accepted fix.
  if (!dailyGpsHasLock || !dailyGpsSleeping || dailyGpsRelockActive) return;

  const uint32_t toSlot = nfwLpSecondsToNextHorusSlot();
  if (toSlot <= NFW_LP_TX_GUARD_SECONDS) return;

  uint32_t sleepMs = (toSlot - NFW_LP_TX_GUARD_SECONDS) * 1000UL;
  if (sleepMs > NFW_LP_MAX_SLEEP_MS) sleepMs = NFW_LP_MAX_SLEEP_MS;

  // Never sleep across the deliberate 24-hour GNSS wake.  Leave a 2-second
  // margin so gpsHandler() gets CPU time before the due point.
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

  const bool fullWake = nfwLpSleepMs(sleepMs);
  if (!fullWake) return;

  // If this wake places us inside the pre-TX guard, refresh the environmental
  // measurements once now.  That gives the scheduler fresh PTU/RPM411 values
  // without keeping those measurement paths active throughout the idle period.
  const uint32_t afterToSlot = nfwLpSecondsToNextHorusSlot();
  if (afterToSlot > 0UL && afterToSlot <= NFW_LP_TX_GUARD_SECONDS) {
    pressureHandler();
    sensorBoomHandler();
  }

  // Sparse diagnostics: first wake and then every 12 sleep entries (~hour-scale
  // in the 5-minute profile), so the RAM log is not flooded.
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
    summary["mcu_idle_timer"] = "TIM2 one-shot at 1kHz"
    summary["mcu_idle_hse_pll_off"] = True
    summary["mcu_idle_hsi_off"] = True
    summary["mcu_idle_advances_hal_tick"] = True
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

print("Applied RSM424 v9 2MHz low-power sleep between five-minute TX windows")
