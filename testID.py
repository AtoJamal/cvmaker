from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

async def handle_channel_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.channel_post:
        msg = update.channel_post

        if msg.video:
            print(f"Video file ID: {msg.video.file_id}")
        elif msg.document:
            print(f"Document file ID: {msg.document.file_id}")
        elif msg.photo:
            print(f"Photo file ID: {msg.photo[-1].file_id}")  # highest resolution
        else:
            print("Received something else.")

app = ApplicationBuilder().token("8005097551:AAGdSdVVHTJ3Ih1iACU0agKtvxxaFF_dKdk").build()
app.add_handler(MessageHandler(filters.ALL, handle_channel_media))
app.run_polling()
