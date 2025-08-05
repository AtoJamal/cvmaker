import asyncio
from telegram import Bot

async def get_file_ids():
    bot = Bot("8005097551:AAGdSdVVHTJ3Ih1iACU0agKtvxxaFF_dKdk")
    
    # Forward messages to yourself to get file_ids
    updates = await bot.get_updates()
    for update in updates:
        if update.message:
            msg = update.message
            if msg.video:
                print(f"Video file_id: {msg.video.file_id}")
            elif msg.document:
                print(f"Document file_id: {msg.document.file_id}")
            elif msg.photo:
                print(f"Photo file_id: {msg.photo[-1].file_id}")

asyncio.run(get_file_ids())