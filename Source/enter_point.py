import faulthandler
import os
import sys
import traceback
from datetime import datetime
from html import escape
from os.path import join, basename, abspath
from os import environ
from sqlite3 import IntegrityError
from pathlib import Path
import csv

from PyQt6.QtCore import QDateTime, Qt, pyqtSignal, QThread, QMimeData
from PyQt6.QtGui import QPixmap, QStandardItemModel, QStandardItem, QCursor, QDrag
from PyQt6.QtWidgets import QMainWindow, QApplication, QDialog, QMessageBox, QDialogButtonBox, QFileDialog, QSizePolicy, \
    QLabel, QPushButton, QMenu

from .comment_dialog_interface import Ui_Dialog as CommentDialogUI
from .consts import *
from .db import Database
from .flight_dialog_interface import Ui_Dialog as FlightDialogUI
from .food_dialog_interface import Ui_Dialog as FoodDialogUI
from .interface import Ui_MainWindow
from .local_model import load_model, is_loaded, StreamWorker
from .passenger_dialog_interface import Ui_Dialog as PassengerDialogUI

if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w')

faulthandler.enable()


def hook(exc_type, exc, tb):
    traceback.print_exception(exc_type, exc, tb)


sys.excepthook = hook


def show_menu(self, data, db, current_flight):
    menu = QMenu(self)

    for item in data:
        menu.addAction(item)

    action = menu.exec(QCursor.pos())

    if action:
        name, count = action.text().split(' - ')

        if int(count) <= 0:
            QMessageBox.warning(self, 'Ошибка', 'Нельзя выдать несуществующую еду')

            return

        db.decrease_food_by_name(name, current_flight)

        self.load_food()
        self.select_first_food()


def setup_photo_label(label: QLabel):
    label.setScaledContents(False)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
    label.setMinimumSize(1, 1)


def show_pixmap(label: QLabel, pixmap: QPixmap):
    if pixmap is None or pixmap.isNull():
        label.clear()

        return

    label.setPixmap(pixmap.scaled(
        label.size(),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation
    ))


def ask_image(widget, choose_text: str):
    try:
        photo_path, _ = QFileDialog.getOpenFileName(widget, choose_text,
                                                    'C:/Users/',
                                                    'Изображения (*.png *.jpg *.jpeg)')

        photo_pixmap = QPixmap(photo_path)

        data_dir = Path(environ.get('LOCALAPPDATA') or Path.home()) / 'CabinDeck' / 'Images'
        data_dir.mkdir(parents=True, exist_ok=True)

        photo_pixmap.save(join(data_dir, basename(photo_path)))

        return photo_pixmap, abspath(join(data_dir, basename(photo_path)))
    except:
        QMessageBox.critical(widget, 'Ошибка', 'Произошла ошибка обработки картинки')

        return False


class MovableButton(QPushButton):
    rightClicked = pyqtSignal()
    dropped = pyqtSignal(str, str)

    def __init__(self, seat):
        super().__init__(seat)

        self.setAcceptDrops(True)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return

        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(self.text())
        drag.setMimeData(mime)

        drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self, event):
        if event.source() is not self and isinstance(
                event.source(), MovableButton
        ):
            event.acceptProposedAction()

    def dropEvent(self, event):
        source = event.source()

        if isinstance(source, MovableButton):
            self.dropped.emit(source.text(), self.text())

        event.acceptProposedAction()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton and self.rect().contains(event.position().toPoint()):
            self.rightClicked.emit()

        super().mouseReleaseEvent(event)


class LoadingModelThread(QThread):
    model_loaded = pyqtSignal()

    def run(self):
        try:
            load_model()

            self.model_loaded.emit()
        except Exception:
            traceback.print_exc()


class FlightDialog(QDialog, FlightDialogUI):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setupUi(self)

        self.setWindowTitle("Новый рейс")

        self.date_time.setDateTime(QDateTime.currentDateTime())
        self.date_time.setDisplayFormat("dd.MM.yyyy HH:mm")
        self.date_time.setCalendarPopup(True)

        self.buttonBox.button(QDialogButtonBox.StandardButton.Ok).setText('Добавить')
        self.buttonBox.button(QDialogButtonBox.StandardButton.Cancel).setText('Отменить')

    def accept(self):
        if not all(field.text().strip() for field in (self.arrival, self.departure, self.number)):
            QMessageBox.warning(self, 'Ошибка', 'Заполните все поля')

            return

        super().accept()

    def get_data(self):
        return (self.number.text().strip(), self.departure.text().strip(), self.arrival.text().strip(),
                self.date_time.dateTime().toString('yyyy-MM-dd HH:mm'))


class PassengerDialog(QDialog, PassengerDialogUI):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setupUi(self)

        self.setWindowTitle("Новый пассажир")

        self.place.addItems([seat for row in cabin for seat in row if seat])

        self.buttonBox.button(QDialogButtonBox.StandardButton.Ok).setText('Добавить')
        self.buttonBox.button(QDialogButtonBox.StandardButton.Cancel).setText('Отменить')

        self.add_photo.clicked.connect(self.choose_photo)

        setup_photo_label(self.photo)

        self.photo_path = None
        self.photo_pixmap = None  # todo: deleted unused photos

    def choose_photo(self):
        pixmap = ask_image(self, f'Выберете фото для {self.name.text()}')

        if pixmap == False:
            self.photo_path = None
            self.photo_pixmap = None

            return

        self.photo_pixmap, self.photo_path = pixmap

        show_pixmap(self.photo, self.photo_pixmap)

    def accept(self):
        if not self.name.text().strip():
            QMessageBox.warning(self, 'Ошибка', 'Заполните все поля')

            return

        super().accept()

    def get_data(self):
        return (self.place.currentText().strip(), self.name.text().strip(), self.class_choice.currentText().strip(),
                self.photo_pixmap, self.photo_path)


class FoodDialog(QDialog, FoodDialogUI):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setupUi(self)

        self.setWindowTitle("Новое питание")

        self.buttonBox.button(QDialogButtonBox.StandardButton.Ok).setText('Добавить')
        self.buttonBox.button(QDialogButtonBox.StandardButton.Cancel).setText('Отменить')

    def accept(self):
        if not self.name.text().strip():
            QMessageBox.warning(self, 'Ошибка', 'Заполните все поля')

            return

        super().accept()

    def get_data(self):
        return self.name.text().strip(), int(self.count.value())


class CommentDialog(QDialog, CommentDialogUI):
    def __init__(self, parent=None, importance=False, text=''):
        super().__init__(parent)

        self.setupUi(self)

        self.setWindowTitle("Новое комментарий")

        self.importantCheckBox.setChecked(importance)

        self.commentEdit.setText(text)

        self.default_key_press = self.commentEdit.keyPressEvent

        self.commentEdit.keyPressEvent = self.new_key_press

        self.buttonBox.button(QDialogButtonBox.StandardButton.Ok).setText('Добавить')
        self.buttonBox.button(QDialogButtonBox.StandardButton.Cancel).setText('Отменить')

    def new_key_press(self, event):
        is_enter = event.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return)
        is_shift = event.modifiers() & Qt.KeyboardModifier.ShiftModifier

        if is_enter and not is_shift:
            self.accept()
        else:
            self.default_key_press(event)

    def accept(self):
        if not self.commentEdit.toPlainText().strip():
            QMessageBox.warning(self, 'Ошибка', 'Заполните все поля')

            return

        super().accept()

    def get_data(self):
        return self.commentEdit.toPlainText().strip(), self.importantCheckBox.isChecked()


class Window(QMainWindow, Ui_MainWindow):
    def __init__(self):
        super().__init__()

        self.setupUi(self)

        self.db = Database('database.db')

        self.setWindowTitle('CabinDeck')

        self.seat_buttons = dict()

        setup_photo_label(self.photo)
        setup_photo_label(self.photo_in_passengers)

        self.new_flight.clicked.connect(self.show_flight_dialog)
        self.add_passenger.clicked.connect(self.show_passenger_dialog)
        self.listView.clicked.connect(self.on_passenger_selected)
        self.food_list.clicked.connect(self.on_food_selected)
        self.add_button.clicked.connect(self.show_food_dialog)
        self.new_photo_in_passengers.clicked.connect(
            self.new_photo_callback)
        self.new_photo.clicked.connect(self.new_photo_callback)
        self.food_count.editingFinished.connect(self.on_food_changed)
        self.food_name.editingFinished.connect(self.on_food_changed)
        self.add_comment_in_passengers.clicked.connect(self.on_comment_adding)
        self.add_comment.clicked.connect(self.on_comment_adding)
        self.comments_in_passengers.doubleClicked.connect(self.on_comment_clicked)
        self.comments.doubleClicked.connect(self.on_comment_clicked)
        self.flight_choice.currentIndexChanged.connect(self.on_flight_changed)
        self.name.editingFinished.connect(self.update_passenger_from_scheme)
        self.class_in_scheme.currentTextChanged.connect(self.update_passenger_from_scheme)
        self.name_in_passengers.editingFinished.connect(self.update_passenger_from_passengers)
        self.class_in_passengers.currentTextChanged.connect(self.update_passenger_from_passengers)
        self.export_data.clicked.connect(self.export)

        self.default_key_press = self.questionType.keyPressEvent
        self.questionType.keyPressEvent = self.new_key_press

        self.model = QStandardItemModel()
        self.listView.setModel(self.model)
        self.listView.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.listView.customContextMenuRequested.connect(self.show_passenger_menu)

        self.food_model = QStandardItemModel()
        self.food_list.setModel(self.food_model)

        self.comment_model = QStandardItemModel()
        self.comments_in_passengers.setModel(self.comment_model)
        self.comments.setModel(self.comment_model)

        self.current_flight_id = -1
        self.current_passenger_in_list = -1
        self.current_food_in_list = -1

        self.chat_html = ''
        self.partial = ''
        self.worker = None

        self.build_scheme()

        self.rebuild_choice()

        self.loading_model_thread = LoadingModelThread()

        self.loading_model_thread.model_loaded.connect(self.set_ready_for_asking)

        self.loading_model_thread.start()

        self.modelAnswer.setHtml(
            '<h3>                         Модель загружается, первый запуск может занять время и интернет</h3>')

    def set_ready_for_asking(self):
        self.modelAnswer.setHtml('<h3>                         Спросите свой, <b>любой</b> вопрос</h3>')

    def new_key_press(self, event):
        is_enter = event.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return)
        is_shift = event.modifiers() & Qt.KeyboardModifier.ShiftModifier

        if is_enter and not is_shift:
            self.ask_question()
        else:
            self.default_key_press(event)

    def export(self):
        if self.current_flight_id == -1:
            QMessageBox.warning(self, 'Ошибка', 'Нельзя сделать отчет по невыбранному рейсы')

            return

        csv_filter = 'CSV файлы (*.csv)'
        txt_filter = 'TXT файлы (*.txt)'

        file_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            'Сохранить отчет',
            str(Path.home() / 'export.csv'),
            f'{csv_filter};;{txt_filter}',
        )

        if not file_path:
            return

        path = Path(file_path)
        is_csv = selected_filter == csv_filter

        if not path.suffix:
            path = path.with_suffix('.csv' if is_csv else '.txt')

        flight = self.flight_choice.currentText()
        passengers = self.db.get_passengers(self.current_flight_id)
        foods = self.db.get_foods(self.current_flight_id)

        rows = ([(flight,)] + [''] + [('Имя пассажира', 'Класс обслуживания', 'Место')] +
                list(
                    map(lambda x: (x[2], x[4], x[3]), passengers)) + [''] +
                [('Название пищи', 'Количество')] + list(
                    map(lambda y: (y[2], y[3]), foods)))

        try:
            if is_csv:
                with path.open('w', encoding='utf-8', newline='') as f:
                    writer = csv.writer(f)

                    writer.writerows(rows)
            else:
                with path.open('w', encoding='utf-8', newline='') as f:
                    f.write('\n'.join('\t'.join(map(str, row)) for row in rows) + '\n')
        except Exception:
            QMessageBox.critical(self, 'Ошибка сохранения', 'Попробуйте снова')

    def ask_question(self):
        question = self.questionType.toPlainText().strip()

        if not question:
            return

        if self.worker is not None:
            QMessageBox.warning(self, 'Ошибка', 'Дождитесь окончания ответа')

            return

        if not is_loaded():
            QMessageBox.warning(self, 'Ошибка', 'Модель еще не загрузилась')

            return

        self.questionType.clear()

        self.chat_html += (f'<p><b>Ты: </b> {escape(question)}</p>'
                           '<p><b>Ответ: </b></p>')

        self.partial = ''

        self.render_chat()

        self.worker = StreamWorker(question)

        self.worker.chunk.connect(self.on_chunk)
        self.worker.error.connect(self.on_error)
        self.worker.done.connect(self.on_done)
        self.worker.finished.connect(self.on_worker_finished)

        self.worker.start()

    def render_chat(self, tail: str = ''):
        self.modelAnswer.setHtml(self.chat_html + tail)

        vertBar = self.modelAnswer.verticalScrollBar()

        vertBar.setValue(vertBar.maximum())

    def on_chunk(self, piece: str):
        self.partial += piece

        self.render_chat('<p>' + escape(self.partial).replace('\n', '<br>') + '</p>')

    def on_done(self, text: str):
        self.chat_html += text

        self.render_chat()

    def on_worker_finished(self):
        worker = self.sender()

        if worker is self.worker:
            self.worker = None

        worker.deleteLater()

    def on_error(self, message: str):
        QMessageBox.warning(self, 'Ошибка', 'Произошла ошибка')

        print(message)

        # self.render_chat() todo: unuseful

    def move_passenger(self, old_seat, new_seat):
        if self.current_flight_id == -1:
            return

        passenger = self.db.get_passenger_by_seat(self.current_flight_id, old_seat)

        if passenger is None:
            return

        occupied = self.db.get_passenger_by_seat(self.current_flight_id, new_seat)

        if occupied is not None:
            QMessageBox.warning(self, 'Ошибка', 'Это место уже занято')

            return

        self.db.change_passenger(passenger[0], seat=new_seat)

        self.load_passengers()
        self.refresh_scheme()
        self.select_first_passenger()

    def build_scheme(self):
        for row_i, row in enumerate(cabin):
            for seat_i, seat in enumerate(row):
                grid_col = seat_i if seat_i < 3 else seat_i + 1

                btn = MovableButton(seat)

                btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
                btn.setMinimumSize(30, 30)
                btn.clicked.connect(lambda _, s=seat: self.on_seat_clicked(s))
                btn.rightClicked.connect(lambda s=seat: self.show_food(s))
                btn.dropped.connect(self.move_passenger)

                self.scheme.addWidget(btn, row_i, grid_col)
                self.seat_buttons[seat] = btn

        self.scheme.setColumnMinimumWidth(3, 20)
        self.scheme.setColumnStretch(3, 0)

    def show_food(self, seat):
        if self.current_flight_id == -1:
            return

        person = self.db.get_passenger_by_seat(self.current_flight_id, seat)

        if person is None:
            return

        foods = self.db.get_foods(self.current_flight_id)

        show_menu(self, [f'{food[2]} - {food[3]}' for food in foods], self.db, self.current_flight_id)

    def refresh_scheme(self):
        taken = dict()

        if self.current_flight_id != -1:
            taken = {p[3]: p for p in self.db.get_passengers(self.current_flight_id)}

        for seat, button in self.seat_buttons.items():
            if seat in taken:
                button.setStyleSheet('''background-color: #8E5151;
                                     border-radius: 6px;
                                     padding: 4px;
                                     margin: 2px''')
                button.setToolTip(taken[seat][2])
            else:
                button.setStyleSheet('''background-color: #528759;
                                     border-radius: 6px;
                                     padding: 4px;
                                     margin: 2px''')
                button.setToolTip('Свободно')

    def on_seat_clicked(self, seat):
        if self.current_flight_id == -1:
            return

        person = self.db.get_passenger_by_seat(self.current_flight_id, seat)

        if person is None:
            self.clear_labels()

            self.place.setText(seat)

            self.comment_model.clear()

            return

        self.refresh_passenger(person[0])

        for row in range(self.model.rowCount()):
            item = self.model.item(row)

            if item.data(Qt.ItemDataRole.UserRole) == person[0]:
                self.listView.setCurrentIndex(item.index())

                break

        # todo: when selected -> border on button

    def refresh_passenger(self, passenger_id: int):
        self.current_passenger_in_list = passenger_id

        _, _, name, seat, service_path, photo_path = self.db.get_passenger(passenger_id)

        self.place_in_passengers.setText(seat)
        self.name_in_passengers.blockSignals(True)
        self.name_in_passengers.setText(name)
        self.name_in_passengers.blockSignals(False)
        self.class_in_passengers.blockSignals(True)
        self.class_in_passengers.clear()
        self.class_in_passengers.addItems(['Эконом', 'Бизнес'])
        self.class_in_passengers.setCurrentText(service_path)
        self.class_in_passengers.blockSignals(False)

        self.place.setText(seat)
        self.name.blockSignals(True)
        self.name.setText(name)
        self.name.blockSignals(False)
        self.class_in_scheme.blockSignals(True)
        self.class_in_scheme.clear()
        self.class_in_scheme.addItems(['Эконом', 'Бизнес'])
        self.class_in_scheme.setCurrentText(service_path)
        self.class_in_scheme.blockSignals(False)

        pixmap = QPixmap(photo_path)

        show_pixmap(self.photo_in_passengers, pixmap)
        show_pixmap(self.photo, pixmap)

        self.load_comments()

    def show_passenger_menu(self, pos):
        index = self.listView.indexAt(pos)

        if not index.isValid():
            return

        passenger_id = index.data(Qt.ItemDataRole.UserRole)

        menu = QMenu(self.listView)

        delete_action = menu.addAction('Удалить')

        action = menu.exec(
            self.listView.viewport().mapToGlobal(pos)
        )

        if action == delete_action:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Question)
            box.setWindowTitle('Подтверждение')
            box.setText('Вы уверены?')

            btn_yes = box.addButton('Да', QMessageBox.ButtonRole.YesRole)
            box.setDefaultButton(box.addButton('Нет', QMessageBox.ButtonRole.NoRole))

            box.exec()

            if box.clickedButton() == btn_yes:
                self.db.delete_passenger(passenger_id)

                self.load_passengers()
                self.refresh_scheme()
                self.select_first_passenger()

    def update_passenger_from_passengers(self):
        if self.current_passenger_in_list == -1:
            QMessageBox.warning(self, 'Ошибка', 'Не выбран пользователь')

            return

        self.name_in_passengers.clearFocus()

        name, class_ = self.name_in_passengers.text(), self.class_in_passengers.currentText()

        self.db.change_passenger(self.current_passenger_in_list, full_name=name, service_class=class_)

        self.load_passengers()
        self.select_first_passenger(not_first=True)

    def update_passenger_from_scheme(self):
        if self.current_passenger_in_list == -1:
            QMessageBox.warning(self, 'Ошибка', 'Не выбран пользователь')

            return

        self.name.clearFocus()

        name, class_ = self.name.text(), self.class_in_scheme.currentText()

        self.db.change_passenger(self.current_passenger_in_list, full_name=name, service_class=class_)

        self.load_passengers()
        self.select_first_passenger(not_first=True)

    def new_photo_callback(self):
        if self.current_passenger_in_list == -1:
            QMessageBox.warning(self, 'Ошибка', 'Не выбран пользователь')

            return

        pixmap = ask_image(self, f'Выберете фото для {self.name_in_passengers.text()}')

        if pixmap == False:
            return

        photo_pixmap, photo_path = pixmap

        show_pixmap(self.photo_in_passengers, photo_pixmap)
        show_pixmap(self.photo, photo_pixmap)

        self.db.change_passenger(self.current_passenger_in_list, photo_path=photo_path)

    def add_passenger_item(self, passenger_id: int, name: str):
        item = QStandardItem(name)
        item.setData(passenger_id, Qt.ItemDataRole.UserRole)
        item.setEditable(False)

        self.model.appendRow(item)

        return item

    def add_food_item(self, food_id: int, name: str):
        item = QStandardItem(name)
        item.setData(food_id, Qt.ItemDataRole.UserRole)
        item.setEditable(False)

        self.food_model.appendRow(item)

        return item

    def load_passengers(self):
        self.model.clear()

        if self.current_flight_id == -1:
            return

        for person in self.db.get_passengers(self.current_flight_id):
            self.add_passenger_item(person[0], person[2])

    def load_food(self):
        self.food_model.clear()

        if self.current_flight_id == -1:
            return

        for food in self.db.get_foods(self.current_flight_id):
            self.add_food_item(food[0], food[2])

    def load_comments(self):
        self.comment_model.clear()

        if self.current_flight_id == -1 or self.current_passenger_in_list == -1:
            return

        for comment in self.db.get_comments(self.current_passenger_in_list):
            item = QStandardItem(comment[3])
            item.setData(comment[0], Qt.ItemDataRole.UserRole)
            item.setEditable(False)

            if comment[2] == 'warning':
                item.setBackground(int('8E5151', 16))

            self.comment_model.appendRow(item)

    def on_passenger_selected(self, index):
        self.refresh_passenger(index.data(Qt.ItemDataRole.UserRole))

        self.load_comments()

    def on_food_selected(self, index):
        self.current_food_in_list = index.data(Qt.ItemDataRole.UserRole)

        name, quantity = self.db.get_food(self.current_food_in_list)

        self.food_name.setText(name)
        self.food_count.setValue(quantity)

    def on_comment_clicked(self, index):
        comment_id = index.data(Qt.ItemDataRole.UserRole)

        name, importance = self.db.get_comment(comment_id)

        importance = True if importance == 'warning' else False

        self.on_comment_adding(importance, name, comment_id)

    def clear_labels(self):
        self.current_passenger_in_list = -1

        for label in (self.place_in_passengers, self.name_in_passengers, self.class_in_passengers, self.class_in_scheme,
                      self.place, self.name, self.photo, self.photo_in_passengers):
            label.blockSignals(True)
            label.clear()
            label.blockSignals(False)

    def clear_food_labels(self):
        self.current_food_in_list = -1

        self.food_count.clear()
        self.food_name.clear()

    def show_flight_dialog(self):
        dialog = FlightDialog(self)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            flight_id = self.db.add_flight(*dialog.get_data())

            self.rebuild_choice(flight_id)

    def show_passenger_dialog(self):
        dialog = PassengerDialog(self)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            if self.current_flight_id == -1:
                QMessageBox.warning(self, 'Ошибка', 'Нету рейсов')

                return

            place, name, class_, pixmap, photo_path = dialog.get_data()

            try:
                passenger_id = self.db.add_passenger(self.current_flight_id, name, place, class_, photo_path)
            except IntegrityError:
                QMessageBox.warning(self, 'Ошибка', 'Место занято')

                return

            index = self.model.indexFromItem(self.add_passenger_item(passenger_id, name))

            self.listView.setCurrentIndex(index)

            self.on_passenger_selected(index)

            self.refresh_scheme()

    def show_food_dialog(self):
        dialog = FoodDialog(self)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            if self.current_flight_id == -1:
                QMessageBox.warning(self, 'Ошибка', 'Нету рейсов')

                return

            name, count = dialog.get_data()

            try:
                food_id = self.db.add_food(self.current_flight_id, name, count)
            except IntegrityError:
                QMessageBox.warning(self, 'Ошибка', 'Такое питание уже есть')

                return

            index = self.food_model.indexFromItem(self.add_food_item(food_id, name))

            self.food_list.setCurrentIndex(index)

            self.on_food_selected(index)

    def on_comment_adding(self, important=False, text='', comment_id=None):
        dialog = CommentDialog(self, important, text)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            if self.current_flight_id == -1:
                QMessageBox.warning(self, 'Ошибка', 'Нету рейсов')

                return

            if self.current_passenger_in_list == -1:
                QMessageBox.warning(self, 'Ошибка', 'Не выбран пассажир')

                return

            comment, important = dialog.get_data()

            if not comment:
                QMessageBox.warning(self, 'Ошибка', 'Комментарий не может быть пустым')

                return

            if comment_id is not None:
                self.db.change_comment(comment_id, comment, important)
            else:
                self.db.add_comment(self.current_passenger_in_list, comment, important)

            self.load_comments()

    def on_food_changed(self):
        if self.current_food_in_list == -1:
            return

        self.food_name.clearFocus()
        self.food_count.clearFocus()

        name = self.food_name.text().strip()
        count = int(self.food_count.value())

        if not name:
            QMessageBox.warning(self, 'Ошибка', 'Имя не может быть пустым')

            return

        try:
            self.db.change_food(self.current_food_in_list, name, count)
        except IntegrityError:
            QMessageBox.warning(self, 'Ошибка', 'Такое питание уже есть')

            return

        index = self.food_list.currentIndex()

        if index.isValid():
            self.food_model.setData(index, name)

    def on_flight_changed(self):
        self.current_flight_id = self.flight_choice.currentData() or -1

        self.comment_model.clear()

        self.load_passengers()
        self.refresh_scheme()
        self.select_first_passenger()
        self.load_food()
        self.select_first_food()

    def select_first_passenger(self, not_first=False):
        if not self.model.rowCount():
            self.clear_labels()
        else:
            index = self.model.index(0, 0)

            if not_first:
                matches = self.model.match(
                    index,
                    Qt.ItemDataRole.UserRole,
                    self.current_passenger_in_list,
                    1,
                    Qt.MatchFlag.MatchExactly
                )

                if matches:
                    index = matches[0]
            else:
                self.current_passenger_in_list = index

            self.listView.setCurrentIndex(index)

            self.on_passenger_selected(index)

    def select_first_food(self):
        if not self.food_model.rowCount():
            self.clear_food_labels()
        else:
            index = self.food_model.index(0, 0)

            self.current_food_in_list = index

            self.food_list.setCurrentIndex(index)

            self.on_food_selected(index)

    def rebuild_choice(self, select_id: int | None = None):
        self.flight_choice.blockSignals(True)
        self.flight_choice.clear()

        for f in self.db.get_flights():
            dt = datetime.strptime(f[4], '%Y-%m-%d %H:%M').strftime('%d.%m.%Y %H:%M')

            self.flight_choice.addItem(f'{f[1]}  {f[2]} -> {f[3]}  {dt}', f[0])

        if select_id is not None:
            index = self.flight_choice.findData(select_id)

            if index != -1:
                self.flight_choice.setCurrentIndex(index)

        self.flight_choice.blockSignals(False)

        self.on_flight_changed()

    def closeEvent(self, event):
        if self.worker is not None:
            self.worker.stop()

            self.worker.wait()

        self.db.close()

        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = Window()
    window.show()

    sys.exit(app.exec())
