import unittest
from http import HTTPStatus
from unittest.mock import MagicMock, patch

from tests.support import main, make_handler


class WorkOrderTimeTests(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "operation_id": 7,
            "quantity": 1,
            "start_time": "2026-11-11T10:00:00",
            "end_time": "2026-11-11T10:30:00",
        }
        self.connection = MagicMock()
        self.connection.execute.return_value.fetchone.side_effect = [
            {"duration_minutes": 30}, {"id": 42}, {"id": 42, **self.payload},
        ]
        connect = patch.object(main, "db_connect")
        self.connect = connect.start()
        self.addCleanup(connect.stop)
        self.connect.return_value.__enter__.return_value = self.connection

    def create_order(self, **changes):
        handler = make_handler({**self.payload, **changes})
        handler.orders("POST", 1, None)
        return handler

    def assert_rejected(self, handler):
        handler.json.assert_called_once()
        value, status = handler.json.call_args.args
        self.assertEqual(status, HTTPStatus.BAD_REQUEST)
        self.assertTrue(value["error"])
        self.assertLessEqual(self.connection.execute.call_count, 1)

    def test_end_before_start_is_rejected(self):
        self.assert_rejected(self.create_order(end_time="2026-11-11T09:59:59"))

    def test_end_equal_to_start_is_rejected(self):
        self.assert_rejected(self.create_order(end_time=self.payload["start_time"]))

    def test_interval_one_second_shorter_than_operation_is_rejected(self):
        self.assert_rejected(self.create_order(end_time="2026-11-11T10:29:59"))

    def test_interval_equal_to_operation_is_accepted(self):
        handler = self.create_order()
        handler.json.assert_called_once_with(
            {"data": {"id": 42, **self.payload}}, HTTPStatus.CREATED
        )
        self.assertEqual(self.connection.execute.call_count, 3)
        insert_parameters = self.connection.execute.call_args_list[1].args[1]
        self.assertEqual(insert_parameters, (
            1, 7, 1, self.payload["start_time"], self.payload["end_time"], "",
        ))

    def test_interval_one_second_longer_than_operation_is_accepted(self):
        handler = self.create_order(end_time="2026-11-11T10:30:01")
        self.assertEqual(handler.json.call_args.args[1], HTTPStatus.CREATED)
        self.assertEqual(self.connection.execute.call_count, 3)

    def test_interval_across_midnight_is_accepted(self):
        handler = self.create_order(
            start_time="2026-11-11T23:45:00", end_time="2026-11-12T00:15:00"
        )
        self.assertEqual(handler.json.call_args.args[1], HTTPStatus.CREATED)

    def test_invalid_dates_and_types_are_rejected_without_database_access(self):
        for field in ("start_time", "end_time"):
            for invalid in ("not-a-date", "2026-02-30T10:00:00", "", None, 123):
                with self.subTest(field=field, value=invalid):
                    handler = self.create_order(**{field: invalid})
                    self.assert_rejected(handler)
                    self.connect.assert_not_called()

    def test_missing_dates_are_rejected_without_database_access(self):
        for field in ("start_time", "end_time"):
            with self.subTest(field=field):
                payload = dict(self.payload)
                del payload[field]
                handler = make_handler(payload)
                handler.orders("POST", 1, None)
                self.assert_rejected(handler)
                self.connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
