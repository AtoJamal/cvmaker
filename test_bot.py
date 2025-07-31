import logging
import os
import re
import asyncio
from dotenv import load_dotenv
from typing import Optional
from telegram import Update
from telegram.ext import (
    MessageHandler,
    CommandHandler,
    filters,
    ContextTypes,
    Application
)
import telegram

# Set up detailed logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

class TestBot:
    def __init__(self):
        """Initialize TestBot with enhanced logging"""
        logger.info("🔄 Initializing TestBot instance")
        self.user_cache = {}
        self.private_channel_id = None
        logger.info("✅ TestBot instance initialized")

    def register_handlers(self, application: Application) -> None:
        """Register all handlers with detailed logging"""
        logger.info("🔄 Starting handler registration process")
        
        load_dotenv()
        self.private_channel_id = os.getenv('PRIVATE_CHANNEL_ID')
        
        if not self.private_channel_id:
            error_msg = "❌ PRIVATE_CHANNEL_ID not set in .env file"
            logger.error(error_msg)
            raise ValueError(error_msg)

        try:
            channel_id_int = int(self.private_channel_id)
            logger.info(f"✅ Valid private channel ID: {channel_id_int}")
        except ValueError:
            error_msg = "❌ PRIVATE_CHANNEL_ID must be a valid integer"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        application.add_handler(MessageHandler(filters.ALL, self.debug_all_messages), group=-1)
        application.add_handler(CommandHandler("teststart", self.start_command), group=0)
        application.add_handler(CommandHandler("register", self.register_command), group=0)

        # Register handlers
        handlers = [
            MessageHandler(
                filters.Chat(int(self.private_channel_id)) & (filters.PHOTO | filters.Document.ALL),
                self.handle_file_upload
            ),
            MessageHandler(
                filters.Chat(int(self.private_channel_id)) & filters.TEXT & ~filters.COMMAND,
                self.handle_text_message
            ),
            MessageHandler(
                filters.ChatType.PRIVATE,
                self.cache_user_info
            ),
        ]

        for handler in handlers:
            application.add_handler(handler)
        
        logger.info("✅ All handlers registered successfully")

    async def debug_all_messages(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Debug handler to log all messages"""
        user = update.effective_user
        logger.info(f"🔍 DEBUG: Message from user: {user.username if user else 'Unknown'}")
        logger.info(f"🔍 DEBUG: Message text: {update.message.text if update.message else 'No text'}")
        logger.info(f"🔍 DEBUG: Chat type: {update.effective_chat.type}")

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /teststart command with enhanced caching"""
        logger.info("🚨 TESTSTART COMMAND HANDLER TRIGGERED!")
        logger.info("🔄 /teststart command received")
        user = update.effective_user
        
        if not user:
            logger.warning("❌ No user associated with /teststart command")
            return

        try:
            fresh_user = await context.bot.get_chat(user.id)
            username = fresh_user.username.lower() if fresh_user.username else None
            user_id = fresh_user.id
            
            if username:
                self._update_caches(username, user_id, context)
                logger.info(f"✅ User registered via /teststart: @{username} -> {user_id}")
                
                response = (
                    f"👋 Hello @{fresh_user.username}!\n\n"
                    f"✅ You are now registered with ID: {user_id}\n"
                    f"🔒 Your username has been cached for future messages."
                )
            else:
                response = "Please set a username in Telegram settings to receive messages."
                
            await update.message.reply_text(response)
        except Exception as e:
            logger.error(f"❌ Error in /teststart: {str(e)}")
            await update.message.reply_text("Registration failed. Please try again.")

    async def register_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /register command with detailed logging"""
        logger.info("🚨 REGISTER COMMAND HANDLER TRIGGERED!")
        logger.info("🔄 /register command received")
        user = update.effective_user
        
        if not user:
            logger.warning("❌ No user associated with /register command")
            return

        if user.username:
            username = user.username.lower()
            user_id = user.id
            self._update_caches(username, user_id, context)
            logger.info(f"✅ User registered via /register: @{username} -> {user_id}")
            
            response = (
                f"✅ Registration successful!\n\n"
                f"👤 Username: @{user.username}\n"
                f"🆔 User ID: {user_id}\n\n"
                f"📁 You can now receive files and messages through this bot!"
            )
            await update.message.reply_text(response)
        else:
            logger.warning("❌ User has no username set")
            await update.message.reply_text(
                "❌ Registration failed!\n\n"
                "Please set a username in your Telegram settings first, then try /register again."
            )

    async def cache_user_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Cache user information with detailed logging"""
        if not update.effective_user:
            logger.debug("❌ No effective user in update")
            return
                
        if not update.effective_user.username:
            logger.debug("❌ User has no username to cache")
            return

        username = update.effective_user.username.lower()
        user_id = update.effective_user.id
        self._update_caches(username, user_id, context)
        logger.info(f"✅ Cached user info: @{username} -> {user_id}")

    async def _resolve_with_retry(self, username: str, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
        """Helper method to resolve username with retry logic"""
        clean_username = username.replace('@', '').lower()
        
        logger.info(f"🔍 Checking caches for: {clean_username}")
        logger.info(f"🔍 Bot cache: {context.bot_data.get('user_cache', {})}")
        logger.info(f"🔍 Local cache: {self.user_cache}")
        
        # Check bot_data cache first
        if 'user_cache' in context.bot_data and clean_username in context.bot_data['user_cache']:
            user_id = context.bot_data['user_cache'][clean_username]
            logger.info(f"✅ Found in bot cache: {clean_username} -> {user_id}")
            return user_id
        
        # Check local cache
        if clean_username in self.user_cache:
            user_id = self.user_cache[clean_username]
            logger.info(f"✅ Found in local cache: {clean_username} -> {user_id}")
            return user_id
        
        logger.info(f"❌ Not found in caches: {clean_username}")
        
        # Try resolution via get_chat
        try:
            logger.info(f"🔄 Attempting get_chat for @{clean_username}")
            chat = await asyncio.wait_for(
                context.bot.get_chat(f"@{clean_username}"),
                timeout=3.0
            )
            if chat.type == 'private':
                user_id = chat.id
                self._update_caches(clean_username, user_id, context)
                logger.info(f"✅ Resolved via get_chat: @{clean_username} -> {user_id}")
                return user_id
        except asyncio.TimeoutError:
            logger.warning(f"⌛ get_chat timed out for @{clean_username}")
        except telegram.error.BadRequest as e:
            logger.warning(f"❌ get_chat failed for @{clean_username}: {str(e)}")
        
        # Try resolution via channel admins
        if self.private_channel_id:
            try:
                logger.info(f"🔄 Checking channel admins for @{clean_username}")
                admins = await context.bot.get_chat_administrators(self.private_channel_id)
                for admin in admins:
                    if admin.user.username and admin.user.username.lower() == f"@{clean_username}".lower():
                        user_id = admin.user.id
                        self._update_caches(clean_username, user_id, context)
                        logger.info(f"✅ Resolved via admins: @{clean_username} -> {user_id}")
                        return user_id
            except Exception as e:
                logger.warning(f"⚠️ Error checking admins for @{clean_username}: {str(e)}")
        
        logger.warning(f"❌ Could not resolve username @{clean_username}")
        return None

    async def resolve_username_to_id(self, username: str, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Resolve username to user ID with improved error handling"""
        clean_username = username.replace('@', '').lower()
        full_username = f"@{clean_username}"
        logger.info(f"🔍 Starting username resolution for: {full_username}")
        
        user_id = await self._resolve_with_retry(clean_username, context)
        if user_id:
            logger.info(f"✅ Successfully resolved {full_username} to ID: {user_id}")
            return user_id
        
        error_msg = (
            f"❌ Could not resolve username {full_username}\n"
            f"Tried multiple resolution methods\n"
            f"Please ensure:\n"
            f"1. You've sent /teststart to the bot\n"
            f"2. Your username is correct and public\n"
            f"3. You haven't blocked the bot\n"
            f"4. The bot has proper permissions"
        )
        logger.error(error_msg)
        raise ValueError(error_msg)

    def _update_caches(self, username: str, user_id: int, context: ContextTypes.DEFAULT_TYPE):
        """Update all caches with resolved user"""
        clean_username = username.replace('@', '').lower()
        self.user_cache[clean_username] = user_id
        if 'user_cache' not in context.bot_data:
            context.bot_data['user_cache'] = {}
        context.bot_data['user_cache'][clean_username] = user_id
        logger.info(f"✅ Updated caches for @{clean_username} -> {user_id}")

    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle text messages with improved error handling"""
        logger.info("=== TEXT MESSAGE HANDLER STARTED ===")
        
        message = update.message or update.channel_post
        if not message:
            logger.warning("❌ No message or channel_post in update")
            return
            
        logger.info(f"📨 Message received in chat: {message.chat_id}")

        if str(message.chat_id) != str(self.private_channel_id):
            logger.warning(f"❌ Message from wrong chat: {message.chat_id}, expected: {self.private_channel_id}")
            return

        message_text = message.text
        if not message_text:
            logger.warning("❌ No text content in message")
            await message.reply_text("Please include a username (e.g., @username) and message content.")
            return

        logger.info(f"📝 Message text: {message_text[:100]}...")

        username_match = re.match(r'^@(\w+)\s*(.*)', message_text, re.DOTALL)
        if not username_match:
            logger.warning("❌ Message doesn't start with @username pattern")
            await message.reply_text("Please start the message with a username (e.g., @username).")
            return

        username = username_match.group(1)
        remaining_text = username_match.group(2).strip()
        full_username = f"@{username}"
        
        if not remaining_text:
            logger.warning("❌ No message content after username")
            await message.reply_text(f"Please include message content after {full_username}.")
            return

        try:
            logger.info(f"🔍 Attempting to forward message to {full_username}")
            target_user_id = await self.resolve_username_to_id(username, context)
            
            await context.bot.send_message(
                chat_id=target_user_id,
                text=remaining_text
            )
            logger.info(f"📤 Message forwarded to user ID: {target_user_id}")
            
            await message.reply_text(f"✅ Message sent to {full_username} successfully.")
            
        except ValueError as e:
            logger.error(f"❌ Username resolution failed: {str(e)}")
            await message.reply_text(str(e))
        except telegram.error.Forbidden:
            logger.error(f"❌ Bot blocked by user {full_username}")
            await message.reply_text(f"❌ Failed to send to {full_username}. The user has blocked this bot.")
        except Exception as e:
            logger.error(f"❌ Unexpected error: {str(e)}")
            await message.reply_text(f"❌ Error sending message: {str(e)}")

    async def handle_file_upload(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle file uploads with detailed logging"""
        logger.info("=== FILE UPLOAD HANDLER STARTED ===")
        
        message = update.message or update.channel_post
        if not message:
            logger.warning("❌ No message or channel_post in update")
            return
            
        logger.info(f"📨 File received in chat: {message.chat_id}")

        if str(message.chat_id) != str(self.private_channel_id):
            logger.warning(f"❌ File from wrong chat: {message.chat_id}, expected: {self.private_channel_id}")
            return

        has_photo = bool(message.photo)
        has_document = bool(message.document)
        logger.info(f"📂 File type - Photo: {has_photo}, Document: {has_document}")

        if not (has_photo or has_document):
            logger.warning("❌ No valid file attachment")
            await message.reply_text("❌ No photo or document found. Please upload a file with the message.")
            return

        message_text = message.caption if message.caption else message.text
        if not message_text:
            logger.warning("❌ No username provided with file")
            await message.reply_text("Please include a username (e.g., @username) with the file.")
            return

        username_match = re.match(r'^@(\w+)\s*(.*)', message_text, re.DOTALL)
        if not username_match:
            fallback_match = re.search(r'@?(\w+)', message_text)
            if not fallback_match:
                logger.warning("❌ No valid username found in message")
                await message.reply_text("No valid username found. Please include a username (with or without '@').")
                return
            username = fallback_match.group(1)
            caption_content = None
            logger.info(f"ℹ️ Using fallback username match: @{username}")
        else:
            username = username_match.group(1)
            caption_content = username_match.group(2).strip() if username_match.group(2).strip() else None
            logger.info(f"ℹ️ Using standard username match: @{username}")

        full_username = f"@{username}"
        logger.info(f"🔍 Processing file for username: {full_username}")

        try:
            logger.info(f"🔄 Resolving username: {full_username}")
            target_user_id = await self.resolve_username_to_id(username, context)
            logger.info(f"✅ Resolved {full_username} to ID: {target_user_id}")

            if message.photo:
                photo = message.photo[-1]
                logger.info(f"📷 Sending photo to user ID: {target_user_id}")
                await context.bot.send_photo(
                    chat_id=target_user_id,
                    photo=photo.file_id,
                    caption=caption_content
                )
                file_type = "photo"
            elif message.document:
                document = message.document
                logger.info(f"📄 Sending document to user ID: {target_user_id}")
                await context.bot.send_document(
                    chat_id=target_user_id,
                    document=document.file_id,
                    caption=caption_content
                )
                file_type = "document"
            else:
                logger.error("❌ Unexpected file type")
                await message.reply_text("No valid file (photo or document) found.")
                return

            logger.info(f"✅ {file_type.capitalize()} sent successfully")
            await message.reply_text(
                f"✅ {file_type.capitalize()} sent to {full_username} successfully."
            )

        except ValueError as e:
            logger.error(f"❌ Username resolution failed: {str(e)}")
            await message.reply_text(str(e))
        except telegram.error.Forbidden:
            logger.error(f"❌ Bot blocked by user {full_username}")
            await message.reply_text(
                f"❌ Failed to send to {full_username}. The user has blocked this bot."
            )
        except Exception as e:
            logger.error(f"❌ Unexpected error: {str(e)}")
            await message.reply_text(f"❌ Error sending file: {str(e)}")