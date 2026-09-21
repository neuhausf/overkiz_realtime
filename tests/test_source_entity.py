"""Tests for what happens to the original Overkiz entity.

The realtime entity is only useful if it replaces the original one in the user
interface. These tests pin down that it does so without ever cutting the branch
it sits on: the source entity stays loaded and reachable in every mode, and
everything that was changed is handed back when the entry goes away.
"""

from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.overkiz_realtime.const import (
    CONF_SOURCE_ENTITY_ID,
    CONF_SOURCE_HANDLING,
    CONF_TRAVEL_TIME_DOWN,
    CONF_TRAVEL_TIME_UP,
    DOMAIN,
    SOURCE_HANDLING_HIDE,
    SOURCE_HANDLING_KEEP,
    SOURCE_HANDLING_TAKEOVER,
)
from homeassistant.components.cover import CoverEntityFeature
from homeassistant.const import EVENT_CALL_SERVICE, SERVICE_OPEN_COVER
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

SOURCE_PLATFORM = "overkiz"
SOURCE_UNIQUE_ID = "io://1234-5678-9012/11223344"

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


@pytest.fixture(autouse=True)
def follow_registry_renames(hass: HomeAssistant) -> None:
    """Make the fake source entity follow its registry entry.

    A real entity moves its state over when its registry entry is renamed. The
    source here is only a state plus a registry entry, so that move is wired up
    by hand -- without it the old entity_id would stay occupied and nothing
    could take it over.
    """

    @callback
    def _mirror(event: Event) -> None:
        data = event.data
        if data["action"] != "update" or "old_entity_id" not in data:
            return

        old_entity_id = data["old_entity_id"]
        if (state := hass.states.get(old_entity_id)) is None:
            return

        hass.states.async_remove(old_entity_id)
        hass.states.async_set(data["entity_id"], state.state, dict(state.attributes))

    hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _mirror)


@pytest.fixture
def source(hass: HomeAssistant) -> er.RegistryEntry:
    """Register a source cover the way the Overkiz integration would."""
    entry = er.async_get(hass).async_get_or_create(
        "cover",
        SOURCE_PLATFORM,
        SOURCE_UNIQUE_ID,
        suggested_object_id="buro1",
        original_name="Buro 1",
    )
    hass.states.async_set(
        entry.entity_id,
        "open",
        {
            "supported_features": int(POSITIONABLE),
            "device_class": "shutter",
            "friendly_name": "Buro 1",
            "current_position": 60,
        },
    )
    return entry


async def _setup(
    hass: HomeAssistant, source: er.RegistryEntry, handling: str
) -> MockConfigEntry:
    """Set up an entry with the given source handling mode."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Buro 1 Realtime",
        data={CONF_SOURCE_ENTITY_ID: source.entity_id},
        options={
            CONF_TRAVEL_TIME_UP: 20.0,
            CONF_TRAVEL_TIME_DOWN: 25.0,
            CONF_SOURCE_HANDLING: handling,
        },
        unique_id=source.id,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _realtime_entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str | None:
    """Return the entity_id the realtime entity ended up with."""
    return er.async_get(hass).async_get_entity_id("cover", DOMAIN, entry.entry_id)


def _source_entity(hass: HomeAssistant, source: er.RegistryEntry) -> er.RegistryEntry:
    """Return the source's registry entry, found by its stable registry id."""
    found = er.async_get(hass).entities.get_entry(source.id)
    assert found is not None
    return found


async def test_keep_leaves_the_source_alone(
    hass: HomeAssistant, source: er.RegistryEntry
) -> None:
    """In "keep" mode nothing about the source changes."""
    entry = await _setup(hass, source, SOURCE_HANDLING_KEEP)

    assert _source_entity(hass, source).entity_id == "cover.buro1"
    assert _source_entity(hass, source).hidden_by is None
    assert _realtime_entity_id(hass, entry) == "cover.buro_1_realtime"
    assert hass.states.get("cover.buro1") is not None


async def test_hide_hides_the_source_but_keeps_it_working(
    hass: HomeAssistant, source: er.RegistryEntry
) -> None:
    """Hiding must not take the source out of service."""
    entry = await _setup(hass, source, SOURCE_HANDLING_HIDE)

    hidden = _source_entity(hass, source)
    assert hidden.hidden_by is er.RegistryEntryHider.INTEGRATION
    # Same entity_id, still in the state machine, still has its state: hiding
    # is a user interface concern, nothing more.
    assert hidden.entity_id == "cover.buro1"
    assert hass.states.get("cover.buro1").attributes["current_position"] == 60
    assert _realtime_entity_id(hass, entry) == "cover.buro_1_realtime"


async def test_hide_does_not_claim_a_source_the_user_hid(
    hass: HomeAssistant, source: er.RegistryEntry
) -> None:
    """A source the user hid stays hidden by the user, and stays hidden."""
    registry = er.async_get(hass)
    registry.async_update_entity(source.entity_id, hidden_by=er.RegistryEntryHider.USER)

    entry = await _setup(hass, source, SOURCE_HANDLING_HIDE)
    assert _source_entity(hass, source).hidden_by is er.RegistryEntryHider.USER

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    # Removing our entry must not undo the user's own choice.
    assert _source_entity(hass, source).hidden_by is er.RegistryEntryHider.USER


async def test_takeover_swaps_the_entity_ids(
    hass: HomeAssistant, source: er.RegistryEntry
) -> None:
    """The realtime entity ends up on the id the dashboards already use."""
    entry = await _setup(hass, source, SOURCE_HANDLING_TAKEOVER)

    assert _realtime_entity_id(hass, entry) == "cover.buro1"
    assert _source_entity(hass, source).entity_id == "cover.buro1_overkiz"
    assert _source_entity(hass, source).hidden_by is er.RegistryEntryHider.INTEGRATION

    # Both entities are live under their new ids.
    assert hass.states.get("cover.buro1") is not None
    assert hass.states.get("cover.buro1_overkiz") is not None


async def test_takeover_survives_a_reload(
    hass: HomeAssistant, source: er.RegistryEntry
) -> None:
    """A reload must not shuffle the ids around a second time."""
    entry = await _setup(hass, source, SOURCE_HANDLING_TAKEOVER)

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert _realtime_entity_id(hass, entry) == "cover.buro1"
    assert _source_entity(hass, source).entity_id == "cover.buro1_overkiz"


async def test_takeover_still_forwards_commands(
    hass: HomeAssistant, source: er.RegistryEntry
) -> None:
    """Commands to the taken-over id reach the renamed source entity."""
    await _setup(hass, source, SOURCE_HANDLING_TAKEOVER)

    forwarded: list[dict[str, Any]] = []

    @callback
    def _record(event: Event) -> None:
        data = event.data
        if data.get("domain") == "cover" and data.get("service") == SERVICE_OPEN_COVER:
            forwarded.append(dict(data.get("service_data") or {}))

    hass.bus.async_listen(EVENT_CALL_SERVICE, _record)

    # What a dashboard or an old automation does: address the original id,
    # which the realtime entity now owns.
    await hass.services.async_call(
        "cover", SERVICE_OPEN_COVER, {"entity_id": "cover.buro1"}, blocking=True
    )
    await hass.async_block_till_done()

    # The call the realtime entity made to the renamed source entity.
    assert {"entity_id": "cover.buro1_overkiz"} in forwarded


async def test_removing_the_entry_hands_the_source_back(
    hass: HomeAssistant, source: er.RegistryEntry
) -> None:
    """Removing the entry restores the entity_id and the visibility."""
    entry = await _setup(hass, source, SOURCE_HANDLING_TAKEOVER)
    assert _source_entity(hass, source).entity_id == "cover.buro1_overkiz"

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    restored = _source_entity(hass, source)
    assert restored.entity_id == "cover.buro1"
    assert restored.hidden_by is None


async def test_switching_back_to_keep_restores_the_source(
    hass: HomeAssistant, source: er.RegistryEntry
) -> None:
    """Changing the option back undoes the takeover on the next setup."""
    entry = await _setup(hass, source, SOURCE_HANDLING_TAKEOVER)
    assert _source_entity(hass, source).entity_id == "cover.buro1_overkiz"

    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_SOURCE_HANDLING: SOURCE_HANDLING_KEEP}
    )
    await hass.async_block_till_done()

    restored = _source_entity(hass, source)
    assert restored.entity_id == "cover.buro1"
    assert restored.hidden_by is None


async def test_source_renamed_by_the_user_is_still_found(
    hass: HomeAssistant, source: er.RegistryEntry
) -> None:
    """The source is tracked by registry id, not by the configured entity_id."""
    er.async_get(hass).async_update_entity(
        source.entity_id, new_entity_id="cover.office_shutter"
    )
    await hass.async_block_till_done()

    entry = await _setup(hass, source, SOURCE_HANDLING_HIDE)

    # The entry still carries the old entity_id in its data, but the hidden
    # entity is the renamed one.
    assert entry.data[CONF_SOURCE_ENTITY_ID] == "cover.buro1"
    hidden = _source_entity(hass, source)
    assert hidden.entity_id == "cover.office_shutter"
    assert hidden.hidden_by is er.RegistryEntryHider.INTEGRATION
