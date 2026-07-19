import os
import asyncio
import re
import json
import random
from flask import Flask, render_template, request, jsonify
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
import sqlite3

BOT_TOKEN = "8624146065:AAFSQBQWda56KNr72HEKEmpqprFMhJynw1g"
OWNER_ID = 8155407559
WEBAPP_URL = "https://police-bot-94sa.onrender.com"

DB_NAME = "game.db"
app = Flask(__name__)

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
    if "Сводка:" in prompt:
        return ("Сводка: В городе напряжённая обстановка. Возможны стычки в Промзоне.\n"
                "Действие1: Подавить бунт в Промзоне\n"
                "Действие2: Усилить патрули в Центре\n"
                "Действие3: Провести переговоры с активистами")
    else:
        return "Операция прошла успешно. [бюджет:9500000, угроза:30%, стабильность:55%]"

DISTRICTS = ["Центр", "Северный", "Спальный", "Зелёный", "Промзона", "Старый город", "Заречный", "Вокзальный"]
DIFFICULTY = {
    "captain": {"budget": 15000000, "loyalty": 80, "threat": 10, "stability": 70, "squads": 3, "army": 2},
    "major": {"budget": 10000000, "loyalty": 65, "threat": 25, "stability": 50, "squads": 2, "army": 1},
    "colonel": {"budget": 6000000, "loyalty": 45, "threat": 40, "stability": 30, "squads": 1, "army": 0}
}

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
    prompt = f"Сводка: день {state['day']}"
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
    result_text = generate_sync(action)
    match = re.search(r'\[бюджет:(\d+), угроза:(\d+)%, стабильность:(\d+)%\]', result_text)
    if match:
        state['budget'] = int(match.group(1)); state['threat'] = int(match.group(2)); state['stability'] = int(match.group(3))
    state['actions_today'] += 1; save_state(user_id, state)
    return jsonify({"result": re.sub(r'\[.*?\]', '', result_text).strip(), "budget": state['budget'], "threat": state['threat'], "stability": state['stability']})

@app.route('/api/end_day', methods=['POST'])
def api_end_day():
    data = request.json; user_id = data['user_id']
    state = get_state(user_id)
    if not state or not state.get('day_started'): return jsonify({"error": "День не начат."})
    night_event = random.choice(["Ночь прошла спокойно.", "Слышны выстрелы в Промзоне.", "Патруль задержал подозрительную группу."])
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

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎮 Играть", web_app=WebAppInfo(url=WEBAPP_URL))]])
    await message.answer("🛡️ «Глава полиции: Час расплаты»\nНажмите кнопку:", reply_markup=kb)

@app.route('/webhook', methods=['GET', 'POST'])
def flask_webhook():
    if request.method == 'GET':
        return jsonify({"status": "webhook is ready"})
    import asyncio as aio
    loop = aio.new_event_loop()
    aio.set_event_loop(loop)
    loop.run_until_complete(dp.feed_webhook_update(bot, request.get_json()))
    loop.close()
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get('PORT', 8080))
    async def set_webhook():
        try:
            await bot.delete_webhook()
            await bot.set_webhook(f"{WEBAPP_URL}/webhook")
            print("Webhook set OK")
        except Exception as e:
            print(f"Webhook error: {e}")
    asyncio.run(set_webhook())
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)