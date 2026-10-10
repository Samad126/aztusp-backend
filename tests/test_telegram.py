"""Telegram linking: `/start <code>` connects the chat to the account the code belongs to. No database needed."""

import pytest
import requests

from app.watcher import telegram
from app.watcher.telegram import CONNECTED, NO_CODE, TelegramLinker


class FakeStore:
    def __init__(self, codes):
        self.codes = codes  # code -> user id, each code works once
        self.linked = {}

    def redeem_link_code(self, code):
        return self.codes.pop(code, None)

    def set_telegram_chat(self, user_id, chat_id):
        self.linked[user_id] = chat_id


class FakeResponse:
    def __init__(self, body=None, status_code=200):
        self.body = body
        self.status_code = status_code

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self.body


@pytest.fixture
def replies(monkeypatch):
    sent = []
    monkeypatch.setattr(telegram, "send_telegram", lambda token, chat, text: sent.append((chat, text)))
    return sent


def update(update_id, text, chat_type="private", chat_id=42):
    return {"update_id": update_id, "message": {"text": text, "chat": {"id": chat_id, "type": chat_type}}}


def test_start_with_a_valid_code_connects_the_chat(replies):
    store = FakeStore({"abc": 7})

    TelegramLinker("tok", store).handle_start("42", "/start abc")

    assert store.linked == {7: "42"}
    assert replies == [("42", CONNECTED)]


@pytest.mark.parametrize("text", ["/start nope", "/start"])
def test_unknown_or_missing_code_connects_nothing(replies, text):
    store = FakeStore({"abc": 7})

    TelegramLinker("tok", store).handle_start("42", text)

    assert store.linked == {}
    assert replies == [("42", NO_CODE)]


def test_only_private_start_messages_are_handled(monkeypatch, replies):
    store = FakeStore({"abc": 7})
    body = {
        "ok": True,
        "result": [update(10, "/start abc", chat_type="group"), update(11, "hello"), update(12, "/start abc")],
    }
    monkeypatch.setattr(telegram.requests, "get", lambda url, params, timeout: FakeResponse(body))
    linker = TelegramLinker("tok", store)

    linker.poll_once()

    assert store.linked == {7: "42"}
    assert linker.offset == 13


def test_polling_asks_only_for_updates_after_the_last_one(monkeypatch, replies):
    asked = []

    def fake_get(url, params, timeout):
        asked.append(params)
        return FakeResponse({"ok": True, "result": [update(10, "hi")]})

    monkeypatch.setattr(telegram.requests, "get", fake_get)
    linker = TelegramLinker("tok", FakeStore({}))

    linker.poll_once()
    linker.poll_once()

    assert "offset" not in asked[0]
    assert asked[1]["offset"] == 11


def test_a_network_error_does_not_log_the_token(monkeypatch, caplog):
    def unreachable(url, params, timeout):
        raise requests.ConnectionError(f"Max retries exceeded with url: {url}")

    class Stop(Exception):
        pass

    def stop_after_one_try(seconds):
        raise Stop

    monkeypatch.setattr(telegram.requests, "get", unreachable)
    monkeypatch.setattr(telegram.time, "sleep", stop_after_one_try)

    with pytest.raises(Stop):
        TelegramLinker("123:SECRET", FakeStore({})).run_forever()
    assert "SECRET" not in caplog.text
