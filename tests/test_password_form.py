import pytest

from app.scraping import password_form
from app.scraping.client import BadCredentials


class Answer:
    url = "https://d.example.com/telebe/info/student_pwd_ajax.php"

    def __init__(self, text="", status=200):
        self.text, self.status = text, status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(self.status)


class FakeScraper:
    username = "M1"

    class settings:
        dashboard_url = "https://d.example.com/"

    def __init__(self, text=""):
        self.text = text
        self.posts = []

    def post_form(self, url, data, files=None, headers=None):
        self.posts.append((url, data))
        return Answer(self.text)


@pytest.fixture
def signs_in(monkeypatch):
    """Stands in for the sign-in with the new password, so no test reaches the site: the passwords it accepts."""
    accepted, tried = set(), []

    class Fresh:
        def __init__(self, settings, username):
            pass

        def login(self, password):
            tried.append(password)
            if password not in accepted:
                raise BadCredentials("rejected")

    monkeypatch.setattr(password_form, "SiteScraper", Fresh)
    return accepted, tried


def test_the_modals_three_fields_are_sent_and_a_new_sign_in_proves_the_change(signs_in):
    accepted, tried = signs_in
    accepted.add("new-pass")
    scraper = FakeScraper("ok")

    result = password_form.change_password(scraper, "old-pass", "new-pass")

    assert scraper.posts == [
        ("https://d.example.com/telebe/info/student_pwd_ajax.php", {"old_pwd": "old-pass", "new_pwd": "new-pass", "new_pwd_confirm": "new-pass"})
    ]
    assert tried == ["new-pass"]
    assert result == {"changed": True, "url": Answer.url, "messages": []}


def test_a_change_the_site_ignores_is_not_reported_as_done(signs_in):
    result = password_form.change_password(FakeScraper("<p>Parol  səhvdir</p>"), "old-pass", "new-pass")

    assert result == {"changed": False, "url": Answer.url, "messages": ["Parol səhvdir"]}


def test_a_site_failure_is_raised_not_reported_as_a_refusal(signs_in):
    scraper = FakeScraper()
    scraper.post_form = lambda *args, **kwargs: Answer(status=500)

    with pytest.raises(RuntimeError):
        password_form.change_password(scraper, "old-pass", "new-pass")
