import logging
import os
from flask import Flask
import threading
import re
import json
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
from telegram.request import HTTPXRequest
from mainapp.models import (
    Candidate,
    Order,
    CandidateManager,
    WorkExperience,
    Education,
    Skill,
    CareerObjective,
    CertificationAward,
    Project,
    Language,
    OtherActivity,
)
import uuid
import firebase_admin
from firebase_admin import credentials, firestore
import django
from typing import Dict, List
import asyncio
import telegram
from translations import PROMPTS


# Ensure Python version is 3.6 or higher
import sys
if sys.version_info < (3, 6):
    raise RuntimeError("This bot requires Python 3.6 or higher")

# Set up Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cvbot_backend.settings')
django.setup()


# Load environment variables
load_dotenv()

# Get Telegram bot token and private channel ID
telegram_bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
private_channel_id = os.getenv('PRIVATE_CHANNEL_ID')
tutorial_video_message_id = os.getenv('TUTORIAL_VIDEO_MESSAGE_ID')
sample_cv_message_ids = os.getenv('SAMPLE_CV_MESSAGE_IDS', '').split(',') if os.getenv('SAMPLE_CV_MESSAGE_IDS') else []

tutorial_video_file_id = os.getenv('TUTORIAL_VIDEO_FILE_ID')
tutorial_video_caption = os.getenv('TUTORIAL_VIDEO_CAPTION', '')
sample_cv_file_ids = os.getenv('SAMPLE_CV_FILE_IDS', '').split(',') if os.getenv('SAMPLE_CV_FILE_IDS') else []
sample_cv_captions = os.getenv('SAMPLE_CV_CAPTIONS', '').split(',') if os.getenv('SAMPLE_CV_CAPTIONS') else [''] * len(sample_cv_file_ids)


# Add new conversation state (add this to the existing states tuple)

# Initialize Firebase only if not already initialized
logger = logging.getLogger(__name__)
logger.info("Attempting to load Firebase credentials from GOOGLE_APPLICATION_CREDENTIALS")
try:
    firebase_admin.get_app()
except ValueError:
    credentials_json = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
    if not credentials_json:
        raise ValueError("GOOGLE_APPLICATION_CREDENTIALS environment variable not set")
    try:
        cred_dict = json.loads(credentials_json)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse GOOGLE_APPLICATION_CREDENTIALS JSON: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Error initializing Firebase with credentials: {str(e)}")
        raise
db = firestore.client()
logger.info("Firestore client obtained.")

# Set up logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# Define conversation states
(
    SELECT_LANGUAGE,
    START,
    COLLECT_PERSONAL_INFO,
    COLLECT_CONTACT_INFO,
    COLLECT_PROFILE_IMAGE,
    COLLECT_PROFESSIONAL_INFO,
    COLLECT_EDUCATION,
    COLLECT_SKILLS,
    COLLECT_CAREER_OBJECTIVE,
    COLLECT_CERTIFICATIONS,
    COLLECT_PROJECTS,
    COLLECT_LANGUAGES,
    COLLECT_ACTIVITIES,
    CONFIRM_ORDER,
    PAYMENT
) = range(15)

class CVBot:
    def __init__(self, token: str):
        # Configure HTTPXRequest with supported parameters
        request = HTTPXRequest(
            connection_pool_size=10,
            connect_timeout=60.0,
            read_timeout=60.0,
            write_timeout=60.0
        )
        logger.info("Initializing Application with token")
        self.application = Application.builder().token(token).request(request).post_init(self.post_init).build()
        self.user_sessions: Dict[str, Dict] = {}  # Dictionary to store user-specific data
        self.user_cache: Dict[str, int] = {}  # Cache for username to user_id mapping


        logger.info("🔄 Initializing CVBot instance")
        logger.info("🔄 Building Application instance")
        logger.info("🔄 Setting up handlers")
        self.setup_handlers()
        logger.info("✅ CVBot initialized successfully") 


    async def post_init(self, application: Application) -> None:
        """Called after application initialization to start background tasks"""
        self.start_background_tasks()

    def setup_handlers(self) -> None:
        """Set up conversation handlers for the bot"""
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler("start", self.start)],
            states={
                SELECT_LANGUAGE: [
                    CallbackQueryHandler(self.select_language, pattern="^lang_")
                ],

                START: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.start_collecting_info),
                    CallbackQueryHandler(self.start_collecting_info, pattern="^(update_profile|new_cv)$"),
                    CallbackQueryHandler(self.handle_returning_user_choice, pattern="^(new_cv|guide_video|samples)$")
                ],

                COLLECT_PERSONAL_INFO: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.collect_personal_info)
                ],
                COLLECT_CONTACT_INFO: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.collect_contact_info)
                ],
                COLLECT_PROFILE_IMAGE: [
                    MessageHandler(
                        filters.PHOTO | filters.Document.IMAGE | filters.Document.MimeType("application/pdf") | filters.TEXT,
                        self.collect_profile_image
                    ),
                    CallbackQueryHandler(self.handle_profile_image_choice, pattern="^continue_professional$")
                ],
                COLLECT_PROFESSIONAL_INFO: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.collect_professional_info),
                    CallbackQueryHandler(self.handle_professional_info_choice, pattern="^(add_another_work|continue_education)$")
                ],
                COLLECT_EDUCATION: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.collect_education),
                    CallbackQueryHandler(self.handle_education_choice, pattern="^(add_another_edu|continue_skills)$")
                ],
                COLLECT_SKILLS: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.collect_skills),
                    CallbackQueryHandler(self.handle_skills_choice, pattern="^(add_another_skill|continue_career)$")
                ],
                COLLECT_CAREER_OBJECTIVE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.collect_career_objective)
                ],
                COLLECT_CERTIFICATIONS: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.collect_certifications),
                    CallbackQueryHandler(self.handle_certifications_choice, pattern="^(add_another_cert|continue_projects)$")
                ],
                COLLECT_PROJECTS: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.collect_projects),
                    CallbackQueryHandler(self.handle_projects_choice, pattern="^(add_another_project|continue_languages)$")
                ],
                COLLECT_LANGUAGES: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.collect_languages),
                    CallbackQueryHandler(self.handle_languages_choice, pattern="^(add_another_language|continue_activities)$")
                ],
                COLLECT_ACTIVITIES: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.collect_activities)
                ],
                CONFIRM_ORDER: [
                    CallbackQueryHandler(self.confirm_order, pattern="^confirm_"),
                    CallbackQueryHandler(self.edit_info, pattern="^edit_")
                ],
                PAYMENT: [
                    MessageHandler(
                        filters.PHOTO | filters.Document.IMAGE | filters.Document.MimeType("application/pdf"),
                        self.handle_payment_screenshot
                    )
                ]
            },
            fallbacks=[CommandHandler("cancel", self.cancel)],
            per_message=False
        )
        
        payment_retry_handler = ConversationHandler(
        entry_points=[CommandHandler("payment", self.handle_payment_command)],  # Fixed method name
        states={
            PAYMENT: [
                MessageHandler(
                    filters.PHOTO | filters.Document.IMAGE | filters.Document.MimeType("application/pdf"),
                    self.handle_payment_screenshot
                )
            ]
        },
        fallbacks=[CommandHandler("cancel", self.cancel)],
        per_message=False
    )
        
        self.application.add_handler(conv_handler)
        self.application.add_handler(payment_retry_handler)
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CallbackQueryHandler(self.handle_admin_response, pattern="^(approve_|reject_)"))
        self.application.add_handler(MessageHandler(filters.Chat(int(private_channel_id)) & filters.REPLY, self.handle_admin_reply))
        self.application.add_handler(MessageHandler(filters.Chat(int(private_channel_id)) & ~filters.REPLY & ~(filters.PHOTO | filters.Document.ALL), self.ignore_non_reply_messages))
        self.application.add_handler(MessageHandler(filters.ChatType.PRIVATE, self.cache_user_info))
        self.application.add_error_handler(self.error_handler)

        self.application.add_handler(MessageHandler(filters.VIDEO | filters.Document.ALL, self.log_file_id), group=1)

    async def log_file_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Temporary handler to log file_id of uploaded media"""
        if update.message and update.message.chat_id == int(private_channel_id):
            if update.message.video:
                logger.info(f"Video file_id: {update.message.video.file_id}")
            elif update.message.document:
                logger.info(f"Document file_id: {update.message.document.file_id}")
    def start_background_tasks(self) -> None:
        """Start background tasks for polling order status changes"""
        self.application.create_task(self.poll_order_status_changes())

    async def poll_order_status_changes(self) -> None:
        """Poll Firestore for order status changes and send notifications"""
        while True:
            try:
                for telegram_id, session in list(self.user_sessions.items()):
                    if 'order_id' not in session or 'chat_id' not in session:
                        logger.debug(f"Skipping session for telegram_id {telegram_id}: missing order_id or chat_id")
                        continue
                    order = Order.get_by_id(session['order_id'])
                    if not order:
                        logger.debug(f"Order {session['order_id']} not found for telegram_id {telegram_id}")
                        continue
                    if order.status in ['verified', 'rejected'] and not session.get('notified', False):
                        if order.status == 'verified':
                            await self.application.bot.send_message(
                                chat_id=session['chat_id'],
                                text=self.get_prompt(session, 'payment_verified')
                            )
                            logger.info(f"Sent payment verified notification to chat_id {session['chat_id']} for order {session['order_id']}")
                        elif order.status == 'rejected':
                            reason = order.statusDetails or "No reason provided"
                            await self.application.bot.send_message(
                                chat_id=session['chat_id'],
                                text=self.get_prompt(session, 'payment_rejected').format(reason=reason)
                            )
                            logger.info(f"Sent payment rejected notification to chat_id {session['chat_id']} for order {session['order_id']}")
                        session['notified'] = True
            except Exception as e:
                logger.error(f"Error in poll_order_status_changes: {str(e)}")
            await asyncio.sleep(300)  # Poll every 5 minutes

    async def cache_user_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Cache user information when they interact with the bot"""
        if update.effective_user and update.effective_user.username:
            username = update.effective_user.username.lower()
            user_id = update.effective_user.id
            self.user_cache[username] = user_id
            logger.debug(f"Cached user: @{username} -> {user_id}")

    async def resolve_username_to_id(self, username: str, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Try to resolve a username to a user ID using multiple methods"""
        clean_username = username.replace('@', '').lower()
        full_username = f"@{clean_username}"
        
        logger.info(f"🔍 Attempting to resolve username: {full_username}")
        
        if clean_username in self.user_cache:
            logger.info(f"✅ Found {full_username} in cache: {self.user_cache[clean_username]}")
            return self.user_cache[clean_username]
        
        try:
            logger.info(f"🔄 Trying get_chat for {full_username}")
            chat = await context.bot.get_chat(full_username)
            if chat.type == 'private':
                user_id = chat.id
                self.user_cache[clean_username] = user_id
                logger.info(f"✅ Resolved {full_username} via get_chat: {user_id}")
                return user_id
            else:
                logger.warning(f"❌ {full_username} is not a private chat (type: {chat.type})")
        except telegram.error.BadRequest as e:
            logger.warning(f"❌ Could not resolve {full_username} via get_chat: {str(e)}")
        except Exception as e:
            logger.error(f"❌ Unexpected error with get_chat for {full_username}: {str(e)}")
        
        try:
            logger.info(f"🔄 Checking channel administrators for {full_username}")
            administrators = await context.bot.get_chat_administrators(private_channel_id)
            for admin in administrators:
                if admin.user.username and admin.user.username.lower() == clean_username:
                    user_id = admin.user.id
                    self.user_cache[clean_username] = user_id
                    logger.info(f"✅ Found {full_username} as channel admin: {user_id}")
                    return user_id
        except Exception as e:
            logger.warning(f"❌ Could not check channel administrators: {str(e)}")
        
        try:
            logger.info(f"🔄 Trying to get chat member info for {full_username}")
            member = await context.bot.get_chat_member(private_channel_id, full_username)
            if member and member.user:
                user_id = member.user.id
                self.user_cache[clean_username] = user_id
                logger.info(f"✅ Found {full_username} as channel member: {user_id}")
                return user_id
        except Exception as e:
            logger.warning(f"❌ Could not get chat member info: {str(e)}")
        
        logger.error(f"❌ Could not resolve username {full_username} using any method")
        raise ValueError(f"Could not resolve username {full_username} to user ID")

    def get_user_session(self, user_id: str) -> dict:
        """Get or create a user session"""
        if user_id not in self.user_sessions:
            self.user_sessions[user_id] = {
                'language': 'en',
                'candidate_data': {'availability': 'To be specified'},
                'careerObjectives': [],
                'skills': [],
                'education': [],
                'languages': [],
                'workExperiences': [],
                'certificationsAwards': [],
                'otherActivities': [],
                'projects': [],
                'current_field': None,
                'current_work_experience': {},
                'current_education': {},
                'current_skill': {},
                'current_certification': {},
                'current_project': {},
                'current_language': {}
            }
        return self.user_sessions[user_id]

    def get_prompt(self, session: dict, key: str) -> str:
        """Get the appropriate prompt based on the user's language"""
        return PROMPTS[session['language']][key]

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Send welcome message and prompt for language selection"""
        user = update.effective_user
        telegram_id = str(user.id)
        session = self.get_user_session(telegram_id)
        session['chat_id'] = update.effective_chat.id
        
        await update.message.reply_text(
            self.get_prompt(session, 'select_language'),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("English", callback_data="lang_en")],
                [InlineKeyboardButton("አማርኛ", callback_data="lang_am")]
            ])
        )
        return SELECT_LANGUAGE

    async def select_language(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle language selection"""
        query = update.callback_query
        await query.answer()
        
        telegram_id = str(query.from_user.id)
        session = self.get_user_session(telegram_id)
        session['chat_id'] = query.message.chat_id
        
        session['language'] = query.data.split('_')[1]
        
        candidate = Candidate.get_by_telegram_user_id(telegram_id)
        menu_text = self.get_prompt(session, 'welcome_back' if candidate else 'welcome')
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(self.get_prompt(session, 'create_new_cv'), callback_data="new_cv")],
            [InlineKeyboardButton(self.get_prompt(session, 'guide_video'), callback_data="guide_video")],
            [InlineKeyboardButton(self.get_prompt(session, 'samples'), callback_data="samples")]
        ])
        
        # Edit the query message and store the message ID
        message = await query.edit_message_text(
            menu_text,
            reply_markup=reply_markup
        )
        session['menu_message_id'] = message.message_id
        logger.info(f"Stored menu message ID {message.message_id} for user {telegram_id}")
        
        return START
    
    async def handle_returning_user_choice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle choices for users"""
        query = update.callback_query
        await query.answer()
        
        telegram_id = str(query.from_user.id)
        session = self.get_user_session(telegram_id)
        session['chat_id'] = query.message.chat_id
        
        if query.data == "new_cv":
            # Delete the previous menu message if it exists
            if 'menu_message_id' in session:
                try:
                    await context.bot.delete_message(
                        chat_id=session['chat_id'],
                        message_id=session['menu_message_id']
                    )
                    logger.info(f"Deleted menu message ID {session['menu_message_id']} for user {telegram_id}")
                    del session['menu_message_id']
                except Exception as e:
                    logger.warning(f"Failed to delete menu message ID {session['menu_message_id']} for user {telegram_id}: {str(e)}")
            await query.edit_message_text(
                self.get_prompt(session, 'welcome_new'), parse_mode="HTML"
            )
            session['current_field'] = 'firstName'
            return COLLECT_PERSONAL_INFO
        elif query.data == "guide_video":
            await self.send_tutorial_video(session['chat_id'], session, context)
            # Show the menu again after sending the video
            menu_text = self.get_prompt(session, 'choose_option')
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton(self.get_prompt(session, 'create_new_cv'), callback_data="new_cv")],
                [InlineKeyboardButton(self.get_prompt(session, 'guide_video'), callback_data="guide_video")],
                [InlineKeyboardButton(self.get_prompt(session, 'samples'), callback_data="samples")]
            ])
            message = await context.bot.send_message(
                chat_id=session['chat_id'],
                text=menu_text,
                reply_markup=reply_markup
            )
            session['menu_message_id'] = message.message_id
            logger.info(f"Stored menu message ID {message.message_id} for user {telegram_id} after guide video")
            return START
        elif query.data == "samples":
            await self.send_sample_cvs(session['chat_id'], session, context)
            # Show the menu again after sending samples
            menu_text = self.get_prompt(session, 'choose_option')
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton(self.get_prompt(session, 'create_new_cv'), callback_data="new_cv")],
                [InlineKeyboardButton(self.get_prompt(session, 'guide_video'), callback_data="guide_video")],
                [InlineKeyboardButton(self.get_prompt(session, 'samples'), callback_data="samples")]
            ])
            message = await context.bot.send_message(
                chat_id=session['chat_id'],
                text=menu_text,
                reply_markup=reply_markup
            )
            session['menu_message_id'] = message.message_id
            logger.info(f"Stored menu message ID {message.message_id} for user {telegram_id} after samples")
            return START
    
    async def send_tutorial_video(self, chat_id: int, session: dict, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Send tutorial video to user using stored file_id"""
        # Delete the previous menu message if it exists
        if 'menu_message_id' in session:
            try:
                await context.bot.delete_message(
                    chat_id=session['chat_id'],
                    message_id=session['menu_message_id']
                )
                logger.info(f"Deleted menu message ID {session['menu_message_id']} for user {session['chat_id']}")
                del session['menu_message_id']
            except Exception as e:
                logger.warning(f"Failed to delete menu message ID {session['menu_message_id']} for user {session['chat_id']}: {str(e)}")
        
        if tutorial_video_file_id:
            try:
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=tutorial_video_file_id,
                    caption=tutorial_video_caption or self.get_prompt(session, 'tutorial_message'),
                    parse_mode='HTML' if tutorial_video_caption else None
                )
                logger.info(f"Sent tutorial video with file_id {tutorial_video_file_id} to chat_id {chat_id}")
            except Exception as e:
                logger.error(f"Error sending tutorial video: {str(e)}")
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=self.get_prompt(session, 'error_message')
                )
        else:
            logger.warning("No tutorial video file_id provided")
            await context.bot.send_message(
                chat_id=chat_id,
                text=self.get_prompt(session, 'error_message')
            )
    
    
    async def send_sample_cvs(self, chat_id: int, session: dict, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Send sample CV files to user using stored file_ids"""
        # Delete the previous menu message if it exists
        if 'menu_message_id' in session:
            try:
                await context.bot.delete_message(
                    chat_id=session['chat_id'],
                    message_id=session['menu_message_id']
                )
                logger.info(f"Deleted menu message ID {session['menu_message_id']} for user {session['chat_id']}")
                del session['menu_message_id']
            except Exception as e:
                logger.warning(f"Failed to delete menu message ID {session['menu_message_id']} for user {session['chat_id']}: {str(e)}")
        
        if sample_cv_file_ids and sample_cv_file_ids[0]:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=self.get_prompt(session, 'sending_samples')
                )
                for file_id, caption in zip(sample_cv_file_ids, sample_cv_captions):
                    if file_id.strip():  # Skip empty file IDs
                        try:
                            # Assume file_id could be for document or photo
                            # Try sending as document first
                            try:
                                await context.bot.send_document(
                                    chat_id=chat_id,
                                    document=file_id,
                                    caption=caption or None,
                                    parse_mode='HTML' if caption else None
                                )
                                logger.info(f"Sent sample CV document with file_id {file_id} to chat_id {chat_id}")
                            except telegram.error.BadRequest:
                                # If document fails, try as photo
                                await context.bot.send_photo(
                                    chat_id=chat_id,
                                    photo=file_id,
                                    caption=caption or None,
                                    parse_mode='HTML' if caption else None
                                )
                                logger.info(f"Sent sample CV photo with file_id {file_id} to chat_id {chat_id}")
                        except Exception as e:
                            logger.error(f"Error sending sample CV with file_id {file_id}: {str(e)}")
                logger.info(f"Sent {len(sample_cv_file_ids)} sample CVs to chat_id {chat_id}")
            except Exception as e:
                logger.error(f"Error sending sample CVs: {str(e)}")
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=self.get_prompt(session, 'error_message')
                )
        else:
            logger.warning("No sample CV file_ids provided")
            await context.bot.send_message(
                chat_id=chat_id,
                text=self.get_prompt(session, 'error_message')
            )
    
    async def start_collecting_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle user choice to update profile or create new CV"""
        query = update.callback_query
        await query.answer()
        
        telegram_id = str(query.from_user.id)
        session = self.get_user_session(telegram_id)
        session['chat_id'] = query.message.chat_id
        
        if query.data == "update_profile":
            candidate = Candidate.get_by_telegram_user_id(telegram_id)
            if not candidate:
                logger.error(f"No candidate found for telegram_id {telegram_id}")
                await query.edit_message_text(self.get_prompt(session, 'error_message'))
                return START
            
            session['candidate_data'] = candidate.to_dict()
            session['candidate_data']['availability'] = session['candidate_data'].get('availability', 'To be specified')
            
            manager = CandidateManager(candidate.uid)
            profile = manager.get_complete_profile()
            logger.info(f"Retrieved profile for candidate {candidate.uid}: {len(profile.get('projects', []))} projects, {len(profile.get('workExperiences', []))} work experiences")
            
            # Validate and filter subcollections to ensure they belong to the candidate
            subcollections = {
                'workExperiences': profile.get('workExperiences', []),
                'education': profile.get('education', []),
                'skills': profile.get('skills', []),
                'careerObjectives': profile.get('careerObjectives', []),
                'certificationsAwards': profile.get('certificationsAwards', []),
                'projects': profile.get('projects', []),
                'languages': profile.get('languages', []),
                'otherActivities': profile.get('otherActivities', [])
            }
            
            for collection_name, items in subcollections.items():
                filtered_items = []
                for item in items:
                    doc_candidate_uid = item.get('candidate_uid')
                    if not doc_candidate_uid:
                        logger.warning(f"Discarded {collection_name} item with missing candidate_uid: {item.get('id', 'unknown')}")
                        continue
                    if doc_candidate_uid != candidate.uid:
                        logger.warning(f"Discarded {collection_name} item {item.get('id', 'unknown')} with mismatched candidate_uid {doc_candidate_uid}, expected {candidate.uid}")
                        continue
                    filtered_items.append(item)
                session[collection_name] = filtered_items
                if len(filtered_items) != len(items):
                    logger.warning(f"Filtered out {len(items) - len(filtered_items)} invalid {collection_name} entries for candidate {candidate.uid}")
                logger.info(f"Loaded {len(filtered_items)} {collection_name} for candidate {candidate.uid}")
            
            await query.edit_message_text(
                self.get_prompt(session, 'edit_section'),
                reply_markup=self.get_profile_sections_keyboard(session)
            )
            return START
        else:
            await query.edit_message_text(
                self.get_prompt(session, 'welcome_new')  , parse_mode="HTML"
            )
            session['current_field'] = 'firstName'
            return COLLECT_PERSONAL_INFO

    def get_profile_sections_keyboard(self, session: dict) -> InlineKeyboardMarkup:
        """Create keyboard for profile sections in the selected language"""
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(self.get_prompt(session, 'personal_info'), callback_data="edit_personal")],
            [InlineKeyboardButton(self.get_prompt(session, 'contact_info'), callback_data="edit_contact")],
            [InlineKeyboardButton(self.get_prompt(session, 'profile_image'), callback_data="edit_profile_image")],
            [InlineKeyboardButton(self.get_prompt(session, 'work_experience'), callback_data="edit_work")],
            [InlineKeyboardButton(self.get_prompt(session, 'education'), callback_data="edit_education")],
            [InlineKeyboardButton(self.get_prompt(session, 'skills'), callback_data="edit_skills")],
            [InlineKeyboardButton(self.get_prompt(session, 'career_objective'), callback_data="edit_career")],
            [InlineKeyboardButton(self.get_prompt(session, 'certifications'), callback_data="edit_certs")],
            [InlineKeyboardButton(self.get_prompt(session, 'projects'), callback_data="edit_projects")],
            [InlineKeyboardButton(self.get_prompt(session, 'languages'), callback_data="edit_languages")],
            [InlineKeyboardButton(self.get_prompt(session, 'other_activities'), callback_data="edit_activities")]
        ])

    async def collect_personal_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Collect personal information from candidate"""
        user = update.effective_user
        telegram_id = str(user.id)
        session = self.get_user_session(telegram_id)
        session['chat_id'] = update.effective_chat.id
        current_field = session['current_field']
        
        if current_field == 'firstName':
            session['candidate_data']['firstName'] = update.message.text
            session['current_field'] = 'middleName'
            await update.message.reply_text(self.get_prompt(session, 'middle_name'))
            return COLLECT_PERSONAL_INFO
        elif current_field == 'middleName':
            session['candidate_data']['middleName'] = update.message.text
            session['current_field'] = 'lastName'
            await update.message.reply_text(self.get_prompt(session, 'last_name'))
            return COLLECT_PERSONAL_INFO
        elif current_field == 'lastName':
            session['candidate_data']['lastName'] = update.message.text
            session['current_field'] = 'phoneNumber'
            await update.message.reply_text(self.get_prompt(session, 'phone_number'))
            return COLLECT_CONTACT_INFO

    async def collect_contact_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Collect contact information from candidate"""
        user = update.effective_user
        telegram_id = str(user.id)
        session = self.get_user_session(telegram_id)
        session['chat_id'] = update.effective_chat.id
        current_field = session['current_field']
        
        if current_field == 'phoneNumber':
            session['candidate_data']['phoneNumber'] = update.message.text
            session['current_field'] = 'emailAddress'
            await update.message.reply_text(self.get_prompt(session, 'email_address'))
            return COLLECT_CONTACT_INFO
        elif current_field == 'emailAddress':
            session['candidate_data']['emailAddress'] = update.message.text
            session['current_field'] = 'linkedinProfile'
            await update.message.reply_text(self.get_prompt(session, 'linkedin_profile'))
            return COLLECT_CONTACT_INFO
        elif current_field == 'linkedinProfile':
            session['candidate_data']['linkedinProfile'] = update.message.text if update.message.text.lower() != 'skip' else None
            session['current_field'] = 'city'
            await update.message.reply_text(self.get_prompt(session, 'city'))
            return COLLECT_CONTACT_INFO
        elif current_field == 'city':
            session['candidate_data']['city'] = update.message.text
            session['current_field'] = 'country'
            await update.message.reply_text(self.get_prompt(session, 'country'))
            return COLLECT_CONTACT_INFO
        elif current_field == 'country':
            session['candidate_data']['country'] = update.message.text
            session['current_field'] = None
            await update.message.reply_text(self.get_prompt(session, 'profile_image_prompt'))
            return COLLECT_PROFILE_IMAGE

    async def collect_profile_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Collect profile image from candidate"""
        telegram_id = str(update.effective_user.id)
        session = self.get_user_session(telegram_id)
        session['chat_id'] = update.effective_chat.id
        
        max_size = 5 * 1024 * 1024
        allowed_mime_types = ['image/jpeg', 'image/png', 'application/pdf']
        allowed_extensions = ['jpg', 'jpeg', 'png', 'pdf']
        
        if update.message.text and update.message.text.lower() == 'skip':
            await update.message.reply_text(
                self.get_prompt(session, 'profile_image_skip'),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(self.get_prompt(session, 'continue_professional'), callback_data="continue_professional")]
                ])
            )
            return COLLECT_PROFILE_IMAGE
        
        try:
            if update.message.photo:
                photo = update.message.photo[-1]
                file = await photo.get_file()
                if file.file_size > max_size:
                    await update.message.reply_text(self.get_prompt(session, 'file_too_large'))
                    return COLLECT_PROFILE_IMAGE
                file_url = file.file_path
                user = update.effective_user
                user_info = f"👤 User: {user.first_name or ''} {user.last_name or ''}".strip()
                if user.username:
                    user_info += f" (@{user.username})"
                user_info += f"\n🆔 User ID: {telegram_id}"
                await context.bot.send_photo(
                    chat_id=private_channel_id,
                    photo=photo.file_id,
                    caption=f"📸 Profile Image Received\n\n{user_info}"
                )
                session['candidate_data']['profileImageUrl'] = file_url
                logger.info(f"Profile image uploaded for user {telegram_id}")
            elif update.message.document:
                document = update.message.document
                if document.file_size > max_size:
                    await update.message.reply_text(self.get_prompt(session, 'file_too_large'))
                    return COLLECT_PROFILE_IMAGE
                if document.mime_type not in allowed_mime_types:
                    await update.message.reply_text(self.get_prompt(session, 'invalid_file_type'))
                    return COLLECT_PROFILE_IMAGE
                if document.file_name:
                    extension = document.file_name.split('.')[-1].lower()
                    if extension not in allowed_extensions:
                        await update.message.reply_text(self.get_prompt(session, 'invalid_file_type'))
                        return COLLECT_PROFILE_IMAGE
                file = await document.get_file()
                file_url = file.file_path
                user = update.effective_user
                user_info = f"👤 User: {user.first_name or ''} {user.last_name or ''}".strip()
                if user.username:
                    user_info += f" (@{user.username})"
                user_info += f"\n🆔 User ID: {telegram_id}"
                await context.bot.send_document(
                    chat_id=private_channel_id,
                    document=document.file_id,
                    caption=f"📸 Profile Image Received\n\n{user_info}"
                )
                session['candidate_data']['profileImageUrl'] = file_url
                logger.info(f"Profile image (document) uploaded for user {telegram_id}")
            else:
                await update.message.reply_text(self.get_prompt(session, 'invalid_file_type'))
                return COLLECT_PROFILE_IMAGE
            
            await update.message.reply_text(
                self.get_prompt(session, 'profile_image_success'),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(self.get_prompt(session, 'continue_professional'), callback_data="continue_professional")]
                ])
            )
            return COLLECT_PROFILE_IMAGE
        except Exception as e:
            logger.error(f"Error handling profile image upload: {str(e)}")
            await update.message.reply_text(self.get_prompt(session, 'error_message'))
            return COLLECT_PROFILE_IMAGE

    async def handle_profile_image_choice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle profile image choice (skip or continue)"""
        query = update.callback_query
        await query.answer()
        
        telegram_id = str(query.from_user.id)
        session = self.get_user_session(telegram_id)
        session['chat_id'] = query.message.chat_id
        
        if query.data == "continue_professional":
            session['current_field'] = 'work_jobTitle'
            session['current_work_experience'] = {}
            await query.edit_message_text(self.get_prompt(session, 'job_title_with_skip'))
            return COLLECT_PROFESSIONAL_INFO

    async def collect_professional_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Collect professional information from candidate"""
        user = update.effective_user
        telegram_id = str(user.id)
        session = self.get_user_session(telegram_id)
        session['chat_id'] = update.effective_chat.id
        current_field = session['current_field']
        
        if current_field == 'work_jobTitle':
            if update.message.text.lower() == 'skip':
                session['workExperiences'] = []  # Set empty work experiences
                session['current_field'] = 'edu_degreeName'
                session['current_education'] = {}
                await update.message.reply_text(self.get_prompt(session, 'degree_name'))
                return COLLECT_EDUCATION
            
            # Normal flow continues
            session['current_work_experience']['jobTitle'] = update.message.text
            session['current_field'] = 'work_companyName'
            await update.message.reply_text(self.get_prompt(session, 'company_name'))
            return COLLECT_PROFESSIONAL_INFO
        elif current_field == 'work_companyName':
            session['current_work_experience']['companyName'] = update.message.text
            session['current_field'] = 'work_location'
            await update.message.reply_text(self.get_prompt(session, 'work_location'))
            return COLLECT_PROFESSIONAL_INFO
        elif current_field == 'work_location':
            session['current_work_experience']['location'] = update.message.text
            session['current_field'] = 'work_description'
            await update.message.reply_text(self.get_prompt(session, 'work_description'))
            return COLLECT_PROFESSIONAL_INFO
        elif current_field == 'work_description':
            session['current_work_experience']['description'] = update.message.text
            session['workExperiences'].append(session['current_work_experience'].copy())
            session['current_work_experience'] = {}
            await update.message.reply_text(
                self.get_prompt(session, 'add_another_work'),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(self.get_prompt(session, 'add_another'), callback_data="add_another_work")],
                    [InlineKeyboardButton(self.get_prompt(session, 'continue'), callback_data="continue_education")]
                ])
            )
            return COLLECT_PROFESSIONAL_INFO

    async def handle_professional_info_choice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle the user's choice to add another work experience or continue"""
        query = update.callback_query
        await query.answer()
        
        telegram_id = str(query.from_user.id)
        session = self.get_user_session(telegram_id)
        session['chat_id'] = query.message.chat_id
        
        if query.data == "add_another_work":
            session['current_field'] = 'work_jobTitle'
            await query.edit_message_text(self.get_prompt(session, 'job_title'))
            return COLLECT_PROFESSIONAL_INFO
        elif query.data == "continue_education":
            session['current_field'] = 'edu_degreeName'
            session['current_education'] = {}
            await query.edit_message_text(self.get_prompt(session, 'degree_name'))
            return COLLECT_EDUCATION

    async def collect_education(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Collect education information from candidate"""
        user = update.effective_user
        telegram_id = str(user.id)
        session = self.get_user_session(telegram_id)
        session['chat_id'] = update.effective_chat.id
        current_field = session['current_field']
        
        if current_field == 'edu_degreeName':
            session['current_education']['degreeName'] = update.message.text
            session['current_field'] = 'edu_institutionName'
            await update.message.reply_text(self.get_prompt(session, 'institution_name'))
            return COLLECT_EDUCATION
        elif current_field == 'edu_institutionName':
            session['current_education']['institutionName'] = update.message.text
            session['current_field'] = 'edu_gpa'
            await update.message.reply_text(self.get_prompt(session, 'gpa'))
            return COLLECT_EDUCATION
        elif current_field == 'edu_gpa':
            session['current_education']['gpa'] = update.message.text if update.message.text.lower() != 'skip' else None
            session['current_field'] = 'edu_description'
            await update.message.reply_text(self.get_prompt(session, 'edu_description'))
            return COLLECT_EDUCATION
        elif current_field == 'edu_description':
            session['current_education']['description'] = update.message.text
            session['current_field'] = 'edu_achievementsHonors'
            await update.message.reply_text(self.get_prompt(session, 'achievements_honors'))
            return COLLECT_EDUCATION
        elif current_field == 'edu_achievementsHonors':
            session['current_education']['achievementsHonors'] = update.message.text if update.message.text.lower() != 'skip' else None
            session['education'].append(session['current_education'].copy())
            session['current_education'] = {}
            await update.message.reply_text(
                self.get_prompt(session, 'add_another_edu'),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(self.get_prompt(session, 'add_another'), callback_data="add_another_edu")],
                    [InlineKeyboardButton(self.get_prompt(session, 'continue'), callback_data="continue_skills")]
                ])
            )
            return COLLECT_EDUCATION

    async def handle_education_choice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle the user's choice to add another education entry or continue"""
        query = update.callback_query
        await query.answer()
        
        telegram_id = str(query.from_user.id)
        session = self.get_user_session(telegram_id)
        session['chat_id'] = query.message.chat_id
        
        if query.data == 'add_another_edu':
            session['current_field'] = 'edu_degreeName'
            await query.edit_message_text(self.get_prompt(session, 'degree_name'))
            return COLLECT_EDUCATION
        elif query.data == 'continue_skills':
            session['skills'] = []  # Set empty skills list
            await query.edit_message_text(self.get_prompt(session, 'career_summary'))
            return COLLECT_CAREER_OBJECTIVE

    async def collect_skills(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Collect skills from candidate"""
        user = update.effective_user
        telegram_id = str(user.id)
        session = self.get_user_session(telegram_id)
        session['chat_id'] = update.effective_chat.id
        current_field = session['current_field']
        
        if current_field == 'skill_skillName':
            session['current_skill']['skillName'] = update.message.text
            session['current_field'] = 'skill_proficiency'
            await update.message.reply_text(self.get_prompt(session, 'skill_proficiency'))
            return COLLECT_SKILLS
        elif current_field == 'skill_proficiency':
            session['current_skill']['proficiency'] = update.message.text
            session['skills'].append(session['current_skill'].copy())
            session['current_skill'] = {}
            await update.message.reply_text(
                self.get_prompt(session, 'add_another_skill'),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(self.get_prompt(session, 'add_another'), callback_data="add_another_skill")],
                    [InlineKeyboardButton(self.get_prompt(session, 'continue'), callback_data="continue_career")]
                ])
            )
            return COLLECT_SKILLS

    async def handle_skills_choice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle the user's choice to add another skill or continue"""
        query = update.callback_query
        await query.answer()
        
        telegram_id = str(query.from_user.id)
        session = self.get_user_session(telegram_id)
        session['chat_id'] = query.message.chat_id
        
        if query.data == "add_another_skill":
            session['current_field'] = 'skill_skillName'
            await query.edit_message_text(self.get_prompt(session, 'skill_name'))
            return COLLECT_SKILLS
        elif query.data == "continue_career":
            await query.edit_message_text(self.get_prompt(session, 'career_summary'))
            return COLLECT_CAREER_OBJECTIVE

    async def collect_career_objective(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Collect career objective from candidate"""
        user = update.effective_user
        telegram_id = str(user.id)
        session = self.get_user_session(telegram_id)
        session['chat_id'] = update.effective_chat.id
        
        if update.message.text.lower() != 'skip':
            session['careerObjectives'].append({
                'summaryText': update.message.text
            })
        
        await update.message.reply_text(self.get_prompt(session, 'certificate_name'))
        session['current_field'] = 'cert_certificateName'
        session['current_certification'] = {}
        return COLLECT_CERTIFICATIONS

    async def collect_certifications(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Collect certifications from candidate"""
        user = update.effective_user
        telegram_id = str(user.id)
        session = self.get_user_session(telegram_id)
        session['chat_id'] = update.effective_chat.id
        current_field = session['current_field']
        
        if current_field == 'cert_certificateName':
            if update.message.text.lower() == 'skip':
                await update.message.reply_text(
                    self.get_prompt(session, 'add_another_cert'),
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton(self.get_prompt(session, 'add_another'), callback_data="add_another_cert")],
                        [InlineKeyboardButton(self.get_prompt(session, 'continue'), callback_data="continue_projects")]
                    ])
                )
                return COLLECT_CERTIFICATIONS
            session['current_certification']['certificateName'] = update.message.text
            session['current_field'] = 'cert_issuer'
            await update.message.reply_text(self.get_prompt(session, 'issuer'))
            return COLLECT_CERTIFICATIONS
        elif current_field == 'cert_issuer':
            session['current_certification']['issuer'] = update.message.text
            session['certificationsAwards'].append(session['current_certification'].copy())
            session['current_certification'] = {}
            await update.message.reply_text(
                self.get_prompt(session, 'add_another_cert'),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(self.get_prompt(session, 'add_another'), callback_data="add_another_cert")],
                    [InlineKeyboardButton(self.get_prompt(session, 'continue'), callback_data="continue_projects")]
                ])
            )
            return COLLECT_CERTIFICATIONS

    async def handle_certifications_choice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle the user's choice to add another certification or continue"""
        query = update.callback_query
        await query.answer()
        
        telegram_id = str(query.from_user.id)
        session = self.get_user_session(telegram_id)
        session['chat_id'] = query.message.chat_id
        
        if query.data == "add_another_cert":
            session['current_field'] = 'cert_certificateName'
            await query.edit_message_text(self.get_prompt(session, 'certificate_name'))
            return COLLECT_CERTIFICATIONS
        elif query.data == "continue_projects":
            session['current_field'] = 'project_projectTitle'
            session['current_project'] = {}
            await query.edit_message_text(self.get_prompt(session, 'project_title'))
            return COLLECT_PROJECTS

    async def collect_projects(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Collect projects from candidate"""
        user = update.effective_user
        telegram_id = str(user.id)
        session = self.get_user_session(telegram_id)
        session['chat_id'] = update.effective_chat.id
        current_field = session['current_field']
        
        if current_field == 'project_projectTitle':
            session['current_project']['projectTitle'] = update.message.text
            session['current_field'] = 'project_description'
            await update.message.reply_text(self.get_prompt(session, 'project_description'))
            return COLLECT_PROJECTS
        elif current_field == 'project_description':
            session['current_project']['description'] = update.message.text
            session['current_field'] = 'project_projectLink'
            await update.message.reply_text(self.get_prompt(session, 'project_link'))
            return COLLECT_PROJECTS
        elif current_field == 'project_projectLink':
            if update.message.text.lower() != 'skip':
                session['current_project']['projectLink'] = update.message.text
            session['projects'].append(session['current_project'].copy())
            session['current_project'] = {}
            await update.message.reply_text(
                self.get_prompt(session, 'add_another_project'),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(self.get_prompt(session, 'add_another'), callback_data="add_another_project")],
                    [InlineKeyboardButton(self.get_prompt(session, 'continue'), callback_data="continue_languages")]
                ])
            )
            return COLLECT_PROJECTS

    async def handle_projects_choice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle the user's choice to add another project or continue"""
        query = update.callback_query
        await query.answer()
        
        telegram_id = str(query.from_user.id)
        session = self.get_user_session(telegram_id)
        session['chat_id'] = query.message.chat_id
        
        if query.data == "add_another_project":
            session['current_field'] = 'project_projectTitle'
            await query.edit_message_text(self.get_prompt(session, 'project_title'))
            return COLLECT_PROJECTS
        elif query.data == "continue_languages":
            session['current_field'] = 'lang_languageName'
            session['current_language'] = {}
            await query.edit_message_text(self.get_prompt(session, 'language_name'))
            return COLLECT_LANGUAGES

    async def collect_languages(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Collect languages from candidate"""
        user = update.effective_user
        telegram_id = str(user.id)
        session = self.get_user_session(telegram_id)
        session['chat_id'] = update.effective_chat.id
        current_field = session['current_field']
        
        if current_field == 'lang_languageName':
            session['current_language']['languageName'] = update.message.text
            session['current_field'] = 'lang_proficiencyLevel'
            await update.message.reply_text(self.get_prompt(session, 'language_proficiency'))
            return COLLECT_LANGUAGES
        elif current_field == 'lang_proficiencyLevel':
            session['current_language']['proficiencyLevel'] = update.message.text
            session['languages'].append(session['current_language'].copy())
            session['current_language'] = {}
            await update.message.reply_text(
                self.get_prompt(session, 'add_another_language'),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(self.get_prompt(session, 'add_another'), callback_data="add_another_language")],
                    [InlineKeyboardButton(self.get_prompt(session, 'continue'), callback_data="continue_activities")]
                ])
            )
            return COLLECT_LANGUAGES

    async def handle_languages_choice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle the user's choice to add another language or continue"""
        query = update.callback_query
        await query.answer()
        
        telegram_id = str(query.from_user.id)
        session = self.get_user_session(telegram_id)
        session['chat_id'] = query.message.chat_id
        
        if query.data == "add_another_language":
            session['current_field'] = 'lang_languageName'
            await query.edit_message_text(self.get_prompt(session, 'language_name'))
            return COLLECT_LANGUAGES
        elif query.data == "continue_activities":
            await query.edit_message_text(self.get_prompt(session, 'activities'))
            return COLLECT_ACTIVITIES

    async def collect_activities(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Collect other activities from candidate"""
        user = update.effective_user
        telegram_id = str(user.id)
        session = self.get_user_session(telegram_id)
        session['chat_id'] = update.effective_chat.id
        
        if update.message.text.lower() != 'skip':
            session['otherActivities'].append({
                'activityType': 'Other',
                'description': update.message.text
            })
        
        summary = self.get_prompt(session, 'summary_header')
        summary += f"{self.get_prompt(session, 'summary_name')}: {session['candidate_data'].get('firstName', '')} {session['candidate_data'].get('middleName', '')} {session['candidate_data'].get('lastName', '')}\n"
        
        summary += f"{self.get_prompt(session, 'summary_contact')}: {session['candidate_data'].get('phoneNumber', '')} | {session['candidate_data'].get('emailAddress', '')}\n"
        summary += f"{self.get_prompt(session, 'summary_location')}: {session['candidate_data'].get('city', '')}, {session['candidate_data'].get('country', '')}\n"
        summary += f"{self.get_prompt(session, 'summary_availability')}: {session['candidate_data'].get('availability', 'To be specified')}\n\n"
        
        summary += f"{self.get_prompt(session, 'summary_work')}:\n"
        for exp in session['workExperiences']:
            summary += f"- {exp.get('jobTitle', 'N/A')} at {exp.get('companyName', 'N/A')}, {exp.get('location', 'N/A')}\n"
            summary += f"  {self.get_prompt(session, 'summary_responsibilities')}: {exp.get('description', 'N/A')}\n"
        
        summary += f"\n{self.get_prompt(session, 'summary_education')}:\n"
        for edu in session['education']:
            summary += f"- {edu.get('degreeName', 'N/A')} from {edu.get('institutionName', 'N/A')}\n"
            summary += f"  {self.get_prompt(session, 'summary_gpa')}: {edu.get('gpa', 'N/A')}\n"
            summary += f"  {self.get_prompt(session, 'summary_edu_description')}: {edu.get('description', 'N/A')}\n"
            summary += f"  {self.get_prompt(session, 'summary_achievements')}: {edu.get('achievementsHonors', 'None')}\n"
        
        if session['skills']:
            summary += f"\n{self.get_prompt(session, 'summary_skills')}:\n"
            for skill in session['skills']:
                summary += f"- {skill.get('skillName', 'N/A')} ({self.get_prompt(session, 'summary_proficiency')}: {skill.get('proficiency', 'N/A')})\n"
        
        summary += f"\n{self.get_prompt(session, 'summary_certifications')}:\n"
        for cert in session['certificationsAwards']:
            summary += f"- {cert.get('certificateName', 'N/A')} from {cert.get('issuer', 'N/A')}\n"
        
        summary += f"\n{self.get_prompt(session, 'summary_projects')}:\n"
        for project in session['projects']:
            summary += f"- {project.get('projectTitle', 'N/A')}\n"
            summary += f"  {self.get_prompt(session, 'summary_edu_description')}: {project.get('description', 'N/A')}\n"
            if project.get('projectLink'):
                summary += f"  {self.get_prompt(session, 'summary_project_link')}: {project.get('projectLink')}\n"
        
        summary += f"\n{self.get_prompt(session, 'summary_languages')}:\n"
        for lang in session['languages']:
            summary += f"- {lang.get('languageName', 'N/A')} ({self.get_prompt(session, 'summary_proficiency')}: {lang.get('proficiencyLevel', 'N/A')})\n"
        
        keyboard = [
            [
                InlineKeyboardButton(self.get_prompt(session, 'confirm'), callback_data="confirm_yes"),
                InlineKeyboardButton(self.get_prompt(session, 'edit'), callback_data="edit_no")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Store the summary message ID for later deletion
        summary_message = await update.message.reply_text(
            text=summary,
            reply_markup=reply_markup
        )
        session['summary_message_id'] = summary_message.message_id
        return CONFIRM_ORDER

    async def confirm_order(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle order confirmation"""
        query = update.callback_query
        await query.answer()
        
        telegram_id = str(query.from_user.id)
        session = self.get_user_session(telegram_id)
        session['chat_id'] = query.message.chat_id
        
        if query.data == "confirm_yes":
            # Send "Saving..." message and store its ID
            saving_message = await query.message.reply_text("Saving...")
            session['saving_message_id'] = saving_message.message_id
            
            # Delete the summary message
            try:
                if 'summary_message_id' in session:
                    await context.bot.delete_message(
                        chat_id=session['chat_id'],
                        message_id=session['summary_message_id']
                    )
                    del session['summary_message_id']
                    logger.info(f"Deleted summary message for user {telegram_id}")
            except Exception as e:
                logger.error(f"Error deleting summary message for user {telegram_id}: {str(e)}")
            
            candidate = Candidate.get_by_telegram_user_id(telegram_id)
            if not candidate:
                candidate = Candidate(
                    uid=str(uuid.uuid4()),
                    telegramUserId=telegram_id,
                    **session['candidate_data']
                )
                candidate.save()
                logger.info(f"Created new candidate {candidate.uid} for telegram_id {telegram_id}")
            else:
                for key, value in session['candidate_data'].items():
                    setattr(candidate, key, value)
                candidate.save()
                logger.info(f"Updated candidate {candidate.uid} for telegram_id {telegram_id}")
            
            # Clear existing subcollections for the candidate to avoid duplicates
            subcollection_models = {
                'workExperiences': WorkExperience,
                'education': Education,
                'skills': Skill,
                'careerObjectives': CareerObjective,
                'certificationsAwards': CertificationAward,
                'projects': Project,
                'languages': Language,
                'otherActivities': OtherActivity
            }
            for collection_name, model in subcollection_models.items():
                collection_ref = db.collection('candidates').document(candidate.uid).collection(collection_name)
                docs = collection_ref.stream()
                for doc in docs:
                    doc.reference.delete()
                logger.info(f"Cleared {collection_name} for candidate {candidate.uid}")
            
            # Save subcollection data with explicit candidate_uid
            for work_exp in session['workExperiences']:
                work_exp['candidate_uid'] = candidate.uid
                WorkExperience(**work_exp).save()
                logger.info(f"Saved WorkExperience for candidate {candidate.uid}: {work_exp.get('jobTitle')}")
            
            for edu in session['education']:
                edu['candidate_uid'] = candidate.uid
                Education(**edu).save()
                logger.info(f"Saved Education for candidate {candidate.uid}: {edu.get('degreeName')}")
            
            for skill in session['skills']:
                skill['candidate_uid'] = candidate.uid
                Skill(**skill).save()
                logger.info(f"Saved Skill for candidate {candidate.uid}: {skill.get('skillName')}")
            
            for career_obj in session['careerObjectives']:
                career_obj['candidate_uid'] = candidate.uid
                CareerObjective(**career_obj).save()
                logger.info(f"Saved CareerObjective for candidate {candidate.uid}: {career_obj.get('summaryText')[:50]}...")
            
            for cert in session['certificationsAwards']:
                cert['candidate_uid'] = candidate.uid
                CertificationAward(**cert).save()
                logger.info(f"Saved CertificationAward for candidate {candidate.uid}: {cert.get('certificateName')}")
            
            for project in session['projects']:
                project['candidate_uid'] = candidate.uid
                Project(**project).save()
                logger.info(f"Saved Project for candidate {candidate.uid}: {project.get('projectTitle')}")
            
            for lang in session['languages']:
                lang['candidate_uid'] = candidate.uid
                Language(**lang).save()
                logger.info(f"Saved Language for candidate {candidate.uid}: {lang.get('languageName')}")
            
            for activity in session['otherActivities']:
                activity['candidate_uid'] = candidate.uid
                OtherActivity(**activity).save()
                logger.info(f"Saved OtherActivity for candidate {candidate.uid}: {activity.get('description')[:50]}...")
            
            order = Order(
                id=str(uuid.uuid4()),
                candidateId=candidate.uid,
                telegramUserId=telegram_id,
                status="awaiting_payment"
            )
            order.save()
            logger.info(f"Created Order {order.id} for candidate {candidate.uid}")
            
            session['order_id'] = order.id
            session['notified'] = False
            
            # Delete the "Saving..." message
            try:
                await context.bot.delete_message(
                    chat_id=session['chat_id'],
                    message_id=session['saving_message_id']
                )
                del session['saving_message_id']
                logger.info(f"Deleted saving message for user {telegram_id}")
            except Exception as e:
                logger.error(f"Error deleting saving message for user {telegram_id}: {str(e)}")

            # In confirm_order method when transitioning to PAYMENT
            session['from_main_flow'] = True
            
            # Send payment instructions
            await context.bot.send_message(
                chat_id=session['chat_id'],
                text=self.get_prompt(session, 'payment_instructions'),
                parse_mode="HTML"
            )
            return PAYMENT
        elif query.data == "edit_no":
            logger.info(f"Edit button clicked by user {telegram_id}, restarting data entry from first name")
            # Reset session data but preserve language and chat_id
            self.user_sessions[telegram_id] = {
                'language': session['language'],
                'chat_id': session['chat_id'],
                'candidate_data': {'availability': 'To be specified'},
                'careerObjectives': [],
                'skills': [],
                'education': [],
                'languages': [],
                'workExperiences': [],
                'certificationsAwards': [],
                'otherActivities': [],
                'projects': [],
                'current_field': 'firstName',
                'current_work_experience': {},
                'current_education': {},
                'current_skill': {},
                'current_certification': {},
                'current_project': {},
                'current_language': {}
            }
            await query.edit_message_text(self.get_prompt(self.user_sessions[telegram_id], 'first_name'))
            return COLLECT_PERSONAL_INFO

    async def edit_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle request to edit specific sections of information"""
        query = update.callback_query
        await query.answer()
        
        telegram_id = str(query.from_user.id)
        session = self.get_user_session(telegram_id)
        session['chat_id'] = query.message.chat_id
        
        logger.info(f"Edit section selected by user {telegram_id}: {query.data}")
        
        if query.data == "edit_no":
            logger.info(f"Edit button clicked by user {telegram_id}, restarting data entry from first name")
            # Reset session data but preserve language and chat_id
            self.user_sessions[telegram_id] = {
                'language': session['language'],
                'chat_id': session['chat_id'],
                'candidate_data': {'availability': 'To be specified'},
                'careerObjectives': [],
                'skills': [],
                'education': [],
                'languages': [],
                'workExperiences': [],
                'certificationsAwards': [],
                'otherActivities': [],
                'projects': [],
                'current_field': 'firstName',
                'current_work_experience': {},
                'current_education': {},
                'current_skill': {},
                'current_certification': {},
                'current_project': {},
                'current_language': {}
            }
            await query.edit_message_text(self.get_prompt(self.user_sessions[telegram_id], 'first_name'))
            return COLLECT_PERSONAL_INFO
        elif query.data == "edit_personal":
            session['current_field'] = 'firstName'
            await query.edit_message_text(self.get_prompt(session, 'first_name'))
            return COLLECT_PERSONAL_INFO
        elif query.data == "edit_contact":
            session['current_field'] = 'phoneNumber'
            await query.edit_message_text(self.get_prompt(session, 'phone_number'))
            return COLLECT_CONTACT_INFO
        elif query.data == "edit_profile_image":
            session['current_field'] = None
            await query.edit_message_text(self.get_prompt(session, 'profile_image_prompt'))
            return COLLECT_PROFILE_IMAGE
        elif query.data == "edit_work":
            session['workExperiences'] = []
            session['current_field'] = 'work_jobTitle'
            await query.edit_message_text(self.get_prompt(session, 'job_title'))
            return COLLECT_PROFESSIONAL_INFO
        elif query.data == "edit_education":
            session['education'] = []
            session['current_field'] = 'edu_degreeName'
            session['current_education'] = {}
            await query.edit_message_text(self.get_prompt(session, 'degree_name'))
            return COLLECT_EDUCATION
        elif query.data == "edit_skills":
            session['skills'] = []
            session['current_field'] = 'skill_skillName'
            session['current_skill'] = {}
            await query.edit_message_text(self.get_prompt(session, 'skill_name'))
            return COLLECT_SKILLS
        elif query.data == "edit_career":
            session['careerObjectives'] = []
            await query.edit_message_text(self.get_prompt(session, 'career_summary'))
            return COLLECT_CAREER_OBJECTIVE
        elif query.data == "edit_certs":
            session['certificationsAwards'] = []
            session['current_field'] = 'cert_certificateName'
            session['current_certification'] = {}
            await query.edit_message_text(self.get_prompt(session, 'certificate_name'))
            return COLLECT_CERTIFICATIONS
        elif query.data == "edit_projects":
            session['projects'] = []
            session['current_field'] = 'project_projectTitle'
            session['current_project'] = {}
            await query.edit_message_text(self.get_prompt(session, 'project_title'))
            return COLLECT_PROJECTS
        elif query.data == "edit_languages":
            session['languages'] = []
            session['current_field'] = 'lang_languageName'
            session['current_language'] = {}
            await query.edit_message_text(self.get_prompt(session, 'language_name'))
            return COLLECT_LANGUAGES
        elif query.data == "edit_activities":
            session['otherActivities'] = []
            await query.edit_message_text(self.get_prompt(session, 'activities'))
            return COLLECT_ACTIVITIES

    async def handle_payment_screenshot(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle payment screenshot upload"""
        telegram_id = str(update.effective_user.id)
        session = self.get_user_session(telegram_id)
        session['chat_id'] = update.effective_chat.id
        
        max_size = 5 * 1024 * 1024
        allowed_mime_types = ['image/jpeg', 'image/png', 'application/pdf']
        allowed_extensions = ['jpg', 'jpeg', 'png', 'pdf']
        
        try:
            user = update.effective_user
            user_info = f"👤 User: {user.first_name or ''} {user.last_name or ''}".strip()
            if user.username:
                user_info += f" (@{user.username})"
            user_info += f"\n🆔 User ID: {telegram_id}"
            user_info += f"\n📋 Order ID: {session.get('order_id', 'N/A')}"
            
            # Load candidate data for phone number if not in session
            if 'candidate_data' not in session or not session['candidate_data'].get('phoneNumber'):
                candidate = Candidate.get_by_telegram_user_id(telegram_id)
                if candidate:
                    session['candidate_data'] = candidate.to_dict()
            
            user_info += f"\n📞 Phone: {session.get('candidate_data', {}).get('phoneNumber', 'N/A')}"
            
            keyboard = [
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f"approve_{telegram_id}_{session['order_id']}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"reject_{telegram_id}_{session['order_id']}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if update.message.photo:
                photo = update.message.photo[-1]
                file = await photo.get_file()
                if file.file_size > max_size:
                    await update.message.reply_text(self.get_prompt(session, 'file_too_large'))
                    return PAYMENT
                file_url = file.file_path
                
                # Check if this is a retry (not from main flow)
                retry_text = " (RETRY)" if session.get('order_id') and not session.get('from_main_flow', False) else ""
                
                await context.bot.send_photo(
                    chat_id=private_channel_id,
                    photo=photo.file_id,
                    caption=f"💳 Payment Screenshot Received{retry_text}\n\n{user_info}",
                    reply_markup=reply_markup
                )
                logger.info(f"Payment screenshot forwarded to private channel for user {telegram_id}, order {session['order_id']}")
                
            elif update.message.document:
                document = update.message.document
                if document.file_size > max_size:
                    await update.message.reply_text(self.get_prompt(session, 'file_too_large'))
                    return PAYMENT
                if document.mime_type not in allowed_mime_types:
                    await update.message.reply_text(self.get_prompt(session, 'invalid_file_type'))
                    return PAYMENT
                if document.file_name:
                    extension = document.file_name.split('.')[-1].lower()
                    if extension not in allowed_extensions:
                        await update.message.reply_text(self.get_prompt(session, 'invalid_file_type'))
                        return PAYMENT
                file = await document.get_file()
                file_url = file.file_path
                
                # Check if this is a retry (not from main flow)
                retry_text = " (RETRY)" if session.get('order_id') and not session.get('from_main_flow', False) else ""
                
                await context.bot.send_document(
                    chat_id=private_channel_id,
                    document=document.file_id,
                    caption=f"💳 Payment Document Received{retry_text}\n\n{user_info}",
                    reply_markup=reply_markup
                )
                logger.info(f"Payment document forwarded to private channel for user {telegram_id}, order {session['order_id']}")
            else:
                await update.message.reply_text(self.get_prompt(session, 'payment_instructions'), parse_mode="HTML")
                return PAYMENT
            
            order = Order.get_by_id(session['order_id'])
            if not order:
                logger.error(f"Order {session['order_id']} not found for telegram_id {telegram_id}")
                await update.message.reply_text(self.get_prompt(session, 'error_message'))
                return PAYMENT
            
            # Update order with new payment screenshot
            order.paymentScreenshotUrl = file_url
            order.update_status("pending_verification", status_details="Payment screenshot submitted, awaiting admin verification")
            order.save()
            
            await update.message.reply_text(self.get_prompt(session, 'payment_confirmation'))
            
            return ConversationHandler.END
            
        except Exception as e:
            logger.error(f"Error in handle_payment_screenshot: {str(e)}")
            await update.message.reply_text(self.get_prompt(session, 'error_message'))
            return PAYMENT

    async def handle_admin_response(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle admin approval/rejection responses"""
        query = update.callback_query
        await query.answer()
        
        try:
            action, telegram_id, order_id = query.data.split('_', 2)
            
            session = self.get_user_session(telegram_id)
            if 'chat_id' not in session:
                logger.error(f"No chat_id found for telegram_id {telegram_id} in session")
                await query.message.reply_text("Error: User session not found.")
                return
            
            order = Order.get_by_id(order_id)
            if not order:
                logger.error(f"Order {order_id} not found for telegram_id {telegram_id}")
                await query.message.reply_text("Error: Order not found.")
                return
            
            if action == "approve":
                try:
                    order.approve_payment()
                    await context.bot.send_message(
                        chat_id=session['chat_id'],
                        text=self.get_prompt(session, 'payment_verified')
                    )
                    await query.edit_message_caption(
                        caption=f"{query.message.caption}\n\n✅ **APPROVED** by {query.from_user.first_name or 'Admin'}",
                        reply_markup=None
                    )
                    logger.info(f"Payment approved for user {telegram_id}, order {order_id} by admin {query.from_user.id}")
                    session['notified'] = True
                except Exception as e:
                    logger.error(f"Error sending approval message to user {telegram_id}: {str(e)}")
                    await query.edit_message_caption(
                        caption=f"{query.message.caption}\n\n✅ **APPROVED** by {query.from_user.first_name or 'Admin'} (Error sending notification to user)",
                        reply_markup=None
                    )
            elif action == "reject":
                try:
                    reason = "No reason provided"
                    order.reject_payment(reason)
                    await context.bot.send_message(
                        chat_id=session['chat_id'],
                        text=self.get_prompt(session, 'payment_rejected').format(reason=reason)
                    )
                    await query.edit_message_caption(
                        caption=f"{query.message.caption}\n\n❌ **REJECTED** by {query.from_user.first_name or 'Admin'}",
                        reply_markup=None
                    )
                    logger.info(f"Payment rejected for user {telegram_id}, order {order_id} by admin {query.from_user.id}")
                    session['notified'] = True
                except Exception as e:
                    logger.error(f"Error sending rejection message to user {telegram_id}: {str(e)}")
                    await query.edit_message_caption(
                        caption=f"{query.message.caption}\n\n❌ **REJECTED** by {query.from_user.first_name or 'Admin'} (Error sending notification to user)",
                        reply_markup=None
                    )
        except ValueError:
            logger.error(f"Invalid callback data format: {query.data}")
            await query.edit_message_caption(
                caption=f"{query.message.caption}\n\n⚠️ **ERROR**: Invalid callback data",
                reply_markup=None
            )
        except Exception as e:
            logger.error(f"Error handling admin response: {str(e)}")
            await query.message.reply_text("An error occurred while processing your response.")

    async def payment_retry_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle /payment command for retrying rejected payments"""
        user = update.effective_user
        telegram_id = str(user.id)
        session = self.get_user_session(telegram_id)
        session['chat_id'] = update.effective_chat.id
        
        # Check if user has a rejected order
        rejected_order = self.get_rejected_order_for_user(telegram_id)
        
        if not rejected_order:
            await update.message.reply_text(
                self.get_prompt(session, 'no_rejected_payment')
            )
            return ConversationHandler.END
        
        # Set up session for payment retry
        session['order_id'] = rejected_order.id
        session['notified'] = False  # Reset notification flag
        session['from_main_flow'] = False  # Mark this as a retry, not from main flow
        
        # Send payment retry instructions
        await update.message.reply_text(
            self.get_prompt(session, 'payment_retry_instructions'),
            parse_mode="HTML"
        )
        
        return PAYMENT

    async def handle_payment_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle /payment command for retrying rejected payments"""
        logger.info(f"🔄 /payment command received from user {update.effective_user.id}")
        user = update.effective_user
        telegram_id = str(user.id)
        
        logger.info(f"🔄 /payment command triggered by user {telegram_id}")
        
        # Get or create session
        session = self.get_user_session(telegram_id)
        session['chat_id'] = update.effective_chat.id
        
        logger.info(f"📋 Session retrieved for user {telegram_id}, checking for rejected orders...")
        
        # Check if user has a rejected order
        rejected_order = self.get_rejected_order_for_user(telegram_id)
        
        if not rejected_order:
            logger.info(f"❌ No rejected order found for user {telegram_id}")
            await update.message.reply_text(
                self.get_prompt(session, 'no_rejected_payment')
            )
            return ConversationHandler.END
        
        logger.info(f"✅ Found rejected order {rejected_order.id} for user {telegram_id}")
        
        # Load candidate data for this user if not in session
        try:
            candidate = Candidate.get_by_telegram_user_id(telegram_id)
            if candidate:
                session['candidate_data'] = candidate.to_dict()
                logger.info(f"📊 Loaded candidate data for user {telegram_id}")
            else:
                logger.warning(f"⚠️ No candidate found for telegram_id {telegram_id}")
        except Exception as e:
            logger.error(f"❌ Error loading candidate data for user {telegram_id}: {str(e)}")
        
        # Set up session for payment retry
        session['order_id'] = rejected_order.id
        session['notified'] = False  # Reset notification flag
        session['from_main_flow'] = False  # Mark this as a retry, not from main flow
        
        logger.info(f"🔄 Set up payment retry session for user {telegram_id}, order {rejected_order.id}")
        
        # Send payment retry instructions
        await update.message.reply_text(
            self.get_prompt(session, 'payment_retry_instructions'),
            parse_mode="HTML"
        )
        
        logger.info(f"📤 Sent payment retry instructions to user {telegram_id}")
        
        return PAYMENT

    # Improved get_rejected_order_for_user method with better error handling:

    def get_rejected_order_for_user(self, telegram_id: str):
        """Get the most recent rejected order for a user"""
        try:
            logger.info(f"🔍 Searching for rejected orders for user {telegram_id}")
            
            # Query Firebase for rejected orders for this user
            orders_ref = db.collection('orders')
            query = orders_ref.where('telegramUserId', '==', telegram_id).where('status', '==', 'rejected')
            
            # Get all rejected orders and sort them by created_at in Python
            orders = list(query.stream())
            logger.info(f"📊 Found {len(orders)} total rejected orders for user {telegram_id}")
            
            if not orders:
                logger.info(f"❌ No rejected orders found for user {telegram_id}")
                return None
            
            # Sort by createdAt or updated_at (fallback to document creation time)
            def get_order_time(order_doc):
                data = order_doc.to_dict()
                created_at = data.get('createdAt') or data.get('created_at')
                if isinstance(created_at, str):
                    try:
                        return datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    except:
                        pass
                elif hasattr(created_at, 'timestamp'):  # Firestore timestamp
                    return created_at
                return order_doc.create_time  # Fallback to document creation time
            
            # Sort orders by time (most recent first)
            orders.sort(key=get_order_time, reverse=True)
            
            # Get the most recent order
            most_recent_order_doc = orders[0]
            order_data = most_recent_order_doc.to_dict()
            order_data['id'] = most_recent_order_doc.id
            
            logger.info(f"✅ Found most recent rejected order {order_data['id']} for user {telegram_id}")
            
            # Create Order instance from the data
            return Order(**order_data)
            
        except Exception as e:
            logger.error(f"❌ Error fetching rejected order for user {telegram_id}: {str(e)}")
            return None

    async def handle_admin_reply(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle admin replies in the private channel to approve or reject payments"""
        if not update.message or not update.message.chat_id:
            logger.debug("Ignoring update with no message or chat_id")
            return
        
        if str(update.message.chat_id) != private_channel_id:
            logger.debug(f"Ignoring message from chat_id {update.message.chat_id}, expected {private_channel_id}")
            return
        
        reply_text = update.message.text.lower() if update.message.text else ""
        if not (reply_text.startswith('approve') or reply_text.startswith('reject:')):
            logger.debug(f"Ignoring reply with text: {reply_text}")
            return
        
        try:
            if not update.message.reply_to_message or not update.message.reply_to_message.caption:
                logger.debug("Ignoring reply with no valid reply_to_message or caption")
                return
            
            caption = update.message.reply_to_message.caption
            if not caption.startswith('💳 Payment'):
                logger.debug(f"Ignoring reply with invalid caption: {caption}")
                return
            
            try:
                order_id = caption.split('Order ID: ')[1].split('\n')[0].strip()
            except IndexError:
                logger.error(f"Failed to parse order_id from caption: {caption}")
                return
            
            order = Order.get_by_id(order_id)
            if not order:
                logger.error(f"Order {order_id} not found")
                return
            
            telegram_id = order.telegramUserId
            session = self.get_user_session(telegram_id)
            if 'chat_id' not in session:
                logger.error(f"No chat_id found for telegram_id {telegram_id} in session")
                return
            
            if reply_text == 'approve':
                order.approve_payment()
                logger.info(f"Order {order_id} approved: paymentVerified={order.paymentVerified}, status={order.status}, statusDetails={order.statusDetails}")
                if not session.get('notified', False):
                    await self.application.bot.send_message(
                        chat_id=session['chat_id'],
                        text=self.get_prompt(session, 'payment_verified')
                    )
                    logger.info(f"Sent immediate payment verified notification to chat_id {session['chat_id']} for order {order_id}")
                    session['notified'] = True
            elif reply_text.startswith('reject:'):
                reason = reply_text[7:].strip() or 'No reason provided'
                order.reject_payment(reason)
                logger.info(f"Order {order_id} rejected: paymentVerified={order.paymentVerified}, status={order.status}, statusDetails={order.statusDetails}")
                if not session.get('notified', False):
                    await self.application.bot.send_message(
                        chat_id=session['chat_id'],
                        text=self.get_prompt(session, 'payment_rejected').format(reason=reason)
                    )
                    logger.info(f"Sent immediate payment rejected notification to chat_id {session['chat_id']} for order {order_id}")
                    session['notified'] = True
        
        except Exception as e:
            logger.error(f"Error in handle_admin_reply: {str(e)}")

    async def ignore_non_reply_messages(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Ignore non-reply messages in the private channel"""
        logger.debug(f"Ignoring non-reply message in private channel: {update.message.text if update.message.text else 'No text'}")

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Cancel the current conversation"""
        telegram_id = str(update.effective_user.id)
        session = self.get_user_session(telegram_id)
        
        # Delete the previous menu message if it exists
        if 'menu_message_id' in session:
            try:
                await context.bot.delete_message(
                    chat_id=session['chat_id'],
                    message_id=session['menu_message_id']
                )
                logger.info(f"Deleted menu message ID {session['menu_message_id']} for user {telegram_id} on cancel")
                del session['menu_message_id']
            except Exception as e:
                logger.warning(f"Failed to delete menu message ID {session['menu_message_id']} for user {telegram_id} on cancel: {str(e)}")
        
        if telegram_id in self.user_sessions:
            del self.user_sessions[telegram_id]
        
        await update.message.reply_text(self.get_prompt(session, 'cancel_message'))
        return ConversationHandler.END

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Send a help message"""
        telegram_id = str(update.effective_user.id)
        session = self.get_user_session(telegram_id)
        await update.message.reply_text(self.get_prompt(session, 'help_message'))

    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Log errors and handle connection issues"""
        logger.error(msg="Exception while handling update:", exc_info=context.error)
        
        if update and update.effective_message and update.effective_user:
            telegram_id = str(update.effective_user.id)
            session = self.get_user_session(telegram_id)
            await update.effective_message.reply_text(self.get_prompt(session, 'error_message'))
        else:
            logger.debug("No effective message or user available to send error message")

    def run(self):
        """Start the bot with retry logic"""
        max_retries = 3
        retry_delay = 5.0
        for attempt in range(max_retries):
            try:
                logger.info("Initializing Application")
                self.application.initialize()  # Explicitly initialize the application
                logger.info("Starting Telegram bot with polling")
                self.application.run_polling(
                    poll_interval=1.0,
                    timeout=10,
                    bootstrap_retries=3,
                    close_loop=False
                )
                return
            except telegram.error.TimedOut as e:
                logger.error(f"Telegram API connection timed out (attempt {attempt + 1}/{max_retries}): {str(e)}")
                if attempt < max_retries - 1:
                    logger.info(f"Retrying in {retry_delay} seconds...")
                    import time
                    time.sleep(retry_delay)
                else:
                    logger.error("Max retries reached. Failed to connect to Telegram API.")
                    raise
            except Exception as e:
                logger.error(f"Error running bot: {str(e)}")
                raise

flask_app = Flask(__name__)

@flask_app.route('/')
def index():
    return 'Bot is alive!', 200

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    threading.Thread(target=run_flask).start()

    # Start the Telegram bot
    bot = CVBot(telegram_bot_token)
    bot.application.run_polling()