from flask import Flask, request, abort
import google.generativeai as genai
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (ApiClient, MessagingApi, ReplyMessageRequest, 
                                  TextMessage, PushMessageRequest, Configuration)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
import time
import os
import datetime
from google.cloud import firestore
import logging
from zoneinfo import ZoneInfo # 使用 Python 內建的時區功能

# --- 組態設定 ---
app = Flask(__name__)

# LINE Bot 設定 (從環境變數讀取)
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET')

if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_CHANNEL_SECRET:
    logging.error("LINE Bot 金鑰未在環境變數中設定！")
    exit()

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
messaging_api_client = ApiClient(configuration)
line_bot_api = MessagingApi(messaging_api_client)

# Gemini API 設定
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
if not GEMINI_API_KEY:
    logging.error("Gemini API Key 未在環境變數中設定！")
    exit()
genai.configure(api_key=GEMINI_API_KEY)

# Firestore 設定
try:
    firestore_client = firestore.Client(database="anime-label") 
    # 用於儲存對話歷史的參考
    chat_memory_ref = firestore_client.collection('gcp-line-bot').document('line-bot-gemini-memory')
    # 新增一個集合，用來儲存使用者狀態
    user_status_ref = firestore_client.collection('users')
except Exception as e:
    logging.error(f"Firestore 初始化失敗: {e}")
    exit()


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
    """處理收到的文字訊息 (使用 Firestore 儲存狀態)"""
    user_id = event.source.user_id
    user_text = event.message.text.strip()
    
    # 為每個使用者建立一個獨立的文件參考
    user_doc_ref = user_status_ref.document(user_id)

    if user_text.lower() in ('結束gemini', '結束 gemini','結束Gemini', '結束Gemini '):
        user_doc_ref.set({'gemini_mode': False}, merge=True)
        reply_message(event.reply_token, '結束Gemini AI服務')
    elif user_text.lower() == 'gemini':
        user_doc_ref.set({'gemini_mode': True}, merge=True)
        try:
            profile = line_bot_api.get_profile(user_id)
            user_name = profile.display_name
            reply_message(event.reply_token, '已轉接到Gemini AI服務')
            time.sleep(1)
            push_message(user_id, f'{user_name} 你好')
        except Exception as e:
            app.logger.error(f"取得個人資料或推播訊息失敗: {e}")
            reply_message(event.reply_token, '已轉接到Gemini AI服務，你好！')
    else:
        # 從 Firestore 讀取該使用者的 gemini_mode 狀態
        try:
            user_doc = user_doc_ref.get()
            # 如果文件存在且 gemini_mode 為 True，則進入 AI 模式
            if user_doc.exists and user_doc.to_dict().get('gemini_mode') is True:
                reply_text = gemini_with_memory(user_text)
                reply_message(event.reply_token, reply_text)
            else:
                # 否則，進入鸚鵡模式
                reply_message(event.reply_token, user_text)
        except Exception as e:
            app.logger.error(f"讀取使用者狀態或呼叫 Gemini 時發生錯誤: {e}")
            reply_message(event.reply_token, "處理你的訊息時發生了錯誤。")


def gemini_with_memory(user_text: str) -> str:
    """使用 Chat Session 處理有記憶的、具備時間感知的對話"""
    
    system_instruction = (
        "你是大概25歲的男士，你的名字叫 Si2sun。\n"
        "固定用繁體中文字回答\n"
        "時區固定UTC+08:00台灣時區"
    )

    model = genai.GenerativeModel(
        model_name='gemini-2.5-flash', # 修正為有效的模型名稱
        system_instruction=system_instruction
    )

    # 讀取並格式化 Firestore 的對話歷史
    try:
        doc = chat_memory_ref.get() # 使用對話歷史專用的文件參考
        memory_from_db = doc.to_dict().get('memory', []) if doc.exists else []

        chat_history = []
        for message in memory_from_db:
            role = 'user' if message.get('name') == 'user' else 'model'
            full_text_content = message.get('text', '')
            chat_history.append({'role': role, 'parts': [full_text_content]})

    except Exception as e:
        app.logger.error(f"讀取或格式化 Firestore 歷史紀錄失敗: {e}")
        chat_history = []
        memory_from_db = []
    
    # 獲取當前台北時間
    taipei_now = datetime.datetime.now(ZoneInfo('Asia/Taipei'))
    current_time_str = taipei_now.strftime("%Y-m-d %H:%M:%S")
    
    # 組合這次要傳送給模型的完整訊息
    full_user_text = f"[{current_time_str}] {user_text}"

    # 帶著歷史紀錄啟動一個對話 Session
    chat = model.start_chat(history=chat_history)

    # 傳送新訊息並取得模型回覆
    try:
        response = chat.send_message(full_user_text)
        model_response_text = response.text
    except Exception as e:
        app.logger.error(f"呼叫 Gemini API 時發生錯誤: {e}")
        model_response_text = "抱歉，我暫時無法回應你的訊息。"

    # 將新的對話回合儲存回記憶體中的列表
    memory_from_db.append({'name': 'user', 'text': full_user_text})
    memory_from_db.append({'name': 'model', 'text': f"[{current_time_str}] {model_response_text}"})

    # 將更新後的完整對話列表寫回 Firestore
    try:
        chat_memory_ref.set({'memory': memory_from_db})
    except Exception as e:
        app.logger.error(f"寫入 Firestore 失敗: {e}")
        
    return model_response_text

# --- LINE Messaging API 輔助函式 (保持不變) ---
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
    port = int(os.environ.get("PORT", 5001))
    app.run(host='0.0.0.0', port=port, debug=True)


