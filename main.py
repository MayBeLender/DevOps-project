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
        logger.info(f"'{self.command} {self.path}' from {self.client_address}")

        routes = {
            "/": "html/login.html",
            "/login": "html/login.html",
            "/success": "html/success.html",
            "/styles.css": "css/styles.css",
            "/404": "html/404.html",
        }

        path = routes.get(self.path, "html/404.html")

        try:
            with open(path, "rb") as file:
                html_file = file.read()
            self.send_response(200)
        except:
            html_file = "File not found".encode("utf-8")
            logger.exception(f"File not found at {path}")
            self.send_response(404)

        content_type = "text/css" if self.path == "/styles.css" else "text/html"
        
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(html_file)))
        self.end_headers()
        self.wfile.write(html_file)

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