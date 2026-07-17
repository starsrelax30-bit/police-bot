import asyncio
import logging
import re
import json
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
import sqlite3

# ---------- НАСТРОЙКИ (ЗАМЕНИ НА СВОИ) ----------
BOT_TOKEN = "8624146065:AAFSQBQWda56KNr72HEKEmpqprFMhJynw1g"
# -------------------------------------------------

DB_NAME = "game.db"

# ---------- БАЗА ДАННЫХ ----------
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS players (
            user_id INTEGER PRIMARY KEY,
            state TEXT DEFAULT '{}'
        )
    """)
    conn.commit()
    conn.close()

def get_state(user_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT state FROM players WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        return json.loads(row[0])
    return None

def save_state(user_id, state_dict):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO players (user_id, state) VALUES (?, ?)",
                (user_id, json.dumps(state_dict)))
    conn.commit()
    conn.close()

# ---------- ГЕНЕРАЦИЯ ОТВЕТОВ (БЕЗ ИИ, ЗАГОТОВКИ) ----------
async def generate_content(prompt):
    if "Сводка:" in prompt:
        return ("Сводка: В городе назревают беспорядки. Оппозиция планирует митинг в центре.\n"
                "Действие1: Отправить спецназ для подавления\n"
                "Действие2: Провести переговоры с лидерами\n"
                "Действие3: Укрепить блокпосты по периметру")
    else:
        import random
        budget = random.randint(80, 110)
        loyalty = random.randint(50, 80)
        threat = random.randint(10, 50)
        return (f"Операция проведена. Ситуация немного изменилась.\n"
                f"[бюджет:{budget}, лояльность:{loyalty}%, угроза:{threat}%]")

# ---------- БОТ ----------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "🛡️ Добро пожаловать в игру «Глава полиции: Час расплаты»!\n"
        "Вы — начальник полиции города, где зреет переворот.\n"
        "Используйте /new_game, чтобы начать новую операцию."
    )

@dp.message(Command("new_game"))
async def new_game(message: types.Message):
    state = {
        "day": 1,
        "budget": 100,
        "loyalty": 70,
        "threat": 20,
        "squads": [{"name": "Альфа", "location": "Центр", "strength": 10}],
        "game_over": False
    }
    save_state(message.from_user.id, state)
    await message.answer(
        "📅 День 1. Город на грани волнений.\n"
        "💰 Бюджет: 100 | 👮 Лояльность: 70% | ⚠️ Угроза: 20%\n"
        "🚔 Ваш отряд — «Альфа» в Центре.\n\n"
        "/next_day — получить сводку и действовать\n"
        "/status — текущее положение\n"
        "/form_squad — создать новый отряд (20 бюджета)\n"
        "/move Название Район — переместить отряд"
    )

@dp.message(Command("status"))
async def status(message: types.Message):
    state = get_state(message.from_user.id)
    if not state:
        await message.answer("Игра не начата. Используйте /new_game.")
        return
    squads_str = "; ".join([f"{s['name']} ({s['location']}, сила {s['strength']})" for s in state['squads']])
    await message.answer(
        f"📊 День {state['day']}\n"
        f"💰 Бюджет: {state['budget']}\n"
        f"👮 Лояльность: {state['loyalty']}%\n"
        f"⚠️ Угроза: {state['threat']}%\n"
        f"🚔 Отряды: {squads_str}"
    )

@dp.message(Command("form_squad"))
async def form_squad(message: types.Message):
    state = get_state(message.from_user.id)
    if not state or state['game_over']:
        await message.answer("Игра не активна. /new_game")
        return
    cost = 20
    if state['budget'] < cost:
        await message.answer(f"Недостаточно бюджета. Требуется {cost}.")
        return
    new_name = f"Отряд-{len(state['squads'])+1}"
    state['budget'] -= cost
    state['squads'].append({"name": new_name, "location": "Центр", "strength": 8})
    save_state(message.from_user.id, state)
    await message.answer(f"✅ Сформирован новый отряд «{new_name}» в Центре. Бюджет: {state['budget']}")

@dp.message(Command("move"))
async def move_squad(message: types.Message):
    args = message.text.split()
    if len(args) != 3:
        await message.answer("Формат: /move НазваниеОтряда Район\nПример: /move Альфа Северный")
        return
    squad_name = args[1]
    district = args[2]
    state = get_state(message.from_user.id)
    if not state:
        return
    for squad in state['squads']:
        if squad['name'].lower() == squad_name.lower():
            squad['location'] = district
            save_state(message.from_user.id, state)
            await message.answer(f"🚔 {squad['name']} переброшен в {district}.")
            return
    await message.answer("Отряд не найден.")

@dp.message(Command("next_day"))
async def next_day(message: types.Message):
    state = get_state(message.from_user.id)
    if not state or state["game_over"]:
        await message.answer("Игра не активна. /new_game")
        return

    squads_desc = ", ".join([f"{s['name']} в {s['location']} (сила {s['strength']})" for s in state['squads']])
    prompt = f"""Сводка: день {state['day']}, бюджет {state['budget']}, лояльность {state['loyalty']}%, угроза {state['threat']}%."""

    response_text = await generate_content(prompt)
    lines = response_text.split('\n')
    summary = ""
    actions = []
    for line in lines:
        if line.startswith("Сводка:"):
            summary = line[7:].strip()
        elif line.startswith("Действие1:"):
            actions.append(line[10:].strip())
        elif line.startswith("Действие2:"):
            actions.append(line[10:].strip())
        elif line.startswith("Действие3:"):
            actions.append(line[10:].strip())

    if not summary or len(actions) < 3:
        summary = "Обстановка напряжённая."
        actions = ["Усилить патрули", "Провести переговоры", "Собрать разведданные"]

    state['pending_actions'] = actions
    save_state(message.from_user.id, state)

    kb = [
        [KeyboardButton(text=actions[0])],
        [KeyboardButton(text=actions[1])],
        [KeyboardButton(text=actions[2])],
        [KeyboardButton(text="/status")]
    ]
    keyboard = ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True, one_time_keyboard=False)
    await message.answer(
        f"📅 День {state['day']}\n\n{summary}\n\nВыберите действие:",
        reply_markup=keyboard
    )

@dp.message()
async def handle_action(message: types.Message):
    state = get_state(message.from_user.id)
    if not state or "pending_actions" not in state:
        await message.answer("Используйте /next_day для продолжения игры.")
        return

    chosen_action = message.text
    if chosen_action not in state['pending_actions']:
        await message.answer("Пожалуйста, выберите действие кнопкой.")
        return

    result_prompt = f"Результат действия: {chosen_action}"
    result_text = await generate_content(result_prompt)

    match = re.search(r'\[бюджет:(\d+), лояльность:(\d+)%, угроза:(\d+)%\]', result_text)
    if match:
        state['budget'] = int(match.group(1))
        state['loyalty'] = int(match.group(2))
        state['threat'] = int(match.group(3))
    state['day'] += 1
    del state['pending_actions']

    if state['threat'] >= 100:
        state['game_over'] = True
        await message.answer("💔 Мятежники захватили город. Вас арестовывают. Игра окончена.", reply_markup=types.ReplyKeyboardRemove())
    elif state['loyalty'] <= 10:
        state['game_over'] = True
        await message.answer("💔 Силовики перешли на сторону оппозиции. Вы отстранены. Конец.", reply_markup=types.ReplyKeyboardRemove())
    elif state['day'] > 30:
        state['game_over'] = True
        await message.answer("🎉 Вы продержались 30 дней! Переворот предотвращён. Поздравляем!", reply_markup=types.ReplyKeyboardRemove())
    else:
        clean_result = re.sub(r'\[.*?\]', '', result_text).strip()
        await message.answer(f"{clean_result}\n\nДень {state['day']}. Бюджет: {state['budget']}. Лояльность: {state['loyalty']}%. Угроза: {state['threat']}%.\nИспользуйте /next_day.")

    save_state(message.from_user.id, state)

# ---------- ЗАПУСК ----------
async def main():
    init_db()
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())