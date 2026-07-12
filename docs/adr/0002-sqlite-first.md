# ADR 0002: SQLite primero

**Estado:** aceptado.

SQLite encaja con un único usuario, facilita instalación y copias de seguridad y no requiere un servicio adicional. SQLAlchemy mantiene abierta una migración futura. La base queda fuera de contenedores y todo cambio usa Alembic.
