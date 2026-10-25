# 📖 Dear Diary

### Tu día, contado con tu voz.

Un diario personal para enviar audios por **Telegram** y consultar tus recuerdos
en una web con **Streamlit**. Whisper transcribe el audio, Ollama prepara su análisis
y SQLite guarda las entradas. El procesamiento de IA es local por defecto.

> 🎙️ Cuenta tu día → ✍️ Se transcribe y analiza → 🌸 Revisa tus recuerdos

## ✨ Qué puedes hacer

| Apartado | Funciones actuales |
| --- | --- |
| **Telegram** | Enviar audios, elegir el día del diario, seguir el progreso y reintentar errores. |
| **Entradas** | Consultar el calendario, añadir texto en un día concreto, editar y borrar entradas. |
| **Mis personas** | Crear fichas y alias, revisar menciones y corregir las personas asociadas. |
| **Buscador** | Buscar con lenguaje natural, texto literal o filtros; consultar entradas y días coincidentes. |
| **Estadísticas** | Ver actividad, rachas, emociones, duración de audios y tu año en píxeles. |

## 🌱 Instalar y configurar

Necesitas **Python 3.12 o superior**, **uv** y **Ollama**.
Ejecuta los comandos desde la carpeta del proyecto.

```powershell
uv sync --locked --group web
```

Si todavía no tienes `.env`, créalo a partir del ejemplo:

```powershell
Copy-Item .env.example .env
```

**Conserva tu `.env` si ya existe.** Configura estos valores sin subir credenciales a Git:

| Variable | Contenido |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Token de tu bot. |
| `TELEGRAM_USER_ID` | Tu identificador numérico de Telegram. |
| `OLLAMA_MODEL` | Modelo de análisis; por defecto, `qwen3:1.7b`. |

Prepara el modelo con Ollama en ejecución:

```powershell
ollama pull qwen3:1.7b
```

Si eliges otro modelo, descarga ese mismo. Whisper descarga sus pesos cuando los
necesita por primera vez, por lo que el primer audio puede tardar más.

El resto de opciones está en [`.env.example`](.env.example): zona horaria
`Europe/Madrid`, Whisper `base`, límites de 20 MB y 30 minutos por audio y
conservación de audios y copias durante siete días por defecto. `SEARCH_MODEL`
permite elegir otro modelo para interpretar búsquedas y `SEARCH_TIMEOUT_SECONDS`
limita su espera a 30 segundos por defecto.

Si Whisper falla al usar CUDA, configura `WHISPER_DEVICE=cpu` y
`WHISPER_COMPUTE_TYPE=int8`.

## 🚀 Arrancar el diario

### Transcribir con Groq (opcional)

1. Crea una cuenta en [Groq Console](https://console.groq.com/) y mantén el plan
   **Free** si quieres evitar facturación.
2. En [API Keys](https://console.groq.com/keys), crea una clave. Guárdala solamente
   en tu `.env`, nunca en Git, capturas ni mensajes.
3. Antes de enviar audios personales, activa **Zero Data Retention** para audio
   en [Data Controls](https://console.groq.com/settings/data-controls).
   La aplicación no puede activar ni verificar esta opción por ti.
4. Añade o actualiza estas líneas en tu `.env` existente:

   ```dotenv
   TRANSCRIPTION_PROVIDER=groq
   GROQ_API_KEY=tu_clave
   GROQ_MODEL=whisper-large-v3
   ```

5. Ejecuta `uv sync --locked --group web` y reinicia el bot y Streamlit.
   Envía primero una nota de voz poco sensible y comprueba el texto en la web.
   El modelo guardado en la entrada será `groq/whisper-large-v3`.

Con Groq, el audio sale de tu ordenador; el análisis con Ollama sigue siendo local.
ZDR depende de los controles y condiciones de Groq y no afecta a Telegram ni a
los audios originales y copias locales. Consulta [su política](https://console.groq.com/docs/your-data).

El motor convierte el audio incrementalmente a WAV mono de 16 kHz y lo divide en
fragmentos consecutivos de hasta diez minutos (19,2 MB), sin truncar ni descartar
muestras. Los textos se unen en orden. Si Groq devuelve HTTP 413, divide ese
fragmento en mitades y vuelve a intentarlo. Los cortes pueden afectar a palabras
en los límites; revisa las transcripciones importantes. Los WAV temporales se
eliminan al terminar o fallar; el original sigue la retención habitual.

Los fallos de red, HTTP 429 y errores de servidor tienen hasta tres reintentos.
Si la espera indicada supera un minuto o se agotan los intentos, la entrada queda
fallida y permite **Reintentar** después. Una transcripción completa ya guardada
se reutiliza si falla el análisis. Si falla a mitad de la transcripción, el
reintento vuelve a procesar el audio completo y consume cuota otra vez.
No se cambia automáticamente de proveedor ni se usa el modelo Turbo.

Comprueba tus cuotas en [Limits](https://console.groq.com/settings/limits).
Dividir archivos no evita las cuotas de duración. El diario conserva sus límites
de recepción de 20 MB y 30 minutos por audio; la división opera después de recibirlo.
La subida desde Telegram sigue sujeta a los límites del Bot API.

Para volver al procesamiento local, usa `TRANSCRIPTION_PROVIDER=local` y reinicia.
Los módulos locales no cargan pesos cuando se selecciona Groq. Las evaluaciones
de `evaluation.run` también usan el proveedor configurado: con Groq envían el
corpus seleccionado a su API y consumen cuota.

Abre dos terminales en la carpeta del proyecto. En el primer uso, arranca el bot
antes que la web para crear la base de datos.

**Terminal 1 · Bot de Telegram**

```powershell
uv run python -m src.bot
```

**Terminal 2 · Web de Streamlit**

```powershell
uv run --group web streamlit run web_app.py
```

Mantén las terminales abiertas. Para detener un servicio, pulsa **Ctrl+C**;
el bot deja terminar la entrada activa antes de cerrar. Usa una sola instancia del bot.

| Para… | Necesitas… |
| --- | --- |
| Consultar, crear o borrar entradas en la web | Streamlit. |
| Recibir audios o analizar entradas nuevas y corregidas | Bot y Ollama. |
| Interpretar búsquedas libres con el modelo | Streamlit y Ollama. |
| Buscar por texto, filtros o frases cubiertas por reglas | Streamlit. |

La web es local y todavía no tiene inicio de sesión. Al actualizar el código,
reinicia **el bot y Streamlit**; refrescar el navegador puede conservar módulos
antiguos. Las migraciones crean una copia previa y conservan las entradas.

## 🎙️ Usar Telegram

Envía una nota de voz o un audio al chat privado del bot. Recibirás confirmación
y, si hay cola, el número de entradas que tienes delante. Un único mensaje muestra
el día, la procedencia, el progreso y finalmente un resumen breve.

- Puedes enviar varios audios: se procesan por orden.
- Si falla, pulsa **Reintentar**. La transcripción guardada se reutiliza.
- Si no se detecta voz, envía otro audio. Los documentos genéricos no se admiten.
- El bot atiende solo a tu usuario y avisa al arrancar y al apagarse de forma controlada.

### Audios de otros días

Antes del audio, envía un mensaje como:

```text
fecha 2026-10-20
```

También se admite `fecha 20/10/2026`. Espera la confirmación y envía el audio:
la fecha se aplica **solo al siguiente audio aceptado**. Después vuelve al día del
envío. `fecha hoy` o `fecha cancelar` cancela la selección.

Se permiten fechas desde 1900 hasta hoy. La selección sobrevive a un reinicio
y no se consume con un audio rechazado por tamaño o duración.

## 🌸 Usar la web

### Entradas

Elige un día en el calendario. Puedes moverte con las flechas o los selectores
de mes y año, y consultar el texto, resumen, emociones, personas, lugares y acontecimientos.

- **＋ Añadir entrada:** escribe una entrada para el día seleccionado y guárdala.
- **Editar entrada:** corrige el texto; el original se conserva y el análisis se regenera.
- **Editar personas:** corrige la identidad de una mención o déjala sin identificar.
- **Borrar entrada:** confirma el borrado definitivo de la entrada y su audio local.

El bot recoge entradas y correcciones pendientes cada cinco segundos mientras
está activo y las recupera al arrancar. También avisa en Telegram cuando procesa
una entrada web. Si el análisis falla, puedes reintentarlo desde la web.

No se editan entradas pendientes o en procesamiento. Las entradas en cola se
pueden borrar; si ya se están procesando, espera a que terminen. Las fichas de
personas y las copias previas se conservan; el mensaje original de Telegram no se borra.
Si el audio está bloqueado, su limpieza queda pendiente del mantenimiento.

El calendario, el buscador y las estadísticas utilizan la **fecha del diario**,
independiente de cuándo se creó o envió la entrada.

### Mis personas

Añade una ficha con nombre, relación, residencia, procedencia, grupos y alias.
Solo el nombre es obligatorio; usa nombres distintos para distinguir las fichas.
Separa los alias con comas o `;`, por ejemplo: `mamá, mami, mi madre`.

Los nombres y alias se reconocen sin distinguir mayúsculas ni tildes de vocales.
Las ambigüedades quedan pendientes salvo que haya una pista explícita y única,
como «mi prima Marta». Las correcciones manuales se conservan al reanalizar.
Una misma expresión comparte identidad en todas sus apariciones de esa entrada.

**Por organizar** muestra menciones sin identificar. **Propuestas del histórico**
permite revisar y aceptar asociaciones anteriores. **Archivada** retira una ficha
de las nuevas asociaciones sin perder su histórico.

### Buscador

Escribe, por ejemplo, **«días con mamá el mes pasado»** y pulsa **Buscar**.
La web muestra los criterios entendidos y los resultados; **Editar interpretación**
permite corregirlos. Las frases habituales se resuelven con reglas y el resto
se interpreta con Ollama. La interpretación libre todavía necesita mejorar y evaluarse.

- **Buscar como texto literal:** busca palabras, nombres y alias en todo el diario.
- **Búsqueda con filtros:** combina fechas, persona, lugar, emoción, sentimiento,
 origen y estado, sin depender del modelo.
- Los nombres y alias pueden encontrar menciones únicas del histórico aún sin
 confirmar. El selector **Persona** exige una asociación ya guardada.

«Último mes» significa un mes hacia atrás desde hoy; «mes pasado», el mes natural
anterior. Los filtros se aplican a la misma entrada. Se cuentan entradas o días
coincidentes: mencionar la oficina no demuestra haber ido allí.

Esta versión todavía no genera respuestas resumidas con evidencias ni utiliza
embeddings. Más detalles en [el diseño del buscador](docs/buscador-natural.md).

### Estadísticas y Tu año en píxeles

Consulta actividad, días registrados, rachas, frecuencia, emociones y duración
de audios por periodo. **Tu año en píxeles** muestra el sentimiento mayoritario
o la cantidad de entradas de cada día; pulsa un píxel para ver sus entradas.

Las duraciones desconocidas se indican y pueden recuperarse de los audios que
todavía se conserven. Pulsa **Actualizar datos** para ver los cambios recientes.
Las cifras describen lo registrado en el diario, no acontecimientos únicos.

## 🎓 Alcance del TFM

El TFM desarrolla un prototipo de diario personal por voz con cuatro componentes:

1. **Captura y análisis:** recepción por Telegram, transcripción con Whisper,
   resumen y extracción de sentimiento, emociones, personas, lugares y acontecimientos con un LLM.
2. **Memoria organizada:** persistencia en SQLite, fichas y resolución de personas,
   fechas del diario y una interfaz web para consultar y corregir los datos.
3. **Consulta en lenguaje natural:** interpretación de preguntas como planes de
   búsqueda validados, filtros temporales y recuperación de entradas o días.
4. **Evaluación:** calidad de la transcripción y del análisis, interpretación de
   búsquedas, fidelidad de los resúmenes, fallos y tiempos de respuesta.

Hay pruebas automatizadas, un corpus sintético y herramientas de evaluación.
**La validación final sigue pendiente:** requiere audios reales anotados, datos
independientes, comparación de configuraciones y valoración humana de los resúmenes.
La interpretación de búsquedas libres tampoco se considera validada todavía.

Las siguientes ampliaciones planteadas son acontecimientos estructurados para
recuentos fiables, respuestas breves con fuentes y búsqueda por similitud conceptual.
Los resúmenes semanales, exportaciones y acceso remoto también quedan pendientes.

## 🧪 Pruebas y evaluación

```powershell
uv run --group web python -m pytest -q
```

Las pruebas usan datos temporales y clientes simulados: no envían mensajes reales
ni consumen modelos. Los experimentos de evaluación sí utilizan modelos reales:

- [Evaluación de transcripción y análisis](evaluation/README.md).
- [Resultados de desarrollo](evaluation/RESULTADOS_DESARROLLO.md).
- [Diseño y evaluación del buscador](docs/buscador-natural.md).

Para evaluar el intérprete de búsquedas forzando Ollama, incluso en las frases
cubiertas por reglas:

```powershell
uv run python -m evaluation.search --model-only
```

## 🗂️ Datos y copias

SQLite conserva los textos y análisis; los audios locales se guardan siete días
por defecto. El bot crea una copia local diaria de SQLite, también con siete días
de retención por defecto. Las copias no incluyen los archivos de audio.

Whisper y Ollama procesan localmente por defecto, pero audios y mensajes pasan
por Telegram. La base y las copias contienen información personal y no tienen
cifrado propio. Conserva una copia fuera del equipo para protegerte ante su pérdida.

Para restaurar, detén bot y web, conserva una copia de la base actual y sustituye
el archivo de `DATABASE_PATH` por la copia elegida. Después arranca los servicios.
Los audios caducados no se recuperan con la copia de SQLite.
