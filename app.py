import os
import json
import time
import tempfile
from datetime import date, datetime
from google import genai
from google.genai import types
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv
import gspread
import streamlit as st

# =====================
# ページ設定
# =====================
st.set_page_config(
    page_title="棚パシャ",
    page_icon="📷",
    layout="centered",
)

# =====================
# カスタムCSS + Material Icons
# =====================
st.markdown("""
<link href="https://fonts.googleapis.com/icon?family=Material+Icons+Outlined" rel="stylesheet">
<style>
/* ---- ベース ---- */
[data-testid="stAppViewContainer"] { background-color: #f8fafc; }
.block-container { padding-top: 1.5rem; padding-bottom: 3rem; max-width: 740px; }

/* ---- サイドバー ---- */
[data-testid="stSidebar"] { background-color: #0d1f3c !important; }
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] span,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] div { color: rgba(255,255,255,0.85) !important; }

/* ---- ナビボタン ---- */
[data-testid="stSidebar"] div[data-testid="stButton"] > button {
    background: transparent !important;
    border: none !important;
    color: rgba(255,255,255,0.7) !important;
    text-align: left !important;
    font-size: 15px !important;
    font-weight: 500 !important;
    border-radius: 10px !important;
    padding: 10px 14px !important;
    transition: background 0.15s !important;
}
[data-testid="stSidebar"] div[data-testid="stButton"] > button:hover {
    background: rgba(255,255,255,0.12) !important;
    color: white !important;
}

/* ---- CTAボタン ---- */
div[data-testid="stButton"] > button[kind="primary"] {
    background: linear-gradient(135deg, #1565C0 0%, #2196F3 100%) !important;
    color: white !important;
    border: none !important;
    border-radius: 14px !important;
    font-size: 16px !important;
    font-weight: 700 !important;
    padding: 14px 0 !important;
    letter-spacing: 0.4px !important;
    box-shadow: 0 4px 14px rgba(21,101,192,0.35) !important;
}
div[data-testid="stButton"] > button[kind="primary"]:hover {
    box-shadow: 0 6px 20px rgba(21,101,192,0.45) !important;
    transform: translateY(-1px) !important;
}

/* ---- インフォカード ---- */
.info-card {
    background: white;
    border: 1px solid #e2e8f0;
    border-radius: 16px;
    padding: 20px 24px;
    margin: 12px 0;
    box-shadow: 0 1px 6px rgba(0,0,0,0.04);
}

/* ---- ステップカード ---- */
.step-grid {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    margin: 10px 0;
}
.step-card {
    flex: 1;
    min-width: 130px;
    background: #f1f5f9;
    border-radius: 12px;
    padding: 16px 12px;
    text-align: center;
}
.step-icon {
    font-family: 'Material Icons Outlined';
    font-size: 28px;
    color: #1565C0;
    display: block;
}
.step-num { font-size: 11px; color: #94a3b8; margin: 4px 0; }
.step-label { font-size: 13px; font-weight: 600; color: #1e293b; line-height: 1.4; }

/* ---- セクションヘッダー ---- */
.section-title {
    font-size: 26px;
    font-weight: 800;
    color: #0d1f3c;
    margin-bottom: 4px;
    line-height: 1.2;
}
.section-sub {
    font-size: 14px;
    color: #64748b;
    margin-bottom: 20px;
}

/* ---- フォームカード ---- */
.form-card {
    background: white;
    border: 1px solid #e2e8f0;
    border-radius: 16px;
    padding: 20px 24px 8px 24px;
    margin: 12px 0 16px 0;
    box-shadow: 0 1px 6px rgba(0,0,0,0.04);
}
.form-card-title {
    font-size: 15px;
    font-weight: 700;
    color: #0d1f3c;
    margin-bottom: 2px;
}
.form-card-sub {
    font-size: 13px;
    color: #94a3b8;
    margin-bottom: 12px;
}

/* ---- 履歴バッジ ---- */
.badge {
    display: inline-block;
    background: #e3f2fd;
    color: #1565C0;
    border-radius: 6px;
    padding: 2px 10px;
    font-size: 12px;
    font-weight: 600;
}
</style>
""", unsafe_allow_html=True)

# =====================
# 設定 & 初期化
# =====================
load_dotenv()
CREDENTIALS_PATH = os.environ.get("CREDENTIALS_PATH", "credentials.json")
TODAY = date.today().strftime("%Y/%m/%d")
LOGO_PATH = "tana_pasha_logo.png"

gemini_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

# セッション状態
defaults = {
    "page": "scan",
    "scan_history": [],
    "sheet_url": "",
    "photo_result": None,
    "video_result": None,
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v


# =====================
# ヘルパー関数
# =====================
def build_prompt(today, media_type="動画"):
    return f"""
このスーパーの棚を撮影した{media_type}から、商品の名前と価格を全て読み取り、
以下のJSON形式で出力してください。
dateは必ず「{today}」を使ってください。

出力形式（このJSONのみを返してください）:
{{
  "date": "{today}",
  "items": [
    {{"name": "商品名", "price": 価格(数値)}},
    {{"name": "商品名", "price": 価格(数値)}}
  ]
}}
"""


def analyze_video(tmp_path, prompt):
    video_file = gemini_client.files.upload(file=tmp_path)
    while video_file.state.name == "PROCESSING":
        time.sleep(3)
        video_file = gemini_client.files.get(name=video_file.name)
    if video_file.state.name == "FAILED":
        raise ValueError("動画の処理に失敗しました")
    for attempt in range(3):
        try:
            response = gemini_client.models.generate_content(
                model="gemini-flash-latest",
                contents=[video_file, prompt]
            )
            return response.text
        except Exception:
            if attempt < 2:
                time.sleep(30)
            else:
                raise


def analyze_image(image_bytes, mime_type, prompt):
    image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
    for attempt in range(3):
        try:
            response = gemini_client.models.generate_content(
                model="gemini-flash-latest",
                contents=[image_part, prompt]
            )
            return response.text
        except Exception:
            if attempt < 2:
                time.sleep(30)
            else:
                raise


def parse_json(raw_text):
    text = raw_text.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def extract_sheet_id(url_or_id):
    if "spreadsheets/d/" in url_or_id:
        return url_or_id.split("spreadsheets/d/")[1].split("/")[0]
    return url_or_id.strip()


def _load_credentials():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    if "gcp_service_account" in st.secrets:
        return Credentials.from_service_account_info(
            dict(st.secrets["gcp_service_account"]), scopes=scopes
        )
    return Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=scopes)


def write_to_sheet(data, sheet_id, store_name, area, own_or_competitor, category):
    creds = _load_credentials()
    client = gspread.authorize(creds)
    sheet = client.open_by_key(sheet_id).get_worksheet(0)
    rows = [
        [data["date"], area, store_name, own_or_competitor, category, item["name"], item["price"]]
        for item in data["items"]
    ]
    sheet.append_rows(rows)
    return len(rows)


def show_result_and_save(result, store_name, area, own_or_competitor, category, sheet_url, result_key):
    st.success(f"✅ 解析完了！　{len(result['items'])} 商品を検出しました")
    st.markdown(f"**📅 {result['date']}　｜　🏪 {store_name or '—'}　｜　📍 {area or '—'}　｜　📦 {category or '—'}**")

    st.dataframe(
        [{"商品名": item["name"], "価格（円）": item["price"]} for item in result["items"]],
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("---")
    if not sheet_url:
        st.warning("💡 左メニューの「設定」でスプレッドシートのURLを登録すると保存できます")
    elif not store_name or not area:
        missing = []
        if not store_name: missing.append("店舗名")
        if not area: missing.append("エリア")
        st.warning(f"⚠️ 未入力の項目があります：{'、'.join(missing)}")
    else:
        if st.button("💾 スプレッドシートに保存する", type="primary", use_container_width=True, key=f"save_{result_key}"):
            try:
                sheet_id = extract_sheet_id(sheet_url)
                with st.spinner("保存中..."):
                    count = write_to_sheet(result, sheet_id, store_name, area, own_or_competitor, category)
                st.success(f"✅ {count}行を保存しました！")
                st.link_button(
                    "📊 スプレッドシートを開く →",
                    f"https://docs.google.com/spreadsheets/d/{sheet_id}",
                    use_container_width=True,
                )
            except Exception as e:
                st.error(f"保存エラー: {e}")


# =====================
# サイドバー
# =====================
with st.sidebar:
    # ロゴ
    if os.path.exists(LOGO_PATH):
        st.image(LOGO_PATH, width=120)
    else:
        st.markdown(
            "<div style='font-size:22px; font-weight:800; color:white; padding:16px 0 2px 0;'>棚パシャ</div>",
            unsafe_allow_html=True,
        )
    st.markdown(
        "<div style='color:rgba(255,255,255,0.4); font-size:12px; padding-bottom:16px;'>棚をパシャッと撮るだけ</div>",
        unsafe_allow_html=True,
    )
    st.markdown("<hr style='border-color:rgba(255,255,255,0.1);margin:0 0 10px 0;'>", unsafe_allow_html=True)

    # ナビゲーション
    history_count = len(st.session_state.scan_history)
    nav_items = [
        ("scan",     "📷",  "新規スキャン"),
        ("history",  "🕐",  f"スキャン履歴　{f'({history_count})' if history_count else ''}"),
        ("settings", "⚙️",  "設定"),
    ]
    for key, icon, label in nav_items:
        if st.button(f"{icon}  {label}", key=f"nav_{key}", use_container_width=True):
            st.session_state.page = key
            st.rerun()

    st.markdown("<hr style='border-color:rgba(255,255,255,0.1);margin:10px 0;'>", unsafe_allow_html=True)

    # 初回設定ガイド
    with st.expander("🆕 はじめての方へ：初回設定ガイド"):
        st.markdown(
            """
            <div style='font-size:13px; line-height:1.8;'>
            スプレッドシートを1枚用意するだけで使えます。<br><br>
            <b>STEP 1</b>　Googleスプレッドシートを新規作成<br>
            <b>STEP 2</b>　以下のアドレスと「共有」→「編集者」に設定<br>
            <b>STEP 3</b>　設定ページにURLを入力して保存
            </div>
            """,
            unsafe_allow_html=True,
        )
        try:
            sa_email = st.secrets["gcp_service_account"]["client_email"]
            st.code(sa_email, language=None)
        except Exception:
            st.caption("管理者よりメールアドレスをご確認ください")


# =====================
# メインエリア：新規スキャン
# =====================
if st.session_state.page == "scan":

    st.markdown("<div class='section-title'>新規スキャン</div>", unsafe_allow_html=True)
    st.markdown("<div class='section-sub'>棚をパシャッと撮るだけで価格を自動記録</div>", unsafe_allow_html=True)

    # 使い方アコーディオン（デフォルト閉じ）
    with st.expander("📖 はじめての方へ：使い方はこちら", expanded=False):
        st.markdown(
            """
            <div class="step-grid">
                <div class="step-card">
                    <span class="step-icon">settings</span>
                    <div class="step-num">STEP 1</div>
                    <div class="step-label">左メニューで<br>設定・情報入力</div>
                </div>
                <div class="step-card">
                    <span class="step-icon">photo_camera</span>
                    <div class="step-num">STEP 2</div>
                    <div class="step-label">棚を<br>パシャッと撮る</div>
                </div>
                <div class="step-card">
                    <span class="step-icon">auto_awesome</span>
                    <div class="step-num">STEP 3</div>
                    <div class="step-label">AIが自動で<br>価格を読み取る</div>
                </div>
                <div class="step-card">
                    <span class="step-icon">table_chart</span>
                    <div class="step-num">STEP 4</div>
                    <div class="step-label">スプレッドシートに<br>自動保存</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # スプレッドシート未設定の案内
    if not st.session_state.sheet_url:
        st.info("💡 左メニューの「⚙️ 設定」からスプレッドシートのURLを登録してください")

    # 店舗情報フォーム（インライン）
    st.markdown(
        "<div class='form-card'>"
        "<div class='form-card-title'>調査情報を入力</div>"
        "<div class='form-card-sub'>この撮影の店舗情報を入力してください</div>"
        "</div>",
        unsafe_allow_html=True,
    )
    col_l, col_r = st.columns(2)
    with col_l:
        store_name = st.text_input("🏪 店舗名", placeholder="例：イオン練馬店")
        own_or_competitor = st.selectbox("🏷️ 自社 / 競合", options=["競合", "自社"])
    with col_r:
        area = st.text_input("📍 エリア", placeholder="例：練馬区")
        category = st.text_input("📦 カテゴリ", placeholder="例：ペットフード")

    st.markdown("<div style='height:4px;'></div>", unsafe_allow_html=True)

    # アップロードタブ
    tab_photo, tab_video = st.tabs(["📷 写真でパシャッと解析（おすすめ）", "🎬 動画で解析"])

    with tab_photo:
        st.caption("📌 棚全体が写るように撮影すると、より多くの商品を読み取れます（数秒で完了）")
        photo_file = st.file_uploader(
            "写真を選択またはカメラで撮影",
            type=["jpg", "jpeg", "png", "webp", "heic"],
            key="photo_uploader",
            label_visibility="collapsed",
        )
        if photo_file is not None:
            st.image(photo_file, use_container_width=True)
            if st.button("🔍 解析スタート", type="primary", key="photo_analyze", use_container_width=True):
                try:
                    mime_map = {
                        "jpg": "image/jpeg", "jpeg": "image/jpeg",
                        "png": "image/png", "webp": "image/webp",
                        "heic": "image/heic",
                    }
                    ext = photo_file.name.split(".")[-1].lower()
                    mime_type = mime_map.get(ext, "image/jpeg")
                    image_bytes = photo_file.read()
                    with st.spinner("🤖 AIが解析中です。少々お待ちください..."):
                        raw_text = analyze_image(image_bytes, mime_type, build_prompt(TODAY, "写真"))
                        result = parse_json(raw_text)
                    st.session_state["photo_result"] = result
                    st.session_state.scan_history.append({
                        "type": "photo",
                        "store": store_name,
                        "area": area,
                        "category": category,
                        "own_or_competitor": own_or_competitor,
                        "result": result,
                        "timestamp": datetime.now().strftime("%H:%M"),
                    })
                except Exception as e:
                    st.error(f"エラーが発生しました: {e}")
        else:
            st.markdown(
                "<div style='text-align:center;padding:48px 0;color:#94a3b8;font-size:14px;'>"
                "📷 写真をドラッグ＆ドロップ、またはタップして選択"
                "</div>",
                unsafe_allow_html=True,
            )

        if st.session_state.get("photo_result") and photo_file is not None:
            st.markdown("---")
            show_result_and_save(
                st.session_state["photo_result"],
                store_name, area, own_or_competitor, category,
                st.session_state.sheet_url,
                "photo",
            )

    with tab_video:
        st.caption("📌 棚をゆっくりパンしながら撮影すると精度が上がります（30秒〜1分）")
        video_file = st.file_uploader(
            "動画を選択（mp4, mov対応）",
            type=["mp4", "mov", "avi"],
            key="video_uploader",
            label_visibility="collapsed",
        )
        if video_file is not None:
            st.video(video_file)
            if st.button("🔍 解析スタート", type="primary", key="video_analyze", use_container_width=True):
                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                    tmp.write(video_file.read())
                    tmp_path = tmp.name
                try:
                    with st.spinner("🤖 動画を解析中です。30秒〜1分ほどお待ちください..."):
                        raw_text = analyze_video(tmp_path, build_prompt(TODAY, "動画"))
                        result = parse_json(raw_text)
                    st.session_state["video_result"] = result
                    st.session_state.scan_history.append({
                        "type": "video",
                        "store": store_name,
                        "area": area,
                        "category": category,
                        "own_or_competitor": own_or_competitor,
                        "result": result,
                        "timestamp": datetime.now().strftime("%H:%M"),
                    })
                except Exception as e:
                    st.error(f"エラーが発生しました: {e}")
                finally:
                    os.unlink(tmp_path)
        else:
            st.markdown(
                "<div style='text-align:center;padding:48px 0;color:#94a3b8;font-size:14px;'>"
                "🎬 動画をドラッグ＆ドロップ、またはタップして選択"
                "</div>",
                unsafe_allow_html=True,
            )

        if st.session_state.get("video_result") and video_file is not None:
            st.markdown("---")
            show_result_and_save(
                st.session_state["video_result"],
                store_name, area, own_or_competitor, category,
                st.session_state.sheet_url,
                "video",
            )


# =====================
# メインエリア：スキャン履歴
# =====================
elif st.session_state.page == "history":

    st.markdown("<div class='section-title'>スキャン履歴</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='section-sub'>このセッション中のスキャン結果（ページを閉じるとリセットされます）</div>",
        unsafe_allow_html=True,
    )

    if not st.session_state.scan_history:
        st.markdown(
            "<div style='text-align:center;padding:80px 0;color:#94a3b8;font-size:15px;'>"
            "まだスキャン履歴はありません<br>"
            "<span style='font-size:13px;'>新規スキャンを行うとここに表示されます</span>"
            "</div>",
            unsafe_allow_html=True,
        )
    else:
        for i, scan in enumerate(reversed(st.session_state.scan_history)):
            idx = len(st.session_state.scan_history) - i
            label = (
                f"#{idx}　{scan['store'] or '（店舗名未入力）'}　"
                f"{scan['timestamp']}　—　{len(scan['result']['items'])}商品"
            )
            with st.expander(label):
                st.markdown(
                    f"<span class='badge'>{scan['own_or_competitor']}</span>"
                    f"<span class='badge'>{scan['area'] or '—'}</span>"
                    f"<span class='badge'>{scan['category'] or '—'}</span>",
                    unsafe_allow_html=True,
                )
                st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)
                st.dataframe(
                    [{"商品名": item["name"], "価格（円）": item["price"]} for item in scan["result"]["items"]],
                    use_container_width=True,
                    hide_index=True,
                )


# =====================
# メインエリア：設定
# =====================
elif st.session_state.page == "settings":

    st.markdown("<div class='section-title'>設定</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='section-sub'>最初に1回だけ設定してください。次回以降は自動で読み込まれます。</div>",
        unsafe_allow_html=True,
    )

    st.markdown(
        "<div class='info-card'>"
        "<div style='font-size:15px;font-weight:700;color:#0d1f3c;margin-bottom:12px;'>📊 保存先スプレッドシート</div>",
        unsafe_allow_html=True,
    )
    new_sheet_url = st.text_input(
        "スプレッドシートURL",
        value=st.session_state.sheet_url,
        placeholder="https://docs.google.com/spreadsheets/d/...",
    )
    if st.button("✅ 保存する", type="primary", use_container_width=True):
        st.session_state.sheet_url = new_sheet_url
        st.success("保存しました！新規スキャンページから使えます。")

    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)

    # 初回設定ガイド（設定ページにも表示）
    with st.expander("📋 スプレッドシートの初回設定手順", expanded=True):
        st.markdown("**STEP 1**　Googleスプレッドシートを新規作成する")
        st.caption("シート名はそのままでOKです（Sheet1 / シート1 どちらでも対応）")
        st.markdown("**STEP 2**　以下のメールアドレスと共有する（権限：編集者）")
        try:
            sa_email = st.secrets["gcp_service_account"]["client_email"]
            st.code(sa_email, language=None)
        except Exception:
            st.info("管理者よりサービスアカウントのメールアドレスをご確認ください")
        st.markdown("**STEP 3**　スプレッドシートのURLを上の入力欄に貼り付けて「保存」")
        st.caption("設定は最初の1回だけです。次回以降はURLを変更する必要はありません。")
