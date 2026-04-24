from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from config import API_ID, API_HASH, PHONE, CHECK_INTERVAL, TARGET_CHAT_ID, API_KEY, PROMPT_IMAGE
import asyncio
from telethon_client import edit_text_with_gpt
from telethon import TelegramClient
import json
from aiogram.types import CallbackQuery, FSInputFile, MessageEntity, InputMediaPhoto, InputMediaVideo, InputMediaDocument
from telethon_client import find_chat
from config import MAIN_CHAT_ID
from state import state
from config import BOT_TOKEN
import re
import requests
import html
import base64
import tempfile
import io
import os
import sqlite3

DISLIKE_TARGET_CHAT_ID = -1003732500567

client = TelegramClient("session/user", API_ID, API_HASH)

bot = Bot(token=BOT_TOKEN)

class CustomMessageEntity(MessageEntity):
    def __init__(self, type, offset, length, custom_emoji_id=None, **kwargs):
        super().__init__(type=type, offset=offset, length=length, **kwargs)
        self.custom_emoji_id = custom_emoji_id

def regenerate_image(image_path: str) -> str:
    url = "https://api.openai.com/v1/images/edits"

    headers = {
        "Authorization": f"Bearer {API_KEY}"
    }

    files = {
        "image": open(image_path, "rb")
    }

    data = {
        "model": "gpt-image-1",
        "prompt": PROMPT_IMAGE,
        "size": "1024x1024"
    }

    response = requests.post(url, headers=headers, files=files, data=data)
    result = response.json()
    try:
        image_base64 = result["data"][0]["b64_json"]
        output_path = tempfile.mktemp(suffix=".png")

        with open(output_path, "wb") as f:
            f.write(base64.b64decode(image_base64))

        return output_path
    except Exception as e:
        
        print("Ошибка генерации картинки:", e)

        # путь к локальной картинке
        fallback_path = os.path.join(os.path.dirname(__file__), "img1.png")

        return fallback_path
        

def restore_entities(entities_json):
    if not entities_json:
        return None
    data = json.loads(entities_json)
    entities = []
    for e in data:
        if e.get("type") == "custom_emoji":
            entities.append(CustomMessageEntity(**e))
        else:
            entities.append(MessageEntity(**e))
    return entities

def escape_html(text: str) -> str:
    if not text:
        return ""
    return html.escape(text)

def get_text_from_offset(text: str, offset: int, length: int) -> str:
    if not text:
        return ""

    # кодируем в utf-16-le
    encoded = text.encode("utf-16-le")

    start = offset * 2
    end = (offset + length) * 2

    # защита от выхода за границы
    start = min(start, len(encoded))
    end = min(end, len(encoded))

    slice_bytes = encoded[start:end]

    # если длина нечётная — обрезаем 1 байт
    if len(slice_bytes) % 2 != 0:
        slice_bytes = slice_bytes[:-1]

    return slice_bytes.decode("utf-16-le", errors="ignore")

async def show_upload(chat_id):
    while True:
        await bot.send_chat_action(chat_id=chat_id, action="upload_photo")
        await asyncio.sleep(4)
        
def entities_to_html_aiogram(text: str, entities_json) -> str:
    if not text:
        return ""

    entities = restore_entities(entities_json)
    if not entities:
        return escape_html(text)

    result = ""
    last_offset = 0

    for e in sorted(entities, key=lambda x: x.offset):
        # текст до entity
        if e.offset > last_offset:
            result += escape_html(
                get_text_from_offset(text, last_offset, e.offset - last_offset)
            )

        entity_text = get_text_from_offset(text, e.offset, e.length)
        entity_text = escape_html(entity_text)

        t = getattr(e, "type", None)

        if t == "bold":
            result += f"<b>{entity_text}</b>"
        elif t == "italic":
            result += f"<i>{entity_text}</i>"
        elif t == "underline":
            result += f"<u>{entity_text}</u>"
        elif t == "strikethrough":
            result += f"<s>{entity_text}</s>"
        elif t == "spoiler":
            result += f"<tg-spoiler>{entity_text}</tg-spoiler>"
        elif t == "code":
            result += f"<code>{entity_text}</code>"
        elif t == "pre":
            result += f"<pre>{entity_text}</pre>"
        elif t == "text_link":
            result += f'<a href="{e.url}">{entity_text}</a>'
        elif t == "url":
            result += f'<a href="{entity_text}">{entity_text}</a>'
        elif t == "email":
            result += f'<a href="mailto:{entity_text}">{entity_text}</a>'
        elif t == "mention":
            result += entity_text
        elif t == "custom_emoji":
            result += entity_text
        else:
            result += entity_text

        last_offset = e.offset + e.length

    # хвост текста
    if last_offset < len(text):
        result += escape_html(
            get_text_from_offset(text, last_offset, len(text) - last_offset)
        )

    return result

async def register_handlers(dp: Dispatcher):

    @dp.message(Command("start"))
    async def start_cmd(msg: types.Message):
        await msg.answer(
            "Команды:\n"
            "/parse — начать парсинг\n"
            "/stop — остановить"
        )

    @dp.message(Command("parse"))
    async def parse_cmd(msg: types.Message):
            state.source_chat = MAIN_CHAT_ID
            state.enabled = True
            state.last_message_id = None

            await msg.answer("✅ Парсинг запущен")

    @dp.callback_query(F.data == "reload")
    async def handle_like(callback: CallbackQuery): 
            db = sqlite3.connect('posts.db')
            await callback.answer("Обновляю текст...")
            c = db.cursor()   
            msg = callback.message
            msg_with_buttons = callback.message
            message_id = msg.message_id
            original_msg = msg_with_buttons.reply_to_message
            msg_id = msg_with_buttons.message_id
            if original_msg:
                msg = original_msg
                msg_id = msg.message_id
            entities = msg.caption_entities or msg.entities
            if entities:
                entities_json = json.dumps([e.model_dump() for e in entities])  
            else:
                entities_json = None
            print("caption", msg.caption)
            text = entities_to_html_aiogram(msg.caption, entities_json)
            print("text после html", text)
            result = await edit_text_with_gpt(text)
            text_gpt = result["text"] if result["ok"] else text
            print(text_gpt)

            if original_msg:
                msg = original_msg
                msg_id = msg_with_buttons.reply_to_message.message_id
            # Обновляем текст в БД
            try:
                c.execute("SELECT * FROM posts WHERE msg_id = ?", (msg_id,))
                rows = c.fetchall()
                if rows[0][4] != None:
                    c.execute("SELECT * FROM posts WHERE group_message_id = ?", (rows[0][4],))
                    rows = c.fetchall()
                # если это одиночное сообщение
                c.execute(
                    "UPDATE posts SET caption = ? WHERE msg_id = ?",
                    (text_gpt, message_id)
                )
                db.commit()
            except Exception as e:
                print("DB update error:", e)
            finally:
                db.close()
            if rows != []:
                await callback.answer("Текст отредактирован!")
                try:
                    if msg.caption is not None:
                        await bot.edit_message_caption(chat_id=TARGET_CHAT_ID, message_id=rows[0][1], caption=text_gpt, parse_mode="HTML", reply_markup=msg.reply_markup)
                    elif msg.text is not None:
                        await bot.edit_message_text(chat_id=TARGET_CHAT_ID, message_id=rows[0][1], text=text_gpt, parse_mode="HTML", reply_markup=msg.reply_markup)
                except Exception as e:
                    print("Edit error:", e)
            else:
                await callback.answer("Текст отредактирован!")
                try:
                    if msg.caption is not None:
                        await msg.edit_caption(caption=text_gpt, parse_mode="HTML", reply_markup=msg.reply_markup)
                    elif msg.text is not None:
                        await msg.edit_text(text=text_gpt, parse_mode="HTML", reply_markup=msg.reply_markup)
                except Exception as e:
                    print("Edit error:", e)
      
    @dp.callback_query(F.data == "reload_image")
    async def reload_image(callback: CallbackQuery):
        await callback.answer("Обрабатываю...")

        msg = callback.message
        original_msg = msg.reply_to_message or msg
        msg_id = original_msg.message_id

        db = sqlite3.connect('posts.db')
        c = db.cursor()

        c.execute("SELECT * FROM posts WHERE msg_id = ?", (msg_id,))
        rows = c.fetchall()

        # 🔥 если это альбом
        if rows and rows[0][4] is not None:
            group_id = rows[0][4]

            c.execute("SELECT * FROM posts WHERE group_message_id = ?", (group_id,))
            rows = c.fetchall()

            media_group = []

            # 🚀 индикатор загрузки
            upload_task = asyncio.create_task(show_upload(msg.chat.id))

            try:
                for row in rows:
                    _, message_id, file_id, caption, group_message_id, entities, type_message = row

                    if type_message != "photo":
                        continue
                    print(caption)
                    file = await bot.get_file(file_id)

                    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as temp:
                        await bot.download_file(file.file_path, temp.name)
                        input_path = temp.name

                    # 🔥 генерация
                    output_path = await asyncio.to_thread(regenerate_image, input_path)

                    # 🔥 отправляем временно → получаем file_id
                    sent = await bot.send_photo(
                        chat_id=msg.chat.id,
                        photo=FSInputFile(output_path),
                        parse_mode = "HTML"
                    )

                    new_file_id = sent.photo[-1].file_id

                    await bot.delete_message(msg.chat.id, sent.message_id)

                    # обновляем БД
                    c.execute(
                        "UPDATE posts SET file_id = ? WHERE msg_id = ?",
                        (new_file_id, message_id)
                    )

                    media_group.append(
                        InputMediaPhoto(media=new_file_id)
                    )

                db.commit()

                # ❗ удаляем старый альбом
                for row in rows:
                    try:
                        await bot.delete_message(chat_id=msg.chat.id, message_id=row[1])
                    except:
                        pass

                # ❗ добавляем caption только к первому
                if media_group:
                    media_group[0].caption = rows[0][3]
                    media_group[0].parse_mode = "HTML"

                # 🚀 отправляем новый альбом
                sent_messages = await bot.send_media_group(
                    chat_id=msg.chat.id,
                    media=media_group
                )

                # 🔥 создаём кнопки под альбомом
                buttons_msg = await bot.send_message(
                    chat_id=msg.chat.id,
                    text="Выбери действие:",
                    reply_markup=msg.reply_markup,
                    reply_to_message_id=sent_messages[0].message_id
                )

                # 🔥 обновляем БД
                for i, sent_msg in enumerate(sent_messages):
                    old_msg_id = rows[i][1]
                    c.execute(
                        "UPDATE posts SET msg_id = ? WHERE msg_id = ?",
                        (sent_msg.message_id, old_msg_id)
                    )
                db.commit()

            finally:
                upload_task.cancel()
                db.close()

            return

        # 🔥 если это одиночное фото (твоя старая логика)
        if not original_msg.photo:
            return

        file = await bot.get_file(original_msg.photo[-1].file_id)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as temp:
            await bot.download_file(file.file_path, temp.name)
            input_path = temp.name

        upload_task = asyncio.create_task(show_upload(msg.chat.id))

        try:
            output_path = await asyncio.to_thread(regenerate_image, input_path)

            sent = await bot.send_photo(
                chat_id=msg.chat.id,
                photo=FSInputFile(output_path),
                caption=msg.caption,
                parse_mode="HTML"
            )

            new_file_id = sent.photo[-1].file_id

            await bot.delete_message(chat_id=msg.chat.id, message_id=sent.message_id)

            c.execute(
                "UPDATE posts SET file_id = ? WHERE msg_id = ?",
                (new_file_id, msg.message_id)
            )
            db.commit()

            await bot.edit_message_media(
                chat_id=msg.chat.id,
                message_id=msg.message_id,
                media=InputMediaPhoto(
                    media=new_file_id,
                    caption=msg.caption,
                    parse_mode = "HTML"
                ),
                reply_markup=msg.reply_markup
            )

        finally:
            upload_task.cancel()
            db.close()

    @dp.callback_query(F.data == "dislike")
    async def handle_dislike(callback: CallbackQuery):
        msg_with_buttons = callback.message
        await msg_with_buttons.delete()

        db = sqlite3.connect('posts.db')
        c = db.cursor()

        original_msg = msg_with_buttons.reply_to_message
        if not original_msg:
            original_msg = msg_with_buttons

        msg_id = original_msg.message_id

        # 🔥 получаем пост
        c.execute("SELECT * FROM posts WHERE msg_id = ?", (msg_id,))
        rows = c.fetchall()

        if not rows:
            await callback.answer("Пост не найден")
            db.close()
            return

        # 🔥 если альбом
        if rows[0][4] is not None:
            group_id = rows[0][4]

            c.execute(
                "SELECT * FROM posts WHERE group_message_id = ?",
                (group_id,)
            )
            rows = c.fetchall()

        # 🔥 сортировка (как в like)
        rows = sorted(rows, key=lambda x: x[1])

        # 🔥 удаление всех сообщений
        for row in rows:
            _, msg_id, *_ = row

            try:
                await bot.delete_message(TARGET_CHAT_ID, msg_id)
            except Exception as e:
                print(f"[LOG] Ошибка удаления msg_id = {msg_id}: {e}")

        db.close()

        await callback.answer("Пост удалён!")
    
    @dp.callback_query(F.data == "like")
    async def handle_like(callback: CallbackQuery):
        MAIN_CHAT_ID = -1003732500567

        msg_with_buttons = callback.message
        await msg_with_buttons.delete()
        print(f"[LOG] Кнопка 'like' нажата. msg_with_buttons.message_id = {msg_with_buttons.message_id}")

        db = sqlite3.connect('posts.db')
        c = db.cursor()

        await callback.answer("Пост успешно опубликован!")

        original_msg = msg_with_buttons.reply_to_message
        if original_msg:
            print(f"[LOG] Оригинальное сообщение найдено. original_msg.message_id = {original_msg.message_id}")
            
        else:
            print("[LOG] Оригинальное сообщение НЕ найдено.")
            original_msg = msg_with_buttons
           

        msg_id = original_msg.message_id

        # 🔥 получаем пост
        c.execute("SELECT * FROM posts WHERE msg_id = ?", (msg_id,))
        rows = c.fetchall()
        print(f"[LOG] Найдено {len(rows)} записей в БД для msg_id = {msg_id}")

        if not rows:
            await callback.answer("Пост не найден")
            db.close()
            return

        # 🔥 если альбом
        if rows[0][4] is not None:  # group_message_id
            group_id = rows[0][4]
            print(f"[LOG] Это альбом с group_message_id = {group_id}")

            c.execute(
                "SELECT * FROM posts WHERE group_message_id = ?",
                (group_id,)
            )
            rows = c.fetchall()
            print(f"[LOG] Альбом содержит {len(rows)} сообщений")

        # 🔥 сортировка
        rows = sorted(rows, key=lambda x: x[1])

        media_group = []
        first_text = None

        for row in rows:
            _, msg_id, file_id, caption, group_message_id, entities, type_message = row
            print(f"[LOG] Обрабатываем msg_id = {msg_id}, type = {type_message}, group_message_id = {group_message_id}")

            if caption:

                if entities:

                    text = entities_to_html_aiogram(caption, entities)

                else:

                    text = caption

            else:

                text = None

            # альбом
            if group_message_id is not None:
                if first_text is None and text:
                    first_text = text

                if type_message == "photo":
                    media_group.append(InputMediaPhoto(media=file_id))
                elif type_message == "video":
                    media_group.append(InputMediaVideo(media=file_id))
                else:
                    media_group.append(InputMediaDocument(media=file_id))

            else:
                # одиночное сообщение
                if type_message == "photo":
                    print(f"[LOG] Отправка одиночного фото msg_id = {msg_id}")
                    await bot.send_photo(MAIN_CHAT_ID, file_id, caption=text, parse_mode="HTML")
                elif type_message == "video":
                    print(f"[LOG] Отправка одиночного видео msg_id = {msg_id}")
                    await bot.send_video(MAIN_CHAT_ID, file_id, caption=text, parse_mode="HTML")
                elif type_message == "text":
                    print(f"[LOG] Отправка одиночного текста msg_id = {msg_id}")
                    await bot.send_message(MAIN_CHAT_ID, text, parse_mode="HTML")

            # удаляем из модерации
            try:
                print(f"[LOG] Удаление сообщения из модерации msg_id = {msg_id}")
                await bot.delete_message(TARGET_CHAT_ID, msg_id)
            except Exception as e:
                print(f"[LOG] Ошибка удаления msg_id = {msg_id}: {e}")

        # 🔥 отправка альбома
        if media_group:
            media_group[0].caption = first_text
            media_group[0].parse_mode = "HTML"
            print(f"[LOG] Отправка альбома с {len(media_group)} медиа")

            await bot.send_media_group(
                chat_id=MAIN_CHAT_ID,
                media=media_group
            )

        db.close()
        print("[LOG] Обработка 'like' завершена")
                
