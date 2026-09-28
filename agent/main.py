import os
import io
import re
import difflib
import json
import logging
import warnings
import asyncio
from dotenv import load_dotenv

from fastapi import FastAPI, UploadFile, File, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
import uvicorn

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.fsm.storage.memory import MemoryStorage

from google import genai
from google.genai import types as genai_types
from gtts import gTTS

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, '.env'))

GEMINI_API_KEY = os.getenv('Gemini_API_KEY')
TGBOT_API_KEY = os.getenv('TGBOT_API_KEY')

ai_client = genai.Client(api_key=GEMINI_API_KEY)
PRIMARY_MODELS = ['gemini-3.5-flash-lite', 'gemini-3.8-flash', 'gemini-3.6-flash', 'gemini-3.5-flash']

bot = Bot(token=TGBOT_API_KEY)
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()

def clean_teacher_text(text):
    return re.sub(r'[“"«]?[\._]?3\.png[”"»]?', '', text).strip()

def get_teacher_media(text):
    if re.search(r'[\._]?3\.png', text):
        return os.path.join(BASE_DIR, 'Databse of knowleages', '_3.png')
    return None

def get_reference_image_path(expected_ans):
    if "1.png" in expected_ans:
        return os.path.join(BASE_DIR, 'Databse of knowleages', '1.png')
    elif "2.png" in expected_ans:
        return os.path.join(BASE_DIR, 'Databse of knowleages', '2.png')
    return None

async def send_teacher_question(chat_id, text, reply_markup=None):
    media_path = get_teacher_media(text)
    clean_text = clean_teacher_text(text)
    if media_path and os.path.exists(media_path):
        photo = FSInputFile(media_path)
        await bot.send_photo(chat_id=chat_id, photo=photo, caption=clean_text, reply_markup=reply_markup)
    else:
        await bot.send_message(chat_id=chat_id, text=clean_text, reply_markup=reply_markup)

templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Load knowledge
with open(os.path.join(BASE_DIR, 'rate_answer.txt'), 'r', encoding='utf-8') as f:
    rate_answer_text = f.read()
with open(os.path.join(BASE_DIR, 'self_agent.txt'), 'r', encoding='utf-8') as f:
    self_agent_text = f.read()
with open(os.path.join(BASE_DIR, 'promts.txt'), 'r', encoding='utf-8') as f:
    promts_text = f.read()

with open(os.path.join(BASE_DIR, 'promts_LLM.json'), 'r', encoding='utf-8') as f:
    llm_prompts = json.load(f)

with open(os.path.join(BASE_DIR, 'Databse of knowleages', 'зависимость периода колебаний пружинного маятника от массы груза.json'), 'r', encoding='utf-8') as f:
    knowledge_db = json.load(f)
questions = knowledge_db.get("rows", [])

# Shared sessions
sessions = {}

def get_session(user_id):
    if user_id not in sessions:
        sessions[user_id] = {"current_q": 0, "grades": [], "mode": "study", "generate_history": [], "topic": ""}
    return sessions[user_id]

def is_drawing_question(q_data):
    # If the answer contains png, it means we expect a picture/drawing
    return ".png" in q_data.get("answer", "").lower() or "изобрази" in q_data.get("teacher", "").lower()

def save_session_log(user_id, teacher_q, student_ans, grade, llm_reply):
    log_path = os.path.join(BASE_DIR, f'student_session_answers_{user_id}.txt')
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(f"Вопрос учителя: {teacher_q}\n")
        f.write(f"Ответ ученика: {student_ans}\n")
        f.write(f"Оценка: {grade}\n")
        f.write(f"Комментарий учителя: {llm_reply}\n")
        f.write("-" * 40 + "\n")

def read_session_log(user_id):
    log_path = os.path.join(BASE_DIR, f'student_session_answers_{user_id}.txt')
    if os.path.exists(log_path):
        with open(log_path, 'r', encoding='utf-8') as f:
            return f.read().strip()
    return ""

def load_typical_errors(filepath):
    errors = []
    if not os.path.exists(filepath):
        return errors
    with open(filepath, 'r', encoding='utf-8') as f:
        for idx, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            
            # Format 1: "Question", error, "message"
            m = re.match(r'^\s*["«](.*?)[»"]\s*,\s*(.*?)\s*,\s*["«](.*?)[»"]\s*$', line)
            if m:
                errors.append({
                    "question": m.group(1).strip(),
                    "error_desc": m.group(2).strip(),
                    "message": m.group(3).strip()
                })
                continue
                
            # Format 2: "Question", error, message
            m = re.match(r'^\s*["«](.*?)[»"]\s*,\s*(.*)$', line)
            if m:
                q = m.group(1).strip()
                rest = m.group(2).strip()
                parts = rest.rsplit(',', 1)
                if len(parts) == 2:
                    errors.append({
                        "question": q,
                        "error_desc": parts[0].strip().strip('"«»'),
                        "message": parts[1].strip().strip('"«»')
                    })
                    continue
                    
            # Format 3: Question, error, message
            parts = line.split(',', 2)
            if len(parts) == 3:
                errors.append({
                    "question": parts[0].strip().strip('"«»'),
                    "error_desc": parts[1].strip().strip('"«»'),
                    "message": parts[2].strip().strip('"«»')
                })
    return errors

def normalize_text(text):
    return re.sub(r'[\W_]+', '', text.lower())

def find_candidate_typical_errors(teacher_q, typical_errors):
    norm_q = normalize_text(teacher_q)
    candidates = []
    for te in typical_errors:
        norm_te = normalize_text(te["question"])
        ratio = difflib.SequenceMatcher(None, norm_q, norm_te).ratio()
        if ratio >= 0.70 or (len(norm_te) > 15 and (norm_te in norm_q or norm_q in norm_te)):
            candidates.append(te)
    return candidates

async def check_student_typical_error(teacher_q, student_ans, candidate, media_bytes=None, mime_type=None):
    ans_text = f'Ответ ученика: "{student_ans}"' if student_ans else 'Ответ ученика содержится в прикрепленном медиа.'
    prompt_text = llm_prompts["check_typical_error"].format(
        teacher_q=teacher_q,
        student_ans_text=ans_text,
        error_desc=candidate['error_desc']
    )

    contents = [prompt_text]
    if media_bytes and mime_type:
        contents.append(genai_types.Part.from_bytes(data=media_bytes, mime_type=mime_type))

    try:
        reply = await call_gemini_with_fallback(contents)
        if "да" in reply.lower():
            return True
    except Exception as e:
        logging.warning(f"Error checking typical error: {e}")
    return False

async def call_gemini_with_fallback(contents, system_instruction=None, response_mime_type=None):
    last_error = None
    config = genai_types.GenerateContentConfig()
    if system_instruction:
        config.system_instruction = system_instruction
    if response_mime_type:
        config.response_mime_type = response_mime_type
        
    for attempt in range(2):
        for model_name in PRIMARY_MODELS:
            try:
                response = await asyncio.to_thread(
                    ai_client.models.generate_content,
                    model=model_name,
                    contents=contents,
                    config=config
                )
                if response and response.text:
                    return response.text.strip()
            except Exception as e:
                logging.warning(f"Model {model_name} failed: {e}")
                last_error = e
        if attempt == 0:
            logging.warning("All models failed on first attempt. Waiting 30s before retry...")
            await asyncio.sleep(30)
    raise last_error or RuntimeError("All models failed")

async def check_redundancy(user_id, next_question):
    history = read_session_log(user_id)
    if not history: return False
    prompt = llm_prompts["check_redundancy"].format(
        history=history,
        next_question=next_question
    )
    try:
        ans = await call_gemini_with_fallback(prompt)
        if "да" in ans.lower(): return True
    except: pass
    return False

def get_system_prompt(question, expected_answer, is_voice=False, has_photo=False, has_ref_img=False):
    note = llm_prompts["note_voice"] if is_voice else llm_prompts["note_text"]
    if has_photo: note += llm_prompts["note_photo"]
    if has_ref_img: note += llm_prompts["note_ref_img"]
    
    return llm_prompts["system_prompt"].format(
        question=question,
        expected_answer=expected_answer,
        note=note,
        rate_answer_text=rate_answer_text,
        promts_text=promts_text,
        self_agent_text=self_agent_text
    )

def extract_grade(llm_reply):
    if not llm_reply:
        return 'А', ""
    clean = llm_reply.strip()
    match = re.match(r"^[\*\s]*([АБВГДABCDE])[\.\)\:\*]+(?:\s*)(.*)", clean, re.IGNORECASE | re.DOTALL)
    if match:
        raw_grade = match.group(1).upper()
        mapping = {'A': 'А', 'B': 'Б', 'C': 'В', 'D': 'Д', 'E': 'Д'}
        grade = mapping.get(raw_grade, raw_grade)
        reply_text = match.group(2).strip()
        reply_text = re.sub(r"^\*+\s*", "", reply_text)
        return grade, reply_text
    return 'А', clean

async def process_generator_step(user_id, chat_id):
    session = get_session(user_id)
    topic = session["topic"]
    
    prompt = llm_prompts["generator_system"].replace("{topic}", topic)
    contents = [prompt]
    
    for entry in session["generate_history"]:
        if "teacher" in entry:
            contents.append(f"Учитель: {entry['teacher']}")
        elif "system" in entry:
            contents.append(f"Система: {entry['system']}")
            
    try:
        reply = await call_gemini_with_fallback(contents, response_mime_type="application/json")
        reply_clean = re.sub(r'^```[a-zA-Z]*\n?|\n?```$', '', reply.strip())
        result = json.loads(reply_clean)
        
        if result.get("status") == "question":
            await bot.send_message(chat_id, f"Уточнение: {result['message']}")
            session["generate_history"].append({"system": result["message"]})
            sessions[user_id] = session
        elif result.get("status") == "success":
            new_scenario = result.get("data")
            file_name = f"{normalize_text(topic)}.json"
            file_path = os.path.join(BASE_DIR, 'Databse of knowleages', file_name)
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(new_scenario, f, ensure_ascii=False, indent=2)
            await bot.send_message(chat_id, f"Сценарий успешно сгенерирован и сохранен как {file_name}!")
            session["mode"] = "study"
            sessions[user_id] = session
        else:
            await bot.send_message(chat_id, "Неизвестный статус от генератора.")
            session["mode"] = "study"
            sessions[user_id] = session
    except Exception as e:
        logging.error(f"Error in generator: {e}", exc_info=True)
        await bot.send_message(chat_id, "Произошла ошибка при генерации.")
        session["mode"] = "study"
        sessions[user_id] = session

# ===================== TELEGRAM BOT =====================
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    user_id = str(message.from_user.id)
    session = get_session(user_id)
    session["current_q"] = 0
    session["grades"] = []
    session["mode"] = "study"
    sessions[user_id] = session
    
    log_path = os.path.join(BASE_DIR, f'student_session_answers_{user_id}.txt')
    if os.path.exists(log_path): os.remove(log_path)
    
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎙 Голосовой звонок", url=f"http://127.0.0.1:8000/?user_id={user_id}")]])
    await message.answer("Привет! Я твой виртуальный учитель. Ты можешь отвечать текстом/фото здесь, либо переключиться на звонок:", reply_markup=markup)
    
    q_data = questions[0]
    await send_teacher_question(message.chat.id, q_data["teacher"])

@dp.message(Command("generate"))
async def generate_cmd(message: types.Message):
    user_id = str(message.from_user.id)
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Пожалуйста, укажите тему для генерации. Пример: /generate зависимость частоты колебаний пружинного маятника от жесткости пружины")
        return
        
    topic = args[1].strip()
    session = get_session(user_id)
    session["mode"] = "generate"
    session["topic"] = topic
    session["generate_history"] = []
    sessions[user_id] = session
    
    await message.answer(f"Генерирую сценарий по теме: {topic}. Идет проверка...")
    await process_generator_step(user_id, message.chat.id)

@dp.message(F.text | F.photo | F.voice)
async def handle_tg_message(message: types.Message):
    user_id = str(message.from_user.id)
    session = get_session(user_id)
    
    if session.get("mode") == "generate":
        if not message.text:
            await message.answer("Пожалуйста, ответьте текстом на вопрос генератора.")
            return
        session["generate_history"].append({"teacher": message.text})
        sessions[user_id] = session
        await process_generator_step(user_id, message.chat.id)
        return
        
    current_q_idx = session["current_q"]
    
    if current_q_idx >= len(questions):
        await message.answer("Исследование завершено! Поздравляю.")
        return

    q_data = questions[current_q_idx]
    teacher_q = q_data["teacher"]
    expected_ans = q_data["answer"]
    
    student_ans = message.text if message.text else (message.caption if message.caption else "")
    has_photo = bool(message.photo)
    has_voice = bool(message.voice)
    
    photo_bytes = None
    if has_photo:
        photo = message.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        d_file = await bot.download_file(file_info.file_path)
        photo_bytes = d_file.read()
        
    voice_bytes = None
    if has_voice:
        file_info = await bot.get_file(message.voice.file_id)
        d_file = await bot.download_file(file_info.file_path)
        voice_bytes = d_file.read()

    ans_label = student_ans
    if has_photo:
        ans_label = f"<Фото/Схема: {student_ans}>" if student_ans else "<Фото/Схема>"
    elif has_voice:
        ans_label = f"<Голосовое: {student_ans}>" if student_ans else "<Голосовое сообщение>"

    # 1. Проверка на типичные ошибки (алгоритмическая + LLM)
    typical_errors = load_typical_errors(os.path.join(BASE_DIR, 'typical_errors.txt'))
    candidates = find_candidate_typical_errors(teacher_q, typical_errors)
    matched_typical_msg = None
    if candidates:
        for candidate in candidates:
            media_b = photo_bytes if has_photo else (voice_bytes if has_voice else None)
            mime = "image/jpeg" if has_photo else ("audio/ogg" if has_voice else None)
            if await check_student_typical_error(teacher_q, student_ans, candidate, media_bytes=media_b, mime_type=mime):
                matched_typical_msg = candidate["message"]
                break
                
    if matched_typical_msg:
        grade = 'В'
        reply_text = matched_typical_msg
        await message.answer(reply_text)
        save_session_log(user_id, teacher_q, ans_label, grade, reply_text)
        return

    # 2. Стандартная кумулятивная проверка через Gemini
    ref_img_path = get_reference_image_path(expected_ans)
    ref_img_bytes = None
    if ref_img_path and os.path.exists(ref_img_path):
        with open(ref_img_path, 'rb') as f:
            ref_img_bytes = f.read()

    sys_instruction = get_system_prompt(teacher_q, expected_ans, is_voice=has_voice, has_photo=has_photo, has_ref_img=bool(ref_img_bytes))
    
    history = read_session_log(user_id)
    contents = []
    if history:
        contents.append(llm_prompts["history_prefix"].format(history=history))
    
    if student_ans:
        contents.append(llm_prompts["new_msg_prefix"].format(student_ans=student_ans))
    else:
        contents.append(llm_prompts["new_msg_media_only"])
    
    if photo_bytes:
        contents.append(genai_types.Part.from_bytes(data=photo_bytes, mime_type="image/jpeg"))
    if voice_bytes:
        contents.append(genai_types.Part.from_bytes(data=voice_bytes, mime_type="audio/ogg"))
    if ref_img_bytes:
        contents.append(llm_prompts["ref_img_prefix"])
        contents.append(genai_types.Part.from_bytes(data=ref_img_bytes, mime_type="image/png"))
    
    try:
        llm_reply = await call_gemini_with_fallback(contents, sys_instruction)
    except Exception as e:
        logging.error(f"Error handling message: {e}", exc_info=True)
        await message.answer("Техническая ошибка. Попробуй ещё раз.")
        return

    grade, reply_text = extract_grade(llm_reply)
    await message.answer(reply_text)
    save_session_log(user_id, teacher_q, ans_label, grade, reply_text)
    
    if grade not in ['В', 'Г', 'Д']:
        session["grades"].append(grade)
        next_q_idx = current_q_idx + 1
        while next_q_idx < len(questions):
            next_q = questions[next_q_idx]
            if await check_redundancy(user_id, next_q["teacher"]):
                save_session_log(user_id, next_q["teacher"], "<ПРОПУСК КАК ДУБЛИКАТ>", "А", "<Автоматический пропуск>")
                next_q_idx += 1
            else: break
        
        session["current_q"] = next_q_idx
        sessions[user_id] = session
        
        if next_q_idx < len(questions):
            markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎙 Голосовой звонок", url=f"http://127.0.0.1:8000/?user_id={user_id}")]])
            await send_teacher_question(message.chat.id, questions[next_q_idx]["teacher"], reply_markup=markup)
        else:
            await message.answer("Поздравляю, исследование успешно завершено!")

# ===================== FASTAPI WEB =====================
@app.get("/", response_class=HTMLResponse)
async def index(request: Request, user_id: str = "guest"):
    return templates.TemplateResponse(request=request, name="index.html", context={"user_id": user_id})

@app.get("/sync_state")
async def sync_state(user_id: str = "guest"):
    session = get_session(user_id)
    current_q_idx = session["current_q"]
    if current_q_idx >= len(questions): return {"status": "finished"}
    q_data = questions[current_q_idx]
    
    needs_drawing = is_drawing_question(q_data)
    if needs_drawing:
        return {"status": "needs_drawing", "message": "Это задание требует отправки рисунка. Пожалуйста, вернитесь в Telegram-чат и отправьте фото."}
    return {"status": "ok", "question": clean_teacher_text(q_data["teacher"])}

@app.post("/start_call")
async def start_call(user_id: str = "guest"):
    session = get_session(user_id)
    current_q_idx = session["current_q"]
    
    if current_q_idx >= len(questions):
        text = "Исследование завершено."
    else:
        text = clean_teacher_text(questions[current_q_idx]["teacher"])
    
    tts = gTTS(text=text, lang='ru')
    audio_io = io.BytesIO()
    tts.write_to_fp(audio_io)
    return Response(content=audio_io.getvalue(), media_type="audio/mpeg")

@app.post("/upload_audio")
async def upload_audio(audio: UploadFile = File(...), user_id: str = "guest"):
    session = get_session(user_id)
    current_q_idx = session["current_q"]
    
    if current_q_idx >= len(questions):
        tts = gTTS(text="Исследование завершено.", lang='ru')
        audio_io = io.BytesIO()
        tts.write_to_fp(audio_io)
        return Response(content=audio_io.getvalue(), media_type="audio/mpeg")
        
    q_data = questions[current_q_idx]
    teacher_q = q_data["teacher"]
    expected_ans = q_data["answer"]
    
    audio_bytes = await audio.read()
    
    # 1. Проверка на типичные ошибки (алгоритмическая + LLM)
    typical_errors = load_typical_errors(os.path.join(BASE_DIR, 'typical_errors.txt'))
    candidates = find_candidate_typical_errors(teacher_q, typical_errors)
    matched_typical_msg = None
    if candidates:
        for candidate in candidates:
            if await check_student_typical_error(teacher_q, "Голосовой ответ ученика", candidate, media_bytes=audio_bytes, mime_type="audio/webm"):
                matched_typical_msg = candidate["message"]
                break
                
    if matched_typical_msg:
        grade = 'В'
        reply_text = matched_typical_msg
        save_session_log(user_id, teacher_q, "<Голос web>", grade, reply_text)
        tts = gTTS(text=reply_text, lang='ru')
        audio_io = io.BytesIO()
        tts.write_to_fp(audio_io)
        return Response(content=audio_io.getvalue(), media_type="audio/mpeg")

    # 2. Стандартная проверка через Gemini
    ref_img_path = get_reference_image_path(expected_ans)
    ref_img_bytes = None
    if ref_img_path and os.path.exists(ref_img_path):
        with open(ref_img_path, 'rb') as f:
            ref_img_bytes = f.read()

    sys_instruction = get_system_prompt(teacher_q, expected_ans, is_voice=True, has_ref_img=bool(ref_img_bytes))
    
    history = read_session_log(user_id)
    contents = []
    if history:
        contents.append(llm_prompts["history_prefix_web"].format(history=history))
        
    contents.append(llm_prompts["new_msg_audio_only"])
    contents.append(genai_types.Part.from_bytes(data=audio_bytes, mime_type="audio/webm"))
    if ref_img_bytes:
        contents.append(llm_prompts["ref_img_prefix"])
        contents.append(genai_types.Part.from_bytes(data=ref_img_bytes, mime_type="image/png"))
    
    try:
        llm_reply = await call_gemini_with_fallback(contents, sys_instruction)
    except Exception as e:
        logging.error(f"Error handling web audio: {e}", exc_info=True)
        tts = gTTS(text="Произошла техническая ошибка. Пожалуйста, повтори ответ ещё раз.", lang='ru')
        audio_io = io.BytesIO()
        tts.write_to_fp(audio_io)
        return Response(content=audio_io.getvalue(), media_type="audio/mpeg")
        
    grade, reply_text = extract_grade(llm_reply)
    
    full_reply = reply_text
    save_session_log(user_id, teacher_q, "<Голос web>", grade, reply_text)
    
    if grade not in ['В', 'Г', 'Д']:
        session["grades"].append(grade)
        next_q_idx = current_q_idx + 1
        while next_q_idx < len(questions):
            next_q = questions[next_q_idx]
            if await check_redundancy(user_id, next_q["teacher"]):
                save_session_log(user_id, next_q["teacher"], "<ПРОПУСК КАК ДУБЛИКАТ>", "А", "<Автоматический пропуск>")
                next_q_idx += 1
            else: break
            
        session["current_q"] = next_q_idx
        sessions[user_id] = session
        
        if next_q_idx < len(questions):
            next_q = questions[next_q_idx]
            if is_drawing_question(next_q):
                full_reply += " Следующее задание требует рисунка. Пожалуйста, перейди в телеграм-чат и отправь фото."
                asyncio.create_task(send_teacher_question(user_id, f"Продолжаем в чате! Задание:\n{next_q['teacher']}"))
            else:
                full_reply += " " + clean_teacher_text(next_q["teacher"])
                if get_teacher_media(next_q["teacher"]):
                    full_reply += " Я отправил изображение к заданию в наш Telegram-чат, посмотри его."
                    asyncio.create_task(send_teacher_question(user_id, next_q['teacher']))
        else:
            full_reply += " Поздравляю, исследование успешно завершено!"

    tts = gTTS(text=full_reply, lang='ru')
    audio_io = io.BytesIO()
    tts.write_to_fp(audio_io)
    return Response(content=audio_io.getvalue(), media_type="audio/mpeg")

async def run_bot():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

async def run_web():
    config = uvicorn.Config(app=app, host="0.0.0.0", port=8000, loop="asyncio")
    server = uvicorn.Server(config)
    await server.serve()

async def main_runner():
    await asyncio.gather(run_bot(), run_web())

if __name__ == "__main__":
    asyncio.run(main_runner())
