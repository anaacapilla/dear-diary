# Comprobación de desarrollo

Se ejecutó el corpus synthetic.jsonl con Ollama local y qwen3:1.7b. Se compararon
ajustes de prompt sobre esos mismos diez ejemplos y se conservó un prompt breve,
combinado con filtrado literal de entidades. Este uso convierte el corpus en un
conjunto de desarrollo; no debe presentarse como evaluación independiente del TFM.

Resultado de la ejecución final (prompt 5):

| Medida | Valor |
| --- | ---: |
| Muestras | 10 |
| Fallos de ejecución | 0 |
| Exactitud de sentimiento | 0,900 |
| F1 micro de personas | 0,833 |
| F1 micro de lugares | 0,833 |
| F1 micro de emociones | 0,714 |
| Tiempo medio de análisis | 12,01 s |

Los resultados completos y los metadatos están en evaluation/results/
synthetic-qwen3-v5.jsonl y sus archivos asociados, excluidos de Git.
Los metadatos incluyen prompts, digest, parámetros y código utilizado.
Las mediciones pueden variar entre ejecuciones y dependen del equipo.

La evaluación detecta errores residuales del modelo: no se consideran resueltos por
validar JSON ni por pasar las pruebas del programa. Falta evaluar habla real, WER,
fidelidad de los resúmenes y corpus independiente. Una prueba real de Whisper base
sobre silencio devolvió una transcripción vacía; no mide su precisión sobre habla.
