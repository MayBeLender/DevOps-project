import unittest
from unittest.mock import MagicMock, patch

from tests.support import main, make_handler


class DeleteTests(unittest.TestCase):
    def setUp(self):
        self.handler = make_handler()
        self.connection = MagicMock()
        connect = patch.object(main, "db_connect")
        self.connect = connect.start()
        self.addCleanup(connect.stop)
        self.connect.return_value.__enter__.return_value = self.connection

    def assert_delete_succeeds(self, resource, expected_sql):
        getattr(self.handler, resource)("DELETE", 3, 17)

        self.connect.assert_called_once_with()
        self.connection.execute.assert_called_once_with(expected_sql, (17, 3))
        self.handler.json.assert_called_once_with({"deleted": 17})
        self.handler.body.assert_not_called()
        self.connect.return_value.__exit__.assert_called_once_with(None, None, None)

    def assert_database_failure_is_propagated(self, resource):
        error = RuntimeError("test database failure")
        self.connection.execute.side_effect = error

        with self.assertRaises(RuntimeError) as raised:
            getattr(self.handler, resource)("DELETE", 3, 17)

        self.assertIs(raised.exception, error)
        self.handler.json.assert_not_called()
        self.handler.body.assert_not_called()
        exit_args = self.connect.return_value.__exit__.call_args.args
        self.assertIs(exit_args[0], RuntimeError)
        self.assertIs(exit_args[1], error)

    def test_delete_operation_filters_id_and_workshop_and_returns_deleted_id(self):
        self.assert_delete_succeeds(
            "operations", "DELETE FROM operations WHERE id = %s AND workshop_id = %s"
        )

    def test_delete_material_filters_id_and_workshop_and_returns_deleted_id(self):
        self.assert_delete_succeeds(
            "materials", "DELETE FROM materials WHERE id = %s AND workshop_id = %s"
        )

    def test_delete_operation_does_not_report_success_on_database_error(self):
        self.assert_database_failure_is_propagated("operations")

    def test_delete_material_does_not_report_success_on_database_error(self):
        self.assert_database_failure_is_propagated("materials")


if __name__ == "__main__":
    unittest.main()
