"""Constants for Overkiz Realtime Position."""

from __future__ import annotations

import logging
from typing import Final

DOMAIN: Final = "overkiz_realtime"
LOGGER: Final = logging.getLogger(__package__)

PLATFORMS: Final = ["cover"]

STORAGE_VERSION: Final = 1

# Configuration
CONF_SOURCE_ENTITY_ID: Final = "source_entity_id"
CONF_TRAVEL_TIME_UP: Final = "travel_time_up"
CONF_TRAVEL_TIME_DOWN: Final = "travel_time_down"
CONF_TILT_ENABLED: Final = "tilt_enabled"
CONF_TILT_TIME_UP: Final = "tilt_time_up"
CONF_TILT_TIME_DOWN: Final = "tilt_time_down"
CONF_TILT_FOLLOWS_POSITION: Final = "tilt_follows_position"
CONF_UPDATE_INTERVAL: Final = "update_interval"
CONF_COMMAND_DELAY: Final = "command_delay"
CONF_RESYNC_THRESHOLD: Final = "resync_threshold"
CONF_AUTO_CALIBRATION: Final = "auto_calibration"
CONF_CALIBRATION_WEIGHT: Final = "calibration_weight"
CONF_TIMED_POSITIONING: Final = "timed_positioning"
CONF_SOURCE_HANDLING: Final = "source_handling"

# How the original Overkiz entity is treated once the realtime entity exists.
# The source entity is never disabled or deleted: this integration reads its
# state and forwards every command to it, so a disabled source would break the
# realtime entity along with it.
SOURCE_HANDLING_KEEP: Final = "keep"
SOURCE_HANDLING_HIDE: Final = "hide"
SOURCE_HANDLING_TAKEOVER: Final = "takeover"
SOURCE_HANDLING_OPTIONS: Final = (
    SOURCE_HANDLING_KEEP,
    SOURCE_HANDLING_HIDE,
    SOURCE_HANDLING_TAKEOVER,
)

# Suffix appended to the source entity_id when the realtime entity takes the
# original one over.
SOURCE_TAKEOVER_SUFFIX: Final = "overkiz"

# Defaults
DEFAULT_TRAVEL_TIME: Final = 25.0
DEFAULT_TILT_TIME: Final = 1.5
DEFAULT_UPDATE_INTERVAL: Final = 0.5
DEFAULT_COMMAND_DELAY: Final = 0.0
DEFAULT_RESYNC_THRESHOLD: Final = 15.0
DEFAULT_AUTO_CALIBRATION: Final = True
DEFAULT_CALIBRATION_WEIGHT: Final = 0.2
DEFAULT_TIMED_POSITIONING: Final = True
DEFAULT_TILT_FOLLOWS_POSITION: Final = True

# Offered when setting up a new entry. Entries created before this option
# existed fall back to SOURCE_HANDLING_KEEP so that an update never hides an
# entity behind the user's back.
DEFAULT_SOURCE_HANDLING: Final = SOURCE_HANDLING_HIDE
FALLBACK_SOURCE_HANDLING: Final = SOURCE_HANDLING_KEEP

# A single measurement may deviate from the current value by at most this
# factor, otherwise it is rejected as an outlier.
CALIBRATION_MAX_DEVIATION: Final = 0.5

# Additional entity attributes
ATTR_SOURCE_ENTITY_ID: Final = "source_entity_id"
ATTR_TRAVEL_TIME_UP: Final = "travel_time_up"
ATTR_TRAVEL_TIME_DOWN: Final = "travel_time_down"
ATTR_TILT_TIME_UP: Final = "tilt_time_up"
ATTR_TILT_TIME_DOWN: Final = "tilt_time_down"
ATTR_POSITION_ESTIMATED: Final = "position_estimated"
ATTR_TARGET_POSITION: Final = "target_position"
ATTR_TRAVEL_TIME_REMAINING: Final = "travel_time_remaining"
ATTR_LAST_CALIBRATION: Final = "last_calibration"
ATTR_CALIBRATION_SAMPLES: Final = "calibration_samples"

# Services
SERVICE_SET_KNOWN_POSITION: Final = "set_known_position"
SERVICE_CALIBRATE: Final = "calibrate"
SERVICE_SET_TRAVEL_TIMES: Final = "set_travel_times"

ATTR_KNOWN_POSITION: Final = "position"
ATTR_KNOWN_TILT_POSITION: Final = "tilt_position"
ATTR_DIRECTION: Final = "direction"

# States of the source entity
STATE_SRC_OPENING: Final = "opening"
STATE_SRC_CLOSING: Final = "closing"
STATE_SRC_OPEN: Final = "open"
STATE_SRC_CLOSED: Final = "closed"

# Storage keys for the learned travel times
STORAGE_TRAVEL_TIME_UP: Final = "learned_travel_time_up"
STORAGE_TRAVEL_TIME_DOWN: Final = "learned_travel_time_down"
STORAGE_TILT_TIME_UP: Final = "learned_tilt_time_up"
STORAGE_TILT_TIME_DOWN: Final = "learned_tilt_time_down"
STORAGE_LAST_CALIBRATION: Final = "last_calibration"
STORAGE_SAMPLES: Final = "calibration_samples"
STORAGE_LAST_POSITION: Final = "last_position"
STORAGE_LAST_TILT_POSITION: Final = "last_tilt_position"

# Storage keys bookkeeping what we changed on the source entity, so that it can
# be restored exactly as it was.
STORAGE_SOURCE_HIDDEN_BY_US: Final = "source_hidden_by_us"
STORAGE_SOURCE_RENAMED_FROM: Final = "source_renamed_from"
STORAGE_SOURCE_RENAMED_TO: Final = "source_renamed_to"
