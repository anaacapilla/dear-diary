# 📖 Dear Diary

### Tu día, contado con tu voz.

Un diario personal en el que envías audios por **Telegram** y consultas tus recuerdos
desde una web con **Streamlit**. El audio se transcribe, se analiza y se guarda junto
con su resumen, emociones, personas, lugares y acontecimientos.

> 🎙️ **Cuenta tu día** → ✍️ **Se transcribe y analiza** → 🌸 **Revísalo en tu diario**

## ✨ Qué puedes hacer

| En Telegram | En la web |
| --- | --- |
| Enviar notas de voz y audios | Explorar las entradas en un calendario |
| Seguir el progreso del procesamiento | Leer transcripciones, resúmenes y análisis |
| Reintentar si ocurre un error | Corregir la transcripción de una entrada |
| Recibir avisos de inicio y apagado del bot | Buscar por texto y fechas, y consultar estadísticas |

## Mis personas

En la barra lateral de Streamlit, **Mis personas** permite mantener un registro
editable con el nombre completo, alias, procedencia, residencia actual, relación,
de qué conoces a cada persona y grupos. Añade filas al final de la tabla y pulsa
**Guardar cambios**; **Descartar cambios** vuelve a cargar los datos guardados.
Separa los alias con `;`. Los grupos se seleccionan entre los existentes y también
puedes crear etiquetas; se unifican las diferencias de mayúsculas y espacios.
Solo el nombre completo es obligatorio y debe ser único en el registro.
Para retirar una ficha sin perder sus vínculos, marca **Archivada**.

Los nombres y alias únicos se vinculan al procesar entradas nuevas, conservando
las transcripciones y las menciones originales. Las coincidencias usan palabras
completas, sin distinguir mayúsculas y espacios; no se adivinan variantes ni erratas.
Los alias compartidos permanecen sin identificar. Archivar una persona detiene
sus nuevas asociaciones y reserva sus alias para evitar atribuciones incorrectas.

**Por organizar** recoge menciones existentes y nuevas que no tienen identidad.
Puedes crear una ficha o añadir una mención como alias a una persona existente.
Después, en **Propuestas del histórico**, pulsa **Buscar propuestas**, revisa las
transcripciones, marca las asociaciones deseadas y pulsa **Aceptar seleccionadas**.
Guardar un alias no aplica por sí solo esas propuestas ni regenera los análisis.
Desde cada entrada, tanto en el calendario como en los resultados del buscador,
**Editar personas** permite asignar o corregir la persona de cada mención y dejarla
sin identificar. También puedes añadir una mención que aparezca en la transcripción
y no se haya detectado. Estas correcciones afectan solo a esa entrada, se conservan
al reanalizarla mientras siga apareciendo la mención y no crean alias globales.
La selección se aplica a todas las apariciones de la misma expresión en esa entrada.

Al actualizar desde una versión anterior, Streamlit o el bot migran SQLite al
arrancar. Se crea automáticamente una copia en la carpeta `backups` de la base de
datos. La migración incorpora las menciones anteriores a **Por organizar**.

## 🌱 Primeros pasos

Necesitas **Python 3.12 o superior**, **uv** y **Ollama**.
Ejecuta los comandos desde la carpeta del proyecto.

### 1. Instala las dependencias

```powershell
uv sync --locked --group web
```

### 2. Configura tu diario

Si todavía no tienes un archivo `.env`, créalo a partir del ejemplo:

```powershell
Copy-Item .env.example .env
```

Edita estos valores en `.env`:

| Variable | Qué debes indicar |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | El token de tu bot de Telegram |
| `TELEGRAM_USER_ID` | Tu identificador numérico de usuario |
| `OLLAMA_MODEL` | El modelo de análisis; por defecto, `qwen3:1.7b` |

**Conserva tu `.env` si ya existe:** no lo sobrescribas con el ejemplo ni lo subas a Git.
El resto de opciones tiene valores predeterminados para empezar.

### 3. Prepara el modelo de análisis

Con Ollama instalado y en ejecución:

```powershell
ollama pull qwen3:1.7b
```

Si cambias el modelo en `.env`, descarga ese mismo modelo en Ollama.
Whisper descarga sus pesos cuando los necesita por primera vez; el primer audio
puede tardar más.

## 🚀 Arrancar el diario

Abre **dos terminales** en la carpeta del proyecto.

### Terminal 1 · Bot de Telegram

```powershell
uv run python -m src.bot
```

Arranca primero el bot en el primer uso para que prepare la base de datos.
Mantén una sola instancia del bot por token y base de datos.

### Terminal 2 · Web de Streamlit

```powershell
uv run --group web streamlit run web_app.py
```

Mantén las terminales abiertas mientras uses cada servicio. Para detener uno,
pulsa **Ctrl+C** en su terminal. El bot deja terminar la entrada activa antes de cerrar.

| Quiero… | Qué debe estar funcionando |
| --- | --- |
| Consultar las entradas ya guardadas | Streamlit |
| Recibir y procesar nuevos audios | Bot y Ollama |
| Corregir el texto y regenerar su análisis | Streamlit, bot y Ollama |

La web funciona en el mismo ordenador y todavía no dispone de inicio de sesión.

## 🎙️ Usar Telegram

1. Abre el chat privado con tu bot y envía una **nota de voz o un audio**.
2. Recibirás una confirmación. Si hay más entradas esperando, verás cuántas tienes delante.
3. Un único mensaje se actualizará durante la descarga, transcripción y análisis.
4. Al terminar, aparecerá **Entrada guardada** con un resumen breve.

Puedes enviar varios audios seguidos: se procesan por orden. Por defecto se admiten
hasta **20 MB y 30 minutos** por audio. Los archivos enviados como documentos genéricos
no se admiten.

Si falla el procesamiento, pulsa **Reintentar**. La transcripción ya guardada se
reutiliza; un fallo del análisis no la borra. Si no se detecta voz, envía otro audio.

El bot atiende únicamente al usuario configurado en su chat privado. Al iniciarse y
al detenerse de forma controlada envía un aviso; un cierre forzado o la falta de
conexión pueden impedirlo. `/start` orienta sobre cómo enviar un audio; la consulta
y la gestión de las entradas se realizan desde la web.

## 🌸 Explorar la web

### Entradas

Navega por el calendario con las **flechas** o los selectores de **mes y año**.
Los días con entradas están marcados: pulsa uno para ver lo almacenado.
Las fechas corresponden al envío del audio, no necesariamente al día de lo que cuentas.

En cada entrada puedes consultar la transcripción, el resumen, las emociones,
las personas, los lugares y los acontecimientos detectados.

**Editar entrada** permite corregir la transcripción completa y guardar o cancelar.
El original se conserva y el análisis anterior se invalida. El bot recoge los cambios
cada cinco segundos para regenerarlo; si está apagado, retomará las correcciones de
entradas Telegram al arrancar. Las entradas antiguas sin identidad de Telegram
requieren gestión local. No se pueden editar entradas pendientes o en procesamiento.

### Buscador

Busca palabras o frases en transcripciones, resúmenes y acontecimientos, dentro de
un periodo. La búsqueda no distingue mayúsculas de minúsculas.

**Las preguntas en lenguaje natural todavía están pendientes de implementación.**

### Estadísticas

Consulta las entradas por día, los días con actividad, el estado del procesamiento
y los sentimientos de las entradas analizadas. Son datos sobre lo registrado en el
diario; no equivalen a un recuento de acontecimientos únicos.

Pulsa **Actualizar datos** para releer los cambios recientes.

## 🗂️ Datos, privacidad y copias

- **Almacenamiento:** SQLite guarda las transcripciones, análisis y estados del proceso.
- **Procesamiento:** faster-whisper transcribe y Ollama analiza localmente por defecto.
  Los audios y las respuestas pasan por Telegram.
- **Audios:** se conservan siete días por defecto; las transcripciones y los análisis permanecen.
- **Copias:** el bot crea una copia local diaria de SQLite. Las copias caducan a los siete días
  por defecto y no incluyen los audios.
- **Mantenimiento:** la limpieza se ejecuta al arrancar el bot y cada hora mientras está activo.
- **Actualizaciones:** las migraciones de la base crean una copia previa y conservan las entradas.

La base, los audios y las copias contienen información personal y no tienen cifrado
propio. Guárdalos en una cuenta y disco con acceso controlado. Las copias locales
no protegen frente a la pérdida del equipo: conserva también una copia fuera de él.

Para restaurar, detén el bot y la web, conserva una copia de la base actual y sustituye
el archivo configurado en `DATABASE_PATH` por una copia de seguridad. Después vuelve
a arrancar los servicios. Los audios que hayan caducado no se recuperan.

<details>
<summary>⚙️ Opciones de configuración</summary>

Todas las opciones están en [`.env.example`](.env.example). Las rutas relativas se
resuelven desde la carpeta del proyecto.

| Opción | Valor predeterminado |
| --- | --- |
| Zona horaria | `Europe/Madrid` |
| Modelo de transcripción | `base` |
| Dispositivo de Whisper | `auto` |
| Conservación de audios y copias | 7 días |
| Límite por audio | 20 MB / 30 minutos |

Si CUDA no está disponible o falla por incompatibilidad de bibliotecas, utiliza
`WHISPER_DEVICE=cpu` y `WHISPER_COMPUTE_TYPE=int8`. PyTorch no es necesario para el bot.

Para instalar las dependencias opcionales de los notebooks:

```powershell
uv sync --group notebooks --group web
```

</details>

## 🧪 Pruebas y TFM

Ejecuta las pruebas, incluida la interfaz web:

```powershell
uv run --group web python -m pytest -q
```

Las pruebas utilizan bases temporales, clientes simulados y credenciales ficticias:
no envían mensajes reales ni consumen modelos.

El directorio [`evaluation`](evaluation/README.md) contiene el corpus sintético,
los experimentos con modelos reales y las métricas reproducibles del TFM.
El corpus inicial sirve como comprobación, no como validación científica final.
