from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from jarvis.core.cron import matches, next_run, parse

IST = ZoneInfo("Asia/Kolkata")


def test_daily_seven_fires_next_morning():
    after = datetime(2026, 9, 7, 9, 30, tzinfo=IST)  # a Monday
    assert next_run("0 7 * * *", after) == datetime(2026, 9, 8, 7, 0, tzinfo=IST)


def test_weekday_mornings_skip_the_weekend():
    friday_night = datetime(2026, 9, 11, 22, 0, tzinfo=IST)
    assert next_run("0 8 * * 1-5", friday_night) == datetime(2026, 9, 14, 8, 0, tzinfo=IST)


def test_sunday_is_zero_and_seven():
    assert parse("0 18 * * 7")[4] == {0}
    assert matches("0 18 * * 0", datetime(2026, 9, 13, 18, 0, tzinfo=IST))


def test_steps_lists_and_ranges():
    assert parse("*/15 9-11,14 * * *")[0] == {0, 15, 30, 45}
    assert parse("*/15 9-11,14 * * *")[1] == {9, 10, 11, 14}


def test_strictly_after():
    at = datetime(2026, 9, 7, 7, 0, tzinfo=IST)
    assert next_run("0 7 * * *", at) == datetime(2026, 9, 8, 7, 0, tzinfo=IST)


@pytest.mark.parametrize("bad", ["0 7 * *", "60 7 * * *", "0 25 * * *", "a b c d e", "0 7 * * */0"])
def test_bad_expressions_raise(bad):
    with pytest.raises(ValueError):
        parse(bad)
