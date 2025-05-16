from fastapi import FastAPI, Request, HTTPException, Form, Body
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
import os
import logging
import httpx
import logging
import aioboto3
import uvicorn
import asyncio
import json
from botocore.exceptions import ClientError
from dotenv import load_dotenv
from openai import OpenAI
from src.prompts import SYSTEM_PROMPT
import pathlib
import time
from datetime import datetime, timezone, timedelta


# Load environment variables
load_dotenv()

# Initialize logging
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize templates
templates = Jinja2Templates(directory="src/templates")

# DynamoDB Configuration
DYNAMODB_ENDPOINT = os.getenv("DYNAMODB_ENDPOINT", "http://localhost:8001")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "dummy")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "dummy")
AWS_REGION = os.getenv("AWS_REGION", "us-west-2")

WELCOME_TABLE = "WelcomeQueue"
MESSENGER_BASE_URL = os.getenv("MESSENGER_BASE_URL", "http://localhost:9000")
WELCOME_DELAY_MIN = int(os.getenv("WELCOME_DELAY_MINUTES", "10"))
WELCOME_CHECK_INTERVAL = int(os.getenv("WELCOME_CHECK_INTERVAL", "600"))

# TABLE_NAME = "Users"
# FAISS_SERVICE_URL = "http://172.17.0.1:8010/search"
# db = None

TABLE_NAME = os.getenv("TABLE_NAME", "Users")
FAISS_SERVICE_URL = os.getenv("FAISS_SERVICE_URL", "http://172.17.0.1:8010/search")

# --- Utility Functions ---
async def get_dynamodb_resource():
    session = aioboto3.Session()
    async with session.client(
        "dynamodb",
        endpoint_url=DYNAMODB_ENDPOINT,
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    ) as dynamodb:
        return dynamodb


async def query_faiss_service(query: str):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            FAISS_SERVICE_URL,
            json={"db_name": "db_diseases", "query": query, "top_k": 3}
        )
        if response.status_code == 200:
            return response.json().get("results", [])
        else:
            logger.error(f"Ошибка запроса в faiss_service: {response.text}")
            return []

async def get_user_data_from_db(user_id: str) -> dict:
    try:
        async with aioboto3.Session().client(
            "dynamodb",
            endpoint_url=DYNAMODB_ENDPOINT,
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=AWS_REGION,
        ) as dynamodb:
            response = await dynamodb.get_item(
                TableName=TABLE_NAME,
                Key={"user_id": {"S": user_id}}
            )
            user_data = response.get("Item")
            if not user_data:
                return None

            # Десериализация conversation_history
            conversation_history_str = user_data.get("conversation_history", {}).get("S", "[]")
            try:
                conversation_history = json.loads(conversation_history_str)
            except json.JSONDecodeError:
                conversation_history = []

            # Десериализация scenario_history
            scenario_history_str = user_data.get("scenario_history", {}).get("S", "[]")
            try:
                scenario_history = json.loads(scenario_history_str)
            except json.JSONDecodeError:
                scenario_history = []

            return {
                "id": user_data.get("user_id", {}).get("S", ""),
                "name": user_data.get("name", {}).get("S", ""),
                "birthday": user_data.get("birthday", {}).get("S", ""),
                "health_diary": user_data.get("health_diary", {}).get("S", ""),
                "conversation_history": conversation_history,
                "scenario_history": scenario_history
            }
    except ClientError as e:
        logger.error(f"Ошибка при запросе к таблице Users: {e}")
        raise HTTPException(status_code=500, detail="Ошибка при запросе к базе данных.")
    except Exception as e:
        logger.error(f"Общая ошибка при взаимодействии с DynamoDB: {e}")
        raise HTTPException(status_code=500, detail="Общая ошибка при запросе к базе данных.")


async def get_chat_messages(chat_id: str, user_id: str) -> list[dict]:
    url = f"{MESSENGER_BASE_URL}/internal/messenger/last-messages-by-chat"
    payload = {"userId": user_id, "chatIds": [chat_id]}
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        # правильный ключ – "data"
        items = resp.json().get("data", [])
        return items[0:1]                      # вернём хотя бы один элемент, если есть



async def send_welcome_message(chat_id: str, user_id: str) -> None:
    url = f"{MESSENGER_BASE_URL}/internal/messenger"
    now_ms = int(time.time() * 1000)
    payload = {
        "chatId":   chat_id,
        "time":     now_ms,                     # ← ДОБАВИЛИ
        "message": (
            "Hello, beautiful! Welcome to Eshe. "
            "This isn’t just a period tracker — Eshe is your all-in-one "
            "wellness companion. Would you like a quick tour of what’s inside?"
        ),
        "type":     "SIMPLE",
        "senderId": "support",
        "systemAction": {
            "id":         "welcome_buttons",
            "text":       "Choose a topic:",
            "answerType": "BUTTON",
            "inputType":  "NONE",
            "buttons": [
                {"id": "btn_checkups",  "title": "Tell me about Check-Ups"},
                {"id": "btn_courses",   "title": "Tell me about Mini-Courses"},
                {"id": "btn_articles",  "title": "Tell me about Articles"},
                {"id": "btn_calendars", "title": "Tell me about Calendars"}
            ]
        }
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, timeout=10)
        if resp.status_code >= 400:           # подробный лог, если снова 4xx/5xx
            logger.error("Messenger 400-response: %s", resp.text)
        resp.raise_for_status()
        logger.info("Welcome-сообщение отправлено user=%s", user_id)




async def put_into_welcome_queue(user_id: str) -> None:
    async with aioboto3.Session().client(
        "dynamodb",
        endpoint_url=DYNAMODB_ENDPOINT,
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    ) as dynamodb:
        try:
            await dynamodb.put_item(
                TableName=WELCOME_TABLE,
                Item={
                    "user_id": {"S": user_id},
                    "created_at": {"N": str(int(time.time()))},
                    "processed": {"BOOL": False},
                    "failed_attempts": {"N": "0"},
                },
                ConditionExpression="attribute_not_exists(user_id)"   # <-- ключевая строка
            )
        except ClientError as ce:
            # «ConditionalCheckFailed» - OK, запись уже была
            if ce.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise


async def mark_processed(user_id: str) -> None:
    async with aioboto3.Session().client(
        "dynamodb",
        endpoint_url=DYNAMODB_ENDPOINT,
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    ) as dynamodb:
        await dynamodb.update_item(
            TableName=WELCOME_TABLE,
            Key={"user_id": {"S": user_id}},
            UpdateExpression="SET processed = :p",
            ExpressionAttributeValues={":p": {"BOOL": True}}
        )


# ─────────────────────────  фоновая задача  ──────────────────────────────────

async def welcome_worker() -> None:
    """Циклически проверяет WelcomeQueue и отправляет welcome при необходимости."""
    while True:
        try:
            async with aioboto3.Session().client(
                "dynamodb",
                endpoint_url=DYNAMODB_ENDPOINT,
                region_name=AWS_REGION,
                aws_access_key_id=AWS_ACCESS_KEY_ID,
                aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            ) as dynamodb:

                scan_resp = await dynamodb.scan(TableName=WELCOME_TABLE)
                for item in scan_resp.get("Items", []):
                    if item.get("processed", {}).get("BOOL"):
                        continue  # уже обработали

                    user_id = item["user_id"]["S"]
                    created_at = int(item["created_at"]["N"])
                    if time.time() - created_at < WELCOME_DELAY_MIN * 60:
                        continue  # ещё рано

                    chat_id = f"sup:{user_id}"
                    try:
                        msgs = await get_chat_messages(chat_id, user_id)
                        # проверяем, есть ли хоть одно сообщение, где senderId == user_id
                        has_user_msg = any(m.get("lastMessage", {}).get("senderId") == user_id for m in msgs)
                        if not has_user_msg:
                            await send_welcome_message(chat_id, user_id)
                        # вне зависимости, отправили или юзер уже ответил — помечаем “processed”
                        await mark_processed(user_id)
                    except Exception as exc:
                        logger.error(f"Не удалось обработать user={user_id}: {exc}")

        except Exception as e:
            logger.exception(f"welcome_worker global error: {e}")

        await asyncio.sleep(WELCOME_CHECK_INTERVAL)



# --- Endpoints ---

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(welcome_worker())

@app.get("/", response_class=HTMLResponse)
async def home_page(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/users", response_class=HTMLResponse)
async def get_users(request: Request):
    try:
        dynamodb = await get_dynamodb_resource()
        response = await dynamodb.scan(TableName=TABLE_NAME)
        users = response.get("Items", [])
        if not users:
            return RedirectResponse(url="/add_user", status_code=303)

        formatted_users = []
        for user in users:
            # conversation_history
            conversation_history_str = user.get("conversation_history", {}).get("S", "[]")
            try:
                conversation_history = json.loads(conversation_history_str)
            except json.JSONDecodeError:
                conversation_history = []

            # scenario_history
            scenario_history_str = user.get("scenario_history", {}).get("S", "[]")
            try:
                scenario_history = json.loads(scenario_history_str)
            except json.JSONDecodeError:
                scenario_history = []

            formatted_users.append({
                "id": user.get("user_id", {}).get("S", ""),
                "name": user.get("name", {}).get("S", ""),
                "birthday": user.get("birthday", {}).get("S", ""),
                "health_diary": user.get("health_diary", {}).get("S", ""),
                "conversation_history": conversation_history,
                "scenario_history": scenario_history
            })

        return templates.TemplateResponse("users_table.html", {"request": request, "users": formatted_users})
    except Exception as e:
        logger.error(f"Error retrieving users: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve users.")



@app.get("/add_user", response_class=HTMLResponse)
async def add_user_form(request: Request):
    return templates.TemplateResponse("user_form.html", {"request": request})


@app.post("/add_user")
async def add_user(payload: dict):
    """
    Добавление или обновление пользователя.
    Дополнительно кладём юзера в очередь WelcomeQueue, если он там ещё не был.
    """
    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Поле user_id обязательно")

    try:
        dynamodb = await get_dynamodb_resource()

        resp = await dynamodb.get_item(
            TableName=TABLE_NAME,
            Key={"user_id": {"S": user_id}}
        )
        current = resp.get("Item", {})

        hist_raw = current.get("conversation_history", {}).get("S", "[]")
        try:
            conversation_history = json.loads(hist_raw)
        except json.JSONDecodeError:
            conversation_history = []

        updated_item = {
            "user_id": {"S": user_id},
            "name": {"S": payload.get("name", current.get("name", {}).get("S", ""))},
            "birthday": {"S": payload.get("birthday", current.get("birthday", {}).get("S", ""))},
            "health_diary": {"S": payload.get("health_diary", current.get("health_diary", {}).get("S", ""))},
            "conversation_history": {"S": json.dumps(conversation_history)},
        }

        await dynamodb.put_item(TableName=TABLE_NAME, Item=updated_item)

        # Кладём в WelcomeQueue (если это первый визит ― условие в put_into_welcome_queue)
        try:
            await put_into_welcome_queue(user_id)
        except ClientError as ce:
            if ce.response["Error"]["Code"] != "ConditionalCheckFailedException":
                logger.error(f"Ошибка WelcomeQueue для {user_id}: {ce}")

        return RedirectResponse(url="/users", status_code=303)

    except Exception as e:
        logger.error(f"Error add/update user: {e}")
        raise HTTPException(status_code=500, detail="Failed to add/update user")



def process_question_sync(user_prompt):
    # Отправляем запрос в OpenAI
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt}
    ]

    client = OpenAI()
    completion = client.chat.completions.create(
        model="gpt-4o-mini-2024-07-18",
        messages=messages,
        temperature=0
    )

    answer = completion.choices[0].message.content
    input_tokens = completion.usage.prompt_tokens
    output_tokens = completion.usage.completion_tokens

    metadata = {
        "system_prompt": SYSTEM_PROMPT,
        "user_prompt": user_prompt,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens
    }

    return answer, metadata

@app.get("/process_question", response_class=HTMLResponse)
async def interact_page(request: Request):
    return templates.TemplateResponse("interact.html", {"request": request, "answer": None, "metadata": None})


@app.post("/process_question")
async def process_question(request: Request, payload: dict = Body(...)):
    user_id = payload.get("user_id")
    question = payload.get("question")
    use_anamnesis = payload.get("use_anamnesis", False)
    use_knowledge_base = payload.get("use_knowledge_base", False)
    use_conversation_history = payload.get("use_conversation_history", False)  # Новый параметр

    if not user_id:
        raise HTTPException(status_code=400, detail="Поле user_id обязательно.")

    try:
        user_prompt = ""

        # Получаем данные пользователя (для истории диалога и анамнеза)
        user_data = await get_user_data_from_db(user_id)
        conversation_history = user_data.get("conversation_history", []) if user_data else []

        # Использование анамнеза
        if use_anamnesis and user_data:
            user_prompt += f"Информация о пользователе:\n"
            user_prompt += f"ID: {user_data.get('id', '')}\n"
            user_prompt += f"Имя: {user_data.get('name', '')}\n"
            user_prompt += f"Дата рождения: {user_data.get('birthday', '')}\n"
            user_prompt += f"Дневник здоровья: {user_data.get('health_diary', '')}\n\n"

        # Использование истории диалога в user_prompt
        if use_conversation_history and conversation_history:
            history_text = "\n".join(
                [f"{msg['role']}: {msg['message']}" for msg in conversation_history]
            )
            user_prompt += f"История диалога:\n{history_text}\n\n"

        # Использование базы знаний
        if use_knowledge_base:
            relevant_chunks = await query_faiss_service(question)
            if relevant_chunks:
                user_prompt += "\nИнформация из базы знаний:\n"
                for idx, chunk in enumerate(relevant_chunks):
                    user_prompt += f"{idx + 1}. {chunk['text'].strip()}\n"

        # Добавление вопроса
        user_prompt += (
            f"\n\nВопрос: {question}. Ответь на вопрос на основе предоставленной информации и собственных "
            f"знаний. Можно отвечать на вопросы связанные с медициной, здоровьем, врачами, лекарствами и подобной тематикой."
        )

        logger.info(f"Сформированный user_prompt для пользователя {user_id}: {user_prompt}")

        # Выполнение синхронной функции
        answer, metadata = await asyncio.to_thread(process_question_sync, user_prompt)

        # Обновление истории диалога в базе данных (всегда)
        new_entry_user = {"role": "user", "message": question, "timestamp": int(asyncio.get_event_loop().time())}
        new_entry_model = {"role": "model", "message": answer, "timestamp": int(asyncio.get_event_loop().time())}

        conversation_history.append(new_entry_user)
        conversation_history.append(new_entry_model)

        # Ограничение количества хранимых сообщений (например, последние 6)
        conversation_history = conversation_history[-6:]

        # Копирование текущих данных пользователя и обновление истории
        updated_data = user_data.copy() if user_data else {}
        updated_data["conversation_history"] = conversation_history

        # Сохранение обновленных данных в базе данных
        async with aioboto3.Session().client(
            "dynamodb",
            endpoint_url=DYNAMODB_ENDPOINT,
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=AWS_REGION,
        ) as dynamodb:
            await dynamodb.put_item(
                TableName=TABLE_NAME,
                Item={
                    "user_id": {"S": user_id},
                    "name": {"S": updated_data.get("name", "")},
                    "birthday": {"S": updated_data.get("birthday", "")},
                    "health_diary": {"S": updated_data.get("health_diary", "")},
                    "conversation_history": {"S": json.dumps(updated_data["conversation_history"])}
                }
            )

        # Возвращаем JSON-ответ
        return JSONResponse(content={
            "answer": answer,
            "metadata": metadata
        })

    except Exception as e:
        logger.error(f"Ошибка при обработке вопроса: {e}")
        return JSONResponse(content={
            "error": "Ошибка: невозможно обработать запрос"
        }, status_code=500)


@app.post("/scenario/execute")
async def execute_scenario(payload: dict = Body(...)):
    """
    Универсальный эндпоинт, который принимает:
    - scenarioFileName: название файла сценария (например, "health_ai_assistant_scenario.json")
    - metadata
    - userAnswer
    """
    scenario_filename = payload.get("scenarioFileName")  # Новый параметр
    metadata = payload.get("metadata", {})
    user_answer = payload.get("userAnswer", {})

    if not scenario_filename:
        raise HTTPException(status_code=400, detail="Поле 'scenarioFileName' обязательно")

    # Загружаем сценарий из файла, который находится в папке scenarios/
    BASE_DIR = pathlib.Path(__file__).parent
    scenario_path = BASE_DIR / "scenarios" / scenario_filename
    try:
        with open(scenario_path, "r", encoding="utf-8") as f:
            scenario = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Не удалось загрузить сценарий: {str(e)}")

    # Извлекаем ключевые поля
    user_id = metadata.get("userId")
    cycle_day = metadata.get("cycleDay")  # пример использования дня цикла

    if not user_id:
        raise HTTPException(status_code=400, detail="Поле metadata.userId обязательно")

    # Получаем "steps" из scenario
    steps = scenario.get("steps", [])
    # Например, ищем step, на который ссылается user_answer, или первый шаг
    current_step_id = user_answer.get("stepId")
    selected_button_id = user_answer.get("selectedButtonId")

    # -- 1. Сохраняем ответ пользователя в БД (по аналогии с разговором) --
    # Загружаем из БД данные пользователя, чтобы объединить историю
    user_data = await get_user_data_from_db(user_id)
    if not user_data:
        raise HTTPException(status_code=404, detail=f"Пользователь с ID {user_id} не найден")

    # Предположим, что в user_data мы создадим/храним "scenario_history".
    # Если нет, инициализируем пустой список.
    scenario_history = user_data.get("scenario_history", [])

    # Добавим запись о том, что пользователь ответил
    if current_step_id and selected_button_id:
        scenario_history.append({
            "role": "user",
            "stepId": current_step_id,
            "selectedButtonId": selected_button_id
        })

    # -- 2. Определяем, какой шаг дальше показывать --
    #    Либо это первый запрос (нет ответа от пользователя),
    #    либо пользователь уже ответил и нужно перейти к следующему шагу.
    next_step_id = None

    if current_step_id and selected_button_id:
        # Ищем в scenario сам шаг current_step_id
        for st in steps:
            if st.get("stepId") == current_step_id:
                # Ищем в buttons кнопку, у которой id == selected_button_id
                buttons = st.get("buttons", [])
                for btn in buttons:
                    if btn.get("id") == selected_button_id:
                        next_step_id = btn.get("nextActionId")
                        break
                break
    else:
        # Если первый запрос без userAnswer, берем "первый" шаг сценария
        # Либо если сценарий где-то явно хранит firstStepId
        next_step_id = scenario.get("firstStepId")
        # Или можно взять steps[0]["stepId"] для упрощения

    if not next_step_id:
        # Если у нас нет next_step_id, возможно это конец сценария
        # или ошибка логики. Пока выкинем исключение.
        return JSONResponse(content={
            "message": "Сценарий завершен или не найден следующий шаг.",
            "scenarioFinished": True
        })

    # -- 3. Ищем шаг next_step_id и готовим ответ --
    selected_step = None
    for st in steps:
        if st.get("stepId") == next_step_id:
            selected_step = st
            break

    if not selected_step:
        # Если не нашли такой шаг - сценарий завершается
        return JSONResponse(content={
            "message": "Следующий шаг не найден. Сценарий завершён или содержит ошибку.",
            "scenarioFinished": True
        })

    # -- 4. Обновляем scenario_history, добавляя инфу о том,
    #       какой шаг мы собираемся показать
    scenario_history.append({
        "role": "system",
        "stepId": next_step_id,
        "messages": selected_step.get("messages", [])
    })

    # Сохраним всё назад в БД
    # (по аналогии с тем, как сохраняется conversation_history)
    updated_data = user_data.copy()
    updated_data["scenario_history"] = scenario_history

    # Сохраняем
    async with aioboto3.Session().client(
        "dynamodb",
        endpoint_url=DYNAMODB_ENDPOINT,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name=AWS_REGION,
    ) as dynamodb:
        await dynamodb.put_item(
            TableName=TABLE_NAME,
            Item={
                "user_id": {"S": user_id},
                "name": {"S": updated_data.get("name", "")},
                "birthday": {"S": updated_data.get("birthday", "")},
                "health_diary": {"S": updated_data.get("health_diary", "")},
                "conversation_history": {"S": json.dumps(updated_data.get("conversation_history", []))},
                "scenario_history": {"S": json.dumps(updated_data["scenario_history"])}
            }
        )

    # -- 5. Готовим ответ, чтобы фронтенд мог показать пользователю шаг --
    response_payload = {
        "scenarioId": scenario.get("scenarioId"),
        "nextStepId": next_step_id,
        "step": {
            "stepId": selected_step.get("stepId"),
            "name": selected_step.get("name"),
            "messages": selected_step.get("messages", []),
            "answerType": selected_step.get("answerType"),
            "buttons": selected_step.get("buttons", [])
        }
    }

    return JSONResponse(content=response_payload)



if __name__ == "__main__":
    uvicorn.run("src.main:app", host="0.0.0.0", port=8080, reload=True)
