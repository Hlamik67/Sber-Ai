# Сбер ИИ 

## Цель: 
Создать ИИ-ассистента, который будет отвечать на вопросы по внутренним документам. 

## Принцип работы: 
Сотрудник пишет свой вопрос в произвольной форме, ИИ-ассистент понимает, что от него хочет сотрудник и ищет ответ в внутренней базе данных, выдает ответ на вопрос и откуда он это взял. ИИ-ассистент ничего не придумывает, выдает только то, что нашел в внутренних документах Сбера. 

## Запуск сайта
Windows:
docker compose up

Далее в новом терминале 
- python -m venv venv
- .\venv\Scripts\activate
- pip install -r requirements.txt
- streamlit run app.py

Mac:
docker compose up

Далее в новом терминале 
- python3 -m venv venv
- source venv/bin/activate
- pip install -r requirements.txt
- streamlit run app.py

<!-- # Потом допишу  -->

