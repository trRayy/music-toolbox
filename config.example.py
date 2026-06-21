"""Example local configuration.

Copy this file to config.py and fill in your own local values.
Never commit config.py or real credentials.
"""

MYSQL_CONFIG = {
    "host": "localhost",
    "port": 3306,
    "user": "music_user",
    "password": "CHANGE_ME",
    "db": "music_db",
    "charset": "utf8mb4",
    "connect_timeout": 5,
    "read_timeout": 10,
    "write_timeout": 10,
}

TME_HEADER_TOKEN = "YOUR_TME_HEADER_TOKEN"
QQMUSIC_COOKIE = "uin=YOUR_QQ_UIN; qqmusic_key=YOUR_QQMUSIC_KEY; qm_keyst=YOUR_QM_KEYST"
NETEASE_COOKIE = "YOUR_NETEASE_COOKIE"

FEISHU_APP_ID = "YOUR_FEISHU_APP_ID"
FEISHU_APP_SECRET = "YOUR_FEISHU_APP_SECRET"
FEISHU_REDIRECT_URI = "http://127.0.0.1:8000/callback"
FEISHU_USER_ACCESS_TOKEN = "YOUR_FEISHU_USER_ACCESS_TOKEN"
FEISHU_DEFAULT_SHEET_URL = "https://example.feishu.cn/sheets/YOUR_SPREADSHEET_TOKEN"
FEISHU_DEFAULT_WORKSHEET_ID = "YOUR_WORKSHEET_ID"

DASHBOARD_SCRIPT = r"C:\path\to\music_dashboard63.py"
TESSERACT_CMD = r"C:\path\to\Tesseract-OCR\tesseract.exe"
