# experiments/

Scripts from the bring-up, each run by hand, at the car, with someone watching.
They are **outside the application's transmit gate**: they send what they are
told to send. They are published because the build cannot be repeated without
them, not because they are safe to run blindly.

Before running any of them:

- Read the script. Each one says at the top what it sends and why.
- Every script that transmits refuses to start without `--transmit`.
- Unplug the original Blue&Me module first. Two nodes with the same address contend.
- The values in them were measured on one car, a 2011 Giulietta 2.0 JTDm.

| Folder | What it is |
|---|---|
| `node61/node61_responder.py` | answers the diagnostic tester as the telematic node (`61`) during a PROXI alignment, and saves the record the tester writes |
| `node61/write_bc_record.py` | reads or writes record `2023` of the Body Computer. `--read-only` reads it. **Writing changes the car's configuration**: read first, keep that file, it is your way back |
| `node4021/node4021_full.py` | answers the car as the Blue&Me node (`4021`). The application does this now (`vehicle/node.py`); the script remains for tests the application does not have, such as `--glyph-test`. Needs `--value`, your car's PROXI value |
| `node4021/car_tx.sh` | runs that script at the car and puts the CAN interface back as it found it |
| `node4021/app_replay.sh` | bench: replays a recording to the installed application on `vcan0` |
| `node4021/scroll_test.py` | sends extra track text to the radio |
| `wheel/press.sh` | bench: presses a steering-wheel button through the CAN controller's loopback |
| `standby/` | standby simulations and the power measurements behind the 72-hour hold |

The `test_*.py` files next to the scripts check them on a virtual bus and need
no car: `python experiments/node61/test_node61.py`.

The scripts name recordings (`captures/...`) as the source of what they send.
Those are recordings of my car and are not published.
