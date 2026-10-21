import os
import json
import logging
import secrets
import sqlite3
from datetime import datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
from dotenv import load_dotenv

load_dotenv() 
logger = logging.getLogger(__name__)

HOST = os.getenv("HOST")
PORT = int(os.getenv("PORT"))
DB_PATH = os.getenv("DB_PATH")
SESSIONS = {}

def db_connect():
    try:
        connection = sqlite3.connect(DB_PATH)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        logger.info("Connected to database")
    except Exception as e: logger.exception(f"Database connection error: {e}")
    return connection

class BareSiteHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PATCH(self):
        self.route("PATCH")

    def do_DELETE(self):
        self.route("DELETE")

    def route(self, method):
        logger.info(f"'{method} {self.path}' from {self.client_address}")

        try:
            if self.path in {"/", "/login"} and method == "GET":
                return self.file("html/login.html")
            if self.path == "/health" and method == "GET":
                return self.file("html/health.html")
            if self.path == "/dashboard" and method == "GET":
                return self.file("html/dashboard.html")
            if self.path == "/styles.css" and method == "GET":
                return self.file("css/styles.css", "text/css")
            if self.path == "/api/auth/login" and method == "POST":
                return self.login()
            if self.path == "/api/auth/logout" and method == "POST":
                return self.logout()
            if self.path.startswith("/api/"):
                workshop_id = self.auth()
                if workshop_id is None:
                    return
                return self.resource(method, workshop_id)
            return self.file("html/404.html")
        except Exception as e:
            logger.exception(f"Exception in routing: {e}")

    def body(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def json(self, value, status=HTTPStatus.OK, headers=None):
        data = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if headers:
            for key, header in headers.items():
                self.send_header(key, header)
        self.end_headers()
        self.wfile.write(data)
        logger.info(f"Send data: {data} with status: {status} to {self.client_address}")

    def file(self, filename, content_type="text/html"):
        try:
            with open(filename, "rb") as file:
                data = file.read()
        except Exception as e:
            logger.exception(f"Exception while loading file: {e}")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def token(self):
        cookie_header = self.headers.get("Cookie")
        if cookie_header:
            cookies = SimpleCookie()
            cookies.load(cookie_header)
        return cookies.get("session_token").value

    def auth(self):
        token = self.token()
        workshop_id = SESSIONS.get(token)
        if workshop_id is None:
            logger.warning(f"Invalid token from {self.client_address}")
            self.send_response(401)
            self.end_headers()
        return workshop_id

    def login(self):
        data = self.body()
        code = data.get("workshop_code")
        password = data.get("password")
        
        with db_connect() as connection:
            workshop = connection.execute("SELECT id FROM workshops WHERE code = ? AND password = ?",
                (code, password),).fetchone()
        
        if workshop is None: return

        token = secrets.token_urlsafe(24)
        SESSIONS[token] = workshop["id"]

        logger.info(f"{self.client_address} logged in")

        self.send_response(200)
        self.send_header("Set-Cookie", f"session_token={token}; Path=/; HttpOnly; SameSite=Lax")
        self.end_headers()

    def logout(self):
        SESSIONS.pop(self.token())

        logger.info(f"{self.client_address} logged out")

        self.send_response(200)
        self.send_header("Set-Cookie", "session_token=; Path=/; HttpOnly; SameSite=Lax")
        self.end_headers()

    def resource(self, method, workshop_id):
        parts = [item for item in self.path.split("/") if item]

        handlers = {
            "workshops": self.workshops,
            "operations": self.operations,
            "materials": self.materials,
            "work-orders": self.orders,
        }
        handler = handlers.get(parts[1])

        item_id = None
        if len(parts) == 3:
            item_id = int(parts[2])

        handler(method, workshop_id, item_id)

    def workshops(self, method, workshop_id, item_id):
        if method == "GET":
            with db_connect() as connection:
                row = connection.execute("SELECT name FROM workshops WHERE id = ?",
                    (workshop_id,)).fetchone()
            self.json({"data": dict(row)})

    def operations(self, method, workshop_id, item_id):
        if method == "GET":
            with db_connect() as connection:
                rows = connection.execute("SELECT id, name, description, duration_minutes, workers_required FROM operations WHERE workshop_id = ? ORDER BY id DESC",
                    (workshop_id,)).fetchall()
            self.json({"data": [dict(row) for row in rows]})
        
        if method == "POST":
            data = self.body()

            with db_connect() as connection:
                cursor = connection.execute("INSERT INTO operations(workshop_id, name, description, duration_minutes, workers_required) VALUES (?, ?, ?, ?, ?)",
                    (workshop_id, data.get("name"), data.get("description", ""), data.get("duration_minutes"), data.get("workers_required")),)
                logger.info(f"Added operation: {data.get("name")}")
                row = connection.execute("SELECT id, name, description, duration_minutes, workers_required FROM operations WHERE id = ?",
                    (cursor.lastrowid,),).fetchone()
            self.json({"data": dict(row)}, HTTPStatus.CREATED)
        
        if method == "DELETE":
            with db_connect() as connection:
                cursor = connection.execute("DELETE FROM operations WHERE id = ? AND workshop_id = ?", (item_id, workshop_id))
                logger.info(f"Deleted operation with id: {item_id}")
            self.json({"deleted": item_id})

    def materials(self, method, workshop_id, item_id):
        if method == "GET":
            with db_connect() as connection:
                rows = connection.execute("SELECT id, name, unit, quantity FROM materials WHERE workshop_id = ? ORDER BY quantity DESC",
                    (workshop_id,)).fetchall()
            self.json({"data": [dict(row) for row in rows]})

        if method == "POST":
            data = self.body()

            with db_connect() as connection:
                cursor = connection.execute("INSERT INTO materials(workshop_id, name, unit, quantity) VALUES (?, ?, ?, ?)",
                    (workshop_id, data.get("name"), data.get("unit", "шт."), data.get("quantity", 0)),)
                logger.info(f"Added material: {data.get("name")} with quantity of: {data.get("quantity", 0)}")
                row = connection.execute("SELECT id, name, unit, quantity FROM materials WHERE id = ?",
                    (cursor.lastrowid,)).fetchone()
            self.json({"data": dict(row)}, HTTPStatus.CREATED)

        if method == "PATCH":
            data = self.body()

            with db_connect() as connection:
                connection.execute(f"UPDATE materials SET quantity = ? WHERE id = ? AND workshop_id = ?",
                    (data["quantity"], item_id, workshop_id,))
                logger.info(f"Changed {data.get("name")}`s quantity to: {data["quantity"]}")
                row = connection.execute("SELECT id, name, unit, quantity FROM materials WHERE id = ?",
                    (item_id,)).fetchone()
            self.json({"data": dict(row)})

        if method == "DELETE":
            with db_connect() as connection:
                connection.execute("DELETE FROM materials WHERE id = ? AND workshop_id = ?", (item_id, workshop_id))
                logger.info(f"Deleted material with id: {item_id}")
            self.json({"deleted": item_id})

    def orders(self, method, workshop_id, item_id):
        if method == "GET":
            query = """
                SELECT work_orders.id, work_orders.operation_id, operations.name AS operation_name, work_orders.quantity, work_orders.start_time,
                work_orders.end_time, work_orders.notes FROM work_orders INNER JOIN operations ON operations.id = work_orders.operation_id
                WHERE work_orders.workshop_id = ? ORDER BY work_orders.id
            """

            with db_connect() as connection:
                rows = connection.execute(query, (workshop_id,)).fetchall()
            self.json({"data": [dict(row) for row in rows]})

        if method == "POST":
            data = self.body()
            operation_id = data.get("operation_id")
            try:
                start_time = datetime.fromisoformat(data["start_time"])
                end_time = datetime.fromisoformat(data["end_time"])
            except:
                return self.json({"error": "Укажите корректные даты начала и окончания"}, HTTPStatus.BAD_REQUEST)

            with db_connect() as connection:
                operation = connection.execute(
                    "SELECT duration_minutes FROM operations WHERE id = ? AND workshop_id = ?",
                    (operation_id, workshop_id),).fetchone()
                if end_time <= start_time:
                    return self.json({"error": "Время окончания должно быть позже времени начала"}, HTTPStatus.BAD_REQUEST)
                interval_minutes = (end_time - start_time).total_seconds() / 60
                if interval_minutes < operation["duration_minutes"]:
                    return self.json({"error": (f"Временной интервал должен быть не меньше длительности операции ({operation['duration_minutes']} мин.)")}, HTTPStatus.BAD_REQUEST)
                cursor = connection.execute(
                    "INSERT INTO work_orders(workshop_id, operation_id, quantity, start_time, end_time, notes) VALUES (?, ?, ?, ?, ?, ?)",
                    (workshop_id, operation_id, data.get("quantity"), data.get("start_time"), data.get("end_time"), data.get("notes", "")),)
                logger.info(f"Added order: {data.get("name")}")
                row = connection.execute("SELECT id, operation_id, quantity, start_time, end_time, notes FROM work_orders WHERE id = ?",
                    (cursor.lastrowid,),).fetchone()
            self.json({"data": dict(row)}, HTTPStatus.CREATED)

        if method == "DELETE":
            with db_connect() as connection:
                connection.execute("DELETE FROM work_orders WHERE id = ? AND workshop_id = ?", (item_id, workshop_id))
                logger.info(f"Deleted order with id: {item_id}")
            self.json({"deleted": item_id}) 

if __name__ == "__main__":
    logging.basicConfig(filename='./logs/main.log', level=logging.INFO,
        format="[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s",
        datefmt="%d/%b/%Y %H:%M:%S")

    server = HTTPServer((HOST, PORT), BareSiteHandler)
    print(f"Server started at http://{HOST}:{PORT}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.warning("Server stopped via KeyboardInterrupt")
    finally:
        logger.info("Server closed")
        server.server_close()
