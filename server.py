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
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
import sqlite3
from mistralai import Mistral

# ---------- НАСТРОЙКИ ----------
BOT_TOKEN = "8624146065:AAFSQBQWda56KNr72HEKEmpqprFMhJynw1g"
MISTRAL_API_KEY = "Tz5j4fK10kC7iPfovOfXvKdb4RMdY5ZH"
OWNER_ID = 8155407559
WEBAPP_URL = "https://police-bot-94sa.onrender.com"

mistral_client = Mistral(api_key=MISTRAL_API_KEY)
DB_NAME = "game.db"
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

def generate_sync(prompt):
    response = mistral_client.chat.complete(model="mistral-tiny", messages=[{"role": "user", "content": prompt}])
    return response.choices[0].message.content

# ---------- КОНСТАНТЫ ----------
DISTRICTS = ["Центр", "Северный", "Спальный", "Зелёный", "Промзона", "Старый город", "Заречный", "Вокзальный"]
DIFFICULTY = {
    "captain": {"budget": 15000000, "loyalty": 80, "threat": 10, "stability": 70, "squads": 3, "army": 2},
    "major": {"budget": 10000000, "loyalty": 65, "threat": 25, "stability": 50, "squads": 2, "army": 1},
    "colonel": {"budget": 6000000, "loyalty": 45, "threat": 40, "stability": 30, "squads": 1, "army": 0}
}

# ---------- FLASK РОУТЫ ----------
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
        "stability": d["stability"], "trust": 50, "influence": 50, "army_uses": d["army"],
        "curfew": False, "difficulty": diff, "riots": {r: random.randint(5, 25) for r in DISTRICTS},
        "squads": [], "hq": {}, "game_over": False, "day_started": False,
        "pending_actions": [], "last_summary": "", "actions_today": 0, "max_actions": 3
    }
    names = ["Альфа", "Браво", "Чарли"]
    for i in range(d["squads"]):
        state["squads"].append({"name": names[i], "location": DISTRICTS[i], "strength": 10 if i == 0 else 6, "morale": 80 if i == 0 else 60, "type": "спецназ" if i == 0 else "патруль"})
    save_state(user_id, state)
    return jsonify({"status": "ok", "state": state})

@app.route('/api/start_day', methods=['POST'])
def api_start_day():
    data = request.json; user_id = data['user_id']
    state = get_state(user_id)
    if not state or state.get("game_over"): return jsonify({"error": "game not active"})
    if state.get('day_started'): return jsonify({"error": "День уже начат."})
    for r in DISTRICTS:
        if not state.get('curfew'): state['riots'][r] = min(100, state['riots'][r] + random.randint(1, 5))
    income = sum(300000 for s in state['squads'] if s['type'] == 'патруль')
    state['budget'] += income
    for r in list(state.get('hq', {}).keys()):
        state['hq'][r] -= 1
        if state['hq'][r] <= 0: del state['hq'][r]
    squads_desc = ", ".join([f"{s['name']} в {s['location']}" for s in state['squads']])
    riots_desc = ", ".join([f"{k}: {v}%" for k, v in state['riots'].items()])
    prompt = f"""День {state['day']}. Бюджет: {state['budget']:,} ₽. Угроза: {state['threat']}%. Стабильность: {state['stability']}%. Отряды: {squads_desc}. Бунты: {riots_desc}. Опиши проблему и 3 действия. Формат: Сводка: <текст> Действие1: <текст> Действие2: <текст> Действие3: <текст>"""
    response_text = generate_sync(prompt)
    lines = response_text.split('\n')
    summary, actions = "", []
    for line in lines:
        if line.startswith("Сводка:"): summary = line[7:].strip()
        elif line.startswith("Действие1:"): actions.append(line[10:].strip())
        elif line.startswith("Действие2:"): actions.append(line[10:].strip())
        elif line.startswith("Действие3:"): actions.append(line[10:].strip())
    if not summary or len(actions) < 3:
        summary = "Обстановка напряжённая."; actions = ["Подавить бунт", "Усилить патрули", "Провести переговоры"]
    state['pending_actions'] = actions; state['last_summary'] = summary
    state['day_started'] = True; state['actions_today'] = 0
    save_state(user_id, state)
    return jsonify({"summary": summary, "actions": actions, "day": state['day'], "budget": state['budget'], "income": income})

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
    if random.random() < 0.4: night_event = generate_sync("Опиши ночное событие в городе (1 предложение).").strip()
    game_over_msg = None
    if state['threat'] >= 100: state['game_over'] = True; game_over_msg = "💔 Переворот."
    elif state['stability'] <= 10: state['game_over'] = True; game_over_msg = "💔 Хаос."
    elif state['day'] >= 30: state['game_over'] = True; game_over_msg = "🎉 Победа!"
    state['day_started'] = False; state['day'] += 1; state['pending_actions'] = []
    save_state(user_id, state)
    return jsonify({"day": state['day'], "night_event": night_event, "game_over": state.get('game_over',False), "game_over_msg": game_over_msg})

@app.route('/api/extra_action', methods=['POST'])
def api_extra_action():
    data = request.json; user_id = data['user_id']; action_type = data['action_type']
    district = data.get('district', 'Центр'); state = get_state(user_id)
    if not state or not state.get('day_started'): return jsonify({"error": "День не начат."})
    result_msg = ""
    if action_type == "patrol": state['riots'][district] = max(0, state['riots'][district] - random.randint(3,8)); state['budget'] += 300000; result_msg = "👮 Патруль."
    elif action_type == "suppress": state['riots'][district] = max(0, state['riots'][district] - random.randint(25,40)); state['budget'] += random.randint(500000, 2000000); result_msg = "🚨 Бунт подавлен!"
    elif action_type == "hq":
        if state['budget'] < 3000000: return jsonify({"error": "Нужно 3 000 000 ₽."})
        state['budget'] -= 3000000; state['hq'] = state.get('hq',{}); state['hq'][district] = 5; result_msg = "🏴 Штаб."
    elif action_type == "curfew": state['curfew'] = not state.get('curfew', False); result_msg = "🌙 Комендантский час." if state['curfew'] else "☀️ Отменён."
    elif action_type == "army":
        if state.get('army_uses',0) <= 0 or state['threat'] < 75: return jsonify({"error": "Недоступно."})
        state['army_uses'] -= 1; state['threat'] = max(0, state['threat']-30)
        for r in DISTRICTS: state['riots'][r] = max(0, state['riots'][r]-40); result_msg = "🪖 Армия."
    save_state(user_id, state)
    return jsonify({"result": result_msg, "budget": state['budget'], "riots": state['riots'], "threat": state['threat']})

@app.route('/api/squad_action', methods=['POST'])
def api_squad_action():
    data = request.json; user_id = data['user_id']; cmd = data['command']; params = data.get('params',{})
    state = get_state(user_id)
    if not state: return jsonify({"error": "no game"})
    result_msg = ""
    if cmd == "form":
        if state['budget'] < 2000000: return jsonify({"error": "Нужно 2 000 000 ₽."})
        state['budget'] -= 2000000; name = f"Отряд-{len(state['squads'])+1}"
        state['squads'].append({"name":name,"location":"Центр","strength":5,"morale":60,"type":"патруль"}); result_msg = f"✅ {name}"
    elif cmd == "move":
        for s in state['squads']:
            if s['name'].lower() == params.get('name','').lower(): s['location'] = params.get('district','Центр'); result_msg = f"🚔 {s['name']} → {s['location']}"; break
    elif cmd == "disband":
        for s in state['squads']:
            if s['name'].lower() == params.get('name','').lower(): state['budget'] += 500000; state['squads'].remove(s); result_msg = "❌ Расформирован."; break
    save_state(user_id, state)
    return jsonify({"result": result_msg, "budget": state['budget'], "squads": state['squads']})

# ---------- TELEGRAM BOT ----------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎮 Играть", web_app=WebAppInfo(url=WEBAPP_URL))]])
    await message.answer("🛡️ «Глава полиции: Час расплаты»\nНажмите кнопку:", reply_markup=kb)

@dp.message(Command("admin"))
async def admin_cmd(message: types.Message):
    if message.from_user.id != OWNER_ID: return
    conn = sqlite3.connect(DB_NAME); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM players"); total = cur.fetchone()[0]; conn.close()
    await message.answer(f"👥 Игроков: {total}\n💰 Всем +500k: /bonus")

@dp.message(Command("bonus"))
async def bonus_cmd(message: types.Message):
    if message.from_user.id != OWNER_ID: return
    conn = sqlite3.connect(DB_NAME); cur = conn.cursor()
    cur.execute("SELECT user_id, state FROM players"); rows = cur.fetchall(); conn.close()
    for uid, s in rows:
        st = json.loads(s)
        if not st.get('game_over'): st['budget'] = st.get('budget',0) + 500000; save_state(uid, st)
    await message.answer("✅ +500k всем.")

# ---------- ЗАПУСК ----------
async def run_bot():
    await dp.start_polling(bot)

def start_bot():
    asyncio.run(run_bot())

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get('PORT', 8080))
    threading.Thread(target=start_bot, daemon=True).start()
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)