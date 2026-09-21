# Overkiz Realtime Position

<img src="custom_components/overkiz_realtime/brand/logo.png" alt="Overkiz Realtime Position" height="96">

Realtime position for Somfy/Overkiz covers in Home Assistant.

The official [Overkiz integration](https://www.home-assistant.io/integrations/overkiz/)
reports a cover's position only sporadically — typically every 15 to 20 seconds,
and reliably only once the run has finished. While the cover is moving the
dashboard therefore jumps in coarse steps, or does not move at all. That is not
a setting anyone forgot, it is a property of the Somfy cloud and local API (see
[home-assistant/core#76717](https://github.com/home-assistant/core/issues/76717)).

This custom integration puts a second cover entity next to the existing Overkiz
one. That entity computes the position from **travel direction × elapsed time**
and keeps it updated continuously. As soon as the gateway reports a real
position, the calculation snaps back onto it — so the display is smooth and
still does not drift away.

> [!NOTE]
> **Vibe-coded.** This integration was written end to end in conversation with
> an LLM ([Claude Code](https://claude.com/claude-code)) rather than typed out
> by hand. It is covered by an automated test suite that runs against a real
> Home Assistant instance, and it has been **tried out on real hardware** —
> Somfy/Overkiz covers with venetian blind slats — which is where the tilt
> edge case below was found and fixed. Travel times and `command_delay` still
> want checking against your own covers. Bug reports and pull requests are
> welcome.

## How it works

```
Command  ──►  Overkiz entity  ──►  Somfy gateway  ──►  motor
   │                 │
   │                 └─ feedback every ~20 s, reliably at the end of the run
   │
   └──►  calculator: position = start position ± (time × 100 / travel time)
                     updated every 0.5 s (configurable)
```

* **Control** — every command is passed through to the Overkiz entity
  unchanged. The integration switches nothing itself, it only calculates
  alongside.
* **Snap** — when the gateway reports the end of a run together with a
  position, the calculator is set exactly onto it (`position_estimated`
  becomes `false`).
* **Correct** — if feedback arrives mid-run that deviates by more than the
  configured threshold, the calculator adopts it immediately.
* **Follow along** — runs triggered by a radio remote, the Somfy app or
  directly on the Overkiz entity are detected and calculated along with.
* **Learn** — the actual travel time is continuously derived from the
  gateway's feedback (see below).

## Installation

### HACS

1. HACS → Integrations → ⋮ → **Custom repositories**
2. Enter `https://github.com/neuhausf/overkiz_realtime`, category **Integration**
3. Install "Overkiz Realtime Position"
4. Restart Home Assistant

### Manually

Copy the folder `custom_components/overkiz_realtime` to
`<config>/custom_components/overkiz_realtime` and restart Home Assistant.

## Setup

**Settings → Devices & services → Add integration → Overkiz Realtime Position**

1. **Source entity** — the existing `cover.*` entity from the Overkiz
   integration.
2. **Name** — name of the new entity, prefilled with "\<cover\> Realtime".
3. **Travel times** — how long a complete run from fully closed to fully open
   takes, and back. Measuring once with a stopwatch is enough; ±2 s does not
   matter, calibration takes care of the rest.
4. **Slats** — enable for venetian blinds and give the tilt time (usually
   1–2 s).
5. **Original Overkiz entity** — what should happen to it, see the next
   section.

The new entity is filed under the same Somfy device as the original.

## One cover, one entity

Two cover entities for the same physical shutter are one too many. The
**Original Overkiz entity** option decides what happens to the original:

| Mode | What it does |
| --- | --- |
| Leave it alone | Both entities stay visible. |
| **Hide** (default for new entries) | The original is marked hidden. It keeps its state, its history, its entity ID and every automation that uses it — it just disappears from dashboards and auto-generated views. |
| Take over its entity ID | As above, and the two entity IDs are swapped: the realtime entity ends up as `cover.office`, the original moves to `cover.office_overkiz`. Existing dashboards, scripts and automations keep working and now point at the realtime entity. |

### Why the original is never disabled or deleted

This came up as the obvious idea, and it is the one thing that does not work.
The realtime entity has no connection of its own to the Somfy gateway: it
**reads the original entity's state and forwards every command to it**. A
disabled entity is removed from the state machine and accepts no service calls
— disabling the original would take the realtime entity down with it.

Deleting is worse. The Overkiz integration recreates its entities from their
unique IDs on the next reload, so the deletion would not stick, and the
recorder history would be orphaned in the meantime.

That leaves hiding and renaming. Both only touch the entity registry, both
leave the Overkiz integration itself completely untouched, and both are undone
again when the config entry is removed — the original gets its entity ID and
its visibility back. If you hid the original yourself beforehand, that is left
alone as well.

## Options

Via **Configure** on the integration entry:

| Option | Default | Meaning |
| --- | --- | --- |
| Original Overkiz entity | Hide | See above |
| Travel time up / down | 25 s | Duration of a full run per direction |
| Calculate tilt position | off | Calculate tilt as well (venetian blind) |
| Tilt time open / closed | 1.5 s | Duration of a complete tilt |
| Move slats when travel starts | on | Closing tilts the slats shut, opening tilts them open |
| Update interval | 0.5 s | How often the position is recalculated while travelling |
| Command delay | 0 s | Dead time between command and the motor starting |
| Correction threshold | 15 % | Above this deviation, gateway feedback is adopted immediately; 100 % disables correction while travelling |
| Timed positioning | on | For devices without position support (RTS): the run is stopped after the calculated time |
| Keep travel times up to date | on | Automatic calibration |
| Weight of a single measurement | 0.2 | 1.0 adopts every measurement immediately, small values smooth over many runs |

### About the correction threshold

The gateway's reports lag reality by one to two seconds. Too small a threshold
therefore makes the display jump backwards mid-run. 15 % catches gross errors
without visible stuttering.

## Automatic calibration

The integration does **not** measure the travel time from "command sent" to
"gateway reports finished" — that span includes the latency of cloud, gateway
and radio link and would be systematically too long. Instead only the gateway's
position reports are weighed against each other:

```
reported:  10 % at t = 2.5 s
reported:  90 % at t = 22.5 s
           ────────────────────
           80 % in 20 s  →  25 s for 100 %
```

Because both reports are delayed by the same amount, the latency cancels out. A
measurement only counts if at least 40 % of travel lies between the first and
the last report, the run was not interrupted, and the result deviates by no
more than 50 % from the current value. The new value is folded in weighted and
stored permanently — a restart or reload does not lose it.

The values currently in use are exposed as entity attributes
(`travel_time_up`, `travel_time_down`, `last_calibration`,
`calibration_samples`).

If you want it exact, call the `overkiz_realtime.calibrate` service once: the
cover runs fully closed, fully open and closed again, and the measured times
are adopted directly.

## Services

### `overkiz_realtime.set_known_position`

Sets the calculated position without sending a movement command — for instance
after the cover has been moved by hand or with a remote.

```yaml
action: overkiz_realtime.set_known_position
target:
  entity_id: cover.living_room_realtime
data:
  position: 45
  tilt_position: 30
```

### `overkiz_realtime.set_travel_times`

Overrides the travel times at runtime and stores them permanently.

```yaml
action: overkiz_realtime.set_travel_times
target:
  entity_id: cover.living_room_realtime
data:
  travel_time_up: 23.5
  travel_time_down: 26.0
```

### `overkiz_realtime.calibrate`

Calibration run across the full travel. Requires the source entity to report a
position.

```yaml
action: overkiz_realtime.calibrate
target:
  entity_id: cover.living_room_realtime
data:
  direction: both   # both | up | down
```

## Attributes

| Attribute | Meaning |
| --- | --- |
| `source_entity_id` | The underlying Overkiz entity |
| `position_estimated` | `true` for as long as the position is calculated and not confirmed by the gateway |
| `target_position` | Target position of the current run |
| `travel_time_remaining` | Remaining travel time in seconds |
| `travel_time_up` / `travel_time_down` | Travel times currently in use |
| `tilt_time_up` / `tilt_time_down` | Tilt times currently in use |
| `last_calibration` | Time of the last adopted measurement |
| `calibration_samples` | Number of adopted measurements |

## RTS motors without position feedback

Pure RTS devices report no position at all. The integration then becomes the
only source of position:

* Target positions are reached on a timer — the integration sends
  `open`/`close` and a `stop` after the calculated time.
* Automatic calibration is impossible, as there is no feedback. The travel
  times have to be measured and entered.
* The integration knows nothing about runs made with a radio remote. In that
  case run fully open or fully closed once, or use `set_known_position`.

## Tilting the slats

Turning the slats runs the motor for a moment, and the gateway reports that
movement exactly like an ordinary opening or closing run — most visibly when
the slats are driven fully open or fully closed, which is a longer turn than a
few degrees in between.

The integration therefore does not take a movement report for a position run
while a tilt command is in flight: the calculated position stays where it is
and the slats are interpolated instead. The window closes as soon as the
gateway reports the run as finished, and a position command issued in the
meantime supersedes it. Tilts sent straight to the original Overkiz entity are
recognised the same way.

Without this, a full tilt sent the calculated position off to 0 % or 100 %
while the cover had not actually gone anywhere.

## Limits

* If the original Overkiz entity is moved to an **intermediate position** from
  outside without Home Assistant seeing the command (Somfy app, radio remote),
  the integration initially assumes a full run and only corrects with the next
  report. Commands that go through Home Assistant are detected — including
  those sent straight to the Overkiz entity.
* The calculation assumes a constant speed. Acceleration and braking ramps are
  in the range of a few tenths of a second for covers and therefore negligible.
* The motor's wind automation or obstacle detection only becomes visible to the
  integration once the gateway reports the new position.

## Development

```bash
python -m venv .venv && .venv/bin/pip install pytest-homeassistant-custom-component
.venv/bin/python -m pytest
```

The tests run against a real Home Assistant instance and cover the config flow,
interpolation, stop, resync, external runs, slats and their edge cases,
calibration, the services and the handling of the source entity.

The brand assets are generated, not hand-drawn:

```bash
python scripts/generate_brand_assets.py
```

The artwork is an original mark, deliberately not a copy of the Overkiz or
Somfy logo — those are third-party trademarks. Swap the files in
`custom_components/overkiz_realtime/brand/` if you would rather have something
else.

## License

MIT
