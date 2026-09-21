"""Integration tests against a real Home Assistant instance."""

from __future__ import annotations

from typing import Any

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.overkiz_realtime.const import (
    CONF_AUTO_CALIBRATION,
    CONF_CALIBRATION_WEIGHT,
    CONF_COMMAND_DELAY,
    CONF_RESYNC_THRESHOLD,
    CONF_SOURCE_ENTITY_ID,
    CONF_TILT_ENABLED,
    CONF_TILT_FOLLOWS_POSITION,
    CONF_TILT_TIME_DOWN,
    CONF_TILT_TIME_UP,
    CONF_TIMED_POSITIONING,
    CONF_TRAVEL_TIME_DOWN,
    CONF_TRAVEL_TIME_UP,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
)
from homeassistant.components.cover import CoverEntityFeature
from homeassistant.const import EVENT_CALL_SERVICE, STATE_CLOSING, STATE_OPENING
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResultType

SOURCE = "cover.storen"
TARGET = "cover.storen_echtzeit"

POSITIONABLE = (
    CoverEntityFeature.OPEN
    | CoverEntityFeature.CLOSE
    | CoverEntityFeature.STOP
    | CoverEntityFeature.SET_POSITION
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom integrations in these tests."""
    yield


class SourceCalls:
    """Records the service calls that go to the source entity."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Listen for service calls."""
        self.events: list[tuple[str, dict[str, Any]]] = []

        @callback
        def _record(event: Event) -> None:
            data = event.data
            if data.get("domain") != "cover":
                return
            service_data = data.get("service_data") or {}
            if service_data.get("entity_id") != SOURCE:
                return
            self.events.append((data["service"], service_data))

        hass.bus.async_listen(EVENT_CALL_SERVICE, _record)

    def of(self, service: str) -> list[dict[str, Any]]:
        """The calls of one particular service."""
        return [data for name, data in self.events if name == service]


@pytest.fixture
def source_calls(hass: HomeAssistant) -> SourceCalls:
    """Recording of the commands forwarded to the source."""
    return SourceCalls(hass)


def _set_source(
    hass: HomeAssistant,
    state: str,
    position: int | None = None,
    features: int = POSITIONABLE,
    **attributes: Any,
) -> None:
    """Set the state of the source entity."""
    data: dict[str, Any] = {
        "supported_features": int(features),
        "device_class": "shutter",
        "friendly_name": "Storen",
        **attributes,
    }
    if position is not None:
        data["current_position"] = position
    hass.states.async_set(SOURCE, state, data)


def _options(**overrides: Any) -> dict[str, Any]:
    """Default options for the tests."""
    options: dict[str, Any] = {
        CONF_TRAVEL_TIME_UP: 20.0,
        CONF_TRAVEL_TIME_DOWN: 25.0,
        CONF_TILT_ENABLED: False,
        CONF_TILT_TIME_UP: 1.5,
        CONF_TILT_TIME_DOWN: 1.5,
        CONF_TILT_FOLLOWS_POSITION: True,
        CONF_UPDATE_INTERVAL: 0.5,
        CONF_COMMAND_DELAY: 0.0,
        CONF_RESYNC_THRESHOLD: 15.0,
        CONF_TIMED_POSITIONING: True,
        CONF_AUTO_CALIBRATION: True,
        CONF_CALIBRATION_WEIGHT: 1.0,
    }
    options.update(overrides)
    return options


async def _setup(hass: HomeAssistant, **option_overrides: Any) -> MockConfigEntry:
    """Create a config entry and set it up."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Storen Echtzeit",
        data={CONF_SOURCE_ENTITY_ID: SOURCE},
        options=_options(**option_overrides),
        unique_id="source-id",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float
) -> None:
    """Fast forward time and fire the integration's timers."""
    steps = max(int(seconds / 0.5), 1)
    for _ in range(steps):
        freezer.tick(seconds / steps)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


async def test_config_flow_creates_entry(hass: HomeAssistant) -> None:
    """The setup wizard creates an entry."""
    _set_source(hass, "closed", 0)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SOURCE_ENTITY_ID: SOURCE}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "settings"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Storen Echtzeit",
            CONF_TRAVEL_TIME_UP: 20,
            CONF_TRAVEL_TIME_DOWN: 25,
            CONF_TILT_ENABLED: False,
            CONF_TILT_TIME_UP: 1.5,
            CONF_TILT_TIME_DOWN: 1.5,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Storen Echtzeit"
    assert result["data"] == {CONF_SOURCE_ENTITY_ID: SOURCE}
    assert result["options"][CONF_TRAVEL_TIME_UP] == 20

    await hass.async_block_till_done()
    assert hass.states.get(TARGET) is not None


async def test_config_flow_rejects_unknown_entity(hass: HomeAssistant) -> None:
    """An entity without a state is rejected."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SOURCE_ENTITY_ID: "cover.gibt_es_nicht"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_SOURCE_ENTITY_ID: "source_unavailable"}


async def test_initial_position_from_source(hass: HomeAssistant) -> None:
    """The reported position is adopted at startup."""
    _set_source(hass, "open", 40)
    await _setup(hass)

    state = hass.states.get(TARGET)
    assert state is not None
    assert state.attributes["current_position"] == 40
    assert state.attributes["source_entity_id"] == SOURCE
    assert state.attributes["position_estimated"] is False
    assert state.attributes["device_class"] == "shutter"


async def test_open_cover_interpolates(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, source_calls: SourceCalls
) -> None:
    """The position is extrapolated while opening."""
    _set_source(hass, "closed", 0)
    await _setup(hass)

    await hass.services.async_call(
        "cover", "open_cover", {"entity_id": TARGET}, blocking=True
    )
    await hass.async_block_till_done()

    assert len(source_calls.of("open_cover")) == 1

    state = hass.states.get(TARGET)
    assert state.state == STATE_OPENING
    assert state.attributes["target_position"] == 100
    assert state.attributes["position_estimated"] is True

    await _advance(hass, freezer, 10)
    assert hass.states.get(TARGET).attributes["current_position"] == 50

    await _advance(hass, freezer, 10)
    state = hass.states.get(TARGET)
    assert state.attributes["current_position"] == 100
    assert state.state == "open"


async def test_close_uses_own_travel_time(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, source_calls: SourceCalls
) -> None:
    """The closing run uses its own configured travel time."""
    _set_source(hass, "open", 100)
    await _setup(hass)

    await hass.services.async_call(
        "cover", "close_cover", {"entity_id": TARGET}, blocking=True
    )
    await hass.async_block_till_done()

    assert len(source_calls.of("close_cover")) == 1

    await _advance(hass, freezer, 12.5)
    assert hass.states.get(TARGET).attributes["current_position"] == 50
    assert hass.states.get(TARGET).state == STATE_CLOSING


async def test_set_position_forwards_to_source(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, source_calls: SourceCalls
) -> None:
    """Target positions are passed through to the source."""
    _set_source(hass, "closed", 0)
    await _setup(hass)

    await hass.services.async_call(
        "cover",
        "set_cover_position",
        {"entity_id": TARGET, "position": 60},
        blocking=True,
    )
    await hass.async_block_till_done()

    calls = source_calls.of("set_cover_position")
    assert len(calls) == 1
    assert calls[0]["position"] == 60

    await _advance(hass, freezer, 12)
    state = hass.states.get(TARGET)
    assert state.attributes["current_position"] == 60
    assert state.state == "open"


async def test_stop_freezes_position(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, source_calls: SourceCalls
) -> None:
    """A stop freezes the calculated position."""
    _set_source(hass, "closed", 0)
    await _setup(hass)

    await hass.services.async_call(
        "cover", "open_cover", {"entity_id": TARGET}, blocking=True
    )
    await _advance(hass, freezer, 5)

    await hass.services.async_call(
        "cover", "stop_cover", {"entity_id": TARGET}, blocking=True
    )
    await hass.async_block_till_done()

    assert len(source_calls.of("stop_cover")) == 1
    assert hass.states.get(TARGET).attributes["current_position"] == 25

    await _advance(hass, freezer, 10)
    assert hass.states.get(TARGET).attributes["current_position"] == 25


async def test_timed_positioning_for_rts(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, source_calls: SourceCalls
) -> None:
    """Without position support the run is stopped on a timer."""
    features = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
    )
    _set_source(hass, "open", None, features=features)
    await _setup(hass)

    await hass.services.async_call(
        DOMAIN,
        "set_known_position",
        {"entity_id": TARGET, "position": 100},
        blocking=True,
    )
    await hass.async_block_till_done()

    await hass.services.async_call(
        "cover",
        "set_cover_position",
        {"entity_id": TARGET, "position": 60},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert len(source_calls.of("close_cover")) == 1
    assert not source_calls.of("set_cover_position")

    await _advance(hass, freezer, 11)

    assert len(source_calls.of("stop_cover")) == 1
    state = hass.states.get(TARGET)
    assert state.state == "open"
    # The test clock fires timers up to 0.5 s early
    assert state.attributes["current_position"] == pytest.approx(60, abs=2)


async def test_external_movement_is_tracked(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A run triggered from outside is detected and calculated along with."""
    _set_source(hass, "open", 100)
    await _setup(hass)

    _set_source(hass, STATE_CLOSING, 100)
    await hass.async_block_till_done()

    assert hass.states.get(TARGET).state == STATE_CLOSING

    await _advance(hass, freezer, 12.5)
    assert hass.states.get(TARGET).attributes["current_position"] == 50

    _set_source(hass, "closed", 0)
    await hass.async_block_till_done()

    state = hass.states.get(TARGET)
    assert state.attributes["current_position"] == 0
    assert state.attributes["position_estimated"] is False


async def test_external_service_call_is_tracked(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A command sent straight to the Overkiz entity starts the calculation."""
    _set_source(hass, "closed", 0)
    await _setup(hass)

    await hass.services.async_call(
        "cover",
        "set_cover_position",
        {"entity_id": SOURCE, "position": 50},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert hass.states.get(TARGET).state == STATE_OPENING

    await _advance(hass, freezer, 10)
    assert hass.states.get(TARGET).attributes["current_position"] == 50


async def test_late_gateway_report_does_not_restart_travel(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, source_calls: SourceCalls
) -> None:
    """A late report from the gateway must not start a new full run."""
    _set_source(hass, "open", 100)
    await _setup(hass)

    await hass.services.async_call(
        "cover",
        "set_cover_position",
        {"entity_id": TARGET, "position": 60},
        blocking=True,
    )
    await hass.async_block_till_done()

    _set_source(hass, STATE_CLOSING, 100)
    await hass.async_block_till_done()

    # Zielposition ist erreicht, das Gateway meldet die Fahrt aber weiter
    await _advance(hass, freezer, 12)
    assert hass.states.get(TARGET).attributes["current_position"] == 60

    _set_source(hass, STATE_CLOSING, 70)
    await hass.async_block_till_done()
    await _advance(hass, freezer, 3)

    state = hass.states.get(TARGET)
    assert state.state == "open"
    assert state.attributes["current_position"] == 60

    _set_source(hass, "open", 60)
    await hass.async_block_till_done()
    assert hass.states.get(TARGET).attributes["current_position"] == 60


async def test_resync_on_large_deviation(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Large deviations in the gateway feedback are adopted."""
    _set_source(hass, "closed", 0)
    await _setup(hass)

    _set_source(hass, STATE_OPENING, 0)
    await hass.async_block_till_done()

    await _advance(hass, freezer, 10)
    assert hass.states.get(TARGET).attributes["current_position"] == 50

    _set_source(hass, STATE_OPENING, 20)
    await hass.async_block_till_done()
    assert hass.states.get(TARGET).attributes["current_position"] == 20

    _set_source(hass, STATE_OPENING, 25)
    await hass.async_block_till_done()
    assert hass.states.get(TARGET).attributes["current_position"] == 20


async def test_auto_calibration_learns_travel_time(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The travel time is kept up to date from the gateway feedback."""
    _set_source(hass, "closed", 0)
    await _setup(hass, **{CONF_RESYNC_THRESHOLD: 100.0})

    _set_source(hass, STATE_OPENING, 0)
    await hass.async_block_till_done()

    # Gateway meldet 10 % nach 2.5 s und 90 % nach 22.5 s
    # -> 80 % in 20 s -> 25 s for a full run
    await _advance(hass, freezer, 2.5)
    _set_source(hass, STATE_OPENING, 10)
    await hass.async_block_till_done()

    await _advance(hass, freezer, 20)
    _set_source(hass, STATE_OPENING, 90)
    await hass.async_block_till_done()

    await _advance(hass, freezer, 2.5)
    _set_source(hass, "open", 100)
    await hass.async_block_till_done()

    state = hass.states.get(TARGET)
    assert state.attributes["travel_time_up"] == pytest.approx(25.0, abs=0.6)
    assert state.attributes["travel_time_down"] == 25.0
    assert state.attributes["calibration_samples"] == 1


async def test_auto_calibration_rejects_outlier(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Implausible measurements leave the travel time alone."""
    _set_source(hass, "closed", 0)
    await _setup(hass, **{CONF_RESYNC_THRESHOLD: 100.0})

    _set_source(hass, STATE_OPENING, 0)
    await hass.async_block_till_done()

    await _advance(hass, freezer, 2)
    _set_source(hass, STATE_OPENING, 10)
    await hass.async_block_till_done()

    # 80 % in 80 s would mean a 100 s full run instead of the expected 20 s
    await _advance(hass, freezer, 80)
    _set_source(hass, "open", 90)
    await hass.async_block_till_done()

    assert hass.states.get(TARGET).attributes["travel_time_up"] == 20.0


async def test_tilt_follows_position(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The slats close when a closing run starts."""
    features = POSITIONABLE | CoverEntityFeature.SET_TILT_POSITION
    _set_source(hass, "open", 100, features=features, current_tilt_position=100)
    await _setup(hass, **{CONF_TILT_ENABLED: True})

    assert hass.states.get(TARGET).attributes["current_tilt_position"] == 100

    await hass.services.async_call(
        "cover", "close_cover", {"entity_id": TARGET}, blocking=True
    )
    await _advance(hass, freezer, 2)

    assert hass.states.get(TARGET).attributes["current_tilt_position"] == 0


async def test_tilt_position_forwarded(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, source_calls: SourceCalls
) -> None:
    """Tilt commands go to the source and are interpolated."""
    features = POSITIONABLE | CoverEntityFeature.SET_TILT_POSITION
    _set_source(hass, "open", 100, features=features, current_tilt_position=0)
    await _setup(hass, **{CONF_TILT_ENABLED: True})

    await hass.services.async_call(
        "cover",
        "set_cover_tilt_position",
        {"entity_id": TARGET, "tilt_position": 50},
        blocking=True,
    )
    await hass.async_block_till_done()

    calls = source_calls.of("set_cover_tilt_position")
    assert len(calls) == 1
    assert calls[0]["tilt_position"] == 50

    await _advance(hass, freezer, 1)
    assert hass.states.get(TARGET).attributes["current_tilt_position"] == 50


async def test_set_known_position_service(
    hass: HomeAssistant, source_calls: SourceCalls
) -> None:
    """The service sets the position without a movement command."""
    _set_source(hass, "open", 100)
    await _setup(hass)

    await hass.services.async_call(
        DOMAIN,
        "set_known_position",
        {"entity_id": TARGET, "position": 42},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert not source_calls.events
    assert hass.states.get(TARGET).attributes["current_position"] == 42


async def test_set_travel_times_service(hass: HomeAssistant) -> None:
    """Travel times can be changed at runtime."""
    _set_source(hass, "open", 100)
    await _setup(hass)

    await hass.services.async_call(
        DOMAIN,
        "set_travel_times",
        {"entity_id": TARGET, "travel_time_up": 33, "travel_time_down": 44},
        blocking=True,
    )
    await hass.async_block_till_done()

    state = hass.states.get(TARGET)
    assert state.attributes["travel_time_up"] == 33
    assert state.attributes["travel_time_down"] == 44


async def test_learned_times_survive_reload(hass: HomeAssistant) -> None:
    """Learned travel times survive a reload of the entry."""
    _set_source(hass, "open", 100)
    entry = await _setup(hass)

    await hass.services.async_call(
        DOMAIN,
        "set_travel_times",
        {"entity_id": TARGET, "travel_time_up": 31},
        blocking=True,
    )
    await hass.async_block_till_done()

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(TARGET).attributes["travel_time_up"] == 31


async def test_unavailable_source(hass: HomeAssistant) -> None:
    """If the source drops out, the realtime entity goes unavailable too."""
    _set_source(hass, "open", 100)
    await _setup(hass)

    hass.states.async_set(SOURCE, "unavailable", {})
    await hass.async_block_till_done()

    assert hass.states.get(TARGET).state == "unavailable"


async def test_unload_entry(hass: HomeAssistant) -> None:
    """The entry unloads cleanly."""
    _set_source(hass, "open", 100)
    entry = await _setup(hass)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(TARGET)
    assert state is None or state.state in ("unavailable", "unknown")


TILTABLE = (
    POSITIONABLE
    | CoverEntityFeature.OPEN_TILT
    | CoverEntityFeature.CLOSE_TILT
    | CoverEntityFeature.SET_TILT_POSITION
)


async def test_full_tilt_open_does_not_start_a_position_run(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, source_calls: SourceCalls
) -> None:
    """Opening the slats fully must leave the cover position where it is.

    Found on real hardware: turning the slats runs the motor for a moment and
    the gateway reports that like any other run. That report used to be taken
    for a position run, which sent the calculated position off to 100 %.
    """
    _set_source(hass, "open", 40, features=TILTABLE, current_tilt_position=0)
    await _setup(hass, **{CONF_TILT_ENABLED: True})

    await hass.services.async_call(
        "cover", "open_cover_tilt", {"entity_id": TARGET}, blocking=True
    )
    await hass.async_block_till_done()

    # The slats are driven to the angle asked for, and the cover itself is
    # not commanded at all.
    calls = source_calls.of("set_cover_tilt_position")
    assert len(calls) == 1
    assert calls[0]["tilt_position"] == 100
    assert source_calls.of("open_cover") == []

    # The gateway now reports the motor running, as it does for a tilt.
    _set_source(hass, STATE_OPENING, 40, features=TILTABLE, current_tilt_position=0)
    await _advance(hass, freezer, 3)

    state = hass.states.get(TARGET)
    assert state.attributes["current_position"] == 40
    assert state.state not in (STATE_OPENING, STATE_CLOSING)

    # And the run ends where it started.
    _set_source(hass, "open", 40, features=TILTABLE, current_tilt_position=100)
    await hass.async_block_till_done()

    state = hass.states.get(TARGET)
    assert state.attributes["current_position"] == 40
    assert state.attributes["current_tilt_position"] == 100


async def test_full_tilt_close_does_not_start_a_position_run(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The same for closing the slats fully, which reported a closing run."""
    _set_source(hass, "open", 60, features=TILTABLE, current_tilt_position=100)
    await _setup(hass, **{CONF_TILT_ENABLED: True})

    await hass.services.async_call(
        "cover", "close_cover_tilt", {"entity_id": TARGET}, blocking=True
    )
    await hass.async_block_till_done()

    _set_source(hass, STATE_CLOSING, 60, features=TILTABLE, current_tilt_position=100)
    await _advance(hass, freezer, 3)

    state = hass.states.get(TARGET)
    assert state.attributes["current_position"] == 60
    assert state.state not in (STATE_OPENING, STATE_CLOSING)


async def test_partial_tilt_still_leaves_the_position_alone(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A tilt to an intermediate angle behaved correctly and must keep doing so."""
    _set_source(hass, "open", 30, features=TILTABLE, current_tilt_position=0)
    await _setup(hass, **{CONF_TILT_ENABLED: True})

    await hass.services.async_call(
        "cover",
        "set_cover_tilt_position",
        {"entity_id": TARGET, "tilt_position": 50},
        blocking=True,
    )
    await hass.async_block_till_done()

    _set_source(hass, STATE_OPENING, 30, features=TILTABLE, current_tilt_position=0)
    await _advance(hass, freezer, 2)

    assert hass.states.get(TARGET).attributes["current_position"] == 30


async def test_tilt_run_does_not_feed_the_calibration(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Travel times must not be learned from the slats turning."""
    _set_source(hass, "open", 50, features=TILTABLE, current_tilt_position=0)
    entry = await _setup(hass, **{CONF_TILT_ENABLED: True})

    await hass.services.async_call(
        "cover", "open_cover_tilt", {"entity_id": TARGET}, blocking=True
    )
    await hass.async_block_till_done()

    _set_source(hass, STATE_OPENING, 50, features=TILTABLE, current_tilt_position=20)
    await _advance(hass, freezer, 1)
    _set_source(hass, STATE_OPENING, 55, features=TILTABLE, current_tilt_position=80)
    await _advance(hass, freezer, 1)
    _set_source(hass, "open", 55, features=TILTABLE, current_tilt_position=100)
    await hass.async_block_till_done()

    runtime = hass.data[DOMAIN][entry.entry_id]
    assert runtime.calibration == {}
    state = hass.states.get(TARGET)
    assert state.attributes["travel_time_up"] == 20.0
    assert state.attributes["travel_time_down"] == 25.0


async def test_position_run_after_a_tilt_is_still_tracked(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The tilt window must not swallow a real run that follows it."""
    _set_source(hass, "closed", 0, features=TILTABLE, current_tilt_position=0)
    await _setup(hass, **{CONF_TILT_ENABLED: True})

    await hass.services.async_call(
        "cover", "open_cover_tilt", {"entity_id": TARGET}, blocking=True
    )
    await hass.async_block_till_done()

    # Straight afterwards, while the tilt window would still be open, a real
    # opening run is commanded.
    await hass.services.async_call(
        "cover", "open_cover", {"entity_id": TARGET}, blocking=True
    )
    await _advance(hass, freezer, 10)

    # 10 s of a 20 s opening run
    assert hass.states.get(TARGET).attributes["current_position"] == pytest.approx(
        50, abs=3
    )


async def test_external_tilt_command_does_not_start_a_position_run(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A tilt sent straight to the Overkiz entity is recognised as a tilt."""
    _set_source(hass, "open", 70, features=TILTABLE, current_tilt_position=0)
    await _setup(hass, **{CONF_TILT_ENABLED: True})

    hass.bus.async_fire(
        EVENT_CALL_SERVICE,
        {
            "domain": "cover",
            "service": "open_cover_tilt",
            "service_data": {"entity_id": SOURCE},
        },
    )
    await hass.async_block_till_done()

    _set_source(hass, STATE_OPENING, 70, features=TILTABLE, current_tilt_position=0)
    await _advance(hass, freezer, 3)

    assert hass.states.get(TARGET).attributes["current_position"] == 70


async def test_full_tilt_uses_set_position_when_the_source_supports_it(
    hass: HomeAssistant, source_calls: SourceCalls
) -> None:
    """0 % and 100 % go out as a tilt position, not as open/close tilt.

    Found on real hardware: on a Somfy io venetian blind open_cover_tilt and
    close_cover_tilt only nudge the slats -- the motor twitches and the slats
    stay where they were. set_cover_tilt_position reaches the angle, which is
    why 1 % and 99 % worked while 0 % and 100 % did not.
    """
    _set_source(hass, "open", 50, features=TILTABLE, current_tilt_position=50)
    await _setup(hass, **{CONF_TILT_ENABLED: True})

    await hass.services.async_call(
        "cover", "close_cover_tilt", {"entity_id": TARGET}, blocking=True
    )
    await hass.services.async_call(
        "cover", "open_cover_tilt", {"entity_id": TARGET}, blocking=True
    )
    await hass.async_block_till_done()

    assert [
        call["tilt_position"] for call in source_calls.of("set_cover_tilt_position")
    ] == [0, 100]
    assert source_calls.of("close_cover_tilt") == []
    assert source_calls.of("open_cover_tilt") == []


async def test_full_tilt_falls_back_to_open_close_tilt_without_set_support(
    hass: HomeAssistant, source_calls: SourceCalls
) -> None:
    """A source that cannot address an angle still gets open/close tilt."""
    features = (
        POSITIONABLE | CoverEntityFeature.OPEN_TILT | CoverEntityFeature.CLOSE_TILT
    )
    _set_source(hass, "open", 50, features=features, current_tilt_position=50)
    await _setup(hass, **{CONF_TILT_ENABLED: True})

    await hass.services.async_call(
        "cover", "open_cover_tilt", {"entity_id": TARGET}, blocking=True
    )
    await hass.services.async_call(
        "cover", "close_cover_tilt", {"entity_id": TARGET}, blocking=True
    )
    await hass.async_block_till_done()

    assert len(source_calls.of("open_cover_tilt")) == 1
    assert len(source_calls.of("close_cover_tilt")) == 1
    assert source_calls.of("set_cover_tilt_position") == []
