import os
from dotenv import load_dotenv
from http.server import BaseHTTPRequestHandler, HTTPServer
import logging

load_dotenv() 
logger = logging.getLogger(__name__)

HOST = os.getenv("HOST")
PORT = int(os.getenv("PORT"))

class BareSiteHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            body = "Сайт работает"
            self.send_response(200)
        elif self.path == "/health":
            body = "OK"
            self.send_response(200)
        else:
            body = "Страница не найдена"
            self.send_response(404)

        data = body.encode("utf-8")
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    logging.basicConfig(filename='./logs/main.log', level=logging.INFO)

    server = HTTPServer((HOST, PORT), BareSiteHandler)
    logger.info(f"Server started at http://{HOST}:{PORT}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.warning("Server stopped via KeyboardInterrupt")
    finally:
        logger.info("Server closed")
        server.server_close()