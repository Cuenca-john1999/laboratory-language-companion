# ADR 0003: Abstracción de proveedor

**Estado:** aceptado.

La aplicación depende de `ModelProvider`, no de Ollama. El contrato cubre salud, modelos, chat, generación estructurada y reserva embeddings. Esto permite sustituir el runtime y centraliza validación, reparación y errores.
