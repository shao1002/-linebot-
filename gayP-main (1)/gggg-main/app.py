import os
import sqlite3
import numpy as np
from flask import Flask, request, abort
from dotenv import load_dotenv
from linebot import LineBotApi, WebhookHandler
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    QuickReply, QuickReplyButton, MessageAction
)
from sklearn.linear_model import LogisticRegression
from geopy.distance import geodesic  # 計算地理距離

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
            payment TEXT,
            origin_lat REAL,  -- 新增經緯度欄位
            origin_lon REAL,
            dest_lat REAL,
            dest_lon REAL
        )
    """)
    conn.commit()
    conn.close()

init_db()

# 簡單的經緯度查找（模擬，實際應使用 Google Maps API）
def get_coordinates(location):
    # 模擬經緯度數據，實際應透過 API 獲取
    location_map = {
        "台北車站": (25.0478, 121.5170),
        "松山機場": (25.0634, 121.5520),
        "台大": (25.0169, 121.5346),
    }
    return location_map.get(location, (0, 0))  # 預設值

# 訓練邏輯回歸模型（模擬，假設已有歷史數據）
def train_logistic_regression():
    # 假設歷史數據：特徵為距離差（公里）、時間差（分鐘）、付款方式一致性
    X = np.array([
        [5.0, 10, 1],  # 距離 5km, 時間差 10 分鐘, 付款方式一致
        [2.0, 5, 0],   # 距離 2km, 時間差 5 分鐘, 付款方式不一致
        [1.0, 2, 1],   # 距離 1km, 時間差 2 分鐘, 付款方式一致
        [10.0, 30, 0], # 距離 10km, 時間差 30 分鐘, 付款方式不一致
    ])
    y = np.array([1, 0, 1, 0])  # 1: 適合共乘, 0: 不適合
    model = LogisticRegression()
    model.fit(X, y)
    return model

# 初始化邏輯回歸模型
logistic_model = train_logistic_regression()

@app.route("/")
def home():
    return "LineBot with SQLite and Logistic Regression is running!"

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

        # 使用邏輯回歸進行共乘匹配
        match_found = False
        match_user_id = None
        conn = sqlite3.connect("rides.db")
        c = conn.cursor()
        c.execute("""
            SELECT * FROM ride_records
            WHERE user_id != ? AND ride_type = '共乘'
        """, (user_id,))
        potential_matches = c.fetchall()
        conn.close()

        user_time = sum(int(x) * 60 ** i for i, x in enumerate(reversed(time.split(":"))))
        user_origin_coords = get_coordinates(origin)

        for match in potential_matches:
            match_origin, match_time, match_payment = match[2], match[5], match[6]
            match_origin_coords = get_coordinates(match_origin)
            match_time_value = sum(int(x) * 60 ** i for i, x in enumerate(reversed(match_time.split(":"))))

            # 計算特徵
            distance = geodesic(user_origin_coords, match_origin_coords).km
            time_diff = abs(user_time - match_time_value) // 60  # 分鐘
            payment_same = 1 if payment == match_payment else 0

            # 使用邏輯回歸預測
            features = np.array([[distance, time_diff, payment_same]])
            prediction = logistic_model.predict(features)[0]
            if prediction == 1:
                match_found = True
                match_user_id = match[1]
                break

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

        origin_coords = get_coordinates(origin)
        dest_coords = get_coordinates(destination)
        user_states[user_id] = {
            "origin": origin,
            "destination": destination,
            "origin_lat": origin_coords[0],
            "origin_lon": origin_coords[1],
            "dest_lat": dest_coords[0],
            "dest_lon": dest_coords[1]
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
            INSERT INTO ride_records (user_id, origin, destination, ride_type, time, payment, origin_lat, origin_lon, dest_lat, dest_lon)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            data["origin"],
            data["destination"],
            data["ride_type"],
            data["time"],
            payment,
            data["origin_lat"],
            data["origin_lon"],
            data["dest_lat"],
            data["dest_lon"]
        ))
        conn.commit()

        # 使用邏輯回歸進行共乘匹配
        match_found = False
        c.execute("""
            SELECT * FROM ride_records
            WHERE user_id != ? AND ride_type = '共乘'
        """, (user_id,))
        potential_matches = c.fetchall()
        conn.close()

        user_time = sum(int(x) * 60 ** i for i, x in enumerate(reversed(data["time"].split(":"))))
        user_origin_coords = (data["origin_lat"], data["origin_lon"])

        for match in potential_matches:
            match_origin_coords = (match[7], match[8])
            match_time, match_payment = match[5], match[6]
            match_time_value = sum(int(x) * 60 ** i for i, x in enumerate(reversed(match_time.split(":"))))

            distance = geodesic(user_origin_coords, match_origin_coords).km
            time_diff = abs(user_time - match_time_value) // 60
            payment_same = 1 if payment == match_payment else 0

            features = np.array([[distance, time_diff, payment_same]])
            prediction = logistic_model.predict(features)[0]
            if prediction == 1:
                match_found = True
                break

        route_url = f"https://www.google.com/maps/dir/{data['origin']}/{data['destination']}"

        reply = f"""🎉 預約完成！
🛫 出發地：{data['origin']}
🛬 目的地：{data['destination']}
🚘 共乘狀態：{data['ride_type']}
🕐 預約時間：{data['time']}
💳 付款方式：{payment}"""

        if match_found:
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
    port = int(os.environ.get("PORT", 10000))#原為5000，為了和render一樣改成10000
    app.run(host="0.0.0.0", port=port)