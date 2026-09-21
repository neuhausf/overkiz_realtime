"""Tests for the time based position calculation."""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(
    0,
    str(
        pathlib.Path(__file__).resolve().parents[1]
        / "custom_components"
        / "overkiz_realtime"
    ),
)

from travel_calculator import (
    POSITION_CLOSED,
    POSITION_OPEN,
    TravelCalculator,
    TravelMeasurement,
    TravelStatus,
    clamp_position,
)


class FakeClock(TravelCalculator):
    """Calculator with a controllable clock."""

    def __init__(self, travel_time_down: float, travel_time_up: float) -> None:
        self.fake_time = 1000.0
        super().__init__(travel_time_down, travel_time_up)

    def now(self) -> float:
        return self.fake_time

    def tick(self, seconds: float) -> None:
        self.fake_time += seconds


@pytest.fixture
def calc() -> FakeClock:
    """A cover with a 20 s opening run and a 25 s closing run."""
    calculator = FakeClock(travel_time_down=25.0, travel_time_up=20.0)
    calculator.set_position(POSITION_CLOSED)
    return calculator


def test_clamp() -> None:
    assert clamp_position(-5) == 0
    assert clamp_position(105) == 100
    assert clamp_position(42) == 42


def test_full_travel_up(calc: FakeClock) -> None:
    calc.start_travel_up()
    assert calc.travel_direction is TravelStatus.DIRECTION_UP

    calc.tick(10)
    assert calc.current_position() == pytest.approx(50.0)
    assert calc.travel_time_remaining() == pytest.approx(10.0)

    calc.tick(10)
    assert calc.current_position() == pytest.approx(100.0)
    assert calc.position_reached()


def test_travel_does_not_overshoot(calc: FakeClock) -> None:
    calc.start_travel_up()
    calc.tick(120)
    assert calc.current_position() == POSITION_OPEN


def test_asymmetric_travel_times(calc: FakeClock) -> None:
    calc.set_position(POSITION_OPEN)
    calc.start_travel_down()

    calc.tick(12.5)
    assert calc.current_position() == pytest.approx(50.0)


def test_partial_target(calc: FakeClock) -> None:
    calc.start_travel(30.0)
    calc.tick(6.0)
    assert calc.current_position() == pytest.approx(30.0)
    assert calc.position_reached()
    calc.tick(30.0)
    assert calc.current_position() == pytest.approx(30.0)


def test_stop_freezes_position(calc: FakeClock) -> None:
    calc.start_travel_up()
    calc.tick(5)
    calc.stop()

    assert calc.current_position() == pytest.approx(25.0)
    assert not calc.is_traveling()

    calc.tick(60)
    assert calc.current_position() == pytest.approx(25.0)


def test_direction_change_mid_travel(calc: FakeClock) -> None:
    calc.start_travel_up()
    calc.tick(10)
    assert calc.current_position() == pytest.approx(50.0)

    calc.start_travel_down()
    assert calc.travel_direction is TravelStatus.DIRECTION_DOWN

    calc.tick(12.5)
    assert calc.current_position() == pytest.approx(0.0)


def test_update_position_keeps_direction(calc: FakeClock) -> None:
    calc.start_travel_up()
    calc.tick(10)

    # The gateway reports 35 % instead of the calculated 50 %
    calc.update_position(35.0)
    assert calc.travel_direction is TravelStatus.DIRECTION_UP
    assert calc.current_position() == pytest.approx(35.0)

    calc.tick(5)
    assert calc.current_position() == pytest.approx(60.0)


def test_update_position_beyond_target_stops(calc: FakeClock) -> None:
    calc.start_travel(40.0)
    calc.tick(2)
    calc.update_position(45.0)

    assert not calc.is_traveling()
    assert calc.current_position() == pytest.approx(45.0)


def test_set_travel_times_keeps_current_position(calc: FakeClock) -> None:
    calc.start_travel_up()
    calc.tick(10)
    assert calc.current_position() == pytest.approx(50.0)

    calc.set_travel_times(travel_time_down=25.0, travel_time_up=40.0)
    assert calc.current_position() == pytest.approx(50.0)

    calc.tick(10)
    assert calc.current_position() == pytest.approx(75.0)


def test_is_closed_and_open(calc: FakeClock) -> None:
    assert calc.is_closed()
    calc.set_position(POSITION_OPEN)
    assert calc.is_open()


def test_position_known_flag() -> None:
    calculator = FakeClock(25.0, 20.0)
    assert not calculator.position_known
    calculator.set_position(10)
    assert calculator.position_known


def test_measurement_scales_to_full_travel() -> None:
    measurement = TravelMeasurement(direction=TravelStatus.DIRECTION_UP)
    measurement.add(0.0, 10.0)
    measurement.add(12.0, 70.0)

    # 60 % in 12 s -> 20 s for 100 %
    assert measurement.full_travel_time() == pytest.approx(20.0)


def test_measurement_ignores_repeated_positions() -> None:
    measurement = TravelMeasurement(direction=TravelStatus.DIRECTION_DOWN)
    measurement.add(0.0, 90.0)
    measurement.add(3.0, 90.0)
    measurement.add(10.0, 20.0)

    assert len(measurement.samples) == 2
    assert measurement.full_travel_time() == pytest.approx(10.0 * 100 / 70)


def test_measurement_requires_two_samples() -> None:
    measurement = TravelMeasurement(direction=TravelStatus.DIRECTION_UP)
    measurement.add(0.0, 10.0)
    assert measurement.full_travel_time() is None


def test_measurement_rejects_short_distance() -> None:
    measurement = TravelMeasurement(direction=TravelStatus.DIRECTION_UP)
    measurement.add(0.0, 10.0)
    measurement.add(5.0, 30.0)
    assert measurement.full_travel_time() is None


def test_measurement_rejects_wrong_direction() -> None:
    measurement = TravelMeasurement(direction=TravelStatus.DIRECTION_UP)
    measurement.add(0.0, 90.0)
    measurement.add(10.0, 20.0)
    assert measurement.full_travel_time() is None


def test_measurement_rejects_interrupted() -> None:
    measurement = TravelMeasurement(direction=TravelStatus.DIRECTION_UP)
    measurement.add(0.0, 10.0)
    measurement.add(12.0, 70.0)
    measurement.interrupted = True
    assert measurement.full_travel_time() is None


def test_measurement_rejects_implausible_duration() -> None:
    measurement = TravelMeasurement(direction=TravelStatus.DIRECTION_UP)
    measurement.add(0.0, 10.0)
    measurement.add(1.0, 70.0)
    assert measurement.full_travel_time() is None
