"""Handling of the original Overkiz entity the realtime entity is based on.

Two cover entities for one physical shutter are one too many. This module
reconciles the entity registry with the configured ``source_handling`` mode so
that only the realtime entity is left in the user interface.

What it deliberately does *not* do is disable or delete the source entity. The
realtime entity has no connection of its own to the Somfy gateway: it reads the
source entity's state and forwards every command to it. A disabled entity is
removed from the state machine and accepts no service calls, so disabling the
source would take the realtime entity down with it. Deleting is worse still --
the Overkiz integration recreates its entities from their unique IDs on the
next reload, and the recorder history would be orphaned.

That leaves two safe operations, both of which only touch the entity registry
and are fully reversible:

``hide``
    Mark the source entity as hidden. It keeps its state, its history and its
    entity_id, automations continue to work, and it disappears from dashboards
    and auto-generated views.

``takeover``
    Hide the source entity *and* swap the two entity_ids, so the realtime
    entity ends up owning the id that existing dashboards, scripts and
    automations already point at. The source entity keeps running under a
    suffixed id.

Everything this module changes is written to the config entry's store, so that
:func:`async_restore_source_entity` can put the registry back exactly the way
it found it.
"""

from __future__ import annotations

import asyncio
from typing import NamedTuple

from homeassistant.components.cover import DOMAIN as COVER_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_SOURCE_ENTITY_ID,
    CONF_SOURCE_HANDLING,
    DOMAIN,
    FALLBACK_SOURCE_HANDLING,
    LOGGER,
    SOURCE_HANDLING_HIDE,
    SOURCE_HANDLING_KEEP,
    SOURCE_HANDLING_TAKEOVER,
    SOURCE_TAKEOVER_SUFFIX,
    STORAGE_SOURCE_HIDDEN_BY_US,
    STORAGE_SOURCE_RENAMED_FROM,
    STORAGE_SOURCE_RENAMED_TO,
)


class SourceHandling(NamedTuple):
    """Result of reconciling the registry with the configured mode."""

    source_entity_id: str
    """The entity_id the source entity has after reconciling."""

    claim_entity_id: str | None
    """The entity_id the realtime entity should take, if any."""


@callback
def async_get_source_handling(entry: ConfigEntry) -> str:
    """Return the configured handling mode for the source entity."""
    return str(entry.options.get(CONF_SOURCE_HANDLING, FALLBACK_SOURCE_HANDLING))


@callback
def async_resolve_source_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """Return the entity_id the source entity currently has.

    The config flow stores the source's *registry id* as the entry's unique id.
    Resolving through that id rather than through the stored entity_id means a
    renamed source entity is still found -- whether it was the user who renamed
    it or the takeover below.
    """
    stored: str = entry.data[CONF_SOURCE_ENTITY_ID]

    if entry.unique_id:
        registry = er.async_get(hass)
        if (source := registry.entities.get_entry(entry.unique_id)) is not None:
            return source.entity_id

    return stored


@callback
def _async_free_entity_id(hass: HomeAssistant, entity_id: str, suffix: str) -> str:
    """Return an unused entity_id derived from ``entity_id`` plus ``suffix``."""
    registry = er.async_get(hass)
    domain, object_id = entity_id.split(".", 1)
    return registry.async_get_available_entity_id(domain, f"{object_id}_{suffix}")


async def async_apply_source_handling(
    hass: HomeAssistant, entry: ConfigEntry, bookkeeping: dict
) -> SourceHandling:
    """Bring the registry in line with the configured mode.

    ``bookkeeping`` is the config entry's persisted store; it records what was
    changed so that it can be undone later.
    """
    mode = async_get_source_handling(entry)
    registry = er.async_get(hass)
    source_entity_id = async_resolve_source_entity_id(hass, entry)

    if (source := registry.async_get(source_entity_id)) is None:
        # Not registered (e.g. a YAML template cover used as the source).
        # Nothing in the registry to adjust.
        return SourceHandling(source_entity_id, None)

    if mode == SOURCE_HANDLING_KEEP:
        restored = await async_restore_source_entity(hass, entry, bookkeeping)
        return SourceHandling(restored, None)

    if mode in (SOURCE_HANDLING_HIDE, SOURCE_HANDLING_TAKEOVER):
        _async_hide_source(registry, source, bookkeeping)

    if mode != SOURCE_HANDLING_TAKEOVER:
        return SourceHandling(source_entity_id, None)

    return await _async_take_over_entity_id(
        hass, entry, registry, source_entity_id, bookkeeping
    )


@callback
def _async_hide_source(
    registry: er.EntityRegistry, source: er.RegistryEntry, bookkeeping: dict
) -> None:
    """Hide the source entity unless the user has an opinion about it."""
    if source.hidden_by is not None:
        # Already hidden -- by us on an earlier run, or by the user. Either way
        # there is nothing to do, and if the user hid it themselves we must not
        # claim it as ours to unhide later.
        return

    registry.async_update_entity(
        source.entity_id, hidden_by=er.RegistryEntryHider.INTEGRATION
    )
    bookkeeping[STORAGE_SOURCE_HIDDEN_BY_US] = True
    LOGGER.debug("Hid source entity %s", source.entity_id)


async def _async_wait_until_free(
    hass: HomeAssistant, entity_id: str, timeout: float = 5.0
) -> bool:
    """Wait until ``entity_id`` is gone from the state machine.

    Renaming a registry entry only updates the registry; the entity moves its
    state over in a task of its own. Until that has happened the old entity_id
    is still taken, and the registry refuses to hand it to anybody else. So on
    the very first takeover we have to wait for the source to vacate before the
    realtime entity can move in.
    """
    if hass.states.async_available(entity_id):
        return True

    released = asyncio.Event()

    @callback
    def _check(event: Event) -> None:
        if event.data.get("new_state") is None:
            released.set()

    unsub = async_track_state_change_event(hass, [entity_id], _check)
    try:
        async with asyncio.timeout(timeout):
            await released.wait()
    except TimeoutError:
        LOGGER.warning(
            "%s did not become free within %.0f s, leaving the entity IDs as they are",
            entity_id,
            timeout,
        )
        return False
    finally:
        unsub()

    return hass.states.async_available(entity_id)


async def _async_take_over_entity_id(
    hass: HomeAssistant,
    entry: ConfigEntry,
    registry: er.EntityRegistry,
    source_entity_id: str,
    bookkeeping: dict,
) -> SourceHandling:
    """Give the realtime entity the source entity's entity_id."""
    renamed_to = bookkeeping.get(STORAGE_SOURCE_RENAMED_TO)
    renamed_from = bookkeeping.get(STORAGE_SOURCE_RENAMED_FROM)

    if renamed_to == source_entity_id and renamed_from:
        # The swap already happened on an earlier run; nothing has to move.
        return SourceHandling(source_entity_id, str(renamed_from))

    desired = source_entity_id
    moved_to = _async_free_entity_id(hass, desired, SOURCE_TAKEOVER_SUFFIX)
    registry.async_update_entity(source_entity_id, new_entity_id=moved_to)
    bookkeeping[STORAGE_SOURCE_RENAMED_FROM] = desired
    bookkeeping[STORAGE_SOURCE_RENAMED_TO] = moved_to
    LOGGER.debug("Moved source entity %s to %s", desired, moved_to)

    if not await _async_wait_until_free(hass, desired):
        return SourceHandling(moved_to, None)

    _async_claim_entity_id(registry, entry, desired)
    return SourceHandling(moved_to, desired)


@callback
def _async_claim_entity_id(
    registry: er.EntityRegistry, entry: ConfigEntry, desired: str
) -> None:
    """Move an already registered realtime entity onto ``desired``.

    On the very first run the realtime entity is not in the registry yet; it
    then picks the id up itself by pre-setting ``entity_id`` before being added
    (see :mod:`.cover`).
    """
    own_entity_id = registry.async_get_entity_id(COVER_DOMAIN, DOMAIN, entry.entry_id)

    if own_entity_id is None or own_entity_id == desired:
        return

    if registry.async_is_registered(desired):
        LOGGER.warning(
            "Cannot move %s to %s, that entity_id is taken", own_entity_id, desired
        )
        return

    registry.async_update_entity(own_entity_id, new_entity_id=desired)
    LOGGER.debug("Moved realtime entity %s to %s", own_entity_id, desired)


async def async_restore_source_entity(
    hass: HomeAssistant, entry: ConfigEntry, bookkeeping: dict
) -> str:
    """Undo every registry change this integration made to the source entity.

    Returns the entity_id the source entity has afterwards.
    """
    registry = er.async_get(hass)
    source_entity_id = async_resolve_source_entity_id(hass, entry)

    if renamed_from := bookkeeping.pop(STORAGE_SOURCE_RENAMED_FROM, None):
        bookkeeping.pop(STORAGE_SOURCE_RENAMED_TO, None)
        source_entity_id = _async_give_back_entity_id(
            registry, entry, source_entity_id, str(renamed_from)
        )

    if bookkeeping.pop(STORAGE_SOURCE_HIDDEN_BY_US, False):
        source = registry.async_get(source_entity_id)
        if source is not None and source.hidden_by is er.RegistryEntryHider.INTEGRATION:
            registry.async_update_entity(source_entity_id, hidden_by=None)
            LOGGER.debug("Unhid source entity %s", source_entity_id)

    return source_entity_id


@callback
def _async_give_back_entity_id(
    registry: er.EntityRegistry,
    entry: ConfigEntry,
    source_entity_id: str,
    desired: str,
) -> str:
    """Return the original entity_id to the source entity."""
    own_entity_id = registry.async_get_entity_id(COVER_DOMAIN, DOMAIN, entry.entry_id)

    # Step out of the way first, otherwise the id is still taken.
    if own_entity_id == desired:
        registry.async_update_entity(
            own_entity_id,
            new_entity_id=_async_free_entity_id(registry.hass, desired, DOMAIN),
        )

    if registry.async_is_registered(desired):
        LOGGER.warning(
            "Cannot move %s back to %s, that entity_id is taken",
            source_entity_id,
            desired,
        )
        return source_entity_id

    if registry.async_get(source_entity_id) is None:
        return source_entity_id

    registry.async_update_entity(source_entity_id, new_entity_id=desired)
    LOGGER.debug("Moved source entity %s back to %s", source_entity_id, desired)
    return desired
