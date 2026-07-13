# Operaciones locales

## Principios

Los scripts calculan la raíz a partir de su propia ubicación y cambian a ella
antes de trabajar. No dependen del directorio desde el que se invoquen ni
contienen la ruta absoluta del SSD. Las rutas SQLite relativas se interpretan
respecto a la raíz del proyecto.

Un entorno virtual de Python sí contiene rutas generadas por Python. Si cambia
el nombre o punto de montaje del SSD, recrea `.venv` y vuelve a instalar las
dependencias; no copies el entorno virtual entre rutas.

## Diagnóstico

```bash
./scripts/doctor.sh
```

El diagnóstico comprueba:

- disponibilidad y espacio del volumen;
- escritura real en `data`;
- Node.js, npm, Python 3.12 y `.venv`;
- dependencias npm y Python;
- integridad SQLite y revisión Alembic;
- instalación, servidor e inventario de modelos de Ollama;
- ocupación de los puertos 3000 y 8000.

Un símbolo `✗` produce código de salida 1. Los símbolos `!` son avisos: por
ejemplo, Ollama apagado o una migración que `dev.sh` puede aplicar. El script
legado `check-environment.sh` es solo un wrapper y se conserva temporalmente.

## Desarrollo

```bash
./scripts/dev.sh
```

El script no instala dependencias y no inicia Ollama. Valida primero los
requisitos y que ambos puertos estén libres. Si SQLite ya existe, ejecuta
`quick_check`. Antes de una migración pendiente crea un bundle consistente de
solo la base con la etiqueta `pre-migration`; después ejecuta Alembic y verifica
que la revisión final coincida con el único `head`.

FastAPI y Next.js escuchan solo en `127.0.0.1`. La salida de ambos queda en
primer plano para no ocultar fallos. Cuando uno termina o se pulsa `Ctrl-C`, el
script envía una señal de cierre a los dos y espera sus procesos descendientes.

La configuración pública de la web se lee de `.env` como datos, sin ejecutar el
archivo como shell. Esto permite mantener un único `.env` en la raíz aunque
Next.js se ejecute desde su workspace.

## Launcher silencioso de macOS

`./scripts/create-macos-launcher.sh` compila `dist/DeutschOS.app` con la utilidad
nativa `osacompile`. AppleScript usa `do shell script`, por lo que no abre una
ventana de Terminal. La aplicación comprueba la ruta del SSD, ejecuta
`scripts/start.sh` y presenta una alerta de macOS si el arranque falla.

`start.sh` mantiene logs separados en `logs/` y PID files en `run/`. Un bloqueo
atómico evita dos arranques simultáneos. Cada PID file incluye el PID y la hora
de inicio del proceso; antes de reutilizarlo o detenerlo también se comprueba el
comando esperado. Si un servicio DeutschOS válido ya responde sin PID file, se
considera externo: se reutiliza, pero el launcher no adquiere su propiedad.
Un puerto ocupado por una respuesta que no corresponde al servicio esperado se
trata como error y nunca se mata ese proceso.

Ollama se inicia únicamente cuando no responde ya en loopback, con:

```bash
OLLAMA_MODELS=/Volumes/Juegos/DeutschOS/Ollama/models
OLLAMA_HOST=127.0.0.1:11434
OLLAMA_NO_CLOUD=1
OLLAMA_NOHISTORY=1
```

El launcher no descarga modelos y exige al menos un manifiesto local. Para la
preparación de la base reutiliza `dev.sh --prepare-only`, que realiza las mismas
comprobaciones y migraciones seguras sin iniciar servidores. FastAPI y Next.js
se ejecutan sin `--reload` de Python y sin telemetría, con salida persistida en
`logs/api.log` y `logs/web.log`.

Comandos operativos:

```bash
./scripts/start.sh
./scripts/status.sh
./scripts/stop.sh
```

`stop.sh` envía primero SIGTERM y reserva SIGKILL para el último recurso. Solo
actúa sobre procesos cuyo PID file, hora de inicio y comando coinciden; limpia
archivos obsoletos sin señalar procesos ajenos. `status.sh` devuelve cero cuando
Ollama, API y web responden y no hay PID files huérfanos. Los artefactos
`dist/`, `logs/`, `run/` y el almacén `Ollama/` son locales y están ignorados por
Git. No hay LaunchAgent, permisos de administrador ni inicio al iniciar sesión.

## Backup

```bash
./scripts/backup.sh
./scripts/backup.sh "/ruta/local/con espacios"
```

La precedencia del destino es: argumento, `DEUTSCHOS_BACKUP_DIR` y
`./backups`. Una ruta relativa se resuelve desde el proyecto, no desde la shell
del usuario. Dentro del repositorio solo se admite `./backups`, que está
ignorado por Git; cualquier otro destino debe quedar fuera del repositorio. El
destino tampoco puede estar dentro de `data`.

La copia se construye en un directorio parcial con permisos restrictivos y se
renombra al final. SQLite usa su API de backup en lugar de copiar el archivo en
caliente; se ejecuta `quick_check` antes y después. El bundle contiene:

- `database/`: snapshot SQLite coherente;
- `configuration/`: copia saneada de las opciones locales no secretas de `.env`;
- `data/`: futuros archivos pedagógicos regulares;
- `manifest.json`: fecha UTC, revisión Alembic, tamaños y SHA-256.

No se siguen enlaces simbólicos. Se excluyen otras bases no gestionadas,
temporales, cachés, directorios de Ollama/Hugging Face y extensiones habituales
de pesos (`.gguf`, `.safetensors`, `.onnx`, entre otras). Los modelos deben
recuperarse desde su instalación local de Ollama, no desde estos bundles.
Las claves `.env` que no estén en la lista pública del script se omiten. Las
URLs públicas con credenciales embebidas o que no apunten a loopback también se
rechazan. Sus nombres, nunca sus valores, quedan en
`excluded_configuration_keys`. Archivos arbitrarios de `config/local` tampoco
se copian porque podrían contener credenciales futuras.

## Restauración

La restauración permanece deliberadamente manual para evitar sobrescribir datos
por accidente:

1. Detén `dev.sh` y conserva la base actual con otro nombre o en otra unidad.
2. Revisa `manifest.json` y verifica los hashes de los archivos que restaurarás.
3. Copia el snapshot de `database/` a la ruta indicada por
   `DEUTSCHOS_DATABASE_URL` solo cuando hayas confirmado el bundle correcto.
4. Restaura la configuración saneada o archivos pedagógicos de forma selectiva.
5. Ejecuta `./scripts/doctor.sh` antes de volver a arrancar.

El script nunca aplica retención ni elimina backups antiguos.

## Downgrades destructivos

Los downgrades no forman parte del flujo normal. La revisión inicial rechaza
eliminar tablas y la revisión del Learning Engine rechaza borrar evidencia. Solo
una restauración o reset deliberado, después de verificar un backup, puede usar
temporalmente `DEUTSCHOS_ALLOW_DESTRUCTIVE_DOWNGRADE=1`. `dev.sh` nunca define
esa variable ni ejecuta downgrades.
