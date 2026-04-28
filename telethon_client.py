from aiohttp import ClientSession
from telethon import TelegramClient
from config import API_ID, API_HASH, PHONE, CHECK_INTERVAL, TARGET_CHAT_ID, MAIN_CHAT_ID, PROMT
from state import state
import sqlite3
import asyncio
import html
from aiogram.types import (
    FSInputFile,
    MessageEntity,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument,
    FSInputFile,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from telethon.tl.types import (
    MessageEntityBold,
    MessageEntityItalic,
    MessageEntityCode,
    MessageEntityPre,
    MessageEntityTextUrl,
    MessageEntityUnderline,
    MessageEntityStrike,
    MessageEntityMention,
    MessageEntityHashtag,
    MessageEntityBotCommand,
    MessageEntityCashtag,
    MessageEntityEmail,
    DocumentAttributeSticker,
    MessageEntitySpoiler,
    MessageEntityCustomEmoji
)
import re
import tempfile
import re
import io
import os
import json
import time
from openai import AsyncOpenAI, APIError

import httpx
from openai import AsyncOpenAI
from config import BOT_TOKEN, API_KEY, CHANNELS


telegram_send_lock = asyncio.Lock()
async_client = httpx.AsyncClient()  # без proxies
client_gpt = AsyncOpenAI(api_key=API_KEY, http_client=async_client)
client = TelegramClient("session/user", API_ID, API_HASH)

from aiogram import Bot
from aiogram import Dispatcher, types



bot = Bot(token=BOT_TOKEN)

async def start_client():
    await client.start(phone=PHONE)


def clean_html(text: str) -> str:
    if not text:
        return text

    # заменяем <br> на перенос строки
    text = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")

    return text

async def find_chat(chat_name):
    async for dialog in client.iter_dialogs():
        if (
            dialog.name == chat_name
            or getattr(dialog.entity, "username", None) == chat_name.lstrip("@")
        ):
            return dialog.entity
    return None

def escape_markdown(text: str) -> str:
    # Экранируем все специальные символы MarkdownV2
    return re.sub(r'([_\*$begin:math:display$$end:math:display$$begin:math:text$$end:math:text$~`>#+\-=|{}.!])', r'\\\1', text)

def escape_html(text: str) -> str:
    if not text or text.strip() == "":
        return "Текста нет"
    return html.escape(text)

def get_like_dislike_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="🔁 Текст", callback_data="reload"),
        InlineKeyboardButton(text="🖼 Фото", callback_data="reload_image")
    ],
    [
        InlineKeyboardButton(text="👍", callback_data="like"),
        InlineKeyboardButton(text="👎", callback_data="dislike")
    ]
])
def get_source_chats():
    db = sqlite3.connect("posts.db")
    c = db.cursor()
    c.execute("SELECT chat_id FROM source_chats")
    rows = c.fetchall()
    db.close()
    return [row[0] for row in rows]


def get_source_chats_with_titles():
    db = sqlite3.connect("posts.db")
    c = db.cursor()
    c.execute("SELECT chat_id, title FROM source_chats ORDER BY id")
    rows = c.fetchall()
    db.close()
    return rows


def add_source_chat(chat_id: int, title: str | None = None):
    db = sqlite3.connect("posts.db")
    c = db.cursor()
    c.execute(
        """
        INSERT INTO source_chats (chat_id, title)
        VALUES (?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET title = excluded.title
        """,
        (chat_id, title)
    )
    db.commit()
    db.close()


def delete_source_chat(chat_id: int):
    db = sqlite3.connect("posts.db")
    c = db.cursor()

    c.execute("DELETE FROM source_chats WHERE chat_id = ?", (chat_id,))
    deleted_count = c.rowcount

    db.commit()
    db.close()

    return deleted_count > 0

async def get_chat_title(chat_id: int) -> str | None:
    try:
        entity = await client.get_entity(chat_id)
        return getattr(entity, "title", None) or getattr(entity, "username", None)
    except Exception as e:
        logger.error(f"[{chat_id}] Не удалось получить название: {e}")
        return None

async def msg_add_database(msg):
    db = sqlite3.connect('posts.db')

    c = db.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS posts
          (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            msg_id INTEGER NOT NULL,
            file_id TEXT,
            caption TEXT,
            group_message_id INTEGER,
            entities TEXT,
            type_message TEXT
          )""")
    c.execute("""
            CREATE TABLE IF NOT EXISTS source_chats
            (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL UNIQUE
            )
        """)

    message_id = msg.message_id
    file_id = None
    if msg.sticker:
        file_id = msg.sticker.file_id
        msg_type = "sticker"
    elif msg.photo:
        if type(msg.photo) == list:
            file_id = msg.photo[-1].file_id
        else:
            file_id = msg.photo.file_id
        msg_type = "photo"
    elif msg.video:
        if type(msg.video) == list:
            file_id = msg.video[-1].file_id
        else:
            file_id = msg.video.file_id
        msg_type = "video"
    elif msg.voice:
        file_id = msg.voice.file_id
        msg_type = "voice"
    elif msg.document:
        if type(msg.document) == list:
            file_id = msg.document[-1].file_id
        else:
            file_id = msg.document.file_id
        msg_type = "document"
    else:
        msg_type = "text"
    text = msg.caption or msg.text or ""
    try:
        group_message_id = msg.media_group_id or None
    except:
        group_message_id = None
    
    
    entities = msg.caption_entities or msg.entities
    if entities:
        entities_json = json.dumps([e.model_dump() for e in entities])  
    else:
        entities_json = None  
    print(text)
    print(entities_json)
    c.execute(
        """INSERT INTO posts 
        (msg_id, file_id, caption, group_message_id, entities, type_message) 
        VALUES (?, ?, ?, ?, ?, ?)""",
        (message_id, file_id, text, group_message_id, entities_json, msg_type)
    )
    
    db.commit()
    
    db.close()
    
async def send_album(messages):
    media_group_not_gpt = []
    media_group_gpt = []
    caption = None
    entities = None
    temp_files = []

    for msg in messages:
        if msg.raw_text and not caption:
            caption = msg.raw_text
            entities = msg.entities

        if msg.photo:
            name = "photo.jpg"
            suffix = ".jpg"
        elif msg.video:
            name = "video.mp4"
            suffix = ".mp4"
        elif msg.document:
            name = msg.document.attributes[1].file_name
            suffix = '.' + name.split(".")[-1] if "." in name else ".bin"
        elif msg.voice:
            name = "voice.mp3"
            suffix = ".mp3"
        elif msg.media and msg.media.document:
            attributes = msg.media.document.attributes
            has_sticker = any(
                isinstance(attr, DocumentAttributeSticker)
                for attr in attributes
            )
            if has_sticker:
                name = "sticker.webp"
                suffix = ".webp"
            else:
                name = "file.bin"
                suffix = ".bin"
        else:
            suffix = None
            name = "file.bin"

        if not suffix:
            continue

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp_file:
            file_path = tmp_file.name

        temp_files.append((file_path, name, msg))
        await client.download_media(msg.media, file=file_path)

    caption_not_gpt = None
    caption_gpt = None

    if caption:
        caption_not_gpt = await entities_to_html(caption, entities, flag=True)
        caption_gpt = await entities_to_html(caption, entities, flag=False)

    for idx, (file_path, name, msg) in enumerate(temp_files):
        file_input_1 = FSInputFile(file_path, filename=name if msg.document else None)
        file_input_2 = FSInputFile(file_path, filename=name if msg.document else None)

        if msg.photo:
            media_item_not_gpt = InputMediaPhoto(media=file_input_1)
            media_item_gpt = InputMediaPhoto(media=file_input_2)
        elif msg.video:
            media_item_not_gpt = InputMediaVideo(media=file_input_1)
            media_item_gpt = InputMediaVideo(media=file_input_2)
        else:
            media_item_not_gpt = InputMediaDocument(media=file_input_1)
            media_item_gpt = InputMediaDocument(media=file_input_2)

        if idx == 0 and caption_not_gpt:
            media_item_not_gpt.caption = caption_not_gpt
            media_item_not_gpt.parse_mode = "HTML"

        if idx == 0 and caption_gpt:
            media_item_gpt.caption = caption_gpt
            media_item_gpt.parse_mode = "HTML"

        media_group_not_gpt.append(media_item_not_gpt)
        media_group_gpt.append(media_item_gpt)

    try:
        sent_messages_not_gpt = []
        sent_messages_gpt = []

        if media_group_not_gpt:
            sent_messages_not_gpt = await bot.send_media_group(
                chat_id=TARGET_CHAT_ID,
                media=media_group_not_gpt
            )

            first_msg_id = sent_messages_not_gpt[0].message_id
            await bot.send_message(
                chat_id=TARGET_CHAT_ID,
                text="Выберите действие:",
                reply_markup=get_like_dislike_keyboard(),
                reply_to_message_id=first_msg_id
            )

        await asyncio.sleep(2)
        await bot.send_message(
            chat_id=TARGET_CHAT_ID,
            text="──────────────\n⬆️ *Без нейросети*📝\n⬇️ *С нейросетью*🤖\n──────────────",
            parse_mode="MarkDownV2")
        if media_group_gpt:
            sent_messages_gpt = await bot.send_media_group(
                chat_id=TARGET_CHAT_ID,
                media=media_group_gpt
            )

            first_msg_id = sent_messages_gpt[0].message_id
            await bot.send_message(
                chat_id=TARGET_CHAT_ID,
                text="Выберите действие:",
                reply_markup=get_like_dislike_keyboard(),
                reply_to_message_id=first_msg_id
            )

        for sent_msg in sent_messages_not_gpt:
            await msg_add_database(sent_msg)

        for sent_msg in sent_messages_gpt:
            await msg_add_database(sent_msg)

    finally:
        for file_path, _, _ in temp_files:
            if os.path.exists(file_path):
                os.remove(file_path)
    
def escape_md_v2(text: str) -> str:
    if not text or text.strip() == "":
        return "Текста нет"
    return re.sub(r'([_\*\[\]\(\)~`>#+\-=|{}!.])', r'\\\1', text)
    
def escape_md_v2_for_markdown(text: str) -> str:
    """
    Экранирует спецсимволы MarkdownV2, 
    но оставляет * и _ для жирного и курсива.
    """
    if not text:
        return ""
    
    # Экранируем только символы, которые могут ломать MarkdownV2,
    # кроме *, _, [ ]
    pattern = r'([\\`~>#+\-=|{}.!])'
    
    def esc(match):
        return '\\' + match.group(0)
    
    return re.sub(pattern, esc, text)

def get_text_from_offset(text, offset, length):
    # текст в UTF-16LE
    encoded = text.encode("utf-16-le")
    # каждый символ в UTF-16 = 2 байта
    start_byte = offset * 2
    end_byte = start_byte + length * 2
    return encoded[start_byte:end_byte].decode("utf-16-le")

async def entities_to_html(text: str, entities, flag=False) -> str:
    if not text:
        return ""

    if not entities:
        return escape_html(text)

    result = ""
    last_offset = 0

    for e in sorted(entities, key=lambda x: x.offset):
        if e.offset > last_offset:
            result += escape_html(text[last_offset:e.offset])

        entity_text = text[e.offset:e.offset + e.length]
        safe_text = escape_html(entity_text)

        if isinstance(e, MessageEntityBold):
            result += f"<b>{safe_text}</b>"

        elif isinstance(e, MessageEntityItalic):
            result += f"<i>{safe_text}</i>"

        elif isinstance(e, MessageEntityUnderline):
            result += f"<u>{safe_text}</u>"

        elif isinstance(e, MessageEntityStrike):
            result += f"<s>{safe_text}</s>"

        elif isinstance(e, MessageEntityCode):
            result += f"<code>{safe_text}</code>"

        elif isinstance(e, MessageEntityPre):
            result += f"<pre>{safe_text}</pre>"

        elif isinstance(e, MessageEntityTextUrl):
            result += f'<a href="{e.url}">{safe_text}</a>'

        elif isinstance(e, MessageEntitySpoiler):
            result += f"<tg-spoiler>{safe_text}</tg-spoiler>"

        else:
            result += safe_text

        last_offset = e.offset + e.length

    if last_offset < len(text):
        result += escape_html(text[last_offset:])
        
    # если после обработки текста нет
    if not result or result.strip() == "":
        return "Текста нет"
    if flag == False:
        gpt_result = await edit_text_with_gpt(result)

        if not gpt_result["ok"]:
            return result

        return gpt_result["text"]
    else:
        return result

async def edit_text_with_gpt(result):
    for i in range(1, 5):
        try:
            prompt = PROMT + '\n' + result

            response = await client_gpt.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.9,
                max_tokens=300
            )
            text = response.choices[0].message.content.strip()
            text = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
            return {
                "ok": True,
                "text": text
            }

        except Exception as e:
            error_text = str(e)
            print("Ошибка GPT:", error_text)

            if i == 1:
                # 👉 отправляем ошибку отдельным сообщением
                try:
                    await bot.send_message(
                        chat_id=TARGET_CHAT_ID,
                        text=f"⚠️ Ошибка GPT:\n{error_text}"
                    )
                except:
                    pass

            await asyncio.sleep(5)

    return {
        "ok": False,
        "text": result  # 👉 возвращаем ОРИГИНАЛ
    }

# ===== Универсальная отправка медиа =====
async def send_media(msg):
    original_text = msg.message or ""

    caption_not_gpt = await entities_to_html(original_text, msg.entities, flag=True)
    caption = await entities_to_html(original_text, msg.entities, flag=False)
    grouped_id = getattr(msg, 'grouped_id', None)
    # Определяем тип медиа
    if msg.photo:
        name = "photo.jpg"
        suffix = ".jpg"
    elif msg.video:
        name = "video.mp4"
        suffix = ".mp4"
    elif msg.voice:
        name = "voice.mp3"
        suffix = ".mp3"
    elif msg.media and msg.media.document:
        attributes = msg.media.document.attributes

        has_sticker = any(
            isinstance(attr, DocumentAttributeSticker)
            for attr in attributes
        )
        if has_sticker:
            name = "stick.webp"
            suffix = ".webp"

    elif msg.document:
        # пытаемся угадать расширение документа
        name = msg.document.attributes[1].file_name
        suffix = '.' + name.split(".")[-1] if "." in name else ".bin"
    else:
        suffix = None

    if suffix:
        print(suffix)
        # Сохраняем во временный файл
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp_file:
            file_path = tmp_file.name
        await client.download_media(msg.media, file=file_path)
        # print(msg.media)
        # print(msg.video)
        try:
            file_input = FSInputFile(file_path, filename=name if msg.document else None)
            if msg.photo:
                send_msg_not_gpt = await bot.send_photo(
                    chat_id=TARGET_CHAT_ID,
                    photo=file_input,
                    caption=caption_not_gpt,
                    parse_mode="HTML",
                    reply_markup=get_like_dislike_keyboard()
                )
                await bot.send_message(
                    chat_id=TARGET_CHAT_ID,
                    text="──────────────\n⬆️ *Без нейросети*📝\n⬇️ *С нейросетью*🤖\n──────────────",
                    parse_mode="MarkDownV2")
                send_msg = await bot.send_photo(
                    chat_id=TARGET_CHAT_ID,
                    photo=file_input,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=get_like_dislike_keyboard()
                )
            elif msg.video:

                media_item = InputMediaVideo(media=FSInputFile(file_path), caption=caption_not_gpt, parse_mode="HTML")
                send_msgs_not_gpt = await bot.send_media_group(chat_id=TARGET_CHAT_ID, media=[media_item])
                first_msg_id = send_msgs_not_gpt[0].message_id
                await bot.send_message(
                    chat_id=TARGET_CHAT_ID,
                    text="Выберите действие:",
                    reply_markup=get_like_dislike_keyboard(),
                    reply_to_message_id=first_msg_id
                )
                await bot.send_message(
                    chat_id=TARGET_CHAT_ID,
                    text="──────────────\n⬆️ *Без нейросети*📝\n⬇️ *С нейросетью*🤖\n──────────────",
                    parse_mode="MarkDownV2")
                send_msg_not_gpt = send_msgs_not_gpt[0]
                await asyncio.sleep(2)
                media_item = InputMediaVideo(media=FSInputFile(file_path), caption=caption, parse_mode="HTML")
                send_msgs = await bot.send_media_group(chat_id=TARGET_CHAT_ID, media=[media_item])
                first_msg_id = send_msgs[0].message_id

                await bot.send_message(
                    chat_id=TARGET_CHAT_ID,
                    text="Выберите действие:",
                    reply_markup=get_like_dislike_keyboard(),
                    reply_to_message_id=first_msg_id
                )
                send_msg = send_msgs[0]
            elif msg.voice:
                send_msg_not_gpt = await bot.send_voice(
                    chat_id=TARGET_CHAT_ID,
                    voice=file_input,
                    caption=caption_not_gpt,
                    parse_mode="HTML",
                    reply_markup=get_like_dislike_keyboard()
                )
                await bot.send_message(
                    chat_id=TARGET_CHAT_ID,
                    text="──────────────\n⬆️ *Без нейросети*📝\n⬇️ *С нейросетью*🤖\n──────────────",
                    parse_mode="MarkDownV2")
                send_msg = await bot.send_voice(
                    chat_id=TARGET_CHAT_ID,
                    voice=file_input,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=get_like_dislike_keyboard()
                )
            elif msg.document:
                send_msg_not_gpt = await bot.send_document(
                    chat_id=TARGET_CHAT_ID,
                    document=file_input,
                    caption=caption_not_gpt,
                    parse_mode="HTML",
                    reply_markup=get_like_dislike_keyboard()
                )
                await bot.send_message(
                    chat_id=TARGET_CHAT_ID,
                    text="──────────────\n⬆️ *Без нейросети*📝\n⬇️ *С нейросетью*🤖\n──────────────",
                    parse_mode="MarkDownV2")
                send_msg = await bot.send_document(
                    chat_id=TARGET_CHAT_ID,
                    document=file_input,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=get_like_dislike_keyboard()
                )
        finally:
            pass
            if os.path.exists(file_path):
                os.remove(file_path)
    else:
        # если нет медиа, просто текст
        if original_text:
            send_msg_not_gpt = await bot.send_message(
                chat_id=TARGET_CHAT_ID,
                text=caption_not_gpt,
                parse_mode="HTML",
                reply_markup=get_like_dislike_keyboard()
            )
            await bot.send_message(
                chat_id=TARGET_CHAT_ID,
                text="──────────────\n⬆️ *Без нейросети*📝\n⬇️ *С нейросетью*🤖\n──────────────",
                parse_mode="MarkDownV2")
            send_msg = await bot.send_message(
                chat_id=TARGET_CHAT_ID,
                text=caption,
                parse_mode="HTML",
                reply_markup=get_like_dislike_keyboard()
            )
    await msg_add_database(send_msg_not_gpt)
    await msg_add_database(send_msg)

import logging

logger = logging.getLogger(__name__)

async def process_channel(channel_id):
    last_id = state.last_message_ids.get(channel_id)
    logger.info(f"[{channel_id}] Начало обработки канала, last_message_id={last_id}")

    async for msg in client.iter_messages(channel_id, limit=10):
        logger.info(f"[{channel_id}] Получено сообщение msg.id={msg.id}, grouped_id={msg.grouped_id}")

        if last_id and msg.id == last_id:
            logger.info(f"[{channel_id}] Сообщение {msg.id} уже обработано, пропускаем")
            break

        if msg.grouped_id:
            logger.info(f"[{channel_id}] Это альбом с grouped_id={msg.grouped_id}")
            album_messages = []

            async for m in client.iter_messages(channel_id, limit=50):
                if m.grouped_id == msg.grouped_id:
                    album_messages.append(m)
                    logger.info(f"[{channel_id}] Добавляем сообщение {m.id} в альбом")

            if not album_messages:
                logger.warning(f"[{channel_id}] Альбом пустой! grouped_id={msg.grouped_id}")
            else:
                album_messages.sort(key=lambda x: x.id)
                logger.info(f"[{channel_id}] Альбом отсортирован, {len(album_messages)} сообщений")

                try:
                    await send_album(album_messages)
                    logger.info(f"[{channel_id}] Альбом отправлен")
                except Exception as e:
                    logger.error(f"[{channel_id}] Ошибка при отправке альбома: {e}")

                state.last_message_ids[channel_id] = max(m.id for m in album_messages)
                break

        else:
            logger.info(f"[{channel_id}] Одиночное сообщение, отправляем")
            try:
                await send_media(msg)
                logger.info(f"[{channel_id}] Сообщение {msg.id} отправлено")
            except Exception as e:
                logger.error(f"[{channel_id}] Ошибка при отправке сообщения {msg.id}: {e}")

            state.last_message_ids[channel_id] = msg.id
            break

def init_db():
    db = sqlite3.connect("posts.db")
    c = db.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS posts
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            msg_id INTEGER NOT NULL,
            file_id TEXT,
            caption TEXT,
            group_message_id INTEGER,
            entities TEXT,
            type_message TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS source_chats
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL UNIQUE,
            title TEXT
        )
    """)

    c.execute("PRAGMA table_info(source_chats)")
    columns = [row[1] for row in c.fetchall()]
    if "title" not in columns:
        c.execute("ALTER TABLE source_chats ADD COLUMN title TEXT")

    db.commit()
    db.close()

def seed_source_chats(channels: list[int]):
    db = sqlite3.connect("posts.db")
    c = db.cursor()

    for chat_id in channels:
        c.execute(
            "INSERT OR IGNORE INTO source_chats (chat_id) VALUES (?)",
            (chat_id,)
        )

    db.commit()
    db.close()

# ===== Основной парсер =====
async def parser_loop(_):
    while True:
        if state.enabled:
            channels = get_source_chats()
            for ch in channels:
                try:
                    await process_channel(ch)
                except Exception as e:
                    logger.error(f"[{ch}] Ошибка обработки канала: {e}")

        await asyncio.sleep(CHECK_INTERVAL)
