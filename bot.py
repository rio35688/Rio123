import os
import json
import threading
import subprocess
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
import asyncio

# === إعدادات ===
TOKEN = os.getenv("TOKEN")  # يجب تعيين متغير البيئة TOKEN قبل التشغيل
ADMINS = [8145101051]  # ضع هنا معرفات المشرفين
USERS_FILE = "data/users.json"

# تأكد من وجود مجلد البيانات
os.makedirs("data", exist_ok=True)

# === حالات المحادثة ===
STREAM_NAME, M3U8_LINK, FB_KEY = range(3)
IG_STREAM_NAME, IG_LINK, IG_KEY = range(3)
processes = {}  # يخزن عمليات ffmpeg الحالية {tag: Popen}

# === وظائف مساعدة ===
def load_json(path):
    if not os.path.exists(path):
        with open(path, "w") as f:
            json.dump({}, f)
    with open(path, "r") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def is_admin(user_id):
    return user_id in ADMINS

def is_subscribed(user_id):
    users = load_json(USERS_FILE)
    user = users.get(str(user_id), {})
    expires = user.get("expires")
    return expires and datetime.fromisoformat(expires) > datetime.now()

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
        return False, "❌ وصلت الحد المجاني اليومي، اشترك للبث أكثر."
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

def monitor_stream(tag, cmd):
    proc = subprocess.Popen(cmd)
    processes[tag] = proc
    proc.wait()
    processes.pop(tag, None)

def stop_stream_process(tag):
    proc = processes.get(tag)
    if proc and proc.poll() is None:
        proc.terminate()
        processes.pop(tag, None)

# === أوامر البوت ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    username = user.username or "لا يوجد"
    full_name = (user.first_name or "") + (" " + user.last_name if user.last_name else "")
    status = "مشترك ✅" if is_subscribed(user.id) else "غير مشترك ❌"

    keyboard = ReplyKeyboardMarkup(
        [
            ["🎬 تجهيز البث", "🎬 تجهيز البث IG"],
            ["⏹ إيقاف بث معين", "⏹ إيقاف جميع البثوث"],
            ["🔁 إعادة تشغيل البث"]
        ],
        resize_keyboard=True,
    )

    await update.message.reply_text(
        f"مرحباً!\n"
        f"معرفك: `{user.id}`\n"
        f"اسم المستخدم: @{username}\n"
        f"الاسم: {full_name}\n"
        f"الحالة: {status}\n\n"
        f"اختر من القائمة:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

# --- تجهيز بث Facebook ---
async def start_prepare(update: Update, context: ContextTypes.DEFAULT_TYPE):
    allowed, msg = can_stream(update.effective_user.id)
    if not allowed:
        await update.message.reply_text(msg)
        return ConversationHandler.END
    await update.message.reply_text("🎥 أرسل اسم البث:")
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
    await update.message.reply_text("🔑 أرسل مفتاح البث (يبدأ بـ FB-):")
    return FB_KEY

async def get_fb_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = update.message.text.strip()
    if not key.startswith("FB-"):
        await update.message.reply_text("❌ مفتاح غير صالح.")
        return ConversationHandler.END

    user_id = str(update.effective_user.id)
    name = context.user_data["stream_name"]
    link = context.user_data["m3u8"]
    output = f"rtmps://live-api-s.facebook.com:443/rtmp/{key}"

    # إعدادات ffmpeg حسب الاشتراك
    scale = "1920:1080" if is_subscribed(update.effective_user.id) else "854:480"
    video_bitrate = "4500k" if is_subscribed(update.effective_user.id) else "1000k"
    maxrate = "5000k" if is_subscribed(update.effective_user.id) else "1200k"
    bufsize = "6000k" if is_subscribed(update.effective_user.id) else "1500k"
    audio_bitrate = "160k" if is_subscribed(update.effective_user.id) else "128k"

    cmd = [
        "ffmpeg", "-re", "-i", link,
        "-vf", f"scale={scale}",
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
        "-b:v", video_bitrate,
        "-maxrate", maxrate,
        "-bufsize", bufsize,
        "-c:a", "aac", "-b:a", audio_bitrate,
        "-f", "flv", "-rtbufsize", "1500M",
        output
    ]

    tag = f"{user_id}_{name}"
    threading.Thread(target=monitor_stream, args=(tag, cmd), daemon=True).start()

    if not is_subscribed(update.effective_user.id):
        increment_daily_stream_count(user_id)

    await update.message.reply_text(f"✅ تم بدء بث Facebook!\n📛 الاسم: {name}")

    if not is_subscribed(update.effective_user.id):
        def stop_after_30():
            stop_stream_process(tag)
            asyncio.run_coroutine_threadsafe(
                update.message.reply_text("⏰ انتهى وقت البث المجاني (30 دقيقة)."),
                context.application.loop
            )
        threading.Timer(1800, stop_after_30).start()

    return ConversationHandler.END

# --- تجهيز بث Instagram ---
async def start_prepare_ig(update: Update, context: ContextTypes.DEFAULT_TYPE):
    allowed, msg = can_stream(update.effective_user.id)
    if not allowed:
        await update.message.reply_text(msg)
        return ConversationHandler.END
    await update.message.reply_text("📸 أرسل اسم البث (Instagram):")
    return IG_STREAM_NAME

async def get_ig_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["ig_name"] = update.message.text.strip()
    await update.message.reply_text("🔗 أرسل رابط المصدر (M3U8 أو ملف):")
    return IG_LINK

async def get_ig_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["ig_link"] = update.message.text.strip()
    await update.message.reply_text("🔑 أرسل Instagram Stream Key:")
    return IG_KEY

async def get_ig_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = update.message.text.strip()
    user_id = str(update.effective_user.id)
    name = context.user_data["ig_name"]
    link = context.user_data["ig_link"]
    output = f"rtmps://live-upload.instagram.com:443/rtmp/{key}"

    # بث Instagram بأبعاد 16:9 (1920x1080)
    cmd = [
        "ffmpeg", "-re", "-i", link,
        "-vf", "scale=1920:1080",
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
        "-b:v", "2500k", "-maxrate", "3000k", "-bufsize", "4000k",
        "-c:a", "aac", "-b:a", "128k",
        "-f", "flv",
        output
    ]

    tag = f"{user_id}_ig_{name}"
    threading.Thread(target=monitor_stream, args=(tag, cmd), daemon=True).start()

    if not is_subscribed(update.effective_user.id):
        increment_daily_stream_count(user_id)

    await update.message.reply_text(f"✅ بدأ بث Instagram: {name}")

    if not is_subscribed(update.effective_user.id):
        def stop_after_30():
            stop_stream_process(tag)
            asyncio.run_coroutine_threadsafe(
                update.message.reply_text("⏰ انتهى وقت البث المجاني (30 دقيقة)."),
                context.application.loop
            )
        threading.Timer(1800, stop_after_30).start()

    return ConversationHandler.END

# --- إيقاف بث معين حسب الاسم ---
async def stop_named_stream(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user_tags = [tag for tag in processes if tag.startswith(user_id)]
    if not user_tags:
        await update.message.reply_text("⚠️ لا يوجد بثوث نشطة لديك.")
        return

    # عرض قائمة أسماء البثوث النشطة للمستخدم لاختيار أحدها
    buttons = [[tag.split("_", 1)[1]]]  # أسماء البث بعد "_" مباشرة (اسم البث فقط)
    keyboard = ReplyKeyboardMarkup(buttons, one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text("اختر اسم البث الذي تريد إيقافه:", reply_markup=keyboard)

    return "STOP_SELECT_NAME"

async def stop_stream_by_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    selected_name = update.message.text.strip()
    user_id = str(update.effective_user.id)
    tag = f"{user_id}_{selected_name}"
    tag_ig = f"{user_id}_ig_{selected_name}"

    stopped = False
    if tag in processes:
        stop_stream_process(tag)
        stopped = True
    elif tag_ig in processes:
        stop_stream_process(tag_ig)
        stopped = True

    if stopped:
        await update.message.reply_text(f"✅ تم إيقاف البث: {selected_name}")
    else:
        await update.message.reply_text("❌ لم أجد بث بهذا الاسم نشط لديك.")

    return ConversationHandler.END

# --- إيقاف جميع البثوث ---
async def stop_all_streams(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    tags = [tag for tag in processes if tag.startswith(user_id)]
    if not tags:
        await update.message.reply_text("⚠️ لا يوجد بثوث نشطة لديك.")
        return
    for tag in tags:
        stop_stream_process(tag)
    await update.message.reply_text("⏹ تم إيقاف جميع البثوث الخاصة بك.")

# --- إعادة تشغيل البث (حالياً غير مفعل) ---
async def restart_stream(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔁 حالياً ميزة إعادة التشغيل غير مفعلة.")

# --- التعامل مع النصوص العامة ---
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "🎬 تجهيز البث":
        return await start_prepare(update, context)
    elif text == "🎬 تجهيز البث IG":
        return await start_prepare_ig(update, context)
    elif text == "⏹ إيقاف بث معين":
        return await stop_named_stream(update, context)
    elif text == "⏹ إيقاف جميع البثوث":
        return await stop_all_streams(update, context)
    elif text == "🔁 إعادة تشغيل البث":
        return await restart_stream(update, context)
    else:
        await update.message.reply_text("❓ استخدم الأزرار للانتقال للخيارات.")

# --- إضافة مشترك (للأدمن فقط) ---
async def add_subscriber(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ أنت لست مشرفاً.")
        return
    args = context.args
    if len(args) != 2:
        await update.message.reply_text("استخدم: /addsub <user_id> <عدد_الأيام>")
        return
    user_id, days = args[0], args[1]
    try:
        days = int(days)
        expires = datetime.now() + timedelta(days=days)
    except:
        await update.message.reply_text("❌ عدد الأيام غير صالح.")
        return

    users = load_json(USERS_FILE)
    users[user_id] = {"expires": expires.isoformat()}
    save_json(USERS_FILE, users)
    await update.message.reply_text(f"✅ أضيف الاشتراك للمستخدم {user_id} لمدة {days} يوم.")

# --- حذف مشترك (للأدمن فقط) ---
async def remove_subscriber(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ أنت لست مشرفاً.")
        return
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("استخدم: /removesub <user_id>")
        return
    user_id = args[0]
    users = load_json(USERS_FILE)
    if user_id in users:
        users.pop(user_id)
        save_json(USERS_FILE, users)
        await update.message.reply_text(f"✅ تم حذف الاشتراك للمستخدم {user_id}.")
    else:
        await update.message.reply_text("❌ هذا المستخدم غير مشترك.")

# === إعداد المحادثات ===
conv_handler_fb = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex("^🎬 تجهيز البث$"), start_prepare)],
    states={
        STREAM_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_stream_name)],
        M3U8_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_m3u8)],
        FB_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_fb_key)],
    },
    fallbacks=[],
)

conv_handler_ig = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex("^🎬 تجهيز البث IG$"), start_prepare_ig)],
    states={
        IG_STREAM_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_ig_name)],
        IG_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_ig_link)],
        IG_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_ig_key)],
    },
    fallbacks=[],
)

conv_handler_stop_name = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex("^⏹ إيقاف بث معين$"), stop_named_stream)],
    states={
        "STOP_SELECT_NAME": [MessageHandler(filters.TEXT & ~filters.COMMAND, stop_stream_by_name)],
    },
    fallbacks=[],
)

async def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler_fb)
    app.add_handler(conv_handler_ig)
    app.add_handler(conv_handler_stop_name)

    app.add_handler(CommandHandler("addsub", add_subscriber))
    app.add_handler(CommandHandler("removesub", remove_subscriber))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("البوت يعمل...")
    await app.run_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
