# Buscador con lenguaje natural

## Experiencia propuesta e implementada

Una caja de texto con ejemplos y un botón Buscar. Debajo aparecen la interpretación
real del plan y los resultados. Los controles no ocupan la pantalla inicial:
Editar interpretación se abre solo tras una búsqueda; Búsqueda con filtros queda
plegada. La alternativa literal funciona sin Ollama y conserva los alias del histórico.

Ejemplo con fecha de referencia 24/10/2026, Europe/Madrid:

> días que he ido a la ofi este último mes

Plan: 24/09/2026–24/10/2026, lugar oficina, presentación por días. Solo se usa
oficina si existe en el catálogo. Una ausencia de resultados no elimina filtros.
La fecha queda fijada durante la paginación; una nueva búsqueda obtiene la fecha
actual. Los resultados se vuelven a consultar para recoger ediciones del diario.

## Alternativas revisadas

| Opción | Ventajas | Problemas en este proyecto | Decisión |
| --- | --- | --- | --- |
| Mantener todos los selectores | Exactitud y funcionamiento sin modelo | Obliga a traducir mentalmente la frase y ocupa demasiado | Conservar plegados como alternativa |
| Gramática de reglas | Respuesta inmediata y fechas reproducibles | No cubre el español libre, combinaciones y expresiones nuevas | Usar solo cuando se reconoce la frase completa |
| Modelo que genera SQL | Flexibilidad inicial | Acoplamiento al esquema, joins incorrectos, ambigüedades difíciles de explicar | Descartado |
| Modelo que genera un plan tipado | Entiende expresiones libres y usa el catálogo, resultado inspeccionable | Puede fallar semánticamente aunque el JSON sea válido; latencia | Implementado, validado antes de ejecutar |
| FTS5 | Búsqueda textual indexada y rápida | No calcula periodos ni identifica personas; los sinónimos requieren tratamiento propio | Posible optimización futura del texto, no intérprete |
| Embeddings | Útiles para recuerdos sobre temas y similitud conceptual | Similitud no garantiza fechas ni identidades; requiere índice y evaluación adicionales | Futuro complemento para conceptos residuales |
| Servicio de inferencia externo | Permite comparar modelos sin consumir recursos locales | Introduce proveedor, coste y una decisión de privacidad | No se ha añadido ni enviado información a un servicio nuevo |

Ollama permite pasar un JSON Schema y validar con Pydantic, justo el mecanismo
que ya usa el análisis de entradas. Eso restringe la estructura; no demuestra que
la interpretación sea correcta. Referencia: [Structured outputs de Ollama](https://docs.ollama.com/capabilities/structured-outputs).
FTS5 separa tokenización, búsqueda y soporte de sinónimos; no sustituye el análisis
de intención. Referencia: [documentación de SQLite FTS5](https://www.sqlite.org/fts5.html).

## Decisión técnica

1. Obtener fecha de referencia y zona del diario en el servidor.
2. Construir un catálogo con nombres y alias, entidades vinculadas, emociones,
   sentimientos, estados y orígenes. No pasar entradas, resúmenes ni audios.
3. Intentar la gramática corta de frases completas. No extraer palabras sueltas
   ignorando el resto de las condiciones.
4. Si no hay coincidencia completa, pedir a Ollama un SearchPlan con esquema:
   periodo simbólico, condiciones, días de semana, vista y posible aclaración.
5. Validar todos los campos y resolver valores contra el catálogo. Un valor
   inexistente o ambiguo no se descarta para ampliar resultados.
6. Calcular las fechas en Python y ejecutar solo consultas de lectura conocidas.
7. Mostrar los criterios realmente ejecutados. Permitir corregir fechas,
   valores, cualquiera/todos, exclusiones y días semanales sin llamar al modelo.

El modelo no recibe herramientas para consultar o escribir SQL. El intérprete usa
`SEARCH_MODEL` (por defecto `OLLAMA_MODEL`) y temperatura 0. No necesita migración
de BBDD. Los nuevos tipos de entidad indexados aparecen como `entity:<tipo>`.

## Fechas

| Expresión | Criterio con referencia 24/10/2026 |
| --- | --- |
| este último mes / último mes | 24/09/2026–24/10/2026 |
| este mes | 01/10/2026–24/10/2026 |
| el mes pasado | 01/09/2026–30/09/2026 |
| ayer | 23/10/2026 |
| semana pasada | 12/10/2026–18/10/2026, de lunes a domingo |
| este año | 01/01/2026–24/10/2026 |
| sin fecha | Todo el diario |

Los intervalos incluyen ambos extremos. «Últimos N días» incluye N fechas civiles,
contando hoy: últimos 30 días es 25/09/2026–24/10/2026. Los meses y años se
desplazan como periodos de calendario en lugar de convertirlos a 30/365 días.
Los meses se desplazan como meses de calendario: 31/03/2024 menos un mes da
29/02/2024. Un año hacia atrás desde 29/02/2024 da 28/02/2023.
No se aceptan periodos futuros ni anteriores a 1900. Las fechas son días del
diario, no fechas de subida o procesamiento.

## Entidades, sinónimos y lógica

- Los alias de personas conservan la resolución ya existente, incluidas las
  coincidencias históricas únicas y las correcciones manuales. No se cambia el histórico.
- Ofi se resuelve solo a oficina/la oficina indexada y única. No equivale
  automáticamente a trabajo, empresa o coworking. No hay todavía un registro
  persistente de alias de lugares.
- El modelo puede proponer otros equivalentes léxicos, pero su destino debe
  existir. Una persona o lugar sin catálogo puede buscarse como texto residual;
  esa modalidad queda visible en la interpretación.
- Ana o Luis usa unión; Ana y Luis usa intersección. Las condiciones de campos
  diferentes se combinan con AND y se comprueban dentro de una misma entrada.
- «Madrid o alegría» no es representable por este plan; debe pedir aclaración.
- Sin Ana excluye entradas coincidentes, no prueba que Ana no estuviera allí.
- Emociones desconocidas y lugares no extraídos no se convierten en evidencias
  negativas. La cobertura depende del análisis almacenado.
- Las personas archivadas siguen disponibles para consultar su histórico.
- Lugares y emociones son datos del análisis de la entrada. «Oficina» puede
  aparecer en «no fui a la oficina»; este esquema no guarda una relación de visita.

## Alcance y siguientes ampliaciones

La primera versión busca entradas o días y cuenta coincidencias. No responde por
qué ocurrió algo, no sintetiza recuerdos ni compara dos periodos. La exclusión
de días completos y AND entre entradas distintas de un mismo día tampoco se
representan: contar días agrupa después de filtrar entradas.

Para responder con precisión «días que fui físicamente a la oficina», la mejora
de datos sería guardar acontecimientos estructurados: acción, persona, lugar,
fecha, negación y modalidad (hecho, deseo o hipótesis), con evidencia textual.
Alternativamente, una segunda fase podría revisar los fragmentos recuperados,
mostrar evidencia y separar afirmaciones, negaciones y casos inciertos. Eso
requiere una evaluación propia y aumenta tiempo de respuesta. No se ha añadido
una segunda llamada al modelo por cada resultado en esta versión.

Para crecer: normalizar lugares y alias de forma editable, luego FTS5 para texto
y, si las consultas temáticas lo justifican, embeddings sobre fragmentos con
filtros estructurados previos. La BBDD revisada contiene 11 entradas: no hay
evidencia actual de necesitar infraestructura vectorial o una migración de búsqueda.

## Evaluación y estado real

Las pruebas automatizadas cubren cálculo de fechas, fines de mes y bisiestos,
consulta del reloj, ausencia de texto privado en el prompt, validación, alias
ambiguos, decisiones manuales, AND/OR, exclusiones, valores no indexados,
fallos de conexión, JSON inválido, consulta literal e interacción Streamlit.

`python -m evaluation.search` usa un catálogo sintético y verifica planes esperados.
`python -m evaluation.search --model-only` fuerza Ollama incluso en frases de
la gramática; no debe confundirse la cobertura por reglas con la calidad del modelo.
`--diagnose` consulta el modelo instalado e intenta una respuesta breve.
No consulta el diario real. Para comparar otro modelo basta configurar SEARCH_MODEL.

La primera pasada real, forzando qwen3:1.7b en las 11 consultas, obtuvo solo
2/11 planes aceptados y correctos: 7 timeouts de 30 segundos y 2 respuestas
inválidas. Es una pasada exploratoria, no un benchmark repetido. Incluso la
prueba básica de generación superó 10 segundos,
aunque listar modelos respondió. Esto no acredita todavía suficiente precisión
ni latencia para las consultas libres. La gramática garantiza el ejemplo habitual
sin depender del modelo. No se ha descargado ni cambiado el modelo del usuario.

Antes de ampliar el uso libre, repetir el conjunto con el modelo caliente y frío,
registrar acierto exacto del plan, tasa de aclaraciones y latencia. Incluir una
regresión específica para cada interpretación errónea; temperatura cero y JSON
Schema no sustituyen esa evaluación. El catálogo tiene un límite explícito de
tamaño: se informa del exceso, nunca se trunca ocultando alias compartidos.
