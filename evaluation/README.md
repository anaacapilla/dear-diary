# Evaluación del TFM

El corpus synthetic.jsonl contiene diez ejemplos textuales ficticios anotados:
sentimiento, emociones, personas y lugares. Es un punto de partida para comprobar el
evaluador y detectar problemas; no representa un estudio con usuarios ni mide Whisper.
Las etiquetas de sentimiento y emociones son decisiones de anotación que deben revisarse.

## Ejecutar

Con Ollama disponible y el entorno instalado, desde la raíz del repositorio:

```powershell
uv run python -m evaluation.run --dataset evaluation/synthetic.jsonl --output evaluation/results/experimento-01.jsonl
uv run python -m evaluation.score evaluation/results/experimento-01.jsonl --output evaluation/results/metricas-01.json
```

El ejecutor rechaza sobrescribir predicciones anteriores. No abre la base del diario
ni usa Telegram. Los resultados contienen textos y predicciones; no se versionan.
Antes de ejecutar guarda la huella del corpus, el digest del modelo Ollama si el servicio
lo proporciona, los prompts completos, parámetros, paquetes, plataforma, revisión de Git
y una instantánea del código de inferencia y evaluación. Esto conserva también los
experimentos hechos antes de un commit. Añade el hardware y las condiciones acústicas
al registro del estudio. El tiempo inicial puede incluir carga o descarga; mide en frío
y en caliente por separado.

## Corpus de audio

Cada línea del JSONL puede tener audio en lugar de text. La ruta se resuelve desde la
carpeta del corpus. reference_transcription es obligatoria para audios:

```json
{"id":"audio-01","audio":"audios/01.wav","reference_transcription":"Hoy hablé con Ana en Madrid.","expected":{"sentiment":"neutral","emotions":[],"people":["Ana"],"places":["Madrid"]}}
```

Para silencio, usa reference_transcription vacía y no anotes sentimiento. Las menciones
de personas o lugares se anotan incluso si aparecen en frases negadas o hipotéticas:
extraemos menciones, no afirmamos que una visita o encuentro haya ocurrido.
Los acontecimientos y el resumen deben conservar esa negación o modalidad.
Las emociones se anotan como experiencia del narrador; una emoción atribuida únicamente
a otra persona no es una emoción del narrador. Usa los valores del esquema del código.

Elige un corpus independiente, anonimizado y con autorización para su uso. Como estudio
piloto, prepara 30–50 audios variados y amplía según el alcance acordado con el tutor:
silencio, ruido, distintas duraciones, nombres propios, ambigüedad, negaciones,
varias emociones y frases que parezcan instrucciones al modelo.
Guarda los datos personales fuera de Git; evaluation/private/ y evaluation/results/
están excluidos. Los ejemplos sintéticos del repositorio no contienen datos personales reales.

## Métricas y protocolo

- Transcripción: WER por distancia de edición sobre palabras en minúsculas, con
  normalización Unicode y sin puntuación. Se acumulan errores y palabras de referencia
  antes de dividir; WER puede superar 1. Los silencios alucinados se cuentan aparte.
- Entidades y emociones: precisión, exhaustividad y F1 micro sobre conjuntos por entrada.
  Se normalizan espacios, mayúsculas y Unicode, sin coincidencias difusas ni resolución
  de alias. Una mención parcial puede contar como error aun si identifica a la misma persona.
  También se muestran TP, FP y FN y el número de ejemplos anotados.
- Sentimiento: exactitud sobre ejemplos anotados. Los fallos de ejecución cuentan como
  incorrectos, no desaparecen del denominador.
- Funcionamiento: tasa de fallo, tiempos medios y medianos, y número de muestras con
  medición. Revisa los fallos por etapa; WER solo incluye audios con transcripción
  obtenida y publica su cobertura junto a la tasa de fallo.
- Resúmenes: valoración humana independiente de fidelidad, cobertura y legibilidad.
  El formato JSON correcto no prueba que el resumen sea fiel.

Reserva un subconjunto para ajustar prompts y otro que no se use hasta la evaluación
final. Para cada resumen, anota hechos inventados, omisiones relevantes, conservación
de negaciones y valoraciones de 1 a 5 para fidelidad, cobertura y legibilidad.
Si es posible, usa dos anotadores y registra desacuerdos y cómo los resuelves.
Compara al menos dos configuraciones sobre exactamente los mismos audios. Presenta
resultados por condición acústica y duración, además de los totales.

## Límites conocidos

El filtrado de entidades exige una mención literal y descarta etiquetas del narrador;
puede perder una entidad si el modelo la parafrasea. Tampoco garantiza que un fragmento
literal se haya clasificado correctamente como persona o lugar.
El modelo pequeño qwen3:1.7b puede fallar en sentimientos, emociones, atribuciones y
negaciones incluso cuando respeta el esquema. El evaluador permite medir estos fallos.
No se impone una corrección por palabras clave que pueda ocultar errores del modelo.
Las modificaciones de prompt no mejoran necesariamente todas las métricas a la vez.

No se incluyen métricas inventadas de usuarios o audios reales. Los resultados del corpus
sintético son comprobaciones locales de desarrollo y no sustituyen el estudio final.
