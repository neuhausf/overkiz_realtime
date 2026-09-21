"""Zeitbasierte Positionsberechnung für Storen/Rollladen.

Der Rechner kennt keine Home-Assistant-Interna und ist damit
eigenständig testbar. Positionen folgen der HA-Konvention:
0 = geschlossen, 100 = offen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time

POSITION_CLOSED = 0.0
POSITION_OPEN = 100.0

# Kürzeste sinnvolle Fahrzeit, verhindert Division durch Null
MIN_TRAVEL_TIME = 0.1

# Grenzen, innerhalb derer eine Messung für die Kalibrierung zählt
CALIBRATION_MIN_SAMPLE_DISTANCE = 40.0
CALIBRATION_MIN_TIME = 2.0
CALIBRATION_MAX_TIME = 600.0


class TravelStatus(Enum):
    """Aktuelle Fahrtrichtung."""

    DIRECTION_UP = "up"
    DIRECTION_DOWN = "down"
    STOPPED = "stopped"


def clamp_position(position: float) -> float:
    """Position auf den gültigen Bereich begrenzen."""
    return min(POSITION_OPEN, max(POSITION_CLOSED, position))


@dataclass
class TravelMeasurement:
    """Sammelt die Positionsrückmeldungen des Gateways während einer Fahrt.

    Für die Kalibrierung zählen ausschliesslich Werte, die das Gateway selbst
    gemeldet hat. Da alle Meldungen ungefähr gleich verzögert eintreffen,
    kürzt sich diese Verzögerung bei der Differenzbildung heraus.
    """

    direction: TravelStatus
    samples: list[tuple[float, float]] = field(default_factory=list)
    interrupted: bool = False

    def add(self, timestamp: float, position: float) -> None:
        """Rückmeldung aufnehmen, wenn sie eine neue Position zeigt."""
        if self.samples and self.samples[-1][1] == position:
            return
        self.samples.append((timestamp, position))

    def full_travel_time(self) -> float | None:
        """Auf eine Vollfahrt hochgerechnete Fahrzeit, falls messbar."""
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
    """Rechnet die aktuelle Position aus Richtung und verstrichener Zeit."""

    def __init__(self, travel_time_down: float, travel_time_up: float) -> None:
        """Rechner mit den Fahrzeiten für eine volle Fahrt initialisieren."""
        self.travel_time_down = max(float(travel_time_down), MIN_TRAVEL_TIME)
        self.travel_time_up = max(float(travel_time_up), MIN_TRAVEL_TIME)

        self.travel_direction = TravelStatus.STOPPED
        self.position_known = False

        self._reference_position = POSITION_CLOSED
        self._reference_timestamp = self.now()
        self._target_position = POSITION_CLOSED

    def now(self) -> float:
        """Monotone Zeitbasis; in Tests überschreibbar."""
        return time.monotonic()

    def set_travel_times(self, travel_time_down: float, travel_time_up: float) -> None:
        """Fahrzeiten ändern, ohne eine laufende Fahrt zu verlieren."""
        current = self.current_position()
        self.travel_time_down = max(float(travel_time_down), MIN_TRAVEL_TIME)
        self.travel_time_up = max(float(travel_time_up), MIN_TRAVEL_TIME)
        if self.travel_direction is not TravelStatus.STOPPED:
            self._reference_position = current
            self._reference_timestamp = self.now()

    @property
    def target_position(self) -> float:
        """Zielposition der laufenden oder letzten Fahrt."""
        return self._target_position

    def speed(self, direction: TravelStatus) -> float:
        """Geschwindigkeit in Prozent pro Sekunde."""
        if direction is TravelStatus.DIRECTION_UP:
            return (POSITION_OPEN - POSITION_CLOSED) / self.travel_time_up
        return (POSITION_OPEN - POSITION_CLOSED) / self.travel_time_down

    def calculate_travel_time(self, from_position: float, to_position: float) -> float:
        """Fahrzeit zwischen zwei Positionen in Sekunden."""
        distance = to_position - from_position
        if distance == 0:
            return 0.0
        direction = (
            TravelStatus.DIRECTION_UP if distance > 0 else TravelStatus.DIRECTION_DOWN
        )
        return abs(distance) / self.speed(direction)

    def position_at(self, timestamp: float) -> float:
        """Position zu einem bestimmten Zeitpunkt."""
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
        """Aktuell berechnete Position."""
        return self.position_at(self.now())

    def set_position(self, position: float) -> None:
        """Bestätigte Position übernehmen und Fahrt beenden."""
        self._reference_position = clamp_position(float(position))
        self._target_position = self._reference_position
        self._reference_timestamp = self.now()
        self.travel_direction = TravelStatus.STOPPED
        self.position_known = True

    def update_position(self, position: float) -> None:
        """Position während der Fahrt korrigieren, Richtung beibehalten."""
        if self.travel_direction is TravelStatus.STOPPED:
            self.set_position(position)
            return

        self._reference_position = clamp_position(float(position))
        self._reference_timestamp = self.now()
        self.position_known = True

        # Ziel bereits überfahren: Fahrt als beendet betrachten
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
        """Fahrt zu einer Zielposition beginnen."""
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
        """Fahrt nach oben (öffnen) beginnen."""
        self.start_travel(POSITION_OPEN, start_time)

    def start_travel_down(self, start_time: float | None = None) -> None:
        """Fahrt nach unten (schliessen) beginnen."""
        self.start_travel(POSITION_CLOSED, start_time)

    def stop(self) -> None:
        """Fahrt an der aktuell berechneten Position anhalten."""
        self._reference_position = self.current_position()
        self._target_position = self._reference_position
        self._reference_timestamp = self.now()
        self.travel_direction = TravelStatus.STOPPED

    def is_traveling(self) -> bool:
        """True, solange die Store fährt."""
        return self.travel_direction is not TravelStatus.STOPPED

    def is_opening(self) -> bool:
        """True, wenn nach oben gefahren wird."""
        return self.travel_direction is TravelStatus.DIRECTION_UP

    def is_closing(self) -> bool:
        """True, wenn nach unten gefahren wird."""
        return self.travel_direction is TravelStatus.DIRECTION_DOWN

    def position_reached(self) -> bool:
        """True, wenn die Zielposition erreicht ist."""
        if self.travel_direction is TravelStatus.STOPPED:
            return True
        return self.current_position() == self._target_position

    def travel_time_remaining(self) -> float:
        """Verbleibende Fahrzeit in Sekunden."""
        if self.travel_direction is TravelStatus.STOPPED:
            return 0.0
        return abs(self._target_position - self.current_position()) / self.speed(
            self.travel_direction
        )

    def is_closed(self) -> bool:
        """True, wenn vollständig geschlossen."""
        return self.current_position() <= POSITION_CLOSED

    def is_open(self) -> bool:
        """True, wenn vollständig offen."""
        return self.current_position() >= POSITION_OPEN
