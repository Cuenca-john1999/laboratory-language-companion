# ADR 0006: Diagnóstico adaptativo determinista y multidimensional

- Estado: propuesto
- Fecha: 2026-07-13

## Contexto

DeutschOS necesita una primera estimación útil para un alumno con exposición
irregular al alemán y capacidades posiblemente desiguales entre lectura,
escucha, escritura y habla. Una etiqueta CEFR global basada en pocas respuestas
ocultaría esas diferencias. Además, el sistema debe seguir funcionando localmente
cuando Ollama no esté disponible y debe poder explicar por qué produjo cada
conclusión.

El Learning Engine actual usa un currículo versionado A0–A1, un planificador
determinista y un ledger append-only. Sus cuatro resultados categóricos son
`failure`, `partial`, `correct_with_help` y `correct_without_help`. Su campo
`StudentSkill.confidence` aumenta con la cantidad de evidencia efectiva; no
representa la certeza de un evaluador. El currículo actual tampoco contiene
habilidades explícitas de lectura y escritura.

El diagnóstico debe durar aproximadamente 15–25 minutos, permitir pausa y
reanudación, producir evidencias auditables y evitar que una evaluación breve o
errónea declare una habilidad dominada.

## Decisión

Se diseñará un diagnóstico local, adaptativo, multidimensional y controlado por
código determinista versionado.

La primera versión será solo de texto. Medirá comprensión y producción escrita,
gramática activa, vocabulario receptivo/productivo, familiaridad cotidiana y
profesional inicial, y mantenimiento de comunicación en modalidad escrita.
Escucha, habla, pronunciación y fluidez oral quedarán explícitamente no evaluadas
hasta hitos con audio y voz.

El controlador determinista será responsable de:

- elegir eje, habilidad compatible, tipo de tarea y dificultad;
- subir, mantener o bajar dificultad con reglas acotadas;
- registrar ayuda, intentos y problemas de instrucción;
- impedir bucles y aplicar límites por eje, tareas y tiempo;
- calcular resultados y confianza de estimación;
- finalizar, persistir y decidir cualquier proyección al Learning Engine.

Un LLM podrá generar contenido dentro de una plantilla o evaluar respuestas
libres con una rúbrica y salida estructuradas. No podrá seleccionar el itinerario,
calificar directamente el progreso, modificar la confianza agregada ni finalizar
la sesión. Su ausencia no bloqueará el diagnóstico.

Las respuestas y evaluaciones se conservarán primero en entidades diagnósticas
append-only y versionadas. El resultado será por eje y modalidad, con evidencias
positivas, negativas e insuficientes. `not_evaluable` no se convertirá en score.
La confianza del evaluador de una respuesta y la confianza de la estimación de un
eje serán conceptos separados de `StudentSkill.confidence`.

No se proyectará cada respuesta inmediatamente al ledger actual. Una integración
posterior convertirá solo correspondencias curriculares inequívocas mediante una
política por lote, conservadora, auditable y de influencia limitada. El
diagnóstico por sí solo no podrá declarar dominio ni desbloquear una cadena de
prerrequisitos. Lectura, escritura y comunicación simulada permanecerán como ejes
diagnósticos mientras el currículo no defina habilidades equivalentes.

El informe usará bandas internas por habilidad, cantidad/variedad de evidencia y
confianza cualitativa. Podrá mencionar referencias de contenido `pre-A1` o `A1`
cuando estén justificadas, pero no producirá un nivel CEFR global ni certificará
competencia.

## Alternativas consideradas

### Autoevaluación o nivel global inicial

Se rechaza como fuente principal porque la exposición irregular y las diferencias
entre modalidades hacen que una sola etiqueta sea poco fiable. La autoevaluación
puede cambiar el orden de tareas, pero no aporta puntuación.

### Entrevista dirigida y evaluada libremente por un LLM

Se rechaza porque sería difícil de reproducir, auditar y usar con Ollama apagado.
También mezclaría selección, evaluación y finalización en un componente
probabilístico.

### Registrar cada respuesta directamente como `SkillEvidence`

Se rechaza porque el motor actual da influencia normal a cada evidencia y la
primera puede fijar la estimación. Además, varias dimensiones diagnósticas no
tienen aún una habilidad curricular equivalente. La proyección debe ser una
decisión explícita posterior al lote.

### Implementar texto, audio y voz al mismo tiempo

Se rechaza para la primera versión porque aumenta el tiempo, la superficie de
privacidad y el riesgo de confundir modalidades antes de validar el flujo
adaptativo. Texto ofrece una base verificable; audio y voz se añaden después con
sus propias políticas.

### Convertir todos los resultados a porcentajes visibles

Se rechaza porque un decimal basado en dos o tres muestras puede aparentar una
precisión inexistente. Los valores internos deben conservar significado
versionado; la interfaz debe explicar bandas, confianza y límites.

## Consecuencias

### Positivas

- El diagnóstico funciona offline y sus decisiones se pueden reproducir.
- Un problema de Ollama no impide terminar las partes deterministas.
- Las capacidades se observan por habilidad y modalidad, sin CEFR global
  inventado.
- Pausa, reanudación, corrección y repetición conservan un historial auditable.
- El progreso queda protegido frente a una muestra breve o una evaluación
  errónea.
- La versión de texto permite validar pedagogía y experiencia antes de almacenar
  voz.

### Costes y limitaciones

- Se necesitan entidades y una política de proyección adicionales.
- La primera versión no responde a las prioridades principales de escucha y
  habla; las declara pendientes con honestidad.
- Algunas respuestas libres quedarán sin evaluar cuando el LLM no esté disponible
  y no exista una alternativa determinista.
- No podrá estimarse contenido B1/B2 mientras el currículo y las tareas
  versionadas no lo cubran.
- Mantener ejes separados de habilidades curriculares añade complejidad, pero
  evita falsificar correspondencias.

## Condiciones para aceptar este ADR

Antes de cambiar el estado a `aceptado` deben confirmarse:

1. la política de proyección diagnóstica a `SkillEvidence`;
2. los límites de 12–16 tareas objetivo, 20 máximo y 25 minutos activos;
3. las bandas visibles y sus anclas;
4. la retención predeterminada de audio futuro;
5. que comunicación escrita no se proyecta como habla.

La especificación detallada se encuentra en
[`docs/diagnostic-system.md`](../diagnostic-system.md).
