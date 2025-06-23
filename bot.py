from telegram import Bot
import time

# التوكن الخاص بالبوت المرسل
SENDER_BOT_TOKEN = "7728299479:AAEDJsYEdPe3YKABXD2AqqLKZRQbhDj-P9s"

# التوكن الخاص بالبوت المستقبل
RECEIVER_BOT_TOKEN = "7710008981:AAGKwLOb7BKi2ToI3D5faEXJxaLpcPxYn5g"

# إعداد البوتات
sender_bot = Bot(token=SENDER_BOT_TOKEN)
receiver_bot_chat_id = f"@اسم_البوت_المستقبل"  # ضع هنا اسم البوت المستقبل

def send_message_to_receiver():
    message = "/start"
    sender_bot.send_message(chat_id=receiver_bot_chat_id, text=message)

if __name__ == "__main__":
    while True:
        send_message_to_receiver()
        time.sleep(300)  # الانتظار لمدة 5 دقائق