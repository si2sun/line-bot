from flask import Flask, request, abort
import configparser
# import google.genai as genai

import google.generativeai as genai
from google.generativeai.types import GenerationConfig
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (ApiClient, MessagingApi, ReplyMessageRequest, 
                                  TextMessage, PushMessageRequest, Configuration)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
import time
import os
import datetime
from google.cloud import firestore

# --- 組態設定 ---
app = Flask(__name__, static_url_path='/static')
# config = configparser.ConfigParser()
# config.read('config.ini')

# LINE Bot 設定
# LINE_CHANNEL_ACCESS_TOKEN = config.get('line-bot', 'channel_access_token')
# LINE_CHANNEL_SECRET = config.get('line-bot', 'channel_secret')
# configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
# handler = WebhookHandler(LINE_CHANNEL_SECRET)
# LINE Bot 設定 (從環境變數讀取)
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET')
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
messaging_api_client = ApiClient(configuration)
line_bot_api = MessagingApi(messaging_api_client)

# Gemini API 設定
# Gemini API 設定
# GEMINI_API_KEY = config.get('line-bot', 'gemini_api')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
client = genai.configure(api_key=GEMINI_API_KEY)

# genai.configure(api_key=GEMINI_API_KEY)
# Firestore 設定
# 確保你的 GOOGLE_APPLICATION_CREDENTIALS 環境變數已經設定好
# os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = os.path.join(os.path.dirname(__file__), 'animetext-anime1si2sun.json')
firestore_client = firestore.Client(project="animetext", database="anime-label")
doc_ref = firestore_client.collection('gcp-line-bot').document('line-bot-gemini-memory')

# 用於切換模式的全域變數
gemini_mode = False

# --- Webhook 與機器人邏輯 ---
@app.route("/", methods=['POST'])
def callback():
    """LINE Bot 的 Webhook 回呼函式"""
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)
    try:
        handler.handle(body, signature)
    except Exception as e:
        app.logger.error(f"處理請求時發生錯誤: {e}")
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event: MessageEvent):
    """處理收到的文字訊息"""
    global gemini_mode
    user_id = event.source.user_id
    user_text = event.message.text.strip() # 去除前後空白

    if user_text.lower() in ('結束gemini', '結束 gemini'):
        gemini_mode = False
        reply_message(event.reply_token, '結束Gemini AI服務')
    elif user_text.lower() == 'gemini':
        gemini_mode = True
        try:
            profile = line_bot_api.get_profile(user_id)
            user_name = profile.display_name
            reply_message(event.reply_token, '已轉接到Gemini AI服務')
            time.sleep(1) # 暫停一下再發送推播訊息
            push_message(user_id, f'{user_name} 你好')
        except Exception as e:
            app.logger.error(f"取得個人資料或推播訊息失敗: {e}")
            reply_message(event.reply_token, '已轉接到Gemini AI服務，你好！')
    elif gemini_mode:
        try:
            # 呼叫帶有記憶功能的 gemini 函式
            reply_text = gemini_with_memory(user_text)
            reply_message(event.reply_token, reply_text)
        except Exception as e:
            app.logger.error(f"呼叫 Gemini API 時發生錯誤: {e}")
            reply_message(event.reply_token, "抱歉，我現在無法處理你的請求。")
    else:
        # 將收到的文字原封不動地回傳
        reply_message(event.reply_token, user_text)
def gemini_with_memory(user_text: str) -> str:

   
    # ✅ 1. 初始化模型 (使用有效的模型名稱)
    # 'gemini-2.5-flash' 不是公開模型，改用 'gemini-1.5-flash'
    model = genai.GenerativeModel('gemini-2.5-flash')

    # ✅ 2. 讀取並格式化 Firestore 的對話歷史
    try:
        doc = doc_ref.get()
        memory_from_db = doc.to_dict().get('memory', []) if doc.exists else []

        # 將 Firestore 儲存的格式轉換為 Gemini API 需要的格式
        # Firestore: [{'name': 'user', 'text': '[time]...'}, ...]
        # Gemini API: [{'role': 'user', 'parts': ['...']}, ...]
        chat_history = []
        for message in memory_from_db:
            role = 'user' if message.get('name') == 'user' else 'model'
            # 移除我們自己記錄的時間戳，只傳送純文字內容給模型
            text_content = message.get('text', '')
            try:
                clean_text = text_content[text_content.index('] ') + 2:]
            except ValueError:
                clean_text = text_content
            
            chat_history.append({'role': role, 'parts': [clean_text]})

    except Exception as e:
        app.logger.error(f"讀取或格式化 Firestore 歷史紀錄失敗: {e}")
        chat_history = []
        memory_from_db = []

    # ✅ 3. 帶著歷史紀錄啟動一個對話 Session
    chat = model.start_chat(history=chat_history)

    # ✅ 4. 傳送新訊息並取得模型回覆
    try:
        response = chat.send_message(user_text)
        model_response_text = response.text
    except Exception as e:
        app.logger.error(f"呼叫 Gemini API 時發生錯誤: {e}")
        model_response_text = "抱歉，我暫時無法回應你的訊息。"

    # ✅ 5. 將新的對話回合儲存回 Firestore
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    memory_from_db.append({'name': 'user', 'text': f"[{current_time}] {user_text}"})
    memory_from_db.append({'name': 'model', 'text': f"[{current_time}] {model_response_text}"})

    try:
        doc_ref.set({'memory': memory_from_db})
    except Exception as e:
        app.logger.error(f"寫入 Firestore 失敗: {e}")
    return model_response_text

# --- LINE Messaging API 輔助函式 ---
def reply_message(reply_token: str, text: str):
    try:
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=text)]
            )
        )
    except Exception as e:
        app.logger.error(f"傳送回覆訊息失敗: {e}")

def push_message(user_id: str, text: str):
    """傳送推播訊息給使用者"""
    try:
        line_bot_api.push_message(
            PushMessageRequest(
                to=user_id,
                messages=[TextMessage(text=text)]
            )
        )
    except Exception as e:
        app.logger.error(f"傳送推播訊息失敗: {e}")

if __name__ == "__main__":
    # Render 會提供一個 PORT 環境變數，我們的 Gunicorn 會用它
    # 本地測試時，可以保留 app.run，但在 Docker 中它不會被執行
    port = int(os.environ.get("PORT", 5001))

    app.run(port=port, debug=True)

