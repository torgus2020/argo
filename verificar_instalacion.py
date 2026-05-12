"""
Script de verificación post-instalación de Argo.

Corre desde la raíz del proyecto:
    python verificar_instalacion.py

Valida:
1. Estructura de carpetas presente
2. config.json parseable
3. secrets.json existe (warning si no, pero no falla porque puede ser primera corrida)
4. Dependencias instaladas e importables
5. roadmap.json valida contra schema
6. Logger funciona

Devuelve código de salida 0 si todo OK, 1 si hay errores.
"""

import json
import sys
from pathlib import Path


# Códigos ANSI para colorear output en consola
VERDE = "\033[92m"
ROJO = "\033[91m"
AMARILLO = "\033[93m"
RESET = "\033[0m"

RAIZ = Path(__file__).resolve().parent


def imprimir_ok(mensaje: str) -> None:
    print(f"{VERDE}[OK]{RESET} {mensaje}")


def imprimir_error(mensaje: str) -> None:
    print(f"{ROJO}[ERROR]{RESET} {mensaje}")


def imprimir_warning(mensaje: str) -> None:
    print(f"{AMARILLO}[WARN]{RESET} {mensaje}")


def verificar_estructura_carpetas() -> bool:
    """Valida que todas las carpetas esperadas existan."""
    print("\n--- Verificando estructura de carpetas ---")
    carpetas_esperadas = [
        "config",
        "src",
        "src/collectors",
        "src/strategies",
        "src/backtest",
        "src/execution",
        "src/risk",
        "src/analytics",
        "src/roadmap",
        "src/utils",
        "data",
        "data/raw",
        "data/processed",
        "data/backtests",
        "logs",
        "dashboards",
        "tests",
    ]
    todo_ok = True
    for carpeta in carpetas_esperadas:
        ruta = RAIZ / carpeta
        if ruta.exists() and ruta.is_dir():
            imprimir_ok(f"Carpeta presente: {carpeta}")
        else:
            imprimir_error(f"Carpeta faltante: {carpeta}")
            todo_ok = False
    return todo_ok


def verificar_archivos_config() -> bool:
    """Valida config.json y presencia de template/real de secrets."""
    print("\n--- Verificando archivos de configuración ---")
    todo_ok = True

    config_path = RAIZ / "config" / "config.json"
    if not config_path.exists():
        imprimir_error("config/config.json no existe")
        return False

    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        claves_requeridas = ["proyecto", "version", "timezone", "paths", "logging"]
        for clave in claves_requeridas:
            if clave not in cfg:
                imprimir_error(f"config.json falta clave: {clave}")
                todo_ok = False
        if todo_ok:
            imprimir_ok(f"config.json parseable (versión {cfg.get('version')})")
    except json.JSONDecodeError as e:
        imprimir_error(f"config.json no es JSON válido: {e}")
        todo_ok = False

    template_path = RAIZ / "config" / "secrets.json.template"
    if template_path.exists():
        imprimir_ok("secrets.json.template presente")
    else:
        imprimir_error("secrets.json.template faltante")
        todo_ok = False

    secrets_path = RAIZ / "config" / "secrets.json"
    if secrets_path.exists():
        imprimir_ok("secrets.json presente (no se valida contenido por seguridad)")
    else:
        imprimir_warning(
            "secrets.json no existe. Copialo desde secrets.json.template y "
            "completalo con valores reales antes de operar."
        )

    return todo_ok


def verificar_dependencias() -> bool:
    """Intenta importar las dependencias críticas."""
    print("\n--- Verificando dependencias instaladas ---")
    dependencias = [
        "pandas",
        "numpy",
        "requests",
        "httpx",
        "sqlalchemy",
        "pydantic",
        "jsonschema",
        "pytz",
        "schedule",
    ]
    todo_ok = True
    for dep in dependencias:
        try:
            __import__(dep)
            imprimir_ok(f"Dependencia importable: {dep}")
        except ImportError as e:
            imprimir_error(f"Dependencia faltante: {dep} ({e})")
            todo_ok = False
    return todo_ok


def verificar_roadmap() -> bool:
    """Valida roadmap.json contra roadmap.schema.json."""
    print("\n--- Verificando roadmap.json contra schema ---")
    roadmap_path = RAIZ / "roadmap.json"
    schema_path = RAIZ / "roadmap.schema.json"

    if not roadmap_path.exists():
        imprimir_error("roadmap.json no existe")
        return False
    if not schema_path.exists():
        imprimir_error("roadmap.schema.json no existe")
        return False

    try:
        with open(roadmap_path, encoding="utf-8") as f:
            roadmap = json.load(f)
        with open(schema_path, encoding="utf-8") as f:
            schema = json.load(f)
    except json.JSONDecodeError as e:
        imprimir_error(f"Archivo JSON inválido: {e}")
        return False

    try:
        import jsonschema
        jsonschema.validate(instance=roadmap, schema=schema)
        imprimir_ok("roadmap.json valida contra schema")

        # Verificación adicional: dependencias entre hitos referencian hitos existentes
        ids_hitos = set()
        for fase in roadmap["fases"]:
            for hito in fase["hitos"]:
                ids_hitos.add(hito["id"])

        dependencias_huerfanas = []
        for fase in roadmap["fases"]:
            for hito in fase["hitos"]:
                for dep in hito["depende_de"]:
                    if dep not in ids_hitos:
                        dependencias_huerfanas.append(f"{hito['id']} depende de {dep} (no existe)")

        if dependencias_huerfanas:
            for d in dependencias_huerfanas:
                imprimir_error(f"Dependencia huérfana: {d}")
            return False
        imprimir_ok("Todas las dependencias entre hitos referencian hitos existentes")

        return True
    except jsonschema.ValidationError as e:
        imprimir_error(f"roadmap.json no valida contra schema: {e.message}")
        return False


def verificar_logger() -> bool:
    """Importa el logger y escribe un mensaje de prueba."""
    print("\n--- Verificando logger ---")
    try:
        sys.path.insert(0, str(RAIZ))
        from src.utils.logger import obtener_logger
        log = obtener_logger("verificacion")
        log.info("Mensaje de prueba desde verificar_instalacion.py")

        # Verificar que el archivo de log se creó
        logs_dir = RAIZ / "logs"
        archivos_log = list(logs_dir.glob("argo.log*"))
        if archivos_log:
            imprimir_ok(f"Logger funcionando. Archivos en logs/: {len(archivos_log)}")
            return True
        else:
            imprimir_error("Logger no creó archivo en logs/")
            return False
    except Exception as e:
        imprimir_error(f"Logger falló: {e}")
        return False


def main() -> int:
    print("=" * 60)
    print("Argo - Verificación de instalación")
    print("=" * 60)

    resultados = [
        verificar_estructura_carpetas(),
        verificar_archivos_config(),
        verificar_dependencias(),
        verificar_roadmap(),
        verificar_logger(),
    ]

    print("\n" + "=" * 60)
    if all(resultados):
        print(f"{VERDE}Todo OK. El proyecto está listo para avanzar a H0.2.{RESET}")
        return 0
    else:
        print(f"{ROJO}Hay errores. Revisar mensajes arriba antes de avanzar.{RESET}")
        return 1


if __name__ == "__main__":
    sys.exit(main())