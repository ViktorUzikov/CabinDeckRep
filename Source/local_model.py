from llama_cpp import Llama
from huggingface_hub import hf_hub_download
from PyQt6.QtCore import QThread, pyqtSignal
# from json import dumps, loads
# from .db import Database
import os, sys
from pathlib import Path

if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w')


class UnloadedModel(Exception):
    pass


history = []

llm = None

TOOLS = [
    {'type': 'function',
     'function': {
         'name': 'get_flights',
         'description': 'Получить ВСЕ рейсы из базы. Вызывай когда просят получить, показать рейсы. Аргументов не требуется. Не задавай уточняющих вопросов. Return list with params for every flight in list: id, number, departure, arrival, datetime',
         'parameters': {
             'type': 'object',
             'properties': {},
             'required': []
         }
     }}
]

SYSTEM_PROMPT = '''
Ты — помощник CabinDeck, приложения для бортпроводников.
Отвечай на языке пользователя, кратко и по делу.

ДАННЫЕ О РЕЙСАХ
Если пользователь просит показать, назвать, перечислить рейсы
или узнать данные конкретного рейса, используй инструмент get_flights.
Для вызова передавай пустой объект аргументов.
Не задавай уточняющих вопросов перед получением списка.

Вызывай инструмент только через механизм tool calling.
Не пиши вызов функции в тексте ответа.
Не создавай код, шаблоны, переменные, циклы или условия.
Пользователю нужен готовый ответ, а не инструкция по его созданию.

После получения результата инструмента:
- Используй только полученные записи.
- Не придумывай номера рейсов, маршруты, даты и время.
- Не путай ID записи с номером рейса.
- Если пользователь просит все рейсы, перечисли все записи.
- Если пользователь указал номер, найди точное совпадение.
- Если совпадений несколько, попроси уточнить маршрут или дату.
- Если список пуст, сообщи, что в базе пока нет рейсов.
- Если инструмент вернул ошибку, сообщи об ошибке получения данных.
- Не обещай получить данные, если они уже получены.
- Если данных нет, не подставляй примеры вместо настоящих рейсов.

ФОРМАТ ОТВЕТА
Пиши готовый ответ простым HTML.
Разрешены только теги <p>, <b>, <br> и закрывающие </p>, </b>.
Не используй Markdown, блоки кода и шаблонные конструкции.
Каждый абзац заключай в <p>...</p>.
Закрывай каждый тег <b> внутри того же абзаца.
Не используй атрибуты тегов.

Каждый рейс оформляй отдельным абзацем:
номер выделяй жирным, затем с новой строки указывай отправление,
прибытие, дату и время.
Подставляй реальные значения непосредственно в ответ.
Не выводи названия переменных или обозначения полей вместо значений.

ВОЗМОЖНОСТИ ПРИЛОЖЕНИЯ
Пользователь может создавать рейсы и переключаться между ними.
В выбранный рейс можно добавлять пассажиров с именем, местом,
классом обслуживания и необязательным фото.
Данные пассажира можно просмотреть, фото — изменить.
На карте мест красное место занято, зелёное свободно.
Левый щелчок по занятому месту выбирает пассажира.
Правый щелчок по занятому месту открывает меню выдачи питания.
У каждого рейса свой список питания с количеством.
Позиции можно добавлять, переименовывать и менять их количество.
Выдача питания уменьшает количество на один, но не ниже нуля.

Тебе доступно только получение рейсов через get_flights.
Остальные действия объясняй как действия пользователя в интерфейсе.
Не утверждай, что ты изменил данные.
Не придумывай названия кнопок или расположение элементов интерфейса.
'''


class StreamWorker(QThread):
    chunk = pyqtSignal(str)
    done = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, prompt: str):
        super().__init__()
        self.prompt = prompt
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        if llm is None:
            self.error.emit('Модель не загружена')

            # return
        #
        # worker_db = Database('database.db')
        #
        # known_flights = set()

        stream = None

        messages = ([{'role': 'system', 'content': SYSTEM_PROMPT}]
                    + history
                    + [{'role': 'user', 'content': self.prompt}])

        text = ''

        print('Начало обработки', flush=True)

        try:
            # for i in range(5):
            #     print(f'Запрос к инструментам цикл {i}', flush=True)
            #
            #     response = llm.create_chat_completion(
            #         messages=messages,
            #         tools=TOOLS,
            #         tool_choice='auto',
            #         # {'type': 'function', 'function': {'name': 'get_flights'}}
            #         max_tokens=1024,
            #         temperature=0.1,
            #         stream=False
            #     )
            #
            #     message = response['choices'][0]['message']
            #
            #     message['content'] = message.get('content', '') or ''
            #
            #     print('MESSAGE', message, flush=True)
            #     print('FINISH REASON', response['choices'][0]['finish_reason'], flush=True)
            #
            #     calls = message.get('tool_calls') or []
            #
            #     print('CALLS', calls, flush=True)
            #
            #     if not calls:
            #         message = message['content'] or ''
            #
            #         if message:
            #             messages.append({'role': 'assistant', 'content': message})
            #
            #         break
            #
            #     messages.append({'role': 'assistant', 'content': message['content'] or '', 'tool_calls': calls})
            #
            #     for call in calls:
            #         name = call['function']['name']
            #
            #         args = loads(call['function']['arguments'])
            #
            #         try:
            #             result = execute_tool(
            #                 name, args, worker_db, known_flights
            #             )
            #
            #             content = dumps({'success': True, 'result': result}, ensure_ascii=False, default=str)
            #         except Exception as e:
            #             content = dumps({'success': False, 'error': str(e)}, ensure_ascii=False)
            #
            #         messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': content})
            #
            #         print(f'Called {name} with {content}', flush=True)
            #
            #
            # else:
            #     raise RuntimeError('Превышен лимит вызовов инструментов')

            if self._stop:
                return

            if True:
                # self.chunk.emit(message)
                #
                # text = message
                pass
            else:
                print('MESSAGES', messages, flush=True)

                print('STREAM START', flush=True)

                stream = llm.create_chat_completion(messages, max_tokens=1024, temperature=0.3, stream=True)

                print('STREAM CREATED', flush=True)

                for part in stream:
                    if self._stop:
                        break

                    piece = part['choices'][0]['delta'].get('content') or ''

                    # print(piece, flush=True)

                    if piece:
                        text += piece

                        self.chunk.emit(piece)

                stream.close()

                stream = None
        except Exception as e:
            self.error.emit(str(e))

            return
        finally:
            # worker_db.close()

            if stream is not None:
                stream.close()

        if self._stop:
            return

        history.append({'role': 'user', 'content': self.prompt})
        history.append({'role': 'assistant', 'content': text})

        history[:] = history[-10:]

        self.done.emit(text)

        print('STREAM FINISHED', flush=True)


def load_model():
    global llm

    data_dir = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'CabinDeck' / 'Models'
    data_dir.mkdir(parents=True, exist_ok=True)

    model = hf_hub_download(
        repo_id='LiquidAI/LFM2.5-1.2B-Instruct-GGUF',
        # For better experience use 8B, but on slow pc use 3B or for fastest work liquid 1.3B
        filename='LFM2.5-1.2B-Instruct-Q4_K_M.gguf',
        local_dir=data_dir,
    )

    # hf_hub_download(
    #     repo_id='Qwen/Qwen2.5-7B-Instruct-GGUF',
    #     filename='qwen2.5-7b-instruct-q4_k_m-00002-of-00002.gguf',
    #     local_dir=data_dir,
    # )

    llm = Llama(
        model_path=model,
        n_ctx=4096,
        n_threads=6,
        n_gpu_layers=0,
        n_batch=512,
        verbose=False,
        chat_format='chatml-function-calling'
    )

    llm.create_chat_completion(
        messages=[{'role': 'user', 'content': 'Привет'}],
        max_tokens=16,
        temperature=0,
    )


def is_loaded() -> bool:
    return llm is not None


def execute_tool(name, args, db, known_flights):
    print('Executing tool', name)

    if name == 'get_flights':
        flights = db.get_flights()

        known_flights.update(flight[0] for flight in flights)

        return [{'id': i[0], 'number': i[1], 'departure': i[2], 'arrival': i[3],
                 'datetime': i[4]} for i in flights]

    raise ValueError(f'Unknown tool: {name}')
