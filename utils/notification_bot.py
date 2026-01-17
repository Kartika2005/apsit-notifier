import json
import logging
import asyncio
import aiohttp
from bs4 import BeautifulSoup
from telegram.ext import Application

from .config import load_config
from .storage import MongoStorage
from .senders.telegram_sender import TelegramSender
from .senders.whatsapp_sender import WhatsAppSender

logger = logging.getLogger(__name__)


class NotificationBot:
    def __init__(self):
        self.config = load_config()
        self.session = aiohttp.ClientSession()
        self.storage = MongoStorage(
            mongo_uri=self.config.MONGO_URI,
            db_name=self.config.MONGO_DB_NAME,
            collection=self.config.MONGO_COLLECTION,
        )
        self._tick_task: asyncio.Task | None = None
        self.application = (
            Application.builder()
            .token(self.config.TOKEN)
            .post_init(self._on_init)
            .post_shutdown(self._on_shutdown)
            .build()
        )
        self.telegram_sender: TelegramSender | None = None
        self.whatsapp_sender: WhatsAppSender | None = None

    async def _on_init(self, app: Application) -> None:
        if self.config.CHANNEL_ID:
            self.telegram_sender = TelegramSender(app.bot, self.config.CHANNEL_ID)
        if self.config.WHATSAPP_AUTH_TOKEN and self.config.WHATSAPP_RECIPIENT:
            self.whatsapp_sender = WhatsAppSender(
                session=self.session,
                api_url=self.config.WHATSAPP_API_URL,
                auth_token=self.config.WHATSAPP_AUTH_TOKEN,
                recipient=self.config.WHATSAPP_RECIPIENT,
            )
        self._tick_task = app.create_task(self._ticker_loop())

    async def _on_shutdown(self, app: Application) -> None:
        try:
            if self._tick_task and not self._tick_task.done():
                self._tick_task.cancel()
                try:
                    await self._tick_task
                except asyncio.CancelledError:
                    pass
        finally:
            try:
                await self.session.close()
            finally:
                await self.storage.close()

    async def get_latest_notifications(self):
        try:
            async with self.session.get(self.config.CLONED_PAGE_URL) as response:
                if response.status == 200:
                    content = await response.text()
                    return self.parse_content(content)
                logger.error(f"HTTP Error: {response.status}")
                return {}
        except Exception as e:
            logger.error(f"Fetch error: {str(e)}")
            return {}

    def parse_content(self, content):
        notifications = {
            "Latest Announcements": [],
            "Exam Notifications": [],
            "Office Notifications": [],
            "Scholarship Section": [],
            "Application Formats": [],
            "Cultural Events": [],
            "Technical Clubs": [],
            "IEEE & CSI": []
        }

        sections_map = {
            "Latest announcements": "Latest Announcements",
            "Exam Notifications": "Exam Notifications",
            "Office Notifications": "Office Notifications",
            "Scholarship Section": "Scholarship Section",
            "Application Formats": "Application Formats",
            "Cultural Events": "Cultural Events",
            "Technical Clubs": "Technical Clubs",
            "IEEE & CSI": "IEEE & CSI"
        }

        soup = BeautifulSoup(content, 'html.parser')
        sections = soup.select('section.block')

        for section in sections:
            header = section.find('h2')
            if not header:
                continue

            section_title = header.get_text(strip=True)
            section_key = sections_map.get(section_title)
            if not section_key:
                logger.warning(f"Skipping unknown section: {section_title}")
                continue

            content_div = section.find('div', class_='content')
            if not content_div:
                continue

            items = []
            if section_key == "Latest Announcements":
                items = content_div.find_all('li', class_='post')
            else:
                items = content_div.find_all(['a', 'li'])

            for item in items:
                try:
                    if section_key == "Latest Announcements":
                        anchor = item.find('a')
                        if not anchor:
                            continue
                        title = anchor.get_text(strip=True)
                        link = anchor['href']
                        date = item.find('div', class_='date').get_text(strip=True)
                        author = item.find('div', class_='name').get_text(strip=True)
                        notifications[section_key].append({
                            "title": self.clean_text(title),
                            "link": link,
                            "date": date,
                            "author": author
                        })
                    else:
                        if item.name == 'a':
                            title = item.get_text(strip=True)
                            link = item['href']
                        elif item.name == 'li':
                            anchor = item.find('a')
                            if not anchor:
                                continue
                            link = anchor['href']
                            title = item.get_text(strip=True).replace('\n', ' ')
                        else:
                            continue
                        notifications[section_key].append({
                            "title": self.clean_text(title),
                            "link": link
                        })
                except Exception as e:
                    logger.warning(f"Error processing item in {section_key}: {str(e)}")
                    continue

        return notifications

    def clean_text(self, text, for_markdown: bool = False):
        if for_markdown:
            markdown_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
            for char in markdown_chars:
                text = text.replace(char, f"\\{char}")
        else:
            text = text.replace('\\', '')
        return " ".join(text.split())

    async def check_for_updates(self):
        current = await self.get_latest_notifications()
        previous = await self.storage.load_state()
        new_notifications = self.find_new_notifications(current, previous)
        if any(new_notifications.values()):
            await self.send_notifications(new_notifications)
            await self.storage.save_state(current)
            total = sum(len(v) for v in current.values())
            logger.info(f"Saved state to MongoDB with {total} items across {len(current)} sections")

    def find_new_notifications(self, current, previous):
        return {
            section: [item for item in current.get(section, []) if item not in previous.get(section, [])]
            for section in current
        }

    def format_telegram_message(self, section, item):
        clean_section = self.clean_text(section, for_markdown=False)
        clean_title = self.clean_text(item['title'], for_markdown=False)
        link = item['link']
        base = f"📣 New {clean_section}!\n\n{clean_title}\n🔗 {link}"
        if all(key in item for key in ('date', 'author')):
            clean_date = self.clean_text(item['date'], for_markdown=False)
            clean_author = self.clean_text(item['author'], for_markdown=False)
            return f"{base}\n🗓 {clean_date}\n👤 {clean_author}"
        return base

    def format_whatsapp_message(self, section, item):
        message = f"📢 New {section} Alert!\n{item['title']}\n🔗 {item['link']}"
        if 'date' in item and 'author' in item:
            message += f"\n🗓 {item['date']}\n👤 {item['author']}"
        return message

    async def send_notifications(self, new_notifications):
        telegram_messages: list[str] = []
        whatsapp_messages: list[str] = []

        for section, items in new_notifications.items():
            for item in items:
                telegram_messages.append(self.format_telegram_message(section, item))
                whatsapp_messages.append(self.format_whatsapp_message(section, item))

        if self.telegram_sender and telegram_messages:
            await self.telegram_sender.send_items(telegram_messages)
        if self.whatsapp_sender and whatsapp_messages:
            logger.info("Attempting to send WhatsApp messages...")
            await self.whatsapp_sender.send_items(whatsapp_messages)

    async def _ticker_loop(self) -> None:
        try:
            while True:
                await self.check_for_updates()
                await asyncio.sleep(self.config.CHECK_INTERVAL)
        except asyncio.CancelledError:
            pass

    def run(self):
        logger.info("Bot started. Press Ctrl+C to stop.")
        self.application.run_polling()