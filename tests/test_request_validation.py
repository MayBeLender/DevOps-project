import io
import unittest
from unittest.mock import Mock, patch

from tests.support import main, make_handler


class RequestValidationTests(unittest.TestCase):
    def handler(self, headers):
        handler = make_handler()
        handler.path = "/api/materials"
        handler.client_address = ("127.0.0.1", 12345)
        handler.headers = headers
        handler.send_response = Mock()
        handler.end_headers = Mock()
        return handler

    def test_missing_or_invalid_session_denies_api_access(self):
        with patch.dict(main.SESSIONS, {}, clear=True), patch.object(main, "db_connect") as connect:
            for headers in ({}, {"Cookie": "other=value"}, {"Cookie": "session_token=unknown"}):
                with self.subTest(headers=headers):
                    handler = self.handler(headers)
                    handler.resource = Mock()
                    handler.route("GET")
                    handler.send_response.assert_called_once_with(401)
                    handler.end_headers.assert_called_once_with()
                    handler.resource.assert_not_called()
            connect.assert_not_called()

    def test_valid_session_uses_its_workshop(self):
        with patch.dict(main.SESSIONS, {"valid-session": 7}, clear=True):
            handler = self.handler({"Cookie": "session_token=valid-session"})
            handler.resource = Mock()
            handler.route("GET")
            handler.resource.assert_called_once_with("GET", 7)
            handler.send_response.assert_not_called()

    def test_invalid_json_and_nonobject_bodies_return_bad_request(self):
        with patch.dict(main.SESSIONS, {"valid-session": 7}, clear=True):
            with patch.object(main, "db_connect") as connect:
                for body in (b"{", b"\xff", b"[]", b"null", b'"text"', b"123"):
                    with self.subTest(body=body):
                        handler = self.handler({
                            "Cookie": "session_token=valid-session",
                            "Content-Length": str(len(body)),
                        })
                        handler.rfile = io.BytesIO(body)
                        handler.body = main.BareSiteHandler.body.__get__(handler)
                        handler.route("POST")
                        handler.json.assert_called_once_with({"error": "Invalid request data"}, 400)
                connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()