import ast
from pathlib import Path

from src import bot, transcriber


def test_import_does_not_load_model():
    assert transcriber.obtener_modelo.cache_info().currsize == 0


def test_polling_preserves_pending_updates():
    source = Path(bot.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "run_polling"]
    assert len(calls) == 1
    assert any(k.arg == "drop_pending_updates" and k.value.value is False
               for k in calls[0].keywords)


def test_application_has_lifecycle_and_error_handler():
    app = bot.crear_aplicacion()
    assert app.post_init is bot.post_init
    assert app.post_shutdown is bot.post_shutdown
    assert bot.on_error in app.error_handlers
