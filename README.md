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

## Comandos

- /start: bienvenida.
- /help: ayuda y política de conservación.
- /report YYYY-MM-DD: reporte de una fecha en la zona del diario.
- /entries YYYY-MM-DD: identificadores y estados de las entradas del día.
- /retry: añade a la cola hasta diez entradas pendientes o fallidas.
- /retry ID: añade una entrada concreta a la cola, o vuelve a analizar una terminada.
- /delete ID: borra una entrada, sus relaciones y su archivo de audio.
- /backup: guarda una copia adicional de SQLite en el equipo que ejecuta el bot.

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

Un fallo de Ollama no borra la transcripción. /retry reutiliza el texto existente.
Si todavía no hay transcripción y ya caducó el audio, intenta descargarlo otra vez de
Telegram; la recuperación depende de que Telegram siga ofreciendo ese archivo.
Al arrancar recupera automáticamente las entradas interrumpidas en estado pending
o transcribed que tienen un mensaje de Telegram del usuario autorizado.
Las entradas failed y las antiguas sin destino de Telegram requieren /retry.
No se descartan las actualizaciones que Telegram tenga pendientes al reiniciar.
Telegram no es una cola duradera ilimitada: conserva actualizaciones durante un periodo
limitado, por lo que conviene mantener el bot ejecutándose.

Se limitan tamaño y duración declarados del audio a 20 MB y 30 minutos por defecto.
Una transcripción larga se analiza por bloques; los resúmenes se sintetizan de forma
jerárquica sin recortar silenciosamente la entrada. Cada petición a Ollama tiene un
timeout de 120 segundos y hasta dos intentos. La salida se valida con Pydantic.
Las instrucciones incluidas en un diario se tratan como datos en el prompt, aunque
esta separación y el esquema JSON no garantizan por sí solos la fidelidad semántica.

Las respuestas largas se dividen en mensajes de hasta 3500 unidades UTF-16, incluyendo
listas largas y caracteres emoji. El resumen del informe individual se abrevia en
Telegram, mientras que SQLite conserva el resumen completo.

## Mejora de experiencia 1: recepción y cola

Esta entrega incorpora únicamente la recepción con cola. La edición de mensajes de
progreso, los botones y las demás mejoras de experiencia quedan para entregas posteriores.

- La recepción registra primero la entrada en SQLite y confirma antes de descargar
  el audio o cargar Whisper: «Audio recibido. Lo procesaré a continuación».
- Si hay más audios, indica cuántas entradas tiene delante, incluyendo la activa.
  Es una instantánea al confirmar; el mensaje no se actualiza mientras avanza la cola.
- Un único trabajador procesa las entradas en orden FIFO durante la sesión.
  No se ejecutan varias transcripciones o análisis de entradas en paralelo.
- Puedes enviar más audios y usar /help o /entries mientras trabaja. Las operaciones
  largas de /report, /backup y /delete se atienden en segundo plano y esperan su turno
  para acceder a los recursos compartidos; no bloquean la recepción de nuevos audios.
- /retry utiliza la misma cola. Una entrada que ya está esperando o procesándose no
  se añade otra vez. Una entrada fallida se marca pending al solicitar el reintento,
  conservando su transcripción.
- Los mensajes de procesamiento y el resultado responden al audio original, también
  cuando se retoma tras reiniciar. Si el audio se ha eliminado del chat, el bot intenta
  entregar igualmente la respuesta.
- Un fallo de análisis no detiene las entradas siguientes. Si Telegram no entrega la
  confirmación, la entrada permanece registrada y su procesamiento continúa.
- La cola de ejecución está en memoria y los datos y estados están en SQLite.
  Al reiniciar se recuperan pending y transcribed por orden de registro.
  Los fallos no se reintentan automáticamente para evitar bucles de errores.
- Al detener con Ctrl+C se deja terminar la entrada activa antes de cerrar la conexión
  con Telegram. Las entradas aún sin empezar quedan pendientes para el siguiente
  arranque. El cierre puede tardar si el audio activo todavía se está procesando.
- Los reportes y otras solicitudes de comandos no son trabajos persistentes:
  si una interrupción impide responderlos, vuelve a ejecutar el comando.

No requiere cambios de esquema ni dependencias nuevas.

### Prueba manual de esta entrega

1. Arranca una única instancia con `uv run python -m src.bot`.
2. Envía tres notas de voz seguidas, sin esperar al primer resultado.
3. Comprueba que cada una recibe confirmación antes de su procesamiento y que la
   segunda y la tercera indican las entradas que tienen delante cuando corresponde.
4. Mientras se analiza la primera, envía /help y consulta /entries con la fecha de hoy.
   Deben responder sin esperar a que terminen todos los audios.
5. Comprueba que los resultados llegan por orden y responden a su audio correspondiente.
6. Ejecuta /retry ID sobre una entrada en cola: debe indicar que ya está esperando
   o procesándose, sin duplicar el trabajo.
7. Para comprobar la recuperación, envía varios audios y pulsa Ctrl+C mientras se
   procesa el primero. Espera al cierre y vuelve a arrancar: los que no habían
   empezado deben retomarse automáticamente.
8. Opcionalmente, detén Ollama para provocar un fallo: el bot debe conservar la
   transcripción y seguir atendiendo entradas. Vuelve a iniciarlo y usa /retry ID.

## Reportes y fechas

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

SQLite contiene seis tablas: diary_entries, entities, entry_entities, emotions,
entry_emotions y daily_reports. La primera guarda el contenido y los estados de proceso;
las siguientes normalizan entidades y emociones; la última guarda reportes derivados.

La migración de esquema se hace al arrancar, con PRAGMA user_version. Antes de modificar
una base antigua, crea una copia migration-*.sqlite3 junto a la base, en backups/.
Conserva los identificadores, transcripciones y análisis existentes. Activa claves
foráneas en todas las conexiones, aplica transacciones y cierra las conexiones.

El antiguo campo hash pasa a identificar la entrada o mensaje para entradas nuevas;
content_hash guarda la huella del texto sin imponer unicidad. Las filas antiguas
conservan su hash. Un índice único evita duplicados por mensaje de Telegram.

Las personas, lugares y emociones que la versión anterior nunca llegó a guardar no
se pueden reconstruir con una migración de estructura. Usa /entries y /retry ID para
volver a analizar la transcripción de una entrada antigua. Tampoco se pueden recuperar
entradas que el antiguo hash descartó ni audios que ya fueron sobrescritos.

## Conservación y copias

- Los audios caducan a los siete días desde su recepción.
- La limpieza se ejecuta al arrancar y cada hora mientras el bot está en marcha:
  el borrado se produce en la siguiente ejecución tras caducar. Si está apagado,
  se aplica al siguiente arranque.
- La limpieza incluye audios antiguos ya existentes y archivos huérfanos del directorio
  dedicado. Conserva las transcripciones y los análisis, y elimina la referencia al audio.
- SQLite tiene una copia automática diaria y copias manuales con /backup.
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
apagado y evaluación. No envían mensajes reales ni consumen modelos.

El directorio evaluation contiene un corpus textual sintético inicial, un ejecutor
con modelos reales y métricas reproducibles. Consulta evaluation/README.md.
El corpus inicial sirve como comprobación, no como validación científica del TFM.
