from types import SimpleNamespace as NS

from app.routers.applications import best_message_link, message_application

REPLY = """Hi Asif, thanks for your email. Please don't send CVs to this address.
Apply through our careers page: https://boards.greenhouse.io/acme/jobs/123. Follow us on https://twitter.com/acme.
-- Acme Corp, https://acme.com"""


def test_picks_the_application_link():
    assert best_message_link(REPLY) == "https://boards.greenhouse.io/acme/jobs/123"
    assert best_message_link("see https://acme.com/about or https://acme.com/careers.") == "https://acme.com/careers"
    assert best_message_link("no link here") is None
    assert best_message_link("http://127.0.0.1:8000/admin") is None  # never the server's own network


def test_matches_the_emailed_application():
    site = (NS(method="site", sent_to=None), NS(company="Acme Corp"))
    emailed = (NS(method="email", sent_to="hr@acme.com"), NS(company="Acme Corp"))
    other = (NS(method="email", sent_to="jobs@globex.com"), NS(company="Globex"))
    assert message_application(REPLY, [site, other, emailed]) == emailed  # the emailed one first
    assert message_application("from hr@acme.com: apply online", [other, emailed]) == emailed
    assert message_application("unrelated text", [other, emailed]) is None
