import json
import os
import hashlib
import requests
from fastapi import FastAPI, Request, HTTPException, Depends
from pydantic import BaseModel
from telegram import Bot
from fpdf import FPDF, XPos, YPos
from PIL import Image
from io import BytesIO
import datetime
import tempfile
import shutil
import re
import traceback
import base64
import hmac
import subprocess
import time
import threading
from apscheduler.schedulers.background import BackgroundScheduler
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials

app = FastAPI()

# Config o'qish
CONFIG_PATH = "config.json"
with open(CONFIG_PATH) as f:
    CONFIG = json.load(f)

# In-memory DB (users configdan o'qiladi)
users = {user["username"]: user for user in CONFIG["users"]}

# Temp dir for images
TEMP_DIR = tempfile.mkdtemp()

# Google Sheets sozlash
SHEETS_CREDENTIALS = Credentials.from_service_account_file(
    CONFIG["credentials"]["google_sheets"],
    scopes=["https://www.googleapis.com/auth/spreadsheets"]
)
sheets_service = build("sheets", "v4", credentials=SHEETS_CREDENTIALS)
SPREADSHEET_ID = CONFIG["google_sheets"]["url"].split("/d/")[1].split("/")[0]  # URL dan ID olish
WORKSHEET_NAME = CONFIG["google_sheets"]["worksheet_name"]

# Yandex stores (campaigns)
STORES = {store["campaign_id"]: store for store in CONFIG["stores"]}

# Telegram
BOT_TOKEN = CONFIG["telegram"]["bot_token"]
GROUP_ID = CONFIG["telegram"]["group_id"]

# Branding
LOGO_PATH = CONFIG["branding"]["company_logo_path"]

# Schedule time
SCHEDULE_TIME = CONFIG["schedule_time"]  # "HH:MM"

# Security: Telegram initData validate
bot_token_hash = hashlib.sha256(BOT_TOKEN.encode()).hexdigest()
TELEGRAM_SECRET = hashlib.sha256(b"WebAppData" + bot_token_hash.encode()).digest()

def validate_telegram_init_data(init_data: dict) -> bool:
    try:
        hash_value = init_data.pop("hash", None)
        data_check_string = "\n".join([f"{k}={v}" for k, v in sorted(init_data.items())])
        hash_check = hmac.new(TELEGRAM_SECRET, data_check_string.encode(), hashlib.sha256).hexdigest()
        return hash_check == hash_value
    except:
        return False

class User(BaseModel):
    username: str
    password: str
    role: str = "worker"

class Decision(BaseModel):
    order_id: str
    decision: str  # "yes", "no", "skip"

# Auth dependency
async def get_current_user(request: Request):
    try:
        init_data = await request.json() if request.method == "POST" else request.query_params.get("init_data", {})
        if isinstance(init_data, str):
            init_data = json.loads(init_data)
        if not validate_telegram_init_data(init_data):
            raise HTTPException(401, "Invalid Telegram data")
        username = init_data.get("username")
        if username not in users:
            raise HTTPException(401, "Unauthorized")
        user = users[username]
        user["id"] = username
        return user
    except Exception as e:
        raise HTTPException(401, f"Auth error: {str(e)}")

@app.post("/auth")
async def auth(request: Request):
    init_data = await request.json()
    if not validate_telegram_init_data(init_data):
        raise HTTPException(401, "Invalid data")
    username = init_data.get("username")
    password = init_data.get("password")
    if username not in users:
        raise HTTPException(401, "User not found")
    # Password hash check (configda berilgan hash bilan solishtirish)
    password_hash = hashlib.sha512(password.encode()).hexdigest()  # Assume SHA512, uzunligiga qarab
    if users[username]["password_hash"] != password_hash:
        raise HTTPException(401, "Invalid password")
    return {"user": {k: v for k, v in users[username].items() if k != "password_hash"}}

@app.get("/campaigns")
def get_campaigns(current_user: dict = Depends(get_current_user)):
    if not users[current_user["id"]].get("is_admin", False):
        raise HTTPException(403, "Admin only")
    # Configdan stores campaign_id larni qaytarish (Yandex API o'rniga yoki qo'shimcha)
    campaigns = [{"id": store["campaign_id"], "name": store["name"]} for store in CONFIG["stores"]]
    return campaigns

@app.get("/orders/{campaign_id}")
def get_orders(campaign_id: str, current_user: dict = Depends(get_current_user)):
    if campaign_id not in [str(store["campaign_id"]) for store in CONFIG["stores"]]:
        raise HTTPException(404, "Campaign not found")
    if campaign_id not in str(users[current_user["id"]].get("assigned_campaigns", [])):
        raise HTTPException(403, "Access denied")
    # Yandex API so'rov (token configdan)
    store = next((s for s in CONFIG["stores"] if str(s["campaign_id"]) == campaign_id), None)
    if not store:
        raise HTTPException(404, "Store not found")
    headers = {"Authorization": f"OAuth {store['token']}"}
    url = f"https://api.partner.market.yandex.ru/v2/campaigns/{campaign_id}/orders?status=PROCESSING&substatus=STARTED"
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise HTTPException(500, f"Yandex orders error: {response.text}")
    orders = response.json().get("orders", [])
    extracted_data = []
    for order in orders:
        for item in order.get("items", []):
            data = {
                "order_id": str(order["id"]),
                "product_name": item.get("offerName", ""),
                "sku": item.get("shopSku", "-"),
                "barcode": item.get("barcode", "-"),
                "quantity": item.get("count", 1)
            }
            img_path = download_image(f"{data['product_name']} {data['sku']}")
            if img_path:
                data["image_path"] = img_path
            extracted_data.append(data)
    return extracted_data

def download_image(query):
    try:
        clean_query = re.sub(r'[^a-zA-Zа-яА-Я0-9\s\-]', '', query)
        clean_query = " ".join(clean_query.split()[:6])
        if not clean_query:
            return None
        params = {
            "q": clean_query,
            "cx": CONFIG.get("cse_id", "default_cse_id"),  # Configga qo'shing agar kerak
            "key": CONFIG.get("google_api_key", "default_key"),
            "searchType": "image",
            "num": 1,
            "safe": "medium",
        }
        response = requests.get("https://www.googleapis.com/customsearch/v1", params=params, timeout=10)
        data = response.json()
        if "items" not in data:
            return None
        img_url = data["items"][0].get("link")
        if not img_url:
            return None
        img_response = requests.get(img_url, timeout=10)
        img_bytes = BytesIO(img_response.content)
        img = Image.open(img_bytes).convert("RGB")
        safe_name = re.sub(r'[^A-Za-z0-9]', '_', clean_query)[:40]
        temp_path = os.path.join(TEMP_DIR, f"{safe_name}.jpg")
        img.save(temp_path, "JPEG", quality=90)
        return temp_path
    except Exception as e:
        print(f"Image download error: {e}")
        return None

@app.post("/save_decisions")
async def save_decisions(decisions: list[Decision], current_user: dict = Depends(get_current_user)):
    positive = []
    negative = []
    skipped = []
    for dec in decisions:
        item = {"order_id": dec.order_id}  # To'liq item frontenddan kelsin yoki cache dan
        if dec.decision == "yes":
            positive.append(item)
        elif dec.decision == "no":
            negative.append(item)
        else:
            skipped.append(item)
    # Update balance va processed (configda balance bor)
    user_id = current_user["id"]
    users[user_id]["processed_orders"] = users[user_id].get("processed_orders", 0) + len(decisions)
    # Google Sheets ga yozish (hisobot)
    values = [[datetime.date.today().strftime("%Y-%m-%d"), user_id, len(positive), len(negative), len(skipped)]]
    body = {"values": values}
    sheets_service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{WORKSHEET_NAME}!A1",
        valueInputOption="RAW",
        body=body
    ).execute()
    # PDF va Telegram
    today = datetime.date.today().strftime("%Y-%m-%d")
    pos_pdf = generate_pdf(positive, True, today)
    neg_pdf = generate_pdf(negative, False, today)
    bot = Bot(BOT_TOKEN)
    try:
        if pos_pdf:
            bot.send_document(GROUP_ID, document=open(pos_pdf, "rb"), caption="✅ Tasdiqlangan buyurtmalar")
        if neg_pdf:
            bot.send_document(GROUP_ID, document=open(neg_pdf, "rb"), caption="❌ Bekor qilingan / kechiktirilganlar")
    except Exception as e:
        print(f"Telegram send error: {e}")
    # Update last_report_charge
    CONFIG["last_report_charge"] = {"date": today, "user": user_id}
    with open(CONFIG_PATH, "w") as f:
        json.dump(CONFIG, f)
    return {"status": "saved"}


class PDF(FPDF):
    def header(self):
        try: self.image("fineok_logo.jpg", 10, 10, 40, 20)
        except: pass
        try: self.image("spphone_logo.png", self.w - 50, 10, 40, 20)
        except: pass
        self.set_font("DejaVu", "B", 12)
        self.ln(25)
        self.cell(0, 10, f"НАКЛАДНАЯ № ____        ОТ {datetime.date.today().strftime('%d.%m.%Y')}", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(5)

    def footer(self):
        self.set_y(-35)
        self.set_font("DejaVu", "", 9)
        self.cell(90, 6, "От FineOk: ________________________________", 0, 0, "L")
        self.cell(10)
        self.cell(90, 6, "От SP Phone: ________________________________", 0, 1, "L")
        self.cell(90, 6, "(Ф.И.О., подпись)", 0, 0, "L")
        self.cell(10)
        self.cell(90, 6, "(Ф.И.О., подпись)", 0, 1, "L")

def get_multi_cell_height(pdf, w, txt, line_height):
    lines = pdf.multi_cell(w, line_height, txt, split_only=True)
    return len(lines) * line_height

def generate_pdf(items, is_positive, date):
    pdf = PDF(orientation="P", unit="mm", format="A4")
    pdf.add_font("DejaVu", "", "fonts/DejaVuSans.ttf")
    pdf.add_font("DejaVu", "B", "fonts/DejaVuSans-Bold.ttf")
    pdf.set_font("DejaVu", "", 9)
    pdf.add_page()

    if not is_positive:
        tomorrow = (datetime.datetime.strptime(date, "%Y-%m-%d") + datetime.timedelta(days=1)).strftime("%d.%m.%Y")
        pdf.set_font("DejaVu", "B", 16)
        pdf.set_text_color(200, 0, 0)
        pdf.cell(0, 12, "Ҳурматли ҳамкор!", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("DejaVu", "", 10)
        pdf.set_text_color(0, 0, 0)
        pdf.multi_cell(0, 6, f"{tomorrow} йилдаги буюртма бўйича кўрсатилган товарлар сизнинг омборингизда мавжуд бўлмаганлиги сабабли тақдим этилмади. Илтимос, ушбу товарларни бошқа омборингиздан етказиб беришни ташкил этинг.", align="J")
        pdf.ln(5)

    headers = ["№", "Наименование товара", "SKU", "Номер заказа", "Штрихкод", "Кол-во", "Статус"]
    widths = [10, 70, 25, 25, 25, 15, 20]
    line_height = 6
    left_margin = (pdf.w - sum(widths)) / 2
    pdf.set_left_margin(left_margin)

    def draw_header():
        pdf.set_fill_color(240, 240, 240)
        for h, w in zip(headers, widths):
            pdf.cell(w, line_height, h, border=1, align="C", fill=True)
        pdf.ln(line_height)

    draw_header()
    for idx, item in enumerate(items, start=1):
        if pdf.get_y() > pdf.h - 50:
            pdf.add_page()
            draw_header()

        row = [str(idx), item.get("product_name", ""), item.get("sku", ""), item.get("order_id", ""), item.get("barcode", ""), str(item.get("quantity", 0)), ("OK" if is_positive else "NO")]
        max_height = max(get_multi_cell_height(pdf, w, t, line_height) for t, w in zip(row, widths))
        y_top = pdf.get_y()
        x_left = left_margin
        for i, (text, w) in enumerate(zip(row, widths)):
            align = "L" if i == 1 else "C"
            pdf.rect(x_left, y_top, w, max_height)
            y_text = y_top + (max_height - get_multi_cell_height(pdf, w, text, line_height)) / 2
            pdf.set_xy(x_left, y_text)
            pdf.multi_cell(w, line_height, text, border=0, align=align)
            x_left += w
        pdf.set_y(y_top + max_height)

    filename = f"{'positive' if is_positive else 'negative'}_report_{date}.pdf"
    pdf.output(filename)
    return filename


# Admin endpoints
@app.post("/create_user")
def create_user(user: User, current_user: dict = Depends(get_current_user)):
    if not users[current_user["id"]].get("is_admin", False):
        raise HTTPException(403, "Admin only")
    hashed_pass = hashlib.sha512(user.password.encode()).hexdigest()  # Configdagi kabi SHA512
    users[user.username] = {
        "username": user.username,
        "password_hash": hashed_pass,
        "status": "active",
        "balance": 0.0,
        "is_admin": user.role == "admin"
    }
    # Config ni yangilash
    CONFIG["users"].append(users[user.username])
    with open(CONFIG_PATH, "w") as f:
        json.dump(CONFIG, f)
    return {"status": "created"}

@app.post("/assign_campaign")
def assign_campaign(username: str, campaign_id: int, current_user: dict = Depends(get_current_user)):
    if not users[current_user["id"]].get("is_admin", False):
        raise HTTPException(403, "Admin only")
    if username not in users:
        raise HTTPException(404, "User not found")
    assigned = users[username].get("assigned_campaigns", [])
    if campaign_id not in assigned:
        assigned.append(campaign_id)
        users[username]["assigned_campaigns"] = assigned
    # Config yangilash (ixtiyoriy, chunki in-memory)
    return {"status": "assigned"}

@app.get("/stats")
def get_stats(current_user: dict = Depends(get_current_user)):
    if not users[current_user["id"]].get("is_admin", False):
        raise HTTPException(403, "Admin only")
    stats = []
    for u in CONFIG["users"]:
        stats.append({
            "username": u["username"],
            "assigned_campaigns": users[u["username"]].get("assigned_campaigns", []),
            "processed_orders": users[u["username"]].get("processed_orders", 0),
            "balance": u["balance"]
        })
    return stats

def scheduled_report():
    # Kunlik hisobot (misol, barcha campaigns bo'yicha)
    print("Kunlik hisobot ishga tushdi at", SCHEDULE_TIME)
    # Logic qo'shing: Orders olish va sheets ga yozish

scheduler = BackgroundScheduler()
scheduler.add_job(scheduled_report, "cron", hour=int(SCHEDULE_TIME.split(":")[0]), minute=int(SCHEDULE_TIME.split(":")[1]))
scheduler.start()

def start_ngrok():
    subprocess.Popen(["ngrok", "http", "8080"])
    time.sleep(2)
    response = requests.get("http://localhost:4040/api/tunnels")
    tunnels = response.json()["tunnels"]
    for tunnel in tunnels:
        if tunnel["proto"] == "https":
            public_url = tunnel["public_url"]
            print(f"Ngrok public URL: {public_url}")
            return public_url
    raise Exception("Ngrok tunnel not found")

if __name__ == "__main__":
    ngrok_thread = threading.Thread(target=start_ngrok)
    ngrok_thread.start()
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)