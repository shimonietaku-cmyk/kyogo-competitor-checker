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
    page_title="棚パシャ",
    page_icon="📷",
    layout="centered",
)

# --- 不要なデフォルトUIを非表示 ---
st.html("""
<style>
/* ハンバーガーメニュー（右上の三点リーダー） */
[data-testid="stToolbarActions"] { display: none !important; }
/* Deployボタン */
[data-testid="stAppDeployButton"] { display: none !important; }
/* 最下部の「Made with Streamlit」フッター */
footer { display: none !important; }
/* フッター上部の「Running...」バナー（任意） */
[data-testid="stStatusWidget"] { display: none !important; }
</style>
""")

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
    # シート名に依存しないよう、常に1枚目のシートを使用（Sheet1・シート1どちらでも対応）
    sheet = client.open_by_key(sheet_id).get_worksheet(0)
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
LOGO_PATH = "tana_pasha_logo.png"

with st.sidebar:
    if os.path.exists(LOGO_PATH):
        st.image(LOGO_PATH, width=90)
    else:
        st.markdown("## 📷 棚パシャ")
    st.caption("はじめての方はまず下のガイドをご確認ください")

    # ★ 初回設定ガイドを一番上に（デフォルトで開いた状態）
    with st.expander("🆕 はじめての方へ：初回設定ガイド", expanded=True):
        st.markdown("**スプレッドシートを1枚用意するだけで使えます。**")
        st.markdown("---")
        st.markdown("**STEP 1　Googleスプレッドシートを新規作成**")
        st.caption("シート名はそのままでOKです。")
        st.markdown("**STEP 2　以下のアドレスと共有する**")
        st.caption("スプレッドシートの「共有」→ 下記メールを追加 →「編集者」に設定")
        try:
            sa_email = st.secrets["gcp_service_account"]["client_email"]
            st.code(sa_email, language=None)
        except Exception:
            st.info("管理者よりメールアドレスをご確認ください")
        st.markdown("**STEP 3　URLを下の①欄に貼り付ける**")
        st.caption("設定は最初の1回だけ。次回以降はURLを入力するだけです。")

    st.markdown("---")
    st.markdown("#### ⚙️ 調査情報を入力")

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
        st.success("✅ 準備OK！\n写真か動画をアップロードしてください")
    else:
        missing_count = sum([not sheet_url, not store_name, not area, not category])
        st.warning(f"あと **{missing_count}項目** の入力が必要です")


# =====================
# メインエリア
# =====================
col_logo, col_title = st.columns([1, 5])
with col_logo:
    if os.path.exists(LOGO_PATH):
        st.image(LOGO_PATH, width=60)
with col_title:
    st.markdown("# 棚パシャ")
st.markdown("名前のとおり、棚を**パシャッと撮るだけ**で、商品名と価格を自動で読み取りスプレッドシートに記録します。")

# モバイル向け案内バナー（設定未完了時のみ）
if not (sheet_url and store_name and area and category):
    st.markdown(
        """
        <div style="
            background: linear-gradient(135deg, #e3f2fd, #fce4ec);
            border-radius: 12px;
            padding: 18px 22px;
            margin: 16px 0;
        ">
            <div style="font-size: 17px; font-weight: bold; color: #333; margin-bottom: 8px;">
                👆 画面左上の「＞＞」をタップして設定してください
            </div>
            <div style="font-size: 14px; color: #555;">
                設定が完了するとこの案内は消えます
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown("---")

# 使い方フロー（シンプルなステップ）
st.markdown("### 使い方")
st.markdown(
    """
    <div style="display: flex; gap: 8px; margin: 8px 0 20px 0; flex-wrap: wrap;">
        <div style="flex:1; min-width:120px; background:#f8f9fa; border-radius:10px; padding:14px; text-align:center;">
            <div style="font-size:24px;">⚙️</div>
            <div style="font-size:12px; color:#888; margin:4px 0;">STEP 1</div>
            <div style="font-size:13px; font-weight:bold;">左メニューで<br>店舗情報を入力</div>
        </div>
        <div style="flex:1; min-width:120px; background:#f8f9fa; border-radius:10px; padding:14px; text-align:center;">
            <div style="font-size:24px;">📷</div>
            <div style="font-size:12px; color:#888; margin:4px 0;">STEP 2</div>
            <div style="font-size:13px; font-weight:bold;">棚を<br>パシャッと撮る</div>
        </div>
        <div style="flex:1; min-width:120px; background:#f8f9fa; border-radius:10px; padding:14px; text-align:center;">
            <div style="font-size:24px;">🤖</div>
            <div style="font-size:12px; color:#888; margin:4px 0;">STEP 3</div>
            <div style="font-size:13px; font-weight:bold;">AIが自動で<br>価格を読み取る</div>
        </div>
        <div style="flex:1; min-width:120px; background:#f8f9fa; border-radius:10px; padding:14px; text-align:center;">
            <div style="font-size:24px;">📊</div>
            <div style="font-size:12px; color:#888; margin:4px 0;">STEP 4</div>
            <div style="font-size:13px; font-weight:bold;">スプレッドシートに<br>自動保存</div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown("---")

# タブ（写真を先に）
tab_photo, tab_video = st.tabs(["📷 写真でパシャッと解析（おすすめ）", "🎬 動画で解析"])

# =====================
# 写真タブ（デフォルト）
# =====================
with tab_photo:
    st.markdown("#### STEP 2：写真をアップロードする ＝ パシャッと撮るだけ")
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
