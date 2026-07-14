# Roadmap

## Milestone 0 — Fundación

Perfil, persistencia migrada, dashboard real, proveedor Ollama reemplazable, chat local, estados de error, documentación y pruebas.

## Milestone 1 — Learning Engine

Núcleo funcional: currículo A0–A1 versionado, prerrequisitos, ledger de
evidencia idempotente con correcciones, dominio y repetición espaciada
deterministas, plan diario persistido y vistas de plan/habilidades. La entrega
automática de ejercicios y un diagnóstico explícito quedan para un milestone
posterior; no se simulan con datos inventados.

### Próximos pasos del diagnóstico textual

El contrato entre eje diagnóstico y habilidad curricular queda fijado por el
ADR 0008: el eje gobierna el recorrido, el mapping curricular es opcional y los
resultados no se proyectan todavía al progreso normal.

El orden de trabajo es:

1. implementar en `#006E4C` validación estática y simulación reproducible de
   alcanzabilidad;
2. reequilibrar el banco `draft` con tareas de entrada y cobertura suficiente;
3. probar sesiones completas con escenarios correctos, incorrectos, mixtos y no
   evaluables;
4. realizar una auditoría pedagógica final antes de `reviewed`;
5. iniciar el frontend para el alumno solo cuando el banco sea alcanzable.

Hasta entonces se usan la herramienta editorial, pruebas HTTP y el futuro
simulador; ningún banco `draft` se ofrece en producción.

## Milestone 2 — Memoria pedagógica

Extracción automática validada de errores, repaso adaptativo y vocabulario de laboratorio.

## Milestone 3 — Escucha y voz

Ejercicios auditivos, speech-to-text, text-to-speech y conversación de voz en vivo.

## Milestone 4 — Preparación avanzada

Simulaciones B1/B2, lenguaje profesional y clínico, recuperación de documentos.

## Milestone 5 — Portabilidad

Restauración asistida, exportaciones portables y políticas avanzadas de
retención. El backup local básico y consistente forma parte de la fundación.
