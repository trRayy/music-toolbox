# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime

import pandas as pd
import pymysql
from pymysql.err import OperationalError
from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager
from config_loader import get_setting


DEFAULT_COOKIE_STR = get_setting(
    "QQMUSIC_COOKIE",
    "uin=YOUR_QQ_UIN; qqmusic_key=YOUR_QQMUSIC_KEY; qm_keyst=YOUR_QM_KEYST",
)
NULL_REPLACE = "未知"
QQMUSIC_PLATFORM_ID = 1
APP_DIR = os.path.dirname(os.path.abspath(__file__))
QQMUSIC_HOME_URL = "https://y.qq.com/"
QQMUSIC_LOGIN_READY_KEYS = ("uin", "qqmusic_key", "qm_keyst")


def find_chrome_binary():
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def cookies_to_string(cookies):
    parts = []
    for cookie in cookies:
        name = str(cookie.get("name") or "").strip()
        value = str(cookie.get("value") or "").strip()
        if name:
            parts.append(f"{name}={value}")
    return "; ".join(parts)


def has_qqmusic_login_cookie(cookie_dict):
    if not cookie_dict.get("uin"):
        return False
    return bool(cookie_dict.get("qqmusic_key") or cookie_dict.get("qm_keyst"))


def try_open_login_entry(driver, logger=None):
    if logger is None:
        logger = print

    selectors = [
        (By.XPATH, "//a[contains(., '登录')]"),
        (By.XPATH, "//button[contains(., '登录')]"),
        (By.CSS_SELECTOR, "a[href*='login']"),
        (By.CSS_SELECTOR, "button[class*='login']"),
        (By.CSS_SELECTOR, "[class*='login']"),
    ]

    for by, selector in selectors:
        try:
            elements = driver.find_elements(by, selector)
        except Exception:
            continue
        for element in elements:
            try:
                text = (element.text or "").strip()
                if not element.is_displayed():
                    continue
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
                time.sleep(0.5)
                element.click()
                logger(f"已尝试点击登录入口：{text or selector}")
                return True
            except Exception:
                continue

    logger("未自动找到明确的登录按钮，如页面未弹出登录框，请手动点击页面右上角登录。")
    return False


def load_manual_cookie(driver, cookie_str):
    driver.get("https://y.qq.com/")
    for cookie in cookie_str.split("; "):
        if "=" not in cookie:
            continue
        key, value = cookie.split("=", 1)
        driver.add_cookie(
            {
                "name": key.strip(),
                "value": value.strip(),
                "domain": ".qq.com",
                "path": "/",
            }
        )
    driver.refresh()
    time.sleep(2)
    return True


def first_non_empty_text(elements):
    for elem in elements:
        try:
            text = (elem.text or "").strip()
        except Exception:
            continue
        if text:
            return text
    return ""


def extract_comment_nickname(item):
    selectors = [
        (By.CSS_SELECTOR, ".comment__title a"),
        (By.CSS_SELECTOR, ".comment__avatar img[alt]"),
        (By.XPATH, ".//*[contains(@class, 'comment__title')]//a"),
        (By.XPATH, ".//*[contains(@class, 'nick') or contains(@class, 'name') or contains(@class, 'user')]"),
    ]

    for by, selector in selectors:
        try:
            elems = item.find_elements(by, selector)
        except Exception:
            continue

        if by == By.CSS_SELECTOR and selector == ".comment__avatar img[alt]":
            for elem in elems:
                try:
                    alt = (elem.get_attribute("alt") or "").strip()
                except Exception:
                    continue
                if alt:
                    return alt
            continue

        text = first_non_empty_text(elems)
        if text:
            return text

    return ""


def extract_comment_content(item):
    selectors = [
        (By.CSS_SELECTOR, ".comment__text span"),
        (By.CSS_SELECTOR, ".comment__text"),
        (By.XPATH, ".//*[contains(@class, 'text') or contains(@class, 'content')]"),
    ]
    for by, selector in selectors:
        try:
            text = first_non_empty_text(item.find_elements(by, selector))
        except Exception:
            continue
        if text:
            return text
    return ""


def extract_comment_time(item):
    selectors = [
        (By.CSS_SELECTOR, ".comment__date"),
        (By.XPATH, ".//*[contains(@class, 'time') or contains(@class, 'date')]"),
    ]
    for by, selector in selectors:
        try:
            text = first_non_empty_text(item.find_elements(by, selector))
        except Exception:
            continue
        if text:
            return text.replace("\xa0", " ")
    return ""


def extract_like_num(item):
    selectors = [
        (By.CSS_SELECTOR, ".comment__zan"),
        (By.XPATH, ".//*[contains(@class, 'praise') or contains(@class, 'like') or contains(@class, 'zan')]"),
    ]
    for by, selector in selectors:
        try:
            elems = item.find_elements(by, selector)
        except Exception:
            continue
        for elem in elems:
            try:
                text = (elem.text or "").strip()
            except Exception:
                continue
            if not text:
                continue
            digits = "".join(ch for ch in text if ch.isdigit())
            return digits or "0"
    return "0"


def build_driver():
    chrome_options = Options()
    chrome_binary = find_chrome_binary()
    if chrome_binary:
        chrome_options.binary_location = chrome_binary
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    chrome_options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
    )
    # Prefer Selenium Manager first to avoid webdriver_manager cache permission issues.
    try:
        driver = webdriver.Chrome(options=chrome_options)
    except Exception:
        os.environ.setdefault("WDM_LOCAL", "1")
        os.makedirs(os.path.join(APP_DIR, ".wdm"), exist_ok=True)
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=chrome_options,
        )
    driver.maximize_window()
    return driver


def collect_qqmusic_cookie_via_login(timeout_seconds=180, logger=None):
    if logger is None:
        logger = print

    driver = build_driver()
    try:
        logger("正在打开 QQ 音乐首页...")
        driver.get(QQMUSIC_HOME_URL)
        time.sleep(3)
        try_open_login_entry(driver, logger=logger)
        logger("请在打开的浏览器中完成扫码登录，程序会在检测到可用 Cookie 后自动回填。")

        deadline = time.time() + max(int(timeout_seconds), 30)
        last_report_second = -1
        while time.time() < deadline:
            cookies = driver.get_cookies()
            cookie_dict = {str(item.get("name") or "").strip(): str(item.get("value") or "").strip() for item in cookies}
            if has_qqmusic_login_cookie(cookie_dict):
                cookie_str = cookies_to_string(cookies)
                logger(f"已检测到登录成功，获取到 {len(cookies)} 个 Cookie。")
                return cookie_str

            remaining = int(deadline - time.time())
            if remaining % 10 == 0 and remaining != last_report_second:
                last_report_second = remaining
                logger(f"等待扫码登录完成... 剩余 {remaining} 秒")
            time.sleep(1)

        raise TimeoutError(f"等待 QQ 音乐扫码登录超时（{timeout_seconds} 秒）。")
    finally:
        driver.quit()


def get_qqmusic_comments(song_platform_code, cookie_str=DEFAULT_COOKIE_STR, logger=None):
    if logger is None:
        logger = print

    driver = build_driver()
    wait = WebDriverWait(driver, 20)
    all_comments = []

    try:
        logger("加载Cookie并尝试免登...")
        load_manual_cookie(driver, cookie_str)

        song_url = f"https://y.qq.com/n/ryqq/songDetail/{song_platform_code}"
        driver.get(song_url)
        logger(f"已打开歌曲页面：{song_url}")
        time.sleep(5)

        logger("滚动到评论区...")
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.8);")
        time.sleep(3)
        try:
            comment_title = driver.find_element(By.XPATH, "//*[contains(text(), '评论')]")
            ActionChains(driver).scroll_to_element(comment_title).perform()
            time.sleep(2)
        except Exception:
            pass

        logger("查找评论列表...")
        comment_items = []
        locators = [
            (By.CSS_SELECTOR, "#comment_box .comment__list .comment__list_item"),
            (By.CSS_SELECTOR, ".mod_hot_comment .comment__list_item"),
            (By.XPATH, "//li[contains(@class, 'comment__list_item')]"),
            (By.XPATH, "//div[contains(text(), '暂无评论')]"),
        ]

        for locator in locators:
            try:
                elements = wait.until(EC.presence_of_all_elements_located(locator))
            except Exception:
                continue
            if elements:
                comment_items = elements
                logger(f"找到 {len(comment_items)} 个评论项")
                break

        if "暂无评论" in [item.text for item in comment_items]:
            logger("该歌曲暂无评论")
            return pd.DataFrame()

        for idx, item in enumerate(comment_items):
            try:
                nick = extract_comment_nickname(item)
                content = extract_comment_content(item)
                comment_time = extract_comment_time(item)
                like_num = extract_like_num(item)
                if not content:
                    continue
                all_comments.append(
                    {
                        "用户昵称": nick,
                        "评论内容": content,
                        "评论时间": comment_time,
                        "点赞数": like_num,
                    }
                )
            except Exception as e:
                logger(f"提取第{idx}条评论失败：{e}")

        logger(f"最终提取到 {len(all_comments)} 条有效评论")

    except Exception as e:
        logger(f"\n爬取过程出错：{e}")
    finally:
        driver.quit()

    return pd.DataFrame(all_comments)


def parse_comment_time_and_ip(time_str):
    publish_time = None
    comment_ip = NULL_REPLACE

    if pd.isna(time_str) or time_str.strip() == "":
        return publish_time, comment_ip

    if "来自" in time_str:
        time_part, ip_part = time_str.split("来自", 1)
        comment_ip = ip_part.strip() or NULL_REPLACE
        time_part = time_part.strip()
    else:
        time_part = time_str.strip()

    current_year = datetime.now().year
    try:
        if "年" in time_part and "月" in time_part and "日" in time_part:
            time_part = time_part.replace("年", "-").replace("月", "-").replace("日", "").strip()
            publish_time = datetime.strptime(time_part, "%Y-%m-%d %H:%M").strftime("%Y-%m-%d %H:%M:%S")
        elif "月" in time_part and "日" in time_part:
            time_part = f"{current_year}年" + time_part
            time_part = time_part.replace("年", "-").replace("月", "-").replace("日", "").strip()
            publish_time = datetime.strptime(time_part, "%Y-%m-%d %H:%M").strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        publish_time = None

    return publish_time, comment_ip


def save_comments_to_db(df_raw, song_id, mysql_config, logger=None):
    if logger is None:
        logger = print

    if df_raw.empty:
        logger("无评论数据可清洗入库")
        return

    logger("开始数据清洗...")
    df = df_raw.copy()
    df = df[~df["评论内容"].str.contains("说说你的看法吧", na=False)]
    df = df[df["评论内容"].str.strip() != ""]
    df = df.drop_duplicates(subset=["用户昵称", "评论内容", "评论时间"], keep="first")
    df["点赞数"] = df["点赞数"].fillna(0).astype(str).str.strip()
    df["点赞数"] = pd.to_numeric(df["点赞数"], errors="coerce").fillna(0).astype(int)
    df = df.reset_index(drop=True)

    current_year = datetime.now().year
    logger(f"拆分评论时间和IP地址（无年份日期默认补 {current_year} 年）...")
    df[["publish_time", "comment_ip"]] = df["评论时间"].apply(
        lambda x: pd.Series(parse_comment_time_and_ip(x))
    )

    df["comment_id"] = [f"cmt_{uuid.uuid4().hex[:8]}" for _ in range(len(df))]
    df["platform_id"] = QQMUSIC_PLATFORM_ID
    df["song_id"] = song_id
    df["user_nickname"] = df["用户昵称"].fillna(NULL_REPLACE).astype(str).str.strip()
    df["user_nickname"] = df["user_nickname"].replace("", NULL_REPLACE)
    df["is_deleted"] = 0

    null_time_count = df["publish_time"].isna().sum()
    unknown_ip_count = (df["comment_ip"] == NULL_REPLACE).sum()
    unknown_nickname_count = (df["user_nickname"] == NULL_REPLACE).sum()
    logger(f"空时间数据：{null_time_count} 条（存入 NULL）")
    logger(f"替换为 {NULL_REPLACE} 的 IP 数据：{unknown_ip_count} 条")
    logger(f"替换为 {NULL_REPLACE} 的昵称数据：{unknown_nickname_count} 条")

    try:
        conn = pymysql.connect(**mysql_config)
        cursor = conn.cursor()
        logger("数据库连接成功")
    except OperationalError as e:
        logger(f"数据库连接失败：{e}")
        return

    try:
        logger("清空 QQ 音乐旧评论数据...")
        cursor.execute("DELETE FROM t_comment WHERE platform_id = %s AND song_id = %s", (QQMUSIC_PLATFORM_ID, song_id))
        conn.commit()
        logger(f"已删除 {cursor.rowcount} 条旧评论数据")

        insert_sql = """
        INSERT INTO t_comment (
            comment_id, song_id, platform_id, comment_content,
            publish_time, comment_ip, user_nickname, is_deleted
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            comment_content = VALUES(comment_content),
            publish_time = VALUES(publish_time),
            comment_ip = VALUES(comment_ip),
            user_nickname = VALUES(user_nickname)
        """

        insert_data = []
        skip_count = 0
        for _, row in df.iterrows():
            if pd.isna(row["comment_id"]) or row["评论内容"].strip() == "":
                skip_count += 1
                continue
            insert_data.append(
                (
                    row["comment_id"],
                    row["song_id"],
                    row["platform_id"],
                    row["评论内容"].strip(),
                    row["publish_time"],
                    row["comment_ip"],
                    row["user_nickname"],
                    row["is_deleted"],
                )
            )

        if insert_data:
            cursor.executemany(insert_sql, insert_data)
            conn.commit()
            logger(f"成功插入 {len(insert_data)} 条评论数据到数据库")
        else:
            logger("无有效数据可插入")

        logger(f"原始抓取：{len(df_raw)} 条")
        logger(f"清洗后：{len(df)} 条")
        logger(f"空时间数据：{null_time_count} 条")
        logger(f"未知 IP：{unknown_ip_count} 条")
        logger(f"未知昵称：{unknown_nickname_count} 条")
        logger(f"跳过无效数据：{skip_count} 条")
        logger(f"成功入库：{len(insert_data)} 条")

    except Exception as e:
        conn.rollback()
        logger(f"数据插入失败：{e}")
    finally:
        cursor.close()
        conn.close()
        logger("数据库连接已关闭")


def crawl_qqmusic_comments_to_db(song_id, song_platform_code, mysql_config, cookie_str=DEFAULT_COOKIE_STR, logger=None):
    if logger is None:
        logger = print

    logger(f"开始爬取歌曲 ID {song_id}（QQ音乐ID：{song_platform_code}）的评论...")
    df_comments = get_qqmusic_comments(song_platform_code, cookie_str, logger)
    if df_comments.empty:
        logger("\n未爬取到任何评论（可能是暂无评论或页面结构变化）")
        return

    save_comments_to_db(df_comments, song_id, mysql_config, logger)
    logger("\nQQ音乐评论爬取并入库完成")
