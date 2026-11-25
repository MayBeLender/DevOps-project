import unittest
from unittest.mock import Mock, patch

from tests.support import main, make_handler


class WorkOrderStatusTests(unittest.TestCase):
    # Проверяет отказ для пропущенного, неизвестного статуса и неверного типа без доступа к БД.
    def test_invalid_status_is_rejected_before_database_access(self):
        payloads = [{}, *({"status": value} for value in (None, "", "unknown", 1, True, [], {}))]
        with patch.object(main, "db_connect") as connect:
            for payload in payloads:
                with self.subTest(payload=payload):
                    handler = make_handler(payload)
                    handler.orders("PATCH", 1, 42)
                    handler.json.assert_called_once_with(
                        {"error": "Недопустимый статус наряда"}, 400
                    )
            connect.assert_not_called()

    # Проверяет, что обновление без ID наряда не обращается к БД.
    def test_status_update_requires_order_id(self):
        with patch.object(main, "db_connect") as connect:
            handler = make_handler({"status": "completed"})
            handler.orders("PATCH", 1, None)
            handler.json.assert_called_once_with({"error": "Укажите ID наряда"}, 400)
            connect.assert_not_called()

    # Проверяет запрет изменения статуса без действующей сессии.
    def test_status_update_requires_authentication(self):
        with patch.dict(main.SESSIONS, {}, clear=True), patch.object(main, "db_connect") as connect:
            for headers in ({}, {"Cookie": "session_token=unknown"}):
                with self.subTest(headers=headers):
                    handler = make_handler({"status": "completed"})
                    handler.path = "/api/work-orders/42"
                    handler.client_address = ("127.0.0.1", 12345)
                    handler.headers = headers
                    handler.send_response = Mock()
                    handler.end_headers = Mock()
                    handler.route("PATCH")
                    handler.send_response.assert_called_once_with(401)
                    handler.body.assert_not_called()
                    handler.json.assert_not_called()
            connect.assert_not_called()

    # Проверяет, что маршрут возвращает ошибку валидации вместо успешного обновления.
    def test_status_route_reports_invalid_status(self):
        with (
            patch.dict(main.SESSIONS, {"valid-session": 1}, clear=True),
            patch.object(main, "db_connect") as connect,
        ):
            handler = make_handler({"status": "unknown"})
            handler.path = "/api/work-orders/42"
            handler.client_address = ("127.0.0.1", 12345)
            handler.headers = {"Cookie": "session_token=valid-session"}
            handler.route("PATCH")
            handler.json.assert_called_once_with({"error": "Недопустимый статус наряда"}, 400)
            connect.assert_not_called()
