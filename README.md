# AI Health Assistant (FastAPI + DynamoDB + FAISS + OpenAI)

Интерактивный веб-сервис, который позволяет:
- Создавать и обновлять пользователей (с анамнезом и историей диалога)
- Отвечать на вопросы, используя OpenAI и базу знаний через FAISS
- Выполнять сценарии (из JSON-файлов) и сохранять прогресс пользователя

## Используемые технологии
- **FastAPI** — backend и HTML-интерфейс
- **DynamoDB** — хранение информации о пользователях
- **FAISS** — поиск по базе знаний
- **OpenAI API** — генерация ответов на вопросы
- **Docker** — изоляция среды

---

## Установка и запуск

### 1. Клонирование проекта

```bash
git clone https://your-repo-url
cd your-project-folder
```

## 2. Настройка переменных окружения

Создай файл .env (или используй пример ниже)
```bash
OPENAI_API_KEY=sk-xxx...

DYNAMODB_ENDPOINT=http://172.17.0.1:8000
AWS_ACCESS_KEY_ID=dummy
AWS_SECRET_ACCESS_KEY=dummy
AWS_REGION=us-west-2
TABLE_NAME=Users

FAISS_SERVICE_URL=http://172.17.0.1:8010/search
```
 Убедись, что твой FAISS-сервис и DynamoDB уже работают на указанных адресах.
 
## Запуск через Docker
```bash
docker-compose up --build
```

## Эндпоинты

    - GET / — главная страница

    - GET /users — список пользователей

    - GET /add_user — форма добавления пользователя

    - POST /add_user — добавление/обновление пользователя

    - POST /process_question — обработка вопроса

    - POST /scenario/execute — выполнение сценария

## Структура проекта
```bash
.
├── .env
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── src/
│   ├── main.py              # основной файл FastAPI
│   ├── prompts.py           # системный промпт
│   └── templates/           # HTML-шаблоны
└── scenarios/
    └── your_scenarios.json  # сценарии в формате JSON

```

# Эндпоинт `/scenario/execute`

Универсальный POST-эндпоинт для выполнения сценариев (шаг за шагом) из JSON-файла.  
Подходит для диалоговых ассистентов, пошаговых опросов и подобных сценариев.

---

## Входной формат (`application/json`)

```json
{
  "scenarioFileName": "example_scenario.json",
  "metadata": {
    "userId": "user123",
    "cycleDay": 12
  },
  "userAnswer": {
    "stepId": "start",
    "selectedButtonId": "fine"
  }
}
```

## Поля запроса

| Поле              | Обязательное | Описание                                               |
|-------------------|--------------|--------------------------------------------------------|
| `scenarioFileName`| ✅           | Имя JSON-файла сценария (в папке `scenarios/`)         |
| `metadata.userId` | ✅           | ID пользователя в базе данных                         |
| `userAnswer`      | ❌           | Ответ пользователя на текущий шаг (если не первый запрос) |

---

## Логика работы

1. Загружает сценарий из файла.
2. Если это первый шаг — возвращает `firstStepId`.
3. Если передан `userAnswer`, сохраняет выбор пользователя и определяет `nextStepId`.
4. Отправляет следующий шаг со списком сообщений и кнопок.
5. История сохраняется в `scenario_history` в DynamoDB.

## Пример запроса:
```json
{
  "scenarioFileName": "health_ai_assistant_scenario.json",
  "metadata": {
    "userId": "1",
    "cycleDay": 4
  },
  "userAnswer": {
    "stepId": "greeting_step",
    "selectedButtonId": "btn_not_great"
  }
}
```

## Пример ответа
```json
{
  "scenarioId": "health_ai_assistant_scenario",
  "nextStepId": "quick_support_step",
  "step": {
    "stepId": "quick_support_step",
    "name": "Быстрая поддержка",
    "messages": [
      "I hear you. 💛 Sometimes we just need a moment to breathe. Would you like some help right now?"
    ],
    "answerType": "BUTTON",
    "buttons": [
      {
        "id": "btn_meditation",
        "title": "Listen to a short meditation 🧘🏽‍♀️",
        "prompt": "Send me a link to the meditation section",
        "onClick": "REQUEST_GPT"
      },
      {
        "id": "btn_breathing",
        "title": "Try a calming breath exercise 🌿",
        "prompt": "Send me a link to the section with breathing exercises",
        "onClick": "REQUEST_GPT"
      },
      {
        "id": "btn_no",
        "title": "Not now, just continue",
        "nextActionId": "setup_prompt_step"
      }
    ]
  }
}
```