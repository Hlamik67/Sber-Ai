# Указания команде

Этот файл для нас. В главный README его не переносить: там описание проекта для тех, кто будет запускать.

Новые файлы не заводим. Каждый пишет только в свои.

Просьба коментарии в файлах не удалять. 

## Как скачать репозиторий 

Скачиваем весь репозиторий себе на комп через PowerShell/Терминал:
Mac: 
cd ~/Desktop
Windows:
cd $HOME\Desktop 
git clone https://github.com/Hlamik67/Sber-Ai.git
cd Sber-Ai

## Кто где пишит 

Настя пишет README.md, TEAM.md, .gitignore, requirements.txt, docker-compose.yml, data/qa.jsonl, data/raw/README.md, три документа в data/raw и собирает report/final.md. Модели, индекс и экран она не пишет.

Ксюша пишет только app.py: загрузка, вопрос, один ответ и источник. Нарезку и метрики не делает, переключатель способа не ставит.

Лиза Р пишет src/ingest.py, src/chunking.py, src/index.py и поиск по словам в src/search.py. Эмбеддинги и таблицу качества не делает.

Лиза К пишет поиск по смыслу и связку в src/search.py, метрики в src/eval.py и кладёт модель в models. Индекс и экран не поднимает, ответ не генерирует.

notebooks — общая папка для черновиков. Демо и оценка оттуда не запускаются.

В чужих файлах ничего не пишем 

## Подгрузка изменений с Github себе: 

Mac:
cd ~/Desktop/Sber-Ai
git pull
Windows:
cd $HOME\Desktop\Sber-Ai
git pull

Это делать каждый раз перед началом работы, чтобы все изменения с Github были на локальном компьютере 

## Выгрузка в Github 

Каждый один раз настраивает это у себя, в папке проекта.
cd $HOME\Desktop\Sber-Ai
git config user.name "Имя Фамилия"
git config user.email "почта указанная на Github"

Для проверки: 
git config user.name
git config user.email

Выгрузка:
git pull
git status
git add .  
git commit -m "Что изменилось"
git push

Примечание 
git add . - выгружает все измененные файлы, если нужно выгрузить определенный файл, то вместо точки название файла

## Кто что сделал (пояснительная записка каждую неделю)

