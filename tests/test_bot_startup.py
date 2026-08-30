import ast
from pathlib import Path

BOT_FILE = Path(__file__).resolve().parents[1] / "src" / "bot.py"


def _has_asyncio_run_call(source: str) -> bool:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "run":
            if isinstance(func.value, ast.Name) and func.value.id == "asyncio":
                return True
    return False


def test_bot_uses_post_init_instead_of_asyncio_run():
    source = BOT_FILE.read_text(encoding="utf-8")

    assert not _has_asyncio_run_call(source), (
        "No debe llamarse a asyncio.run() antes de app.run_polling() porque "
        "cierra el bucle de eventos y rompe la inicialización del bot."
    )
    assert "app.post_init" in source, (
        "La bienvenida inicial debe registrarse con post_init para ejecutar "
        "en el mismo loop que usa Telegram."
    )
