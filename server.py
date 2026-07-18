import os
import asyncio
import logging
import re
import json
import random
import threading
from flask import Flask, render_template, request, jsonify
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
import sqlite3
from mistralai import Mistral

# ---------- НАСТРОЙКИ (ЗАМЕНИ НА СВОИ) ----------
BOT_TOKEN = "ТВОЙ_ТОКЕН_БОТА"
MISTRAL_API_KEY = "ТВОЙ_API_КЛЮЧ_MISTRAL"
OWNER_ID = 123456789
WEBAPP_URL = "https://ТВОЙ_URL.onrender.com"

mistral_client = Mistral(api_key=MISTRAL_API_KEY)
DB_NAME = "game.db"
ADMIN_DB = "admins.db"

# ---------- FLASK ----------
app = Flask(__name__)

# ---------- БАЗА ДАННЫХ ----------
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS players (user_id INTEGER PRIMARY KEY, state TEXT DEFAULT '{}')")
    conn.commit()
    conn.close()

def get_state(user_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT state FROM players WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return json.loads(row[0]) if row else None

def save_state(user_id, state_dict):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO players (user_id, state) VALUES (?, ?)", (user_id, json.dumps(state_dict)))
    conn.commit()
    conn.close()

# ---------- АДМИН БАЗА ----------
def init_admin_db():
    conn = sqlite3.connect(ADMIN_DB)
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY, level TEXT DEFAULT 'admin')")
    cur.execute("CREATE TABLE IF NOT EXISTS tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, message TEXT, status TEXT DEFAULT 'open', created_at TEXT, reply TEXT DEFAULT '')")
    cur.execute("CREATE TABLE IF NOT EXISTS bans (user_id INTEGER PRIMARY KEY, reason TEXT, banned_by INTEGER, date TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS admin_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER, action TEXT, target_id INTEGER, details TEXT, date TEXT)")
    conn.commit()
    conn.close()

def is_admin(user_id):
    if user_id == OWNER_ID: return 'owner'
    conn = sqlite3.connect(ADMIN_DB)
    cur = conn.cursor()
    cur.execute("SELECT level FROM admins WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None

def check_admin(user_id, min_level='moderator'):
    if user_id == OWNER_ID: return True
    level = is_admin(user_id)
    if not level: return False
    levels = {'moderator': 1, 'admin': 2, 'owner': 3}
    return levels.get(level, 0) >= levels.get(min_level, 0)

def add_admin(user_id, level='admin'):
    conn = sqlite3.connect(ADMIN_DB)
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO admins (user_id, level) VALUES (?, ?)", (user_id, level))
    conn.commit()
    conn.close()

def remove_admin(user_id):
    conn = sqlite3.connect(ADMIN_DB)
    cur = conn.cursor()
    cur.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def get_all_admins():
    conn = sqlite3.connect(ADMIN_DB)
    cur = conn.cursor()
    cur.execute("SELECT user_id, level FROM admins")
    rows = cur.fetchall()
    conn.close()
    return rows

def is_banned(user_id):
    conn = sqlite3.connect(ADMIN_DB)
    cur = conn.cursor()
    cur.execute("SELECT reason FROM bans WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None

def ban_user(user_id, reason, banned_by):
    conn = sqlite3.connect(ADMIN_DB)
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO bans (user_id, reason, banned_by, date) VALUES (?, ?, ?, datetime('now'))", (user_id, reason, banned_by))
    conn.commit()
    conn.close()

def unban_user(user_id):
    conn = sqlite3.connect(ADMIN_DB)
    cur = conn.cursor()
    cur.execute("DELETE FROM bans WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def create_ticket(user_id, username, message):
    conn = sqlite3.connect(ADMIN_DB)
    cur = conn.cursor()
    cur.execute("INSERT INTO tickets (user_id, username, message, status, created_at) VALUES (?, ?, ?, 'open', datetime('now'))", (user_id, username, message))
    conn.commit()
    conn.close()

# ---------- НЕЙРОСЕТЬ ----------
def generate_sync(prompt):
    response = mistral_client.chat.complete(model="mistral-tiny", messages=[{"role": "user", "content": prompt}])
    return response.choices[0].message.content

# ---------- КОНСТАНТЫ ----------
DIFFICULTY = {
    "captain": {"budget": 15000000, "loyalty": 80, "threat": 10, "stability": 70, "trust": 55, "influence": 60, "squads": 3, "army_uses": 2},
    "major": {"budget": 10000000, "loyalty": 65, "threat": 25, "stability": 50, "trust": 45, "influence": 45, "squads": 2, "army_uses": 1},
    "colonel": {"budget": 6000000, "loyalty": 45, "threat": 40, "stability": 30, "trust": 30, "influence": 30, "squads": 1, "army_uses": 0}
}

DISTRICTS = ["Центр", "Северный", "Спальный", "Зелёный", "Промзона", "Старый город", "Заречный", "Вокзальный"]
FACTIONS = ["Теневой совет", "Народный фронт", "Братство", "Медиа-альянс"]

# ======================= FLASK РОУТЫ =======================
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/state')
def api_state():
    state = get_state(request.args.get('user_id', type=int))
    return jsonify(state if state else {"error": "no game"})

@app.route('/api/new_game', methods=['POST'])
def api_new_game():
    data = request.json; user_id = data['user_id']; diff = data.get('difficulty', 'major')
    d = DIFFICULTY[diff]
    state = {
        "day": 1, "budget": d["budget"], "loyalty": d["loyalty"], "threat": d["threat"],
        "stability": d["stability"], "trust": d["trust"], "influence": d["influence"],
        "army_uses_left": d["army_uses"], "curfew": False, "difficulty": diff,
        "riots": {r: random.randint(5, 25) for r in DISTRICTS},
        "squads": [], "hq": {}, "arrests_made": 0,
        "business_contracts": {}, "game_over": False, "day_started": False,
        "pending_actions": [], "last_summary": "", "corruption": 20,
        "special_units": {}, "proc_block": 0, "investigation_progress": 0,
        "actions_today": 0, "max_actions": 3
    }
    squad_names = ["Альфа", "Браво", "Чарли"]
    types = ["спецназ", "патруль", "патруль"]
    for i in range(d["squads"]):
        state["squads"].append({"name": squad_names[i], "location": DISTRICTS[i], "strength": 10 if types[i] == "спецназ" else 6, "morale": 80 if types[i] == "спецназ" else 60, "type": types[i]})
    save_state(user_id, state)
    return jsonify({"status": "ok", "state": state})

@app.route('/api/start_day', methods=['POST'])
def api_start_day():
    data = request.json; user_id = data['user_id']
    state = get_state(user_id)
    if not state or state.get("game_over"): return jsonify({"error": "game not active"})
    if state.get('day_started'): return jsonify({"error": "День уже начат."})
    if 'corruption' not in state: state['corruption'] = 20
    if 'special_units' not in state: state['special_units'] = {}
    if 'proc_block' not in state: state['proc_block'] = 0
    if state['proc_block'] > 0: state['proc_block'] -= 1
    if state['proc_block'] <= 0:
        cg = random.randint(0, 5)
        if 'obep' in state.get('special_units', {}): cg = max(0, cg - 3)
        if 'internal' in state.get('special_units', {}): cg = max(0, cg - 1)
        state['corruption'] = min(100, state.get('corruption', 20) + cg)
    for r in DISTRICTS:
        if not state.get('curfew'):
            g = random.randint(1, 5)
            if 'dps' in state.get('special_units', {}): g = max(0, g - 1)
            state['riots'][r] = min(100, state['riots'][r] + g)
    patrol_income = sum(300000 for s in state['squads'] if s['type'] == 'патруль')
    dps_income = 200000 if 'dps' in state.get('special_units', {}) else 0
    business_income = sum(500000 for r in state.get('business_contracts', {}))
    corruption_loss = int(state['budget'] * (state.get('corruption', 20) / 1000))
    state['budget'] += patrol_income + dps_income + business_income - corruption_loss
    for r in list(state.get('hq', {}).keys()):
        state['hq'][r] -= 1
        if state['hq'][r] <= 0: del state['hq'][r]
    squads_desc = ", ".join([f"{s['name']} в {s['location']}" for s in state['squads']])
    riots_desc = ", ".join([f"{k}: {v}%" for k, v in state['riots'].items()])
    prompt = f"""День {state['day']}. Бюджет: {state['budget']:,} ₽. Угроза: {state['threat']}%. Стабильность: {state['stability']}%. Отряды: {squads_desc}. Бунты: {riots_desc}. Опиши проблему и дай 3 действия. Формат: Сводка: <текст> Действие1: <текст> Действие2: <текст> Действие3: <текст>"""
    response_text = generate_sync(prompt)
    lines = response_text.split('\n')
    summary, actions = "", []
    for line in lines:
        if line.startswith("Сводка:"): summary = line[7:].strip()
        elif line.startswith("Действие1:"): actions.append(line[10:].strip())
        elif line.startswith("Действие2:"): actions.append(line[10:].strip())
        elif line.startswith("Действие3:"): actions.append(line[10:].strip())
    if not summary or len(actions) < 3:
        summary = "Обстановка напряжённая."; actions = ["Подавить бунт", "Проверить бизнес", "Усилить патрули"]
    state['pending_actions'] = actions; state['last_summary'] = summary
    state['day_started'] = True; state['actions_today'] = 0
    save_state(user_id, state)
    return jsonify({"summary": summary, "actions": actions, "day": state['day'], "budget": state['budget'], "income": patrol_income + dps_income + business_income})

@app.route('/api/action', methods=['POST'])
def api_action():
    data = request.json; user_id = data['user_id']; action = data['action']
    state = get_state(user_id)
    if not state or not state.get('day_started'): return jsonify({"error": "День не начат."})
    prompt = f"""Игрок выбрал: "{action}". Бюджет: {state['budget']:,} ₽. Угроза: {state['threat']}%. Стабильность: {state['stability']}%. Опиши результат и укажи: [бюджет:ЧИСЛО, угроза:ЧИСЛО%, стабильность:ЧИСЛО%]"""
    result = generate_sync(prompt)
    match = re.search(r'\[бюджет:(\d+), угроза:(\d+)%, стабильность:(\d+)%\]', result)
    if match:
        state['budget'] = int(match.group(1)); state['threat'] = int(match.group(2)); state['stability'] = int(match.group(3))
    state['actions_today'] += 1; save_state(user_id, state)
    return jsonify({"result": re.sub(r'\[.*?\]', '', result).strip(), "budget": state['budget'], "threat": state['threat'], "stability": state['stability']})

@app.route('/api/end_day', methods=['POST'])
def api_end_day():
    data = request.json; user_id = data['user_id']
    state = get_state(user_id)
    if not state or not state.get('day_started'): return jsonify({"error": "День не начат."})
    night_event = ""
    if random.random() < 0.4:
        night_event = generate_sync(f"Опиши ночное событие (1 предложение). День {state['day']}.").strip()
    game_over_msg = None
    if state['threat'] >= 100: state['game_over'] = True; game_over_msg = "💔 Переворот совершён."
    elif state['stability'] <= 10: state['game_over'] = True; game_over_msg = "💔 Город в хаосе."
    elif state['day'] >= 30: state['game_over'] = True; game_over_msg = "🎉 Вы спасли город!"
    state['day_started'] = False; state['day'] += 1; state['pending_actions'] = []
    save_state(user_id, state)
    return jsonify({"day": state['day'], "night_event": night_event, "game_over": state.get('game_over',False), "game_over_msg": game_over_msg, "budget": state['budget']})

@app.route('/api/extra_action', methods=['POST'])
def api_extra_action():
    data = request.json; user_id = data['user_id']; action_type = data['action_type']
    district = data.get('district', 'Центр'); state = get_state(user_id)
    if not state or not state.get('day_started'): return jsonify({"error": "День не начат."})
    result_msg = ""
    if action_type == "patrol":
        state['riots'][district] = max(0, state['riots'][district] - random.randint(3,8))
        state['budget'] += 300000; result_msg = f"👮 Патруль. +300 000 ₽"
    elif action_type == "raid":
        income = random.randint(500000, 2000000); state['budget'] += income
        result_msg = f"🏪 Рейд. +{income:,} ₽"
    elif action_type == "suppress":
        riot = state['riots'][district]; state['riots'][district] = max(0, riot - random.randint(25,40))
        state['budget'] += random.randint(500000, 2000000); result_msg = "🚨 Бунт подавлен!"
    elif action_type == "hq":
        if state['budget'] < 3000000: return jsonify({"error": "Нужно 3 000 000 ₽."})
        state['budget'] -= 3000000; state['hq'] = state.get('hq',{}); state['hq'][district] = 5
        result_msg = f"🏴 Штаб в {district}."
    elif action_type == "curfew":
        state['curfew'] = not state.get('curfew', False)
        result_msg = "🌙 Комендантский час." if state['curfew'] else "☀️ Отменён."
    elif action_type == "budget":
        if state['budget'] < 10000000: state['budget'] += random.randint(2000000, 8000000); result_msg = "🏛️ Грант получен."
        else: result_msg = "🏛️ Отказано."
    save_state(user_id, state)
    return jsonify({"result": result_msg, "budget": state['budget'], "riots": state['riots']})

@app.route('/api/squad_action', methods=['POST'])
def api_squad_action():
    data = request.json; user_id = data['user_id']; cmd = data['command']; params = data.get('params',{})
    state = get_state(user_id)
    if not state: return jsonify({"error": "no game"})
    result_msg = ""
    if cmd == "form":
        t = params.get('type','патруль'); costs = {"патруль":2000000,"спецназ":3500000}
        if state['budget'] < costs.get(t,2000000): return jsonify({"error": "Мало средств."})
        state['budget'] -= costs[t]; name = f"Отряд-{len(state['squads'])+1}"
        state['squads'].append({"name":name,"location":"Центр","strength":10 if t=="спецназ" else 5,"morale":70,"type":t})
        result_msg = f"✅ {t} «{name}»."
    elif cmd == "move":
        for s in state['squads']:
            if s['name'].lower() == params.get('name','').lower(): s['location'] = params.get('district','Центр'); result_msg = f"🚔 {s['name']} → {s['location']}"; break
    elif cmd == "disband":
        for s in state['squads']:
            if s['name'].lower() == params.get('name','').lower(): state['budget'] += 500000; state['squads'].remove(s); result_msg = "❌ Расформирован."; break
    save_state(user_id, state)
    return jsonify({"result": result_msg, "budget": state['budget'], "squads": state['squads']})

@app.route('/api/new_units', methods=['POST'])
def api_new_units():
    data = request.json; user_id = data['user_id']; unit_type = data['unit_type']
    state = get_state(user_id)
    if not state: return jsonify({"error": "no game"})
    if 'special_units' not in state: state['special_units'] = {}
    costs = {'dps':3000000,'obep':4000000,'sk':5000000,'proc':3500000,'internal':2500000}
    if unit_type not in costs: return jsonify({"error": "Неизвестный тип."})
    if state['budget'] < costs[unit_type]: return jsonify({"error": f"Нужно {costs[unit_type]:,} ₽."})
    state['budget'] -= costs[unit_type]
    state['special_units'][unit_type] = {'name': unit_type, 'active': True}
    save_state(user_id, state)
    return jsonify({"result": f"✅ {unit_type} создан!", "budget": state['budget'], "special_units": state['special_units']})

@app.route('/api/gov_request', methods=['POST'])
def api_gov_request():
    data = request.json; user_id = data['user_id']; structure = data['structure']
    state = get_state(user_id)
    if not state: return jsonify({"error": "no game"})
    result_msg = ""
    if structure == "Армия":
        if state['army_uses_left'] <= 0 or state['threat'] < 75: return jsonify({"error": "Недоступно."})
        state['army_uses_left'] -= 1; state['threat'] = max(0, state['threat']-30)
        for r in DISTRICTS: state['riots'][r] = max(0, state['riots'][r]-40)
        result_msg = "🪖 Армия подавила бунты."
    elif structure == "Росгвардия":
        state['threat'] = max(0, state['threat']-15); result_msg = "⚡ Росгвардия."
    elif structure == "МЧС":
        state['stability'] = min(100, state['stability']+10); result_msg = "🚒 МЧС."
    elif structure == "ФСБ":
        result_msg = f"🕵️ ФСБ: {generate_sync('Разведданные (1 предложение).')}"
    elif structure == "Мэрия":
        g = random.randint(2000000, 8000000); state['budget'] += g; result_msg = f"🏛️ +{g:,} ₽"
    elif structure == "СМИ":
        state['stability'] = min(100, state['stability']+5); result_msg = "📰 Пресс-конференция."
    save_state(user_id, state)
    return jsonify({"result": result_msg, "budget": state['budget']})

# ======================= TELEGRAM BOT =======================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if is_banned(message.from_user.id): await message.answer("⛔ Бан."); return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎮 Играть", web_app=WebAppInfo(url=WEBAPP_URL))]])
    await message.answer("🛡️ «Глава полиции: Час расплаты»\nНажмите кнопку:", reply_markup=kb)

@dp.message(Command("admin"))
async def admin_cmd(message: types.Message):
    if not check_admin(message.from_user.id): return
    kb = [[KeyboardButton(text="📊 Статистика"), KeyboardButton(text="👥 Игроки")],
          [KeyboardButton(text="📢 Рассылка"), KeyboardButton(text="💰 Всем +500k")]]
    if message.from_user.id == OWNER_ID:
        kb.append([KeyboardButton(text="➕ Выдать"), KeyboardButton(text="➖ Забрать")])
    await message.answer("🔧 Админ-панель", reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))

@dp.message(lambda m: check_admin(m.from_user.id) and m.text == "📊 Статистика")
async def a_stats(m: types.Message):
    conn = sqlite3.connect(DB_NAME); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM players"); total = cur.fetchone()[0]; conn.close()
    await m.answer(f"👥 {total} игроков")

@dp.message(lambda m: check_admin(m.from_user.id) and m.text == "💰 Всем +500k")
async def a_bonus(m: types.Message):
    conn = sqlite3.connect(DB_NAME); cur = conn.cursor()
    cur.execute("SELECT user_id, state FROM players"); rows = cur.fetchall(); conn.close()
    for uid, s in rows:
        st = json.loads(s)
        if not st.get('game_over'): st['budget'] = st.get('budget',0) + 500000; save_state(uid, st)
    await m.answer("✅ +500k всем.")

@dp.message(lambda m: check_admin(m.from_user.id) and m.text == "📢 Рассылка")
async def a_broadcast_prompt(m: types.Message):
    s = get_state(m.from_user.id) or {}; s['admin_action'] = 'broadcast'; save_state(m.from_user.id, s)
    await m.answer("Текст:")

@dp.message(lambda m: m.from_user.id == OWNER_ID and m.text == "➕ Выдать")
async def a_add_prompt(m: types.Message):
    s = get_state(m.from_user.id) or {}; s['admin_action'] = 'add_admin'; save_state(m.from_user.id, s)
    await m.answer("ID admin")

@dp.message(lambda m: m.from_user.id == OWNER_ID and m.text == "➖ Забрать")
async def a_remove_prompt(m: types.Message):
    s = get_state(m.from_user.id) or {}; s['admin_action'] = 'remove_admin'; save_state(m.from_user.id, s)
    await m.answer("ID")

@dp.message(lambda m: check_admin(m.from_user.id))
async def a_handle(m: types.Message):
    s = get_state(m.from_user.id)
    if not s or 'admin_action' not in s: return
    action = s['admin_action']; del s['admin_action']; save_state(m.from_user.id, s)
    text = m.text.strip()
    if action == 'broadcast':
        conn = sqlite3.connect(DB_NAME); cur = conn.cursor(); cur.execute("SELECT user_id FROM players"); rows = cur.fetchall(); conn.close()
        c = 0
        for (uid,) in rows:
