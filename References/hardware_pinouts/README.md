# Hardware Pinout References

This folder contains pinout sheets for every board used in the Stealth Autoloader project.
Keep this folder updated as hardware is added.

| File | Board | Used For |
|---|---|---|
| BTT_MMB_CAN_V2.0_pinout.png | BTT MMB CAN V2.0 | autoloader_a and autoloader_b (feed motors + sensors) |
| BTT_MMB_CAN_V1.0_pinout.jpg | BTT MMB CAN V1.0 | Reference / spare boards |
| BTT_Manta_M8P_V1.1_pinout.png | BTT Manta M8P V1.1 | Main printer MCU |
| BTT_EBB42_CAN_V1.0_pinout.png | BTT EBB42 CAN V1.0 | Toolhead boards (T0–T5) |

## MMB CAN V2.0 — Pin Assignment Summary

### Motor drivers
| Driver | STEP | DIR | EN | UART |
|---|---|---|---|---|
| M1 | PD4 | PD3 | PD5 | PB5 |
| M2 | PC9 | PC8 | PD2 | PB4 |
| M3 | PC15 | PC11 | PC10 | PB3 |
| M4 | PC13 | PC12 | PC14 | PD6 |

### 2×7 Header — Stealth Autoloader wiring (entry sensor + encoder per tool)
| Physical pair | Low pin | High pin | Assigned to |
|---|---|---|---|
| Col 0 | PC6 | PC7 | T0 entry / T0 encoder |
| Col 1 | PA8 | PA9 | T1 entry / T1 encoder |
| Col 2 | PB11 | PB12 | T2 entry / T2 encoder |
| Col 3 | PB2 | PB10 | T3 entry / T3 encoder |
| Col 4 | PB0 | PB1 | T4 entry / T4 encoder |
| Col 5 | PC4 | PC5 | T5 entry / T5 encoder |

### Dedicated endstop ports (available for future use)
STOP1=PA15  STOP2=PA10  STOP3=PD9  STOP4=PD8
