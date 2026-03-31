# -*- coding: utf-8 -*-
import os
import re
import time
import numpy as np
import cv2
import pytesseract
from dataclasses import dataclass
from datetime import datetime
import pymysql
from DrissionPage import ChromiumPage


def build_qishui_url(track_id: str) -> str:
    return (
        "https://music.douyin.com/qishui/share/track"
        f"?track_id={track_id}&hybrid_sdk_version=bullet&auto_play_bgm=1"
    )


def ensure_dir(d: str):
    os.makedirs(d, exist_ok=True)


def save_png(img, path):
    ensure_dir(os.path.dirname(path) or ".")
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise RuntimeError("cv2.imencode failed")
    buf.tofile(path)


def imread_cv(path: str):
    data = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(path)
    return img


def to_gray(bgr):
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def crop(img, x1, y1, x2, y2):
    H, W = img.shape[:2]
    x1 = clamp(int(x1), 0, W - 1)
    x2 = clamp(int(x2), 0, W)
    y1 = clamp(int(y1), 0, H - 1)
    y2 = clamp(int(y2), 0, H)
    if x2 <= x1 or y2 <= y1:
        return None
    return img[y1:y2, x1:x2].copy()


@dataclass
class Box:
    x: int
    y: int
    w: int
    h: int
    area: int

    @property
    def cx(self): return self.x + self.w / 2

    @property
    def cy(self): return self.y + self.h / 2


def normalize_bw(bw):
    white_ratio = (bw > 0).mean()
    if white_ratio > 0.75:
        bw = 255 - bw

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, kernel, iterations=2)

    k2 = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    bw = cv2.dilate(bw, k2, iterations=2)

    return bw


def ocr_digits_best(roi_bgr, debug_prefix="", out_dir=""):
    if roi_bgr is None or roi_bgr.size == 0:
        return 0, "", -1.0, None

    gray = to_gray(roi_bgr)
    scale = 8
    gray = cv2.resize(
        gray,
        (gray.shape[1] * scale, gray.shape[0] * scale),
        interpolation=cv2.INTER_CUBIC,
    )

    bws = []

    g1 = cv2.GaussianBlur(gray, (3, 3), 0)
    _, bw1 = cv2.threshold(g1, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bws.append(("bw_otsu", bw1))

    bw2 = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, 31, 7
    )
    bws.append(("bw_adapt", bw2))

    g3 = cv2.convertScaleAbs(gray, alpha=1.7, beta=-35)
    g3 = cv2.GaussianBlur(g3, (3, 3), 0)
    _, bw3 = cv2.threshold(g3, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bws.append(("bw_contrast", bw3))

    best_num = 0
    best_text = ""
    best_conf = -1.0
    best_bw = None
    best_len = 0

    for name, bw in bws:
        bw = normalize_bw(bw)

        if debug_prefix and out_dir:
            save_png(bw, os.path.join(out_dir, f"{debug_prefix}_{name}.png"))

        for psm in [7, 6, 8, 11, 13]:
            config = (
                f'--oem 3 --psm {psm} '
                '-c tessedit_char_whitelist=0123456789 '
                '-c classify_bln_numeric_mode=1'
            )
            data = pytesseract.image_to_data(
                bw, config=config, output_type=pytesseract.Output.DICT
            )

            txts = []
            confs = []
            for t, c in zip(data.get("text", []), data.get("conf", [])):
                t = (t or "").strip()
                if not t:
                    continue
                if re.fullmatch(r"\d+", t):
                    txts.append(t)
                    try:
                        confs.append(float(c))
                    except:
                        pass

            if not txts:
                continue

            joined = "".join(txts)
            nums = re.findall(r"\d+", joined)
            if not nums:
                continue

            num_str = max(nums, key=len)
            cand_len = len(num_str)
            cand_conf = float(np.mean(confs)) if confs else 0.0

            if (cand_len > best_len) or (cand_len == best_len and cand_conf > best_conf):
                best_len = cand_len
                best_conf = cand_conf
                best_text = joined
                best_num = int(num_str)
                best_bw = bw

    return best_num, best_text, best_conf, best_bw


def find_three_icons(screen_bgr, out_dir=""):
    H, W = screen_bgr.shape[:2]

    y1 = int(H * 0.50)
    y2 = int(H * 0.80)
    x1 = int(W * 0.05)
    x2 = int(W * 0.60)
    roi = crop(screen_bgr, x1, y1, x2, y2)
    if roi is None:
        raise RuntimeError("lower roi crop failed")

    b, g, r = cv2.split(roi)
    mask = ((b > 210) & (g > 210) & (r > 210)).astype(np.uint8) * 255

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)

    if out_dir:
        save_png(roi, os.path.join(out_dir, "roi_lower.png"))
        save_png(mask, os.path.join(out_dir, "mask_lower.png"))

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)

    boxes = []
    for i in range(1, num_labels):
        x, y, w, h, area = stats[i]
        if area < 250:
            continue
        if w < 25 or h < 25:
            continue
        if w > 160 or h > 160:
            continue
        boxes.append(Box(x=x, y=y, w=w, h=h, area=area))

    if not boxes:
        raise RuntimeError("no icon candidates found")

    ys = np.array([b.cy for b in boxes])
    y_med = np.median(ys)
    row = [b for b in boxes if abs(b.cy - y_med) < 45]

    row = sorted(row, key=lambda b: b.area, reverse=True)[:12]
    row = sorted(row, key=lambda b: b.x)

    best_triplet = None
    best_score = 1e18
    for i in range(0, len(row) - 2):
        a, b, c = row[i], row[i + 1], row[i + 2]
        d1 = b.cx - a.cx
        d2 = c.cx - b.cx
        if d1 < 40 or d2 < 40 or d1 > 260 or d2 > 260:
            continue
        score = abs(d1 - d2) + 0.002 * (a.area + b.area + c.area) * -1
        if score < best_score:
            best_score = score
            best_triplet = (a, b, c)

    if best_triplet is None:
        best_triplet = (row[0], row[1], row[2])

    if out_dir:
        dbg = roi.copy()
        for idx, bx in enumerate(best_triplet):
            cv2.rectangle(dbg, (bx.x, bx.y), (bx.x + bx.w, bx.y + bx.h), (0, 255, 255), 2)
            cv2.putText(dbg, f"icon{idx}", (bx.x, bx.y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        save_png(dbg, os.path.join(out_dir, "debug_icons.png"))

    mapped = []
    for bx in best_triplet:
        mapped.append(Box(x=bx.x + x1, y=bx.y + y1, w=bx.w, h=bx.h, area=bx.area))
    mapped = sorted(mapped, key=lambda b: b.x)
    return mapped


def number_roi_for_icon(screen_bgr, icon_box: Box, kind: str):
    x, y, w, h = icon_box.x, icon_box.y, icon_box.w, icon_box.h

    y2 = y + int(0.40 * h)
    y1 = y - int(1.05 * h)

    x1 = x + int(0.25 * w)

    if kind == "comment":
        x2 = x + int(2.30 * w)
    elif kind == "share":
        x2 = x + int(2.60 * w)
    else:  # like
        x2 = x + int(2.80 * w)

    roi = crop(screen_bgr, x1, y1, x2, y2)
    return roi, (x1, y1, x2, y2)


def screenshot_page(url: str, out_path: str, wait_sec: int):
    page = ChromiumPage()
    page.set.user_agent(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0"
    )
    page.get(url)
    time.sleep(wait_sec)
    page.get_screenshot(path=out_path)
    page.quit()


def extract_like_comment_share_from_screenshot(screen_path: str, out_dir=""):
    screen = imread_cv(screen_path)

    icons = find_three_icons(screen, out_dir=out_dir)
    kinds = ["like", "comment", "share"]

    results = {}
    for kind, icon in zip(kinds, icons):
        roi, (x1, y1, x2, y2) = number_roi_for_icon(screen, icon, kind)
        if roi is None:
            results[kind] = 0
            continue

        if out_dir:
            save_png(roi, os.path.join(out_dir, f"roi_{kind}.png"))

        num, _, _, _ = ocr_digits_best(roi, debug_prefix=f"bw_{kind}", out_dir=out_dir)
        results[kind] = num

        if out_dir:
            dbg = screen.copy()
            cv2.rectangle(dbg, (icon.x, icon.y), (icon.x + icon.w, icon.y + icon.h), (0, 255, 255), 2)
            cv2.rectangle(dbg, (int(x1), int(y1)), (int(x2), int(y2)), (0, 200, 0), 2)
            cv2.putText(
                dbg, f"{kind}={num}",
                (int(x1), max(0, int(y1) - 8)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 0), 2
            )
            save_png(dbg, os.path.join(out_dir, f"debug_{kind}.png"))

    return results


def save_interaction_to_mysql(mysql_config: dict, song_id: int, platform_id: int, ocr_result: dict, record_date=None):
    """
    写入 t_song_interaction_history
    映射：
      - collect_count <- like（点赞）
      - comment_count <- comment
      - share_count   <- share
    """
    if record_date is None:
        record_date = datetime.now().date()

    like = int(ocr_result.get("like", 0) or 0)
    comment = int(ocr_result.get("comment", 0) or 0)
    share = int(ocr_result.get("share", 0) or 0)

    conn = pymysql.connect(**mysql_config)
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
                    SET comment_count=%s, collect_count=%s, share_count=%s
                    WHERE id=%s;
                    """,
                    (comment, like, share, hid),
                )
                action = "更新"
            else:
                cur.execute(
                    """
                    INSERT INTO t_song_interaction_history
                      (song_id, platform_id, comment_count, collect_count, share_count, record_date)
                    VALUES
                      (%s, %s, %s, %s, %s, %s);
                    """,
                    (song_id, platform_id, comment, like, share, record_date),
                )
                action = "插入"

        conn.commit()
        return action, like, comment, share, record_date
    finally:
        conn.close()


def run_qishui_ocr_to_db(
    *,
    mysql_config: dict,
    song_id: int,
    platform_id: int,
    track_id: str,
    out_dir: str,
    wait_sec: int,
    tesseract_cmd: str,
    logger=print,
):
    """
    GUI 调用入口：截图 -> OCR -> 入库
    返回：ocr_result dict
    """
    ensure_dir(out_dir)

    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    if not os.path.exists(tesseract_cmd):
        raise FileNotFoundError(f"Tesseract 路径不存在：{tesseract_cmd}")

    url = build_qishui_url(track_id)
    logger(f"🔗 URL: {url}")

    screen_path = os.path.join(out_dir, "full.png")
    screenshot_page(url, screen_path, wait_sec=wait_sec)
    logger(f"✅ 截图完成: {screen_path}")

    ocr_result = extract_like_comment_share_from_screenshot(screen_path, out_dir=out_dir)
    logger(f"🎯 OCR: like={ocr_result.get('like',0)} comment={ocr_result.get('comment',0)} share={ocr_result.get('share',0)}")

    action, like, comment, share, record_date = save_interaction_to_mysql(
        mysql_config, song_id, platform_id, ocr_result
    )
    logger(f"✅ 已{action}入库：song_id={song_id}, platform_id={platform_id}, date={record_date}, like={like}, comment={comment}, share={share}")
    logger(f"📁 Debug目录：{out_dir}")

    return ocr_result
