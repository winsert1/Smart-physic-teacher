import os
import re
import difflib
import json
import logging
import warnings
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from google import genai
from google.genai import types as genai_types
import asyncio

warnings.filterwarnings("ignore")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, '.env'))

GEMINI_API_KEY = os.getenv('Gemini_API_KEY')
TGBOT_API_KEY = os.getenv('TGBOT_API_KEY')

ai_client = genai.Client(api_key=GEMINI_API_KEY, http_options={'retry_options': {'attempts': 1}})
PRIMARY_MODELS = ['gemini-3.7-flash', 'gemini-3.8-flash', 'gemini-3.6-flash', 'gemini-3.5-flash']

bot = Bot(token=TGBOT_API_KEY)
dp = Dispatcher(storage=MemoryStorage())
logging.basicConfig(level=logging.INFO)

with open(os.path.join(BASE_DIR, 'rate_answer.txt'), 'r', encoding='utf-8') as f:
    rate_answer_text = f.read()

with open(os.path.join(BASE_DIR, 'self_agent.txt'), 'r', encoding='utf-8') as f:
    self_agent_text = f.read()

with open(os.path.join(BASE_DIR, 'promts.txt'), 'r', encoding='utf-8') as f:
    promts_text = f.read()

with open(os.path.join(BASE_DIR, 'promts_LLM.json'), 'r', encoding='utf-8') as f:
    llm_prompts = json.load(f)

db_path = os.path.join(BASE_DIR, 'Databse of knowleages', 'зависимость периода колебаний пружинного маятника от массы груза.json')
with open(db_path, 'r', encoding='utf-8') as f:
    knowledge_db = json.load(f)
questions = knowledge_db.get("rows", [])

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

class StudyState(StatesGroup):
    answering = State()
    generating = State()

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
                logging.warning(f"Model {model_name} failed with error: {e}. Trying next...")
                last_error = e
        if attempt == 0:
            logging.warning("All models failed on first attempt. Waiting 30s before retry due to potential 429 Too Many Requests...")
            await asyncio.sleep(30)
            
    raise last_error or RuntimeError("All models failed")

async def check_redundancy(user_id, next_question):
    history = read_session_log(user_id)
    if not history:
        return False
        
    prompt = llm_prompts["check_redundancy"].format(
        history=history,
        next_question=next_question
    )
    try:
        ans = await call_gemini_with_fallback(prompt)
        if "да" in ans.lower():
            return True
    except:
        pass
    return False

def get_system_prompt(question, expected_answer, has_photo=False, has_voice=False, has_ref_img=False):
    note = llm_prompts["note_voice"] if has_voice else llm_prompts["note_text"]
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

def calculate_final_score(grades):
    mapping = {'А': 5, 'Б': 4, 'В': 3, 'Г': 2, 'Д': 2}
    numeric_grades = [mapping[g] for g in grades if g in mapping]
    if not numeric_grades:
        return 0.0, 0
    avg = sum(numeric_grades) / len(numeric_grades)
    rounded = int(avg + 0.5)
    return avg, rounded

async def process_generator_step(chat_id, state: FSMContext):
    data = await state.get_data()
    topic = data.get("generate_topic", "")
    history = data.get("generate_history", [])
    
    prompt = llm_prompts["generator_system"].replace("{topic}", topic)
    contents = [prompt]
    
    for entry in history:
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
            history.append({"system": result["message"]})
            await state.update_data(generate_history=history)
        elif result.get("status") == "success":
            new_scenario = result.get("data")
            file_name = f"{normalize_text(topic)}.json"
            file_path = os.path.join(BASE_DIR, 'Databse of knowleages', file_name)
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(new_scenario, f, ensure_ascii=False, indent=2)
            await bot.send_message(chat_id, f"Сценарий успешно сгенерирован и сохранен как {file_name}!")
            await state.clear()
        else:
            await bot.send_message(chat_id, "Неизвестный статус от генератора.")
            await state.clear()
    except Exception as e:
        logging.error(f"Error in generator: {e}", exc_info=True)
        await bot.send_message(chat_id, "Произошла ошибка при генерации.")
        await state.clear()


@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    await message.answer("Привет! Я твой виртуальный учитель. Давай начнем наше исследование!")
    await state.update_data(current_q=0, grades=[])
    
    # Очистка лога новой сессии
    log_path = os.path.join(BASE_DIR, f'student_session_answers_{message.from_user.id}.txt')
    if os.path.exists(log_path):
        os.remove(log_path)
    
    q_data = questions[0]
    await send_teacher_question(message.chat.id, q_data["teacher"])
    await state.set_state(StudyState.answering)

@dp.message(Command("generate"))
async def generate_cmd(message: types.Message, state: FSMContext):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Пожалуйста, укажите тему для генерации. Пример: /generate зависимость частоты колебаний пружинного маятника от жесткости пружины")
        return
        
    topic = args[1].strip()
    await state.set_state(StudyState.generating)
    await state.update_data(generate_topic=topic, generate_history=[])
    
    await message.answer(f"Генерирую сценарий по теме: {topic}. Идет проверка...")
    await process_generator_step(message.chat.id, state)

@dp.message(StudyState.generating, F.text)
async def handle_generating(message: types.Message, state: FSMContext):
    data = await state.get_data()
    history = data.get("generate_history", [])
    history.append({"teacher": message.text})
    await state.update_data(generate_history=history)
    await process_generator_step(message.chat.id, state)

@dp.message(StudyState.answering, F.text | F.photo | F.voice)
async def handle_answer(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    current_q_idx = data.get("current_q", 0)
    grades = data.get("grades", [])
    
    if current_q_idx >= len(questions):
        await message.answer("Исследование уже успешно завершено! Поздравляю.")
        await state.clear()
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
        downloaded_file = await bot.download_file(file_info.file_path)
        photo_bytes = downloaded_file.getvalue() if hasattr(downloaded_file, 'getvalue') else downloaded_file.read()
        
    voice_bytes = None
    if has_voice:
        file_info = await bot.get_file(message.voice.file_id)
        downloaded_file = await bot.download_file(file_info.file_path)
        voice_bytes = downloaded_file.getvalue() if hasattr(downloaded_file, 'getvalue') else downloaded_file.read()

    ans_label = student_ans
    if has_photo:
        ans_label = f"<Фото/Схема: {student_ans}>" if student_ans else "<Фото/Схема>"
    elif has_voice:
        ans_label = f"<Голосовое: {student_ans}>" if student_ans else "<Голосовое сообщение>"

    # 1. Алгоритмическая + LLM проверка на типичные ошибки
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
        session["grades"].append(grade)
        return

    # 2. Стандартный кумулятивный анализ через Gemini
    ref_img_path = get_reference_image_path(expected_ans)
    ref_img_bytes = None
    if ref_img_path and os.path.exists(ref_img_path):
        with open(ref_img_path, 'rb') as f:
            ref_img_bytes = f.read()

    sys_instruction = get_system_prompt(teacher_q, expected_ans, has_photo=has_photo, has_voice=has_voice, has_ref_img=bool(ref_img_bytes))
    
    history = read_session_log(user_id)
    contents = []
    if history:
        contents.append(f"ИСТОРИЯ ДИАЛОГА В ТЕКУЩЕЙ СЕССИИ (предыдущие ответы ученика и замечания учителя):\n{history}\n" + "="*40)
    
    if student_ans:
        contents.append(f"НОВОЕ ТЕКУЩЕЕ СООБЩЕНИЕ УЧЕНИКА: {student_ans}")
    else:
        contents.append("НОВОЕ ТЕКУЩЕЕ СООБЩЕНИЕ УЧЕНИКА: содержится в прикрепленном медиа.")
    
    if photo_bytes:
        contents.append(genai_types.Part.from_bytes(data=photo_bytes, mime_type="image/jpeg"))
    if voice_bytes:
        contents.append(genai_types.Part.from_bytes(data=voice_bytes, mime_type="audio/ogg"))
    if ref_img_bytes:
        contents.append("ЭТАЛОННЫЙ РИСУНОК/СХЕМА (с чем нужно сравнить работу ученика):")
        contents.append(genai_types.Part.from_bytes(data=ref_img_bytes, mime_type="image/png"))
    
    try:
        llm_reply = await call_gemini_with_fallback(contents, system_instruction=sys_instruction)
    except Exception as e:
        logging.error(f"Error calling Gemini API: {e}", exc_info=True)
        await message.answer("Извини, у меня возникли технические неполадки при проверке твоего ответа. Пожалуйста, попробуй ещё раз.")
        return

    grade, reply_text = extract_grade(llm_reply)
    await message.answer(reply_text)
    save_session_log(user_id, teacher_q, ans_label, grade, reply_text)
    
    grades.append(grade)
    
    if grade in ['В', 'Г', 'Д']:
        # В, Г, Д - stay on current question, student must clarify/fix
        await state.update_data(grades=grades)
    else:
        # Lookahead logic: loop to find the next non-redundant question
        next_q_idx = current_q_idx + 1
        while next_q_idx < len(questions):
            next_q = questions[next_q_idx]
            is_redundant = await check_redundancy(user_id, next_q["teacher"])
            if is_redundant:
                logging.info(f"Skipping redundant question: {next_q['teacher']}")
                save_session_log(user_id, next_q["teacher"], "<ПРОПУЩЕН КАК ДУБЛИКАТ>", "А", "<Автоматический пропуск>")
                grades.append("А")
                next_q_idx += 1
            else:
                break
                
        await state.update_data(current_q=next_q_idx, grades=grades)
        
        if next_q_idx < len(questions):
            next_q = questions[next_q_idx]
            await send_teacher_question(message.chat.id, next_q["teacher"])
        else:
            avg, rounded = calculate_final_score(grades)
            if 'В' in grades or 'Г' in grades or 'Д' in grades:
                msg = f"Поздравляю, исследование завершено! Мы разобрали все сложные моменты. Молодец!\n\nКоличественная оценка: {avg:.2f}\nИтоговая оценка: {rounded}"
            else:
                msg = f"Поздравляю, исследование успешно завершено! Ты показал отличные результаты и глубокое понимание темы.\n\nКоличественная оценка: {avg:.2f}\nИтоговая оценка: {rounded}"
            await message.answer(msg)
            await state.clear()

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
