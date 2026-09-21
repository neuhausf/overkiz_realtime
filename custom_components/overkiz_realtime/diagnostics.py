"""Diagnostics data for Overkiz Realtime Position."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import RealtimeRuntimeData
from .const import CONF_SOURCE_ENTITY_ID, DOMAIN
from .source_entity import async_get_source_handling


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Export the diagnostics of a config entry."""
    runtime: RealtimeRuntimeData = hass.data[DOMAIN][entry.entry_id]
    source_entity_id = runtime.source_entity_id
    source_state = hass.states.get(source_entity_id)

    return {
        "entry": {
            "title": entry.title,
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "learned": dict(runtime.calibration),
        "source": {
            "configured_entity_id": entry.data[CONF_SOURCE_ENTITY_ID],
            "handling": async_get_source_handling(entry),
            "entity_id": source_entity_id,
            "state": None if source_state is None else source_state.state,
            "attributes": (
                {} if source_state is None else dict(source_state.attributes)
            ),
        },
    }
