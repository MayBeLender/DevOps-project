import os
from itertools import count
from pathlib import Path
import unittest
from unittest.mock import patch

import psycopg
from dotenv import dotenv_values
from psycopg import sql
from psycopg.rows import dict_row

from tests.support import main, make_handler


class PostgreSQLTests(unittest.TestCase):
    schema_numbers = count(1)

    @classmethod
    def setUpClass(cls):
        env_file = Path(__file__).resolve().parents[1] / ".env"
        settings = {**dotenv_values(env_file), **os.environ}
        test_fields = ("TEST_DB_NAME", "TEST_DB_USER", "TEST_DB_PASSWORD")
        if not any(settings.get(name) for name in test_fields):
            raise unittest.SkipTest("Skip")
        required = ("DB_HOST", "DB_PORT", *test_fields)
        missing = [name for name in required if not settings.get(name)]
        if missing:
            raise RuntimeError("Missing test database settings: " + ", ".join(missing))
        database_name = settings["TEST_DB_NAME"]
        if not (database_name.startswith("test_") or database_name.endswith(("_test", "_tests"))):
            raise RuntimeError("TEST_DB_NAME must start with test_ or end in _test or _tests")
        if database_name == settings.get("DB_NAME"):
            raise RuntimeError("TEST_DB_NAME must differ from the application DB_NAME")
        cls.database_settings = {
            "host": settings["DB_HOST"],
            "port": settings["DB_PORT"],
            "dbname": database_name,
            "user": settings["TEST_DB_USER"],
            "password": settings["TEST_DB_PASSWORD"],
            "connect_timeout": 3,
        }
        with psycopg.connect(**cls.database_settings):
            pass

    def setUp(self):
        with psycopg.connect(**self.database_settings, autocommit=True) as connection:
            while True:
                self.schema = f"test_{next(self.schema_numbers)}"
                try:
                    connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
                    break
                except psycopg.errors.DuplicateSchema:
                    continue
                except psycopg.errors.UniqueViolation as error:
                    # Concurrent CREATE SCHEMA may report the catalog's unique constraint.
                    if error.diag.constraint_name != "pg_namespace_nspname_index":
                        raise
        self.addCleanup(self.drop_schema)
        with self.connect() as connection:
            connection.execute(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
            for code in ("A", "B"):
                connection.execute(
                    "INSERT INTO workshops (code, name, password) VALUES (%s, %s, %s)",
                    (code, "Workshop " + code, "test-fixture-password"),
                )
        connect = patch.object(main, "db_connect", side_effect=self.connect)
        connect.start()
        self.addCleanup(connect.stop)

    def connect(self):
        return psycopg.connect(
            **self.database_settings, options=f"-c search_path={self.schema}",
            row_factory=dict_row,
        )

    def drop_schema(self):
        with psycopg.connect(**self.database_settings) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def rows(self, query, parameters=()):
        with self.connect() as connection:
            return connection.execute(query, parameters).fetchall()

    def call(self, resource, method, payload=None, workshop_id=1, item_id=None):
        handler = make_handler(payload)
        getattr(handler, resource)(method, workshop_id, item_id)
        handler.json.assert_called_once()
        return handler.json.call_args.args

    def operation(self, name="Cutting", workshop_id=1):
        result, status = self.call("operations", "POST", {
            "name": name, "duration_minutes": 30, "workers_required": 1,
        }, workshop_id=workshop_id)
        self.assertEqual(status, 201)
        return result["data"]

    def material(self, name="Steel", quantity=5, workshop_id=1):
        result, status = self.call("materials", "POST", {
            "name": name, "unit": "kg", "quantity": quantity,
        }, workshop_id=workshop_id)
        self.assertEqual(status, 201)
        return result["data"]

    def order_payload(self):
        return {
            "operation_id": self.operation()["id"], "quantity": 1,
            "start_time": "2026-11-11T10:00:00", "end_time": "2026-11-11T10:30:00",
        }

    def test_operation_creation_persists_defaults_and_workshop(self):
        operation = self.operation()
        self.assertEqual(operation["description"], "")
        self.assertEqual(operation["duration_minutes"], 30)
        self.assertEqual(operation["workers_required"], 1)
        stored = self.rows("SELECT * FROM operations WHERE id = %s", (operation["id"],))[0]
        self.assertEqual(stored, {"workshop_id": 1, **operation})

    def test_operation_list_filters_workshop_and_sorts_newest_first(self):
        first = self.operation("First")
        second = self.operation("Second")
        self.operation("Other workshop", workshop_id=2)
        result, = self.call("operations", "GET")
        self.assertEqual(result["data"], [second, first])

    def test_operation_rejects_nonpositive_duration_and_workers(self):
        for field in ("duration_minutes", "workers_required"):
            for value in (0, -1):
                with self.subTest(field=field, value=value):
                    payload = {"name": "Invalid", "duration_minutes": 30, "workers_required": 1}
                    payload[field] = value
                    with self.assertRaises(psycopg.errors.CheckViolation):
                        self.call("operations", "POST", payload)
                    self.assertEqual(self.rows("SELECT id FROM operations"), [])

    def test_material_creation_defaults_to_zero_and_persists_unit(self):
        result, status = self.call("materials", "POST", {"name": "Empty stock"})
        self.assertEqual(status, 201)
        self.assertEqual(result["data"]["quantity"], 0)
        self.assertEqual(result["data"]["unit"], "шт.")
        material = self.material(quantity=2.5)
        stored = self.rows("SELECT * FROM materials WHERE id = %s", (material["id"],))[0]
        self.assertEqual(stored, {"workshop_id": 1, **material})

    def test_material_quantity_can_be_fractional_or_zero(self):
        material = self.material()
        for quantity in (1.25, 0):
            with self.subTest(quantity=quantity):
                result, = self.call("materials", "PATCH", {"quantity": quantity}, item_id=material["id"])
                self.assertEqual(result["data"], {**material, "quantity": quantity})
                stored = self.rows("SELECT quantity FROM materials WHERE id = %s", (material["id"],))
                self.assertEqual(stored[0]["quantity"], quantity)

    def test_negative_material_quantity_is_not_saved(self):
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.call("materials", "POST", {"name": "Invalid", "quantity": -0.01})
        self.assertEqual(self.rows("SELECT id FROM materials"), [])
        material = self.material(quantity=5)
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.call("materials", "PATCH", {"quantity": -0.01}, item_id=material["id"])
        stored = self.rows("SELECT quantity FROM materials WHERE id = %s", (material["id"],))
        self.assertEqual(stored[0]["quantity"], 5)

    def test_order_creation_persists_operation_link_dates_and_defaults(self):
        payload = self.order_payload()
        result, status = self.call("orders", "POST", payload)
        self.assertEqual(status, 201)
        self.assertEqual(result["data"], {"id": result["data"]["id"], **payload, "notes": ""})
        stored = self.rows("SELECT * FROM work_orders")[0]
        self.assertEqual(stored, {"workshop_id": 1, **result["data"]})
        linked = self.rows(
            "SELECT operations.name FROM work_orders JOIN operations ON operations.id = work_orders.operation_id"
        )
        self.assertEqual(linked, [{"name": "Cutting"}])

    def test_order_nonpositive_quantity_is_rejected_by_database(self):
        payload = self.order_payload()
        for quantity in (0, -1):
            with self.subTest(quantity=quantity):
                with self.assertRaises(psycopg.errors.CheckViolation):
                    self.call("orders", "POST", {**payload, "quantity": quantity})
                self.assertEqual(self.rows("SELECT id FROM work_orders"), [])

    def test_short_order_interval_does_not_insert_a_record(self):
        payload = {**self.order_payload(), "end_time": "2026-11-11T10:29:59"}
        result, status = self.call("orders", "POST", payload)
        self.assertEqual(status, 400)
        self.assertTrue(result["error"])
        self.assertEqual(self.rows("SELECT id FROM work_orders"), [])


if __name__ == "__main__":
    unittest.main()