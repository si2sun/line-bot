# 1. 基底映像档
FROM python:3.11-slim

# 2. 設定工作目錄
WORKDIR /app

# 3. 複製並安裝依賴套件
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. 複製應用程式原始碼
COPY . .

# 5. 執行命令
# 使用 "Shell Form" (普通字串) 來確保 shell 可以處理 $PORT 環境變數
# 使用 gunicorn 來運行你的 Flask (WSGI) 應用
CMD gunicorn -w 4 -b 0.0.0.0:$PORT line_gemini_firestore:app
# CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:$PORT", "line_gemini_firestore:app"]
# CMD ["uvicorn", "line_gemini_firestore:app", "--host", "0.0.0.0", "--port", "5001"]
# CMD ["python", "line_gemini_firestore.py"]




