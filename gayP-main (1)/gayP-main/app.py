import os
import sqlite3
from flask import Flask, request, abort
from dotenv import load_dotenv
from linebot import LineBotApi, WebhookHandler
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    QuickReply, QuickReplyButton, MessageAction
)

# 載入 .env 環境變數
load_dotenv()

# 初始化 Flask app
app = Flask(__name__)

# 初始化 LINE Bot
line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

# 使用者狀態暫存
user_states = {}

# 初始化 SQLite 資料庫
def init_db():
    conn = sqlite3.connect("rides.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS ride_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            origin TEXT,
            destination TEXT,
            ride_type TEXT,
            time TEXT,
            payment TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

@app.route("/")
def home():
    return "LineBot with SQLite is running!"

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except:
        abort(400)
    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_input = event.message.text.strip()

    if user_input == "查詢我的預約":
        conn = sqlite3.connect("rides.db")
        c = conn.cursor()
        c.execute("SELECT * FROM ride_records WHERE user_id = ?", (user_id,))
        user_rides = c.fetchall()
        conn.close()

        if not user_rides:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="你目前沒有預約紀錄。")
            )
            return

        latest = user_rides[-1]
        origin, destination, ride_type, time, payment = latest[2:7]

        conn = sqlite3.connect("rides.db")
        c = conn.cursor()
        c.execute("""
            SELECT * FROM ride_records
            WHERE user_id != ? AND ride_type = '共乘' AND origin = ? AND time = ?
        """, (user_id, origin, time))
        match_found = c.fetchone() is not None
        conn.close()

        reply = f"""📋 你最近的預約如下：
🛫 出發地：{origin}
🛬 目的地：{destination}
🚘 共乘狀態：{ride_type}
🕐 預約時間：{time}
💳 付款方式：{payment}
👥 共乘配對狀態：{"✅ 已找到共乘對象！" if match_found else "⏳ 尚未有共乘對象"}
"""
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply)
        )
        return

    if "到" in user_input and "我預約" not in user_input and "我使用" not in user_input:
        try:
            origin, destination = map(str.strip, user_input.split("到"))
        except ValueError:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="請輸入格式為『出發地 到 目的地』")
            )
            return

        user_states[user_id] = {
            "origin": origin,
            "destination": destination
        }

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text=f"🚕 你要從 {origin} 到 {destination}\n請選擇是否共乘：",
                quick_reply=QuickReply(items=[
                    QuickReplyButton(action=MessageAction(label="我要共乘", text="我選擇共乘")),
                    QuickReplyButton(action=MessageAction(label="我要自己搭", text="我不共乘")),
                ])
            )
        )
        return

    if user_input in ["我選擇共乘", "我不共乘"]:
        ride_type = "共乘" if "共乘" in user_input else "不共乘"
        if user_id not in user_states:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="請先輸入『出發地 到 目的地』")
            )
            return

        user_states[user_id]["ride_type"] = ride_type

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="請輸入你想預約的時間，例如：我預約 15:30"
            )
        )
        return

    if user_input.startswith("我預約"):
        time = user_input.replace("我預約", "").strip()
        if user_id not in user_states or "ride_type" not in user_states[user_id]:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="請先輸入『出發地 到 目的地』並選擇共乘狀態")
            )
            return

        user_states[user_id]["time"] = time

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text=f"🕐 你選擇的時間是 {time}\n請選擇付款方式：",
                quick_reply=QuickReply(items=[
                    QuickReplyButton(action=MessageAction(label="LINE Pay", text="我使用 LINE Pay")),
                    QuickReplyButton(action=MessageAction(label="現金", text="我使用 現金")),
                    QuickReplyButton(action=MessageAction(label="悠遊卡", text="我使用 悠遊卡")),
                ])
            )
        )
        return

    if user_input.startswith("我使用"):
        payment = user_input.replace("我使用", "").strip()
        if user_id not in user_states or "time" not in user_states[user_id]:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="請先完成前面的預約步驟")
            )
            return

        user_states[user_id]["payment"] = payment
        data = user_states[user_id]

        conn = sqlite3.connect("rides.db")
        c = conn.cursor()
        c.execute("""
            INSERT INTO ride_records (user_id, origin, destination, ride_type, time, payment)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            data["origin"],
            data["destination"],
            data["ride_type"],
            data["time"],
            payment
        ))
        conn.commit()

        c.execute("""
            SELECT * FROM ride_records
            WHERE user_id != ? AND ride_type = '共乘' AND origin = ? AND time = ?
        """, (user_id, data["origin"], data["time"]))
        match = c.fetchone()
        conn.close()

        route_url = f"https://www.google.com/maps/dir/{data['origin']}/{data['destination']}"

        reply = f"""🎉 預約完成！
🛫 出發地：{data['origin']}
🛬 目的地：{data['destination']}
🚘 共乘狀態：{data['ride_type']}
🕐 預約時間：{data['time']}
💳 付款方式：{payment}"""

        if match:
            reply += "\n🚨 發現共乘對象！你和另一位使用者搭乘相同班次！"
        reply += f"\n\n📍 路線預覽：\n{route_url}"
        reply += "\n\n👉 想再預約，請再輸入『出發地 到 目的地』"

        user_states.pop(user_id, None)

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply)
        )
        return

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text="請輸入格式為『出發地 到 目的地』的訊息")
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
