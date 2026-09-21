"""Config and options flow for Overkiz Realtime Position."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.cover import (
    ATTR_CURRENT_TILT_POSITION,
    DOMAIN as COVER_DOMAIN,
    CoverEntityFeature,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import ATTR_SUPPORTED_FEATURES, CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er, selector

from .const import (
    CONF_AUTO_CALIBRATION,
    CONF_CALIBRATION_WEIGHT,
    CONF_COMMAND_DELAY,
    CONF_RESYNC_THRESHOLD,
    CONF_SOURCE_ENTITY_ID,
    CONF_SOURCE_HANDLING,
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
    DEFAULT_SOURCE_HANDLING,
    DEFAULT_TILT_FOLLOWS_POSITION,
    DEFAULT_TILT_TIME,
    DEFAULT_TIMED_POSITIONING,
    DEFAULT_TRAVEL_TIME,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    FALLBACK_SOURCE_HANDLING,
    SOURCE_HANDLING_OPTIONS,
)

TILT_FEATURES = (
    CoverEntityFeature.OPEN_TILT
    | CoverEntityFeature.CLOSE_TILT
    | CoverEntityFeature.STOP_TILT
    | CoverEntityFeature.SET_TILT_POSITION
)


def _seconds_selector(
    minimum: float, maximum: float, step: float = 0.1
) -> selector.NumberSelector:
    """Number selector in seconds."""
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            mode=selector.NumberSelectorMode.BOX,
            unit_of_measurement="s",
        )
    )


def _source_handling_selector() -> selector.SelectSelector:
    """Pick what happens to the original Overkiz entity."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=list(SOURCE_HANDLING_OPTIONS),
            mode=selector.SelectSelectorMode.LIST,
            translation_key=CONF_SOURCE_HANDLING,
        )
    )


def _basic_schema(defaults: dict[str, Any]) -> dict[Any, Any]:
    """Fields that are identical in the config and the options flow."""
    return {
        vol.Required(
            CONF_TRAVEL_TIME_UP,
            default=defaults.get(CONF_TRAVEL_TIME_UP, DEFAULT_TRAVEL_TIME),
        ): _seconds_selector(0.5, 600),
        vol.Required(
            CONF_TRAVEL_TIME_DOWN,
            default=defaults.get(CONF_TRAVEL_TIME_DOWN, DEFAULT_TRAVEL_TIME),
        ): _seconds_selector(0.5, 600),
        vol.Required(
            CONF_TILT_ENABLED,
            default=defaults.get(CONF_TILT_ENABLED, False),
        ): selector.BooleanSelector(),
        vol.Required(
            CONF_TILT_TIME_UP,
            default=defaults.get(CONF_TILT_TIME_UP, DEFAULT_TILT_TIME),
        ): _seconds_selector(0.1, 60),
        vol.Required(
            CONF_TILT_TIME_DOWN,
            default=defaults.get(CONF_TILT_TIME_DOWN, DEFAULT_TILT_TIME),
        ): _seconds_selector(0.1, 60),
    }


def _advanced_schema(defaults: dict[str, Any]) -> dict[Any, Any]:
    """Fine tuning, only shown in the options flow."""
    return {
        vol.Required(
            CONF_SOURCE_HANDLING,
            default=defaults.get(CONF_SOURCE_HANDLING, FALLBACK_SOURCE_HANDLING),
        ): _source_handling_selector(),
        vol.Required(
            CONF_TILT_FOLLOWS_POSITION,
            default=defaults.get(
                CONF_TILT_FOLLOWS_POSITION, DEFAULT_TILT_FOLLOWS_POSITION
            ),
        ): selector.BooleanSelector(),
        vol.Required(
            CONF_UPDATE_INTERVAL,
            default=defaults.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
        ): _seconds_selector(0.2, 5),
        vol.Required(
            CONF_COMMAND_DELAY,
            default=defaults.get(CONF_COMMAND_DELAY, DEFAULT_COMMAND_DELAY),
        ): _seconds_selector(0, 10),
        vol.Required(
            CONF_RESYNC_THRESHOLD,
            default=defaults.get(CONF_RESYNC_THRESHOLD, DEFAULT_RESYNC_THRESHOLD),
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=100,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="%",
            )
        ),
        vol.Required(
            CONF_TIMED_POSITIONING,
            default=defaults.get(CONF_TIMED_POSITIONING, DEFAULT_TIMED_POSITIONING),
        ): selector.BooleanSelector(),
        vol.Required(
            CONF_AUTO_CALIBRATION,
            default=defaults.get(CONF_AUTO_CALIBRATION, DEFAULT_AUTO_CALIBRATION),
        ): selector.BooleanSelector(),
        vol.Required(
            CONF_CALIBRATION_WEIGHT,
            default=defaults.get(CONF_CALIBRATION_WEIGHT, DEFAULT_CALIBRATION_WEIGHT),
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.05, max=1.0, step=0.05, mode=selector.NumberSelectorMode.SLIDER
            )
        ),
    }


class OverkizRealtimeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up through the user interface."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        self._source_entity_id: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the source entity."""
        errors: dict[str, str] = {}

        if user_input is not None:
            source_entity_id: str = user_input[CONF_SOURCE_ENTITY_ID]
            registry = er.async_get(self.hass)
            source_entry = registry.async_get(source_entity_id)

            if source_entry is not None and source_entry.platform == DOMAIN:
                errors[CONF_SOURCE_ENTITY_ID] = "source_is_realtime"
            elif self.hass.states.get(source_entity_id) is None:
                errors[CONF_SOURCE_ENTITY_ID] = "source_unavailable"
            else:
                await self.async_set_unique_id(
                    source_entry.id if source_entry is not None else source_entity_id
                )
                self._abort_if_unique_id_configured()
                self._source_entity_id = source_entity_id
                return await self.async_step_settings()

        schema = vol.Schema(
            {
                vol.Required(CONF_SOURCE_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=COVER_DOMAIN)
                )
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the name and the travel times."""
        assert self._source_entity_id is not None

        if user_input is not None:
            options = {
                key: value for key, value in user_input.items() if key != CONF_NAME
            }
            options.update(
                {
                    CONF_TILT_FOLLOWS_POSITION: DEFAULT_TILT_FOLLOWS_POSITION,
                    CONF_UPDATE_INTERVAL: DEFAULT_UPDATE_INTERVAL,
                    CONF_COMMAND_DELAY: DEFAULT_COMMAND_DELAY,
                    CONF_RESYNC_THRESHOLD: DEFAULT_RESYNC_THRESHOLD,
                    CONF_TIMED_POSITIONING: DEFAULT_TIMED_POSITIONING,
                    CONF_AUTO_CALIBRATION: DEFAULT_AUTO_CALIBRATION,
                    CONF_CALIBRATION_WEIGHT: DEFAULT_CALIBRATION_WEIGHT,
                }
            )
            return self.async_create_entry(
                title=user_input[CONF_NAME],
                data={CONF_SOURCE_ENTITY_ID: self._source_entity_id},
                options=options,
            )

        suggested_name = self._source_entity_id.split(".", 1)[-1].replace("_", " ")
        tilt_detected = False

        if (state := self.hass.states.get(self._source_entity_id)) is not None:
            suggested_name = state.name
            features = CoverEntityFeature(
                int(state.attributes.get(ATTR_SUPPORTED_FEATURES, 0))
            )
            tilt_detected = bool(features & TILT_FEATURES) or (
                ATTR_CURRENT_TILT_POSITION in state.attributes
            )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_NAME, default=f"{suggested_name} Realtime"
                ): selector.TextSelector(),
                **_basic_schema({CONF_TILT_ENABLED: tilt_detected}),
                vol.Required(
                    CONF_SOURCE_HANDLING, default=DEFAULT_SOURCE_HANDLING
                ): _source_handling_selector(),
            }
        )

        return self.async_show_form(
            step_id="settings",
            data_schema=schema,
            description_placeholders={"source": suggested_name},
            last_step=True,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return OverkizRealtimeOptionsFlow(config_entry)


class OverkizRealtimeOptionsFlow(OptionsFlow):
    """Adjust every parameter after the fact."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialise the options flow with the config entry."""
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        defaults = dict(self._entry.options)
        schema = vol.Schema({**_basic_schema(defaults), **_advanced_schema(defaults)})

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            description_placeholders={
                "source": self._entry.data.get(CONF_SOURCE_ENTITY_ID, "")
            },
        )
