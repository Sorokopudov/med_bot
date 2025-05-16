# AI Health Assistant (FastAPI + DynamoDB + FAISS + OpenAI)

- Позволяет создавать и обновлять пользователей с их анамнезом
- Отвечает на вопросы, используя OpenAI и FAISS-поиск по базе знаний
- Поддерживает пошаговые сценарии взаимодействия с пользователем
- Отправляет welcome-сообщение с задержкой (если чат пуст)

---

## 🧠 Технологии

- **FastAPI** — REST API и веб-интерфейс
- **DynamoDB (локально)** — хранение пользователей, истории диалога и сценариев
- **FAISS (отдельный сервис)** — векторный поиск по базе знаний
- **OpenAI API** — генерация умных ответов (через GPT)
- **Docker** — локальный запуск сервисов и инфраструктуры

---

## 🚀 Установка и запуск

### 1. Клонирование проекта

```bash
git clone https://your-repo-url
cd your-project-folder
```

### 2. Переменные окружения

Создай `.env` с конфигурацией:

```env
# OpenAI API Key
OPENAI_API_KEY=sk-59uq...........Yfu9c728R

# DynamoDB Configuration
DYNAMODB_ENDPOINT=http://172.17.0.1:8001
AWS_ACCESS_KEY_ID=dummy
AWS_SECRET_ACCESS_KEY=dummy
AWS_REGION=us-west-2
TABLE_NAME=Users

# FAISS
FAISS_SERVICE_URL=http://172.17.0.1:8010/search

# Messenger integration
MESSENGER_BASE_URL=http://172.17.0.1:4200
WELCOME_DELAY_MINUTES=1
WELCOME_CHECK_INTERVAL=30
```

_Убедись, что FAISS, DynamoDB и Messenger-сервисы работают по этим адресам._

### 3. Запуск сервисов

```bash
docker-compose up --build
```

---

## 🌐 Основные эндпоинты

| Метод | URL                         | Назначение                              |
|-------|-----------------------------|------------------------------------------|
| GET   | `/`                         | Главная страница                         |
| GET   | `/users`                   | Список всех пользователей                |
| GET   | `/add_user`               | Форма добавления нового пользователя     |
| POST  | `/add_user`               | Добавление / обновление пользователя     |
| POST  | `/process_question`       | Задать вопрос (использует GPT + FAISS)   |
| POST  | `/scenario/execute`       | Выполнить шаг сценария                   |

---

## 💬 Обработка сценариев (`/scenario/execute`)

Этот эндпоинт выполняет один шаг сценария, загруженного из JSON-файла.

### 🔹 Запрос

```json
{
  "scenarioFileName": "onboarding_welcome_scenario.json",
  "metadata": {
    "userId": "abc123"
  },
  "userAnswer": {
    "stepId": "welcome_step",
    "selectedButtonId": "btn_checkups"
  }
}
```

> `userAnswer` опционален — если не передан, возвращается первый шаг сценария.

### 🔹 Ответ

```json
{
  "scenarioId": "onboarding_welcome_scenario",
  "nextStepId": "checkups_step",
  "step": {
    "stepId": "checkups_step",
    "name": "Check-Ups Info",
    "messages": [
      "Check-Ups are like a quick visit to a kind, thoughtful doctor..."
    ],
    "answerType": null,
    "buttons": []
  }
}
```

### 📌 Поведение:

- Все шаги сохраняются в `scenario_history` внутри DynamoDB.
- Ответ **НЕ отправляется** в мессенджер автоматически.
- **Фронт** или отдельный обработчик должен отправить `messages` вручную.

---

## 🤖 Welcome-воркер (отложенное приветствие)

После регистрации нового пользователя через `/add_user`, в таблице создаётся запись с `processed = false`.

Фоновый воркер:

1. Сканирует пользователей с `processed = false`
2. Проверяет: если у пользователя **нет сообщений в чате от support** → отправляет welcome
3. Устанавливает `processed = true`

_Это позволяет избежать дублирования сообщений от других сервисов._

---

## 📁 Структура проекта

```
.
├── .env
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── scenarios/
│   └── onboarding_welcome_scenario.json  # JSON-файл сценария
└── src/
    ├── main.py              # FastAPI-приложение
    ├── prompts.py           # Системные промпты
    ├── db_manager.py        # Работа с DynamoDB
    ├── templates/           # HTML-шаблоны
    └── ...
```

