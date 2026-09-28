import os
from docx import Document
from docx.shared import Pt, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

def add_page_number(run):
    fldChar1 = OxmlElement('w:fldChar')
    fldChar1.set(qn('w:fldCharType'), 'begin')
    instrText = OxmlElement('w:instrText')
    instrText.set(qn('xml:space'), 'preserve')
    instrText.text = "PAGE"
    fldChar2 = OxmlElement('w:fldChar')
    fldChar2.set(qn('w:fldCharType'), 'separate')
    fldChar3 = OxmlElement('w:fldChar')
    fldChar3.set(qn('w:fldCharType'), 'end')
    run._r.append(fldChar1)
    run._r.append(instrText)
    run._r.append(fldChar2)
    run._r.append(fldChar3)

def create_doc():
    doc = Document()
    
    # Configure styles
    style = doc.styles['Normal']
    font = style.font
    font.name = 'Times New Roman'
    font.size = Pt(14)
    pf = style.paragraph_format
    pf.line_spacing = 1.5
    pf.first_line_indent = Cm(1.25)
    pf.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    # Header
    section = doc.sections[0]
    header = section.header
    h_para = header.paragraphs[0]
    h_para.text = "Фамилия И.О., Группа [Ваша Группа]"
    h_para.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    # Footer
    footer = section.footer
    f_para = footer.paragraphs[0]
    f_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    add_page_number(f_para.add_run())

    # Content
    doc.add_heading('Отчет по архитектуре агента: Виртуальный учитель физики', 0)

    doc.add_paragraph(
        "Данный агент представляет собой виртуального учителя физики, предназначенного для двух целевых аудиторий: "
        "учеников и методистов/учителей. Для учеников агент решает учебную задачу пошагового проведения "
        "исследовательского сценария (например, «зависимость периода колебаний пружинного маятника от массы груза»). "
        "Агент оценивает текстовые, голосовые и графические ответы ученика, опираясь на эталоны. "
        "Для учителей агент предоставляет режим автоматической генерации новых сценариев по заданным темам."
    )

    doc.add_paragraph(
        "Архитектура агента состоит из следующих функциональных блоков:\n"
        "1.1 Инициализация и маршрутизация ввода.\n"
        "1.2 Предобработка типичных ошибок.\n"
        "1.3 Кумулятивная оценка ответа ученика.\n"
        "1.4 Генерация новых учебных сценариев."
    )

    doc.add_heading('5.2 Обзор функциональных блоков', level=1)

    # Block 1.1
    doc.add_paragraph("Блок 1.1 — Инициализация и маршрутизация ввода", style='Normal').bold = True
    doc.add_paragraph("Обзор: Принимает текстовые, графические и голосовые сообщения через Telegram Bot и FastAPI Web-сервер. "
                      "Управляет текущим состоянием сессии и переключает интерфейсы (чат/голос).")
    doc.add_paragraph("Задействованные компоненты: Telegram Bot Handler, Web Audio Handler.")
    doc.add_paragraph("Детали компонентов:\n"
                      "1. Telegram Bot Handler\n"
                      "• Тип: Aiogram Router.\n"
                      "• Роль: Обработка команд и медиа-файлов, отправка эталонных изображений.\n"
                      "• Конфигурация: поллинг Telegram API, управление FSM и shared sessions.\n"
                      "• Входные данные: текст, фото, голосовые сообщения.\n"
                      "• Выходные данные: обновление JSON-состояния, отправка текста и фото пользователю.\n\n"
                      "2. Web Audio Handler\n"
                      "• Тип: FastAPI / Uvicorn Server.\n"
                      "• Роль: Обработка аудио через веб-интерфейс.\n"
                      "• Конфигурация: CORS, статические файлы, multipart/form-data.\n"
                      "• Входные данные: аудио-файлы (webm/ogg).\n"
                      "• Выходные данные: генерация аудио через TTS-модели.")

    # Block 1.2
    doc.add_paragraph("Блок 1.2 — Предобработка типичных ошибок", style='Normal').bold = True
    doc.add_paragraph("Обзор: Фильтрует ответы ученика на наличие алгоритмических типичных ошибок для снижения нагрузки на основную LLM.")
    doc.add_paragraph("Задействованные компоненты: Fuzzy Matcher, LLM Typical Error Checker.")
    doc.add_paragraph("Детали компонентов:\n"
                      "1. Fuzzy Matcher\n"
                      "• Тип: Python difflib SequenceMatcher.\n"
                      "• Роль: Поиск подходящей типичной ошибки по тексту вопроса.\n"
                      "• Конфигурация: threshold 0.7.\n"
                      "• Входные данные: текущий вопрос учителя, база typical_errors.txt.\n"
                      "• Выходные данные: список потенциальных ошибок-кандидатов.\n\n"
                      "2. Typical Error Checker\n"
                      "• Тип: LLM Call.\n"
                      "• Роль: Валидация совпадения ответа с описанием типичной ошибки.\n"
                      "• Конфигурация: Gemini 3.5 Flash.\n"
                      "• Входные данные: вопрос, ответ ученика, описание ошибки.\n"
                      "• Выходные данные: Бинарный ответ ДА/НЕТ.")

    # Block 1.3
    doc.add_paragraph("Блок 1.3 — Кумулятивная оценка ответа ученика", style='Normal').bold = True
    doc.add_paragraph("Обзор: Сравнивает ответ ученика с эталонным текстом или изображением с учетом всей истории диалога, выставляет оценку А-Д.")
    doc.add_paragraph("Задействованные компоненты: LLM AI Evaluator, Redundancy Checker.")
    doc.add_paragraph("Детали компонентов:\n"
                      "1. AI Evaluator\n"
                      "• Тип: LLM Chain.\n"
                      "• Роль: Сравнение ответа с эталоном по критериям и формирование фидбека.\n"
                      "• Конфигурация: Gemini 3.8 Flash, системный промпт с правилами оценки.\n"
                      "• Входные данные: история диалога, текущий ответ, эталон, прикрепленное фото.\n"
                      "• Выходные данные: Форматированный текст с оценкой от А до Д.\n\n"
                      "2. Redundancy Checker\n"
                      "• Тип: LLM Call.\n"
                      "• Роль: Анализ истории для пропуска вопросов, на которые уже дан ответ.\n"
                      "• Конфигурация: Gemini 3.5 Flash.\n"
                      "• Входные данные: история сессии, следующий вопрос.\n"
                      "• Выходные данные: Бинарный ответ ДА/НЕТ.")

    # Block 1.4
    doc.add_paragraph("Блок 1.4 — Генерация новых учебных сценариев", style='Normal').bold = True
    doc.add_paragraph("Обзор: Позволяет учителю автоматически создавать новые исследовательские JSON-сценарии.")
    doc.add_paragraph("Задействованные компоненты: Scenario Generator LLM.")
    doc.add_paragraph("Детали компонентов:\n"
                      "1. Scenario Generator LLM\n"
                      "• Тип: LLM Call (JSON mode).\n"
                      "• Роль: Создание сценария или уточнение физических противоречий.\n"
                      "• Конфигурация: Gemini 3.8 Flash, response_mime_type=application/json.\n"
                      "• Входные данные: тема, история генерации.\n"
                      "• Выходные данные: Строгий JSON {status, message/data}.")

    doc.add_heading('5.3 Таблица компонентов агента', level=1)

    table = doc.add_table(rows=1, cols=6)
    table.style = 'Table Grid'
    hdr_cells = table.rows[0].cells
    hdr_cells[0].text = 'Название компонента'
    hdr_cells[1].text = 'Тип'
    hdr_cells[2].text = 'Функциональная роль'
    hdr_cells[3].text = 'Вход. данные'
    hdr_cells[4].text = 'Вых. данные'
    hdr_cells[5].text = 'Примечание'

    components = [
        ("Telegram Bot Handler", "Aiogram Router", "Маршрутизация", "Текст/Фото/Голос", "Обновление стейта", "Поллинг"),
        ("Web Audio Handler", "FastAPI Server", "Обработка аудио", "Webm/ogg", "TTS", "Uvicorn"),
        ("Fuzzy Matcher", "difflib", "Поиск ошибки", "Вопрос", "Кандидаты ошибок", "Threshold 0.7"),
        ("Typical Error Checker", "LLM Call", "Валидация ошибки", "Ответ, Описание", "ДА/НЕТ", "Gemini 3.5 Flash"),
        ("AI Evaluator", "LLM Chain", "Оценка (А-Д)", "История, Ответ, Эталон", "Фидбек", "Gemini 3.8 Flash"),
        ("Redundancy Checker", "LLM Call", "Пропуск вопросов", "История, След. вопрос", "ДА/НЕТ", "Gemini 3.5 Flash"),
        ("Scenario Generator", "LLM JSON", "Генерация сценария", "Тема исследования", "JSON", "Gemini 3.8 Flash")
    ]

    for comp in components:
        row_cells = table.add_row().cells
        for i in range(6):
            row_cells[i].text = comp[i]

    doc.add_heading('5.5 Промпты', level=1)
    doc.add_paragraph("В проекте используются следующие системные и пользовательские промпты, вынесенные в promts_LLM.json:")
    doc.add_paragraph("1. System Prompt (Основной AI Evaluator):", style='Normal').bold = True
    doc.add_paragraph("Текст: «Ты — виртуальный ассистент учителя физики. Твоя задача — вести диалог с учеником... КРИТИЧЕСКИ ВАЖНЫЕ ПРАВИЛА: 1. ФОРМАТ ОТВЕТА начинается с буквы оценки и точки... Оценивай КУМУЛЯТИВНО...»")
    doc.add_paragraph("Пояснение: Задает агенту роль учителя, передает эталон ответа, критерии оценки (А-Д) и правила не дублировать вопросы.")

    doc.add_paragraph("2. Typical Error Checker Prompt:", style='Normal').bold = True
    doc.add_paragraph("Текст: «Контекст урока физики... Вопрос: Подходит ли ответ ученика под данное описание типичной ошибки? Ответь ТОЛЬКО одним словом: ДА или НЕТ.»")
    doc.add_paragraph("Пояснение: Промпт для бинарной классификации ответа ученика. Позволяет перехватывать предсказуемые ошибки без полной оценки алгоритмом.")

    doc.add_paragraph("3. Redundancy Checker Prompt:", style='Normal').bold = True
    doc.add_paragraph("Текст: «История диалога... Следующий сценарный вопрос... Ответил ли ученик уже на суть этого вопроса? Ответь ДА или НЕТ.»")
    doc.add_paragraph("Пояснение: Проверяет, покрыл ли ученик в прошлых ответах суть следующего по плану вопроса, чтобы избежать дублирования.")

    doc.add_paragraph("4. Scenario Generator Prompt:", style='Normal').bold = True
    doc.add_paragraph("Текст: «Ты — эксперт-методист... Сначала проверь тему на физические противоречия... Формат твоего ответа должен быть СТРОГО валидным JSON...»")
    doc.add_paragraph("Пояснение: Промпт для режима учителя. Строго требует JSON формат и предварительно отсеивает антинаучные темы.")

    doc.add_heading('5.6 Используемые решения и ограничения', level=1)
    doc.add_paragraph("Опробованные и отклоненные решения:", style='Normal').bold = True
    doc.add_paragraph("В ходе разработки предпринималась попытка использовать модели Gemini Live (Multimodal Live API) для ведения диалога напрямую. Однако от этого решения было решено отказаться, так как протокол WebSockets и строгие требования к сырому аудио (PCM) нарушают событийную HTTP REST/Webhook архитектуру бота, а также делают невозможной надежную работу с изображениями и строгим форматированием ответов.")

    doc.add_paragraph("Ограничения текущей реализации:", style='Normal').bold = True
    doc.add_paragraph("1. Отсутствие строгой математической валидации: Агент не использует внешние инструменты (например, Python REPL) для символьной проверки формул и единиц измерения, полагаясь только на логику LLM.\n"
                      "2. Алгоритм поиска типичных ошибок: Поиск опирается на библиотеку difflib, которая проверяет лексическое совпадение текста вопроса, а не семантическое (может пропустить синонимы).")
    
    doc.add_paragraph("Способы улучшения:", style='Normal').bold = True
    doc.add_paragraph("Переход на векторный поиск (RAG / Embeddings) для более точного определения типичных ошибок и внедрение инструментального вызова (Function Calling / Code Execution) для математических вычислений.")

    doc.add_paragraph("Статистика трат (Google AI Studio):", style='Normal').bold = True
    
    stat_table = doc.add_table(rows=1, cols=5)
    stat_table.style = 'Table Grid'
    s_hdr = stat_table.rows[0].cells
    s_hdr[0].text = 'Model'
    s_hdr[1].text = 'Category'
    s_hdr[2].text = 'RPM'
    s_hdr[3].text = 'TPM'
    s_hdr[4].text = 'RPD'
    
    stats = [
        ("Gemini 3.8 Flash", "Text-out models", "4 / 5", "3.92K / 250K", "28 / 20"),
        ("Gemini 3.6 Flash", "Text-out models", "3 / 5", "4.07K / 250K", "17 / 20"),
        ("Gemini 3.7 Flash", "Text-out models", "3 / 5", "185.52K / 250K", "20 / 20"),
        ("Gemini 3.5 Flash", "Text-out models", "2 / 5", "4.95K / 250K", "19 / 20"),
        ("Gemini 3.5 Flash Lite", "Text-out models", "5 / 15", "8.21K / 250K", "38 / 500")
    ]
    
    for stat in stats:
        row_cells = stat_table.add_row().cells
        for i in range(5):
            row_cells[i].text = stat[i]

    doc.add_heading('5.7 Используемые инструменты и заметки', level=1)
    doc.add_paragraph("1. Google Gemini API (https://ai.google.dev/): Использованы модели Flash для генерации контента и анализа текста/изображений.\n"
                      "2. Aiogram 3.x (https://docs.aiogram.dev/): Фреймворк для асинхронного управления Telegram ботом.\n"
                      "3. FastAPI (https://fastapi.tiangolo.com/): Фреймворк для обработки HTTP-запросов и голосовых данных.\n"
                      "4. База знаний: JSON-документы со строгой структурой строк (rows) и столбцов (columns), описывающих исследовательский сценарий.")

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    doc.save(os.path.join(base_dir, 'Отчет_по_агенту.docx'))

if __name__ == '__main__':
    create_doc()
