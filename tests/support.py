import importlib
import os
from unittest.mock import Mock, patch


with patch("dotenv.load_dotenv"), patch.dict(os.environ, {"HOST": "127.0.0.1", "PORT": "8000"}):
    main = importlib.import_module("main")

def make_handler(payload=None):
    handler = main.BareSiteHandler.__new__(main.BareSiteHandler)
    handler.body = Mock(return_value=payload if payload is not None else {})
    handler.json = Mock()
    return handler