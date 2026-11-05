from types import SimpleNamespace as NS

from app.services.regions import job_groups
from app.services.scheduler import prioritize


def job(loc, remote=False, title="Frontend Developer"):
    return NS(location=loc, is_remote=remote, title=title)


PLACES = ["Pathanamthitta", "Thiruvalla"]


def test_groups():
    assert job_groups(job("Thiruvalla, Kerala", remote=True), PLACES) == {"remote_district", "remote_india"}
    assert job_groups(job("Pathanamthitta, Kerala"), PLACES) == {"district"}
    assert job_groups(job("Remote, India"), PLACES) == {"remote_india"}
    assert job_groups(job("Berlin, Germany (Remote)"), PLACES) == {"remote_abroad"}
    assert job_groups(job("Kochi (Hybrid)"), PLACES) == {"hybrid"}
    assert job_groups(job("Indianapolis, Indiana", remote=True), PLACES) == {"remote_abroad"}
    assert job_groups(job("Kochi", title="Hybrid App Developer"), PLACES) == set()


def test_queue_follows_the_users_order():
    jobs = [job("Berlin (Remote)"), job("Kochi (Hybrid)"), job("Remote, India"), job("Mumbai")]
    items = [(i, j) for i, j in enumerate(jobs)]
    order = ["remote_india", "hybrid", "remote_abroad"]
    assert [a for a, _ in prioritize(items, order, PLACES, False)] == [2, 1, 0, 3]
    assert [a for a, _ in prioritize(items, order, PLACES, True)] == [2, 1, 0]  # Mumbai office job dropped
    assert prioritize(items, [], PLACES, True) == items  # nothing ranked: score order, nothing dropped


def test_district_defaults_to_profile_location():
    from app.services.regions import home_places
    assert home_places([], "Pathanamthitta, Kerala") == ["Pathanamthitta"]
    assert home_places(["Thiruvalla"], "Pathanamthitta, Kerala") == ["Thiruvalla"]
    assert home_places([], None) == []
