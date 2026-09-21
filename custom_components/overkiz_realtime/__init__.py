"""Overkiz Realtime Position – berechnete Echtzeitposition für Somfy/Overkiz-Storen.

Die Integration legt zu einer bestehenden Cover-Entität (typischerweise aus der
offiziellen Overkiz-Integration) eine zweite Cover-Entität an, welche die
Position während der Fahrt aus Fahrtrichtung und Fahrzeit interpoliert und beim
Fahrtende wieder auf den vom Gateway gemeldeten Wert einrastet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, LOGGER, PLATFORMS, STORAGE_VERSION


@dataclass
class RealtimeRuntimeData:
    """Laufzeitdaten einer Konfigurationseintragung."""

    store: Store
    calibration: dict[str, Any] = field(default_factory=dict)

    def save(self) -> None:
        """Gelernte Werte verzögert persistieren."""
        self.store.async_delay_save(lambda: self.calibration, 30)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Konfigurationseintrag einrichten."""
    store: Store[dict[str, Any]] = Store(
        hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
    )
    calibration = await store.async_load() or {}

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = RealtimeRuntimeData(
        store=store, calibration=calibration
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Konfigurationseintrag entladen."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        runtime: RealtimeRuntimeData = hass.data[DOMAIN].pop(entry.entry_id)
        # Ausstehende Kalibrierwerte sofort sichern
        await runtime.store.async_save(runtime.calibration)
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Eintrag nach Optionsänderung neu laden."""
    LOGGER.debug("Lade %s nach Optionsänderung neu", entry.title)
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Gespeicherte Kalibrierdaten beim Entfernen löschen."""
    store: Store[dict[str, Any]] = Store(
        hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
    )
    await store.async_remove()
