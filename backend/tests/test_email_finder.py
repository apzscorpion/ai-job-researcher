from app.services.email_finder import company_name, domain_matches, pick_email


def test_company_name_drops_suffixes():
    assert company_name("Horizontal Digital Pvt Ltd") == "horizontal"
    assert company_name("VT Group (VTG)") == "vt"


def test_domain_must_carry_company_name():
    assert domain_matches("www.horizontaldigital.com", "Horizontal Digital")
    assert domain_matches("acme-labs.io", "Acme Labs Inc")
    assert not domain_matches("horizontal.com", "Proxify")
    assert domain_matches("fuseenergygroup.com", "Fuse Energy")
    assert not domain_matches("objectways.com", "Objectway")  # a different company
    assert not domain_matches("finixcorporate.com", "Finix")
    assert not domain_matches("healthecareers.com", "X-on Health")  # one shared word isn't the company


def test_pick_email_prefers_careers_inbox_on_company_domain():
    found = ["info@acme.com", "privacy@acme.com", "jobs@acme.com", "hr@gmail.com", "careers@other.com"]
    assert pick_email(found, "acme.com") == "jobs@acme.com"
    assert pick_email(["info@acme.com", "privacy@acme.com"], "www.acme.com") == "info@acme.com"
    assert pick_email(["talent@eu.acme.com"], "acme.com") == "talent@eu.acme.com"
    assert pick_email(["noreply@acme.com", "x@other.com"], "acme.com") is None
    assert pick_email(["sales@acme.com", "john.doe@acme.com"], "acme.com") is None
