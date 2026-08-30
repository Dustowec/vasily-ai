import asyncio
import sys

from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from qasync import QEventLoop, asyncSlot

from core.agent import AgentCore

# Импортируем ваше готовое асинхронное ядро
from core.config import Config


class VasilyGui(QMainWindow):
    def __init__(self):
        super().__init__()
        self.agent = None
        self.init_ui()

        # Запускаем асинхронную инициализацию Василия прямо при старте окна
        asyncio.create_task(self.init_agent())

    def init_ui(self):
        """Сборка красивого современного интерфейса."""
        self.setWindowTitle("Vasily AI - Локальный Агент (v0.0.0.1-pre-pre-alpha)")
        self.resize(800, 600)

        # Главный виджет и вертикальный слой
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)

        # Поле статуса (Watchdog-лайт)
        self.status_label = QLabel("⏳ Инициализация ядра Василия...")
        self.status_label.setStyleSheet("font-weight: bold; color: #ffa500;")
        main_layout.addWidget(self.status_label)

        # Окно истории чата (только для чтения)
        self.chat_area = QTextEdit()
        self.chat_area.setReadOnly(True)
        self.chat_area.setStyleSheet(
            """
            background-color: #1e1e1e;
            color: #ffffff;
            font-family: 'Consolas', 'Courier New', monospace;
            font-size: 13px;
        """
        )
        main_layout.addWidget(self.chat_area)

        # Нижняя панель: ввод текста и кнопка отправки
        input_layout = QHBoxLayout()

        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Введите запрос агенту или 'status'...")
        self.input_field.setEnabled(False)  # Блокируем, пока агент не инициализировался
        # По нажатию Enter в поле ввода — отправляем сообщение
        self.input_field.returnPressed.connect(self.handle_send)
        input_layout.addWidget(self.input_field, stretch=4)

        self.send_button = QPushButton("Отправить")
        self.send_button.setEnabled(False)
        self.send_button.clicked.connect(self.handle_send)
        input_layout.addWidget(self.send_button, stretch=1)

        main_layout.addLayout(input_layout)

    async def init_agent(self):
        """Асинхронная инициализация вашего AgentCore."""
        try:
            config = Config.load()
            config.validate()

            self.agent = AgentCore(config)
            await self.agent.initialize()

            # Агент готов — оживляем интерфейс
            self.status_label.setText("🟢 Василий готов к работе")
            self.status_label.setStyleSheet("font-weight: bold; color: #4caf50;")
            self.input_field.setEnabled(True)
            self.send_button.setEnabled(True)
            self.append_chat("Система", "Ядро успешно запущено! Задайте мне любой вопрос.")
        except Exception as e:
            self.status_label.setText(f"🔴 Ошибка инициализации: {e}")
            self.status_label.setStyleSheet("font-weight: bold; color: #f44336;")

    @asyncSlot()  # Этот декоратор от qasync позволяет методу Qt быть полноценной async-функцией!
    async def handle_send(self):
        """Обработка отправки сообщения Василию."""
        text = self.input_field.text().strip()
        if not text:
            return

        # Очищаем поле ввода и временно блокируем интерфейс, чтобы пользователь не спамил
        self.input_field.clear()
        self.input_field.setEnabled(False)
        self.send_button.setEnabled(False)

        # Выводим сообщение пользователя в чат
        self.append_chat("Вы", text)
        self.status_label.setText("⚙️ Василий думает и дергает плагины...")

        try:
            # Передаем запрос в ваше родное асинхронное ядро handle_request!
            response = await self.agent.handle_request({"text": text})

            if response.get("status") == "success":
                answer = response.get("message", "Нет ответа")
            else:
                answer = f"[Ошибка] {response.get('message', 'Неизвестный сбой ядра')}"

            self.append_chat("Василий", answer)

        except Exception as e:
            self.append_chat("Система", f"Произошла критическая ошибка: {e}")
        finally:
            # Возвращаем интерфейс в рабочее состояние
            self.status_label.setText("🟢 Василий готов к работе")
            self.input_field.setEnabled(True)
            self.send_button.setEnabled(True)
            self.input_field.setFocus()

    def append_chat(self, author: str, message: str):
        """Удобное добавление текста в окно чата."""
        color = "#e1f5fe" if author == "Вы" else "#f1f8e9"
        if author == "Система":
            color = "#ffcc80"

        formatted_text = f"<b style='color: {color};'>[{author}]:</b> {message}<br>"
        self.chat_area.append(formatted_text)


def main():
    app = QApplication(sys.argv)

    # Магия скрещивания: создаем асинхронный цикл для Qt через qasync
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    gui = VasilyGui()
    gui.show()

    # Запускаем бесконечный цикл приложения
    with loop:
        loop.run_forever()


if __name__ == "__main__":
    main()
