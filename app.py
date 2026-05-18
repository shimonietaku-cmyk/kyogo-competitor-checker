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
# シンプルなCSS（外部フォント依存なし）
# =====================
st.html("""
<style>
.block-container { padding-top: 1.2rem; padding-bottom: 3rem; max-width: 720px; }

/* CTAボタン */
div[data-testid="stButton"] > button[kind="primary"] {
    background: linear-gradient(135deg, #1565C0 0%, #1E88E5 100%) !important;
    color: white !important;
    border: none !important;
    border-radius: 12px !important;
    font-size: 16px !important;
    font-weight: 700 !important;
    padding: 14px 0 !important;
    box-shadow: 0 4px 12px rgba(21,101,192,0.3) !important;
}
div[data-testid="stButton"] > button[kind="primary"]:hover {
    box-shadow: 0 6px 18px rgba(21,101,192,0.45) !important;
    transform: translateY(-1px) !important;
}

/* サイドバー テキスト白 */
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] span,
[data-testid="stSidebar"] label { color: rgba(255,255,255,0.9) !important; }
[data-testid="stSidebar"] div[data-testid="stButton"] > button {
    background: transparent !important;
    border: none !important;
    color: rgba(255,255,255,0.75) !important;
    text-align: left !important;
    font-size: 14px !important;
    font-weight: 500 !important;
    border-radius: 8px !important;
    padding: 9px 12px !important;
}
[data-testid="stSidebar"] div[data-testid="stButton"] > button:hover {
    background: rgba(255,255,255,0.12) !important;
    color: white !important;
}
</style>
""")

# =====================
# 設定 & 初期化
# =====================
load_dotenv()
CREDENTIALS_PATH = os.environ.get("CREDENTIALS_PATH", "credentials.json")
TODAY = date.today().strftime("%Y/%m/%d")
LOGO_PATH = "tana_pasha_logo.png"

gemini_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

for k, v in {"page": "scan", "scan_history": [], "sheet_url": ""}.items():
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
                model="gemini-flash-latest", contents=[video_file, prompt]
            )
            return response.text
        except Exception:
            if attempt < 2: time.sleep(30)
            else: raise


def analyze_image(image_bytes, mime_type, prompt):
    image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
    for attempt in range(3):
        try:
            response = gemini_client.models.generate_content(
                model="gemini-flash-latest", contents=[image_part, prompt]
            )
            return response.text
        except Exception:
            if attempt < 2: time.sleep(30)
            else: raise


def parse_json(raw_text):
    text = raw_text.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"): text = text[4:]
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


def show_result_and_save(result, store_name, area, own_or_competitor, category, result_key):
    st.success(f"✅ 解析完了！　{len(result['items'])} 商品を検出しました")
    st.caption(f"📅 {result['date']}　🏪 {store_name or '—'}　📍 {area or '—'}　📦 {category or '—'}")
    st.dataframe(
        [{"商品名": item["name"], "価格（円）": item["price"]} for item in result["items"]],
        use_container_width=True, hide_index=True,
    )
    st.divider()

    sheet_url = st.session_state.sheet_url
    if not sheet_url:
        st.warning("💡 左メニューの「設定」でスプレッドシートURLを登録すると保存できます")
    elif not store_name or not area:
        missing = [x for x, v in [("店舗名", store_name), ("エリア", area)] if not v]
        st.warning(f"⚠️ 未入力：{'・'.join(missing)}")
    else:
        if st.button("💾 スプレッドシートに保存する", type="primary",
                     use_container_width=True, key=f"save_{result_key}"):
            try:
                with st.spinner("保存中..."):
                    count = write_to_sheet(
                        result, extract_sheet_id(sheet_url),
                        store_name, area, own_or_competitor, category
                    )
                st.success(f"✅ {count}行を保存しました！")
                st.link_button("📊 スプレッドシートを開く →",
                               f"https://docs.google.com/spreadsheets/d/{extract_sheet_id(sheet_url)}",
                               use_container_width=True)
            except Exception as e:
                st.error(f"保存エラー: {e}")


# =====================
# サイドバー
# =====================
with st.sidebar:
    if os.path.exists(LOGO_PATH):
        st.image(LOGO_PATH, width=100)
    else:
        st.markdown("### 📷 棚パシャ")

    st.caption("棚をパシャッと撮るだけ")
    st.divider()

    # ナビゲーション
    history_label = f"🕐  スキャン履歴　({len(st.session_state.scan_history)})" \
        if st.session_state.scan_history else "🕐  スキャン履歴"

    for key, label in [("scan", "📷  新規スキャン"), ("history", history_label), ("settings", "⚙️  設定")]:
        if st.button(label, key=f"nav_{key}", use_container_width=True):
            st.session_state.page = key
            st.rerun()

    st.divider()

    with st.expander("🆕 初回設定ガイド", expanded=False):
        st.markdown("""
**① Googleスプレッドシートを新規作成**

**② 以下のアドレスと「共有」→「編集者」**
""")
        try:
            sa_email = st.secrets["gcp_service_account"]["client_email"]
            st.code(sa_email, language=None)
        except Exception:
            st.caption("管理者にメールアドレスをご確認ください")
        st.markdown("**③ 設定ページにURLを保存**")
        st.caption("設定は最初の1回だけです")


# =====================
# 新規スキャン
# =====================
if st.session_state.page == "scan":

    st.markdown("## 新規スキャン")
    st.caption("棚をパシャッと撮るだけで価格を自動記録")

    if not st.session_state.sheet_url:
        st.info("💡 左の「⚙️ 設定」からスプレッドシートのURLを登録してください")

    # 店舗情報
    st.markdown("**調査情報**")
    c1, c2 = st.columns(2)
    with c1:
        store_name = st.text_input("🏪 店舗名", placeholder="例：イオン練馬店")
        own_or_competitor = st.selectbox("🏷️ 自社 / 競合", ["競合", "自社"])
    with c2:
        area = st.text_input("📍 エリア", placeholder="例：練馬区")
        category = st.text_input("📦 カテゴリ", placeholder="例：ペットフード")

    st.divider()

    # アップロード
    tab_photo, tab_video = st.tabs(["📷 写真（おすすめ・数秒）", "🎬 動画（30秒〜1分）"])

    with tab_photo:
        photo_file = st.file_uploader(
            "写真をアップロード（またはカメラで撮影）",
            type=["jpg", "jpeg", "png", "webp", "heic"],
            key="photo_uploader",
        )
        if photo_file:
            st.image(photo_file, use_container_width=True)
            if st.button("🔍 解析スタート", type="primary", key="photo_btn", use_container_width=True):
                try:
                    mime_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                                "png": "image/png", "webp": "image/webp", "heic": "image/heic"}
                    ext = photo_file.name.split(".")[-1].lower()
                    with st.spinner("🤖 AIが解析中です..."):
                        raw = analyze_image(photo_file.read(), mime_map.get(ext, "image/jpeg"),
                                            build_prompt(TODAY, "写真"))
                        result = parse_json(raw)
                    st.session_state["photo_result"] = result
                    st.session_state.scan_history.append({
                        "store": store_name, "area": area, "category": category,
                        "own_or_competitor": own_or_competitor,
                        "result": result, "timestamp": datetime.now().strftime("%H:%M"),
                    })
                except Exception as e:
                    st.error(f"エラー: {e}")

        if st.session_state.get("photo_result") and photo_file:
            st.divider()
            show_result_and_save(st.session_state["photo_result"],
                                 store_name, area, own_or_competitor, category, "photo")

    with tab_video:
        video_file = st.file_uploader(
            "動画をアップロード（mp4 / mov）",
            type=["mp4", "mov", "avi"],
            key="video_uploader",
        )
        if video_file:
            st.video(video_file)
            if st.button("🔍 解析スタート", type="primary", key="video_btn", use_container_width=True):
                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                    tmp.write(video_file.read())
                    tmp_path = tmp.name
                try:
                    with st.spinner("🤖 動画を解析中です。30秒〜1分ほどお待ちください..."):
                        raw = analyze_video(tmp_path, build_prompt(TODAY, "動画"))
                        result = parse_json(raw)
                    st.session_state["video_result"] = result
                    st.session_state.scan_history.append({
                        "store": store_name, "area": area, "category": category,
                        "own_or_competitor": own_or_competitor,
                        "result": result, "timestamp": datetime.now().strftime("%H:%M"),
                    })
                except Exception as e:
                    st.error(f"エラー: {e}")
                finally:
                    os.unlink(tmp_path)

        if st.session_state.get("video_result") and video_file:
            st.divider()
            show_result_and_save(st.session_state["video_result"],
                                 store_name, area, own_or_competitor, category, "video")


# =====================
# スキャン履歴
# =====================
elif st.session_state.page == "history":

    st.markdown("## スキャン履歴")
    st.caption("このセッション中のスキャン結果（ページを閉じるとリセットされます）")

    if not st.session_state.scan_history:
        st.markdown(
            "<div style='text-align:center;padding:60px 0;color:#94a3b8;'>"
            "まだスキャン履歴はありません</div>",
            unsafe_allow_html=True,
        )
    else:
        for i, scan in enumerate(reversed(st.session_state.scan_history)):
            idx = len(st.session_state.scan_history) - i
            title = f"#{idx}　{scan['store'] or '店舗名未入力'}　{scan['timestamp']}　— {len(scan['result']['items'])}商品"
            with st.expander(title):
                st.caption(f"{scan['area']} ｜ {scan['own_or_competitor']} ｜ {scan['category']}")
                st.dataframe(
                    [{"商品名": item["name"], "価格（円）": item["price"]}
                     for item in scan["result"]["items"]],
                    use_container_width=True, hide_index=True,
                )


# =====================
# 設定
# =====================
elif st.session_state.page == "settings":

    st.markdown("## 設定")
    st.caption("スプレッドシートのURLを登録してください（最初の1回だけ）")

    with st.container(border=True):
        st.markdown("**📊 保存先スプレッドシートURL**")
        new_url = st.text_input(
            "URL", value=st.session_state.sheet_url,
            placeholder="https://docs.google.com/spreadsheets/d/...",
            label_visibility="collapsed",
        )
        if st.button("✅ 保存する", type="primary", use_container_width=True):
            st.session_state.sheet_url = new_url
            st.success("保存しました！「新規スキャン」から使えます。")

    st.divider()

    with st.expander("📋 スプレッドシートの初回設定手順", expanded=True):
        st.markdown("**STEP 1**　Googleスプレッドシートを新規作成する")
        st.caption("シート名はそのままでOK（Sheet1 / シート1 どちらでも対応）")
        st.markdown("**STEP 2**　以下のメールアドレスと共有する（権限：編集者）")
        try:
            sa_email = st.secrets["gcp_service_account"]["client_email"]
            st.code(sa_email, language=None)
        except Exception:
            st.info("管理者よりサービスアカウントのメールアドレスをご確認ください")
        st.markdown("**STEP 3**　上の入力欄にURLを貼り付けて「保存」")
        st.caption("設定は最初の1回だけです。")
