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
BOT_TOKEN = "8624146065:AAFSQBQWda56KNr72HEKEmpqprFMhJynw1g"
MISTRAL_API_KEY = "Tz5j4fK10kC7iPfovOfXvKdb4RMdY5ZH"
OWNER_ID = 8155407559
WEBAPP_URL = "https://police-bot-94sa.onrender.com"
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

def get_all_bans():
    conn = sqlite3.connect(ADMIN_DB)
    cur = conn.cursor()
    cur.execute("SELECT user_id, reason, banned_by, date FROM bans")
    rows = cur.fetchall()
    conn.close()
    return rows

def add_log(admin_id, action, target_id, details=""):
    conn = sqlite3.connect(ADMIN_DB)
    cur = conn.cursor()
    cur.execute("INSERT INTO admin_logs (admin_id, action, target_id, details, date) VALUES (?, ?, ?, ?, datetime('now'))", (admin_id, action, target_id, details))
    conn.commit()
    conn.close()

def get_logs(limit=20):
    conn = sqlite3.connect(ADMIN_DB)
    cur = conn.cursor()
    cur.execute("SELECT admin_id, action, target_id, details, date FROM admin_logs ORDER BY id DESC LIMIT ?", (limit,))
    rows = cur.fetchall()
    conn.close()
    return rows

def create_ticket(user_id, username, message):
    conn = sqlite3.connect(ADMIN_DB)
    cur = conn.cursor()
    cur.execute("INSERT INTO tickets (user_id, username, message, status, created_at) VALUES (?, ?, ?, 'open', datetime('now'))", (user_id, username, message))
    tid = cur.lastrowid
    conn.commit()
    conn.close()
    return tid

def get_open_tickets():
    conn = sqlite3.connect(ADMIN_DB)
    cur = conn.cursor()
    cur.execute("SELECT id, user_id, username, message, created_at FROM tickets WHERE status='open' ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()
    return rows

def reply_ticket(ticket_id, reply_text):
    conn = sqlite3.connect(ADMIN_DB)
    cur = conn.cursor()
    cur.execute("UPDATE tickets SET reply=?, status='closed' WHERE id=?", (reply_text, ticket_id))
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
    user_id = request.args.get('user_id', type=int)
    state = get_state(user_id)
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
        "squads": [], "hq": {}, "arrests_made": 0, "informants": {},
        "relations": {s: 50 for s in ["Армия", "Росгвардия", "МЧС", "ФСБ", "Мэрия", "СМИ"]},
        "factions_active": {f: random.randint(10, 40) for f in FACTIONS},
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
    units_desc = ", ".join([v['name'] for v in state.get('special_units', {}).values()]) if state.get('special_units') else "нет"
    prompt = f"""Ты — штабной аналитик. День {state['day']}. Бюджет: {state['budget']:,} ₽. Угроза: {state['threat']}%. Стабильность: {state['stability']}%. Доверие: {state['trust']}%. Влияние: {state['influence']}%. Коррупция: {state.get('corruption',20)}%. Отряды: {squads_desc}. Спецподразделения: {units_desc}. Бунты: {riots_desc}. Опиши проблему дня и дай 3 действия. Формат: Сводка: <текст> Действие1: <текст> Действие2: <текст> Действие3: <текст>"""
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
    state['day_started'] = True; state['actions_today'] = 0; state['max_actions'] = 3
    save_state(user_id, state)
    return jsonify({"summary": summary, "actions": actions, "day": state['day'], "budget": state['budget'], "income": patrol_income + dps_income + business_income, "corruption": state.get('corruption',20), "actions_left": 3, "special_units": state.get('special_units',{})})

@app.route('/api/action', methods=['POST'])
def api_action():
    data = request.json; user_id = data['user_id']; action = data['action']
    state = get_state(user_id)
    if not state or not state.get('day_started'): return jsonify({"error": "День не начат."})
    max_acts = state['max_actions'] + (1 if state.get('hq') else 0)
    if state['actions_today'] >= max_acts: return jsonify({"error": "Лимит действий."})
    prompt = f"""Игрок выбрал: "{action}". Бюджет: {state['budget']:,} ₽. Угроза: {state['threat']}%. Стабильность: {state['stability']}%. Доверие: {state['trust']}%. Влияние: {state['influence']}%. Опиши результат и укажи: [бюджет:ЧИСЛО, лояльность:ЧИСЛО%, угроза:ЧИСЛО%, стабильность:ЧИСЛО%, доверие:ЧИСЛО%, влияние:ЧИСЛО%]"""
    result = generate_sync(prompt)
    match = re.search(r'\[бюджет:(\d+), лояльность:(\d+)%, угроза:(\d+)%, стабильность:(\d+)%, доверие:(\d+)%, влияние:(\d+)%\]', result)
    if match:
        state['budget'] = int(match.group(1)); state['loyalty'] = int(match.group(2))
        state['threat'] = int(match.group(3)); state['stability'] = int(match.group(4))
        state['trust'] = int(match.group(5)); state['influence'] = int(match.group(6))
    state['actions_today'] += 1; save_state(user_id, state)
    return jsonify({"result": re.sub(r'\[.*?\]', '', result).strip(), "budget": state['budget'], "threat": state['threat'], "stability": state['stability'], "trust": state['trust'], "influence": state['influence'], "actions_left": max_acts - state['actions_today']})

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
    elif state['loyalty'] <= 10: state['game_over'] = True; game_over_msg = "💔 Силовики перешли к мятежникам."
    elif state['stability'] <= 10: state['game_over'] = True; game_over_msg = "💔 Город в хаосе."
    elif state['trust'] <= 5: state['game_over'] = True; game_over_msg = "💔 Массовые протесты."
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
        if not [s for s in state['squads'] if s['location'] == district]: return jsonify({"error": "Нет отрядов."})
        state['riots'][district] = max(0, state['riots'][district] - random.randint(3,8))
        state['budget'] += 300000; result_msg = f"👮 Патруль. +300 000 ₽"
    elif action_type == "raid":
        if not [s for s in state['squads'] if s['location'] == district]: return jsonify({"error": "Нет отрядов."})
        income = random.randint(500000, 2000000); state['budget'] += income
        state['trust'] = max(0, state['trust'] - random.randint(3,8)); result_msg = f"🏪 Рейд. +{income:,} ₽"
    elif action_type == "suppress":
        squads = [s for s in state['squads'] if s['location'] == district]
        if not squads: return jsonify({"error": "Нет отрядов."})
        strength = sum(s['strength'] for s in squads); riot = state['riots'][district]
        if strength >= riot / 2:
            state['riots'][district] = max(0, riot - random.randint(25,40)); state['budget'] += random.randint(500000, 2000000)
            result_msg = "🚨 Бунт подавлен!"
        else:
            state['riots'][district] = min(100, riot + random.randint(5,15))
            for s in squads: s['strength'] = max(1, s['strength'] - random.randint(1,3))
            result_msg = "❌ Не хватило сил!"
    elif action_type == "hq":
        if district in state.get('hq',{}): return jsonify({"error": "Штаб уже есть."})
        if state['budget'] < 3000000: return jsonify({"error": "Нужно 3 000 000 ₽."})
        state['budget'] -= 3000000; state['hq'] = state.get('hq',{}); state['hq'][district] = 5
        result_msg = f"🏴 Штаб в {district}."
    elif action_type == "checkpoint":
        if state['budget'] < 1000000: return jsonify({"error": "Нужно 1 000 000 ₽."})
        state['budget'] -= 1000000; state['riots'][district] = max(0, state['riots'][district] - random.randint(10,20))
        result_msg = f"🚧 Блокпост в {district}."
    elif action_type == "curfew":
        state['curfew'] = not state.get('curfew', False)
        if state['curfew']:
            for r in DISTRICTS: state['riots'][r] = max(0, state['riots'][r] - random.randint(5,15))
            state['trust'] = max(0, state['trust'] - 10); result_msg = "🌙 Комендантский час."
        else: state['trust'] = min(100, state['trust'] + 5); result_msg = "☀️ Отменён."
    elif action_type == "intel":
        if state['budget'] < 500000: return jsonify({"error": "Нужно 500 000 ₽."})
        state['budget'] -= 500000; result_msg = f"🕵️ {generate_sync('Разведсводка (1 предложение).')}"
    elif action_type == "budget":
        if state['influence'] > 40:
            g = random.randint(2000000, 8000000); state['budget'] += g; result_msg = f"🏛️ Грант +{g:,} ₽"
        else: result_msg = "🏛️ Отказано."
    save_state(user_id, state)
    return jsonify({"result": result_msg, "budget": state['budget'], "riots": state['riots'], "stability": state['stability'], "trust": state['trust']})

@app.route('/api/squad_action', methods=['POST'])
def api_squad_action():
    data = request.json; user_id = data['user_id']; cmd = data['command']; params = data.get('params',{})
    state = get_state(user_id)
    if not state: return jsonify({"error": "no game"})
    result_msg = ""
    if cmd == "form":
        t = params.get('type','патруль'); costs = {"патруль":2000000,"спецназ":3500000,"переговорщики":1500000,"кинологи":2500000}
        if state['budget'] < costs.get(t,2000000): return jsonify({"error": "Мало средств."})
        state['budget'] -= costs[t]; name = f"Отряд-{len(state['squads'])+1}"
        state['squads'].append({"name":name,"location":"Центр","strength":10 if t=="спецназ" else 5,"morale":70,"type":t})
        result_msg = f"✅ {t} «{name}»."
    elif cmd == "move":
        for s in state['squads']:
            if s['name'].lower() == params.get('name','').lower(): s['location'] = params.get('district','Центр'); result_msg = f"🚔 {s['name']} → {s['location']}"; break
    elif cmd == "reinforce":
        if state['budget'] < 1500000: return jsonify({"error": "Нужно 1 500 000 ₽."})
        for s in state['squads']:
            if s['name'].lower() == params.get('name','').lower(): state['budget'] -= 1500000; s['strength'] += 5; result_msg = f"💪 {s['name']} усилен."; break
    elif cmd == "disband":
        for s in state['squads']:
            if s['name'].lower() == params.get('name','').lower(): state['budget'] += 500000; state['squads'].remove(s); result_msg = f"❌ Расформирован."; break
    save_state(user_id, state)
    return jsonify({"result": result_msg, "budget": state['budget'], "squads": state['squads']})

@app.route('/api/new_units', methods=['POST'])
def api_new_units():
    data = request.json; user_id = data['user_id']; unit_type = data['unit_type']
    state = get_state(user_id)
    if not state: return jsonify({"error": "no game"})
    if 'special_units' not in state: state['special_units'] = {}
    costs = {'dps':3000000,'obep':4000000,'sk':5000000,'proc':3500000,'internal':2500000}
    names = {'dps':'ДПС','obep':'ОБЭП','sk':'СК','pro