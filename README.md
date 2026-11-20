# MCT431 Industrial Networks — Milestone 1 Starter Project

**Fall 2026 | Two PCs + two ESP32 DevKit boards | Build revision 1**

This is an editable, integrated starter implementation aligned to the uploaded **MCT431 Project Fall 26.pdf**, especially Milestone 1 (page 5) and the general requirements (pages 1–3). **The Python simulation has been tested; the two ESP-IDF firmware projects have NOT been compiled or tested on the team's actual hardware. Hardware integration and fixes may be required.** This is not finished submission evidence.

## What is inside?

| Folder | Runs where | Purpose |
|---|---|---|
| `esp32_a/` | ESP32 A | Produces numbered virtual parts via framed USB serial |
| `esp32_b/` | ESP32 B | Wi-Fi TCP inspection server; applies N/K rule live |
| `gateway/` | PC 1 | Gateway controller, queue, state machine, HTTP/WebSocket, logs |
| `dashboard/` | Browser on PC 2 | Operator start/pause/resume/stop and status |
| `shared/` | Python gateway | ATR, SLIP and CRC utilities |
| `tests/` | Any Python PC | Framing and simulator integration tests |
| `docs/` | Team | Architecture, message specification and test procedure |
| `evidence/` | Generated at runtime | `simulation/` or `hardware/` logs; do not confuse the two |

**No motors, sensors, or Raspberry Pi needed.** You need two classic ESP32 DevKit/WROOM-style boards (UART0 USB-serial bridge), two USB data cables and two PCs. See `docs/TEAM_TEST_DAY.md` before gathering.

## Very quick no-hardware demonstration (Python installed; no pip installs)

1. Extract this ZIP on any PC with **Python 3.10+**.
2. Open Command Prompt **inside the `MCT431_Milestone1` folder**.
3. Run:

   ```powershell
   py -m gateway.app --simulate
   ```

4. Open <http://localhost:8000> on the same PC. Wait for both simulated devices to appear **Online**.
5. Keep default settings: **target=10, production=500 ms, inspection=1000 ms, N=4, K=1**, then click **Start**. When it finishes, expect **8 PASS / 2 FAIL**.
6. Try a faster simulation with `target=12`, `production_ms=100`, `inspection_ms=400`, `N=3`, `K=0`. It should eventually finish with **8 PASS / 4 FAIL**, and you should see a queue-full/backpressure event.

The `run_simulation.bat` file runs the same command if `py` is available.

**IMPORTANT:** This is *not* genuine ESP32 traffic or Wireshark evidence. Use hardware mode for submission captures. Simulation is for checking the software and learning the workflow beforehand.

## Real-hardware setup — two Windows PCs

```text
                   PC 1: GATEWAY, Wi-Fi hotspot, Python
                       | USB Serial / SLIP
                       |
                  ESP32 A (production)

 PC 1 hotspot ))) ESP32 B (inspection server over TCP)
              ))) PC 2 (operator browser via HTTP/WebSocket)
```

1. On **PC 1**, enable **2.4 GHz Wi-Fi hotspot** (or use a 2.4 GHz router). Note the SSID/password and the PC's hotspot IP address using `ipconfig`. `192.168.137.1` is common on Windows, **but do not assume it**.
2. In `esp32_b/main/app_config.h`, set `MCT_WIFI_SSID` and `MCT_WIFI_PASS` to the hotspot settings. Prefer your own strong password; the provided values are placeholders.
3. On a PC with a working **ESP-IDF v5.x** installation, flash **ESP32 B**, then open its serial monitor just long enough to read the `ESP32 B IP address` line. You may flash it from PC 1 or PC 2. Power ESP32 B from USB afterward.
4. Flash **ESP32 A**. Its `sdkconfig.defaults` disables console text so UART0 carries framed binary messages. **Close the firmware monitor** before PC 1 opens that COM port.
5. Install Python 3.10+ on PC 1 and, in the extracted project root, run:

   ```powershell
   py -m pip install -r requirements.txt
   py -m gateway.app --serial COM5 --inspection-host 192.168.137.101
   ```

   Replace `COM5` with **ESP32 A's actual COM port** and `192.168.137.101` with **ESP32 B's actual hotspot IP**.
6. Connect **PC 2** to the same hotspot. Open `http://<PC1-hotspot-IP>:8000` in Chrome or Edge; no Python or IDE is needed on PC 2 for operating the dashboard. Windows may prompt to allow inbound TCP **port 8000** through Defender Firewall (private network only).
7. Wait until both devices show **Online**, configure the batch, and press Start. Do not start until both devices are online.

### Flashing with ESP-IDF

For a classic ESP32 target, from an **ESP-IDF terminal**:

```powershell
cd esp32_b
idf.py set-target esp32
idf.py -p COM7 flash monitor
```

Read the IP address from the monitor, then leave the monitor. For ESP32 A:

```powershell
cd esp32_a
idf.py set-target esp32
idf.py -p COM5 flash
```

Run commands from the respective subfolders. The `COM7` and `COM5` examples are placeholders. `idf.py` must be executed in an ESP-IDF environment. The code targets classic ESP32 DevKit/WROOM hardware; a native USB ESP32-S3/C3 board may need USB-CDC/UART changes.

### Why PySerial but not FastAPI?

To minimize installation trouble, the gateway's HTTP server, WebSocket broadcaster, JSON messages, test tooling and simulation are implemented with **Python standard-library modules**. In *hardware mode only*, **PySerial** is needed to open the Windows serial COM port.

## Rules and exact team decisions

- **Queue capacity:** 5 by default (`--capacity 5`).
- **Queue-full policy:** **BLOCK production (backpressure)**, not discard. ESP32 A waits for the gateway's acceptance ACK. A pending arrival is not admitted into the bounded queue until there is a slot. The dashboard shows backpressure.
- **State policy:** Idle, Running, Paused, Fault. Pause stops **new production**; already queued or in-progress inspection jobs can finish. Stop aborts the current batch, leaving its counters in an aborted report. Final completion requires *all parts inspected*.
- **Configured at runtime before Start:** target parts, production cycle time, inspection time, rule parameters N and K. Firmware is not reflashed. **This starter interface does not provide mid-batch configuration edits**, which are not explicitly required for all values by the PDF; N/K are delivered live over TCP before every batch.
- **Inspection rule:** Reject if `(part_id + K) % N == 0`; actual verdict computed **on ESP32 B**. Gateway never computes production verdicts.
- **Job ID:** `B<batch_id>-P<part_id>`, preserved unchanged over serial ACK, TCP INSPECT and TCP result.
- **Three events:** DELlVERY, ACCEPTANCE and COMPLETION are separate application acknowledgements and logged separately.
- **CRC:** CRC-16/CCITT-FALSE (`poly=0x1021`, `init=0xFFFF`, `xorout=0`, no reflection). This variant is a **team design choice**; the PDF only says CRC-16/CCITT.
- **Simulated ATR payload:** UTF-8/ASCII JSON within the specified header + CRC. Milestone 2's fixed 16/64/128-byte benchmark payload pattern is **not** part of this Milestone 1 production traffic.
- **Timeouts:** 3 bounded attempts for gateway commands/jobs; 8s heartbeat threshold; production part retries limited to 3 unless explicit queue backpressure. See `docs/MESSAGE_SPEC.md`.
- **Duplicate suppression:** ESP32 B keeps verdict bits per part for the active batch (up to 1,000 parts); ESP32 A caches recent command IDs. Gateway tracks all jobs in the active batch. Exactly-once behavior is guaranteed **within a powered batch session**, not across an ESP32 power loss and reboot; a device loss causes Fault and requires a fresh batch.

## Validate locally

From project root:

```powershell
py -m unittest discover -s tests -v
```

This tests framing, CRC, TCP message boundaries, complete simulated batches and full-queue backpressure. It does not validate flashed firmware or Wi-Fi behavior.

## Evidence collection

Once the team uses real devices, the gateway automatically writes:

- `evidence/hardware/events.csv` — raw timestamps, link direction, identifiers and application events.
- `evidence/hardware/serial_frames.log` — serial traffic and raw unescaped ATR frame hex; frame checks and IDs.

Capture TCP packets on PC 1 with **Wireshark**, display filter:

```text
tcp.port == 5001
```

Verify the `INSPECT` job ID matches the `COMPLETION` job ID in the packet payload. Save a `.pcapng` and annotate screenshots. Wireshark alone will not show USB serial from a regular COM port: use the provided serial log for that.

After a completed hardware batch:

```powershell
py -m gateway.analyze_logs evidence/hardware/events.csv
```

This generates a simple batch-result SVG plot and JSON summary. These are **Milestone 1 operational measurements**, not the formal protocol comparison required in Milestone 2.

**Remaining team work:** flash hardware and validate physical communication, capture real Wireshark/serial evidence, inspect and revise code if boards differ, verify all fault/disconnect scenarios, prepare architecture/state diagrams and message specification in submission form, provide contribution table with specific member tasks and perform an individual oral rehearsal.

## Troubleshooting

- **ESP32 A offline**: Check COM port, data cable, driver, baud 115200, and that `idf.py monitor` is closed. `--simulate` does not need COM ports.
- **ESP32 B offline**: Read its actual IP from its serial monitor; confirm 2.4 GHz hotspot settings, password, same subnet, and port 5001. Ensure PC 1 can reach it.
- **PC 2 dashboard doesn't open**: Use PC 1's hotspot IP, not `localhost` on PC 2. Allow inbound TCP 8000 on the **private/local network**.
- **Corrupted serial messages**: Ensure you're using a classic ESP32 with USB-to-UART0 bridge, no application console logging and 115200 baud. Reflash after `sdkconfig.defaults` changes if necessary.
- **Python not found**: This computer needs Python installed. You can still keep/edit source code now and run it on another team member's computer.
- **Fault**: Read the red message and `evidence/hardware/events.csv`, fix the connection, then click Stop/Reset and run a **new** batch.

## Instructor document references

- Project overview, devices, wired/wireless requirements: PDF **page 1**.
- Rule N/K, queues, time values, states, IDs/ACKs, retries, duplicates and stale data: PDF **pages 2–3**.
- Required source/evidence/diagrams/contributions: PDF **page 3**.
- Milestone 1 links, interfaces and working demonstration: PDF **page 5**.
- Formal six-protocol benchmarks are **Milestone 2**, PDF **pages 4 & 6**.
