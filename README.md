# dear-diary
Dear Diary es un proyecto que busca aunar la escritura de un diario con la posibilidad de recuperar información del pasado y analizar las emociones y momentos del día a día.

> **Trabajo Final de Máster (TFM)**
> **Autora:** Ana Capilla Lozano
> **Titulación:** Máster en Inteligencia Artificial Generativa Avanzada

Este proyecto consiste en un bot de Telegram diseñado para actuar como un diario inteligente. El sistema recibe notas de voz, las transcribe localmente usando **OpenAI Whisper**, procesa el texto mediante **LLMs** para extraer métricas clave (sentimientos, entidades, resúmenes) y almacena la información estructurada en **SQLite** junto con una arquitectura **RAG** para realizar búsquedas semánticas sobre las entradas del diario.

---

## 🛠️ Tecnologías Utilizadas

* **Lenguaje:** Python 3.12+
* **Gestor de entorno y dependencias:** [uv](https://github.com/astral-sh/uv)
* **Bot Interface:** `python-telegram-bot`
* **Transcripción:** `openai-whisper` (Local)
* **LLM & RAG:** Ollama
* **Almacenamiento:** SQLite (Datos estructurados) + Vector DB (Embeddings)

---

## 🚀 Instalación y Configuración

Sigue estos pasos para replicar el entorno de desarrollo local:

### 1. Requisitos Previos
Tener instalado Git y `uv` en el sistema:
```bash
pip install uv  # O mediante el instalador oficial de uv

git clone [https://github.com/TU_USUARIO/TU_REPOSITORIO.git](https://github.com/TU_USUARIO/TU_REPOSITORIO.git)
cd TU_REPOSITORIO

# Sincroniza el entorno virtual exacto con uv
uv sync

cp .env.example .env

TELEGRAM_BOT_TOKEN="tu_token_aqui"
OPENAI_API_KEY="tu_key_aqui"
DATABASE_URL="sqlite:///diary.db"

uv run python src/bot.py

.
├── src/
│   ├── bot.py          # Handlers y lógica de la interfaz de Telegram
│   ├── transcriber.py  # Integración local con OpenAI Whisper
│   ├── extractor.py    # Procesamiento con LLM (sentimientos, resumen)
│   └── database.py      # Persistencia de datos en SQLite y embeddings
├── .env.example        # Plantilla de variables de entorno
├── pyproject.toml      # Declaración de dependencias del proyecto
├── uv.lock             # Registro exacto de versiones
└── README.md           # Documentación principal
