import os
import json
import threading
import subprocess
import requests
import re
from urllib.parse import urljoin
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    CallbackQueryHandler,
    filters,
)

# إعدادات
TOKEN = "7710008981:AAGKwLOb7BKi2ToI3D5faEXJxaLpcPxYn5g"  # ضعه في إعدادات Deepnote أو استبدله برمز البوت مباشرة
ADMINS = [8145101051]
USERS_FILE = "data/users.json"
LAST_STREAMS_FILE = "data/last_streams.json"
os.makedirs("data", exist_ok=True)

SELECT_BROADCAST_TYPE, STREAM_NAME, M3U8_LINK, STREAM_KEY, ADD_SUBSCRIBE = range(5)
processes = {}

# تحميل/حفظ JSON
def load_json(path):
    if not os.path.exists(path):
        with open(path, "w") as f:
            json.dump({}, f)
    try:
        with open(path, "r") as f:
            return json.load(f)
    except:
        return {}

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# اشتراكات
def is_admin(user_id):
    return user_id in ADMINS

def is_subscribed(user_id):
    users = load_json(USERS_FILE)
    user = users.get(str(user_id), {})
    expires = user.get("expires")
    if not expires:
        return False
    try:
        return datetime.fromisoformat(expires) > datetime.now()
    except:
        return False

def can_stream(user_id):
    if is_subscribed(user_id):
        return True, ""
    users = load_json(USERS_FILE)
    user = users.get(str(user_id), {})
    usage = user.get("daily_stream_count", 0)
    last_date_str = user.get("daily_stream_date")
    last_date = datetime.fromisoformat(last_date_str) if last_date_str else None
    now = datetime.now()
    if not last_date or last_date.date() < now.date():
        usage = 0
    if usage >= 1:
        return False, "❌ وصلت الحد المجاني اليومي. يرجى الاشتراك للبث أكثر."
    return True, ""

def increment_daily_stream_count(user_id):
    users = load_json(USERS_FILE)
    user = users.get(str(user_id), {})
    now = datetime.now()
    last_date_str = user.get("daily_stream_date")
    last_date = datetime.fromisoformat(last_date_str) if last_date_str else None
    if not last_date or last_date.date() < now.date():
        user["daily_stream_count"] = 1
        user["daily_stream_date"] = now.isoformat()
    else:
        user["daily_stream_count"] = user.get("daily_stream_count", 0) + 1
    users[str(user_id)] = user
    save_json(USERS_FILE, users)

# اختيار الجودة من m3u8
def get_m3u8_by_quality(master_url, get_highest=True):
    try:
        response = requests.get(master_url)
        if response.status_code != 200:
            return master_url

        lines = response.text.splitlines()
        qualities = []
        for i in range(len(lines)):
            if lines[i].startswith("#EXT-X-STREAM-INF"):
                res_match = re.search(r"RESOLUTION=(\d+)x(\d+)", lines[i])
                if res_match:
                    width = int(res_match.group(1))
                    height = int(res_match.group(2))
                    next_line = lines[i + 1].strip()
                    full_url = next_line if next_line.startswith("http") else urljoin(master_url, next_line)
                    qualities.append(((width, height), full_url))

        if not qualities:
            return master_url

        qualities.sort(key=lambda x: x[0][0] * x[0][1], reverse=get_highest)
        return qualities[0][1]
    except Exception as e:
        print("Error selecting quality:", e)
        return master_url

# بث ffmpeg
def monitor_stream(tag, cmd, user_id, is_pro):
    proc = subprocess.Popen(cmd)
    processes[tag] = proc

    if not is_pro:
        def stop_later():
            proc.terminate()
            processes.pop(tag, None)

        threading.Timer(600, stop_later).start()

    proc.wait()
    processes.pop(tag, None)

# واجهة Telegram
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    status = "مشترك ✅" if is_subscribed(user.id) else "غير مشترك ❌"
    buttons = [["🎬 تجهيز البث", "⏹ إيقاف البث"], ["🔁 إعادة تشغيل البث", "📞 تواصل مع الدعم"]]
    if is_admin(user.id):
        buttons.append(["➕ إضافة مفتاح اشتراك"])

    keyboard = ReplyKeyboardMarkup(buttons, resize_keyboard=True)
    text = (
        f"مرحباً!\nمعرفك: `{user.id}`\n"
        f"الاسم: {user.full_name}\n"
        f"الحالة: {status}\n\n"
        "اختر من القائمة:"
    )
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id

    if text == "➕ إضافة مفتاح اشتراك" and is_admin(user_id):
        await update.message.reply_text("أرسل: `user_id | 2025-07-01`", parse_mode="Markdown")
        context.user_data["awaiting_subscribe_data"] = True
        return ADD_SUBSCRIBE

    if context.user_data.get("awaiting_subscribe_data"):
        try:
            data = text.split("|")
            target_user_id = data[0].strip()
            expire_date = data[1].strip()
            datetime.fromisoformat(expire_date)
            users = load_json(USERS_FILE)
            user = users.get(target_user_id, {})
            user["expires"] = expire_date
            users[target_user_id] = user
            save_json(USERS_FILE, users)
            await update.message.reply_text(f"✅ تم تحديث اشتراك {target_user_id}")
        except:
            await update.message.reply_text("❌ خطأ في الصيغة")
        context.user_data["awaiting_subscribe_data"] = False
        return ConversationHandler.END

    if text == "🎬 تجهيز البث":
        allowed, msg = can_stream(user_id)
        if not allowed:
            await update.message.reply_text(msg)
            return ConversationHandler.END
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Live FB", callback_data="live_fb"),
             InlineKeyboardButton("Live IG", callback_data="live_ig")],
            [InlineKeyboardButton("protected", callback_data="use_filter")]
        ])
        await update.message.reply_text("اختر نوع البث:", reply_markup=keyboard)
        return SELECT_BROADCAST_TYPE

    elif text == "⏹ إيقاف البث":
        proc = processes.get(str(user_id))
        if proc and proc.poll() is None:
            proc.terminate()
            processes.pop(str(user_id), None)
            await update.message.reply_text("✅ تم إيقاف البث.")
        else:
            await update.message.reply_text("❌ لا يوجد بث نشط.")
        return ConversationHandler.END

    elif text == "📞 تواصل مع الدعم":
        await update.message.reply_text("راسل: @premuimuser12")
        return ConversationHandler.END

    elif text == "🔁 إعادة تشغيل البث":
        data = load_json(LAST_STREAMS_FILE).get(str(user_id))
        if not data:
            await update.message.reply_text("❌ لا يوجد بث سابق.")
            return ConversationHandler.END

        await update.message.reply_text("🔁 جاري إعادة تشغيل البث...")
        increment_daily_stream_count(user_id)
        threading.Thread(
            target=monitor_stream,
            args=(str(user_id), data["cmd"], user_id, is_subscribed(user_id)),
            daemon=True
        ).start()
        return ConversationHandler.END

    else:
        await update.message.reply_text("اختر أمر من القائمة.")
        return ConversationHandler.END

# مراحل المحادثة
async def select_broadcast_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "use_filter":
        context.user_data["use_filter"] = True
        await query.message.reply_text("✅ تم تفعيل الحماية، الآن اختر نوع البث:")
        return SELECT_BROADCAST_TYPE

    context.user_data["broadcast_type"] = data
    await query.message.reply_text("🎥 أرسل اسم البث:")
    return STREAM_NAME

async def get_stream_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["stream_name"] = update.message.text.strip()
    await update.message.reply_text("🔗 أرسل رابط M3U8:")
    return M3U8_LINK

async def get_m3u8(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link = update.message.text.strip()
    if not link.endswith(".m3u8"):
        await update.message.reply_text("❌ الرابط غير صالح.")
        return ConversationHandler.END
    context.user_data["m3u8"] = link
    await update.message.reply_text("🔑 أرسل مفتاح البث:")
    return STREAM_KEY

async def get_stream_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = update.message.text.strip()
    user_id = update.effective_user.id
    raw_link = context.user_data.get("m3u8")
    is_pro = is_subscribed(user_id)
    link = get_m3u8_by_quality(raw_link, get_highest=is_pro)

    use_filter = context.user_data.get("use_filter", False)
    broadcast_type = context.user_data.get("broadcast_type")

    if broadcast_type == "live_fb":
        output = f"rtmps://live-api-s.facebook.com:443/rtmp/{key}"
    elif broadcast_type == "live_ig":
        output = f"rtmps://live-upload.instagram.com:443/rtmp/{key}"
    else:
        await update.message.reply_text("❌ نوع البث غير معروف.")
        return ConversationHandler.END

    vf = "eq=contrast=1.05:brightness=0.02:saturation=1.02,drawbox=x=0:y=0:w=iw:h=60:color=black@0.3:t=fill" if (use_filter or is_pro) else "null"
    af = "atempo=0.99,asetrate=44100*0.98" if (use_filter or is_pro) else "anull"

    cmd = [
        "ffmpeg", "-re", "-i", link, "-vf", vf, "-c:v", "libx264",
        "-preset", "veryfast", "-maxrate", "4500k" if is_pro else "1500k",
        "-bufsize", "6000k" if is_pro else "3000k",
        "-g", "50", "-r", "30", "-pix_fmt", "yuv420p",
        "-af", af, "-c:a", "aac", "-b:a", "128k" if is_pro else "96k",
        "-ar", "44100", "-ac", "2", "-f", "flv", output
    ]

    await update.message.reply_text("🚀 بدء البث...")
    increment_daily_stream_count(user_id)
    save_json(LAST_STREAMS_FILE, {**load_json(LAST_STREAMS_FILE), str(user_id): {"cmd": cmd}})
    threading.Thread(target=monitor_stream, args=(str(user_id), cmd, user_id, is_pro), daemon=True).start()
    return ConversationHandler.END

# تشغيل البوت
async def main():
    app = ApplicationBuilder().token(TOKEN).build()
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)],
        states={
            SELECT_BROADCAST_TYPE: [CallbackQueryHandler(select_broadcast_type)],
            STREAM_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_stream_name)],
            M3U8_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_m3u8)],
            STREAM_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_stream_key)],
            ADD_SUBSCRIBE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)],
        },
        fallbacks=[]
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)
    print("✅ البوت يعمل الآن...")
    await app.run_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())