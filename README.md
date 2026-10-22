# dear-diary

Diario de voz personal en Telegram. Recibe un audio en el chat privado autorizado,
lo transcribe con faster-whisper, extrae resumen, sentimiento, emociones, personas,
lugares y acontecimientos con Ollama y lo guarda en SQLite.

## Instalación

Requisitos: Python 3.12+, uv y Ollama ejecutándose con un modelo disponible.

```powershell
uv sync --locked
Copy-Item .env.example .env
ollama pull qwen3:1.7b
uv run python -m src.bot
```

Edita el token y el identificador numérico en .env antes de arrancar. No vuelvas a
copiar el ejemplo sobre una configuración existente. El proyecto requiere un único
proceso del bot por base de datos y token. No se debe iniciar otra instancia mientras
la anterior está procesando entradas.

Los valores predeterminados son Europe/Madrid y siete días de conservación de audios.
Las rutas relativas de configuración se resuelven desde la raíz del proyecto.
Usa un directorio dedicado para AUDIO_DIR y otro para BACKUP_DIR.

Whisper utiliza el identificador base, compatible con CTranslate2; el valor antiguo
openai/whisper-base se traduce automáticamente a base para conservar la compatibilidad
con .env existentes. El modelo se carga al transcribir el primer audio; puede
descargar sus pesos si no están en caché. Las pruebas no lo cargan.

WHISPER_DEVICE=auto detecta CUDA mediante CTranslate2. Una GPU requiere las bibliotecas
compatibles con la versión instalada de CTranslate2. Si no están disponibles, configura
WHISPER_DEVICE=cpu y WHISPER_COMPUTE_TYPE=int8. PyTorch no es necesario para este bot.
Los paquetes de notebooks están en el grupo opcional notebooks; para usarlos:
uv sync --group notebooks. Los notebooks antiguos son exploratorios y pueden requerir
sus propias dependencias adicionales.

## Uso de Telegram

Envía una nota de voz o un audio en tu chat privado. El bot confirma la recepción,
indica cuántas entradas hay delante y actualiza un único mensaje mientras descarga,
transcribe y analiza. Al terminar muestra «Entrada guardada» y un resumen breve.
Si falla, conserva lo procesado y ofrece únicamente el botón Reintentar.
Si no detecta voz, te pide enviar otro audio.

El menú de comandos queda vacío. /start se conserva como respuesta mínima al botón
Iniciar de Telegram: «Envíame un audio para añadir una entrada a tu diario».
Los mensajes de texto y los comandos antiguos reciben esa misma orientación;
no consultan, modifican ni borran entradas. La consulta y la edición se hacen en la web.
Los botones antiguos se retiran al pulsarlos o cuando se actualiza su tarjeta.

Al arrancar envía «Bot en marcha. Puedes enviarme audios». Si hay entradas
interrumpidas, añade el aviso de recuperación al mismo mensaje. Al detenerlo de
forma controlada (por ejemplo, con Ctrl+C), termina la entrada activa y envía
«Bot detenido. No procesaré audios hasta que vuelva a arrancar» antes de cerrar
la conexión con Telegram. Un cierre forzado, un corte eléctrico o la falta de
conexión pueden impedir ese aviso. Se mantienen las copias y la limpieza
automáticas, sin comandos de administración en Telegram.

El bot acepta únicamente al usuario configurado, dentro de su chat privado.
No responde con información del diario en grupos. Los archivos enviados como documentos
genéricos no se admiten; se admiten notas de voz y mensajes de tipo audio de Telegram.

## Procesamiento y recuperación

Cada mensaje se identifica por chat_id + message_id. Volver a recibir el mismo mensaje
no duplica una entrada, pero dos mensajes con las mismas palabras sí crean dos entradas.
Sin identidad de Telegram, cada llamada de guardado crea una entrada independiente.

1. Guarda una entrada pending con el identificador del archivo de Telegram.
2. Confirma la recepción y añade la entrada a la cola. Un único trabajador descarga
   el audio con un nombre UUID y renombrado atómico desde un archivo .part.
3. Transcribe y guarda el texto antes de llamar al LLM: estado transcribed.
4. Valida el JSON y guarda el análisis y todas sus relaciones en una transacción: completed.
5. Si no hay voz, marca empty. Si falla una etapa, marca failed y conserva lo ya guardado.

Un fallo de Ollama no borra la transcripción. Reintentar reutiliza el texto existente.
Si todavía no hay transcripción y ya caducó el audio, intenta descargarlo otra vez de
Telegram; la recuperación depende de que Telegram siga ofreciendo ese archivo.
Al arrancar recupera automáticamente las entradas interrumpidas en estado pending
o transcribed que tienen un mensaje de Telegram del usuario autorizado.
Las entradas failed se reintentan desde el botón de su tarjeta. Las entradas antiguas
sin destino de Telegram requieren gestión local; no se reintentan automáticamente.
No se descartan las actualizaciones que Telegram tenga pendientes al reiniciar.
El arranque solicita expresamente `message` y `callback_query` para recibir tanto
audios como pulsaciones, aunque Telegram conserve un filtro de una ejecución anterior.
Telegram no es una cola duradera ilimitada: conserva actualizaciones durante un periodo
limitado, por lo que conviene mantener el bot ejecutándose.

Se limitan tamaño y duración declarados del audio a 20 MB y 30 minutos por defecto.
Una transcripción larga se analiza por bloques; los resúmenes se sintetizan de forma
jerárquica sin recortar silenciosamente la entrada. Cada petición a Ollama tiene un
timeout de 120 segundos y hasta dos intentos. La salida se valida con Pydantic.
Las instrucciones incluidas en un diario se tratan como datos en el prompt, aunque
esta separación y el esquema JSON no garantizan por sí solos la fidelidad semántica.

Cada entrada conserva en SQLite el identificador de su mensaje editable, la
transcripción original, las correcciones y las métricas. Se mantiene la migración
compatible con las bases anteriores, con copia previa y sin borrar entradas.

## Prueba manual: interfaz simplificada

1. Detén la instancia anterior y arranca con `uv run python -m src.bot`.
2. Comprueba que desaparece el menú de comandos y llega un único aviso «Bot en marcha».
3. Pulsa Iniciar o envía /start: debe responder solo con la instrucción para enviar audio.
4. Envía dos audios seguidos: el segundo debe indicar su posición en la cola.
   Cada tarjeta se actualiza y termina con un resumen breve, sin botones de gestión.
5. Envía un audio sin voz: debe pedir otro audio, sin botones de corrección.
6. Provoca un fallo de Ollama y espera la tarjeta de error. Restablece el servicio
   y pulsa Reintentar: debe reutilizar el texto y actualizar la misma tarjeta.
   Pulsar varias veces no debe duplicar el trabajo.
7. Pulsa un botón de gestión de una tarjeta antigua: debe orientar hacia la web
   y mostrar la tarjeta simplificada sin ejecutar la acción antigua.
8. Reinicia con entradas en cola: debe avisar de la recuperación y retomarlas.
9. Guarda una corrección desde la web con el bot encendido: debe regenerarse
   el análisis en la misma tarjeta de Telegram, sin añadir botones de gestión.
10. Detén el bot con Ctrl+C: debe terminar la entrada activa, si la hay, y enviar
    un único aviso «Bot detenido».

## Reportes y fechas

La lógica de reportes se conserva en la capa de datos; ya no se expone como comando
de Telegram.

Las fechas se convierten a Europe/Madrid antes de determinar el día, incluyendo los
cambios de horario de verano. Fechas antiguas sin zona horaria se interpretan en esa
zona. Si cambias DIARY_TIMEZONE, la agrupación se calcula de nuevo con la nueva zona.

El reporte cuenta entradas completed y muestra el número de pendientes/fallidas.
Una entrada usa su propio resumen. Varias entradas producen una síntesis LLM conjunta,
que se guarda en daily_reports. El sentimiento global es el sentimiento común si
todas coinciden, o mezclado si difieren. Personas, lugares y emociones se unen sin
duplicados exactos. Si falla la síntesis, se muestran los resúmenes guardados y se
informa de ello. No se presenta esa concatenación como una síntesis LLM.
Una nueva entrada, una actualización o un borrado invalidan los reportes guardados.

## Persistencia y migraciones

SQLite contiene siete tablas: diary_entries, entities, entry_entities, emotions,
entry_emotions, daily_reports y entry_edit_requests. Guardan las entradas y sus estados,
entidades y emociones normalizadas, reportes derivados y la petición de corrección activa.

La migración de esquema se hace al arrancar, con PRAGMA user_version. Antes de modificar
una base antigua, crea una copia migration-*.sqlite3 junto a la base, en backups/.
Conserva los identificadores, transcripciones y análisis existentes. Activa claves
foráneas en todas las conexiones, aplica transacciones y cierra las conexiones.

El antiguo campo hash pasa a identificar la entrada o mensaje para entradas nuevas;
content_hash guarda la huella del texto sin imponer unicidad. Las filas antiguas
conservan su hash. Un índice único evita duplicados por mensaje de Telegram.

Las personas, lugares y emociones que la versión anterior nunca llegó a guardar no
se pueden reconstruir con una migración de estructura. La transcripción puede
corregirse desde la web para generar un nuevo análisis. Tampoco se pueden recuperar
entradas que el antiguo hash descartó ni audios que ya fueron sobrescritos.

## Conservación y copias

- Los audios caducan a los siete días desde su recepción.
- La limpieza se ejecuta al arrancar y cada hora mientras el bot está en marcha:
  el borrado se produce en la siguiente ejecución tras caducar. Si está apagado,
  se aplica al siguiente arranque.
- La limpieza incluye audios antiguos ya existentes y archivos huérfanos del directorio
  dedicado. Conserva las transcripciones y los análisis, y elimina la referencia al audio.
- SQLite tiene una copia automática diaria durante el mantenimiento.
  Se utiliza la API de backup de SQLite, no una copia insegura de un archivo abierto.
- Copias automáticas, manuales y de migración caducan a los siete días; el control de
  caducidad se ejecuta con el mantenimiento.
- Borrar una entrada no reescribe copias anteriores. Estas pueden contenerla hasta
  que caduquen. Las copias son locales y no incluyen los archivos de audio.

El contenido se procesa localmente con Whisper y, por defecto, con Ollama local.
El audio y las respuestas pasan por Telegram. No se escriben transcripciones,
resúmenes ni cuerpos de excepciones en los logs de la aplicación.
SQLite, audios y backups contienen información personal sin cifrado propio:
deben quedar en una cuenta y disco con acceso controlado. .gitignore los excluye,
pero no elimina archivos que ya estuvieran versionados.

Para restaurar: detén el bot, conserva una copia del archivo actual y copia un backup
sobre DATABASE_PATH; después arranca una única instancia. La restauración recupera
SQLite, no los audios que hayan caducado. No sustituyas el archivo mientras esté abierto.

## Pruebas y evaluación del TFM

```powershell
uv run python -m pytest -q
```

Las pruebas usan SQLite temporal, clientes simulados y credenciales ficticias.
Cubren guardado de relaciones, deduplicación, medianoche y cambios de horario,
migración y restauración, reintentos, límites de Telegram, privacidad, retención,
síntesis, cola FIFO, recepción durante el procesamiento, recuperación al reiniciar,
apagado, mensaje único, menú vacío, avisos de inicio y apagado, autorización y reintento
desde botones, corrección desde la web y evaluación. No envían mensajes reales ni consumen modelos.

El directorio evaluation contiene un corpus textual sintético inicial, un ejecutor
con modelos reales y métricas reproducibles. Consulta evaluation/README.md.
El corpus inicial sirve como comprobación, no como validación científica del TFM.

## Web personal (primera versión)

Instala el grupo opcional y arranca la web desde la raíz del proyecto:

```powershell
uv sync --locked --group web
uv run --group web streamlit run web_app.py
```

Abre  El bot se ejecuta por separado con su comando habitual.
Ambos leen DATABASE_PATH del mismo .env. Arranca primero el bot para inicializar o
migrar la base; las consultas de la web abren SQLite en modo de solo lectura y no
cargan Whisper ni Ollama. Guardar una corrección sí escribe en la base.

La barra lateral ofrece Entradas (calendario mensual y detalle del día), Buscador
(coincidencia literal sin distinguir mayúsculas, con filtro de fechas) y Estadísticas
(entradas por día, estados y sentimientos). Pulsa Actualizar datos para releer la base.
Las fechas del calendario son fechas de envío, no fechas inferidas de acontecimientos.
Los resultados se muestran de diez en diez. El calendario permite cambiar de mes
con flechas o con los selectores de mes y año. Desde el detalle del calendario,
Editar entrada permite corregir la transcripción completa, guardar o cancelar.
Se conserva el original, se invalida el análisis anterior y el bot recoge las
entradas pendientes cada cinco segundos para regenerarlo. Reinicia una vez el bot
tras instalar esta actualización para activar esa recogida; si está apagado, las
correcciones de entradas Telegram se recuperan al arrancar. Las entradas antiguas
sin identidad Telegram requieren gestión local para regenerar su análisis. No se pueden editar entradas en proceso;
una edición obsoleta no sobrescribe cambios posteriores.
Las preguntas en lenguaje natural quedan pendientes de una siguiente fase.
Telegram queda centrado en recibir audios y comunicar su procesamiento.

Por defecto solo escucha en interfaz local y no tiene autenticación propia. Para acceder
remotamente, usa un túnel SSH o acceso privado autenticado; no expongas el puerto
públicamente. La web muestra contenido personal del diario.

Pruebas incluyendo la interfaz: `uv run --group web python -m pytest -q`.
