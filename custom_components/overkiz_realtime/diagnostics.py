"""Diagnosedaten für Overkiz Realtime Position."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import RealtimeRuntimeData
from .const import CONF_SOURCE_ENTITY_ID, DOMAIN


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Diagnose eines Konfigurationseintrags exportieren."""
    runtime: RealtimeRuntimeData = hass.data[DOMAIN][entry.entry_id]
    source_entity_id: str = entry.data[CONF_SOURCE_ENTITY_ID]
    source_state = hass.states.get(source_entity_id)

    return {
        "entry": {
            "title": entry.title,
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "learned": dict(runtime.calibration),
        "source": {
            "entity_id": source_entity_id,
            "state": None if source_state is None else source_state.state,
            "attributes": (
                {} if source_state is None else dict(source_state.attributes)
            ),
        },
    }
