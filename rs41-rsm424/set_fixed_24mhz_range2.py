#!/usr/bin/env python3
"""Set RSM424 STM32L412 to fixed 24 MHz HSE SYSCLK, PLL off, VOS Range 2.

This deliberately keeps the proven v12 SysTick/WFI wall-clock design. Only the
boot-time clock/power policy changes:
  * 24 MHz board crystal (HSE) feeds SYSCLK directly;
  * PLL is never enabled;
  * HSI16 remains enabled while active because PA8/MCO clocks the RPM411;
  * FLASH latency is 3 WS, required for 24 MHz in Voltage Range 2;
  * after the 24 MHz clock and 3 WS latency are established, VOS is changed from
    Range 1 to Range 2;
  * SystemCoreClock and SysTick are explicitly corrected because generic
    STM32duino builds use HSE_VALUE=8 MHz while this board's crystal is 24 MHz.

The script also retags the inherited v12 low-power diagnostic strings as v13 and
updates /out/build-summary.json so CI can audit the exact clock policy.
"""
from pathlib import Path
import json
import re
import sys

if len(sys.argv) != 3:
    raise SystemExit(
        f"Usage: {sys.argv[0]} clock_override.cpp rs41-nfw_sonde-firmware.ino"
    )

clock_path = Path(sys.argv[1])
ino_path = Path(sys.argv[2])
clock = clock_path.read_text(encoding="utf-8")
ino = ino_path.read_text(encoding="utf-8")

# Replace only the STM32L412 SystemClock_Config body. Keep the RSM4x2/F100 branch
# completely untouched.
pattern = re.compile(
    r'(// ── RSM4x4 · STM32L412 .*?\n#if defined\(ARDUINO_GENERIC_L412RBIXP\) \|\| defined\(ARDUINO_GENERIC_L412RBTXP\)\n\n)'
    r'extern "C" void SystemClock_Config\(void\)\n\{.*?\n\}\n\n'
    r'(?=// ── RSM4x2)',
    re.S,
)

replacement = r'''\1extern "C" void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {};

  // v13: fixed 24 MHz HSE clock, no PLL. HSI16 stays on while active because
  // MCO1/PA8 uses HSI to clock the RPM411 pressure daughterboard.
  //
  // RM0394 Range-2 transition sequence is respected:
  //   1. establish a system frequency <= 26 MHz;
  //   2. set Flash latency appropriate for the Range-2 target (3 WS @ 24 MHz);
  //   3. switch VOS to Range 2.
  RCC_OscInitStruct.OscillatorType      = RCC_OSCILLATORTYPE_HSE | RCC_OSCILLATORTYPE_HSI;
  RCC_OscInitStruct.HSEState            = RCC_HSE_ON;
  RCC_OscInitStruct.HSIState            = RCC_HSI_ON;
  RCC_OscInitStruct.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
  RCC_OscInitStruct.PLL.PLLState        = RCC_PLL_OFF;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK) {
    Error_Handler();
  }

  RCC_ClkInitStruct.ClockType           = RCC_CLOCKTYPE_HCLK  | RCC_CLOCKTYPE_SYSCLK
                                        | RCC_CLOCKTYPE_PCLK1 | RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource        = RCC_SYSCLKSOURCE_HSE;
  RCC_ClkInitStruct.AHBCLKDivider       = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider      = RCC_HCLK_DIV1;
  RCC_ClkInitStruct.APB2CLKDivider      = RCC_HCLK_DIV1;
  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_3) != HAL_OK) {
    Error_Handler();
  }

  // Generic STM32duino has HSE_VALUE=8 MHz. Correct the runtime clock variable
  // and the 1 ms SysTick to the physical 24 MHz board crystal before using HAL
  // timeout machinery again.
  SystemCoreClock = 24000000UL;
  SysTick_Config(SystemCoreClock / 1000UL);

  // 24 MHz is inside the STM32L412 Range-2 limit (26 MHz). Flash is already at
  // the required 3 WS before lowering VCORE.
  if (HAL_PWREx_ControlVoltageScaling(PWR_REGULATOR_VOLTAGE_SCALE2) != HAL_OK) {
    Error_Handler();
  }
}

'''
clock, n = pattern.subn(replacement, clock, count=1)
if n != 1:
    raise SystemExit(f"L412 clock override: expected exactly one replacement, got {n}")

# Retag the v12 low-power diagnostics so hardware logs identify the actual image.
replacements = {
    '[pwr]: v12 systick sleep start req=': '[pwr]: v13 24MHz-R2 systick sleep start req=',
    '[pwr]: v12 systick sleep end elapsed=': '[pwr]: v13 24MHz-R2 systick sleep end elapsed=',
    '[pwr]: v12 conservative sleep armed: SysTick wall clock, WFI CPU sleep, HSI/RPM411 paused':
        '[pwr]: v13 24MHz Range2 sleep armed: HSE direct, PLL off, SysTick/WFI, HSI/RPM411 paused',
}
for old, new in replacements.items():
    count = ino.count(old)
    if count != 1:
        raise SystemExit(f"diagnostic retag {old!r}: expected 1 match, got {count}")
    ino = ino.replace(old, new, 1)

clock_path.write_text(clock, encoding="utf-8")
ino_path.write_text(ino, encoding="utf-8")

policy = {
    "board": "RSM424 / STM32L412",
    "sysclk_source": "HSE direct",
    "hse_hz": 24000000,
    "sysclk_hz": 24000000,
    "hclk_hz": 24000000,
    "pclk1_hz": 24000000,
    "pclk2_hz": 24000000,
    "pll_enabled": False,
    "hsi_active_enabled": True,
    "hsi_idle_enabled": False,
    "voltage_scaling": "Range 2",
    "flash_latency_ws": 3,
    "systick_hz": 1000,
    "runtime_sysclk_switching": False,
}
Path("/out/clock-policy.json").write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")

summary_path = Path("/out/build-summary.json")
if summary_path.exists():
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["mcu_idle_policy"] = "v13 fixed 24MHz HSE Range2 + v12-proven SysTick/WFI sleep"
    summary["mcu_idle_sysclk_hz"] = 24000000
    summary["mcu_clock_source"] = "24MHz HSE direct"
    summary["mcu_pll_enabled"] = False
    summary["mcu_voltage_scaling"] = "Range 2"
    summary["mcu_flash_latency_ws"] = 3
    summary["mcu_idle_hse_retained"] = True
    summary["mcu_idle_hse_pll_retained"] = False
    summary["mcu_idle_pll_enabled"] = False
    summary["mcu_idle_sysclk_switching"] = False
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

print("Applied RSM424 v13 fixed 24 MHz HSE / PLL-off / Voltage Range 2 policy")
