import requests
import pandas as pd
import pymysql
from datetime import datetime, timedelta
import urllib.parse
import requests
import warnings
import pandas as pd
from config_loader import get_setting

warnings.filterwarnings("ignore", message="Unverified HTTPS request")
class FriendlyTMEError(RuntimeError):
    """给 GUI 用的友好错误（不要打印 traceback）"""
    pass

# 计算日期范围
def compute_date_range(days_before: int):
    start_date = (datetime.now() - timedelta(days=days_before)).strftime('%Y-%m-%d')
    end_date = datetime.now().strftime('%Y-%m-%d')
    return start_date, end_date

# 构造腾讯音乐端的API URL
def build_trend_url(tme_song_id: str, start_date: str, end_date: str) -> str:
    return f"https://y.tencentmusic.com/cd-gateway/musician/song/figure/trend?startDate={start_date}&endDate={end_date}&dataType=play&songId={tme_song_id}"

# 构造请求头

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0',
    'Referer': 'https://y.tencentmusic.com/',
    'tme-header-token': get_setting('TME_HEADER_TOKEN', 'YOUR_TME_HEADER_TOKEN'),
    'tme-header-feferer': '/',
    'tme-header-herf': 'https://y.tencentmusic.com/#/user/organdata/works/detail/YOUR_SONG_ID',
    'tme-header-trace': 'trace_placeholder',
    'tme-source-platform': '0',
    'Content-Type': 'application/json;charset=utf-8'
}

def build_headers(base_headers: dict, tme_song_id: str) -> dict:
    """根据 tme_song_id 动态构建请求头"""
    tme_song_id = urllib.parse.quote(tme_song_id)  # URL 编码

    headers = dict(base_headers)  # 复制 base_headers
    headers['tme-header-herf'] = f'https://y.tencentmusic.com/#/user/organdata/works/detail/{tme_song_id}'  # 使用 URL 编码后的 ID

    # 打印请求头，看看是否包含中文字符或无法编码的字符
    print("请求头:", headers)  # 打印请求头以供调试

    return headers

# 获取数据库连接
def get_conn(mysql_config: dict):
    return pymysql.connect(**mysql_config)

# 获取歌曲信息：从 t_song 表读取歌曲，返回 [(song_id, song_name)]
def list_songs(mysql_config: dict):
    conn = get_conn(mysql_config)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT song_id, song_name FROM t_song ORDER BY song_id;")
            return cur.fetchall()
    finally:
        conn.close()

# 获取平台代码映射：{platform_code: platform_id}
def get_platform_code_to_id(mysql_config: dict):
    conn = get_conn(mysql_config)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT platform_id, platform_code FROM t_platform;")
            rows = cur.fetchall()
        return {code: pid for pid, code in rows}
    finally:
        conn.close()

# 获取某首歌的所有平台 song_platform_code：{platform_id: song_platform_code}
def get_song_platform_codes(mysql_config: dict, song_id: int):
    conn = get_conn(mysql_config)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT platform_id, song_platform_code FROM t_song_platform WHERE song_id=%s;",
                (song_id,)
            )
            rows = cur.fetchall()
        return {pid: code for pid, code in rows}
    finally:
        conn.close()

# 获取已经录入的日期：{stat_date}
def get_existing_dates(mysql_config: dict, song_id: int, platform_id: int):
    conn = get_conn(mysql_config)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT stat_date FROM t_play_stat WHERE song_id=%s AND platform_id=%s;",
                (song_id, platform_id)
            )
            rows = cur.fetchall()
        return {r[0].strftime("%Y-%m-%d") if hasattr(r[0], "strftime") else str(r[0]) for r in rows}
    finally:
        conn.close()

# 爬取腾讯音乐端的数据
def crawl_tme_play_trend(tme_song_id: str, base_headers: dict, days_before: int = 30):
    """爬取腾讯音乐平台数据"""
    start_date, end_date = compute_date_range(days_before)
    url = build_trend_url(tme_song_id, start_date, end_date)
    headers = build_headers(base_headers, tme_song_id)  # 使用传入的 tme_song_id 构建请求头

    try:
        # 请求数据
        resp = requests.get(url, headers=headers, timeout=15, verify=False)  # 强制使用 utf-8 编码
        if resp.status_code != 200:
            raise RuntimeError(f"请求失败 {resp.status_code}: {resp.text[:300]}")

        # 打印返回的原始数据
        print("API 返回的原始数据:", resp.text)

        data = resp.json()
        if "data" not in data or not isinstance(data["data"], list):
            raise RuntimeError("返回结构异常：没有 data 列表")

        extracted = []
        for day in data["data"]:
            date_str = day.get("statisDayStr")
            play_cnt = day.get("playCnt", {}) or {}
            extracted.append({
                "stat_date": date_str,
                "qyin": int(play_cnt.get("qyin", 0) or 0),
                "kugou": int(play_cnt.get("kugou", 0) or 0),
                "kuwo": int(play_cnt.get("kuwo", 0) or 0),
            })

        df = pd.DataFrame(extracted)
        df["stat_date"] = pd.to_datetime(df["stat_date"]).dt.strftime("%Y-%m-%d")
        df = df.sort_values("stat_date").reset_index(drop=True)
        return df, start_date, end_date

    except requests.exceptions.RequestException as e:
        print(f"请求失败: {e}")
        raise

def insert_missing_play_stat(mysql_config: dict, song_id: int, df: pd.DataFrame, logger=print):
    """
    只插入“未记录日期”，避免重复。
    腾讯端一次更新：qyin/kugou/kuwo -> platform_id=1/2/3
    """
    code_to_id = get_platform_code_to_id(mysql_config)
    pid_qyin = code_to_id.get("qyin")
    pid_kugou = code_to_id.get("kugou")
    pid_kuwo = code_to_id.get("kuwo")

    if not pid_qyin:
        raise RuntimeError("t_platform 缺少 qyin 平台定义")
    if not pid_kugou or not pid_kuwo:
        logger("⚠️ 提示：t_platform 里 kugou/kuwo 未配置，将只写入 qyin（如果存在）")

    # 平台编码映射（用于 platform_song_id 字段）
    song_platform_codes = get_song_platform_codes(mysql_config, song_id)  # {platform_id: code}

    # 预取已有日期（按平台）
    exist_qyin = get_existing_dates(mysql_config, song_id, pid_qyin) if pid_qyin else set()
    exist_kugou = get_existing_dates(mysql_config, song_id, pid_kugou) if pid_kugou else set()
    exist_kuwo = get_existing_dates(mysql_config, song_id, pid_kuwo) if pid_kuwo else set()

    conn = get_conn(mysql_config)
    inserted = 0
    try:
        with conn.cursor() as cur:
            for _, row in df.iterrows():
                d = row["stat_date"]

                # 逐平台写入（只写缺失日期）
                for pcode, pid, exist_set in [
                    ("qyin", pid_qyin, exist_qyin),
                    ("kugou", pid_kugou, exist_kugou),
                    ("kuwo", pid_kuwo, exist_kuwo),
                ]:
                    if not pid:
                        continue
                    if d in exist_set:
                        continue

                    play_count = int(row.get(pcode, 0) or 0)
                    play_count_str = f"{play_count:,}" if play_count > 0 else "0"
                    platform_song_id = (song_platform_codes.get(pid) or "")

                    sql = """
                    INSERT INTO t_play_stat (song_id, platform_id, platform_song_id, stat_date, play_count, play_count_str)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """
                    cur.execute(sql, (song_id, pid, platform_song_id, d, play_count, play_count_str))
                    inserted += 1
                    exist_set.add(d)

        conn.commit()
        return inserted
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
def get_tme_song_id(mysql_config: dict, song_id: int):
    """从 t_song_tme 读取腾讯端 tme_song_id"""
    conn = get_conn(mysql_config)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT tme_song_id FROM t_song_tme WHERE song_id=%s LIMIT 1;", (song_id,))
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()

def insert_missing_play_stat_filtered(mysql_config: dict, song_id: int, df, wanted_codes: list[str], logger=print):
    """
    只插入“未记录日期”，并且只写入 wanted_codes 中的平台（qyin/kugou/kuwo）
    依赖：get_platform_code_to_id, get_song_platform_codes, get_existing_dates, get_conn
    """
    code_to_id = get_platform_code_to_id(mysql_config)

    wanted = []
    for c in wanted_codes:
        pid = code_to_id.get(c)
        if pid:
            wanted.append((c, pid))
        else:
            logger(f"⚠️ t_platform 中缺少 platform_code={c}，将跳过")

    if not wanted:
        raise RuntimeError("没有可写入的平台（请检查 t_platform.platform_code 是否包含 qyin/kugou/kuwo）")

    song_platform_codes = get_song_platform_codes(mysql_config, song_id)  # {platform_id: code}

    exist_map = {}
    for c, pid in wanted:
        exist_map[pid] = get_existing_dates(mysql_config, song_id, pid)

    conn = get_conn(mysql_config)
    inserted = 0
    try:
        with conn.cursor() as cur:
            # 兼容 df 列是 "stat_date" 或 GUI里是 "日期"
            has_stat_date = "stat_date" in df.columns
            for _, row in df.iterrows():
                d = row["stat_date"] if has_stat_date else row["日期"].strftime("%Y-%m-%d")

                for c, pid in wanted:
                    if d in exist_map[pid]:
                        continue

                    play_count = int(row.get(c, 0) or 0)
                    play_count_str = f"{play_count:,}" if play_count > 0 else "0"
                    platform_song_id = (song_platform_codes.get(pid) or "")

                    sql = """
                    INSERT INTO t_play_stat (song_id, platform_id, platform_song_id, stat_date, play_count, play_count_str)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """
                    cur.execute(sql, (song_id, pid, platform_song_id, d, play_count, play_count_str))
                    inserted += 1
                    exist_map[pid].add(d)

        conn.commit()
        return inserted
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

