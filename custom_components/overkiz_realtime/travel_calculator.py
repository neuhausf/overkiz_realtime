"""Time based position calculation for covers and roller shutters.

The calculator knows nothing about Home Assistant internals and is therefore
testable on its own. Positions follow the HA convention:
0 = closed, 100 = open.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time

POSITION_CLOSED = 0.0
POSITION_OPEN = 100.0

# Shortest sensible travel time, guards against division by zero
MIN_TRAVEL_TIME = 0.1

# Bounds within which a measurement counts towards the calibration
CALIBRATION_MIN_SAMPLE_DISTANCE = 40.0
CALIBRATION_MIN_TIME = 2.0
CALIBRATION_MAX_TIME = 600.0


class TravelStatus(Enum):
    """Current travel direction."""

    DIRECTION_UP = "up"
    DIRECTION_DOWN = "down"
    STOPPED = "stopped"


def clamp_position(position: float) -> float:
    """Clamp a position into the valid range."""
    return min(POSITION_OPEN, max(POSITION_CLOSED, position))


@dataclass
class TravelMeasurement:
    """Collects the gateway's position reports during a run.

    Only values the gateway reported itself count towards the calibration.
    Because every report arrives with roughly the same delay, that delay
    cancels out when the difference between two of them is taken.
    """

    direction: TravelStatus
    samples: list[tuple[float, float]] = field(default_factory=list)
    interrupted: bool = False

    def add(self, timestamp: float, position: float) -> None:
        """Record a report if it shows a new position."""
        if self.samples and self.samples[-1][1] == position:
            return
        self.samples.append((timestamp, position))

    def full_travel_time(self) -> float | None:
        """Travel time extrapolated to a full run, if measurable."""
        if self.interrupted or len(self.samples) < 2:
            return None

        first_time, first_position = self.samples[0]
        last_time, last_position = self.samples[-1]

        travelled = last_position - first_position
        elapsed = last_time - first_time

        expected_sign = 1.0 if self.direction is TravelStatus.DIRECTION_UP else -1.0
        if travelled * expected_sign <= 0:
            return None
        if abs(travelled) < CALIBRATION_MIN_SAMPLE_DISTANCE:
            return None
        if not CALIBRATION_MIN_TIME <= elapsed <= CALIBRATION_MAX_TIME:
            return None

        return elapsed * POSITION_OPEN / abs(travelled)


class TravelCalculator:
    """Derives the current position from direction and elapsed time."""

    def __init__(self, travel_time_down: float, travel_time_up: float) -> None:
        """Initialise the calculator with the travel times of a full run."""
        self.travel_time_down = max(float(travel_time_down), MIN_TRAVEL_TIME)
        self.travel_time_up = max(float(travel_time_up), MIN_TRAVEL_TIME)

        self.travel_direction = TravelStatus.STOPPED
        self.position_known = False

        self._reference_position = POSITION_CLOSED
        self._reference_timestamp = self.now()
        self._target_position = POSITION_CLOSED

    def now(self) -> float:
        """Monotonic time base; overridable in tests."""
        return time.monotonic()

    def set_travel_times(self, travel_time_down: float, travel_time_up: float) -> None:
        """Change the travel times without losing a run in progress."""
        current = self.current_position()
        self.travel_time_down = max(float(travel_time_down), MIN_TRAVEL_TIME)
        self.travel_time_up = max(float(travel_time_up), MIN_TRAVEL_TIME)
        if self.travel_direction is not TravelStatus.STOPPED:
            self._reference_position = current
            self._reference_timestamp = self.now()

    @property
    def target_position(self) -> float:
        """Target position of the current or most recent run."""
        return self._target_position

    def speed(self, direction: TravelStatus) -> float:
        """Speed in percent per second."""
        if direction is TravelStatus.DIRECTION_UP:
            return (POSITION_OPEN - POSITION_CLOSED) / self.travel_time_up
        return (POSITION_OPEN - POSITION_CLOSED) / self.travel_time_down

    def calculate_travel_time(self, from_position: float, to_position: float) -> float:
        """Travel time between two positions, in seconds."""
        distance = to_position - from_position
        if distance == 0:
            return 0.0
        direction = (
            TravelStatus.DIRECTION_UP if distance > 0 else TravelStatus.DIRECTION_DOWN
        )
        return abs(distance) / self.speed(direction)

    def position_at(self, timestamp: float) -> float:
        """Position at a given point in time."""
        if self.travel_direction is TravelStatus.STOPPED:
            return self._reference_position

        elapsed = timestamp - self._reference_timestamp
        if elapsed <= 0:
            return self._reference_position

        delta = self.speed(self.travel_direction) * elapsed
        if self.travel_direction is TravelStatus.DIRECTION_UP:
            return min(self._reference_position + delta, self._target_position)
        return max(self._reference_position - delta, self._target_position)

    def current_position(self) -> float:
        """Currently calculated position."""
        return self.position_at(self.now())

    def set_position(self, position: float) -> None:
        """Adopt a confirmed position and end the run."""
        self._reference_position = clamp_position(float(position))
        self._target_position = self._reference_position
        self._reference_timestamp = self.now()
        self.travel_direction = TravelStatus.STOPPED
        self.position_known = True

    def update_position(self, position: float) -> None:
        """Correct the position mid-run, keeping the direction."""
        if self.travel_direction is TravelStatus.STOPPED:
            self.set_position(position)
            return

        self._reference_position = clamp_position(float(position))
        self._reference_timestamp = self.now()
        self.position_known = True

        # Already past the target: treat the run as finished
        if (
            self.travel_direction is TravelStatus.DIRECTION_UP
            and self._reference_position >= self._target_position
        ) or (
            self.travel_direction is TravelStatus.DIRECTION_DOWN
            and self._reference_position <= self._target_position
        ):
            self.stop()

    def start_travel(
        self, target_position: float, start_time: float | None = None
    ) -> None:
        """Begin a run towards a target position."""
        timestamp = self.now() if start_time is None else start_time
        self._reference_position = self.position_at(timestamp)
        self._reference_timestamp = timestamp
        self._target_position = clamp_position(float(target_position))

        if self._target_position > self._reference_position:
            self.travel_direction = TravelStatus.DIRECTION_UP
        elif self._target_position < self._reference_position:
            self.travel_direction = TravelStatus.DIRECTION_DOWN
        else:
            self.travel_direction = TravelStatus.STOPPED

    def start_travel_up(self, start_time: float | None = None) -> None:
        """Begin an upward (opening) run."""
        self.start_travel(POSITION_OPEN, start_time)

    def start_travel_down(self, start_time: float | None = None) -> None:
        """Begin a downward (closing) run."""
        self.start_travel(POSITION_CLOSED, start_time)

    def stop(self) -> None:
        """Stop the run at the currently calculated position."""
        self._reference_position = self.current_position()
        self._target_position = self._reference_position
        self._reference_timestamp = self.now()
        self.travel_direction = TravelStatus.STOPPED

    def is_traveling(self) -> bool:
        """True for as long as the cover is travelling."""
        return self.travel_direction is not TravelStatus.STOPPED

    def is_opening(self) -> bool:
        """True when travelling upwards."""
        return self.travel_direction is TravelStatus.DIRECTION_UP

    def is_closing(self) -> bool:
        """True when travelling downwards."""
        return self.travel_direction is TravelStatus.DIRECTION_DOWN

    def position_reached(self) -> bool:
        """True once the target position is reached."""
        if self.travel_direction is TravelStatus.STOPPED:
            return True
        return self.current_position() == self._target_position

    def travel_time_remaining(self) -> float:
        """Remaining travel time in seconds."""
        if self.travel_direction is TravelStatus.STOPPED:
            return 0.0
        return abs(self._target_position - self.current_position()) / self.speed(
            self.travel_direction
        )

    def is_closed(self) -> bool:
        """True when fully closed."""
        return self.current_position() <= POSITION_CLOSED

    def is_open(self) -> bool:
        """True when fully open."""
        return self.current_position() >= POSITION_OPEN
