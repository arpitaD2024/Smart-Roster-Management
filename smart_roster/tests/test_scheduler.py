from datetime import date
from smart_roster.backend.scheduler import _workday

class Rule:
    calendar = "MON_SAT"
    work_days = "0,1,2,3,4,5"

def test_mon_sat_calendar():
    assert _workday(Rule(), date(2026, 11, 23))
    assert not _workday(Rule(), date(2026, 11, 22))

def test_custom_calendar():
    r = Rule()
    r.calendar = "CUSTOM"
    r.work_days = "0,2,4"
    assert _workday(r, date(2026, 11, 23))
    assert not _workday(r, date(2026, 11, 24))
