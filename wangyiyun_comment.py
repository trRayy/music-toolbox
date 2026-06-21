from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pymysql
from config_loader import get_mysql_config


DEFAULT_COOKIE_ENV = "NETEASE_COOKIE"
DEFAULT_PAGE_SIZE = 50
DEFAULT_SLEEP_SECONDS = 0.8
DEFAULT_PLATFORM_ID = 4
DEFAULT_TARGET_TABLE = "t_comment"
DEFAULT_TEST_TABLE = "t_comment_wangyiyun_test"
NULL_REPLACE = "未知"


def extract_song_id(song_value: str) -> str:
    song_value = str(song_value or "").strip()
    if not song_value:
        raise ValueError("网易云歌曲ID不能为空")

    if song_value.isdigit():
        return song_value

    match = re.search(r"[?&]id=(\d+)", song_value)
    if match:
        return match.group(1)

    raise ValueError(f"无法从输入中解析网易云歌曲ID: {song_value}")


def build_headers(song_id: str, cookie: str) -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        ),
        "Referer": f"https://music.163.com/#/song?id={song_id}",
        "Origin": "https://music.163.com",
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Cookie": cookie,
    }


def request_json(url: str, params: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    request = Request(f"{url}?{urlencode(params)}", headers=headers)
    with urlopen(request, timeout=20) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        payload = response.read().decode(charset, errors="replace")
    return json.loads(payload)


def fetch_comment_page(song_id: str, offset: int, limit: int, cookie: str) -> dict[str, Any]:
    url = f"https://music.163.com/api/v1/resource/comments/R_SO_4_{song_id}"
    params = {
        "offset": offset,
        "limit": limit,
        "beforeTime": 0,
    }
    return request_json(url, params, build_headers(song_id, cookie))


def normalize_comment(comment: dict[str, Any]) -> dict[str, Any]:
    raw_comment_id = str(comment.get("commentId") or "").strip()
    comment_id = f"wy_{raw_comment_id}" if raw_comment_id else ""
    content = str(comment.get("content") or "").strip()
    raw_time = comment.get("time")

    user = comment.get("user") or {}
    nickname = str(user.get("nickname") or "").strip() or NULL_REPLACE

    ip_value = ""
    ip_location = comment.get("ipLocation")
    if isinstance(ip_location, dict):
        ip_value = str(
            ip_location.get("ip")
            or ip_location.get("location")
            or ip_location.get("country")
            or ""
        ).strip()
    elif ip_location:
        ip_value = str(ip_location).strip()

    if not ip_value:
        ip_value = str(comment.get("ip") or "").strip()

    time_ms = int(raw_time) if isinstance(raw_time, (int, float)) else 0
    publish_time = (
        datetime.fromtimestamp(time_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
        if time_ms
        else None
    )

    return {
        "comment_id": comment_id,
        "comment_content": content,
        "comment_ip": ip_value or NULL_REPLACE,
        "publish_time": publish_time,
        "user_nickname": nickname,
        "_time_ms": time_ms,
    }


def sort_comments(comments: list[dict[str, Any]], order: str) -> list[dict[str, Any]]:
    reverse = order == "desc"
    return sorted(comments, key=lambda item: int(item.get("_time_ms", 0)), reverse=reverse)


def fetch_comments(
    song_value: str,
    cookie: str,
    *,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_comments: int | None = None,
    sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
    sort_order: str = "desc",
    logger=print,
) -> tuple[str, list[dict[str, Any]]]:
    song_id = extract_song_id(song_value)
    comments: list[dict[str, Any]] = []
    offset = 0
    page_index = 1

    while True:
        data = fetch_comment_page(song_id, offset=offset, limit=page_size, cookie=cookie)
        page_comments = data.get("comments") or []

        if not page_comments:
            break

        normalized = [normalize_comment(item) for item in page_comments]
        comments.extend(normalized)
        logger(f"第 {page_index} 页抓取完成：本页 {len(normalized)} 条，累计 {len(comments)} 条")

        if max_comments is not None and len(comments) >= max_comments:
            comments = comments[:max_comments]
            break

        if not data.get("more"):
            break

        offset += page_size
        page_index += 1
        time.sleep(max(sleep_seconds, 0))

    return song_id, sort_comments(comments, sort_order)


def ensure_test_table(mysql_config: dict, table_name: str, logger=print) -> None:
    if table_name == DEFAULT_TARGET_TABLE:
        return

    conn = pymysql.connect(**mysql_config)
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE TABLE IF NOT EXISTS `{table_name}` LIKE `t_comment`;")
        conn.commit()
        logger(f"已确认测试表存在：{table_name}")
    finally:
        conn.close()


def save_comments_to_db(
    comments: list[dict[str, Any]],
    song_id: int,
    platform_id: int,
    mysql_config: dict,
    *,
    table_name: str = DEFAULT_TARGET_TABLE,
    logger=print,
) -> int:
    if not comments:
        logger("没有可入库的评论数据")
        return 0

    conn = pymysql.connect(**mysql_config)
    try:
        with conn.cursor() as cur:
            delete_sql = f"DELETE FROM `{table_name}` WHERE platform_id = %s AND song_id = %s"
            cur.execute(delete_sql, (platform_id, song_id))
            logger(f"已清理 {table_name} 中 song_id={song_id}, platform_id={platform_id} 的旧数据 {cur.rowcount} 条")

            insert_sql = f"""
            INSERT INTO `{table_name}` (
                comment_id, song_id, platform_id, comment_content,
                publish_time, comment_ip, user_nickname, is_deleted
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                comment_content = VALUES(comment_content),
                publish_time = VALUES(publish_time),
                comment_ip = VALUES(comment_ip),
                user_nickname = VALUES(user_nickname),
                is_deleted = VALUES(is_deleted)
            """

            rows = []
            for item in comments:
                if not item.get("comment_id") or not item.get("comment_content"):
                    continue
                rows.append(
                    (
                        item["comment_id"],
                        song_id,
                        platform_id,
                        item["comment_content"],
                        item["publish_time"],
                        item["comment_ip"],
                        item["user_nickname"],
                        0,
                    )
                )

            if not rows:
                logger("清洗后没有有效评论可写入")
                conn.rollback()
                return 0

            cur.executemany(insert_sql, rows)
        conn.commit()
        logger(f"成功写入 {len(rows)} 条评论到 {table_name}")
        return len(rows)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def crawl_wangyiyun_comments_to_db(
    *,
    song_id: int,
    song_platform_code: str,
    mysql_config: dict,
    cookie_str: str | None = None,
    cookie_env: str = DEFAULT_COOKIE_ENV,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_comments: int | None = None,
    sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
    sort_order: str = "desc",
    table_name: str = DEFAULT_TARGET_TABLE,
    platform_id: int = DEFAULT_PLATFORM_ID,
    logger=print,
) -> tuple[str, int]:
    cookie = (cookie_str or os.environ.get(cookie_env, "")).strip()
    if not cookie:
        raise ValueError(
            f"网易云 Cookie 为空。请填写 Cookie，或设置环境变量 {cookie_env}。"
        )

    ensure_test_table(mysql_config, table_name, logger=logger)
    parsed_song_code, comments = fetch_comments(
        song_platform_code,
        cookie,
        page_size=page_size,
        max_comments=max_comments,
        sleep_seconds=sleep_seconds,
        sort_order=sort_order,
        logger=logger,
    )
    inserted = save_comments_to_db(
        comments,
        song_id,
        platform_id,
        mysql_config,
        table_name=table_name,
        logger=logger,
    )
    logger(
        f"网易云评论抓取完成：song_id={song_id}, 网易云song_id={parsed_song_code}, 入库表={table_name}"
    )
    return parsed_song_code, inserted


def parse_args(argv: list[str] | None = None):
    import argparse

    default_db_config = get_mysql_config()
    parser = argparse.ArgumentParser(description="抓取网易云评论并写入MySQL")
    parser.add_argument("--song", required=True, help="网易云 song_id 或完整歌曲 URL")
    parser.add_argument("--song-id", type=int, required=True, help="本地数据库中的 song_id")
    parser.add_argument("--cookie-env", default=DEFAULT_COOKIE_ENV, help="Cookie 环境变量名")
    parser.add_argument("--cookie", default="", help="直接传入 Cookie")
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--max-comments", type=int, default=None)
    parser.add_argument("--sleep", type=float, default=DEFAULT_SLEEP_SECONDS)
    parser.add_argument("--sort", choices=("asc", "desc"), default="desc")
    parser.add_argument("--table-name", default=DEFAULT_TARGET_TABLE)
    parser.add_argument("--platform-id", type=int, default=DEFAULT_PLATFORM_ID)
    parser.add_argument("--host", default=default_db_config["host"])
    parser.add_argument("--port", type=int, default=default_db_config["port"])
    parser.add_argument("--user", default=default_db_config["user"])
    parser.add_argument("--password", default=default_db_config["password"])
    parser.add_argument("--db", default=default_db_config["db"])
    parser.add_argument("--charset", default=default_db_config["charset"])
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    mysql_config = {
        "host": args.host,
        "port": args.port,
        "user": args.user,
        "password": args.password,
        "db": args.db,
        "charset": args.charset,
        "connect_timeout": 5,
        "read_timeout": 10,
        "write_timeout": 10,
    }

    try:
        parsed_song_code, inserted = crawl_wangyiyun_comments_to_db(
            song_id=args.song_id,
            song_platform_code=args.song,
            mysql_config=mysql_config,
            cookie_str=args.cookie,
            cookie_env=args.cookie_env,
            page_size=args.page_size,
            max_comments=args.max_comments,
            sleep_seconds=args.sleep,
            sort_order=args.sort,
            table_name=args.table_name,
            platform_id=args.platform_id,
            logger=print,
        )
        print(f"完成：网易云 song_id={parsed_song_code}，共写入 {inserted} 条")
        return 0
    except HTTPError as exc:
        print(f"HTTP 错误 {exc.code}，可能是 Cookie 失效或接口变更", file=sys.stderr)
        return 2
    except URLError as exc:
        print(f"网络错误: {exc.reason}", file=sys.stderr)
        return 3
    except json.JSONDecodeError:
        print("返回结果不是合法 JSON，可能被拦截或重定向", file=sys.stderr)
        return 4
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 5


if __name__ == "__main__":
    raise SystemExit(run())
