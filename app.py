import os
import json
import time
import tempfile
from datetime import date
from google import genai
from google.genai import types
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv
import gspread
import streamlit as st

# --- ページ設定（必ず最初に呼ぶ）---
st.set_page_config(
    page_title="競合価格チェッカー",
    page_icon="🛒",
    layout="centered",
)

# --- 設定 ---
load_dotenv()
CREDENTIALS_PATH = os.environ.get("CREDENTIALS_PATH", "credentials.json")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
TODAY = date.today().strftime("%Y/%m/%d")

gemini_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])


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
    sheet = client.open_by_key(sheet_id).worksheet("シート1")
    rows = [
        [data["date"], area, store_name, own_or_competitor, category, item["name"], item["price"]]
        for item in data["items"]
    ]
    sheet.append_rows(rows)
    return len(rows)


def show_result_and_save(result, store_name, area, own_or_competitor, category, sheet_url):
    st.success(f"✅ 解析完了！　{len(result['items'])} 商品を検出しました")

    st.markdown(f"**📅 撮影日：** {result['date']}")
    if store_name:
        st.markdown(
            f"**🏪 店舗：** {store_name}　｜　"
            f"**📍 エリア：** {area}　｜　"
            f"**🏷️** {own_or_competitor}　｜　"
            f"**📦 カテゴリ：** {category}"
        )

    st.markdown("#### 📋 抽出結果")
    st.dataframe(
        [{"商品名": item["name"], "価格（円）": item["price"]} for item in result["items"]],
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("---")
    st.markdown("#### STEP 4：スプレッドシートに保存する")

    settings_ok = store_name and area and sheet_url
    if not settings_ok:
        missing = []
        if not store_name:
            missing.append("店舗名")
        if not area:
            missing.append("店舗エリア")
        if not sheet_url:
            missing.append("スプレッドシートURL")
        st.warning(f"⚠️ 左のメニューで未入力の項目があります：{'、'.join(missing)}")
    else:
        if st.button("💾 スプレッドシートに保存する", type="primary", use_container_width=True):
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
    st.markdown("## ⚙️ 設定")
    st.info("📌 **最初にここで設定してください**\n\nスマホの場合は左上の **＞＞** をタップすると開きます", icon=None)
    st.markdown("---")

    st.markdown("**① 保存先スプレッドシート**")
    sheet_url = st.text_input(
        "スプレッドシートURL",
        value="",
        placeholder="https://docs.google.com/spreadsheets/d/...",
        label_visibility="collapsed",
    )

    st.markdown("**② 店舗名**")
    store_name = st.text_input(
        "店舗名",
        placeholder="例：イオン練馬店",
        label_visibility="collapsed",
    )

    st.markdown("**③ 店舗エリア**")
    area = st.text_input(
        "店舗エリア",
        placeholder="例：練馬区",
        label_visibility="collapsed",
    )

    st.markdown("**④ 自社 / 競合**")
    own_or_competitor = st.selectbox(
        "自社 / 競合",
        options=["競合", "自社"],
        label_visibility="collapsed",
    )

    st.markdown("**⑤ カテゴリ**")
    category = st.text_input(
        "カテゴリ",
        placeholder="例：ペットフード、飲料",
        label_visibility="collapsed",
    )

    st.markdown("---")

    # 設定完了チェック
    all_filled = sheet_url and store_name and area and category
    if all_filled:
        st.success("✅ 設定完了！\nあとは写真か動画をアップロードしてください")
    else:
        missing_count = sum([not sheet_url, not store_name, not area, not category])
        st.warning(f"あと **{missing_count}項目** の入力が必要です")

    st.markdown("---")
    st.caption("※ スプレッドシートはサービスアカウントと共有済みである必要があります")


# =====================
# メインエリア
# =====================
st.markdown("# 🛒 競合価格チェッカー")
st.markdown("棚の**写真・動画**をアップロードするだけで、商品名と価格を自動で読み取りスプレッドシートに記録します。")

st.markdown("---")

# 使い方フロー
st.markdown("### 📖 使い方")
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.markdown("**STEP 1**\n\n⚙️\n\n左メニューで店舗情報を入力")
with col2:
    st.markdown("**STEP 2**\n\n📷\n\n写真または動画をアップロード")
with col3:
    st.markdown("**STEP 3**\n\n🔍\n\n「解析スタート」を押す")
with col4:
    st.markdown("**STEP 4**\n\n💾\n\nスプレッドシートに保存")

st.markdown("---")

# タブ（写真を先に）
tab_photo, tab_video = st.tabs(["📷 写真で解析（おすすめ）", "🎬 動画で解析"])

# =====================
# 写真タブ（デフォルト）
# =====================
with tab_photo:
    st.markdown("#### STEP 2：写真をアップロードする")
    st.caption("📌 棚全体が写るように撮影すると、より多くの商品を読み取れます（数秒で完了）")

    photo_file = st.file_uploader(
        "写真を選択またはカメラで撮影",
        type=["jpg", "jpeg", "png", "webp", "heic"],
        key="photo_uploader",
        label_visibility="collapsed",
    )

    if photo_file is not None:
        st.image(photo_file, use_container_width=True)
        st.markdown("#### STEP 3：解析する")
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
                with st.spinner("🤖 AIが写真を解析中です。少々お待ちください..."):
                    raw_text = analyze_image(image_bytes, mime_type, build_prompt(TODAY, "写真"))
                    result = parse_json(raw_text)
                st.session_state["photo_result"] = result
            except Exception as e:
                st.error(f"エラーが発生しました: {e}")
    else:
        st.markdown(
            """
            <div style='text-align:center; padding: 40px 0; color: #888;'>
                📷 ここに写真をドラッグ＆ドロップ<br>またはボタンをタップして選択してください
            </div>
            """,
            unsafe_allow_html=True,
        )

    if "photo_result" in st.session_state and photo_file is not None:
        st.markdown("---")
        show_result_and_save(
            st.session_state["photo_result"],
            store_name, area, own_or_competitor, category, sheet_url
        )

# =====================
# 動画タブ
# =====================
with tab_video:
    st.markdown("#### STEP 2：動画をアップロードする")
    st.caption("📌 棚をゆっくりパンしながら撮影すると精度が上がります（処理に30秒〜1分かかります）")

    video_file = st.file_uploader(
        "動画を選択（mp4, mov対応）",
        type=["mp4", "mov", "avi"],
        key="video_uploader",
        label_visibility="collapsed",
    )

    if video_file is not None:
        st.video(video_file)
        st.markdown("#### STEP 3：解析する")
        if st.button("🔍 解析スタート", type="primary", key="video_analyze", use_container_width=True):
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                tmp.write(video_file.read())
                tmp_path = tmp.name
            try:
                with st.spinner("🤖 AIが動画を解析中です。30秒〜1分ほどお待ちください..."):
                    raw_text = analyze_video(tmp_path, build_prompt(TODAY, "動画"))
                    result = parse_json(raw_text)
                st.session_state["video_result"] = result
            except Exception as e:
                st.error(f"エラーが発生しました: {e}")
            finally:
                os.unlink(tmp_path)
    else:
        st.markdown(
            """
            <div style='text-align:center; padding: 40px 0; color: #888;'>
                🎬 ここに動画をドラッグ＆ドロップ<br>またはボタンをタップして選択してください
            </div>
            """,
            unsafe_allow_html=True,
        )

    if "video_result" in st.session_state and video_file is not None:
        st.markdown("---")
        show_result_and_save(
            st.session_state["video_result"],
            store_name, area, own_or_competitor, category, sheet_url
        )
