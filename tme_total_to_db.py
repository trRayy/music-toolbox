# -*- coding: utf-8 -*-
import argparse
import time
from datetime import datetime, timezone, timedelta
import requests
import pymysql


# ===================== 固定 token（你要求：不参数化） =====================
TME_HEADER_TOKEN = (
    "eyJhbGciOiJIUzI1NiJ9."
    "eyJqdGkiOiJ0bWUiLCJpYXQiOjE3Njg3OTI0ODksInN1YiI6InBhc3NwcG9ydCIs"
    "ImxvZ2luVHlwZSI6NSwibWlkIjo0Nzk3MDYzLCJsb2dpblNvdXJjZSI6bnVsbCwid"
    "GVuYW50IjoibXVzaWNpYW4iLCJleHAiOjE3NzEzODQ0ODl9."
    "3ylupF8NP2rdAc1ACG2_Ayc_YEEBTC2rgM73gNYFA1k"
)


# ===================== MySQL 配置（按你环境改） =====================
MYSQL_CONFIG = {
    "host": "localhost",
    "port": 3306,
    "user": "root",
    "password": "root",
    "db": "t_music_data",
    "charset": "utf8mb4",
    "connect_timeout": 5,
    "read_timeout": 10,
    "write_timeout": 10,
}


# ===================== 腾讯音乐 total API =====================
def build_headers_tme(tme_song_id: str) -> dict:
    """token 固定；trace/herf 随 songId 动态"""
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0"
        ),
        "Referer": "https://y.tencentmusic.com/",
        "Content-Type": "application/json;charset=utf-8",
        "tme-header-feferer": "/",
        "tme-header-token": TME_HEADER_TOKEN,
        "tme-header-trace": f"trace_{int(time.time())}",
        "tme-header-herf": f"https://y.tencentmusic.com/#/user/organdata/works/detail/{tme_song_id}",
        "tme-source-platform": "0",
    }


def fetch_tme_total(tme_song_id: str) -> dict:
    url = (
        "https://y.tencentmusic.com/"
        f"cd-gateway/musician/song/figure/total?songId={tme_song_id}"
    )
    headers = build_headers_tme(tme_song_id)
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    j = resp.json()

    # 成功一般 code == "0"
    if str(j.get("code")) != "0" or not j.get("data"):
        raise RuntimeError(f"接口返回异常：{j}")

    return j["data"]


# ===================== 数据解析 =====================
def ms_to_cn_date(ms: int) -> str:
    """毫秒时间戳 -> 北京时间 YYYY-MM-DD"""
    tz_cn = timezone(timedelta(hours=8))
    dt = datetime.fromtimestamp(int(ms) / 1000, tz=tz_cn)
    return dt.strftime("%Y-%m-%d")


def safe_int(v, default=0) -> int:
    try:
        return int(v or 0)
    except Exception:
        return default


def parse_favor_share_by_platform(data_obj: dict):
    """
    从 data 中提取三平台：
      qyin/kugou/kuwo 的累计收藏 & 累计转发
    返回：
      record_date(str), rows(list[dict])
    """
    if data_obj.get("statisDay"):
        record_date = ms_to_cn_date(data_obj["statisDay"])
    else:
        record_date = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")

    favor_block = data_obj.get("cumulativeFavorCnt", {}) or {}
    share_block = data_obj.get("cumulativeShareCnt", {}) or {}

    rows = []
    for pcode in ("qyin", "kugou", "kuwo"):
        rows.append({
            "platform_code": pcode,
            "collect_count": safe_int(favor_block.get(pcode, 0)),
            "share_count": safe_int(share_block.get(pcode, 0)),
            "record_date": record_date,
        })
    return record_date, rows


# ===================== MySQL 读写 =====================
def get_conn():
    return pymysql.connect(**MYSQL_CONFIG)


def get_platform_code_to_id() -> dict:
    """
    从 t_platform 读取 platform_code -> platform_id
    需要表里有 qyin/kugou/kuwo 三条
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT platform_id, platform_code FROM t_platform;")
            rows = cur.fetchall()
        return {code: pid for pid, code in rows}
    finally:
        conn.close()


def upsert_favor_share(song_id: int, platform_id: int, record_date: str, collect_count: int, share_count: int):
    """
    只更新 collect_count / share_count。
    若该 song_id+platform_id+record_date 已存在：UPDATE
    否则：INSERT（comment_count 不动，默认 0/或库默认）
    """
    conn = get_conn()
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


# ===================== CLI 参数 =====================
def parse_args():
    p = argparse.ArgumentParser(
        description="抓取腾讯音乐端 figure/total 的 收藏/转发，并写入 t_song_interaction_history（qyin/kugou/kuwo）"
    )
    p.add_argument("--song_id", type=int, required=True, help="数据库里的 song_id（写入 t_song_interaction_history）")
    p.add_argument(
        "--platform_song_code",
        required=True,
        help="腾讯音乐端 songId（即 figure/total?songId=xxxx 的 xxxx）",
    )
    p.add_argument(
        "--dry_run",
        action="store_true",
        help="只打印将要写入的内容，不写数据库",
    )
    return p.parse_args()


# ===================== main =====================
def main():
    args = parse_args()
    song_id = args.song_id
    tme_song_id = str(args.platform_song_code).strip()

    print(f"🚀 开始抓取 total：song_id={song_id} | tme_song_id={tme_song_id}")
    data_obj = fetch_tme_total(tme_song_id)
    record_date, rows = parse_favor_share_by_platform(data_obj)

    code_to_id = get_platform_code_to_id()
    missing = [c for c in ("qyin", "kugou", "kuwo") if c not in code_to_id]
    if missing:
        raise RuntimeError(f"t_platform 缺少平台定义：{missing}（需要 qyin/kugou/kuwo）")

    print(f"📅 record_date = {record_date}")
    print("=== 解析结果（将写入）===")
    for r in rows:
        print(f"  {r['platform_code']}: collect={r['collect_count']} share={r['share_count']}")

    if args.dry_run:
        print("🟡 dry_run=TRUE：未写入数据库")
        return

    ok = 0
    for r in rows:
        platform_id = code_to_id[r["platform_code"]]
        action = upsert_favor_share(
            song_id=song_id,
            platform_id=platform_id,
            record_date=r["record_date"],
            collect_count=r["collect_count"],
            share_count=r["share_count"],
        )
        ok += 1
        print(f"✅ {action}成功：platform={r['platform_code']} (platform_id={platform_id}) collect={r['collect_count']} share={r['share_count']}")

    print(f"\n📌 完成：共处理 {ok} 条（qyin/kugou/kuwo）")


if __name__ == "__main__":
    main()
