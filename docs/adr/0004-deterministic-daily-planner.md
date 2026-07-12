# ADR 0004: Planificador diario determinista

- Estado: aceptado
- Fecha: 2026-07-11

## Contexto

DeutschOS necesita decidir qué estudiar con información local: currículo,
prerrequisitos, estimaciones por habilidad, historial, repasos vencidos,
preferencias, motivación y minutos disponibles. Delegar esta decisión a un LLM
haría el resultado difícil de reproducir y de auditar, y dejaría la aplicación
inutilizable cuando Ollama no esté disponible.

## Decisión

El código del `learning_engine` crea el plan con reglas versionadas. El orden de
prioridad es:

1. habilidades con revisión vencida, primero la más atrasada y después la de
   menor dominio/confianza;
2. una habilidad ya iniciada que todavía no cumple su criterio interno;
3. como máximo una habilidad nueva cuyos prerrequisitos estén dominados.

El planificador nunca introduce una habilidad con prerrequisitos pendientes.
Alterna el tipo de ejercicio respecto al historial reciente y prefiere
actividades de escucha y producción cuando son compatibles, en especial cuando
el perfil las prioriza. Motivación 1–2 produce intensidad `low`, 3 produce
`normal` y 4–5 produce `high`. La intensidad modifica la variedad y la
introducción de contenido, no las estimaciones de dominio.

Los bloques duran al menos cinco minutos, suman exactamente el tiempo solicitado
y el caso mínimo de diez minutos está cubierto por pruebas. Un plan conserva
objetivo, habilidad principal, revisiones, posible habilidad nueva, bloques,
motivo interno, códigos de razón y las versiones de motor y currículo. Se
persiste antes de responder; `GET /api/learning/today` solo lee el último plan
del día en la zona horaria local configurada.

## Responsabilidades

- El código selecciona habilidades, horarios, intensidad, dominio y repasos.
- El LLM podrá proponer en otro milestone el contenido textual de un bloque ya
  decidido. No puede cambiar la habilidad, la duración ni la calificación.
- La interfaz reproduce el plan de la API y no deriva una "habilidad principal"
  por su cuenta.

## Consecuencias

El mismo estado y las mismas entradas producen el mismo plan. Las razones
internas permiten explicar y probar decisiones. La estrategia es intencionalmente
sencilla: mejorarla exige una nueva versión y pruebas, no un cambio de prompt.
