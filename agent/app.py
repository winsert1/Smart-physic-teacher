import os
import io
import asyncio
from fastapi import FastAPI, UploadFile, File, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from google import genai
from google.genai import types as genai_types
from gtts import gTTS
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, '.env'))

GEMINI_API_KEY = os.getenv('Gemini_API_KEY')
ai_client = genai.Client(api_key=GEMINI_API_KEY, http_options={'retry_options': {'attempts': 1}})
PRIMARY_MODELS = ['gemini-3.5-flash', 'gemini-3.8-flash']

app = FastAPI()

templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Load files once
with open(os.path.join(BASE_DIR, 'rate_answer.txt'), 'r', encoding='utf-8') as f:
    rate_answer_text = f.read()

with open(os.path.join(BASE_DIR, 'self_agent.txt'), 'r', encoding='utf-8') as f:
    self_agent_text = f.read()

with open(os.path.join(BASE_DIR, 'promts.txt'), 'r', encoding='utf-8') as f:
    promts_text = f.read()

import json
db_path = os.path.join(BASE_DIR, 'Databse of knowleages', 'зависимость периода колебаний пружинного маятника от массы груза.json')
with open(db_path, 'r', encoding='utf-8') as f:
    knowledge_db = json.load(f)
questions = knowledge_db.get("rows", [])

# Simple in-memory session (in production use proper DB/Redis)
sessions = {}

def get_system_prompt(question, expected_answer):
    sys_prompt = f"""Ты — виртуальный ассистент учителя физики. (Режим голосового звонка)
Твоя задача — вести диалог с учеником, следуя исследовательскому сценарию.

Текущий вопрос учителя: {question}
Эталонный ответ: {expected_answer}

Оцени ответ ученика (голосовое сообщение) и выбери оценку от А до Д согласно критериям:
{rate_answer_text}

Общее описание задачи:
{promts_text}

Правила поведения (статус учителя чаще сверху-вниз):
{self_agent_text}

ВАЖНЫЕ ПРАВИЛА:
1. НЕ говори прямо "правильно/неправильно". Твой ответ должен звучать ЕСТЕСТВЕННО для голосового звонка.
2. ИЗБЕГАЙ ТАВТОЛОГИИ: используй синонимы, добавляй 1-3 слова метафор. Для А и Б будь ОЧЕНЬ КРАТОК.
3. ДВОЙНЫЕ ВОПРОСЫ:
   - Если оценка А или Б: ЗАПРЕЩЕНО задавать встречные вопросы (система задаст следующий).
   - Если оценка В: ОБЯЗАТЕЛЬНО задай уточняющий вопрос для пояснения текущего момента.
   - Если оценка Г или Д: задавай уточняющие вопросы/примеры.
"""
    return sys_prompt

async def call_gemini_with_fallback(contents, system_instruction=None):
    last_error = None
    config = genai_types.GenerateContentConfig()
    if system_instruction:
        config.system_instruction = system_instruction
        
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
                print(f"Model {model_name} failed with error: {e}. Trying next...")
                last_error = e
        if attempt == 0:
            print("All models failed on first attempt. Waiting 30s before retry due to potential 429 Too Many Requests...")
            await asyncio.sleep(30)
            
    raise last_error or RuntimeError("All models failed")

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.post("/start_call")
async def start_call(user_id: str = "guest"):
    sessions[user_id] = {"current_q": 0}
    first_q = questions[0]["teacher"]
    
    tts = gTTS(text=first_q, lang='ru')
    audio_io = io.BytesIO()
    tts.write_to_fp(audio_io)
    return Response(content=audio_io.getvalue(), media_type="audio/mpeg")

@app.post("/upload_audio")
async def upload_audio(audio: UploadFile = File(...), user_id: str = "guest"):
    session = sessions.get(user_id, {"current_q": 0})
    current_q_idx = session["current_q"]
    
    if current_q_idx >= len(questions):
        tts = gTTS(text="Исследование уже успешно завершено! Поздравляю.", lang='ru')
        audio_io = io.BytesIO()
        tts.write_to_fp(audio_io)
        return Response(content=audio_io.getvalue(), media_type="audio/mpeg")
        
    q_data = questions[current_q_idx]
    teacher_q = q_data["teacher"]
    expected_ans = q_data["answer"]
    
    audio_bytes = await audio.read()
    
    sys_instruction = get_system_prompt(teacher_q, expected_ans)
    contents = [
        "Ученик ответил голосом (файл прикреплен).",
        genai_types.Part.from_bytes(data=audio_bytes, mime_type="audio/webm") # web browsers record in webm usually
    ]
    
    llm_reply = await call_gemini_with_fallback(contents, system_instruction=sys_instruction)
    
    grade = 'А'
    reply_text = llm_reply
    if llm_reply and llm_reply[0].upper() in ['А', 'Б', 'В', 'Г', 'Д', 'A', 'B', 'C', 'D', 'E']:
        first_char = llm_reply[0].upper()
        mapping = {'A': 'А', 'B': 'Б', 'C': 'В', 'D': 'Д', 'E': 'Д'}
        grade = mapping.get(first_char, first_char)
        parts = llm_reply.split('.', 1)
        if len(parts) > 1 and len(parts[0]) <= 2:
            reply_text = parts[1].strip()
        else:
            parts = llm_reply.split(')', 1)
            if len(parts) > 1 and len(parts[0]) <= 2:
                reply_text = parts[1].strip()

def calculate_final_score(grades):
    mapping = {'А': 5, 'Б': 4, 'В': 3, 'Г': 2, 'Д': 2}
    numeric_grades = [mapping[g] for g in grades if g in mapping]
    if not numeric_grades:
        return 0.0, 0
    avg = sum(numeric_grades) / len(numeric_grades)
    rounded = int(avg + 0.5)
    return avg, rounded

    full_reply = reply_text
    
    # Progress logic
    if "grades" not in session:
        session["grades"] = []
    session["grades"].append(grade)
    
    if grade in ['В', 'Г', 'Д']:
        # Stay on current
        pass
    else:
        # Move forward (simplistic lookahead bypass for voice mode)
        next_q_idx = current_q_idx + 1
        session["current_q"] = next_q_idx
        sessions[user_id] = session
        
        if next_q_idx < len(questions):
            next_q = questions[next_q_idx]
            full_reply += " " + next_q["teacher"]
        else:
            avg, rounded = calculate_final_score(session["grades"])
            full_reply += f" Поздравляю, исследование успешно завершено! Количественная оценка: {avg:.2f}. Итоговая оценка: {rounded}."

    # TTS
    tts = gTTS(text=full_reply, lang='ru')
    audio_io = io.BytesIO()
    tts.write_to_fp(audio_io)
    
    return Response(content=audio_io.getvalue(), media_type="audio/mpeg")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
