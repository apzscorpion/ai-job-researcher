from app.services.resources import decide


def r(cpu=20, other=10, ram=50, temp=None):
    return {"cpu": cpu, "other_cpu": other, "ram": ram, "temp": temp}


def test_pauses_when_hot_or_busy():
    assert decide(r(cpu=90), False, 0)[0]
    assert decide(r(other=70), False, 0)[0]  # other apps need the CPU
    assert decide(r(ram=95), False, 0)[0]
    paused, reason, _ = decide(r(temp=88), False, 0)
    assert paused and "88" in reason
    assert decide(r(), False, 0) == (False, "", 0)


def test_resumes_only_after_two_calm_readings():
    paused, _, calm = decide(r(cpu=70), True, 0)  # between the limits: stays paused, not calm
    assert paused and calm == 0
    paused, _, calm = decide(r(cpu=30), True, calm)
    assert paused and calm == 1
    assert decide(r(cpu=30), True, calm) == (False, "", 0)
