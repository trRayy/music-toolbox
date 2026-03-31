import tkinter as tk
from tkinter import ttk, messagebox
import subprocess
import sys
import threading
import queue
import traceback
import os
import pymysql
import socket
import time
import webbrowser
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
import mysql_table_to_feishu_sheet as feishu_sheet_import

# ========= 导入模块 =========
def optional_import(module_name: str):
    try:
        return __import__(module_name), None
    except Exception as exc:
        return None, exc


qqmusic_comment_crawler, QQMUSIC_IMPORT_ERROR = optional_import("qqmusic_comment_crawler")
tme_crawler, TME_IMPORT_ERROR = optional_import("tme_crawler")
qishui_ocr, QISHUI_IMPORT_ERROR = optional_import("qishui_ocr")
wangyiyun_comment, WANGYIYUN_IMPORT_ERROR = optional_import("wangyiyun_comment")

# ========= 配置 =========
DASHBOARD_SCRIPT = r"C:\Users\User\Desktop\auto\music_dashboard63.py"
DASHBOARD_PORT = 8513

MYSQL_CONFIG = {
    'host': 'localhost',
    'port': 3306,
    'user': 'root',
    'password': 'root',
    'db': 't_music_data',
    'charset': 'utf8mb4',
    'connect_timeout': 5,
    'read_timeout': 10,
    'write_timeout': 10,
}

HEADERS_TME = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36 Edg/145.0.0.0',
    'Referer': 'https://y.tencentmusic.com/',
    'tme-header-token': 'eyJhbGciOiJIUzI1NiJ9.eyJqdGkiOiJ0bWUiLCJpYXQiOjE3NzMxMDQ2NDcsInN1YiI6InBhc3NwcG9ydCIsInBob25lIjoiKzg2KjE4NTE2MDYwNzM5IiwibG9naW5UeXBlIjoyLCJtaWQiOjQ3OTcwNjMsInRlbmFudCI6Im11c2ljaWFuIiwibG9naW5Tb3VyY2UiOm51bGwsImV4cCI6MTc3NTY5NjY0N30.WasX7U1WWxwvdH60bgEScsq444pnjautyrj3Z7MLIfY',
    'tme-header-feferer': '/',
    'tme-header-herf': 'https://y.tencentmusic.com/#/user/organdata/works/detail/15181859',
    'tme-header-trace': '7685rupgeh0obfrm4lrcasqil88j0u6g',
    'tme-source-platform': '0',
    'Content-Type': 'application/json;charset=utf-8'
}

QISHUI_PLATFORM_ID_DEFAULT = 5
QISHUI_OUT_DIR_DEFAULT = "dy_music_ocr_out"
TESSERACT_CMD_DEFAULT = r"C:\Users\User\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"
WAIT_SEC_DEFAULT = 6

TME_PLATFORM_CODES = ("qyin", "kugou", "kuwo")
DEFAULT_CODE_TO_PLATFORM_ID = {"qyin": 1, "kugou": 2, "kuwo": 3}
WANGYIYUN_PLATFORM_ID_DEFAULT = 4
QQMUSIC_DEFAULT_COOKIE_STR = getattr(qqmusic_comment_crawler, "DEFAULT_COOKIE_STR", "")
WANGYIYUN_COOKIE_ENV = getattr(wangyiyun_comment, "DEFAULT_COOKIE_ENV", "NETEASE_COOKIE")
WANGYIYUN_TARGET_TABLE = getattr(wangyiyun_comment, "DEFAULT_TARGET_TABLE", "t_comment")
WANGYIYUN_TEST_TABLE = getattr(wangyiyun_comment, "DEFAULT_TEST_TABLE", "t_comment_wangyiyun_test")
FEISHU_TOKEN_ENV = "FEISHU_USER_ACCESS_TOKEN"
FEISHU_DEFAULT_SHEET_URL = "https://gx1mlm3tj1l.feishu.cn/sheets/GdpOsM9orhCph3tbWFicv1YJn1g"
FEISHU_DEFAULT_WORKSHEET_ID = "db7efd"


# ========= 通用小工具 =========
def log_print(text_widget: tk.Text, msg: str):
    text_widget.insert("end", msg + "\n")
    text_widget.see("end")


def db_fetch_one(sql, params=()):
    conn = pymysql.connect(**MYSQL_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()
    finally:
        conn.close()


def db_fetch_all(sql, params=()):
    conn = pymysql.connect(**MYSQL_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def is_port_free(port: int, host: str = "127.0.0.1") -> bool:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.2)
        result = s.connect_ex((host, port))
        s.close()
        return result != 0
    except Exception:
        return False


def find_free_port(start_port: int, max_tries: int = 50) -> int:
    port = int(start_port)
    for _ in range(max_tries):
        if is_port_free(port):
            return port
        port += 1
    raise RuntimeError(f"从 {start_port} 开始连续 {max_tries} 个端口都不可用")


def get_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


def is_friendly_tme_error(e: Exception) -> bool:
    return e.__class__.__name__ == "FriendlyTMEError"


def tz_cn():
    return timezone(timedelta(hours=8))


def ms_to_cn_date(ms: int) -> str:
    dt = datetime.fromtimestamp(int(ms) / 1000, tz=tz_cn())
    return dt.strftime("%Y-%m-%d")


def safe_int(v, default=0):
    try:
        return int(v or 0)
    except Exception:
        return default


def ensure_feature_module(module_obj, import_error, feature_name: str):
    if module_obj is None:
        raise RuntimeError(f"{feature_name} 不可用，缺少依赖：{import_error}")
    return module_obj


# ========= QQ音乐评论数累计更新 =========
def update_qqmusic_comment_cumulative(song_id: int, logger=None):
    if logger is None:
        logger = print
    
    conn = None
    try:
        conn = pymysql.connect(**MYSQL_CONFIG)
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        logger("🔍 开始更新QQ音乐评论累计数（song_id={}）".format(song_id))

        comment_sql = """
        SELECT 
            song_id,
            DATE(IFNULL(publish_time, crawl_time)) AS publish_date,
            COUNT(*) AS day_count
        FROM t_comment
        WHERE platform_id = 1 
          AND is_deleted = 0
          AND song_id = %s
          AND IFNULL(publish_time, crawl_time) IS NOT NULL
        GROUP BY song_id, DATE(IFNULL(publish_time, crawl_time))
        ORDER BY publish_date;
        """
        cursor.execute(comment_sql, (song_id,))
        comment_data = cursor.fetchall()
        
        if not comment_data:
            logger("⚠️ 未找到该歌曲的QQ音乐有效评论数据，跳过评论数更新")
            return
        
        df = pd.DataFrame(comment_data)
        df['publish_date'] = pd.to_datetime(df['publish_date']).dt.date
        df['cumulative_count'] = df.groupby('song_id')['day_count'].cumsum()
        
        history_sql = """
        SELECT DISTINCT record_date
        FROM t_song_interaction_history
        WHERE platform_id = 1 
          AND song_id = %s
        ORDER BY record_date;
        """
        cursor.execute(history_sql, (song_id,))
        history_data = cursor.fetchall()
        if not history_data:
            logger("⚠️ 该歌曲无互动量表数据，跳过评论数更新")
            return
        
        df_history = pd.DataFrame(history_data)
        df_history['record_date'] = pd.to_datetime(df_history['record_date']).dt.date
        
        df_merged = pd.merge(
            df_history,
            df[['publish_date', 'cumulative_count']],
            left_on='record_date',
            right_on='publish_date',
            how='left'
        )
        df_merged['cumulative_count'] = df_merged['cumulative_count'].ffill().fillna(0).astype(int)
        
        update_sql = """
        UPDATE t_song_interaction_history
        SET comment_count = %s
        WHERE song_id = %s
          AND platform_id = 1
          AND record_date = %s;
        """
        update_count = 0
        for _, row in df_merged.iterrows():
            cursor.execute(update_sql, (
                row['cumulative_count'],
                song_id,
                row['record_date']
            ))
            update_count += cursor.rowcount
        
        conn.commit()
        logger("✅ 成功更新 {} 条QQ音乐评论累计数".format(update_count))
        
        verify_sql = """
        SELECT record_date, comment_count
        FROM t_song_interaction_history
        WHERE song_id = %s AND platform_id = 1
        ORDER BY record_date DESC
        LIMIT 3;
        """
        cursor.execute(verify_sql, (song_id,))
        verify_result = cursor.fetchall()
        if verify_result:
            logger("📊 最新3条评论累计值验证：")
            for row in verify_result:
                logger(f"   日期：{row['record_date']} | 累计评论数：{row['comment_count']}")
                
    except Exception as e:
        if conn:
            conn.rollback()
        logger("❌ 更新QQ音乐评论数失败：{}".format(str(e)))
        logger(traceback.format_exc())
    finally:
        if conn:
            cursor.close()
            conn.close()


def update_wangyiyun_comment_cumulative(song_id: int, logger=None):
    if logger is None:
        logger = print

    conn = None
    try:
        conn = pymysql.connect(**MYSQL_CONFIG)
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        logger(f"开始更新网易云评论累计数（song_id={song_id}）")

        comment_sql = """
        SELECT
            song_id,
            DATE(IFNULL(publish_time, crawl_time)) AS publish_date,
            COUNT(*) AS day_count
        FROM t_comment
        WHERE platform_id = 4
          AND is_deleted = 0
          AND song_id = %s
          AND IFNULL(publish_time, crawl_time) IS NOT NULL
        GROUP BY song_id, DATE(IFNULL(publish_time, crawl_time))
        ORDER BY publish_date;
        """
        cursor.execute(comment_sql, (song_id,))
        comment_data = cursor.fetchall()
        if not comment_data:
            logger("未找到该歌曲的网易云有效评论数据，跳过评论累计更新")
            return

        df = pd.DataFrame(comment_data)
        df["publish_date"] = pd.to_datetime(df["publish_date"]).dt.date
        df["cumulative_count"] = df.groupby("song_id")["day_count"].cumsum()

        history_sql = """
        SELECT DISTINCT record_date
        FROM t_song_interaction_history
        WHERE platform_id = 4
          AND song_id = %s
        ORDER BY record_date;
        """
        cursor.execute(history_sql, (song_id,))
        history_data = cursor.fetchall()
        if not history_data:
            logger("该歌曲没有网易云互动历史数据，跳过评论累计更新")
            return

        df_history = pd.DataFrame(history_data)
        df_history["record_date"] = pd.to_datetime(df_history["record_date"]).dt.date

        df_merged = pd.merge(
            df_history,
            df[["publish_date", "cumulative_count"]],
            left_on="record_date",
            right_on="publish_date",
            how="left",
        )
        df_merged["cumulative_count"] = df_merged["cumulative_count"].ffill().fillna(0).astype(int)

        update_sql = """
        UPDATE t_song_interaction_history
        SET comment_count = %s
        WHERE song_id = %s
          AND platform_id = 4
          AND record_date = %s;
        """
        update_count = 0
        for _, row in df_merged.iterrows():
            cursor.execute(update_sql, (row["cumulative_count"], song_id, row["record_date"]))
            update_count += cursor.rowcount

        conn.commit()
        logger(f"成功更新 {update_count} 条网易云评论累计数")

    except Exception as e:
        if conn:
            conn.rollback()
        logger(f"更新网易云评论累计数失败：{e}")
        logger(traceback.format_exc())
    finally:
        if conn:
            cursor.close()
            conn.close()


def upsert_qqmusic_comment_cumulative(song_id: int, logger=None):
    if logger is None:
        logger = print

    conn = None
    try:
        conn = pymysql.connect(**MYSQL_CONFIG)
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        logger(f"开始回填QQ音乐评论累计数（song_id={song_id}）")

        comment_sql = """
        SELECT
            DATE(IFNULL(publish_time, crawl_time)) AS publish_date,
            COUNT(*) AS day_count
        FROM t_comment
        WHERE platform_id = 1
          AND is_deleted = 0
          AND song_id = %s
          AND IFNULL(publish_time, crawl_time) IS NOT NULL
        GROUP BY DATE(IFNULL(publish_time, crawl_time))
        ORDER BY publish_date;
        """
        cursor.execute(comment_sql, (song_id,))
        comment_data = cursor.fetchall()
        if not comment_data:
            logger("未找到该歌曲的QQ音乐有效评论数据，跳过评论累计回填")
            return

        df = pd.DataFrame(comment_data)
        df["publish_date"] = pd.to_datetime(df["publish_date"]).dt.date
        df["cumulative_count"] = df["day_count"].cumsum()

        select_sql = """
        SELECT id
        FROM t_song_interaction_history
        WHERE song_id = %s AND platform_id = 1 AND record_date = %s
        LIMIT 1;
        """
        update_sql = """
        UPDATE t_song_interaction_history
        SET comment_count = %s
        WHERE id = %s;
        """
        insert_sql = """
        INSERT INTO t_song_interaction_history
          (song_id, platform_id, comment_count, collect_count, share_count, record_date)
        VALUES
          (%s, 1, %s, 0, 0, %s);
        """

        update_count = 0
        insert_count = 0
        for _, row in df.iterrows():
            record_date = row["publish_date"]
            cumulative_count = int(row["cumulative_count"])
            cursor.execute(select_sql, (song_id, record_date))
            existing = cursor.fetchone()
            if existing:
                cursor.execute(update_sql, (cumulative_count, existing["id"]))
                update_count += cursor.rowcount
            else:
                cursor.execute(insert_sql, (song_id, cumulative_count, record_date))
                insert_count += cursor.rowcount

        conn.commit()
        logger(f"QQ音乐评论累计回填完成：更新 {update_count} 条，新增 {insert_count} 条")

    except Exception as e:
        if conn:
            conn.rollback()
        logger(f"QQ音乐评论累计回填失败：{e}")
        logger(traceback.format_exc())
    finally:
        if conn:
            cursor.close()
            conn.close()


def upsert_wangyiyun_comment_cumulative(song_id: int, logger=None):
    if logger is None:
        logger = print

    conn = None
    try:
        conn = pymysql.connect(**MYSQL_CONFIG)
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        logger(f"开始回填网易云评论累计数（song_id={song_id}）")

        comment_sql = """
        SELECT
            DATE(IFNULL(publish_time, crawl_time)) AS publish_date,
            COUNT(*) AS day_count
        FROM t_comment
        WHERE platform_id = 4
          AND is_deleted = 0
          AND song_id = %s
          AND IFNULL(publish_time, crawl_time) IS NOT NULL
        GROUP BY DATE(IFNULL(publish_time, crawl_time))
        ORDER BY publish_date;
        """
        cursor.execute(comment_sql, (song_id,))
        comment_data = cursor.fetchall()
        if not comment_data:
            logger("未找到该歌曲的网易云有效评论数据，跳过评论累计回填")
            return

        df = pd.DataFrame(comment_data)
        df["publish_date"] = pd.to_datetime(df["publish_date"]).dt.date
        df["cumulative_count"] = df["day_count"].cumsum()

        select_sql = """
        SELECT id
        FROM t_song_interaction_history
        WHERE song_id = %s AND platform_id = 4 AND record_date = %s
        LIMIT 1;
        """
        update_sql = """
        UPDATE t_song_interaction_history
        SET comment_count = %s
        WHERE id = %s;
        """
        insert_sql = """
        INSERT INTO t_song_interaction_history
          (song_id, platform_id, comment_count, collect_count, share_count, record_date)
        VALUES
          (%s, 4, %s, 0, 0, %s);
        """

        update_count = 0
        insert_count = 0
        for _, row in df.iterrows():
            record_date = row["publish_date"]
            cumulative_count = int(row["cumulative_count"])
            cursor.execute(select_sql, (song_id, record_date))
            existing = cursor.fetchone()
            if existing:
                cursor.execute(update_sql, (cumulative_count, existing["id"]))
                update_count += cursor.rowcount
            else:
                cursor.execute(insert_sql, (song_id, cumulative_count, record_date))
                insert_count += cursor.rowcount

        conn.commit()
        logger(f"网易云评论累计回填完成：更新 {update_count} 条，新增 {insert_count} 条")

    except Exception as e:
        if conn:
            conn.rollback()
        logger(f"网易云评论累计回填失败：{e}")
        logger(traceback.format_exc())
    finally:
        if conn:
            cursor.close()
            conn.close()


# ========= Tab：查看数据库 =========
class MySQLViewer(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.conn = None

        ctrl = ttk.Frame(self)
        ctrl.pack(fill="x", padx=10, pady=10)

        ttk.Label(ctrl, text="表：").pack(side="left")
        self.table_var = tk.StringVar()
        self.table_combo = ttk.Combobox(ctrl, textvariable=self.table_var, state="readonly", width=28)
        self.table_combo.pack(side="left", padx=6)
        self.table_combo.bind("<<ComboboxSelected>>", lambda e: self.load_data())

        ttk.Label(ctrl, text="限制行数：").pack(side="left")
        self.limit_var = tk.StringVar(value="200")
        ttk.Entry(ctrl, textvariable=self.limit_var, width=8).pack(side="left", padx=6)

        ttk.Button(ctrl, text="连接/刷新表", command=self.load_tables).pack(side="left", padx=6)
        ttk.Button(ctrl, text="刷新数据", command=self.load_data).pack(side="left")

        self.status_var = tk.StringVar(value="未连接数据库")
        ttk.Label(self, textvariable=self.status_var).pack(anchor="w", padx=10)

        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, padx=10, pady=10)

        self.tree = ttk.Treeview(frame, show="headings")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        self.after(150, self.load_tables)

    def connect(self):
        if self.conn:
            return True
        try:
            self.conn = pymysql.connect(**MYSQL_CONFIG)
            return True
        except Exception as e:
            messagebox.showerror("连接失败", str(e))
            self.conn = None
            return False

    def load_tables(self):
        if not self.connect():
            return
        try:
            with self.conn.cursor() as cur:
                cur.execute("SHOW TABLES;")
                tables = [r[0] for r in cur.fetchall()]
            self.table_combo["values"] = tables
            if tables:
                self.table_combo.current(0)
                self.status_var.set(f"已连接：{MYSQL_CONFIG['db']}（{len(tables)} 张表）")
                self.load_data()
        except Exception as e:
            messagebox.showerror("读取表失败", str(e))

    def load_data(self):
        if not self.connect():
            return
        table = self.table_var.get()
        if not table:
            return
        try:
            limit = int(self.limit_var.get())
        except Exception:
            messagebox.showerror("参数错误", "限制行数必须是整数")
            return

        try:
            with self.conn.cursor() as cur:
                cur.execute(f"DESCRIBE `{table}`;")
                cols = [r[0] for r in cur.fetchall()]
                cur.execute(f"SELECT * FROM `{table}` LIMIT %s;", (limit,))
                rows = cur.fetchall()

            self.tree["columns"] = cols
            for c in cols:
                self.tree.heading(c, text=c)
                self.tree.column(c, width=140, stretch=True)

            for i in self.tree.get_children():
                self.tree.delete(i)
            for row in rows:
                self.tree.insert("", "end", values=row)

        except Exception as e:
            messagebox.showerror("查询失败", str(e))


# ========= Tab：腾讯更新（播放趋势）=========
class ExportMySQLToFeishuTab(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)

        form = ttk.Frame(self)
        form.pack(fill="x", padx=12, pady=12)

        ttk.Label(form, text="MySQL表：").grid(row=0, column=0, sticky="w")
        self.table_var = tk.StringVar()
        self.table_combo = ttk.Combobox(form, textvariable=self.table_var, state="readonly", width=28)
        self.table_combo.grid(row=0, column=1, sticky="w", padx=8)
        ttk.Button(form, text="刷新表列表", command=self.load_tables).grid(row=0, column=2, sticky="w")

        ttk.Label(form, text="飞书表格链接：").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.sheet_url_var = tk.StringVar(value=FEISHU_DEFAULT_SHEET_URL)
        ttk.Entry(form, textvariable=self.sheet_url_var, width=90).grid(
            row=1, column=1, columnspan=4, sticky="we", padx=8, pady=(10, 0)
        )

        ttk.Label(form, text="工作表ID：").grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.worksheet_id_var = tk.StringVar(value=FEISHU_DEFAULT_WORKSHEET_ID)
        ttk.Entry(form, textvariable=self.worksheet_id_var, width=20).grid(
            row=2, column=1, sticky="w", padx=8, pady=(10, 0)
        )

        ttk.Label(form, text="工作表标题：").grid(row=2, column=2, sticky="w", pady=(10, 0))
        self.worksheet_title_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.worksheet_title_var, width=24).grid(
            row=2, column=3, sticky="w", padx=8, pady=(10, 0)
        )

        ttk.Label(form, text="起始单元格：").grid(row=3, column=0, sticky="w", pady=(10, 0))
        self.start_cell_var = tk.StringVar(value="A1")
        ttk.Entry(form, textvariable=self.start_cell_var, width=12).grid(
            row=3, column=1, sticky="w", padx=8, pady=(10, 0)
        )

        ttk.Label(form, text="批次行数：").grid(row=3, column=2, sticky="w", pady=(10, 0))
        self.fetch_size_var = tk.StringVar(value="500")
        ttk.Entry(form, textvariable=self.fetch_size_var, width=12).grid(
            row=3, column=3, sticky="w", padx=8, pady=(10, 0)
        )

        ttk.Label(form, text="user_access_token：").grid(row=4, column=0, sticky="w", pady=(10, 0))
        self.token_var = tk.StringVar(value=os.getenv(FEISHU_TOKEN_ENV, ""))
        ttk.Entry(form, textvariable=self.token_var, width=90).grid(
            row=4, column=1, columnspan=4, sticky="we", padx=8, pady=(10, 0)
        )

        btns = ttk.Frame(form)
        btns.grid(row=5, column=0, columnspan=5, sticky="w", pady=(12, 0))

        ttk.Button(btns, text="从环境读取Token", command=self.load_token_from_env).pack(side="left")
        self.inspect_btn = ttk.Button(btns, text="读取工作表", command=self.start_inspect)
        self.inspect_btn.pack(side="left", padx=8)
        ttk.Button(btns, text="打开飞书表格", command=self.open_sheet_url).pack(side="left")
        self.run_btn = ttk.Button(btns, text="导入到飞书表格", command=self.start_export)
        self.run_btn.pack(side="left", padx=8)

        tips = (
            "说明：会把所选 MySQL 表的表头和数据按起始单元格写入飞书。"
            " 当前不会自动清空旧数据；如果新表比旧表短，原表格末尾可能保留旧内容。"
        )
        ttk.Label(form, text=tips).grid(row=6, column=0, columnspan=5, sticky="w", pady=(10, 0))

        self.log = tk.Text(self, height=20)
        self.log.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.q = queue.Queue()
        self.load_tables()
        self.after(100, self.flush_log)

    def flush_log(self):
        while True:
            try:
                msg = self.q.get_nowait()
            except queue.Empty:
                break
            log_print(self.log, msg)
        self.after(100, self.flush_log)

    def set_busy(self, busy: bool):
        state = "disabled" if busy else "normal"
        self.after(0, lambda: self.run_btn.config(state=state))
        self.after(0, lambda: self.inspect_btn.config(state=state))

    def load_tables(self):
        try:
            rows = db_fetch_all("SHOW TABLES;")
            tables = [r[0] for r in rows]
            current = self.table_var.get()
            self.table_combo["values"] = tables
            if current in tables:
                self.table_var.set(current)
            elif tables:
                self.table_combo.current(0)
            self.q.put(f"已加载数据表 {len(tables)} 张，数据库：{MYSQL_CONFIG['db']}")
        except Exception as e:
            messagebox.showerror("加载数据表失败", str(e))

    def load_token_from_env(self):
        token = os.getenv(FEISHU_TOKEN_ENV, "").strip()
        self.token_var.set(token)
        if token:
            self.q.put(f"已从环境变量 {FEISHU_TOKEN_ENV} 读取 token，长度={len(token)}")
        else:
            self.q.put(f"环境变量 {FEISHU_TOKEN_ENV} 为空，请手动粘贴 user_access_token")

    def open_sheet_url(self):
        sheet_url = self.sheet_url_var.get().strip()
        if not sheet_url:
            messagebox.showwarning("提示", "请先填写飞书表格链接")
            return
        try:
            webbrowser.open(sheet_url)
        except Exception as e:
            messagebox.showerror("打开失败", str(e))

    def get_token(self) -> str:
        token = self.token_var.get().strip() or os.getenv(FEISHU_TOKEN_ENV, "").strip()
        if not token:
            raise ValueError("请先填写 user_access_token，或设置环境变量 FEISHU_USER_ACCESS_TOKEN")
        return token

    def start_inspect(self):
        try:
            sheet_url = self.sheet_url_var.get().strip()
            token = self.get_token()
            spreadsheet_token = feishu_sheet_import.extract_spreadsheet_token(sheet_url)
        except Exception as e:
            messagebox.showwarning("提示", str(e))
            return

        self.set_busy(True)
        self.q.put(f"开始读取飞书工作表列表：{spreadsheet_token}")
        th = threading.Thread(target=self._inspect_worker, args=(spreadsheet_token, token), daemon=True)
        th.start()

    def _inspect_worker(self, spreadsheet_token: str, token: str):
        try:
            sheets = feishu_sheet_import.get_sheets(spreadsheet_token, token)
            self.q.put(f"工作表数量：{len(sheets)}")
            for sheet in sheets:
                grid = sheet.get("grid_properties") or {}
                self.q.put(
                    f"- {sheet.get('title')} | sheet_id={sheet.get('sheet_id')} | "
                    f"{grid.get('row_count')}行 x {grid.get('column_count')}列"
                )
            if sheets and not self.worksheet_id_var.get().strip():
                first_sheet_id = str(sheets[0].get("sheet_id") or "")
                self.after(0, lambda: self.worksheet_id_var.set(first_sheet_id))
        except Exception as e:
            self.q.put("读取工作表失败：" + str(e))
            self.q.put(traceback.format_exc())
        finally:
            self.set_busy(False)

    def start_export(self):
        table_name = self.table_var.get().strip()
        if not table_name:
            messagebox.showwarning("提示", "请先选择 MySQL 表")
            return

        try:
            token = self.get_token()
            fetch_size = int(self.fetch_size_var.get().strip())
            feishu_sheet_import.require_positive(fetch_size, "fetch_size")
            spreadsheet_token = feishu_sheet_import.extract_spreadsheet_token(self.sheet_url_var.get().strip())
            start_cell = self.start_cell_var.get().strip().upper()
            feishu_sheet_import.parse_cell_reference(start_cell)
        except Exception as e:
            messagebox.showwarning("提示", str(e))
            return

        worksheet_id = self.worksheet_id_var.get().strip()
        worksheet_title = self.worksheet_title_var.get().strip()

        self.set_busy(True)
        self.q.put("")
        self.q.put(
            f"开始导入：table={table_name} | spreadsheet={spreadsheet_token} | "
            f"worksheet_id={worksheet_id or '-'} | worksheet_title={worksheet_title or '-'} | start_cell={start_cell}"
        )
        th = threading.Thread(
            target=self._export_worker,
            args=(table_name, spreadsheet_token, worksheet_id, worksheet_title, start_cell, fetch_size, token),
            daemon=True,
        )
        th.start()

    def _export_worker(
        self,
        table_name: str,
        spreadsheet_token: str,
        worksheet_id: str,
        worksheet_title: str,
        start_cell: str,
        fetch_size: int,
        token: str,
    ):
        try:
            table_name = feishu_sheet_import.validate_table_name(table_name)
            mysql_config = dict(MYSQL_CONFIG)
            start_row, start_col = feishu_sheet_import.parse_cell_reference(start_cell)

            self.q.put(f"读取 MySQL 表结构：{table_name}")
            columns, row_count = feishu_sheet_import.get_table_columns_and_count(mysql_config, table_name)
            self.q.put(f"MySQL 表信息：{len(columns)} 列，{row_count} 行")

            sheets = feishu_sheet_import.get_sheets(spreadsheet_token, token)
            target_sheet = feishu_sheet_import.choose_sheet(sheets, worksheet_id, worksheet_title)
            sheet_id = str(target_sheet.get("sheet_id") or "")
            sheet_title = str(target_sheet.get("title") or "")
            feishu_sheet_import.ensure_sheet_capacity(
                target_sheet, start_row, start_col, row_count + 1, len(columns)
            )
            self.q.put(f"目标工作表：{sheet_title} ({sheet_id})")

            self.q.put("写入表头...")
            feishu_sheet_import.write_values(
                spreadsheet_token,
                sheet_id,
                start_row,
                start_col,
                [columns],
                token,
            )

            next_row = start_row + 1
            written_rows = 0
            for batch_index, rows in enumerate(
                feishu_sheet_import.iter_table_rows(mysql_config, table_name, fetch_size),
                start=1,
            ):
                feishu_sheet_import.write_values(
                    spreadsheet_token,
                    sheet_id,
                    next_row,
                    start_col,
                    rows,
                    token,
                )
                next_row += len(rows)
                written_rows += len(rows)
                self.q.put(f"批次 {batch_index} 写入 {len(rows)} 行，累计 {written_rows}/{row_count}")

            self.q.put(f"导入完成：已写入表头 1 行，数据 {written_rows} 行")
        except Exception as e:
            self.q.put("导入飞书表格失败：" + str(e))
            self.q.put(traceback.format_exc())
        finally:
            self.set_busy(False)


class UpdateTabTME(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)

        form = ttk.Frame(self)
        form.pack(fill="x", padx=12, pady=12)

        ttk.Label(form, text="选择歌曲：").grid(row=0, column=0, sticky="w")
        self.song_var = tk.StringVar()
        self.song_combo = ttk.Combobox(form, textvariable=self.song_var, state="readonly", width=40)
        self.song_combo.grid(row=0, column=1, sticky="w", padx=8)

        ttk.Label(form, text="最近N天：").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.days_var = tk.StringVar(value="30")
        ttk.Entry(form, textvariable=self.days_var, width=10).grid(row=1, column=1, sticky="w", padx=8, pady=(10, 0))

        self.run_btn = ttk.Button(form, text="开始更新（腾讯端）", command=self.start_update)
        self.run_btn.grid(row=2, column=1, sticky="w", pady=(12, 0))

        ttk.Button(form, text="刷新歌曲列表", command=self.load_songs).grid(row=2, column=2, sticky="w", pady=(12, 0))

        self.log = tk.Text(self, height=18)
        self.log.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.q = queue.Queue()
        self.load_songs()
        self.after(100, self.flush_log)

    def load_songs(self):
        try:
            rows = tme_crawler.list_songs(MYSQL_CONFIG)
            self.song_map = {f"{sid} - {name}": sid for sid, name in rows}
            self.song_combo["values"] = list(self.song_map.keys())
            if rows:
                self.song_combo.current(0)
            log_print(self.log, f"✅ 已加载歌曲 {len(rows)} 首")
        except Exception as e:
            messagebox.showerror("加载歌曲失败", str(e))

    def flush_log(self):
        while True:
            try:
                msg = self.q.get_nowait()
            except queue.Empty:
                break
            log_print(self.log, msg)
        self.after(100, self.flush_log)

    def _get_tme_song_id(self, song_id: int):
        try:
            row = db_fetch_one("SELECT tme_song_id FROM t_song_tme WHERE song_id=%s LIMIT 1;", (song_id,))
            if row and row[0]:
                return str(row[0]), "t_song_tme"
        except Exception:
            pass

        try:
            codes = tme_crawler.get_song_platform_codes(MYSQL_CONFIG, song_id)
            qq_code = codes.get(1)
            if qq_code:
                return str(qq_code), "t_song_platform(platform_id=1)"
        except Exception:
            pass

        return None, None

    def start_update(self):
        label = self.song_var.get()
        if not label or label not in self.song_map:
            messagebox.showwarning("提示", "请先选择歌曲")
            return

        try:
            days = int(self.days_var.get())
        except Exception:
            messagebox.showwarning("提示", "最近N天必须是整数")
            return

        song_id = self.song_map[label]
        self.run_btn.config(state="disabled")
        self.q.put(f"🚀 开始更新：song_id={song_id} | 最近 {days} 天 | 来源=腾讯音乐端")

        th = threading.Thread(target=self._run_tme_update, args=(song_id, days), daemon=True)
        th.start()

    def _run_tme_update(self, song_id: int, days: int):
        try:
            tme_song_id, source = self._get_tme_song_id(song_id)
            if not tme_song_id:
                self.q.put("⚠️ 未找到腾讯端 song_id（建议在 t_song_tme 录入 tme_song_id）")
                self.q.put("   也未找到 t_song_platform 中 platform_id=1 的 QQ code，无法更新。")
                return

            self.q.put(f"🔎 使用 {source} 作为 tme_song_id：{tme_song_id}")

            df, start_date, end_date = tme_crawler.crawl_tme_play_trend(
                str(tme_song_id),
                HEADERS_TME,
                days_before=days
            )
            self.q.put(f"📅 爬取完成：{start_date} ~ {end_date}，共 {len(df)} 天")

            inserted = tme_crawler.insert_missing_play_stat(MYSQL_CONFIG, song_id, df, logger=self.q.put)

            if inserted == 0:
                self.q.put("✅ 数据已是最新：本次没有新增记录（正常情况）")
            else:
                self.q.put(f"✅ 入库完成：新增 {inserted} 条（只插缺失日期）")

            self.q.put("👉 现在可以去“Dashboard”页点按钮打开看板")

        except Exception as e:
            if is_friendly_tme_error(e):
                self.q.put(f"⚠️ {str(e)}")
                self.q.put("   （接口波动/登录态/限流导致，可稍后重试）")
            else:
                self.q.put("❌ 更新失败：" + str(e))
                self.q.put(traceback.format_exc())
        finally:
            self.run_btn.config(state="normal")


# ========= Tab：腾讯（收藏转发）=========
class UpdateTabTMEInteract(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)

        form = ttk.Frame(self)
        form.pack(fill="x", padx=12, pady=12)

        ttk.Label(form, text="选择歌曲：").grid(row=0, column=0, sticky="w")
        self.song_var = tk.StringVar()
        self.song_combo = ttk.Combobox(form, textvariable=self.song_var, state="readonly", width=40)
        self.song_combo.grid(row=0, column=1, sticky="w", padx=8)
        self.song_combo.bind("<<ComboboxSelected>>", lambda e: self.auto_fill_tme_song_id())

        ttk.Label(form, text="腾讯端 songId：").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.tme_songid_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.tme_songid_var, width=20).grid(row=1, column=1, sticky="w", padx=8, pady=(10, 0))

        ttk.Label(form, text="(优先 t_song_tme，其次 platform_id=4)").grid(row=1, column=2, sticky="w", pady=(10, 0))

        self.run_btn = ttk.Button(form, text="更新收藏/转发（腾讯端）", command=self.start_run)
        self.run_btn.grid(row=2, column=1, sticky="w", pady=(12, 0))

        ttk.Button(form, text="刷新歌曲列表", command=self.load_songs).grid(row=2, column=2, sticky="w", pady=(12, 0))

        self.log = tk.Text(self, height=18)
        self.log.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.q = queue.Queue()
        self.load_songs()
        self.after(100, self.flush_log)

    def flush_log(self):
        while True:
            try:
                msg = self.q.get_nowait()
            except queue.Empty:
                break
            log_print(self.log, msg)
        self.after(100, self.flush_log)

    def load_songs(self):
        try:
            rows = tme_crawler.list_songs(MYSQL_CONFIG)
            self.song_map = {f"{sid} - {name}": sid for sid, name in rows}
            self.song_combo["values"] = list(self.song_map.keys())
            if rows:
                self.song_combo.current(0)
                self.auto_fill_tme_song_id()
            self.q.put(f"✅ 已加载歌曲 {len(rows)} 首")
        except Exception as e:
            messagebox.showerror("加载歌曲失败", str(e))

    def _get_tme_song_id_for_total(self, song_id: int):
        try:
            row = db_fetch_one("SELECT tme_song_id FROM t_song_tme WHERE song_id=%s LIMIT 1;", (song_id,))
            if row and row[0]:
                return str(row[0]), "t_song_tme"
        except Exception:
            pass

        try:
            row = db_fetch_one(
                "SELECT song_platform_code FROM t_song_platform WHERE song_id=%s AND platform_id=4 LIMIT 1;",
                (song_id,),
            )
            if row and row[0]:
                return str(row[0]), "t_song_platform(platform_id=4)"
        except Exception:
            pass

        return None, None

    def auto_fill_tme_song_id(self):
        label = self.song_var.get()
        if not label or label not in self.song_map:
            return
        song_id = self.song_map[label]
        tme_song_id, source = self._get_tme_song_id_for_total(song_id)
        if tme_song_id:
            self.tme_songid_var.set(tme_song_id)
            self.q.put(f"🔎 已自动带出腾讯端 songId：{tme_song_id}（来源：{source}）")
        else:
            self.tme_songid_var.set("")
            self.q.put("⚠️ 未找到腾讯端数字 songId：请先在 t_song_tme 写入 tme_song_id，或在 t_song_platform(platform_id=4) 写入。")

    def _build_total_headers(self, tme_song_id: str) -> dict:
        h = dict(HEADERS_TME)
        h["tme-header-trace"] = f"trace_{int(time.time())}"
        h["tme-header-herf"] = f"https://y.tencentmusic.com/#/user/organdata/works/detail/{tme_song_id}"
        h["Referer"] = "https://y.tencentmusic.com/"
        return h

    def _fetch_total_json(self, tme_song_id: str) -> dict:
        url = f"https://y.tencentmusic.com/cd-gateway/musician/song/figure/total?songId={tme_song_id}"
        headers = self._build_total_headers(tme_song_id)
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        j = r.json()
        if str(j.get("code")) != "0" or not j.get("data"):
            raise RuntimeError(f"接口返回异常：code={j.get('code')} message={j.get('message')} data={j.get('data')}")
        return j["data"]

    def _get_code_to_platform_id(self) -> dict:
        try:
            rows = db_fetch_all("SELECT platform_id, platform_code FROM t_platform;")
            m = {code: int(pid) for pid, code in rows if code}
            ok = all(c in m for c in TME_PLATFORM_CODES)
            if ok:
                return m
        except Exception:
            pass
        return dict(DEFAULT_CODE_TO_PLATFORM_ID)

    def _upsert_collect_share(self, song_id: int, platform_id: int, record_date: str, collect_count: int, share_count: int):
        conn = pymysql.connect(**MYSQL_CONFIG)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id FROM t_song_interaction_history
                    WHERE song_id=%s AND platform_id=%s AND record_date=%s
                    LIMIT 1;
                    """,
                    (song_id, platform_id, record_date),
                )
                row = cur.fetchone()

                if row:
                    hid = row[0]
                    cur.execute(
                        """
                        UPDATE t_song_interaction_history
                        SET collect_count=%s, share_count=%s
                        WHERE id=%s;
                        """,
                        (collect_count, share_count, hid),
                    )
                    action = "更新"
                else:
                    cur.execute(
                        """
                        INSERT INTO t_song_interaction_history
                          (song_id, platform_id, comment_count, collect_count, share_count, record_date)
                        VALUES
                          (%s, %s, 0, %s, %s, %s);
                        """,
                        (song_id, platform_id, collect_count, share_count, record_date),
                    )
                    action = "插入"

            conn.commit()
            return action
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def start_run(self):
        try:
            ensure_feature_module(qishui_ocr, QISHUI_IMPORT_ERROR, "汽水OCR")
        except Exception as e:
            messagebox.showwarning("提示", str(e))
            return

        label = self.song_var.get()
        if not label or label not in self.song_map:
            messagebox.showwarning("提示", "请先选择歌曲")
            return

        tme_song_id = self.tme_songid_var.get().strip()
        if not tme_song_id:
            messagebox.showwarning("提示", "腾讯端 songId 为空：请先补齐 t_song_tme 或 t_song_platform(platform_id=4)")
            return

        song_id = self.song_map[label]
        self.run_btn.config(state="disabled")
        self.q.put(f"🚀 开始更新收藏/转发：song_id={song_id} | tme_songId={tme_song_id}")

        th = threading.Thread(target=self._run_worker, args=(song_id, tme_song_id), daemon=True)
        th.start()

    def _run_worker(self, song_id: int, tme_song_id: str):
        try:
            data = self._fetch_total_json(tme_song_id)

            if data.get("statisDay"):
                record_date = ms_to_cn_date(data["statisDay"])
            else:
                record_date = datetime.now(tz_cn()).strftime("%Y-%m-%d")

            favor_block = data.get("cumulativeFavorCnt", {}) or {}
            share_block = data.get("cumulativeShareCnt", {}) or {}

            self.q.put(f"📅 record_date = {record_date}")
            code_to_pid = self._get_code_to_platform_id()

            written = 0
            for code in TME_PLATFORM_CODES:
                pid = code_to_pid.get(code)
                if pid is None:
                    self.q.put(f"⚠️ 找不到 platform_code={code} 对应的 platform_id（请检查 t_platform 或默认映射）")
                    continue

                collect_count = safe_int(favor_block.get(code, 0))
                share_count = safe_int(share_block.get(code, 0))

                action = self._upsert_collect_share(
                    song_id=song_id,
                    platform_id=int(pid),
                    record_date=record_date,
                    collect_count=collect_count,
                    share_count=share_count,
                )
                written += 1
                self.q.put(f"✅ {action}：{code}(platform_id={pid}) collect={collect_count} share={share_count}")

            if written == 0:
                self.q.put("⚠️ 本次没有写入任何记录：请检查平台映射（t_platform）或默认 platform_id 设置")
            else:
                self.q.put(f"📌 完成：共处理 {written} 条（qyin/kugou/kuwo）")

            upsert_qqmusic_comment_cumulative(song_id, logger=self.q.put)

        except Exception as e:
            self.q.put("❌ 更新收藏/转发失败：" + str(e))
            self.q.put(traceback.format_exc())
        finally:
            self.run_btn.config(state="normal")


# ========= Tab：汽水 OCR =========
class UpdateTabQishui(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)

        form = ttk.Frame(self)
        form.pack(fill="x", padx=12, pady=12)

        ttk.Label(form, text="选择歌曲：").grid(row=0, column=0, sticky="w")
        self.song_var = tk.StringVar()
        self.song_combo = ttk.Combobox(form, textvariable=self.song_var, state="readonly", width=40)
        self.song_combo.grid(row=0, column=1, sticky="w", padx=8)
        self.song_combo.bind("<<ComboboxSelected>>", lambda e: self.auto_fill_track_id())

        ttk.Label(form, text="汽水 track_id：").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.track_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.track_var, width=34).grid(row=1, column=1, sticky="w", padx=8, pady=(10, 0))

        ttk.Label(form, text="platform_id：").grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.platform_var = tk.StringVar(value=str(QISHUI_PLATFORM_ID_DEFAULT))
        ttk.Entry(form, textvariable=self.platform_var, width=10).grid(row=2, column=1, sticky="w", padx=8, pady=(10, 0))

        ttk.Label(form, text="等待秒数：").grid(row=3, column=0, sticky="w", pady=(10, 0))
        self.wait_var = tk.StringVar(value=str(WAIT_SEC_DEFAULT))
        ttk.Entry(form, textvariable=self.wait_var, width=10).grid(row=3, column=1, sticky="w", padx=8, pady=(10, 0))

        ttk.Label(form, text="输出目录：").grid(row=4, column=0, sticky="w", pady=(10, 0))
        self.outdir_var = tk.StringVar(value=QISHUI_OUT_DIR_DEFAULT)
        ttk.Entry(form, textvariable=self.outdir_var, width=34).grid(row=4, column=1, sticky="w", padx=8, pady=(10, 0))

        ttk.Label(form, text="Tesseract 路径：").grid(row=5, column=0, sticky="w", pady=(10, 0))
        self.tess_var = tk.StringVar(value=TESSERACT_CMD_DEFAULT)
        ttk.Entry(form, textvariable=self.tess_var, width=60).grid(row=5, column=1, columnspan=2, sticky="w", padx=8, pady=(10, 0))

        btns = ttk.Frame(form)
        btns.grid(row=6, column=1, sticky="w", pady=(12, 0))

        self.run_btn = ttk.Button(btns, text="开始OCR并入库（汽水）", command=self.start_run)
        self.run_btn.pack(side="left")

        ttk.Button(btns, text="打开输出目录", command=self.open_out_dir).pack(side="left", padx=8)
        ttk.Button(btns, text="刷新歌曲列表", command=self.load_songs).pack(side="left", padx=8)

        self.log = tk.Text(self, height=18)
        self.log.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.q = queue.Queue()
        self.load_songs()
        self.after(100, self.flush_log)

    def flush_log(self):
        while True:
            try:
                msg = self.q.get_nowait()
            except queue.Empty:
                break
            log_print(self.log, msg)
        self.after(100, self.flush_log)

    def load_songs(self):
        try:
            rows = tme_crawler.list_songs(MYSQL_CONFIG)
            self.song_map = {f"{sid} - {name}": sid for sid, name in rows}
            self.song_combo["values"] = list(self.song_map.keys())
            if rows:
                self.song_combo.current(0)
                self.auto_fill_track_id()
            self.q.put(f"✅ 已加载歌曲 {len(rows)} 首")
        except Exception as e:
            messagebox.showerror("加载歌曲失败", str(e))

    def auto_fill_track_id(self):
        label = self.song_var.get()
        if not label or label not in self.song_map:
            return
        song_id = self.song_map[label]
        try:
            row = db_fetch_one(
                "SELECT song_platform_code FROM t_song_platform WHERE song_id=%s AND platform_id=%s LIMIT 1;",
                (song_id, QISHUI_PLATFORM_ID_DEFAULT),
            )
            if row and row[0]:
                self.track_var.set(str(row[0]))
                self.q.put(f"🔎 已自动带出 track_id：{row[0]}")
            else:
                self.track_var.set("")
                self.q.put("⚠️ 未找到该歌在 platform_id=5 的 song_platform_code（请手动填 track_id）")
        except Exception as e:
            self.q.put(f"⚠️ 自动读取 track_id 失败：{e}")

    def open_out_dir(self):
        d = self.outdir_var.get().strip()
        if not d:
            return
        ensure = os.path.abspath(d)
        try:
            os.makedirs(ensure, exist_ok=True)
            os.startfile(ensure)
        except Exception as e:
            messagebox.showerror("打开失败", str(e))

    def start_run(self):
        try:
            ensure_feature_module(qqmusic_comment_crawler, QQMUSIC_IMPORT_ERROR, "QQ音乐评论爬取")
        except Exception as e:
            messagebox.showwarning("提示", str(e))
            return

        label = self.song_var.get()
        if not label or label not in self.song_map:
            messagebox.showwarning("提示", "请先选择歌曲")
            return

        track_id = self.track_var.get().strip()
        if not track_id:
            messagebox.showwarning("提示", "请填写汽水 track_id（song_platform_code）")
            return

        try:
            platform_id = int(self.platform_var.get())
        except Exception:
            messagebox.showwarning("提示", "platform_id 必须是整数")
            return

        try:
            wait_sec = int(self.wait_var.get())
        except Exception:
            messagebox.showwarning("提示", "等待秒数必须是整数")
            return

        out_dir = self.outdir_var.get().strip() or QISHUI_OUT_DIR_DEFAULT
        tess_cmd = self.tess_var.get().strip()

        song_id = self.song_map[label]

        self.run_btn.config(state="disabled")
        self.q.put(f"🚀 开始汽水OCR：song_id={song_id} platform_id={platform_id} track_id={track_id}")

        th = threading.Thread(
            target=self._run_worker,
            args=(song_id, platform_id, track_id, out_dir, wait_sec, tess_cmd),
            daemon=True
        )
        th.start()

    def _run_worker(self, song_id, platform_id, track_id, out_dir, wait_sec, tess_cmd):
        try:
            def logger(msg):
                self.q.put(msg)

            qishui_ocr.run_qishui_ocr_to_db(
                mysql_config=MYSQL_CONFIG,
                song_id=song_id,
                platform_id=platform_id,
                track_id=track_id,
                out_dir=out_dir,
                wait_sec=wait_sec,
                tesseract_cmd=tess_cmd,
                logger=logger,
            )
            self.q.put("✅ 汽水OCR流程完成")
        except Exception as e:
            self.q.put("❌ 汽水OCR失败：" + str(e))
            self.q.put(traceback.format_exc())
        finally:
            self.run_btn.config(state="normal")


# ========= Tab：QQ音乐评论爬取 =========
class UpdateTabQQMusicComment(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)

        form = ttk.Frame(self)
        form.pack(fill="x", padx=12, pady=12)

        ttk.Label(form, text="选择歌曲：").grid(row=0, column=0, sticky="w")
        self.song_var = tk.StringVar()
        self.song_combo = ttk.Combobox(form, textvariable=self.song_var, state="readonly", width=40)
        self.song_combo.grid(row=0, column=1, sticky="w", padx=8)
        self.song_combo.bind("<<ComboboxSelected>>", lambda e: self.auto_fill_qqmusic_code())

        ttk.Label(form, text="QQ音乐ID：").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.qqmusic_code_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.qqmusic_code_var, width=34).grid(row=1, column=1, sticky="w", padx=8, pady=(10, 0))

        ttk.Label(form, text="Cookie（留空用默认）：").grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.cookie_var = tk.StringVar(value=QQMUSIC_DEFAULT_COOKIE_STR)
        cookie_entry = ttk.Entry(form, textvariable=self.cookie_var, width=60)
        cookie_entry.grid(row=2, column=1, columnspan=2, sticky="w", padx=8, pady=(10, 0))
        
        btns = ttk.Frame(form)
        btns.grid(row=3, column=1, sticky="w", pady=(12, 0))

        self.run_btn = ttk.Button(btns, text="开始爬取评论（QQ音乐）", command=self.start_run)
        self.run_btn.pack(side="left")

        ttk.Button(btns, text="刷新歌曲列表", command=self.load_songs).pack(side="left", padx=8)
        ttk.Button(btns, text="恢复默认Cookie", command=lambda: self.cookie_var.set(QQMUSIC_DEFAULT_COOKIE_STR)).pack(side="left", padx=8)

        self.log = tk.Text(self, height=18)
        self.log.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.q = queue.Queue()
        self.load_songs()
        self.after(100, self.flush_log)

    def flush_log(self):
        while True:
            try:
                msg = self.q.get_nowait()
            except queue.Empty:
                break
            log_print(self.log, msg)
        self.after(100, self.flush_log)

    def load_songs(self):
        try:
            sql = "SELECT song_id, song_name FROM t_song ORDER BY song_id"
            rows = db_fetch_all(sql)
            self.song_map = {f"{sid} - {name}": sid for sid, name in rows}
            self.song_combo["values"] = list(self.song_map.keys())
            if rows:
                self.song_combo.current(0)
                self.auto_fill_qqmusic_code()
            self.q.put(f"✅ 已加载歌曲 {len(rows)} 首")
        except Exception as e:
            messagebox.showerror("加载歌曲失败", str(e))

    def auto_fill_qqmusic_code(self):
        label = self.song_var.get()
        if not label or label not in self.song_map:
            return
        song_id = self.song_map[label]
        try:
            sql = "SELECT song_platform_code FROM t_song_platform WHERE song_id=%s AND platform_id=1 LIMIT 1;"
            row = db_fetch_one(sql, (song_id,))
            if row and row[0]:
                self.qqmusic_code_var.set(str(row[0]))
                self.q.put(f"🔎 已自动带出QQ音乐ID：{row[0]}")
            else:
                self.qqmusic_code_var.set("")
                self.q.put("⚠️ 未找到该歌在platform_id=1的song_platform_code（请手动填写QQ音乐ID）")
        except Exception as e:
            self.q.put(f"⚠️ 自动读取QQ音乐ID失败：{e}")

    def start_run(self):
        try:
            ensure_feature_module(wangyiyun_comment, WANGYIYUN_IMPORT_ERROR, "网易云评论爬取")
        except Exception as e:
            messagebox.showwarning("提示", str(e))
            return

        label = self.song_var.get()
        if not label or label not in self.song_map:
            messagebox.showwarning("提示", "请先选择歌曲")
            return

        qqmusic_code = self.qqmusic_code_var.get().strip()
        if not qqmusic_code:
            messagebox.showwarning("提示", "请填写QQ音乐ID（song_platform_code）")
            return

        cookie_str = self.cookie_var.get().strip() or QQMUSIC_DEFAULT_COOKIE_STR
        song_id = self.song_map[label]

        self.run_btn.config(state="disabled")
        self.q.put(f"🚀 开始爬取QQ音乐评论：song_id={song_id} QQ音乐ID={qqmusic_code}")

        th = threading.Thread(
            target=self._run_worker,
            args=(song_id, qqmusic_code, cookie_str),
            daemon=True
        )
        th.start()

    def _run_worker(self, song_id, qqmusic_code, cookie_str):
        try:
            qqmusic_comment_crawler.crawl_qqmusic_comments_to_db(
                song_id=song_id,
                song_platform_code=qqmusic_code,
                mysql_config=MYSQL_CONFIG,
                cookie_str=cookie_str,
                logger=self.q.put
            )
            
            upsert_qqmusic_comment_cumulative(song_id, logger=self.q.put)
            
        except Exception as e:
            self.q.put("❌ 爬取失败：" + str(e))
            self.q.put(traceback.format_exc())
        finally:
            self.run_btn.config(state="normal")


class UpdateTabWangYiYunComment(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)

        form = ttk.Frame(self)
        form.pack(fill="x", padx=12, pady=12)

        ttk.Label(form, text="选择歌曲：").grid(row=0, column=0, sticky="w")
        self.song_var = tk.StringVar()
        self.song_combo = ttk.Combobox(form, textvariable=self.song_var, state="readonly", width=40)
        self.song_combo.grid(row=0, column=1, sticky="w", padx=8)
        self.song_combo.bind("<<ComboboxSelected>>", lambda e: self.auto_fill_wangyiyun_code())

        ttk.Label(form, text="网易云 song_id/URL：").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.song_code_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.song_code_var, width=40).grid(row=1, column=1, sticky="w", padx=8, pady=(10, 0))

        ttk.Label(form, text="Cookie：").grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.cookie_var = tk.StringVar(value=os.getenv(WANGYIYUN_COOKIE_ENV, ""))
        ttk.Entry(form, textvariable=self.cookie_var, width=72).grid(row=2, column=1, columnspan=3, sticky="w", padx=8, pady=(10, 0))

        ttk.Label(form, text="最大评论数(可空)：").grid(row=3, column=0, sticky="w", pady=(10, 0))
        self.max_comments_var = tk.StringVar(value="")
        ttk.Entry(form, textvariable=self.max_comments_var, width=12).grid(row=3, column=1, sticky="w", padx=8, pady=(10, 0))

        ttk.Label(form, text=f"正式入库表：{WANGYIYUN_TARGET_TABLE}").grid(row=4, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Label(form, text="当前会正式写入 t_comment，并同步回填网易云评论累计数").grid(row=5, column=0, columnspan=3, sticky="w", pady=(4, 0))

        btns = ttk.Frame(form)
        btns.grid(row=6, column=1, sticky="w", pady=(12, 0))

        self.run_btn = ttk.Button(btns, text="开始爬取评论（网易云）", command=self.start_run)
        self.run_btn.pack(side="left")

        ttk.Button(btns, text="刷新歌曲列表", command=self.load_songs).pack(side="left", padx=8)
        ttk.Button(
            btns,
            text="读取环境变量Cookie",
            command=self.load_cookie_from_env,
        ).pack(side="left", padx=8)

        self.log = tk.Text(self, height=18)
        self.log.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.q = queue.Queue()
        self.load_songs()
        self.after(100, self.flush_log)

    def flush_log(self):
        while True:
            try:
                msg = self.q.get_nowait()
            except queue.Empty:
                break
            log_print(self.log, msg)
        self.after(100, self.flush_log)

    def load_cookie_from_env(self):
        cookie = os.getenv(WANGYIYUN_COOKIE_ENV, "")
        self.cookie_var.set(cookie)
        if cookie.strip():
            self.q.put(f"已读取环境变量 {WANGYIYUN_COOKIE_ENV}，长度 {len(cookie.strip())}")
        else:
            self.q.put(f"环境变量 {WANGYIYUN_COOKIE_ENV} 为空，所以按钮看起来像没反应")

    def load_songs(self):
        try:
            rows = db_fetch_all("SELECT song_id, song_name FROM t_song ORDER BY song_id")
            self.song_map = {f"{sid} - {name}": sid for sid, name in rows}
            self.song_combo["values"] = list(self.song_map.keys())
            if rows:
                self.song_combo.current(0)
                self.auto_fill_wangyiyun_code()
            self.q.put(f"已加载歌曲 {len(rows)} 首")
        except Exception as e:
            messagebox.showerror("加载歌曲失败", str(e))

    def auto_fill_wangyiyun_code(self):
        label = self.song_var.get()
        if not label or label not in self.song_map:
            return
        song_id = self.song_map[label]
        try:
            row = db_fetch_one(
                "SELECT COALESCE(NULLIF(url, ''), song_platform_code) FROM t_song_platform WHERE song_id=%s AND platform_id=%s LIMIT 1;",
                (song_id, WANGYIYUN_PLATFORM_ID_DEFAULT),
            )
            if row and row[0]:
                self.song_code_var.set(str(row[0]))
                self.q.put(f"已自动带出网易云标识：{row[0]}")
            else:
                self.song_code_var.set("")
                self.q.put("未找到该歌曲在 t_song_platform(platform_id=4) 的 song_platform_code/url")
        except Exception as e:
            self.q.put(f"自动读取网易云标识失败：{e}")

    def start_run(self):
        label = self.song_var.get()
        if not label or label not in self.song_map:
            messagebox.showwarning("提示", "请先选择歌曲")
            return

        song_platform_code = self.song_code_var.get().strip()
        if not song_platform_code:
            messagebox.showwarning("提示", "请填写网易云 song_id 或 URL")
            return

        max_comments_text = self.max_comments_var.get().strip()
        try:
            max_comments = int(max_comments_text) if max_comments_text else None
        except Exception:
            messagebox.showwarning("提示", "最大评论数必须是整数或留空")
            return

        song_id = self.song_map[label]
        cookie_str = self.cookie_var.get().strip()

        self.run_btn.config(state="disabled")
        self.q.put(
            f"开始抓取网易云评论：song_id={song_id} | song_platform_code={song_platform_code} | 目标表={WANGYIYUN_TARGET_TABLE}"
        )

        th = threading.Thread(
            target=self._run_worker,
            args=(song_id, song_platform_code, cookie_str, max_comments),
            daemon=True,
        )
        th.start()

    def _run_worker(self, song_id, song_platform_code, cookie_str, max_comments):
        try:
            wangyiyun_comment.crawl_wangyiyun_comments_to_db(
                song_id=song_id,
                song_platform_code=song_platform_code,
                mysql_config=MYSQL_CONFIG,
                cookie_str=cookie_str,
                max_comments=max_comments,
                table_name=WANGYIYUN_TARGET_TABLE,
                platform_id=WANGYIYUN_PLATFORM_ID_DEFAULT,
                logger=self.q.put,
            )
            upsert_wangyiyun_comment_cumulative(song_id, logger=self.q.put)
        except Exception as e:
            self.q.put("网易云评论抓取失败：" + str(e))
            self.q.put(traceback.format_exc())
        finally:
            self.run_btn.config(state="normal")


class UpdateTabBatch(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)

        form = ttk.Frame(self)
        form.pack(fill="x", padx=12, pady=12)

        self.enable_tme_play = tk.BooleanVar(value=True)
        self.enable_tme_interact = tk.BooleanVar(value=True)
        self.enable_qq_comment = tk.BooleanVar(value=True)
        self.enable_wyy_comment = tk.BooleanVar(value=True)
        self.enable_qishui = tk.BooleanVar(value=True)

        ttk.Checkbutton(form, text="腾讯播放量", variable=self.enable_tme_play).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(form, text="腾讯收藏/转发", variable=self.enable_tme_interact).grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Checkbutton(form, text="QQ评论", variable=self.enable_qq_comment).grid(row=0, column=2, sticky="w", padx=(10, 0))
        ttk.Checkbutton(form, text="网易云评论", variable=self.enable_wyy_comment).grid(row=0, column=3, sticky="w", padx=(10, 0))
        ttk.Checkbutton(form, text="汽水OCR", variable=self.enable_qishui).grid(row=0, column=4, sticky="w", padx=(10, 0))

        ttk.Label(form, text="腾讯近 N 天：").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.days_var = tk.StringVar(value="30")
        ttk.Entry(form, textvariable=self.days_var, width=8).grid(row=1, column=1, sticky="w", pady=(10, 0))

        ttk.Label(form, text="QQ Cookie：").grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.qq_cookie_var = tk.StringVar(value=QQMUSIC_DEFAULT_COOKIE_STR)
        ttk.Entry(form, textvariable=self.qq_cookie_var, width=90).grid(row=2, column=1, columnspan=4, sticky="w", pady=(10, 0))

        ttk.Label(form, text="腾讯端 token：").grid(row=3, column=0, sticky="w", pady=(10, 0))
        self.tme_token_var = tk.StringVar(value=HEADERS_TME.get("tme-header-token", ""))
        ttk.Entry(form, textvariable=self.tme_token_var, width=90).grid(row=3, column=1, columnspan=4, sticky="w", pady=(10, 0))

        ttk.Label(form, text="网易云 Cookie：").grid(row=4, column=0, sticky="w", pady=(10, 0))
        self.wyy_cookie_var = tk.StringVar(value=os.getenv(WANGYIYUN_COOKIE_ENV, ""))
        ttk.Entry(form, textvariable=self.wyy_cookie_var, width=90).grid(row=4, column=1, columnspan=4, sticky="w", pady=(10, 0))

        ttk.Label(form, text="汽水等待秒数：").grid(row=5, column=0, sticky="w", pady=(10, 0))
        self.wait_var = tk.StringVar(value=str(WAIT_SEC_DEFAULT))
        ttk.Entry(form, textvariable=self.wait_var, width=8).grid(row=5, column=1, sticky="w", pady=(10, 0))

        ttk.Label(form, text="汽水输出目录：").grid(row=6, column=0, sticky="w", pady=(10, 0))
        self.outdir_var = tk.StringVar(value=QISHUI_OUT_DIR_DEFAULT)
        ttk.Entry(form, textvariable=self.outdir_var, width=40).grid(row=6, column=1, columnspan=2, sticky="w", pady=(10, 0))

        ttk.Label(form, text="Tesseract：").grid(row=7, column=0, sticky="w", pady=(10, 0))
        self.tess_var = tk.StringVar(value=TESSERACT_CMD_DEFAULT)
        ttk.Entry(form, textvariable=self.tess_var, width=70).grid(row=7, column=1, columnspan=4, sticky="w", pady=(10, 0))

        tips = (
            "说明：腾讯相关批量更新只使用 t_song_tme。"
            " 对《星海（Starry Sea）》和《Crash Down》这类缺少 tme_song_id 的歌曲会自动跳过。"
        )
        ttk.Label(form, text=tips).grid(row=8, column=0, columnspan=5, sticky="w", pady=(10, 0))

        btns = ttk.Frame(form)
        btns.grid(row=9, column=0, columnspan=5, sticky="w", pady=(12, 0))

        self.run_btn = ttk.Button(btns, text="一键批量更新全部歌曲", command=self.start_run)
        self.run_btn.pack(side="left")
        ttk.Button(btns, text="读取网易云环境变量Cookie", command=self.load_wyy_cookie_from_env).pack(side="left", padx=8)

        self.log = tk.Text(self, height=22)
        self.log.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.q = queue.Queue()
        self.after(100, self.flush_log)

    def load_wyy_cookie_from_env(self):
        cookie = os.getenv(WANGYIYUN_COOKIE_ENV, "")
        self.wyy_cookie_var.set(cookie)
        if cookie.strip():
            self.q.put(f"已读取环境变量 {WANGYIYUN_COOKIE_ENV}，长度 {len(cookie.strip())}")
        else:
            self.q.put(f"环境变量 {WANGYIYUN_COOKIE_ENV} 为空，所以看起来像“没反应”")

    def flush_log(self):
        while True:
            try:
                msg = self.q.get_nowait()
            except queue.Empty:
                break
            log_print(self.log, msg)
        self.after(100, self.flush_log)

    def _get_all_songs(self):
        rows = db_fetch_all("SELECT song_id, song_name FROM t_song ORDER BY song_id")
        return [{"song_id": sid, "song_name": name} for sid, name in rows]

    def _get_platform_code(self, song_id: int, platform_id: int):
        row = db_fetch_one(
            "SELECT song_platform_code FROM t_song_platform WHERE song_id=%s AND platform_id=%s LIMIT 1;",
            (song_id, platform_id),
        )
        return str(row[0]) if row and row[0] else None

    def _get_tme_song_id_strict(self, song_id: int):
        row = db_fetch_one("SELECT tme_song_id FROM t_song_tme WHERE song_id=%s LIMIT 1;", (song_id,))
        return str(row[0]) if row and row[0] else None

    def _build_total_headers(self, tme_song_id: str, token_override: str = "") -> dict:
        h = dict(HEADERS_TME)
        if token_override.strip():
            h["tme-header-token"] = token_override.strip()
        h["tme-header-trace"] = f"trace_{int(time.time())}"
        h["tme-header-herf"] = f"https://y.tencentmusic.com/#/user/organdata/works/detail/{tme_song_id}"
        return h

    def _fetch_total_json(self, tme_song_id: str, token_override: str = "") -> dict:
        url = f"https://y.tencentmusic.com/cd-gateway/musician/song/figure/total?songId={tme_song_id}"
        r = requests.get(url, headers=self._build_total_headers(tme_song_id, token_override), timeout=15)
        r.raise_for_status()
        j = r.json()
        if str(j.get("code")) != "0" or not j.get("data"):
            raise RuntimeError(f"腾讯互动接口返回异常: code={j.get('code')} message={j.get('message')}")
        return j["data"]

    def _get_code_to_platform_id(self) -> dict:
        rows = db_fetch_all("SELECT platform_id, platform_code FROM t_platform;")
        return {code: int(pid) for pid, code in rows if code}

    def _upsert_collect_share(self, song_id: int, platform_id: int, record_date: str, collect_count: int, share_count: int):
        conn = pymysql.connect(**MYSQL_CONFIG)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id FROM t_song_interaction_history
                    WHERE song_id=%s AND platform_id=%s AND record_date=%s
                    LIMIT 1;
                    """,
                    (song_id, platform_id, record_date),
                )
                row = cur.fetchone()
                if row:
                    cur.execute(
                        """
                        UPDATE t_song_interaction_history
                        SET collect_count=%s, share_count=%s
                        WHERE id=%s;
                        """,
                        (collect_count, share_count, row[0]),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO t_song_interaction_history
                          (song_id, platform_id, comment_count, collect_count, share_count, record_date)
                        VALUES
                          (%s, %s, 0, %s, %s, %s);
                        """,
                        (song_id, platform_id, collect_count, share_count, record_date),
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def start_run(self):
        try:
            if self.enable_qq_comment.get():
                ensure_feature_module(qqmusic_comment_crawler, QQMUSIC_IMPORT_ERROR, "QQ音乐评论爬取")
            if self.enable_wyy_comment.get():
                ensure_feature_module(wangyiyun_comment, WANGYIYUN_IMPORT_ERROR, "网易云评论爬取")
            if self.enable_qishui.get():
                ensure_feature_module(qishui_ocr, QISHUI_IMPORT_ERROR, "汽水OCR")
        except Exception as e:
            messagebox.showwarning("提示", str(e))
            return

        try:
            days = int(self.days_var.get())
            wait_sec = int(self.wait_var.get())
        except Exception:
            messagebox.showwarning("提示", "天数和汽水等待秒数必须是整数")
            return

        self.run_btn.config(state="disabled")
        self.q.put("开始一键批量更新全部歌曲")
        th = threading.Thread(
            target=self._run_worker,
            args=(
                days,
                wait_sec,
                self.qq_cookie_var.get().strip(),
                self.tme_token_var.get().strip(),
                self.wyy_cookie_var.get().strip(),
                self.outdir_var.get().strip() or QISHUI_OUT_DIR_DEFAULT,
                self.tess_var.get().strip(),
            ),
            daemon=True,
        )
        th.start()

    def _run_worker(self, days: int, wait_sec: int, qq_cookie: str, tme_token: str, wyy_cookie: str, out_dir: str, tess_cmd: str):
        summary = []
        songs = self._get_all_songs()
        code_to_pid = self._get_code_to_platform_id()

        try:
            for song in songs:
                song_id = int(song["song_id"])
                song_name = str(song["song_name"])
                self.q.put("")
                self.q.put(f"========== song_id={song_id} | {song_name} ==========")

                if self.enable_tme_play.get():
                    try:
                        tme_song_id = self._get_tme_song_id_strict(song_id)
                        if not tme_song_id:
                            self.q.put("跳过腾讯播放量：缺少 t_song_tme.tme_song_id")
                        else:
                            tme_headers = dict(HEADERS_TME)
                            if tme_token.strip():
                                tme_headers["tme-header-token"] = tme_token.strip()
                            df, start_date, end_date = tme_crawler.crawl_tme_play_trend(tme_song_id, tme_headers, days_before=days)
                            inserted = tme_crawler.insert_missing_play_stat(MYSQL_CONFIG, song_id, df, logger=self.q.put)
                            self.q.put(f"腾讯播放量完成：{start_date} ~ {end_date}，新增 {inserted} 条")
                    except Exception as e:
                        self.q.put(f"腾讯播放量失败：{e}")

                if self.enable_tme_interact.get():
                    try:
                        tme_song_id = self._get_tme_song_id_strict(song_id)
                        if not tme_song_id:
                            self.q.put("跳过腾讯收藏/转发：缺少 t_song_tme.tme_song_id")
                        else:
                            data = self._fetch_total_json(tme_song_id, tme_token)
                            record_date = ms_to_cn_date(data["statisDay"]) if data.get("statisDay") else datetime.now(tz_cn()).strftime("%Y-%m-%d")
                            favor_block = data.get("cumulativeFavorCnt", {}) or {}
                            share_block = data.get("cumulativeShareCnt", {}) or {}
                            written = 0
                            for code in TME_PLATFORM_CODES:
                                pid = code_to_pid.get(code)
                                if not pid:
                                    continue
                                self._upsert_collect_share(
                                    song_id=song_id,
                                    platform_id=pid,
                                    record_date=record_date,
                                    collect_count=safe_int(favor_block.get(code, 0)),
                                    share_count=safe_int(share_block.get(code, 0)),
                                )
                                written += 1
                            self.q.put(f"腾讯收藏/转发完成：record_date={record_date}，处理 {written} 个平台")
                    except Exception as e:
                        self.q.put(f"腾讯收藏/转发失败：{e}")

                if self.enable_qq_comment.get():
                    try:
                        qq_code = self._get_platform_code(song_id, 1)
                        if not qq_code:
                            self.q.put("跳过QQ评论：缺少 platform_id=1 的 song_platform_code")
                        elif not qq_cookie:
                            self.q.put("跳过QQ评论：Cookie 为空")
                        else:
                            qqmusic_comment_crawler.crawl_qqmusic_comments_to_db(
                                song_id=song_id,
                                song_platform_code=qq_code,
                                mysql_config=MYSQL_CONFIG,
                                cookie_str=qq_cookie,
                                logger=self.q.put,
                            )
                            upsert_qqmusic_comment_cumulative(song_id, logger=self.q.put)
                    except Exception as e:
                        self.q.put(f"QQ评论失败：{e}")

                if self.enable_wyy_comment.get():
                    try:
                        wyy_code = self._get_platform_code(song_id, 4)
                        if not wyy_code:
                            self.q.put("跳过网易云评论：缺少 platform_id=4 的 song_platform_code")
                        elif not wyy_cookie:
                            self.q.put("跳过网易云评论：Cookie 为空")
                        else:
                            wangyiyun_comment.crawl_wangyiyun_comments_to_db(
                                song_id=song_id,
                                song_platform_code=wyy_code,
                                mysql_config=MYSQL_CONFIG,
                                cookie_str=wyy_cookie,
                                table_name=WANGYIYUN_TARGET_TABLE,
                                platform_id=WANGYIYUN_PLATFORM_ID_DEFAULT,
                                logger=self.q.put,
                            )
                            upsert_wangyiyun_comment_cumulative(song_id, logger=self.q.put)
                    except Exception as e:
                        self.q.put(f"网易云评论失败：{e}")

                if self.enable_qishui.get():
                    try:
                        track_id = self._get_platform_code(song_id, 5)
                        if not track_id:
                            self.q.put("跳过汽水OCR：缺少 platform_id=5 的 song_platform_code")
                        else:
                            qishui_ocr.run_qishui_ocr_to_db(
                                mysql_config=MYSQL_CONFIG,
                                song_id=song_id,
                                platform_id=QISHUI_PLATFORM_ID_DEFAULT,
                                track_id=track_id,
                                out_dir=out_dir,
                                wait_sec=wait_sec,
                                tesseract_cmd=tess_cmd,
                                logger=self.q.put,
                            )
                    except Exception as e:
                        self.q.put(f"汽水OCR失败：{e}")

                summary.append(f"{song_id}-{song_name}: 完成")

            self.q.put("")
            self.q.put("批量更新完成")
            for line in summary:
                self.q.put(line)
        finally:
            self.run_btn.config(state="normal")


# ========= 主窗口 =========
class ToolboxApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("音乐数据工具箱")
        self.geometry("1100x780")

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)

        # Tab1: Dashboard
        tab_dash = ttk.Frame(nb)
        nb.add(tab_dash, text="Dashboard")
        ttk.Label(tab_dash, text="点击按钮启动 Streamlit Dashboard", font=("Microsoft YaHei", 12)).pack(pady=20)
        ttk.Button(tab_dash, text="📊 启动 Dashboard", command=self.start_dashboard).pack()

        # Tab2: 查看数据库
        tab_db = MySQLViewer(nb)
        tab_feishu_export = ExportMySQLToFeishuTab(nb)
        nb.add(tab_feishu_export, text="MySQL导入飞书")
        nb.add(tab_db, text="查看数据库")

        # Tab3: 腾讯播放趋势更新
        tab_tme = UpdateTabTME(nb)
        nb.add(tab_tme, text="更新播放量数据（腾讯端）")

        # Tab4: 汽水OCR
        tab_qs = UpdateTabQishui(nb)
        nb.add(tab_qs, text="汽水互动量（赞评转）")

        # Tab5: 腾讯收藏转发
        tab_tme_inter = UpdateTabTMEInteract(nb)
        nb.add(tab_tme_inter, text="腾讯互动量（收藏转发）")

        # Tab6: QQ音乐评论爬取
        tab_qq_comment = UpdateTabQQMusicComment(nb)
        nb.add(tab_qq_comment, text="QQ音乐评论爬取")

        # Tab7: 网易云评论爬取
        tab_wyy_comment = UpdateTabWangYiYunComment(nb)
        nb.add(tab_wyy_comment, text="网易云评论爬取")

        # Tab8: 一键批量更新
        tab_batch = UpdateTabBatch(nb)
        nb.add(tab_batch, text="一键批量更新")

    def start_dashboard(self):
        try:
            port = find_free_port(DASHBOARD_PORT)

            cmd = (
                f'"{sys.executable}" -m streamlit run "{DASHBOARD_SCRIPT}" '
                f'--server.port {port} --server.address 0.0.0.0 '
                f'--server.headless true'
            )

            subprocess.Popen(cmd, shell=True)
            time.sleep(0.3)

            lan_ip = get_lan_ip()
            local_url = f"http://localhost:{port}"
            network_url = f"http://{lan_ip}:{port}"

            text_to_copy = (
                "You can now view your Streamlit app in your browser.\n\n"
                f"Local URL: {local_url}\n"
                f"Network URL: {network_url}\n"
            )

            try:
                self.clipboard_clear()
                self.clipboard_append(text_to_copy)
                self.update()
            except Exception:
                pass

            try:
                webbrowser.open(local_url)
            except Exception:
                pass

            messagebox.showinfo(
                "Dashboard 已启动",
                text_to_copy + "\n（已自动复制到剪贴板）"
            )

        except Exception as e:
            messagebox.showerror("启动失败", str(e))


def main():
    app = ToolboxApp()
    app.mainloop()


if __name__ == "__main__":
    main()
