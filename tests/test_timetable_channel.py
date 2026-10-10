import asyncio
from types import SimpleNamespace

import pytest
from telethon.crypto import AuthKey
from telethon.sessions import StringSession

from app.timetable import channel
from app.timetable.config import TimetableSource
from app.timetable.pdf import Lessons


def make_session() -> str:
    """A session string that Telethon accepts (the account behind it is not real)."""
    session = StringSession()
    session.set_dc(2, "149.154.167.51", 443)
    session.auth_key = AuthKey(bytes(range(256)))
    return session.save()


SESSION = make_session()
SOURCE = TimetableSource(channel="aztu_timetable", api_id=1, api_hash="hash", session=SESSION, search_limit=5)


def pdf_post(post_id: int, group: str | None, *, mime: str = "application/pdf", name: str = "timetable.pdf"):
    """A channel post with a document. Its bytes stand in for the PDF: the group named in them is the only content."""
    data = None if group is None else group.encode()
    return SimpleNamespace(id=post_id, file=SimpleNamespace(mime_type=mime, name=name), data=data)


class FakeClient:
    """The Telethon calls the search uses. Posts are given newest first, as Telegram returns them."""

    def __init__(self, posts, dialogs=()):
        self.posts = posts
        self.dialogs = list(dialogs)  # ids of the chats the account is in
        self.downloads: list[int] = []
        self.read_from: list = []

    async def iter_dialogs(self):
        for dialog_id in self.dialogs:
            yield SimpleNamespace(id=dialog_id)

    async def iter_messages(self, entity, limit, filter):
        self.read_from.append(entity)
        for post in self.posts[:limit]:
            yield post

    async def download_media(self, post, file):
        self.downloads.append(post.id)
        return post.data


class FakePdf:
    """Stands in for a parsed PDF: its bytes are the one group code it holds, and b"bad" cannot be read."""

    def __init__(self, data: bytes):
        if data == b"bad":
            raise ValueError("not a PDF")
        self.code = data.decode()

    def has_group(self, group: str) -> bool:
        return group == self.code

    def lessons(self, group: str):
        return Lessons(sessions=[{"day": "Bazar ertesi", "course": group}], grid={"times": [], "days": []})


@pytest.fixture(autouse=True)
def fake_pdf(monkeypatch):
    channel._pdfs_by_post.clear()  # the cache is module-wide; each test starts empty
    monkeypatch.setattr(channel, "TimetablePdf", FakePdf)
    yield
    channel._pdfs_by_post.clear()


def search(client, groups, source=SOURCE):
    return asyncio.run(channel.search_channel(client, source, groups))


def test_each_group_comes_from_the_newest_pdf_that_has_it():
    client = FakeClient(
        [
            pdf_post(30, "M2"),
            pdf_post(29, None, mime="image/jpeg", name="photo.jpg"),
            pdf_post(28, "M1"),
            pdf_post(27, "M1"),
        ]
    )

    found = search(client, ["M1", "M2"])

    assert [(hit.group, hit.post_id) for hit in found] == [("M2", 30), ("M1", 28)]
    assert client.downloads == [30, 28]  # the image is never downloaded, and post 27 is not needed


def test_search_stops_after_the_search_limit():
    client = FakeClient([pdf_post(3, "M2"), pdf_post(2, "M2"), pdf_post(1, "M1")])
    limited = TimetableSource(channel="aztu_timetable", api_id=1, api_hash="h", session=SESSION, search_limit=2)

    assert search(client, ["M1"], limited) == []
    assert client.downloads == [3, 2]


def test_a_post_is_downloaded_once_across_searches():
    posts = [pdf_post(5, "M1")]
    first, second = FakeClient(posts), FakeClient(posts)

    search(first, ["M1"])
    search(second, ["M1"])

    assert first.downloads == [5]
    assert second.downloads == []


def test_a_pdf_that_cannot_be_read_is_skipped():
    client = FakeClient([pdf_post(2, "bad"), pdf_post(1, "M1")])

    assert [(hit.group, hit.post_id) for hit in search(client, ["M1"])] == [("M1", 1)]


def test_a_document_that_is_not_a_pdf_is_ignored_even_if_it_has_the_group_name():
    client = FakeClient([pdf_post(2, "M1", mime="application/zip", name="archive.zip")])

    assert search(client, ["M1"]) == []
    assert client.downloads == []


def test_pdf_is_recognised_by_file_name_when_the_mime_type_is_generic():
    client = FakeClient([pdf_post(2, "M1", mime="application/octet-stream", name="Dərs cədvəli.PDF")])

    assert [hit.group for hit in search(client, ["M1"])] == ["M1"]


def test_unsigned_session_is_reported(monkeypatch):
    class UnsignedClient:
        def __init__(self, *args, **kwargs):
            pass

        async def connect(self):
            pass

        async def is_user_authorized(self):
            return False

        async def disconnect(self):
            pass

    monkeypatch.setattr(channel, "TelegramClient", UnsignedClient)

    with pytest.raises(channel.TimetableSourceError, match="not signed in"):
        channel.search(SOURCE, ["M1"])


PRIVATE_ID = -1004368645921


def test_private_channel_is_read_by_its_id_once_the_account_is_in_it():
    client = FakeClient([pdf_post(4, "M1")], dialogs=[111, PRIVATE_ID])
    private = TimetableSource(channel=str(PRIVATE_ID), api_id=1, api_hash="h", session=SESSION)

    found = search(client, ["M1"], private)

    assert [(hit.group, hit.post_id) for hit in found] == [("M1", 4)]
    assert client.read_from == [PRIVATE_ID]


def test_private_channel_the_account_is_not_in_is_an_error():
    client = FakeClient([pdf_post(4, "M1")], dialogs=[111])
    private = TimetableSource(channel=str(PRIVATE_ID), api_id=1, api_hash="h", session=SESSION)

    with pytest.raises(channel.TimetableSourceError, match="not a member"):
        search(client, ["M1"], private)
