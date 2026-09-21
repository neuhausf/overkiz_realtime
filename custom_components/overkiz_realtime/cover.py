"""Cover-Plattform mit berechneter Echtzeitposition."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timedelta
import time
from typing import Any

import voluptuous as vol

from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_CURRENT_TILT_POSITION,
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    DOMAIN as COVER_DOMAIN,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    EVENT_CALL_SERVICE,
    SERVICE_CLOSE_COVER,
    SERVICE_CLOSE_COVER_TILT,
    SERVICE_OPEN_COVER,
    SERVICE_OPEN_COVER_TILT,
    SERVICE_SET_COVER_POSITION,
    SERVICE_SET_COVER_TILT_POSITION,
    SERVICE_STOP_COVER,
    SERVICE_STOP_COVER_TILT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Context, Event, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import (
    device_registry as dr,
    entity_platform,
    entity_registry as er,
)
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from . import RealtimeRuntimeData
from .const import (
    ATTR_CALIBRATION_SAMPLES,
    ATTR_DIRECTION,
    ATTR_KNOWN_POSITION,
    ATTR_KNOWN_TILT_POSITION,
    ATTR_LAST_CALIBRATION,
    ATTR_POSITION_ESTIMATED,
    ATTR_SOURCE_ENTITY_ID,
    ATTR_TARGET_POSITION,
    ATTR_TILT_TIME_DOWN,
    ATTR_TILT_TIME_UP,
    ATTR_TRAVEL_TIME_DOWN,
    ATTR_TRAVEL_TIME_REMAINING,
    ATTR_TRAVEL_TIME_UP,
    CALIBRATION_MAX_DEVIATION,
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
    DEFAULT_AUTO_CALIBRATION,
    DEFAULT_CALIBRATION_WEIGHT,
    DEFAULT_COMMAND_DELAY,
    DEFAULT_RESYNC_THRESHOLD,
    DEFAULT_TILT_FOLLOWS_POSITION,
    DEFAULT_TILT_TIME,
    DEFAULT_TIMED_POSITIONING,
    DEFAULT_TRAVEL_TIME,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    LOGGER,
    SERVICE_CALIBRATE,
    SERVICE_SET_KNOWN_POSITION,
    SERVICE_SET_TRAVEL_TIMES,
    STATE_SRC_CLOSED,
    STATE_SRC_CLOSING,
    STATE_SRC_OPENING,
    STORAGE_LAST_CALIBRATION,
    STORAGE_SAMPLES,
    STORAGE_TILT_TIME_DOWN,
    STORAGE_TILT_TIME_UP,
    STORAGE_TRAVEL_TIME_DOWN,
    STORAGE_TRAVEL_TIME_UP,
)
from .travel_calculator import (
    POSITION_CLOSED,
    POSITION_OPEN,
    TravelCalculator,
    TravelMeasurement,
    TravelStatus,
    clamp_position,
)

TILT_FEATURES = (
    CoverEntityFeature.OPEN_TILT
    | CoverEntityFeature.CLOSE_TILT
    | CoverEntityFeature.STOP_TILT
    | CoverEntityFeature.SET_TILT_POSITION
)

# Wartezeit nach einem Kalibrier-Kommando, bis das Gateway die Fahrt meldet
CALIBRATION_START_GRACE = 6.0

# Dienste, die eine Fahrt der Quell-Entität auslösen können
_EXTERNAL_MOVE_SERVICES = {
    SERVICE_OPEN_COVER,
    SERVICE_CLOSE_COVER,
    SERVICE_SET_COVER_POSITION,
    SERVICE_STOP_COVER,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Cover-Entität für einen Konfigurationseintrag anlegen."""
    runtime: RealtimeRuntimeData = hass.data[DOMAIN][entry.entry_id]
    source_entity_id: str = entry.data[CONF_SOURCE_ENTITY_ID]

    async_add_entities([OverkizRealtimeCover(hass, entry, runtime, source_entity_id)])

    platform = entity_platform.async_get_current_platform()

    platform.async_register_entity_service(
        SERVICE_SET_KNOWN_POSITION,
        {
            vol.Required(ATTR_KNOWN_POSITION): vol.All(
                vol.Coerce(float), vol.Range(min=0, max=100)
            ),
            vol.Optional(ATTR_KNOWN_TILT_POSITION): vol.All(
                vol.Coerce(float), vol.Range(min=0, max=100)
            ),
        },
        "async_set_known_position",
    )

    platform.async_register_entity_service(
        SERVICE_SET_TRAVEL_TIMES,
        {
            vol.Optional(CONF_TRAVEL_TIME_UP): vol.All(
                vol.Coerce(float), vol.Range(min=0.5, max=600)
            ),
            vol.Optional(CONF_TRAVEL_TIME_DOWN): vol.All(
                vol.Coerce(float), vol.Range(min=0.5, max=600)
            ),
            vol.Optional(CONF_TILT_TIME_UP): vol.All(
                vol.Coerce(float), vol.Range(min=0.1, max=60)
            ),
            vol.Optional(CONF_TILT_TIME_DOWN): vol.All(
                vol.Coerce(float), vol.Range(min=0.1, max=60)
            ),
        },
        "async_set_travel_times",
    )

    platform.async_register_entity_service(
        SERVICE_CALIBRATE,
        {vol.Optional(ATTR_DIRECTION, default="both"): vol.In(["both", "up", "down"])},
        "async_calibrate",
    )


class OverkizRealtimeCover(CoverEntity, RestoreEntity):
    """Cover, dessen Position während der Fahrt interpoliert wird."""

    _attr_should_poll = False
    _attr_has_entity_name = False

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        runtime: RealtimeRuntimeData,
        source_entity_id: str,
    ) -> None:
        """Entität aufsetzen."""
        self._entry = entry
        self._runtime = runtime
        self._source_entity_id = source_entity_id

        self._attr_unique_id = entry.entry_id
        self._attr_name = entry.title

        options = entry.options
        self._tilt_enabled = bool(options.get(CONF_TILT_ENABLED, False))
        self._tilt_follows_position = bool(
            options.get(CONF_TILT_FOLLOWS_POSITION, DEFAULT_TILT_FOLLOWS_POSITION)
        )
        self._update_interval = float(
            options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
        )
        self._command_delay = float(
            options.get(CONF_COMMAND_DELAY, DEFAULT_COMMAND_DELAY)
        )
        self._resync_threshold = float(
            options.get(CONF_RESYNC_THRESHOLD, DEFAULT_RESYNC_THRESHOLD)
        )
        self._timed_positioning = bool(
            options.get(CONF_TIMED_POSITIONING, DEFAULT_TIMED_POSITIONING)
        )
        self._auto_calibration = bool(
            options.get(CONF_AUTO_CALIBRATION, DEFAULT_AUTO_CALIBRATION)
        )
        self._calibration_weight = float(
            options.get(CONF_CALIBRATION_WEIGHT, DEFAULT_CALIBRATION_WEIGHT)
        )

        learned = runtime.calibration
        self._calc = TravelCalculator(
            float(
                learned.get(
                    STORAGE_TRAVEL_TIME_DOWN,
                    options.get(CONF_TRAVEL_TIME_DOWN, DEFAULT_TRAVEL_TIME),
                )
            ),
            float(
                learned.get(
                    STORAGE_TRAVEL_TIME_UP,
                    options.get(CONF_TRAVEL_TIME_UP, DEFAULT_TRAVEL_TIME),
                )
            ),
        )
        self._tilt_calc = TravelCalculator(
            float(
                learned.get(
                    STORAGE_TILT_TIME_DOWN,
                    options.get(CONF_TILT_TIME_DOWN, DEFAULT_TILT_TIME),
                )
            ),
            float(
                learned.get(
                    STORAGE_TILT_TIME_UP,
                    options.get(CONF_TILT_TIME_UP, DEFAULT_TILT_TIME),
                )
            ),
        )

        self._measurement: TravelMeasurement | None = None
        self._force_calibration = False
        self._source_moving = False
        self._position_confirmed = False
        self._idle_event = asyncio.Event()
        self._idle_event.set()
        self._own_context_ids: deque[str] = deque(maxlen=20)

        self._unsub_updater: Any = None
        self._unsub_auto_stop: Any = None
        self._unsub_delayed_start: Any = None

        # Gerät der Quell-Entität übernehmen, damit beide Entitäten
        # beim gleichen Somfy-Gerät erscheinen.
        registry = er.async_get(hass)
        source_entry = registry.async_get(source_entity_id)
        if (
            source_entry is not None
            and source_entry.device_id
            and (device := dr.async_get(hass).async_get(source_entry.device_id))
        ):
            self.device_entry = device

    # ------------------------------------------------------------------
    # Lebenszyklus
    # ------------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        """Auf die Quelle hören und den Startzustand herstellen."""
        await super().async_added_to_hass()

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._source_entity_id], self._async_source_changed
            )
        )
        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_CALL_SERVICE, self._async_service_called)
        )

        source_state = self.hass.states.get(self._source_entity_id)
        if source_state is not None and source_state.state not in (
            STATE_UNAVAILABLE,
            STATE_UNKNOWN,
        ):
            position = self._position_from_state(source_state)
            if position is not None:
                self._calc.set_position(position)
                self._position_confirmed = True

            tilt = source_state.attributes.get(ATTR_CURRENT_TILT_POSITION)
            if self._tilt_enabled and tilt is not None:
                self._tilt_calc.set_position(float(tilt))

        if not self._calc.position_known and (
            last_state := await self.async_get_last_state()
        ):
            position = last_state.attributes.get(ATTR_CURRENT_POSITION)
            if position is not None:
                self._calc.set_position(float(position))
            tilt = last_state.attributes.get(ATTR_CURRENT_TILT_POSITION)
            if self._tilt_enabled and tilt is not None:
                self._tilt_calc.set_position(float(tilt))

    async def async_will_remove_from_hass(self) -> None:
        """Timer abräumen."""
        self._async_stop_updater()
        self._cancel_auto_stop()
        self._cancel_delayed_start()
        await super().async_will_remove_from_hass()

    # ------------------------------------------------------------------
    # Eigenschaften
    # ------------------------------------------------------------------

    @property
    def available(self) -> bool:
        """Verfügbar, solange die Quell-Entität verfügbar ist."""
        state = self.hass.states.get(self._source_entity_id)
        return state is not None and state.state not in (
            STATE_UNAVAILABLE,
            STATE_UNKNOWN,
        )

    @property
    def device_class(self) -> CoverDeviceClass | None:
        """Geräteklasse der Quelle übernehmen."""
        state = self.hass.states.get(self._source_entity_id)
        if state is not None and (
            device_class := state.attributes.get(ATTR_DEVICE_CLASS)
        ):
            try:
                return CoverDeviceClass(device_class)
            except ValueError:
                return None
        return CoverDeviceClass.SHUTTER

    @property
    def supported_features(self) -> CoverEntityFeature:
        """Unterstützte Funktionen, abgeleitet von der Quelle."""
        features = (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
            | CoverEntityFeature.SET_POSITION
        )

        if self._tilt_enabled and (self._source_features() & TILT_FEATURES):
            features |= TILT_FEATURES

        return features

    @property
    def current_cover_position(self) -> int | None:
        """Berechnete Position, 0 = geschlossen, 100 = offen."""
        if not self._calc.position_known:
            return None
        return round(self._calc.current_position())

    @property
    def current_cover_tilt_position(self) -> int | None:
        """Berechnete Lamellenposition."""
        if not self._tilt_enabled or not self._tilt_calc.position_known:
            return None
        return round(self._tilt_calc.current_position())

    @property
    def is_opening(self) -> bool:
        """True während einer Auffahrt."""
        return self._calc.is_opening()

    @property
    def is_closing(self) -> bool:
        """True während einer Abfahrt."""
        return self._calc.is_closing()

    @property
    def is_closed(self) -> bool | None:
        """True, wenn die Store vollständig geschlossen ist."""
        if not self._calc.position_known:
            return None
        return self.current_cover_position == 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Zusatzinformationen zu Berechnung und Kalibrierung."""
        attributes: dict[str, Any] = {
            ATTR_SOURCE_ENTITY_ID: self._source_entity_id,
            ATTR_TRAVEL_TIME_UP: round(self._calc.travel_time_up, 2),
            ATTR_TRAVEL_TIME_DOWN: round(self._calc.travel_time_down, 2),
            ATTR_POSITION_ESTIMATED: self._calc.is_traveling()
            or not self._position_confirmed,
        }

        if self._calc.is_traveling():
            attributes[ATTR_TARGET_POSITION] = round(self._calc.target_position)
            attributes[ATTR_TRAVEL_TIME_REMAINING] = round(
                self._calc.travel_time_remaining(), 1
            )

        if self._tilt_enabled:
            attributes[ATTR_TILT_TIME_UP] = round(self._tilt_calc.travel_time_up, 2)
            attributes[ATTR_TILT_TIME_DOWN] = round(self._tilt_calc.travel_time_down, 2)

        learned = self._runtime.calibration
        if last_calibration := learned.get(STORAGE_LAST_CALIBRATION):
            attributes[ATTR_LAST_CALIBRATION] = last_calibration
        if samples := learned.get(STORAGE_SAMPLES):
            attributes[ATTR_CALIBRATION_SAMPLES] = samples

        return attributes

    # ------------------------------------------------------------------
    # Kommandos
    # ------------------------------------------------------------------

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Store vollständig öffnen."""
        await self._async_move_to(POSITION_OPEN)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Store vollständig schliessen."""
        await self._async_move_to(POSITION_CLOSED)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Store auf eine Zielposition fahren."""
        await self._async_move_to(float(kwargs[ATTR_POSITION]))

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Fahrt anhalten und die berechnete Position einfrieren."""
        self._cancel_auto_stop()
        self._cancel_delayed_start()
        if self._source_features() & CoverEntityFeature.STOP:
            await self._async_call_source(SERVICE_STOP_COVER)
        self._freeze()

    async def async_open_cover_tilt(self, **kwargs: Any) -> None:
        """Lamellen vollständig öffnen."""
        await self._async_move_tilt_to(POSITION_OPEN)

    async def async_close_cover_tilt(self, **kwargs: Any) -> None:
        """Lamellen vollständig schliessen."""
        await self._async_move_tilt_to(POSITION_CLOSED)

    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        """Lamellen auf eine Zielposition fahren."""
        await self._async_move_tilt_to(float(kwargs[ATTR_TILT_POSITION]))

    async def async_stop_cover_tilt(self, **kwargs: Any) -> None:
        """Lamellenbewegung anhalten."""
        features = self._source_features()
        if features & CoverEntityFeature.STOP_TILT:
            await self._async_call_source(SERVICE_STOP_COVER_TILT)
        elif features & CoverEntityFeature.STOP:
            await self._async_call_source(SERVICE_STOP_COVER)
        self._tilt_calc.stop()
        self._async_refresh_state()

    async def _async_move_to(self, target: float) -> None:
        """Fahrbefehl an die Quelle senden und die Berechnung starten."""
        target = clamp_position(target)
        current = self._calc.current_position()
        features = self._source_features()
        auto_stop = False

        if target >= POSITION_OPEN:
            service: str = SERVICE_OPEN_COVER
            data: dict[str, Any] = {}
        elif target <= POSITION_CLOSED:
            service, data = SERVICE_CLOSE_COVER, {}
        elif features & CoverEntityFeature.SET_POSITION:
            service = SERVICE_SET_COVER_POSITION
            data = {ATTR_POSITION: round(target)}
        elif self._timed_positioning:
            if abs(target - current) < 1:
                return
            service = SERVICE_OPEN_COVER if target > current else SERVICE_CLOSE_COVER
            data = {}
            auto_stop = True
        else:
            raise ServiceValidationError(
                f"{self._source_entity_id} unterstützt keine Zielposition und die "
                "zeitgesteuerte Positionierung ist deaktiviert."
            )

        self._cancel_auto_stop()
        self._cancel_delayed_start()
        await self._async_call_source(service, data)
        self._schedule_travel(target, auto_stop)

    async def _async_move_tilt_to(self, target: float) -> None:
        """Lamellenbefehl an die Quelle senden und die Berechnung starten."""
        if not self._tilt_enabled:
            raise ServiceValidationError(
                "Die Lamellenberechnung ist für diese Entität nicht aktiviert."
            )

        target = clamp_position(target)
        features = self._source_features()

        if target >= POSITION_OPEN and features & CoverEntityFeature.OPEN_TILT:
            service: str = SERVICE_OPEN_COVER_TILT
            data: dict[str, Any] = {}
        elif target <= POSITION_CLOSED and features & CoverEntityFeature.CLOSE_TILT:
            service, data = SERVICE_CLOSE_COVER_TILT, {}
        elif features & CoverEntityFeature.SET_TILT_POSITION:
            service = SERVICE_SET_COVER_TILT_POSITION
            data = {ATTR_TILT_POSITION: round(target)}
        elif target >= POSITION_OPEN:
            service, data = SERVICE_OPEN_COVER_TILT, {}
        else:
            service, data = SERVICE_CLOSE_COVER_TILT, {}

        await self._async_call_source(service, data)
        self._tilt_calc.start_travel(target)
        self._async_start_updater()
        self._async_refresh_state()

    async def _async_call_source(
        self, service: str, data: dict[str, Any] | None = None
    ) -> None:
        """Dienst auf der Quell-Entität ausführen."""
        context = Context(
            parent_id=self._context.id if self._context is not None else None
        )
        self._own_context_ids.append(context.id)

        await self.hass.services.async_call(
            COVER_DOMAIN,
            service,
            {ATTR_ENTITY_ID: self._source_entity_id, **(data or {})},
            blocking=True,
            context=context,
        )

    # ------------------------------------------------------------------
    # Fahrtsteuerung
    # ------------------------------------------------------------------

    @callback
    def _schedule_travel(self, target: float, auto_stop: bool = False) -> None:
        """Fahrt starten, ggf. nach der konfigurierten Kommandoverzögerung."""
        if self._command_delay <= 0:
            self._begin_travel(target, auto_stop)
            return

        @callback
        def _start(_now: datetime) -> None:
            self._unsub_delayed_start = None
            self._begin_travel(target, auto_stop)

        self._unsub_delayed_start = async_call_later(
            self.hass, self._command_delay, _start
        )

    @callback
    def _begin_travel(self, target: float, auto_stop: bool = False) -> None:
        """Berechnung einer Fahrt beginnen."""
        self._calc.start_travel(target)

        if not self._calc.is_traveling():
            self._idle_event.set()
            self._async_refresh_state()
            return

        self._position_confirmed = False
        self._idle_event.clear()
        self._measurement = TravelMeasurement(direction=self._calc.travel_direction)

        if self._tilt_enabled and self._tilt_follows_position:
            self._tilt_calc.start_travel(
                POSITION_OPEN if self._calc.is_opening() else POSITION_CLOSED
            )

        if auto_stop:
            self._unsub_auto_stop = async_call_later(
                self.hass,
                max(self._calc.travel_time_remaining(), 0.1),
                self._async_auto_stop,
            )

        self._async_start_updater()
        self._async_refresh_state()

    async def _async_auto_stop(self, _now: datetime) -> None:
        """Zeitgesteuerter Halt, wenn die Quelle keine Zielposition kennt."""
        self._unsub_auto_stop = None
        try:
            await self._async_call_source(SERVICE_STOP_COVER)
        except HomeAssistantError as err:
            LOGGER.warning("Stop-Kommando fehlgeschlagen: %s", err)
        self._calc.stop()
        self._async_refresh_state()

    @callback
    def _freeze(self) -> None:
        """Berechnung an der aktuellen Position anhalten."""
        self._calc.stop()
        if self._tilt_enabled:
            self._tilt_calc.stop()
        if self._measurement is not None:
            self._measurement.interrupted = True
        self._async_stop_updater()
        self._async_refresh_state()

    @callback
    def _async_start_updater(self) -> None:
        """Periodische Neuberechnung während der Fahrt starten."""
        if self._unsub_updater is not None:
            return
        self._unsub_updater = async_track_time_interval(
            self.hass, self._async_tick, timedelta(seconds=self._update_interval)
        )

    @callback
    def _async_stop_updater(self) -> None:
        """Periodische Neuberechnung beenden."""
        if self._unsub_updater is not None:
            self._unsub_updater()
            self._unsub_updater = None

    @callback
    def _cancel_auto_stop(self) -> None:
        """Geplanten zeitgesteuerten Halt verwerfen."""
        if self._unsub_auto_stop is not None:
            self._unsub_auto_stop()
            self._unsub_auto_stop = None

    @callback
    def _cancel_delayed_start(self) -> None:
        """Verzögerten Fahrtstart verwerfen."""
        if self._unsub_delayed_start is not None:
            self._unsub_delayed_start()
            self._unsub_delayed_start = None

    @callback
    def _async_tick(self, _now: datetime) -> None:
        """Zyklische Neuberechnung während der Fahrt."""
        if self._calc.is_traveling() and self._calc.position_reached():
            self._calc.stop()
        if (
            self._tilt_enabled
            and self._tilt_calc.is_traveling()
            and self._tilt_calc.position_reached()
        ):
            self._tilt_calc.stop()

        if not self._calc.is_traveling() and not (
            self._tilt_enabled and self._tilt_calc.is_traveling()
        ):
            self._async_stop_updater()

        self._async_refresh_state()

    @callback
    def _async_refresh_state(self) -> None:
        """Zustand schreiben, sofern die Entität bereits registriert ist."""
        if self.hass is not None and self.entity_id:
            self.async_write_ha_state()

    # ------------------------------------------------------------------
    # Rückmeldungen der Quelle
    # ------------------------------------------------------------------

    @callback
    def _source_features(self) -> CoverEntityFeature:
        """Von der Quelle gemeldete Funktionen."""
        if (state := self.hass.states.get(self._source_entity_id)) is None:
            return CoverEntityFeature(0)
        return CoverEntityFeature(int(state.attributes.get(ATTR_SUPPORTED_FEATURES, 0)))

    @callback
    def _position_from_state(self, state: State) -> float | None:
        """Position aus einem Quellzustand lesen."""
        position = state.attributes.get(ATTR_CURRENT_POSITION)
        if position is not None:
            return clamp_position(float(position))
        # Geräte ohne Positionsrückmeldung melden wenigstens "geschlossen"
        if state.state == STATE_SRC_CLOSED:
            return POSITION_CLOSED
        return None

    @callback
    def _async_source_changed(self, event: Event) -> None:
        """Zustandsänderung der Quell-Entität verarbeiten."""
        new_state: State | None = event.data.get("new_state")

        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            self._source_moving = False
            self._async_refresh_state()
            return

        position = self._position_from_state(new_state)
        tilt = new_state.attributes.get(ATTR_CURRENT_TILT_POSITION)
        tilt = None if tilt is None else clamp_position(float(tilt))

        moving_up = new_state.state == STATE_SRC_OPENING
        moving_down = new_state.state == STATE_SRC_CLOSING
        was_moving = self._source_moving
        self._source_moving = moving_up or moving_down

        if self._source_moving:
            self._handle_source_moving(moving_up, position, was_moving)
        elif was_moving:
            self._handle_travel_finished(position, tilt)
        elif not self._calc.is_traveling():
            self._handle_source_idle(position, tilt)
        elif position is not None:
            # Wir rechnen bereits, die Quelle hat die Fahrt noch nicht
            # gemeldet: nur grobe Abweichungen korrigieren.
            self._resync(position)

        self._async_refresh_state()

    @callback
    def _handle_source_moving(
        self, moving_up: bool, position: float | None, was_moving: bool
    ) -> None:
        """Quelle meldet eine laufende Fahrt.

        Eine neue Fahrt wird nur beim Übergang in den Bewegungszustand oder bei
        einer Richtungsumkehr gestartet. Meldet das Gateway die Fahrt nur
        verzögert zu Ende, darf das keine neue Vollfahrt auslösen.
        """
        wrong_direction = (moving_up and self._calc.is_closing()) or (
            not moving_up and self._calc.is_opening()
        )

        if not was_moving or wrong_direction:
            if moving_up and not self._calc.is_opening():
                self._begin_travel(POSITION_OPEN)
            elif not moving_up and not self._calc.is_closing():
                self._begin_travel(POSITION_CLOSED)

        if position is None:
            return

        if self._measurement is not None:
            self._measurement.add(time.monotonic(), position)

        self._resync(position)

    @callback
    def _handle_travel_finished(
        self, position: float | None, tilt: float | None
    ) -> None:
        """Quelle meldet das Ende einer Fahrt."""
        self._cancel_auto_stop()

        if position is not None and self._measurement is not None:
            self._measurement.add(time.monotonic(), position)

        self._apply_calibration()

        if position is not None:
            self._calc.set_position(position)
            self._position_confirmed = True
        else:
            self._calc.stop()

        if self._tilt_enabled:
            if tilt is not None:
                self._tilt_calc.set_position(tilt)
            else:
                self._tilt_calc.stop()

        self._async_stop_updater()
        self._idle_event.set()

    @callback
    def _handle_source_idle(self, position: float | None, tilt: float | None) -> None:
        """Quelle steht still und meldet eine Position."""
        if position is not None:
            self._calc.set_position(position)
            self._position_confirmed = True
        if self._tilt_enabled and tilt is not None:
            self._tilt_calc.set_position(tilt)
        self._idle_event.set()

    @callback
    def _resync(self, position: float) -> None:
        """Grössere Abweichungen zur Rückmeldung des Gateways korrigieren."""
        if self._resync_threshold >= POSITION_OPEN:
            return

        estimated = self._calc.current_position()
        if abs(position - estimated) <= self._resync_threshold:
            return

        LOGGER.debug(
            "%s: Position wird von %.1f auf %.1f korrigiert",
            self.entity_id,
            estimated,
            position,
        )
        self._calc.update_position(position)

    @callback
    def _async_service_called(self, event: Event) -> None:
        """Fahrbefehle erkennen, die direkt an die Quell-Entität gehen.

        Damit bleibt die Berechnung auch dann korrekt, wenn eine Automation
        oder das Dashboard die originale Overkiz-Entität steuert.
        """
        data = event.data
        if data.get("domain") != COVER_DOMAIN:
            return

        service = data.get("service")
        if service not in _EXTERNAL_MOVE_SERVICES:
            return

        if event.context is not None and event.context.id in self._own_context_ids:
            return

        service_data = data.get("service_data") or {}
        entity_id = service_data.get(ATTR_ENTITY_ID)
        if isinstance(entity_id, str):
            entity_ids = {entity_id}
        elif isinstance(entity_id, (list, tuple, set)):
            entity_ids = set(entity_id)
        else:
            return

        if self._source_entity_id not in entity_ids:
            return

        if service == SERVICE_STOP_COVER:
            self._cancel_auto_stop()
            self._cancel_delayed_start()
            self._freeze()
            return

        if service == SERVICE_OPEN_COVER:
            target = POSITION_OPEN
        elif service == SERVICE_CLOSE_COVER:
            target = POSITION_CLOSED
        else:
            position = service_data.get(ATTR_POSITION)
            if position is None:
                return
            target = clamp_position(float(position))

        self._cancel_auto_stop()
        self._cancel_delayed_start()
        self._schedule_travel(target)

    # ------------------------------------------------------------------
    # Kalibrierung
    # ------------------------------------------------------------------

    @callback
    def _apply_calibration(self) -> None:
        """Gemessene Fahrzeit in die gelernten Werte einrechnen."""
        measurement = self._measurement
        self._measurement = None

        if measurement is None:
            return
        if not self._auto_calibration and not self._force_calibration:
            return
        if (measured := measurement.full_travel_time()) is None:
            return

        going_up = measurement.direction is TravelStatus.DIRECTION_UP
        current = self._calc.travel_time_up if going_up else self._calc.travel_time_down

        if (
            abs(measured - current) / current > CALIBRATION_MAX_DEVIATION
            and not self._force_calibration
        ):
            LOGGER.debug(
                "%s: Messung %.1fs verworfen (aktuell %.1fs)",
                self.entity_id,
                measured,
                current,
            )
            return

        weight = 1.0 if self._force_calibration else self._calibration_weight
        updated = current * (1 - weight) + measured * weight

        if going_up:
            self._calc.set_travel_times(self._calc.travel_time_down, updated)
            self._runtime.calibration[STORAGE_TRAVEL_TIME_UP] = round(updated, 2)
        else:
            self._calc.set_travel_times(updated, self._calc.travel_time_up)
            self._runtime.calibration[STORAGE_TRAVEL_TIME_DOWN] = round(updated, 2)

        self._runtime.calibration[STORAGE_LAST_CALIBRATION] = (
            dt_util.utcnow().isoformat()
        )
        self._runtime.calibration[STORAGE_SAMPLES] = (
            int(self._runtime.calibration.get(STORAGE_SAMPLES, 0)) + 1
        )
        self._runtime.save()

        LOGGER.info(
            "%s: Fahrzeit %s auf %.1f s angepasst (Messung %.1f s)",
            self.entity_id,
            "hoch" if going_up else "runter",
            updated,
            measured,
        )

    # ------------------------------------------------------------------
    # Dienste
    # ------------------------------------------------------------------

    async def async_set_known_position(
        self, position: float, tilt_position: float | None = None
    ) -> None:
        """Position ohne Fahrbefehl setzen, z. B. nach einem manuellen Eingriff."""
        self._cancel_auto_stop()
        self._cancel_delayed_start()
        self._async_stop_updater()

        if self._measurement is not None:
            self._measurement.interrupted = True

        self._calc.set_position(position)
        self._position_confirmed = False

        if tilt_position is not None and self._tilt_enabled:
            self._tilt_calc.set_position(tilt_position)

        self._async_refresh_state()

    async def async_set_travel_times(
        self,
        travel_time_up: float | None = None,
        travel_time_down: float | None = None,
        tilt_time_up: float | None = None,
        tilt_time_down: float | None = None,
    ) -> None:
        """Fahrzeiten zur Laufzeit setzen und dauerhaft speichern."""
        if travel_time_up is not None or travel_time_down is not None:
            self._calc.set_travel_times(
                travel_time_down or self._calc.travel_time_down,
                travel_time_up or self._calc.travel_time_up,
            )
            self._runtime.calibration[STORAGE_TRAVEL_TIME_UP] = round(
                self._calc.travel_time_up, 2
            )
            self._runtime.calibration[STORAGE_TRAVEL_TIME_DOWN] = round(
                self._calc.travel_time_down, 2
            )

        if tilt_time_up is not None or tilt_time_down is not None:
            self._tilt_calc.set_travel_times(
                tilt_time_down or self._tilt_calc.travel_time_down,
                tilt_time_up or self._tilt_calc.travel_time_up,
            )
            self._runtime.calibration[STORAGE_TILT_TIME_UP] = round(
                self._tilt_calc.travel_time_up, 2
            )
            self._runtime.calibration[STORAGE_TILT_TIME_DOWN] = round(
                self._tilt_calc.travel_time_down, 2
            )

        self._runtime.save()
        self._async_refresh_state()

    async def async_calibrate(self, direction: str = "both") -> None:
        """Kalibrierfahrt ausführen und die Fahrzeiten neu messen."""
        source_state = self.hass.states.get(self._source_entity_id)
        if (
            source_state is None
            or source_state.attributes.get(ATTR_CURRENT_POSITION) is None
        ):
            raise ServiceValidationError(
                f"{self._source_entity_id} meldet keine Position. Eine "
                "Kalibrierfahrt ist nicht möglich, bitte die Fahrzeiten mit "
                f"{DOMAIN}.{SERVICE_SET_TRAVEL_TIMES} manuell setzen."
            )

        if direction == "up":
            legs = (POSITION_CLOSED, POSITION_OPEN)
        elif direction == "down":
            legs = (POSITION_OPEN, POSITION_CLOSED)
        else:
            legs = (POSITION_CLOSED, POSITION_OPEN, POSITION_CLOSED)

        timeout = max(self._calc.travel_time_up, self._calc.travel_time_down) * 2 + 60

        self._force_calibration = True
        try:
            for target in legs:
                await self._async_run_calibration_leg(target, timeout)
        finally:
            self._force_calibration = False

    async def _async_run_calibration_leg(self, target: float, timeout: float) -> None:
        """Eine Vollfahrt ausführen und auf die Rückmeldung des Gateways warten."""
        await self._async_move_to(target)
        await asyncio.sleep(CALIBRATION_START_GRACE)

        if self._idle_event.is_set() and not self._calc.is_traveling():
            return

        try:
            async with asyncio.timeout(timeout):
                await self._idle_event.wait()
        except TimeoutError as err:
            raise HomeAssistantError(
                f"Die Kalibrierfahrt von {self._source_entity_id} wurde nicht "
                f"innerhalb von {timeout:.0f} s bestätigt."
            ) from err
