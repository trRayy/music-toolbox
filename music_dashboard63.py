# -*- coding: utf-8 -*-
import os
import re
import io
import warnings
from datetime import datetime, timedelta, date
import hashlib

import streamlit as st
import pymysql
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.offline import plot

import jieba
from wordcloud import WordCloud
import matplotlib.pyplot as plt
from PIL import Image
import matplotlib.font_manager as fm
from collections import Counter
from config_loader import get_mysql_config

warnings.filterwarnings("ignore")

# ===================== 0) 基础配置 =====================
MYSQL_CONFIG = get_mysql_config(autocommit=True)

DEFAULT_SINGER_NAME = "魏子越"
DEFAULT_SONG_NAME = "《归来》"

# 词云停用词
STOP_WORDS = {
    "的", "了", "是", "我", "你", "他", "她", "它", "在", "有", "就", "都",
    "很", "也", "还", "不", "没", "和", "与", "及", "等", "个", "这", "那",
    "一个", "我们", "你们", "他们", "因为", "所以", "但是", "还是", "就是",
    "说说", "看法", "剩余", "字", "来自", "次", "天", "日", "月", "年",
}

# 事件标注配色（高区分度）
EVENT_COLORS = [
    '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', 
    '#e377c2', '#7f7f7f', '#bcbd22', '#17becf', '#aec7e8', '#ffbb78',
    '#98df8a', '#ff9896', '#c5b0d5', '#c49c94', '#f7b6d3', '#c7c7c7',
    '#dbdb8d', '#9edae5'
]

# ===================== 1) 中文字体自动配置（词云/绘图兼容） =====================
def get_chinese_font_path():
    font_names = ["SimHei", "Microsoft YaHei", "微软雅黑", "PingFang SC", "Heiti TC", "SimSun"]
    for font_name in font_names:
        try:
            font = fm.FontProperties(family=font_name)
            font_path = fm.findfont(font)
            if font_path and os.path.exists(font_path):
                return font_path
        except Exception:
            continue

    # 本地字体文件兜底
    local_candidates = [
        os.path.join(os.getcwd(), "fonts", "SimHei.ttf"),
        os.path.join(os.getcwd(), "SimHei.ttf"),
        os.path.join(os.getcwd(), "simhei.ttf"),
    ]
    for p in local_candidates:
        if os.path.exists(p):
            return p
    return None

CHINESE_FONT_PATH = get_chinese_font_path()
if CHINESE_FONT_PATH:
    try:
        plt.rcParams["font.sans-serif"] = [fm.FontProperties(fname=CHINESE_FONT_PATH).get_name()]
    except Exception:
        plt.rcParams["font.sans-serif"] = ["SimHei"]
else:
    plt.rcParams["font.sans-serif"] = ["SimHei"]
plt.rcParams["axes.unicode_minus"] = False  # 解决负号显示问题

# 词云配置
WC_CONFIG = {
    "width": 820,
    "height": 560,
    "background_color": "white",
    "max_words": 380,
    "max_font_size": 190,
    "random_state": 42,
    "stopwords": STOP_WORDS,
    "prefer_horizontal": 1.0,
    "relative_scaling": 0.5,
    "collocations": False,
    "font_path": CHINESE_FONT_PATH if CHINESE_FONT_PATH else None,
}

# ===================== 2) 通用工具函数 =====================
def safe_close(conn):
    """安全关闭数据库连接"""
    if conn is None:
        return
    try:
        if getattr(conn, "open", False):
            conn.close()
    except Exception:
        pass

def get_mysql_connection():
    """获取MySQL连接"""
    try:
        return pymysql.connect(**MYSQL_CONFIG)
    except Exception as e:
        st.error(f"❌ MySQL 连接失败：{e}")
        return None

def get_event_color(event_id: int) -> str:
    """为每个事件ID分配固定唯一颜色"""
    hash_val = int(hashlib.md5(str(event_id).encode()).hexdigest(), 16)
    return EVENT_COLORS[hash_val % len(EVENT_COLORS)]

# ===================== 3) 缓存查询函数（按模块拆分，避免重复查询） =====================
@st.cache_data(ttl=300)
def get_song_release_date_cached(song_name: str):
    """获取歌曲发行日期"""
    conn = get_mysql_connection()
    if not conn:
        return None
    try:
        sql = "SELECT release_date FROM t_song WHERE song_name = %s LIMIT 1"
        df = pd.read_sql(sql, conn, params=[song_name])
        if not df.empty and pd.notna(df.iloc[0]["release_date"]):
            return pd.to_datetime(df.iloc[0]["release_date"]).date()
        return None
    except Exception as e:
        st.warning(f"⚠️ 获取歌曲发行日期失败：{e}")
        return None
    finally:
        safe_close(conn)

@st.cache_data(ttl=300)
def get_all_singers_cached():
    """获取所有艺人"""
    conn = get_mysql_connection()
    if not conn:
        return [DEFAULT_SINGER_NAME]
    try:
        df = pd.read_sql("SELECT singer_name FROM t_singer ORDER BY singer_id", conn)
        return df["singer_name"].tolist() if not df.empty else [DEFAULT_SINGER_NAME]
    except Exception:
        return [DEFAULT_SINGER_NAME]
    finally:
        safe_close(conn)

@st.cache_data(ttl=300)
def get_songs_by_singer_cached(singer_name: str):
    """按艺人获取歌曲"""
    conn = get_mysql_connection()
    if not conn:
        return [DEFAULT_SONG_NAME]
    try:
        sql = """
        SELECT s.song_name
        FROM t_song s
        JOIN t_singer si ON s.singer_id = si.singer_id
        WHERE si.singer_name = %s
        ORDER BY s.song_id
        """
        df = pd.read_sql(sql, conn, params=[singer_name])
        return df["song_name"].tolist() if not df.empty else [DEFAULT_SONG_NAME]
    except Exception:
        return [DEFAULT_SONG_NAME]
    finally:
        safe_close(conn)

@st.cache_data(ttl=180)
def get_play_data_cached(singer_name, song_name, start_date=None, end_date=None):
    """获取播放量数据（过滤total全平台汇总）"""
    conn = get_mysql_connection()
    if not conn:
        return pd.DataFrame()
    try:
        sql = """
        SELECT 
            s.song_name, si.singer_name, p.platform_name, p.platform_code,
            ps.stat_date, ps.play_count, ps.play_count_str
        FROM t_play_stat ps
        JOIN t_song s ON ps.song_id = s.song_id
        JOIN t_singer si ON s.singer_id = si.singer_id
        JOIN t_platform p ON ps.platform_id = p.platform_id
        WHERE si.singer_name = %s AND s.song_name = %s
          AND p.platform_code <> 'total'
        """
        params = [singer_name, song_name]
        if start_date and end_date:
            sql += " AND ps.stat_date BETWEEN %s AND %s"
            params.extend([start_date, end_date])
        sql += " ORDER BY ps.stat_date, p.platform_name"

        df = pd.read_sql(sql, conn, params=params)
        if not df.empty:
            df["stat_date"] = pd.to_datetime(df["stat_date"])
        return df
    except Exception:
        return pd.DataFrame()
    finally:
        safe_close(conn)

@st.cache_data(ttl=180)
def get_interaction_data_cached(singer_name, song_name, start_date=None, end_date=None):
    """获取互动量数据（评论/收藏/转发，过滤无效数据）"""
    conn = get_mysql_connection()
    if not conn:
        return pd.DataFrame()
    try:
        sql = """
        SELECT 
            s.song_name, si.singer_name, p.platform_name, p.platform_code,
            ih.record_date, ih.comment_count, ih.collect_count, ih.share_count
        FROM t_song_interaction_history ih
        JOIN t_song s ON ih.song_id = s.song_id
        JOIN t_singer si ON s.singer_id = si.singer_id
        JOIN t_platform p ON ih.platform_id = p.platform_id
        WHERE si.singer_name = %s AND s.song_name = %s
          AND p.platform_code <> 'total'
        """
        params = [singer_name, song_name]
        if start_date and end_date:
            sql += " AND ih.record_date BETWEEN %s AND %s"
            params.extend([start_date, end_date])
        sql += " ORDER BY ih.record_date, p.platform_name"

        df = pd.read_sql(sql, conn, params=params)
        if not df.empty:
            df["record_date"] = pd.to_datetime(df["record_date"])
            df[["comment_count", "collect_count", "share_count"]] = df[["comment_count", "collect_count", "share_count"]].fillna(0).astype(int)
        return df
    except Exception as e:
        st.warning(f"⚠️ 获取互动量数据失败：{e}")
        return pd.DataFrame()
    finally:
        safe_close(conn)

@st.cache_data(ttl=60)
def get_all_interaction_data_cached(singer_name, song_name):
    """获取全量互动量数据（无日期筛选，专用于提取各平台最新数据）"""
    return get_interaction_data_cached(singer_name, song_name, None, None)

@st.cache_data(ttl=180)
def get_events_for_song_cached(song_name: str, start_date_str: str, end_date_str: str):
    """获取歌曲关联事件（含开始/结束时间）"""
    conn = get_mysql_connection()
    if not conn:
        return pd.DataFrame()

    try:
        # 获取song_id
        song_id_df = pd.read_sql("SELECT song_id FROM t_song WHERE song_name=%s LIMIT 1", conn, params=[song_name])
        if song_id_df.empty:
            return pd.DataFrame()
        song_id = int(song_id_df.iloc[0]["song_id"])

        # 查询事件
        sql = """
        SELECT 
            e.event_id, e.event_type, e.event_title, e.event_desc,
            e.event_start_time, e.event_end_time
        FROM t_event e
        JOIN t_event_song es ON e.event_id = es.event_id
        WHERE es.song_id = %s
          AND (
              (e.event_end_time IS NOT NULL AND e.event_start_time <= %s AND e.event_end_time >= %s)
              OR
              (e.event_end_time IS NULL AND e.event_start_time BETWEEN %s AND %s)
          )
        ORDER BY e.event_start_time
        """
        params = [song_id, end_date_str, start_date_str, start_date_str, end_date_str]
        
        df = pd.read_sql(sql, conn, params=params)
        if not df.empty:
            df["event_start_time"] = pd.to_datetime(df["event_start_time"], errors="coerce")
            df["event_end_time"] = pd.to_datetime(df["event_end_time"], errors="coerce")
            df["event_end_time"] = df["event_end_time"].fillna(df["event_start_time"])
            df["event_color"] = df["event_id"].apply(get_event_color)
        return df
    except Exception as e:
        st.error(f"获取事件失败：{e}")
        return pd.DataFrame()
    finally:
        safe_close(conn)

@st.cache_data(ttl=300)
def get_comment_platforms_cached():
    """获取评论平台列表"""
    conn = get_mysql_connection()
    if not conn:
        return ["QQ音乐"]
    try:
        df = pd.read_sql(
            "SELECT platform_name FROM t_platform WHERE platform_code <> 'total' ORDER BY platform_id",
            conn
        )
        return df["platform_name"].tolist() if not df.empty else ["QQ音乐"]
    except Exception:
        return ["QQ音乐"]
    finally:
        safe_close(conn)

@st.cache_data(ttl=180)
def get_comment_data_cached(
    song_name: str, platform_name: str | None,
    start_dt: datetime | None, end_dt: datetime | None,
    include_unknown_time: bool,
):
    """获取评论数据（支持平台/时间筛选）"""
    conn = get_mysql_connection()
    if not conn:
        return pd.DataFrame()
    try:
        song_id_df = pd.read_sql("SELECT song_id FROM t_song WHERE song_name=%s LIMIT 1", conn, params=[song_name])
        if song_id_df.empty:
            return pd.DataFrame()
        song_id = int(song_id_df.iloc[0]["song_id"])

        sql = """
        SELECT c.comment_content, c.publish_time, c.user_nickname, c.comment_ip, p.platform_name
        FROM t_comment c
        JOIN t_platform p ON c.platform_id = p.platform_id
        WHERE c.song_id = %s AND c.is_deleted = 0
          AND p.platform_code <> 'total'
        """
        params: list = [song_id]

        if platform_name:
            sql += " AND p.platform_name = %s"
            params.append(platform_name)
        if start_dt and end_dt:
            if include_unknown_time:
                sql += " AND ((c.publish_time BETWEEN %s AND %s) OR c.publish_time IS NULL)"
                params.extend([start_dt, end_dt])
            else:
                sql += " AND (c.publish_time BETWEEN %s AND %s)"
                params.extend([start_dt, end_dt])
        else:
            if not include_unknown_time:
                sql += " AND c.publish_time IS NOT NULL"

        sql += " ORDER BY c.publish_time DESC"
        df = pd.read_sql(sql, conn, params=params)
        df["publish_time"] = pd.to_datetime(df["publish_time"], errors="coerce")
        return df
    except Exception:
        return pd.DataFrame()
    finally:
        safe_close(conn)

@st.cache_data(ttl=180)
def get_comment_platform_distribution_cached(song_name: str):
    """按评论明细表统计各平台评论总数，用于评论占比展示。"""
    conn = get_mysql_connection()
    if not conn:
        return pd.DataFrame()
    try:
        song_id_df = pd.read_sql("SELECT song_id FROM t_song WHERE song_name=%s LIMIT 1", conn, params=[song_name])
        if song_id_df.empty:
            return pd.DataFrame()
        song_id = int(song_id_df.iloc[0]["song_id"])

        sql = """
        SELECT p.platform_name, p.platform_code, COUNT(*) AS comment_count
        FROM t_comment c
        JOIN t_platform p ON c.platform_id = p.platform_id
        WHERE c.song_id = %s
          AND c.is_deleted = 0
          AND p.platform_code <> 'total'
        GROUP BY p.platform_name, p.platform_code
        ORDER BY p.platform_id
        """
        return pd.read_sql(sql, conn, params=[song_id])
    except Exception:
        return pd.DataFrame()
    finally:
        safe_close(conn)

# ===================== 4) 词云/词频分析函数 =====================
def tokenize_and_count(comment_text: str, topn: int = 10):
    """分词并统计词频"""
    if not comment_text or not comment_text.strip():
        return [], []

    seg_list = jieba.cut(comment_text, cut_all=False)
    tokens = []
    for w in seg_list:
        w = w.strip()
        if not w or w in STOP_WORDS or len(w) <= 1 or re.fullmatch(r"[\d\W_]+", w):
            continue
        tokens.append(w)

    counter = Counter(tokens)
    return counter.most_common(topn), tokens

def generate_wordcloud_from_tokens(tokens: list[str]):
    """生成词云图片"""
    if not tokens:
        return None
    seg_text = " ".join(tokens)

    try:
        wc = WordCloud(**WC_CONFIG)
        wc.generate(seg_text)

        img_buffer = io.BytesIO()
        plt.figure(figsize=(7.2, 4.8))
        plt.imshow(wc, interpolation="bilinear")
        plt.axis("off")
        plt.tight_layout(pad=0)
        plt.savefig(img_buffer, format="PNG", dpi=140, bbox_inches="tight")
        plt.close()

        img_buffer.seek(0)
        return Image.open(img_buffer)
    except Exception as e:
        st.error(f"❌ 词云生成失败：{e}")
        return None

# ===================== 5) Plotly事件标注函数 =====================
def generate_event_date_series(df_events: pd.DataFrame):
    """生成事件日期序列（用于折线图标注）"""
    event_dates = []
    if df_events.empty:
        return event_dates
    
    for _, event in df_events.iterrows():
        etype = str(event.get("event_type", ""))
        title = str(event.get("event_title", ""))
        desc = str(event.get("event_desc", ""))
        event_id = event.get("event_id")
        event_color = event.get("event_color", "#ff7f0e")
        start_t = event.get("event_start_time")
        end_t = event.get("event_end_time")
        
        start_str = start_t.strftime("%Y-%m-%d %H:%M") if pd.notna(start_t) else "未知"
        end_str = end_t.strftime("%Y-%m-%d %H:%M") if pd.notna(end_t) else "未知"
        hover_text = (
            f"<b>📌 事件</b><br>ID：{event_id}<br>类型：{etype}<br>标题：{title}<br>"
            f"时间范围：{start_str} ~ {end_str}<br>说明：{desc}"
        )
        
        if pd.notna(start_t) and pd.notna(end_t):
            current_date = start_t.date()
            end_date = end_t.date()
            while current_date <= end_date:
                event_dates.append((pd.to_datetime(current_date), hover_text, event_id, title, event_color))
                current_date += timedelta(days=1)
    return event_dates

def add_events_to_total_trend(fig: go.Figure, daily_total: pd.DataFrame, df_events: pd.DataFrame, selected_event_ids: list):
    """总播放量趋势图添加事件标注"""
    if df_events.empty or daily_total.empty:
        return fig

    filtered_events = df_events[df_events['event_id'].isin(selected_event_ids)] if selected_event_ids else df_events.copy()
    if filtered_events.empty:
        return fig

    event_dates = generate_event_date_series(filtered_events)
    play_count_map = dict(zip(pd.to_datetime(daily_total["stat_date"]), daily_total["play_count"]))

    event_groups = {}
    for event_date, hover_text, event_id, event_title, event_color in event_dates:
        if event_date in play_count_map:
            if event_id not in event_groups:
                event_groups[event_id] = {'x': [], 'y': [], 'hover_texts': [], 'title': event_title, 'color': event_color}
            event_groups[event_id]['x'].append(event_date)
            event_groups[event_id]['y'].append(play_count_map[event_date])
            event_groups[event_id]['hover_texts'].append(hover_text)

    for event_id, group in event_groups.items():
        fig.add_trace(
            go.Scatter(
                x=group['x'], y=group['y'], mode="markers+text",
                name=f"事件: {group['title'][:20]}",
                marker=dict(size=14, symbol="diamond", color=group['color'], line=dict(width=2, color="white"), opacity=0.9),
                text=[f"●"] * len(group['x']), textposition="top right",
                hovertext=group['hover_texts'], hovertemplate="%{hovertext}<extra></extra>",
                showlegend=True
            )
        )
    return fig

def add_events_to_platform_trend(fig: go.Figure, df_play: pd.DataFrame, df_events: pd.DataFrame, selected_event_ids: list):
    """各平台播放量趋势图添加事件标注"""
    if df_events.empty or df_play.empty:
        return fig

    filtered_events = df_events[df_events['event_id'].isin(selected_event_ids)] if selected_event_ids else df_events.copy()
    if filtered_events.empty:
        return fig

    event_dates = generate_event_date_series(filtered_events)
    play_count_map = {}
    df_play2 = df_play.copy()
    df_play2["stat_date"] = pd.to_datetime(df_play2["stat_date"])
    for _, row in df_play2.iterrows():
        key = (row["stat_date"], row["platform_name"])
        play_count_map[key] = row["play_count"]

    event_groups = {}
    for event_date, hover_text, event_id, event_title, event_color in event_dates:
        platforms = df_play2[df_play2["stat_date"] == event_date]["platform_name"].unique()
        for platform in platforms:
            key = (event_date, platform)
            if key in play_count_map:
                if event_id not in event_groups:
                    event_groups[event_id] = {'x': [], 'y': [], 'hover_texts': [], 'title': event_title, 'color': event_color}
                event_groups[event_id]['x'].append(event_date)
                event_groups[event_id]['y'].append(play_count_map[key])
                event_groups[event_id]['hover_texts'].append(f"{hover_text}<br>平台：{platform}")

    for event_id, group in event_groups.items():
        if group['x']:
            fig.add_trace(
                go.Scatter(
                    x=group['x'], y=group['y'], mode="markers",
                    name=f"事件: {group['title'][:20]}",
                    marker=dict(size=12, symbol="diamond", color=group['color'], line=dict(width=2, color="white"), opacity=0.9),
                    hovertext=group['hover_texts'], hovertemplate="%{hovertext}<extra></extra>",
                    showlegend=True
                )
            )
    return fig

# ===================== 6) HTML报告导出函数 =====================
def safe_filename(name: str, max_len=120):
    """安全生成文件名（过滤特殊字符）"""
    name = name.replace("《", "").replace("》", "")
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", "_", name).strip("_")
    return name[:max_len] if len(name) > max_len else name

def generate_html_report(df, singer_name, song_name, start_date, end_date):
    """生成播放量HTML报告"""
    total_play = int(df["play_count"].sum())
    avg_daily_play = float(df.groupby("stat_date")["play_count"].sum().mean())
    daily_play = df.groupby("stat_date")["play_count"].sum()
    max_daily_play = int(daily_play.max())
    max_daily_date = daily_play.idxmax().strftime("%Y-%m-%d")
    platform_count = int(df["platform_name"].nunique())

    # 生成图表
    daily_total = df.groupby("stat_date")["play_count"].sum().reset_index()
    fig_trend = px.line(daily_total, x="stat_date", y="play_count", title=f"{singer_name} - {song_name} 每日总播放量趋势", template="plotly_white", markers=True)
    trend_html = plot(fig_trend, output_type="div", include_plotlyjs="cdn")

    platform_total = df.groupby("platform_name")["play_count"].sum().reset_index()
    fig_pie = px.pie(platform_total, values="play_count", names="platform_name", title=f"{singer_name} - {song_name} 平台播放量占比", template="plotly_white", hole=0.3)
    pie_html = plot(fig_pie, output_type="div", include_plotlyjs="cdn")

    fig_multi = px.line(df, x="stat_date", y="play_count", color="platform_name", title=f"{singer_name} - {song_name} 各平台每日播放量趋势", template="plotly_white", markers=True)
    multi_html = plot(fig_multi, output_type="div", include_plotlyjs="cdn")

    # 格式化数据
    df_display = df.copy()
    df_display["play_count"] = df_display["play_count"].apply(lambda x: f"{int(x):,}")
    df_display["stat_date"] = df_display["stat_date"].dt.strftime("%Y-%m-%d")
    table_html = df_display.to_html(index=False, classes="table table-striped table-hover", escape=False)

    file_name = safe_filename(f"{singer_name}_{song_name}_{start_date}_to_{end_date}.html")
    html_template = f"""
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{singer_name} - {song_name} 播放量报告</title>
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
        <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
        <style>
            body {{ padding: 20px; font-family: Arial, sans-serif; }}
            .metric-card {{ background: #f8f9fa; padding: 20px; border-radius: 8px; margin-bottom: 20px; }}
            .chart-container {{ margin-bottom: 40px; }}
            h1 {{ color: #333; margin-bottom: 30px; }}
            h2 {{ color: #555; margin-top: 40px; margin-bottom: 20px; }}
            h3 {{ color: #666; margin-top: 20px; margin-bottom: 10px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1 class="text-center">🎵 {singer_name} - {song_name} 播放量报告</h1>
            <p class="text-center text-muted">统计时间：{start_date} 至 {end_date}</p>
            <h2>📊 数据概览</h2>
            <div class="row">
                <div class="col-md-3"><div class="metric-card"><h5>总播放量</h5><p class="h3">{total_play:,}</p></div></div>
                <div class="col-md-3"><div class="metric-card"><h5>日均播放量</h5><p class="h3">{avg_daily_play:.0f}</p></div></div>
                <div class="col-md-3"><div class="metric-card"><h5>最高单日播放量</h5><p class="h3">{max_daily_play:,}</p><small>{max_daily_date}</small></div></div>
                <div class="col-md-3"><div class="metric-card"><h5>统计平台数</h5><p class="h3">{platform_count}</p></div></div>
            </div>
            <h2>📈 可视化分析</h2>
            <h3>每日总播放量趋势</h3><div class="chart-container">{trend_html}</div>
            <div class="row">
                <div class="col-md-12"><h3>平台占比</h3><div class="chart-container">{pie_html}</div></div>
            </div>
            <h3>各平台每日趋势</h3><div class="chart-container">{multi_html}</div>
            <h2>🗂️ 原始数据</h2>
            <div class="table-responsive">{table_html}</div>
            <footer class="text-center text-muted mt-5">报告生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</footer>
        </div>
    </body>
    </html>
    """

    with open(file_name, "w", encoding="utf-8") as f:
        f.write(html_template)
    return os.path.abspath(file_name)

# ===================== 7) 主页面（核心：各平台取自身最新数据） =====================
def main():
    st.set_page_config(page_title="音乐播放量Dashboard", page_icon="🎵", layout="wide")
    st.title("🎵 歌曲播放数据 & 互动量分析 & 评论分析")
    st.divider()

    # ===== 侧边栏：筛选条件+工具 =====
    with st.sidebar:
        st.subheader("工具")
        if st.button("🔄 清缓存并重跑"):
            st.cache_data.clear()
            st.rerun()

        st.divider()
        st.subheader("筛选条件（全局通用）")
        # 艺人/歌曲选择
        singers = get_all_singers_cached()
        singer_idx = singers.index(DEFAULT_SINGER_NAME) if DEFAULT_SINGER_NAME in singers else 0
        selected_singer = st.selectbox("选择艺人", singers, index=singer_idx, key="singer_select")

        songs = get_songs_by_singer_cached(selected_singer)
        song_idx = songs.index(DEFAULT_SONG_NAME) if DEFAULT_SONG_NAME in songs else 0
        selected_song = st.selectbox("选择歌曲", songs, index=song_idx, key="song_select")

        # 日期选择
        df_temp = get_play_data_cached(selected_singer, selected_song, None, None)
        song_release_date = get_song_release_date_cached(selected_song)
        
        if not df_temp.empty:
            min_date = df_temp["stat_date"].min().date()
            max_date = df_temp["stat_date"].max().date()
            default_start_date = song_release_date if (song_release_date and min_date <= song_release_date <= max_date) else min_date
        else:
            default_start_date = song_release_date if song_release_date else (datetime.now().date() - timedelta(days=30))
            max_date = datetime.now().date()

        start_date = st.date_input("开始日期", default_start_date, min_value=default_start_date, max_value=max_date)
        end_date = st.date_input("结束日期", max_date, min_value=default_start_date, max_value=max_date)
        if start_date > end_date:
            start_date, end_date = end_date, start_date
            st.caption("已自动交换开始/结束日期")

        # 事件筛选
        st.divider()
        st.subheader("事件筛选")
        df_events_sidebar = get_events_for_song_cached(selected_song, start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))
        if not df_events_sidebar.empty:
            event_options = [f"[{row['event_id']}] {row['event_title'][:30]}" for _, row in df_events_sidebar.iterrows()]
            event_id_map = {f"[{row['event_id']}] {row['event_title'][:30]}": row['event_id'] for _, row in df_events_sidebar.iterrows()}
            
            col1, col2 = st.columns(2)
            with col1:
                if st.button("全选事件"):
                    st.session_state['selected_events'] = event_options
            with col2:
                if st.button("取消全选"):
                    st.session_state['selected_events'] = []
            
            if 'selected_events' not in st.session_state:
                st.session_state['selected_events'] = event_options
            else:
                valid_selected = [x for x in st.session_state['selected_events'] if x in event_options]
                st.session_state['selected_events'] = valid_selected if valid_selected else event_options
            
            selected_event_labels = st.multiselect('选择要显示的事件', event_options, default=st.session_state['selected_events'], key="event_multiselect")
            selected_event_ids = [event_id_map[label] for label in selected_event_labels]
            st.session_state['selected_events'] = selected_event_labels
        else:
            st.caption("⚠️ 暂无可用事件")
            selected_event_ids = []
            if 'selected_events' in st.session_state:
                del st.session_state['selected_events']

    # ===== 加载核心数据 =====
    df_play = get_play_data_cached(selected_singer, selected_song, start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))
    df_events = get_events_for_song_cached(selected_song, start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))
    df_interaction = get_interaction_data_cached(selected_singer, selected_song, start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))

    # ===== 播放量数据分析 =====
    st.subheader("📊 播放量数据概览")
    if df_play.empty:
        st.warning("⚠️ 暂无符合条件的播放量数据！")
    else:
        col1, col2, col3, col4 = st.columns(4)
        total_play = int(df_play["play_count"].sum())
        avg_daily_play = float(df_play.groupby("stat_date")["play_count"].sum().mean())
        daily_play = df_play.groupby("stat_date")["play_count"].sum()
        max_daily_play = int(daily_play.max())
        max_daily_date = daily_play.idxmax().strftime("%Y-%m-%d")
        platform_count = int(df_play["platform_name"].nunique())

        col1.metric("总播放量", f"{total_play:,}")
        col2.metric("日均播放量", f"{avg_daily_play:.0f}")
        col3.metric("最高单日播放量", f"{max_daily_play:,}", f"日期：{max_daily_date}")
        col4.metric("统计平台数", platform_count)

        st.divider()
        st.subheader("📈 播放量可视化分析（含事件标注）")
        # 事件列表展示
        if not df_events.empty:
            with st.expander("📌 本时间范围内事件列表（含颜色）", expanded=False):
                tmp = df_events[["event_id", "event_start_time", "event_end_time", "event_type", "event_title", "event_desc"]].copy()
                tmp["event_start_time"] = tmp["event_start_time"].dt.strftime("%Y-%m-%d %H:%M")
                tmp["event_end_time"] = tmp["event_end_time"].dt.strftime("%Y-%m-%d %H:%M").fillna("无")
                tmp["颜色预览"] = df_events["event_color"].apply(lambda c: f"<span style='display:inline-block;width:20px;height:20px;background:{c};border:1px solid #ccc;'></span>")
                tmp.rename(columns={"event_id":"事件ID","event_start_time":"开始时间","event_end_time":"结束时间","event_type":"事件类型","event_title":"事件标题","event_desc":"事件描述"}, inplace=True)
                st.write(tmp.to_html(escape=False, index=False), unsafe_allow_html=True)
        else:
            st.caption("本时间范围内无事件数据（新增事件后请清缓存重跑）。")

        # 每日总播放量趋势
        daily_total = df_play.groupby("stat_date")["play_count"].sum().reset_index()
        fig_trend = px.line(daily_total, x="stat_date", y="play_count", title=f"{selected_singer} - {selected_song} 每日总播放量趋势", template="plotly_white", markers=True)
        fig_trend.update_layout(height=420, legend=dict(orientation="h", yanchor="bottom", y=1, xanchor="right", x=1))
        fig_trend = add_events_to_total_trend(fig_trend, daily_total, df_events, selected_event_ids)
        st.plotly_chart(fig_trend, use_container_width=True)

        # 平台播放量占比
        platform_total = df_play.groupby("platform_name")["play_count"].sum().reset_index()
        fig_pie = px.pie(platform_total, values="play_count", names="platform_name", title=f"{selected_singer} - {selected_song} 平台播放量占比", template="plotly_white", hole=0.3)
        st.plotly_chart(fig_pie, use_container_width=True)

        # 各平台每日播放量趋势
        fig_multi = px.line(df_play, x="stat_date", y="play_count", color="platform_name", title=f"{selected_singer} - {selected_song} 各平台每日播放量趋势", template="plotly_white", markers=True)
        fig_multi.update_layout(height=440, xaxis=dict(title_standoff=25), legend=dict(orientation="h", yanchor="bottom", y=1, xanchor="right", x=1, title=None), margin=dict(b=180))
        fig_multi = add_events_to_platform_trend(fig_multi, df_play, df_events, selected_event_ids)
        st.plotly_chart(fig_multi, use_container_width=True)

        # 播放量原始数据
        st.divider()
        st.subheader("🗂️ 播放量原始数据")
        df_display = df_play.copy()
        df_display["play_count"] = df_display["play_count"].apply(lambda x: f"{int(x):,}")
        st.dataframe(df_display, use_container_width=True)

    # ===== 互动量数据分析（核心：各平台取自身最新数据） =====
    st.divider()
    st.subheader("❤️ 歌曲互动量分析（评论/收藏/转发）")
    if df_interaction.empty:
        st.warning("⚠️ 暂无符合条件的互动量数据！")
    else:
        # 各平台最新数据（筛选范围内）
        latest_inter = df_interaction.sort_values(by=["platform_name", "record_date"], ascending=[True, False]).drop_duplicates(subset="platform_name", keep="first")
        # 核心指标
        col1, col2, col3, col4 = st.columns(4)
        df_comment_platform_total = get_comment_platform_distribution_cached(selected_song)
        total_comment = int(df_comment_platform_total["comment_count"].sum()) if not df_comment_platform_total.empty else int(latest_inter["comment_count"].sum())
        total_collect = int(latest_inter["collect_count"].sum())
        total_share = int(latest_inter["share_count"].sum())
        daily_inter = df_interaction.groupby("record_date").agg(comment_count=("comment_count", "sum"), collect_count=("collect_count", "sum"), share_count=("share_count", "sum")).reset_index().fillna(0)
        avg_daily_comment = float(daily_inter["comment_count"].mean()) if not daily_inter.empty else 0
        max_daily_comment = int(daily_inter["comment_count"].max()) if not daily_inter.empty else 0
        max_comment_date = daily_inter.loc[daily_inter["comment_count"].idxmax(), "record_date"].strftime("%Y-%m-%d") if not daily_inter.empty else "无"
        
        col1.metric("总评论数（各平台最新）", f"{total_comment:,}")
        col2.metric("总收藏数（各平台最新）", f"{total_collect:,}")
        col3.metric("总转发数（各平台最新）", f"{total_share:,}")
        col4.metric("最高单日评论", f"{max_daily_comment:,}", f"日期：{max_comment_date}")

        st.divider()
        st.subheader("📈 互动量可视化分析")

        # 1. 每日总互动量趋势（中文图例）
        st.write("### 每日总互动量趋势（评论/收藏/转发）")
        daily_inter_total = df_interaction.groupby("record_date").agg(
            comment_count=("comment_count", "sum"),
            collect_count=("collect_count", "sum"),
            share_count=("share_count", "sum")
        ).reset_index().fillna(0)
        daily_inter_total[["comment_count", "collect_count", "share_count"]] = daily_inter_total[["comment_count", "collect_count", "share_count"]].astype(int)
        if not daily_inter_total.empty:
            fig_inter_trend = px.line(
                daily_inter_total, x="record_date", y=["comment_count", "collect_count", "share_count"],
                title=f"{selected_singer} - {selected_song} 每日互动量趋势", template="plotly_white", markers=True,
                labels={
                    "value": "数量", "variable": "互动类型",
                    "comment_count": "评论数", "collect_count": "收藏数", "share_count": "转发数"
                }
            )
            fig_inter_trend.update_layout(height=420, legend=dict(orientation="h", yanchor="bottom", y=-0.3, xanchor="right", x=1, title=None), xaxis=dict(title="记录日期", title_standoff=20))
            st.plotly_chart(fig_inter_trend, use_container_width=True)
        else:
            st.info("ℹ️ 暂无有效互动量趋势数据")

        # 2. 各平台互动量占比（核心修改：每个平台取自身最新数据）
        st.write("### 各平台互动量占比（各平台自身最新数据）")
        st.cache_data.clear()
        df_all_interaction = get_all_interaction_data_cached(selected_singer, selected_song)
        if df_all_interaction.empty:
            st.warning("⚠️ 数据库中暂无该歌曲的互动量数据！")
        else:
            # 按平台分组，取每个平台自身最新数据
            df_latest_per_platform = df_all_interaction.sort_values(
                by=['platform_name', 'record_date'], ascending=[True, False]
            ).groupby('platform_name').head(1).reset_index(drop=True)
            df_latest_per_platform['latest_date'] = df_latest_per_platform['record_date'].dt.strftime("%Y-%m-%d")

            # 各平台最新数据详情
            with st.expander("🔍 各平台最新数据详情", expanded=False):
                st.dataframe(
                    df_latest_per_platform[['platform_name', 'latest_date', 'comment_count', 'collect_count', 'share_count']],
                    use_container_width=True,
                    column_config={
                        "platform_name": "平台名称", "latest_date": "最新数据日期",
                        "comment_count": "评论数", "collect_count": "收藏数", "share_count": "转发数"
                    }
                )

            # 分维度占比饼图
            tab1, tab2, tab3 = st.tabs(["评论数占比", "收藏数占比", "转发数占比"])
            # 评论数占比
            platform_comment = df_comment_platform_total[df_comment_platform_total['comment_count'] > 0].copy()
            if not platform_comment.empty:
                fig_comment_pie = px.pie(
                    platform_comment, values="comment_count", names="platform_name",
                    title="各平台评论数占比（按评论明细总数）", template="plotly_white", hole=0.3
                )
                fig_comment_pie.update_traces(hovertemplate="<b>%{label}</b><br>评论数：%{value}<extra></extra>")
                with tab1:
                    st.plotly_chart(fig_comment_pie, use_container_width=True)
            else:
                with tab1:
                    st.info("ℹ️ 暂无有效评论数数据")
            # 收藏数占比
            platform_collect = df_latest_per_platform[['platform_name', 'collect_count', 'latest_date']][df_latest_per_platform['collect_count'] > 0].copy()
            if not platform_collect.empty:
                fig_collect_pie = px.pie(
                    platform_collect, values="collect_count", names="platform_name",
                    title="各平台收藏数占比（自身最新数据）", template="plotly_white", hole=0.3,
                    hover_data={"latest_date": True}
                )
                fig_collect_pie.update_traces(hovertemplate="<b>%{label}</b><br>最新日期：%{customdata[0]}<br>收藏数：%{value}<extra></extra>")
                with tab2:
                    st.plotly_chart(fig_collect_pie, use_container_width=True)
            else:
                with tab2:
                    st.info("ℹ️ 暂无有效收藏数数据")
            # 转发数占比
            platform_share = df_latest_per_platform[['platform_name', 'share_count', 'latest_date']][df_latest_per_platform['share_count'] > 0].copy()
            if not platform_share.empty:
                fig_share_pie = px.pie(
                    platform_share, values="share_count", names="platform_name",
                    title="各平台转发数占比（自身最新数据）", template="plotly_white", hole=0.3,
                    hover_data={"latest_date": True}
                )
                fig_share_pie.update_traces(hovertemplate="<b>%{label}</b><br>最新日期：%{customdata[0]}<br>转发数：%{value}<extra></extra>")
                with tab3:
                    st.plotly_chart(fig_share_pie, use_container_width=True)
            else:
                with tab3:
                    st.info("ℹ️ 暂无有效转发数数据")

        # 3. 各平台每日互动量趋势（中文图例）
        st.write("### 各平台每日互动量趋势")
        if not df_interaction.empty:
            fig_inter_platform = px.line(
                df_interaction, x="record_date", y=["comment_count", "collect_count", "share_count"], color="platform_name",
                title=f"{selected_singer} - {selected_song} 各平台每日互动量趋势", template="plotly_white", markers=True,
                labels={
                    "value": "数量", "variable": "互动类型", "platform_name": "平台名称",
                    "comment_count": "评论数", "collect_count": "收藏数", "share_count": "转发数"
                }
            )
            fig_inter_platform.update_layout(height=440, legend=dict(orientation="h", yanchor="bottom", y=-0.5, xanchor="right", x=1, title=None), xaxis=dict(title="记录日期", title_standoff=25), margin=dict(b=180))
            st.plotly_chart(fig_inter_platform, use_container_width=True)
        else:
            st.info("ℹ️ 暂无有效各平台互动量趋势数据")

        # 互动量原始数据
        st.divider()
        st.subheader("🗂️ 互动量原始数据")
        df_inter_display = df_interaction.copy()
        for col in ["comment_count", "collect_count", "share_count"]:
            df_inter_display[col] = df_inter_display[col].apply(lambda x: f"{int(x):,}")
        st.dataframe(df_inter_display, use_container_width=True)

    # ===== 评论词云分析 =====
    st.divider()
    st.subheader("💬 评论词云分析")
    st.divider()
    wc_left, wc_right = st.columns([6, 4], vertical_alignment="top")

    with wc_right:
        st.markdown("### 🎛️ 词云筛选")
        platforms = get_comment_platforms_cached()
        platform_options = ["总评论（全平台）"] + platforms
        selected_platform_ui = st.selectbox("平台", platform_options, index=0, key="wc_platform_select")
        use_time_filter = st.toggle("启用时间筛选（按发布时间）", value=False)
        include_unknown_time = st.toggle("包含时间未知评论", value=True)

        wc_end = datetime.now().date()
        wc_start = song_release_date if song_release_date else (wc_end - timedelta(days=7))
        if use_time_filter:
            wc_start_date = st.date_input("开始日期", wc_start, key="wc_start_date")
            wc_end_date = st.date_input("结束日期", wc_end, key="wc_end_date")
            if wc_start_date > wc_end_date:
                wc_start_date, wc_end_date = wc_end_date, wc_start_date
                st.caption("已自动交换开始/结束日期")
            start_dt = datetime.combine(wc_start_date, datetime.min.time())
            end_dt = datetime.combine(wc_end_date, datetime.max.time().replace(microsecond=0))
        else:
            start_dt = None
            end_dt = None

    platform_name = None if selected_platform_ui == "总评论（全平台）" else selected_platform_ui
    df_comment = get_comment_data_cached(selected_song, platform_name, start_dt, end_dt, include_unknown_time)
    if df_comment.empty:
        with wc_left:
            st.warning("⚠️ 当前筛选条件下没有评论数据。")
    else:
        comment_text = " ".join(df_comment["comment_content"].dropna().astype(str).tolist())
        top_words, tokens = tokenize_and_count(comment_text, topn=10)
        wc_image = generate_wordcloud_from_tokens(tokens)

        with wc_left:
            title = f"{selected_song} - {selected_platform_ui}"
            if use_time_filter and start_dt and end_dt:
                title += f"（{start_dt.date()} ~ {end_dt.date()}）"
            st.write(f"### {title}")
            if wc_image:
                st.image(wc_image, width=520)
            else:
                st.info("有效词不足，无法生成词云。")

        with wc_right:
            st.markdown("### 🔥 词频 Top10（点击词查看相关评论）")
            if "selected_word" not in st.session_state:
                st.session_state["selected_word"] = None
            if not top_words:
                st.caption("没有统计到有效词频。")
            else:
                for w, c in top_words:
                    c1, c2 = st.columns([3, 2])
                    with c1:
                        if st.button(w, key=f"word_btn_{w}"):
                            st.session_state['selected_word'] = w
                    with c2:
                        st.write(f"{c} 次")

        # 查看选中词的相关评论
        chosen = st.session_state.get("selected_word")
        if chosen:
            st.divider()
            st.markdown(f"### 🧩 包含「{chosen}」的评论")
            mask = df_comment["comment_content"].fillna("").astype(str).str.contains(re.escape(chosen))
            matched = df_comment.loc[mask, ["platform_name", "user_nickname", "comment_content", "publish_time", "comment_ip"]].copy()
            kw = st.text_input("在匹配评论里进一步筛选（可选）", value="", key="sub_filter_kw")
            if kw.strip():
                mm = matched["comment_content"].fillna("").astype(str).str.contains(re.escape(kw.strip()))
                matched = matched.loc[mm].copy()
            with st.expander(f"展开查看（{len(matched)} 条）", expanded=True):
                matched.rename(columns={"platform_name":"平台","user_nickname":"用户昵称","comment_content":"评论内容","publish_time":"发布时间","comment_ip":"IP/省份"}, inplace=True)
                matched["发布时间"] = matched["发布时间"].apply(lambda x: x.strftime("%Y-%m-%d %H:%M") if pd.notna(x) else "未知")
                matched["IP/省份"] = matched["IP/省份"].fillna("未知")
                matched["用户昵称"] = matched["用户昵称"].fillna("匿名用户")
                st.dataframe(matched.head(200), use_container_width=True)

    # ===== 报告导出 =====
    st.divider()
    st.subheader("📤 报告导出")
    if st.button("生成当前页面的HTML报告", type="primary") and (not df_play.empty):
        html_path = generate_html_report(df_play, selected_singer, selected_song, start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))
        st.success(f"✅ HTML 报告已生成：{html_path}")
        with open(html_path, "rb") as f:
            st.download_button(label="下载HTML报告", data=f, file_name=os.path.basename(html_path), mime="text/html")

    # 中文字体提示
    with st.sidebar:
        st.divider()
        if not CHINESE_FONT_PATH:
            st.caption("⚠️ 未检测到中文字体，词云可能显示方块！可在根目录放置 SimHei.ttf")

if __name__ == "__main__":
    main()
