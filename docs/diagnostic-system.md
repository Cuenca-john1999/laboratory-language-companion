# Sistema de diagnóstico inicial adaptativo

## 1. Objetivo

El diagnóstico inicial de LLC debe obtener una primera imagen útil y
auditable de las capacidades de Jhon sin convertir una muestra breve en una
etiqueta global. La experiencia debe sentirse como una primera clase: explica
qué está ocurriendo, permite equivocarse, ofrece aclaraciones y termina con
recomendaciones concretas.

El resultado es una estimación interna por habilidad y modalidad. No es un
examen oficial, no certifica un nivel CEFR y no debe resumirse como «eres A1».
Una habilidad no se considera dominada por una única respuesta ni por el
diagnóstico completo sin evidencias posteriores de aprendizaje.

El diseño parte del perfil actual de Jhon: español nativo, inglés aproximado B2,
exposición irregular al alemán en Alemania, lectura aparentemente más fuerte
que habla y escucha, y prioridad en comprensión rápida y producción espontánea.
Sus intereses profesionales y personales sirven para dar contexto, no para
subir la puntuación ni para presumir vocabulario previo.

## 2. Alcance inicial

### Primera versión: solo texto

La primera versión debe durar normalmente entre 15 y 25 minutos, poder pausarse
y continuar desde el último punto estable, y funcionar completamente en local.
Su alcance es:

- comprensión escrita;
- producción escrita breve;
- gramática activa;
- vocabulario receptivo y productivo;
- familiaridad con alemán cotidiano;
- familiaridad inicial con vocabulario profesional y de laboratorio;
- mantenimiento de la comunicación mediante una simulación escrita;
- una aproximación limitada a la fluidez escrita, claramente identificada como
  tal.

La versión de texto no puede medir de forma válida comprensión auditiva,
producción oral, pronunciación ni fluidez oral. Tampoco debe convertir una
respuesta escrita en evidencia de `speaking.basic`. Esas áreas se mostrarán
como «no evaluadas todavía», no como debilidades.

### Fuera del alcance inicial

- un banco completo de preguntas;
- audio, grabación o transcripción;
- certificación o equivalencia CEFR global;
- actualización irreversible del progreso;
- generación libre del itinerario por un LLM;
- contenido superior al currículo versionado disponible;
- diagnóstico clínico, cognitivo o de dificultades de aprendizaje.

El currículo activo `a0-a1.v1` contiene catorce habilidades, con referencias
curriculares `A0` y `A1`; `pre-A1` es una banda diagnóstica de salida, no un valor
de `CurriculumSkill.cefr_reference`. No contiene aún habilidades explícitas
`reading.*` o
`writing.*`; por ello, las observaciones de lectura y escritura deben conservarse
como ejes diagnósticos independientes hasta que una versión futura del currículo
defina su correspondencia. El diseño no inventa habilidades ni las añade al
dominio actual.

El contrato normativo entre eje diagnóstico y currículo está en el
[`ADR 0008`](adr/0008-diagnostic-axes-and-skill-mappings.md). `axis` expresa la
capacidad principal observada; `skill_id` solo añade una correspondencia
curricular directa cuando puede defenderse y puede ser `null` sin invalidar la
evidencia diagnóstica.

## 3. Habilidades medidas

| Eje                            | Qué se observa                                                   | Texto v1                  | Hito posterior          | Límite de interpretación                                        |
| ------------------------------ | ---------------------------------------------------------------- | ------------------------- | ----------------------- | --------------------------------------------------------------- |
| Comprensión escrita            | Localizar información, relaciones y significado en textos breves | Sí                        | Textos más largos       | No prueba comprensión auditiva                                  |
| Producción escrita             | Claridad, adecuación y construcción de mensajes breves           | Sí                        | Escritura extensa       | Una muestra corta no prueba escritura sostenida                 |
| Gramática activa               | Selección y producción de formas en contexto                     | Sí                        | Producción oral         | Reconocer una regla no equivale a usarla espontáneamente        |
| Vocabulario receptivo          | Reconocer significado en contexto                                | Sí                        | Reconocimiento auditivo | No equivale a disponibilidad productiva                         |
| Vocabulario productivo         | Recuperar palabras para expresar una intención                   | Sí                        | Producción oral         | Se registra por separado del receptivo                          |
| Comprensión auditiva           | Extraer información de alemán hablado a distintas velocidades    | No                        | Audio local             | Requiere estímulos versionados y controles de reproducción      |
| Producción oral                | Formular mensajes hablados comprensibles                         | No                        | Voz local               | No se infiere de texto escrito                                  |
| Pronunciación                  | Inteligibilidad y rasgos orientativos                            | No                        | Voz local, orientativo  | Nunca se presenta como diagnóstico clínico ni acento «correcto» |
| Fluidez                        | Continuidad, pausas y capacidad de formular                      | Solo proxy escrito        | Voz local               | El tiempo escrito no representa fluidez oral                    |
| Mantener la comunicación       | Reformular, pedir aclaración y rodear una palabra desconocida    | Sí, en simulación escrita | Interacción oral        | Debe conservar la modalidad en el resultado                     |
| Confianza de estimación        | Calidad, cantidad, variedad y acuerdo de las evidencias          | Sí                        | Sí                      | Es certeza de la estimación, no confianza personal del alumno   |
| Alemán cotidiano               | Comprensión y producción en situaciones comunes                  | Sí                        | Escucha e interacción   | La familiaridad temática no sustituye competencia lingüística   |
| Alemán profesional/laboratorio | Reconocimiento y uso inicial de términos en contexto             | Sí, exploratorio          | Escenarios multimodales | No presupone dominio por la profesión de Jhon                   |

La aspiración pedagógica es multidimensional: una respuesta podría aportar
evidencia positiva de comprensión del mensaje y evidencia insuficiente de
gramática si el alumno responde en español. Texto v1, sin embargo, atribuye cada
tarea solo a su eje principal; `secondary_axes` describe dimensiones auxiliares
pero no las puntúa. Hacer operativa esa atribución múltiple requiere otra decisión
de scoring y persistencia para no duplicar evidencia.

## 4. Flujo completo

1. **Bienvenida y consentimiento.** Explicar en español que es una primera
   aproximación, que puede pausarse y que puede pedir una aclaración sin perder
   puntos.
2. **Comprobación de contexto.** Confirmar idioma de instrucciones, tiempo
   disponible y que el alumno puede escribir. Una autoestimación opcional solo
   orienta el orden; nunca aporta puntuación.
3. **Calentamiento.** Presentar una tarea alemana breve y de baja dificultad para
   reducir ansiedad y verificar que se entiende la interacción.
4. **Calibración.** Obtener anclas independientes de recepción y producción. El
   controlador comienza en dificultad 1 y solo avanza con evidencia válida.
5. **Secciones adaptativas.** Alternar recepción y producción para evitar fatiga.
   Cubrir primero los ejes textuales esenciales; profundizar únicamente cuando
   la siguiente tarea pueda reducir incertidumbre.
6. **Muestras contextualizadas.** Incluir una situación cotidiana y, si queda
   tiempo y la base general lo permite, una tarea profesional/laboratorio de
   carácter exploratorio.
7. **Revisión antes de cerrar.** Detectar ejes sin muestra válida, contradicciones
   fuertes y tareas no evaluables. Usar como máximo una tarea de desempate por
   eje.
8. **Cierre.** Guardar un resultado versionado, declarar qué no se evaluó y
   presentar fortalezas, prioridades y una propuesta de primera semana.

El alumno puede pausar entre tareas. Si abandona mientras responde, se conserva
la última respuesta enviada, pero nunca un borrador tecleado. Al volver, se
repite la instrucción de la tarea activa o se ofrece omitirla.

## 5. Máquina de estados propuesta

### Sesión

| Estado         | Significado                                      | Transiciones permitidas                        |
| -------------- | ------------------------------------------------ | ---------------------------------------------- |
| `not_started`  | Existe la sesión, sin consentimiento ni tareas   | `onboarding`, `cancelled`                      |
| `onboarding`   | Se confirma contexto y consentimiento            | `calibrating`, `paused`, `cancelled`           |
| `calibrating`  | Se obtienen las primeras anclas                  | `assessing`, `paused`, `error`                 |
| `assessing`    | El controlador selecciona y presenta tareas      | `reviewing`, `paused`, `time_limited`, `error` |
| `reviewing`    | Se comprueba cobertura y contradicciones         | `assessing`, `completing`, `paused`            |
| `paused`       | Estado persistido y reanudable                   | Estado anterior, `cancelled`                   |
| `time_limited` | Se alcanzó el límite; se cierra con lo observado | `completing`                                   |
| `completing`   | Se calcula y persiste el resultado               | `completed`, `error`                           |
| `completed`    | Resultado inmutable disponible                   | Sin transición; repetir crea otra sesión       |
| `cancelled`    | El alumno decidió no continuar                   | Sin transición; puede iniciar otra sesión      |
| `abandoned`    | Una sesión ya iniciada se cerró sin completarse  | Sin transición; puede iniciar otra sesión      |
| `error`        | Fallo recuperable o cierre seguro                | Estado estable anterior, `cancelled`           |

`starting`, `pausing` o `resuming` pueden ser estados de interfaz, pero no deben
persistirse si no añaden significado de dominio. Toda transición persistida
incluye versión del motor, instante UTC y un código de razón.

### Tarea y respuesta

Una tarea puede estar `selected`, `presented`, `answered`, `skipped`,
`not_understood`, `abandoned`, `evaluated` o `invalidated`. La respuesta se
registra de manera append-only por intento. Corregir una evaluación crea una
nueva evaluación que sustituye lógicamente a la anterior; no modifica la fila
original.

La selección siguiente solo ocurre después de persistir la respuesta y su
evaluación. Así, una interrupción no pierde progreso ni genera dos tareas
activas.

## 6. Reglas adaptativas

### Inicio y cambio de dificultad

- Cada familia comienza en dificultad 1. La autoestimación nunca salta esta
  ancla, aunque puede cambiar el orden de las secciones.
- Dos resultados válidos `correct_without_help` en tareas independientes del
  mismo nivel o niveles contiguos permiten subir un grado.
- Un resultado correcto y otro parcial mantienen el nivel y cambian el tipo de
  tarea para distinguir conocimiento inestable de un problema del formato.
- Un resultado `failure` baja un grado cuando sea posible. En dificultad 1 se
  cambia a una tarea más guiada.
- `correct_with_help` mantiene o baja la dificultad; nunca provoca una subida.
- Una respuesta no evaluable por instrucción o fallo técnico no cambia la
  estimación ni la dificultad.

La escala 1–5 es interna al diagnóstico y debe definirse por complejidad de la
tarea, apoyo disponible y demanda productiva. No es una conversión automática a
CEFR. La primera versión solo usa contenido respaldado por `a0-a1.v1`, aunque la
escala reserve niveles para crecer.

### Profundización y redundancia

- Se profundiza cuando existe una contradicción entre dos evidencias de calidad,
  cuando falta una segunda muestra independiente o cuando una tarea adicional
  puede decidir entre dos bandas contiguas.
- Se omite una tarea si mide el mismo objetivo, formato y dificultad que dos
  evidencias ya concordantes.
- No se presentan más de dos tareas consecutivas del mismo tipo ni más de tres
  cambios de dirección de dificultad en una sección.
- Una tarea de desempate es el máximo permitido por contradicción. Si persiste,
  el resultado conserva menor confianza en lugar de abrir un bucle.

### Límites y parada

| Límite                                             | Valor propuesto para texto v1                                          |
| -------------------------------------------------- | ---------------------------------------------------------------------- |
| Cobertura mínima prevista para un informe completo | 10 respuestas evaluables y cobertura de los ejes textuales principales |
| Objetivo normal                                    | 12–16 tareas evaluables                                                |
| Máximo absoluto                                    | 20 tareas presentadas                                                  |
| Mínimo por eje prioritario                         | 2 evidencias independientes o estado explícito «no evaluado»           |
| Objetivo por eje prioritario                       | 3 evidencias válidas con al menos 2 tipos de tarea                     |
| Máximo por eje                                     | 5 tareas, incluido un desempate                                        |
| Tiempo objetivo                                    | 15–25 minutos activos                                                  |
| Corte temporal                                     | Al llegar a 25 minutos activos, terminar la tarea actual y cerrar      |
| Aclaraciones                                       | 1 reformulación por tarea                                              |
| Reintentos                                         | 1, solo si la tarea lo permite y queda marcado como asistencia         |

El alumno siempre puede terminar antes; en ese caso recibe un informe parcial y
no se convierte la falta de cobertura en resultados negativos. El tiempo activo
excluye pausas y periodos de inactividad. No se debe acelerar ni penalizar a Jhon
por escribir despacio. El tiempo de respuesta es metadato débil para detectar
fatiga o problemas de interacción, no parte de la puntuación.

Una sección termina cuando alcanza su cobertura objetivo con evidencias
concordantes, llega a cinco tareas, se agota el tiempo o el alumno decide
omitirla. El diagnóstico termina cuando todos los ejes textuales prioritarios
tienen cobertura o una razón explícita de ausencia, o cuando alcanza sus límites
de tiempo/tareas. La confianza alta nunca es requisito para permitir terminar.

### Instrucciones, idioma y ausencia de respuesta

- **No entiende la instrucción:** ofrecer una reformulación en español sin
  puntuar la primera interacción. La tarea siguiente queda marcada con
  `instruction_translation` o `clarification`.
- **Responde en español a producción alemana:** preguntar una vez si entendió la
  consigna. Si entendió, puede existir evidencia de intención comunicativa, pero
  no de producción alemana correcta. Si no entendió, la muestra es no evaluable
  y se presenta una variante guiada.
- **Respuesta vacía:** la primera vez se ofrece continuar, pedir ayuda u omitir.
  Una segunda entrega vacía marca la tarea como `skipped`, sin score.
- **Abandono o cierre:** pausar después de un periodo razonable; no fabricar un
  fallo. Al reanudar, el alumno decide retomar u omitir la tarea.
- **Solicitud de ayuda:** la ayuda se registra antes de mostrarla. Una respuesta
  posterior puede ser correcta con ayuda, nunca sin ayuda.

## 7. Tipos de tarea

El conjunto inicial debe ser pequeño y versionado. Una plantilla declara los
ejes que puede medir, la dificultad, la respuesta admisible y una rúbrica o
clave verificable.

| Tipo                              | Habilidad principal                                  | Dificultad | Entrada y respuesta                                  | Evaluación                                                    | Ambigüedad y uso de LLM                                                           |
| --------------------------------- | ---------------------------------------------------- | ---------- | ---------------------------------------------------- | ------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| Elegir entre dos formas           | Gramática activa o vocabulario receptivo             | 1–2        | Frase y dos opciones; una selección                  | Clave determinista                                            | Bajo riesgo si solo hay una solución contextual                                   |
| Ordenar palabras                  | Gramática activa                                     | 1–3        | Tokens versionados; secuencia ordenada               | Conjunto explícito de órdenes válidos                         | Separables, puntuación y mayúsculas no deben crear falsos fallos; no necesita LLM |
| Completar una frase               | Gramática o vocabulario productivo                   | 1–3        | Frase con hueco; texto corto                         | Lista de variantes y normalización limitada                   | Si hay muchas soluciones válidas, LLM acotado o revisión conservadora             |
| Corregir una frase                | Gramática activa                                     | 2–4        | Frase con un error objetivo; corrección              | Cambio objetivo más variantes permitidas                      | Debe evitar frases con varios errores discutibles; normalmente determinista       |
| Respuesta personal breve          | Producción escrita y vocabulario productivo          | 1–3        | Pregunta sencilla; 1–2 frases                        | Rúbrica estructurada: comprensibilidad, objetivo y autonomía  | LLM útil pero no imprescindible; no evaluar la veracidad personal                 |
| Mensaje corto                     | Producción escrita y comunicación funcional          | 2–4        | Situación y propósito; 2–4 frases                    | Elementos comunicativos y rasgos lingüísticos separados       | Varias respuestas válidas; LLM limitado por rúbrica y salida estructurada         |
| Comprender texto breve            | Comprensión escrita y vocabulario receptivo          | 1–4        | Texto versionado y pregunta; selección o texto breve | Información explícita con claves                              | Determinista para selección; respuesta libre requiere equivalencias controladas   |
| Explicar con palabras propias     | Comprensión y reformulación escrita                  | 3–5        | Texto o idea breve; paráfrasis                       | Cobertura de ideas, no coincidencia literal                   | LLM estructurado; alto riesgo si el texto fuente es ambiguo                       |
| Describir una situación           | Producción escrita, vocabulario y gramática          | 2–5        | Escena textual o imagen futura; mensaje              | Rúbrica multidimensional                                      | No exigir detalles no presentes; LLM estructurado para texto libre                |
| Resolver una falta de vocabulario | Mantenimiento de comunicación                        | 2–4        | Intención con una palabra desconocida; reformulación | Si logra comunicar la intención y pedir/crear una alternativa | Puede evaluarse con rúbrica; siempre etiquetar modalidad escrita                  |
| Escucha futura                    | Comprensión auditiva                                 | 1–5        | Audio local, controles y respuesta                   | Clave/rúbrica por estímulo                                    | Requiere audio versionado; el LLM no controla reproducción ni dificultad          |
| Respuesta oral futura             | Producción oral, fluidez y pronunciación orientativa | 1–5        | Consigna, grabación local                            | Transcripción más rúbricas separadas                          | Requiere voz; la pronunciación debe tener baja pretensión diagnóstica             |

Ejemplos meramente ilustrativos, no banco definitivo:

- elegir `bin` o `bist` en una frase cuyo sujeto está explícito;
- localizar a qué hora abre un lugar en un aviso breve;
- escribir dos frases para presentarse;
- explicar en alemán que no recuerda una palabra y pedir que la describan;
- reconocer, de forma exploratoria, la función de `Probe` en una instrucción de
  laboratorio muy breve.

Los ejemplos profesionales no deben requerir conocimientos científicos para
resolver la lengua. Una tarea sobre laboratorio debe aportar todo el contexto
técnico necesario y medir alemán, no microbiología.

## 8. Sistema de evaluación

### Resultado categórico

El diagnóstico reutiliza conceptualmente las cuatro categorías del Learning
Engine y añade un estado diagnóstico que no se proyecta como puntuación:

| Resultado              | Interpretación                                                     | Score interno de referencia |
| ---------------------- | ------------------------------------------------------------------ | --------------------------- |
| `failure`              | No cumple el objetivo lingüístico con la evidencia disponible      | `0.0`                       |
| `partial`              | Cumple una parte relevante, pero falta un elemento necesario       | `0.4`                       |
| `correct_with_help`    | Cumple el objetivo después de ayuda registrada                     | `0.7`                       |
| `correct_without_help` | Cumple el objetivo sin ayuda ni reintento                          | `1.0`                       |
| `not_evaluable`        | No permite concluir por instrucción, técnica, omisión o ambigüedad | Sin score                   |

Los valores 0, 0.4, 0.7 y 1.0 ya tienen significado versionado en
`learning-engine-v2`; no deben reinterpretarse como «porcentaje de alemán». En
el diagnóstico sirven para compatibilidad y agregación conservadora, siempre
junto al resultado categórico y la rúbrica.

### Dimensiones de rúbrica

Una tarea libre no recibe una puntuación holística opaca. La evaluación conserva
por separado, según proceda:

- cumplimiento de la intención comunicativa;
- comprensibilidad;
- uso del objetivo gramatical o léxico;
- cobertura de información solicitada;
- autonomía y asistencia;
- idioma/modalidad de la respuesta;
- problemas de la propia tarea.

El evaluador determinista tiene prioridad cuando existe una clave suficiente.
Un LLM puede evaluar texto libre únicamente con un esquema cerrado, una rúbrica
versionada y citas breves de la respuesta como justificación. No selecciona la
habilidad, dificultad, siguiente tarea, score final, confianza agregada ni
finalización. Si LM Studio no está disponible o su salida no valida, la tarea se
pospone, usa una evaluación determinista alternativa o queda `not_evaluable`;
la sesión no se bloquea.

### Asistencia e intentos

La asistencia se registra como una lista ordenada con valores como:

- `none`;
- `instruction_translation`;
- `clarification`;
- `lexical_hint`;
- `grammar_hint`;
- `worked_example`;
- `retry`.

`instruction_translation` no implica por sí sola desconocimiento del alemán,
pero sí informa sobre cómo se presentó la evidencia. Una pista que revela parte
de la respuesta impide clasificarla como `correct_without_help`. El número de
intentos incluye entregas evaluables; clics, borradores y aclaraciones no se
convierten artificialmente en fallos.

El score y la confianza agregada expresan cosas distintas. Una coincidencia con
ayuda conserva el score categórico versionado `0.7`; en la agregación, además,
su factor de autonomía es `0.90`, frente a `1.00` sin ayuda. Para una mezcla se
usa el promedio de esos factores antes de aplicar una sola vez las penalizaciones
de contradicción e insuficiencia. Así, la evidencia sigue siendo positiva, pero
la estimación reconoce que todavía no demuestra el mismo desempeño autónomo.
La confianza del evaluador concreto no cambia: una clave determinista puede
seguir clasificando la respuesta con certeza aunque esta haya necesitado ayuda.

## 9. Evidencias y confianza

Cada evidencia diagnóstica debe conservar como mínimo:

- sesión, tarea, plantilla y versiones;
- eje y, si existe correspondencia válida, `skill_id` del currículo;
- tipo de tarea y dificultad interna;
- respuesta, instante y tiempo activo opcional;
- número de intento y asistencia recibida;
- estado de comprensión de la instrucción;
- resultado categórico y score nullable;
- dimensiones de la rúbrica;
- polaridad `positive`, `negative` o `insufficient`;
- confianza del evaluador y método de evaluación;
- justificación auditable y códigos de razón.

### Dos conceptos que no deben mezclarse

1. **Confianza del evaluador:** certeza de que una respuesta concreta fue
   interpretada correctamente. Una clave exacta puede tener confianza alta; una
   respuesta libre ambigua debe tenerla baja o ser no evaluable.
2. **Confianza de estimación:** solidez de la conclusión por eje, considerando
   número de muestras efectivas, variedad, independencia, dificultad y acuerdo.

El campo actual `StudentSkill.confidence` representa principalmente cantidad de
evidencia efectiva (`evidence_count / 10`, con tope versionado), no ninguno de
los dos conceptos anteriores. La futura implementación debe usar nombres
distintos, como `evaluator_confidence` y `estimate_confidence`, y definir de forma
explícita cualquier conversión. No debe copiar uno de esos valores directamente
a `StudentSkill.confidence`.

La confianza del evaluador usa el intervalo cerrado 0–1 como peso operativo,
no como porcentaje visible, y tiene anclas versionadas:

- `1.0`: clave determinista inequívoca validada;
- `0.8`: rúbrica estructurada con soporte claro y sin alternativa relevante;
- `0.6`: evaluación utilizable con una alternativa menor; exige otra muestra;
- menor que `0.6`: evidencia insuficiente, no participa en una proyección;
- `0.0`: no evaluable.

La interfaz muestra «confianza baja/moderada/alta», cantidad y variedad de
evidencia, no decimales de falsa precisión. Una estimación por eje se calcula
solo a partir de evidencias evaluables, ponderando de forma versionada la certeza
del evaluador, capacidad discriminatoria de la tarea y asistencia. El motor
determinista usa un factor medio de autonomía de `1.00` sin ayuda y `0.90` con
ayuda; la fórmula completa y sus pruebas se documentan en
`docs/diagnostic-engine.md`.

Como criterio de cobertura, no como fórmula definitiva: confianza baja significa
una sola muestra útil, muestras de un único formato o una contradicción abierta;
moderada requiere al menos dos muestras independientes y concordantes de dos
formatos cuando el eje lo permita; alta requiere al menos cuatro muestras, dos
formatos y comportamiento concordante en dificultades contiguas, sin desempate
pendiente. Los umbrales numéricos que materialicen estas etiquetas se versionarán
en `#006C` y no se cambiarán mediante prompts.

Una evidencia positiva apoya una capacidad a la dificultad observada; una
negativa muestra una dificultad concreta; una insuficiente solo registra que no
se pudo concluir. Ausencia de evidencia nunca equivale a evidencia negativa.

## 10. Resultado para el alumno

El informe debe abrir con una explicación breve:

> Esta es una estimación interna basada en las tareas realizadas hoy. No es un
> certificado ni un nivel CEFR oficial, y cambiará con nuevas evidencias.

Después presenta:

1. **Fortalezas observadas**, vinculadas a ejemplos de capacidad, no a elogios
   genéricos.
2. **Áreas para reforzar**, redactadas como próximos objetivos alcanzables.
3. **Ficha por eje**, con banda observada, confianza, número/tipos de muestras,
   ayudas relevantes y límites.
4. **No evaluado**, especialmente escucha, habla, pronunciación y fluidez oral en
   la versión de texto.
5. **Recomendaciones inmediatas**, limitadas a dos o tres acciones.
6. **Primera semana propuesta**, como bosquejo de cinco intenciones de estudio a
   partir de resultados aceptados, currículo y preferencias. No se persisten
   cinco `DailyPlan` por adelantado: el plan real se genera cada día con el estado
   actualizado.

Bandas internas posibles:

- `sin evidencia suficiente`;
- `base inicial observada`;
- `en desarrollo`;
- `funcional en tareas guiadas`;
- `consistente en esta muestra`.

Cuando el contenido y las evidencias lo permitan, se puede añadir una referencia
por habilidad como «contenido pre-A1 observado» o «A1 inicial en comprensión
escrita». Siempre debe nombrar la habilidad, la modalidad y la confianza. No se
promedian ejes para producir un CEFR global.

Ejemplo de tono: «Comprendiste información explícita en dos textos breves sin
ayuda. La estimación es moderada porque todavía falta comprobar textos menos
guiados». Se evita «dominas lectura al 82 %» si el número no tiene una definición
comprensible y suficiente evidencia.

## 11. Integración con Learning Engine

### Eje principal y correspondencias posibles

Cada tarea aporta evidencia a exactamente un eje principal. El selector, la
dificultad, el máximo por eje, la cobertura y la parada operan con ese eje. Los
seis ejes prioritarios son `reading_comprehension`, `written_production`,
`active_grammar`, `receptive_vocabulary`, `productive_vocabulary` y
`communication_repair.typed`.

`skill_id` no controla ese recorrido. Es opcional y solo debe usarse cuando una
respuesta aporta evidencia directa y defendible sobre la habilidad curricular.
Debe ser `null` ante una relación indirecta, un constructo sin equivalente o un
mapping que induciría una conclusión engañosa. `secondary_axes` es actualmente
metadato descriptivo: no crea evidencia, cobertura ni agregados adicionales.

Aplicado al contenido:

- Gramática activa puede asociarse a habilidades concretas como
  `grammar.sein_present`, no a una habilidad genérica inventada.
- Vocabulario general puede asociarse a `vocabulary.everyday_core` y el sondeo
  profesional a `vocabulary.laboratory_intro` cuando la tarea mide exactamente
  ese contenido.
- Lectura y escritura permanecen como ejes diagnósticos hasta que el currículo
  contenga habilidades versionadas equivalentes.
- La simulación escrita de mantenimiento de comunicación no se asocia a
  `speaking.basic`.
- Escucha, habla y pronunciación quedan sin evaluar en texto v1.

El ledger existente ya admite `SkillEvidence.source = diagnostic`, pero su
proyección actual trata una evidencia diagnóstica como cualquier intento y la
primera evidencia puede fijar directamente la estimación inicial. Por eso no se
recomienda escribir cada respuesta de forma inmediata en el ledger actual.

La implementación conserva todos los `DiagnosticResult` como `not_projected` y
no escribe `StudentSkill` ni `SkillEvidence`. Esto también se aplica cuando una
tarea tiene `skill_id`: el mapping no es una autorización de proyección. Las
tareas con `skill_id = null` forman resultados válidos por eje.

La integración futura debe seguir este orden:

1. guardar respuestas y resultados diagnósticos en su propio historial;
2. completar la sesión y comprobar cobertura/calidad;
3. convertir únicamente correspondencias inequívocas en evidencias diagnósticas
   mediante una política versionada y de influencia limitada;
4. impedir que el lote diagnóstico, por sí solo, marque una habilidad como
   dominada o desbloquee una cadena completa de prerrequisitos;
5. conservar referencias bidireccionales entre resultado y `SkillEvidence`;
6. permitir corregir una proyección con el mecanismo append-only existente.

`StudentSkill` continúa siendo una proyección reconstruible del ledger efectivo.
El diagnóstico aporta evidencia inicial, no reemplaza la práctica. Un resultado
con baja confianza debe llevar al `DailyPlan` a explorar la habilidad, no a
saltarla. Los repasos espaciados empiezan después de práctica válida; el
diagnóstico no debe programar una larga revisión como si fuera aprendizaje ya
consolidado.

El perfil puede registrar la fecha y estado del diagnóstico preferido, además de
preferencias confirmadas durante la bienvenida. El historial de sesiones enlaza
el diagnóstico como evento separado. Nunca se sobrescribe el perfil completo con
inferencias de una respuesta.

## 12. Modelo de datos propuesto

Este modelo es conceptual; no autoriza migraciones ni cambios de dominio.

### `DiagnosticSession`

- identificador y `profile_id`;
- `diagnostic_version`, `curriculum_version` y versión del motor;
- estado, sección activa y estado estable anterior;
- semilla o clave de orden reproducible;
- idioma de instrucciones y duración objetivo;
- instantes UTC de inicio, actualización, pausa y cierre;
- segundos activos, tareas presentadas/evaluables y límite máximo;
- motivo de finalización;
- modelo/proveedor opcional utilizado en evaluaciones;
- relación `repeats_session_id` o `supersedes_session_id` sin sobrescribirla.

Para reanudar de forma determinista puede conservar un pequeño estado de
selección versionado: cobertura por eje, dificultad actual, últimos tipos y
códigos de decisión. No debe contener secretos ni prompts duplicados.

### `DiagnosticTask`

- sesión, orden y estado;
- identificador y versión de plantilla;
- tipo, eje principal, ejes secundarios descriptivos y `skill_id` opcional;
- dificultad, modalidad y código de razón de selección;
- contenido presentado y opciones;
- respuesta esperada o rúbrica estructurada;
- origen `bank` o `llm` y versiones de prompt/modelo si aplica;
- instantes de selección y presentación.

El contenido generado por LLM se valida antes de mostrarse. La tarea persistida
es la fuente auditable; no se intenta regenerarla al reanudar.

### `DiagnosticResponse`

- tarea, intento y respuesta enviada;
- idioma/modalidad observada;
- estado de comprensión de la instrucción;
- asistencia ordenada;
- tiempo activo opcional e instante UTC;
- estado evaluable/no evaluable y razón;
- resultado, score nullable y rúbrica por dimensiones;
- polaridad y confianza del evaluador;
- evaluador y versiones;
- referencia append-only a una evaluación corregida.

### `DiagnosticResult`

Se propone una fila por sesión y eje o habilidad, no solo un JSON global:

- eje, modalidad y `skill_id` opcional;
- banda interna y score estimado nullable;
- confianza de estimación y etiqueta visible;
- conteo de evidencias positivas, negativas e insuficientes;
- tipos y rango de dificultad cubiertos;
- fortalezas, límites y recomendación mediante códigos más texto visible;
- estado de proyección al Learning Engine;
- referencias a las `SkillEvidence` creadas, si las hubiera;
- versión y fecha del cálculo.

Una instantánea de informe puede conservar el orden y el lenguaje mostrado, pero
las filas normalizadas son la base para auditar y recalcular.

### Datos adicionales que podrían necesitarse

- plantillas y rúbricas versionadas;
- catálogo de ejes diagnósticos independiente del currículo;
- eventos de asistencia y aclaración;
- política de retención por modalidad;
- relación explícita entre eje diagnóstico y habilidad curricular por versión;
- proyección diagnóstica por lote para no convertir cada respuesta aisladamente.

## 13. Casos límite

- **Todas las respuestas son correctas:** subir dentro del contenido disponible,
  terminar al alcanzar el máximo curricular y declarar techo de medición; no
  inferir B1/B2.
- **Todas son incorrectas:** mantener tareas accesibles, alternar formatos y
  cerrar con baja confianza y una propuesta de base; no prolongar el fracaso.
- **Resultados contradictorios:** una tarea de desempate; si persiste, conservar
  ambas evidencias y bajar confianza.
- **El alumno conoce el tema pero no el alemán:** puntuar la lengua y la intención
  por separado; el conocimiento técnico no rescata formas lingüísticas.
- **Contenido ambiguo o clave errónea:** invalidar la tarea, no al alumno, y
  excluirla de la estimación.
- **LM Studio desconectado:** continuar con tareas/evaluaciones deterministas;
  posponer o marcar no evaluables las respuestas libres que lo requieran.
- **Reinicio de aplicación:** reanudar desde la última transición persistida y no
  duplicar tareas ni evaluaciones.
- **Cambio de versión durante una pausa:** terminar con la versión original o
  pedir iniciar una sesión nueva; nunca mezclar reglas silenciosamente.
- **Alumno pide terminar pronto:** crear informe parcial con cobertura y límites
  explícitos.
- **Respuesta ofensiva o datos sensibles espontáneos:** no reutilizarlos como
  contexto; permitir borrar la sesión.
- **Entrada extremadamente larga:** aplicar límite explicado, conservar solo la
  entrega aceptada y no enviarla fuera del equipo.
- **Fallo de evaluación después de guardar respuesta:** reintentar de forma
  idempotente; la respuesta no se vuelve a presentar como nueva tarea.

## 14. Privacidad

Todo el flujo es local-first y de un solo usuario. Se guardan únicamente los
datos necesarios para reanudar, evaluar y auditar:

- tareas efectivamente presentadas;
- respuestas enviadas, no borradores ni pulsaciones;
- ayudas, tiempos activos opcionales y evaluaciones;
- resultados, versiones y relaciones con progreso;
- ajustes explícitamente confirmados por Jhon.

No deben guardarse audio ambiente, vídeo, telemetría, portapapeles, trazas de
teclado, razonamiento interno del LLM, secretos ni contenido de otras
aplicaciones. Los logs operativos no deben incluir respuestas, prompts completos
ni datos personales; deben usar identificadores y códigos de error.

Para voz futura:

- grabación explícita con indicador visible y sin escucha permanente;
- archivos locales fuera de Git y de copias genéricas no cifradas;
- política configurable y comprensible de conservación;
- opción recomendada de borrar el audio tras obtener y confirmar la evidencia,
  conservando solo transcripción/evaluación si el alumno lo acepta;
- eliminación conjunta de audio, derivados y cachés cuando se solicite.

Repetir crea una sesión nueva y mantiene las anteriores para comparar, sin
sobrescribirlas. Jhon debe poder borrar una sesión completa o sus datos crudos.
Si ya produjo `SkillEvidence`, el borrado o invalidación debe crear correcciones
auditables en el ledger antes de retirar los datos diagnósticos; nunca deja una
proyección sin origen ni modifica silenciosamente el pasado.

Una evaluación errónea no debe contaminar permanentemente el aprendizaje:
influencia inicial limitada, correcciones append-only, nueva sesión que puede
superar la anterior, confianza decreciente ante contradicciones y requisito de
evidencias posteriores independientes antes de declarar dominio.

## 15. Plan de implementación por microtareas

La primera línea de trabajo es solo texto. Audio y voz empiezan después de la
aceptación de esa versión.

| Prompt  | Alcance                                                     | Razonamiento | Consumo estimado | Dependencias                                     | Criterio de finalización                                                                                                        |
| ------- | ----------------------------------------------------------- | ------------ | ---------------- | ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------- |
| `#006B` | Entidades diagnósticas, esquemas y migración reproducible   | Alto         | Moderado         | Este diseño y decisiones de retención/proyección | Modelo append-only, restricciones 0–1, UTC, migración desde vacío y actualización probadas; sin motor ni UI                     |
| `#006C` | Máquina de estados y motor adaptativo determinista de texto | Alto–ULTRA   | Moderado–alto    | `#006B`, plantillas mínimas y bandas acordadas   | Selección, límites, pausa, reanudación, idempotencia y casos límite cubiertos; sin endpoints ni LLM                             |
| `#006D` | Endpoints y contratos API                                   | Alto         | Moderado         | `#006C`                                          | Crear/reanudar/responder/pausar/completar/leer informe con códigos HTTP e idempotencia probados                                 |
| `#006E` | Interfaz de primera clase, pausa e informe                  | Alto         | Moderado         | `#006D`                                          | Flujo accesible en español, estados reales, sin datos inventados y aceptación manual básica                                     |
| `#006F` | Generación/evaluación LLM estructurada con fallback         | Alto–ULTRA   | Moderado–alto    | Flujo determinista estable                       | Esquemas estrictos, prompts versionados, auditoría, tolerancia a LM Studio apagado y comparación con evaluaciones deterministas |
| `#006G` | Aceptación manual y ajuste conservador                      | Alto         | Moderado         | `#006B–F`                                        | Sesión real de 15–25 minutos, pausa/reanudación, informe honesto, revisión de logs/datos y defectos corregidos                  |
| `#006H` | Comprensión auditiva local                                  | Alto         | Moderado–alto    | Texto aceptado, política de audio                | Estímulos versionados, controles, evidencias auditivas separadas y funcionamiento offline                                       |
| `#006I` | Voz, fluidez y pronunciación orientativa                    | Alto–ULTRA   | Alto             | `#006H`, decisión de retención                   | Grabación explícita local, métricas prudentes, borrado probado y ninguna inferencia oral desde texto                            |

Cada prompt debe terminar en un commit aislado solo cuando sus validaciones
pasen. `#006B` no debe incluir el motor, y `#006C` no debe anticipar la UI. Esta
separación mantiene revisables las decisiones con mayor riesgo.

Antes de iniciar la interfaz de `#006E`, el banco textual debe superar el contrato
de alcanzabilidad del ADR 0008. `#006E4C` ya proporciona validación estática,
escenarios y exploración acotada del selector. El siguiente paso es corregir el
banco `draft` en `#006E4D` hasta que esa herramienta declare readiness. Mientras
tanto no se expone al alumno un flujo cuyo contenido pueda agotarse antes de la
cobertura mínima.

### Implementación de `#006C`

El motor interno implementado en `llc_api.diagnostic_engine` concreta
este diseño sin modificar la migración `0005`:

- expone comandos y recibos Pydantic que rechazan campos desconocidos;
- traduce el resultado público `incorrect` al valor persistido `failure`, sin
  cambiar el significado de las categorías existentes;
- serializa cada mutación con `BEGIN IMMEDIATE` y un único commit;
- conserva pausa, tiempo activo e idempotencia a través de reinicios;
- selecciona únicamente candidatos textuales, deterministas e inyectados;
- devuelve los ejes de audio y voz como `not_assessed`, sin persistir resultados
  artificiales;
- agrega únicamente la revisión efectiva de cada respuesta; separa las cadenas
  persistidas por `(axis, skill_id)` y crea una nueva revisión solo cuando cambia
  ese agregado;
- mantiene `communication_repair.typed` separado y nunca propaga evidencia de
  escritura a escucha u oralidad;
- no importa proveedores de modelos ni escribe en `StudentSkill` o
  `SkillEvidence`.

La implementación puede excluir pausas explícitas y el periodo durante el que la
API estuvo apagada. Hasta que la interfaz aporte eventos de actividad o pausa
automática, no puede distinguir una tarea en curso de inactividad con la misma
instancia del proceso viva; ese intervalo sigue contando y queda documentado
como límite de `#006C`.

La primera entrega y su evaluación determinista se persisten atómicamente por
la forma de `DiagnosticResponse` en `0005`. Separarlas para un evaluador
asíncrono, o convertir cada transición de estado en un ledger DB inmutable,
queda como una decisión de esquema posterior. Las reglas ejecutables y sus
límites se detallan en [Motor diagnóstico determinista](diagnostic-engine.md) y
en el ADR 0007.

### Implementación de `#006D`: contratos HTTP

FastAPI expone el motor bajo `/api/diagnostic` mediante DTO Pydantic estrictos.
El contrato disponible es:

| Acción               | Método y ruta                                              | Idempotencia                      |
| -------------------- | ---------------------------------------------------------- | --------------------------------- |
| Crear                | `POST /api/diagnostic/sessions`                            | `request_id`                      |
| Leer                 | `GET /api/diagnostic/sessions/{session_id}`                | Lectura sin efectos               |
| Iniciar              | `POST /api/diagnostic/sessions/{session_id}/start`         | `operation_id`                    |
| Pausar               | `POST /api/diagnostic/sessions/{session_id}/pause`         | `operation_id`                    |
| Reanudar             | `POST /api/diagnostic/sessions/{session_id}/resume`        | `operation_id`                    |
| Abandonar            | `POST /api/diagnostic/sessions/{session_id}/abandon`       | `operation_id`                    |
| Fallo técnico        | `POST /api/diagnostic/sessions/{session_id}/fail`          | `operation_id`                    |
| Siguiente tarea      | `POST /api/diagnostic/sessions/{session_id}/next-task`     | `operation_id`                    |
| Responder            | `POST /api/diagnostic/sessions/{session_id}/responses`     | `submission_id` y `evaluation_id` |
| Corregir             | `POST /api/diagnostic/responses/{response_id}/corrections` | `evaluation_id`                   |
| Consultar resultados | `GET /api/diagnostic/sessions/{session_id}/results`        | Lectura sin efectos               |
| Completar            | `POST /api/diagnostic/sessions/{session_id}/complete`      | `operation_id`                    |

Las repeticiones con el mismo identificador y contenido devuelven el mismo
resultado sin duplicar filas. Un identificador reutilizado con contenido
distinto devuelve `409`. Los errores conocidos se traducen a `404`, `409`,
`422` o `503`; no se exponen trazas ni mensajes internos.

La representación pública de una tarea excluye respuesta esperada, rúbrica,
reglas de scoring, razón de selección y metadatos reservados para el evaluador.
La respuesta HTTP tampoco devuelve el texto libre de Jhon ni las justificaciones
internas. El endpoint de resultados usa la agregación reconstruida en memoria y
no crea revisiones al hacer `GET`.

Las tareas cerradas v2 publican `answer_contract = option-id.v1` y opciones
`{id, label}`. La entrega usa una unión discriminada:

```json
{
  "answer": {
    "kind": "single_choice",
    "selected_option_id": "opt_k4m2"
  }
}
```

El equivalente textual v2 usa `{"kind": "text", "text": "..."}`. Las tareas
v1 continúan aceptando exclusivamente `response_text`; combinar ambos formatos
devuelve `422`. Los option IDs se comparan de forma exacta y el label nunca es
una respuesta válida v2. La respuesta persistida conserva un discriminador en
su snapshot, por lo que replay, idempotencia y correcciones no reinterpretan el
valor aunque cambie un label en una versión posterior. Este contrato no proyecta
evidencia a `StudentSkill` ni `SkillEvidence`.

El `CandidateProvider` es una dependencia. Solo las pruebas usan fixtures
deterministas pequeños. Como aún no existe un banco pedagógico de producción,
la API real devuelve `503` para todas las operaciones diagnósticas en lugar de
presentar esos fixtures como contenido auténtico. No hay integración con
LM Studio, audio, frontend ni proyección a `StudentSkill`/`SkillEvidence`.

El cargador editorial solo permite que bancos con estado explícito `production`,
checksum verificado y tareas deterministas lleguen a ese proveedor. Los estados
`draft`, `reviewed` y `deprecated` permanecen fuera del flujo del alumno.

## 16. Riesgos y decisiones pendientes

### Riesgos principales

- **Contaminación del progreso:** el ledger actual da peso completo a la primera
  evidencia. Mitigación: historial diagnóstico separado y proyección por lote,
  limitada y versionada.
- **Falsa equivalencia modal:** lectura/escritura o chat escrito podrían terminar
  etiquetados como habla. Mitigación: eje y modalidad obligatorios; no proyectar
  sin habilidad curricular equivalente.
- **Confianza mal interpretada:** existen tres conceptos cercanos. Mitigación:
  nombres separados, etiquetas visibles y reglas documentadas.
- **Dependencia del LLM:** una evaluación libre puede ser inestable o no estar
  disponible. Mitigación: núcleo determinista, salida estricta, fallback y
  `not_evaluable`.
- **Sesgo por interés o profesión:** temas familiares pueden mejorar el contexto
  sin reflejar competencia general. Mitigación: tareas profesionales exploratorias
  y cobertura general primero.
- **Techo del currículo:** `a0-a1.v1` no permite confirmar B1. Mitigación: declarar
  techo de medición y evitar extrapolaciones.
- **Fatiga:** demasiadas dimensiones en 25 minutos reducen calidad. Mitigación:
  cobertura mínima, alternancia y áreas de voz explícitamente diferidas.
- **Privacidad de texto/voz:** las respuestas pueden contener datos personales.
  Mitigación: datos locales mínimos, logs sanitizados y borrado controlado.

### Decisiones confirmadas para texto v1

1. **Proyección al progreso.** El resultado permanece separado en la primera
   entrega. Una futura proyección por lote necesitará otra decisión explícita.
2. **Eje de comunicación escrita.** Se conserva
   `communication_repair.typed` como eje diagnóstico, sin convertirlo en
   `speaking.basic`.
3. **Límites.** El objetivo es de 12–16 tareas, máximo 20 y corte a los
   25 minutos activos.
4. **Uso inicial del LLM.** Las tareas de banco y la corrección determinista son
   el núcleo; el LLM se difiere y solo podrá tratar texto libre estructurado.
5. **Audio.** Esta fase no guarda audio. Su política de retención se decidirá
   antes del hito de voz.
6. **Bandas visibles.** Se usarán descriptores internos por habilidad y
   referencias `pre-A1`/`A1` únicamente cuando el contenido y la evidencia las
   justifiquen.
7. **Eje y currículo.** `axis` es el constructo diagnóstico principal;
   `skill_id` es un mapping opcional y directo, y `secondary_axes` no genera
   evidencia en texto v1.
8. **Preparación editorial.** Un banco no puede pasar a `reviewed` hasta superar
   validación de alcanzabilidad bajo las reglas reales del selector.

La persistencia inicial no proyecta ningún resultado a `StudentSkill`.
