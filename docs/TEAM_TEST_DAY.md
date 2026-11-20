# Team testing day — 2-PC step-by-step checklist

## Before meeting (can do without hardware)

- [ ] Share this entire ZIP with all five members (same version).
- [ ] Agree which PC will be **Gateway (PC 1)** and which will be **Operator (PC 2)**.
- [ ] On any available Python PC, run simulation using `py -m gateway.app --simulate` and open `http://localhost:8000`.
- [ ] Read `MESSAGE_SPEC.md` so every contributor knows the same IDs and message framing.
- [ ] Assign one member to production firmware, one inspection firmware, one gateway, one operator UI, and one reliability/testing.

## Hardware bring-up in the meeting

1. [ ] Two ESP32 DevKit/WROOM boards and two *data* USB cables available.
2. [ ] Start PC 1 hotspot at **2.4 GHz**. Record SSID/password and PC 1 IP from `ipconfig`.
3. [ ] Connect PC 2 to the hotspot and verify PC 1 is reachable.
4. [ ] Edit `esp32_b/main/app_config.h` with the exact hotspot SSID/password.
5. [ ] Flash ESP32 B, read its DHCP IP on the ESP-IDF serial monitor. Keep ESP32 B powered by USB; close monitor if no longer needed.
6. [ ] Flash ESP32 A and close serial monitor. Confirm its COM port in Windows Device Manager.
7. [ ] On PC 1 (from project root): `py -m pip install -r requirements.txt`.
8. [ ] Start `py -m gateway.app --serial COMx --inspection-host <ESP32-B-IP>` with the correct values.
9. [ ] On PC 2, browse to `http://<PC1-IP>:8000`.
10. [ ] Both ESP32 indicators show Online (heartbeats and TCP connection).

## Live end-to-end demonstration tests

| Test | Instructor/demo settings | Expected |
|---|---|---|
| First batch | Target 10, production 500ms, inspection 1000ms, N4 K1 | 10 produced, 10 inspected, 8 PASS, 2 FAIL |
| Queue fills | Target 12, production 100ms, inspection 400ms, N3 K0; capacity 2 or 5 | Observable QUEUE_FULL_BACKPRESSURE; no lost parts; 8 PASS, 4 FAIL |
| Queue mostly empty | Target 10, production 1000ms, inspection 100ms, N4 K1 | Queue normally near empty; completes correctly |
| Pause/resume | Target 10 with slower interval | No new production during pause; queued inspections may finish; correct final counts |
| Stop | Start a longer batch then Stop | State Idle; active batch marked ABORTED; no false completion. Before restarting, wait until previous in-flight inspection clears |
| Duplicate | Repeat same message identifier using a test/retry tool | No duplicate inspection execution |
| Disconnect | Unplug ESP32 B or turn off hotspot during a batch | Fault rather than accepting stale data |

- [ ] Save `evidence/hardware/events.csv` after tests.
- [ ] Save `evidence/hardware/serial_frames.log`.
- [ ] Capture ESP32 B TCP exchange with Wireshark: `tcp.port == 5001`.
- [ ] Run `py -m gateway.analyze_logs evidence/hardware/events.csv` to create a simple plot.
- [ ] Confirm all five people can explain the message path end-to-end.
- [ ] Document who changed/implemented which modules, no percentage-only contribution table.

## Interpreting display values

- **Produced**: distinct valid PART messages observed at gateway (including one temporarily blocked by full queue).
- **Inspected**: completed inspection jobs with valid PASS/FAIL results from ESP32 B.
- **Queue occupancy**: accepted waiting jobs, not including the currently inspecting job or one blocked production arrival.
- **Backpressure**: gateway queue is full and a new produced part is waiting for acceptance. No part is dropped.
- **Batch completed**: produced = inspected = requested target, and accepted + rejected = target.

## One limitation to handle in the final submission

This starter code has been exercised using software simulators only. It has **not yet** been compiled with your local ESP-IDF toolchain or run against your boards. Fix any target-specific compilation/USB serial issues during the meeting, then collect actual hardware traces. Exact wording of the instructor-assigned timing range was not given in the PDF, so the starter currently accepts 100–30000 ms for both timings; if the examiner requests outside this range, revise the validation consistently in the gateway and firmware.
