# 1. 基底映像檔 (Base Image)
# 使用官方的 Python 3.11 slim 版本，它是一個輕量級的 Debian 發行版
FROM python:3.11-slim

# 2. 設定工作目錄 (Working Directory)
# 在容器中建立一個目錄來存放應用程式檔案
WORKDIR /app

# 3. 複製並安裝依賴套件 (Copy and Install Dependencies)
# 這是優化映像檔建置速度的技巧，只要 requirements.txt 沒變，這一層就不會重新執行
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. 複製應用程式原始碼與設定檔 (Copy Application Code and Configs)
# 將目前目錄下的所有檔案複製到容器的 /app 目錄中
COPY . .

# 5. 設定環境變數 (Environment Variables)
# 設定 Google Cloud 憑證的環境變數，指向我們複製進來的金鑰檔案
# !!! 注意：請將 'si2sun.json' 換成你自己的金鑰檔案名稱 !!!
ENV GOOGLE_APPLICATION_CREDENTIALS=si2sun.json

# 6. 開放通訊埠 (Expose Port)
# 這行其實可以省略，因為 Render 會忽略它，但保留也無妨
EXPOSE 5001

# 7. 執行命令 (Entrypoint Command)
# 將 5001 硬性設定，改為讀取 $PORT 環境變數
# Render 會自動設定這個變數，Gunicorn 會讀取它

CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:$PORT", "line_gemini_firestore:app"]
# CMD ["python", "line_gemini_firestore.py"]

