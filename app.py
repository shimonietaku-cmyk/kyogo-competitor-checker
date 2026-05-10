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

# --- 設定 ---
load_dotenv()
CREDENTIALS_PATH = os.environ.get("CREDENTIALS_PATH", "credentials.json")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
TODAY = date.today().strftime("%Y/%m/%d")

gemini_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def build_prompt(today, media_type="動画"):
    """プロンプトを生成する（動画・写真どちらにも対応）"""
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
    """動画をアップロードしてGeminiで解析する"""
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
        except Exception as e:
            if attempt < 2:
                time.sleep(30)
            else:
                raise


def analyze_image(image_bytes, mime_type, prompt):
    """画像をインラインで送信してGeminiで解析する（動画より高速）"""
    image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
    for attempt in range(3):
        try:
            response = gemini_client.models.generate_content(
                model="gemini-flash-latest",
                contents=[image_part, prompt]
            )
            return response.text
        except Exception as e:
            if attempt < 2:
                time.sleep(30)
            else:
                raise


def parse_json(raw_text):
    """GeminiのレスポンスからJSONを抽出する"""
    text = raw_text.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def extract_sheet_id(url_or_id):
    """スプレッドシートのURLまたはIDからIDを取り出す"""
    if "spreadsheets/d/" in url_or_id:
        return url_or_id.split("spreadsheets/d/")[1].split("/")[0]
    return url_or_id.strip()


def _load_credentials():
    """Streamlit Cloud Secrets またはローカルファイルからサービスアカウント認証情報を取得する"""
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    if "gcp_service_account" in st.secrets:
        return Credentials.from_service_account_info(
            dict(st.secrets["gcp_service_account"]), scopes=scopes
        )
    return Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=scopes)


def write_to_sheet(data: dict, sheet_id: str, store_name: str, area: str, own_or_competitor: str, category: str):
    """解析結果をGoogleスプレッドシートに追記する"""
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
    """解析結果の表示と保存ボタンを表示する（動画・写真共通）"""
    st.subheader(f"📅 撮影日: {result['date']}")
    if store_name:
        st.caption(f"🏪 店舗：{store_name}　📍 エリア：{area}　🏷️ {own_or_competitor}　📦 カテゴリ：{category}")

    st.subheader("📋 抽出結果")
    st.table([
        {"商品名": item["name"], "価格（円）": item["price"]}
        for item in result["items"]
    ])
    st.caption(f"合計 {len(result['items'])} 商品")

    if st.button("💾 Googleスプレッドシートに保存", type="secondary"):
        if not store_name:
            st.warning("サイドバーに店舗名を入力してください")
        elif not area:
            st.warning("サイドバーに店舗エリアを入力してください")
        else:
            try:
                sheet_id = extract_sheet_id(sheet_url)
                with st.spinner("保存中..."):
                    count = write_to_sheet(result, sheet_id, store_name, area, own_or_competitor, category)
                st.success(f"✅ {count}行をスプレッドシートに保存しました！")
                st.link_button(
                    "スプレッドシートを開く",
                    f"https://docs.google.com/spreadsheets/d/{sheet_id}"
                )
            except Exception as e:
                st.error(f"保存エラー: {e}")


# --- Streamlit UI ---
st.title("🛒 競合価格チェッカー")
st.caption("スーパーの棚を撮影した動画または写真から商品名と価格を自動抽出します")

# --- サイドバー：設定 ---
with st.sidebar:
    st.header("⚙️ 設定")

    sheet_url = st.text_input(
        "スプレッドシートURL",
        value="",
        help="保存先スプレッドシートのURLを貼り付けてください"
    )
    store_name = st.text_input(
        "店舗名",
        placeholder="例：イオン練馬店",
    )
    area = st.text_input(
        "店舗エリア",
        placeholder="例：練馬区、新宿区",
    )
    own_or_competitor = st.selectbox(
        "自社 / 競合",
        options=["競合", "自社"],
    )
    category = st.text_input(
        "カテゴリ",
        placeholder="例：ペットフード、飲料",
    )
    st.divider()
    st.caption("※ スプレッドシートはサービスアカウントと共有済みである必要があります")

# --- タブ：動画 / 写真 ---
tab_video, tab_photo = st.tabs(["🎬 動画アップロード", "📷 写真アップロード"])

# === 動画タブ ===
with tab_video:
    st.caption("動画をアップロードして解析します（処理に30秒〜1分かかります）")
    video_file = st.file_uploader(
        "動画ファイルを選択（mp4, mov対応）",
        type=["mp4", "mov", "avi"],
        key="video_uploader"
    )
    if video_file is not None:
        st.video(video_file)
        if st.button("🔍 解析スタート", type="primary", key="video_analyze"):
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                tmp.write(video_file.read())
                tmp_path = tmp.name
            try:
                with st.spinner("動画を解析中... しばらくお待ちください"):
                    raw_text = analyze_video(tmp_path, build_prompt(TODAY, "動画"))
                    result = parse_json(raw_text)
                st.session_state["result"] = result
                st.success("解析完了！")
            except Exception as e:
                st.error(f"エラーが発生しました: {e}")
            finally:
                os.unlink(tmp_path)

    if "result" in st.session_state and video_file is not None:
        show_result_and_save(st.session_state["result"], store_name, area, own_or_competitor, category, sheet_url)

# === 写真タブ ===
with tab_photo:
    st.caption("写真をアップロードして解析します（数秒で完了します）")
    photo_file = st.file_uploader(
        "写真ファイルを選択（jpg, png, heic対応）",
        type=["jpg", "jpeg", "png", "webp", "heic"],
        key="photo_uploader"
    )
    if photo_file is not None:
        st.image(photo_file, use_container_width=True)
        if st.button("🔍 解析スタート", type="primary", key="photo_analyze"):
            try:
                mime_map = {
                    "jpg": "image/jpeg", "jpeg": "image/jpeg",
                    "png": "image/png", "webp": "image/webp",
                    "heic": "image/heic"
                }
                ext = photo_file.name.split(".")[-1].lower()
                mime_type = mime_map.get(ext, "image/jpeg")
                image_bytes = photo_file.read()

                with st.spinner("写真を解析中..."):
                    raw_text = analyze_image(image_bytes, mime_type, build_prompt(TODAY, "写真"))
                    result = parse_json(raw_text)
                st.session_state["photo_result"] = result
                st.success("解析完了！")
            except Exception as e:
                st.error(f"エラーが発生しました: {e}")

    if "photo_result" in st.session_state and photo_file is not None:
        show_result_and_save(st.session_state["photo_result"], store_name, area, own_or_competitor, category, sheet_url)