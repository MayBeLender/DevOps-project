import os
import unittest
from itertools import count
from pathlib import Path
from unittest.mock import patch

import psycopg
from dotenv import dotenv_values
from psycopg import sql
from psycopg.rows import dict_row

from tests.support import main, make_handler, migrate_schema


class PostgreSQLTests(unittest.TestCase):
    schema_numbers = count(1)

    @classmethod
    def setUpClass(cls):
        env_file = Path(__file__).resolve().parent / ".env.test"
        settings = {**dotenv_values(env_file), **os.environ}
        test_fields = ("TEST_DB_NAME", "TEST_DB_USER", "TEST_DB_PASSWORD")
        if not any(settings.get(name) for name in test_fields):
            raise unittest.SkipTest("Skip")
        required = ("TEST_DB_HOST", "TEST_DB_PORT", *test_fields)
        missing = [name for name in required if not settings.get(name)]
        if missing:
            raise RuntimeError("Missing test database settings: " + ", ".join(missing))
        database_name = settings["TEST_DB_NAME"]
        if not (database_name.startswith("test_") or database_name.endswith(("_test", "_tests"))):
            raise RuntimeError("TEST_DB_NAME must start with test_ or end in _test or _tests")
        if database_name == settings.get("DB_NAME"):
            raise RuntimeError("TEST_DB_NAME must differ from the application DB_NAME")
        cls.database_settings = {
            "host": settings["TEST_DB_HOST"],
            "port": settings["TEST_DB_PORT"],
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
                    connection.execute(
                        sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema))
                    )
                    break
                except psycopg.errors.DuplicateSchema:
                    continue
                except psycopg.errors.UniqueViolation as error:
                    if error.diag.constraint_name != "pg_namespace_nspname_index":
                        raise
        self.addCleanup(self.drop_schema)
        migrate_schema(self.database_settings, self.schema)
        with self.connect() as connection:
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
            **self.database_settings,
            options=f"-c search_path={self.schema}",
            row_factory=dict_row,
        )

    def drop_schema(self):
        with psycopg.connect(**self.database_settings) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema))
            )

    def rows(self, query, parameters=()):
        with self.connect() as connection:
            return connection.execute(query, parameters).fetchall()

    def call(self, resource, method, payload=None, workshop_id=1, item_id=None):
        handler = make_handler(payload)
        getattr(handler, resource)(method, workshop_id, item_id)
        handler.json.assert_called_once()
        return handler.json.call_args.args

    def operation(self, name="Cutting", workshop_id=1):
        result, status = self.call(
            "operations",
            "POST",
            {
                "name": name,
                "duration_minutes": 30,
                "workers_required": 1,
            },
            workshop_id=workshop_id,
        )
        self.assertEqual(status, 201)
        return result["data"]

    def material(self, name="Steel", quantity=5, workshop_id=1):
        result, status = self.call(
            "materials",
            "POST",
            {
                "name": name,
                "unit": "kg",
                "quantity": quantity,
            },
            workshop_id=workshop_id,
        )
        self.assertEqual(status, 201)
        return result["data"]

    def order_payload(self):
        return {
            "operation_id": self.operation()["id"],
            "quantity": 1,
            "start_time": "2026-11-11T10:00:00",
            "end_time": "2026-11-11T10:30:00",
        }

    # Проверяет сохранение операции, её значений по умолчанию и привязки к цеху.
    def test_operation_creation_persists_defaults_and_workshop(self):
        operation = self.operation()
        self.assertEqual(operation["description"], "")
        self.assertEqual(operation["duration_minutes"], 30)
        self.assertEqual(operation["workers_required"], 1)
        stored = self.rows("SELECT * FROM operations WHERE id = %s", (operation["id"],))[0]
        self.assertEqual(stored, {"workshop_id": 1, **operation})

    # Проверяет выборку операций своего цеха от новых к старым.
    def test_operation_list_filters_workshop_and_sorts_newest_first(self):
        first = self.operation("First")
        second = self.operation("Second")
        self.operation("Other workshop", workshop_id=2)
        (result,) = self.call("operations", "GET")
        self.assertEqual(result["data"], [second, first])

    # Проверяет запрет нулевой и отрицательной длительности и численности работников.
    def test_operation_rejects_nonpositive_duration_and_workers(self):
        for field in ("duration_minutes", "workers_required"):
            for value in (0, -1):
                with self.subTest(field=field, value=value):
                    payload = {"name": "Invalid", "duration_minutes": 30, "workers_required": 1}
                    payload[field] = value
                    with self.assertRaises(psycopg.errors.CheckViolation):
                        self.call("operations", "POST", payload)
                    self.assertEqual(self.rows("SELECT id FROM operations"), [])

    # Проверяет значения материала по умолчанию и сохранение единицы измерения.
    def test_material_creation_defaults_to_zero_and_persists_unit(self):
        result, status = self.call("materials", "POST", {"name": "Empty stock"})
        self.assertEqual(status, 201)
        self.assertEqual(result["data"]["quantity"], 0)
        self.assertEqual(result["data"]["unit"], "шт.")
        material = self.material(quantity=2.5)
        stored = self.rows("SELECT * FROM materials WHERE id = %s", (material["id"],))[0]
        self.assertEqual(stored, {"workshop_id": 1, **material})

    # Проверяет сохранение дробного и нулевого количества материала.
    def test_material_quantity_can_be_fractional_or_zero(self):
        material = self.material()
        for quantity in (1.25, 0):
            with self.subTest(quantity=quantity):
                (result,) = self.call(
                    "materials", "PATCH", {"quantity": quantity}, item_id=material["id"]
                )
                self.assertEqual(result["data"], {**material, "quantity": quantity})
                stored = self.rows(
                    "SELECT quantity FROM materials WHERE id = %s", (material["id"],)
                )
                self.assertEqual(stored[0]["quantity"], quantity)

    # Проверяет запрет отрицательного количества при создании и обновлении материала.
    def test_negative_material_quantity_is_not_saved(self):
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.call("materials", "POST", {"name": "Invalid", "quantity": -0.01})
        self.assertEqual(self.rows("SELECT id FROM materials"), [])
        material = self.material(quantity=5)
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.call("materials", "PATCH", {"quantity": -0.01}, item_id=material["id"])
        stored = self.rows("SELECT quantity FROM materials WHERE id = %s", (material["id"],))
        self.assertEqual(stored[0]["quantity"], 5)

    # Проверяет сохранение наряда, его дат, связи с операцией и значений по умолчанию.
    def test_order_creation_persists_operation_link_dates_and_defaults(self):
        payload = self.order_payload()
        result, status = self.call("orders", "POST", payload)
        self.assertEqual(status, 201)
        self.assertEqual(
            result["data"],
            {
                "id": result["data"]["id"],
                **payload,
                "notes": "",
                "status": "planned",
            },
        )
        stored = self.rows("SELECT * FROM work_orders")[0]
        self.assertEqual(stored, {"workshop_id": 1, **result["data"]})
        linked = self.rows(
            "SELECT operations.name FROM work_orders JOIN operations ON operations.id = work_orders.operation_id"
        )
        self.assertEqual(linked, [{"name": "Cutting"}])

    # Проверяет сохранение каждого статуса и его отображение в списке нарядов.
    def test_order_status_updates_persist_and_appear_in_list(self):
        payload = self.order_payload()
        created, _ = self.call("orders", "POST", payload)
        order = created["data"]
        for status in ("in_progress", "completed", "planned", "planned"):
            with self.subTest(status=status):
                (result,) = self.call("orders", "PATCH", {"status": status}, item_id=order["id"])
                expected = {**order, "status": status}
                self.assertEqual(result["data"], expected)
                self.assertEqual(
                    self.rows("SELECT * FROM work_orders"),
                    [
                        {"workshop_id": 1, **expected},
                    ],
                )
                (listed,) = self.call("orders", "GET")
                self.assertEqual(listed["data"], [{**expected, "operation_name": "Cutting"}])

    # Проверяет, что новый наряд получает начальный статус даже при передаче другого.
    def test_order_creation_always_starts_planned(self):
        result, status = self.call(
            "orders",
            "POST",
            {
                **self.order_payload(),
                "status": "completed",
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(result["data"]["status"], "planned")

    # Проверяет отказ без изменения данных для чужого или несуществующего наряда.
    def test_order_status_cannot_change_another_workshop_or_missing_order(self):
        operation = self.operation("Foreign", workshop_id=2)
        created, _ = self.call(
            "orders",
            "POST",
            {
                "operation_id": operation["id"],
                "quantity": 1,
                "start_time": "2026-11-11T10:00:00",
                "end_time": "2026-11-11T10:30:00",
            },
            workshop_id=2,
        )
        order = created["data"]
        for item_id in (order["id"], order["id"] + 1):
            with self.subTest(item_id=item_id):
                result, status = self.call(
                    "orders", "PATCH", {"status": "completed"}, item_id=item_id
                )
                self.assertEqual(status, 404)
                self.assertEqual(result, {"error": "Наряд не найден"})
                self.assertEqual(
                    self.rows("SELECT * FROM work_orders"),
                    [
                        {"workshop_id": 2, **order},
                    ],
                )

    # Проверяет ограничения БД на неизвестный и пустой статус при прямой записи.
    def test_order_status_database_constraints_reject_invalid_values(self):
        self.call("orders", "POST", self.order_payload())
        for value, error in (
            ("unknown", psycopg.errors.CheckViolation),
            ("", psycopg.errors.CheckViolation),
            (None, psycopg.errors.NotNullViolation),
        ):
            with self.subTest(value=value):
                with self.assertRaises(error), self.connect() as connection:
                    connection.execute("UPDATE work_orders SET status = %s", (value,))
                self.assertEqual(
                    self.rows("SELECT status FROM work_orders"), [{"status": "planned"}]
                )

    # Проверяет изменение статуса через маршрут с авторизацией по сессии цеха.
    def test_order_status_route_uses_session_workshop(self):
        created, _ = self.call("orders", "POST", self.order_payload())
        handler = make_handler({"status": "in_progress"})
        handler.path = f"/api/work-orders/{created['data']['id']}"
        handler.client_address = ("127.0.0.1", 12345)
        handler.headers = {"Cookie": "session_token=status-test-session"}
        with patch.dict(main.SESSIONS, {"status-test-session": 1}, clear=True):
            handler.route("PATCH")
        handler.json.assert_called_once_with({"data": {**created["data"], "status": "in_progress"}})
        self.assertEqual(self.rows("SELECT status FROM work_orders"), [{"status": "in_progress"}])

    # Проверяет заполнение статуса старых нарядов, повторную миграцию и откат без потери нарядов.
    def test_status_migration_backfills_existing_orders_and_downgrades(self):
        migrate_schema(self.database_settings, self.schema, "003", downgrade=True)
        payload = self.order_payload()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO work_orders (workshop_id, operation_id, quantity, start_time, end_time) "
                "VALUES (%s, %s, %s, %s, %s)",
                (1, payload["operation_id"], 1, payload["start_time"], payload["end_time"]),
            )
        original = self.rows("SELECT * FROM work_orders")[0]
        migrate_schema(self.database_settings, self.schema)
        self.assertEqual(
            self.rows("SELECT * FROM work_orders"), [{**original, "status": "planned"}]
        )
        self.assertEqual(
            self.rows("SELECT version_num FROM alembic_version"), [{"version_num": "004"}]
        )
        self.call("orders", "PATCH", {"status": "completed"}, item_id=original["id"])
        migrate_schema(self.database_settings, self.schema)
        self.assertEqual(self.rows("SELECT status FROM work_orders"), [{"status": "completed"}])
        migrate_schema(self.database_settings, self.schema, "003", downgrade=True)
        self.assertEqual(self.rows("SELECT * FROM work_orders"), [original])
        self.assertEqual(
            self.rows("SELECT version_num FROM alembic_version"), [{"version_num": "003"}]
        )
        migrate_schema(self.database_settings, self.schema)
        self.assertEqual(
            self.rows("SELECT * FROM work_orders"), [{**original, "status": "planned"}]
        )

    # Проверяет запрет нулевого и отрицательного количества в наряде на уровне БД.
    def test_order_nonpositive_quantity_is_rejected_by_database(self):
        payload = self.order_payload()
        for quantity in (0, -1):
            with self.subTest(quantity=quantity):
                with self.assertRaises(psycopg.errors.CheckViolation):
                    self.call("orders", "POST", {**payload, "quantity": quantity})
                self.assertEqual(self.rows("SELECT id FROM work_orders"), [])

    # Проверяет, что наряд короче операции отклоняется и не сохраняется в БД.
    def test_short_order_interval_does_not_insert_a_record(self):
        payload = {**self.order_payload(), "end_time": "2026-11-11T10:29:59"}
        result, status = self.call("orders", "POST", payload)
        self.assertEqual(status, 400)
        self.assertTrue(result["error"])
        self.assertEqual(self.rows("SELECT id FROM work_orders"), [])

    # Проверяет создание операции длительностью в одну минуту с одним работником.
    def test_operation_accepts_minimum_duration_and_workers(self):
        result, status = self.call(
            "operations",
            "POST",
            {
                "name": "Minimum",
                "duration_minutes": 1,
                "workers_required": 1,
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(result["data"]["duration_minutes"], 1)
        self.assertEqual(result["data"]["workers_required"], 1)
        self.assertEqual(
            self.rows("SELECT duration_minutes, workers_required FROM operations"),
            [
                {"duration_minutes": 1, "workers_required": 1},
            ],
        )

    # Проверяет, что операция без обязательных полей не сохраняется в БД.
    def test_missing_required_operation_fields_do_not_create_records(self):
        for field in ("name", "duration_minutes", "workers_required"):
            with self.subTest(field=field):
                payload = {"name": "Incomplete", "duration_minutes": 30, "workers_required": 1}
                del payload[field]
                with self.assertRaises(psycopg.errors.NotNullViolation):
                    self.call("operations", "POST", payload)
                self.assertEqual(self.rows("SELECT id FROM operations"), [])

    # Проверяет отказ при повторном имени операции с сохранением исходных данных.
    def test_duplicate_operation_name_does_not_change_existing_data(self):
        original = self.operation("Unique operation")
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.operation("Unique operation")
        self.assertEqual(self.rows("SELECT * FROM operations"), [{"workshop_id": 1, **original}])

    # Проверяет выборку материалов своего цеха по убыванию количества.
    def test_material_list_filters_workshop_and_sorts_by_quantity(self):
        low = self.material("Low", quantity=0)
        high = self.material("High", quantity=2.5)
        self.material("Other workshop", quantity=100, workshop_id=2)
        (result,) = self.call("materials", "GET")
        self.assertEqual(result["data"], [high, low])

    # Проверяет, что обновление не раскрывает и не меняет материал другого цеха.
    def test_material_patch_cannot_read_or_change_another_workshop(self):
        material = self.material("Other workshop", workshop_id=2)
        result, status = self.call("materials", "PATCH", {"quantity": 0}, item_id=material["id"])
        self.assertEqual(status, 404)
        self.assertEqual(result, {"error": "Material not found"})
        self.assertEqual(self.rows("SELECT * FROM materials"), [{"workshop_id": 2, **material}])

    # Проверяет защиту операций, материалов и нарядов другого цеха от удаления.
    def test_deletes_cannot_remove_another_workshops_records(self):
        operation = self.operation("Other operation", workshop_id=2)
        material = self.material("Other material", workshop_id=2)
        payload = {
            "operation_id": operation["id"],
            "quantity": 1,
            "start_time": "2026-11-11T10:00:00",
            "end_time": "2026-11-11T10:30:00",
        }
        order, status = self.call("orders", "POST", payload, workshop_id=2)
        self.assertEqual(status, 201)
        for resource, table, item in (
            ("operations", "operations", operation),
            ("materials", "materials", material),
            ("orders", "work_orders", order["data"]),
        ):
            with self.subTest(resource=resource):
                self.call(resource, "DELETE", item_id=item["id"])
                rows = self.rows(sql.SQL("SELECT * FROM {}").format(sql.Identifier(table)))
                self.assertEqual(rows, [{"workshop_id": 2, **item}])

    # Проверяет отказ в создании наряда без операции, с несуществующей или чужой операцией.
    def test_order_rejects_missing_or_foreign_operation(self):
        foreign_operation = self.operation("Foreign operation", workshop_id=2)
        for operation_id in (None, foreign_operation["id"], foreign_operation["id"] + 1):
            with self.subTest(operation_id=operation_id):
                result, status = self.call(
                    "orders",
                    "POST",
                    {
                        "operation_id": operation_id,
                        "quantity": 1,
                        "start_time": "2026-11-11T10:00:00",
                        "end_time": "2026-11-11T10:30:00",
                    },
                )
                self.assertEqual(status, 404)
                self.assertEqual(result, {"error": "Operation not found"})
                self.assertEqual(self.rows("SELECT id FROM work_orders"), [])

    # Проверяет, что удаление операции удаляет только связанные с ней наряды.
    def test_deleting_operation_cascades_to_its_orders_only(self):
        payload = self.order_payload()
        self.call("orders", "POST", payload)
        other = self.operation("Remaining operation")
        remaining, status = self.call("orders", "POST", {**payload, "operation_id": other["id"]})
        self.assertEqual(status, 201)
        self.call("operations", "DELETE", item_id=payload["operation_id"])
        self.assertEqual(
            self.rows("SELECT * FROM work_orders"),
            [
                {"workshop_id": 1, **remaining["data"]},
            ],
        )
        self.assertEqual(self.rows("SELECT * FROM operations"), [{"workshop_id": 1, **other}])


if __name__ == "__main__":
    unittest.main()
