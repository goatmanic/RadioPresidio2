#!/usr/bin/env python3
"""Instrument RS41-NFW XDATA output with a RAM-resident circular log.

The original XDATA UART remains fully functional. Human-readable XDATA log output
is mirrored into a 16384-byte ring buffer with stable global symbols so OpenOCD can
recover the actual recent runtime log through SWD/ST-Link.

The periodic bulk $NFW telemetry frame is deliberately *not* copied into the ring:
it can exceed 1 KB and is emitted repeatedly during calibration/runtime loops,
which would otherwise overwrite the useful human-readable diagnostics.

Arduino Print may deliver a printed string through write(uint8_t) one byte at a
time, so filtering is performed as a line-oriented byte-stream state machine. It
recognizes both the current "$NFW|" prefix and the older "$NFW," form, forwards
all bytes unchanged to the physical XDATA UART, and suppresses only those complete
$NFW lines from the RAM ring. A counter records how many $NFW frames were omitted.

RAM-log ABI v3 changes the ring from 8 KiB to 16 KiB. A magic/version pair lets
the host-side dumper refuse to decode SRAM with an incompatible decoder.
"""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit(f"Usage: {sys.argv[0]} rs41-nfw_sonde-firmware.ino")

p = Path(sys.argv[1])
s = p.read_text(encoding="utf-8")

needle = "// XDATA (2,3)          rx    tx\nHardwareSerial xdataSerial(PB11, PB10);\n"
if needle not in s:
    raise SystemExit("Could not locate XDATA HardwareSerial declaration")

replacement = r'''// XDATA (2,3)          rx    tx
// Diagnostic build: mirror human-readable XDATA TX into a RAM circular log so
// the latest real runtime log can be recovered non-invasively through ST-Link/SWD.
// Periodic bulk $NFW telemetry frames are intentionally omitted from the ring.
// Filtering is byte-stream/line oriented because Arduino Print commonly routes
// print(String/char*) through write(uint8_t) one byte at a time.
// Symbols are intentionally global and non-static for ELF/OpenOCD discovery.
constexpr uint32_t NFW_RAM_LOG_SIZE = 16384;
volatile uint32_t nfwRamLogMagic __attribute__((used)) = 0x4E46574C;    // ASCII "NFWL"
volatile uint32_t nfwRamLogVersion __attribute__((used)) = 3;
volatile uint32_t nfwRamLogWrite = 0;
volatile uint32_t nfwRamLogTotal = 0;
volatile uint32_t nfwRamLogNfwFramesOmitted = 0;
uint8_t nfwRamLog[NFW_RAM_LOG_SIZE] = {0};

static inline void nfwRamLogByte(uint8_t b) {
  uint32_t pos = nfwRamLogWrite;
  nfwRamLog[pos] = b;
  pos++;
  if (pos >= NFW_RAM_LOG_SIZE) pos = 0;
  nfwRamLogWrite = pos;
  nfwRamLogTotal++;
}

HardwareSerial xdataHardwareSerial(PB11, PB10);

class XDataRamLogSerial : public Print {
 public:
  explicit XDataRamLogSerial(HardwareSerial &serial) : hw(serial) {}

  void begin(unsigned long baud) {
    // Runtime writes deliberately keep these host-visible identity symbols in
    // the LTO-linked image and restore their known values on every XDATA start.
    nfwRamLogMagic = 0x4E46574C;
    nfwRamLogVersion = 3;
    resetLineFilter();
    hw.begin(baud);
  }
  int available() { return hw.available(); }
  int read() { return hw.read(); }
  int peek() { return hw.peek(); }
  void flush() { hw.flush(); }

  size_t write(uint8_t b) override {
    filterRamByte(b);
    return hw.write(b);
  }

  size_t write(const uint8_t *buffer, size_t size) {
    // Apply exactly the same filter to bulk writes, but preserve HardwareSerial's
    // efficient bulk forwarding. This keeps RAM filtering independent of which
    // Print overload Arduino selects for a given call site.
    for (size_t i = 0; i < size; ++i) filterRamByte(buffer[i]);
    return hw.write(buffer, size);
  }

  using Print::write;

 private:
  HardwareSerial &hw;
  uint8_t prefixBuf[5] = {0};
  uint8_t prefixLen = 0;
  bool prefixPending = true;
  bool suppressLine = false;

  void resetLineFilter() {
    prefixLen = 0;
    prefixPending = true;
    suppressLine = false;
  }

  static bool prefixByteMatches(uint8_t index, uint8_t b) {
    switch (index) {
      case 0: return b == '$';
      case 1: return b == 'N';
      case 2: return b == 'F';
      case 3: return b == 'W';
      case 4: return b == '|' || b == ',';
      default: return false;
    }
  }

  void flushPendingPrefix() {
    for (uint8_t i = 0; i < prefixLen; ++i) nfwRamLogByte(prefixBuf[i]);
    prefixLen = 0;
  }

  void filterRamByte(uint8_t b) {
    if (suppressLine) {
      // Keep forwarding to XDATA, but omit the complete $NFW line from RAM.
      if (b == '\n') resetLineFilter();
      return;
    }

    if (!prefixPending) {
      nfwRamLogByte(b);
      if (b == '\n') resetLineFilter();
      return;
    }

    // At the start of each line, delay committing the first five bytes until we
    // know whether the line is "$NFW|"/"$NFW,". This prevents the prefix itself
    // from leaking into the RAM log before suppression is decided.
    const uint8_t index = prefixLen;
    prefixBuf[prefixLen++] = b;

    if (!prefixByteMatches(index, b)) {
      flushPendingPrefix();
      prefixPending = false;
      if (b == '\n') resetLineFilter();
      return;
    }

    if (prefixLen == 5) {
      // Confirmed $NFW line. Discard the pending prefix and suppress through LF.
      prefixLen = 0;
      prefixPending = false;
      suppressLine = true;
      nfwRamLogNfwFramesOmitted++;
    }
  }
};

XDataRamLogSerial xdataSerial(xdataHardwareSerial);
'''

s = s.replace(needle, replacement, 1)
p.write_text(s, encoding="utf-8")
print("Injected 16384-byte line-filtered XDATA RAM log (NFWL v3); $NFW|/$NFW, frames omitted")
