"""Overkiz Realtime Position -- calculated realtime position for Somfy/Overkiz covers.

The integration adds a second cover entity next to an existing one (typically
from the official Overkiz integration). That entity interpolates the position
while the cover is travelling, from travel direction and travel time, and snaps
back onto the value reported by the gateway when the run ends.

The original entity is not replaced by a second cloud connection but kept as
the integration's data path: its state is read, and every command is forwarded
to it. Which is why it is only ever hidden or renamed, never disabled or
deleted -- see :mod:`.source_entity`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, LOGGER, PLATFORMS, STORAGE_VERSION
from .source_entity import (
    async_apply_source_handling,
    async_resolve_source_entity_id,
    async_restore_source_entity,
)


@dataclass
class RealtimeRuntimeData:
    """Runtime data of a config entry."""

    store: Store
    calibration: dict[str, Any] = field(default_factory=dict)
    source_entity_id: str = ""
    claim_entity_id: str | None = None

    def save(self) -> None:
        """Persist learned values with a delay."""
        self.store.async_delay_save(lambda: self.calibration, 30)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a config entry."""
    store: Store[dict[str, Any]] = Store(
        hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
    )
    calibration = await store.async_load() or {}

    # Reconcile the registry before the platform is set up, so the cover entity
    # is created with the entity_id it is meant to keep.
    before = dict(calibration)
    handling = await async_apply_source_handling(hass, entry, calibration)
    if calibration != before:
        await store.async_save(calibration)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = RealtimeRuntimeData(
        store=store,
        calibration=calibration,
        source_entity_id=handling.source_entity_id,
        claim_entity_id=handling.claim_entity_id,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        runtime: RealtimeRuntimeData = hass.data[DOMAIN].pop(entry.entry_id)
        # Persist pending calibration values right away
        await runtime.store.async_save(runtime.calibration)
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry after an options change."""
    LOGGER.debug("Reloading %s after an options change", entry.title)
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Hand the source entity back and drop the stored calibration data."""
    store: Store[dict[str, Any]] = Store(
        hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
    )
    bookkeeping = await store.async_load() or {}

    # The realtime entity is gone by now, so the original entity_id is free
    # again and the source can have it back.
    await async_restore_source_entity(hass, entry, bookkeeping)

    await store.async_remove()


__all__ = [
    "RealtimeRuntimeData",
    "async_resolve_source_entity_id",
]
