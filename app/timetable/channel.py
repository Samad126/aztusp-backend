"""Search the timetable channel's newest PDFs for each of a student's groups.

Read through Telethon as a user account, since a bot cannot list a channel's older posts. Each PDF is downloaded and
indexed once; the index is kept for a few posts, because a post does not change once it is sent.
"""

import asyncio
import logging
from dataclasses import dataclass

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import InputMessagesFilterDocument

from .config import TimetableSource
from .pdf import Lessons, TimetablePdf

log = logging.getLogger(__name__)

CACHE_SIZE = 8
_pdfs_by_post: dict[tuple[str, int], TimetablePdf] = {}


class TimetableSourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class FoundGroup:
    group: str
    lessons: Lessons  # both weeks, merged
    post_id: int  # the channel post the timetable came from


def search(source: TimetableSource, groups: list[str]) -> list[FoundGroup]:
    """Blocking. Returns the groups that were found; a group with no timetable in the newest PDFs is left out."""
    return asyncio.run(_connect_and_search(source, groups))


async def _connect_and_search(source: TimetableSource, groups: list[str]) -> list[FoundGroup]:
    client = TelegramClient(StringSession(source.session), source.api_id, source.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise TimetableSourceError("The timetable Telegram session is not signed in; run python -m app.timetable.login")
        return await search_channel(client, source, groups)
    finally:
        await client.disconnect()


async def search_channel(client, source: TimetableSource, groups: list[str]) -> list[FoundGroup]:
    """Go through the channel's PDFs from the newest, stopping when every group is found or the limit is used up."""
    waiting = list(groups)
    found: list[FoundGroup] = []
    if isinstance(source.peer, int):
        await _ensure_member(client, source.peer)
    async for post in client.iter_messages(source.peer, limit=source.search_limit, filter=InputMessagesFilterDocument):
        if not _is_pdf(post):
            continue
        pdf = await _pdf_of(client, source.channel, post)
        if pdf is None:
            continue
        for group in list(waiting):
            if not pdf.has_group(group):
                continue
            try:
                lessons = pdf.lessons(group)
            except Exception:
                log.exception("Could not read group %s from the timetable in post %s", group, post.id)
                continue
            if lessons is not None:
                found.append(FoundGroup(group=group, lessons=lessons, post_id=post.id))
                waiting.remove(group)
        if not waiting:
            break
    return found


async def _ensure_member(client, peer: int) -> None:
    """A private channel can be read only after the account's dialogs are loaded, which gives Telethon its access hash."""
    async for dialog in client.iter_dialogs():
        if dialog.id == peer:
            return
    raise TimetableSourceError("The timetable Telegram account is not a member of the timetable channel")


async def _pdf_of(client, channel: str, post) -> TimetablePdf | None:
    key = (channel.lower(), post.id)
    pdf = _pdfs_by_post.get(key)
    if pdf is not None:
        return pdf

    data = await client.download_media(post, file=bytes)
    if not data:
        return None
    try:
        pdf = TimetablePdf(data)
    except Exception:
        # One broken post should not hide the timetables in the others.
        log.exception("Could not read the timetable PDF in post %s", post.id)
        return None

    if len(_pdfs_by_post) >= CACHE_SIZE:
        _pdfs_by_post.pop(next(iter(_pdfs_by_post)), None)
    _pdfs_by_post[key] = pdf
    return pdf


def _is_pdf(post) -> bool:
    file = post.file
    return file is not None and (file.mime_type == "application/pdf" or (file.name or "").lower().endswith(".pdf"))
