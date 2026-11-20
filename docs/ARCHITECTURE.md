# Architecture diagram

```mermaid
flowchart TB
    PC2[PC 2: Operator browser]
    GW[PC 1: Python Gateway\nHTTP API + WebSocket server\nProduction controller + bounded queue\nCSV + serial logger]
    A[ESP32 A: Production Station\nPart generator + SLIP ATR CRC]
    B[ESP32 B: Inspection Station\nTCP server + rule N/K + dedup]
    PC2 <-->|HTTP commands + WebSocket status via Wi-Fi| GW
    A <-->|Wired USB Serial 115200| GW
    B <-->|Wi-Fi TCP 5001, length-prefix JSON| GW
```

Notes:

- The two ESP32s do not exchange traffic directly during Milestone 1.
- The gateway computer can use its own Wi-Fi hotspot or a local 2.4 GHz router.
- PC 2 needs only a browser; Python application code runs on PC 1.
- Report serial as a **wired** point-to-point connection, separately from Wi-Fi TCP and operator traffic.
- For state chart, see `MESSAGE_SPEC.md`.
