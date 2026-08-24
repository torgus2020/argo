"""
Valida el universo de suscripción de Argo contra el catálogo VIVO de Primary/BIND.

Por qué existe
--------------
El collector suscribe los 366 símbolos de `instrumento_broker_mapping` en un
único mensaje. Primary rechaza el mensaje COMPLETO si alguno de los símbolos
no existe en su catálogo — devuelve un solo ERROR con el payload entero y la
descripción del primer símbolo inválido. Resultado: un instrumento deslistado
tumba la captura de toda la rueda, en silencio, con el service en
`active (running)`.

Eso fue exactamente lo que pasó: el mapeo se generó el 2026-05-29 contra un
catálogo donde `MERV - XMEV - CRESC - CI` SÍ existía (su metadata_json trae
cficode y underlying reales). El catálogo derivó después. El universo no estaba
mal cuando se creó: envejeció.

Este script contesta una sola pregunta, en read-only:
    ¿cuáles de nuestros símbolos activos ya no existen en el catálogo de BIND?

Uso
---
    # solo diagnóstico (default, no toca nada):
    python scripts/validar_universo_vs_catalogo.py

    # además, desactiva (activo=0) los que no existen y sella fecha_validacion
    # en los que sí. Requiere tipear el flag: es la confirmación manual.
    python scripts/validar_universo_vs_catalogo.py --aplicar

Deja el informe en logs/validacion_universo_<stamp>.txt y el detalle en
data/processed/validacion_universo_<stamp>.json.

Read-only por defecto. Con --aplicar hace UPDATE sobre instrumento_broker_mapping
(nunca DELETE: un símbolo desactivado se puede reactivar; uno borrado, no).

Nota de diseño (corrección 2026-08-21)
--------------------------------------
La v1 de este script acumulaba el informe en una lista y lo escribía a disco en
la ÚLTIMA línea de main(). Cuando falló la conexión, no dejó archivo: el
diagnóstico se perdió justo en el caso en que hacía falta. Es la misma clase de
error que el incidente que este script investiga — un proceso que solo deja
señal cuando le va bien.

Ahora el informe se abre ANTES de cualquier trabajo y cada línea se escribe con
flush inmediato. Si el proceso muere, lo escrito hasta ahí ya está en disco.
Toda excepción, prevista o no, se vuelca con traceback completo al mismo archivo.

Regla general del proyecto: un script de diagnóstico escribe mientras avanza,
nunca al final.
"""

import argparse
import json
import sqlite3
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

# Convención del proyecto: inyectar la raíz al sys.path antes de importar src.
RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ_PROYECTO))

import pyRofex  # noqa: E402
from src.collectors.primary_conexion import (  # noqa: E402
    conectar_primary_produccion,
    ErrorConexionPrimary,
)

RUTA_DB = RAIZ_PROYECTO / "data" / "argo.sqlite"
RUTA_SECRETS = RAIZ_PROYECTO / "config" / "secrets.json"
DIR_LOGS = RAIZ_PROYECTO / "logs"
DIR_PROCESSED = RAIZ_PROYECTO / "data" / "processed"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


class Informe:
    """
    Escritor de informe con volcado inmediato a disco.

    La diferencia con acumular en memoria y escribir al final no es de estilo:
    es la diferencia entre tener diagnóstico cuando algo revienta y no tenerlo.
    Cada línea va a pantalla Y al archivo, con flush, para que un proceso que
    muere a mitad de camino igual deje su rastro.
    """

    def __init__(self, ruta: Path):
        self.ruta = ruta
        ruta.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(ruta, "w", encoding="utf-8")

    def p(self, txt="") -> None:
        linea = str(txt)
        print(linea)
        self._f.write(linea + "\n")
        self._f.flush()

    def cerrar(self) -> None:
        try:
            self._f.close()
        except Exception:  # noqa: BLE001
            pass


def revisar_estructura_secrets(inf: Informe) -> bool:
    """
    Pre-flight sobre secrets.json: confirma que parsea y que el bloque de
    producción tiene las claves requeridas.

    NUNCA imprime valores — solo nombres de bloque y de clave, y para el bloque
    de producción, la longitud de cada valor. Con eso alcanza para distinguir
    "falta la clave" de "la clave está vacía" sin que una credencial toque
    jamás el log ni la pantalla.

    Existe porque el fallo más probable acá es un JSON roto al editar a mano,
    y el error de json.JSONDecodeError trae línea y columna: sirve de sobra
    para arreglarlo y no expone nada.
    """
    inf.p("")
    inf.p("[0] Pre-flight de secrets.json (sin exponer valores)")

    if not RUTA_SECRETS.exists():
        inf.p(f"    FALLA: no existe {RUTA_SECRETS}")
        return False

    try:
        with open(RUTA_SECRETS, encoding="utf-8") as f:
            secrets = json.load(f)
    except json.JSONDecodeError as e:
        inf.p("    FALLA: secrets.json NO es JSON válido.")
        inf.p(f"           {e}")
        inf.p("           (típico de una edición manual: coma de más al final")
        inf.p("            de un bloque, coma faltante entre bloques, o comillas")
        inf.p("            curvas pegadas desde otro editor)")
        return False
    except Exception as e:  # noqa: BLE001
        inf.p(f"    FALLA leyendo secrets.json: {type(e).__name__}: {e}")
        return False

    if not isinstance(secrets, dict):
        inf.p(f"    FALLA: la raíz de secrets.json es {type(secrets).__name__}, no un objeto.")
        return False

    inf.p(f"    JSON válido. Bloques en la raíz ({len(secrets)}): "
          f"{', '.join(sorted(secrets.keys()))}")

    bloque = secrets.get("primary_produccion")
    if bloque is None and isinstance(secrets.get("brokers"), dict):
        bloque = secrets["brokers"].get("primary_produccion")
        if bloque is not None:
            inf.p("    NOTA: 'primary_produccion' está anidado bajo 'brokers'.")

    if bloque is None:
        inf.p("    FALLA: no existe el bloque 'primary_produccion'.")
        return False
    if not isinstance(bloque, dict):
        inf.p(f"    FALLA: 'primary_produccion' es {type(bloque).__name__}, no un objeto.")
        return False

    requeridas = ("user", "password", "account", "endpoint")
    inf.p("    Bloque 'primary_produccion' — claves requeridas:")
    ok = True
    for clave in requeridas:
        if clave not in bloque:
            inf.p(f"      {clave:<10} AUSENTE")
            ok = False
            continue
        valor = str(bloque[clave])
        largo = len(valor.strip())
        if largo == 0:
            inf.p(f"      {clave:<10} presente pero VACÍA")
            ok = False
        elif clave == "account":
            # 'account' no es secreto: es un número de comitente, y ver cuál es
            # resuelve de una el hallazgo del default apuntando a la cuenta
            # equivocada. Las otras tres nunca se muestran.
            inf.p(f"      {clave:<10} OK  valor={valor.strip()}")
        else:
            inf.p(f"      {clave:<10} OK  ({largo} caracteres)")

    extra = sorted(set(bloque.keys()) - set(requeridas))
    if extra:
        inf.p(f"    Claves adicionales del bloque: {', '.join(extra)}")

    return ok


def obtener_catalogo_primary(inf: Informe) -> set:
    """
    Devuelve el conjunto de símbolos que Primary/BIND reconoce hoy.

    Se prueba primero get_all_instruments (liviano). Si no devuelve nada
    utilizable, se cae a get_detailed_instruments. La estructura de la
    respuesta cambia entre versiones de pyRofex, así que se parsea
    defensivamente en vez de asumir una forma.
    """
    candidatos = []
    for nombre in ("get_all_instruments", "get_detailed_instruments"):
        fn = getattr(pyRofex, nombre, None)
        if fn is None:
            inf.p(f"    {nombre}() no existe en esta versión de pyRofex.")
            continue
        try:
            resp = fn()
        except Exception as e:  # noqa: BLE001
            inf.p(f"    {nombre}() lanzó excepción: {type(e).__name__}: {e}")
            continue
        if not isinstance(resp, dict) or resp.get("status") != "OK":
            inf.p(f"    {nombre}() sin status OK: {str(resp)[:300]}")
            continue
        lista = resp.get("instruments") or []
        simbolos = set()
        for item in lista:
            # Forma habitual: {"instrumentId": {"marketId": "...", "symbol": "..."}}
            iid = item.get("instrumentId") if isinstance(item, dict) else None
            if isinstance(iid, dict) and iid.get("symbol"):
                simbolos.add(iid["symbol"])
            elif isinstance(item, dict) and item.get("symbol"):
                simbolos.add(item["symbol"])
        if simbolos:
            inf.p(f"    {nombre}() devolvió {len(simbolos)} símbolos.")
            candidatos.append(simbolos)
            break
        inf.p(f"    {nombre}() devolvió status OK pero 0 símbolos parseables.")

    if not candidatos:
        raise ErrorConexionPrimary(
            "Ningún endpoint de catálogo devolvió símbolos utilizables. "
            "No se puede validar: abortando sin tocar nada."
        )
    return candidatos[0]


def leer_universo_activo() -> list:
    """Lee los símbolos activos de Primary desde instrumento_broker_mapping."""
    if not RUTA_DB.exists():
        raise SystemExit(f"No existe la base en {RUTA_DB}")
    con = sqlite3.connect(RUTA_DB)
    con.row_factory = sqlite3.Row
    filas = con.execute(
        """
        SELECT m.id, m.symbol_externo, m.plazo, m.moneda_liquidacion,
               m.instrumento_id, i.ticker
          FROM instrumento_broker_mapping m
          LEFT JOIN instrumentos i ON i.id = m.instrumento_id
         WHERE m.broker = 'primary' AND m.activo = 1
         ORDER BY m.symbol_externo
        """
    ).fetchall()
    con.close()
    return [dict(f) for f in filas]


def aplicar_cambios(ids_faltantes: list, ids_ok: list) -> None:
    """
    Desactiva los símbolos inexistentes y sella fecha_validacion en los válidos.
    NUNCA borra: activo=0 es reversible, DELETE no.
    """
    ahora = datetime.now(timezone.utc)
    con = sqlite3.connect(RUTA_DB)
    try:
        if ids_faltantes:
            con.executemany(
                "UPDATE instrumento_broker_mapping "
                "   SET activo = 0, updated_at = ? "
                " WHERE id = ?",
                [(ahora, i) for i in ids_faltantes],
            )
        if ids_ok:
            con.executemany(
                "UPDATE instrumento_broker_mapping "
                "   SET fecha_validacion = ?, updated_at = ? "
                " WHERE id = ?",
                [(ahora, ahora, i) for i in ids_ok],
            )
        con.commit()
    finally:
        con.close()


def main(inf: Informe, args: argparse.Namespace, stamp: str) -> int:
    inf.p("=" * 78)
    inf.p("VALIDACION UNIVERSO ARGO vs CATALOGO PRIMARY/BIND")
    inf.p(f"UTC   : {datetime.now(timezone.utc).isoformat()}")
    inf.p(f"Base  : {RUTA_DB}")
    inf.p(f"Modo  : {'APLICAR CAMBIOS' if args.aplicar else 'solo diagnostico (read-only)'}")
    inf.p("=" * 78)

    # Pre-flight: si secrets está roto, decirlo con claridad acá y no dejar que
    # el error salga disfrazado de "problema de conexión" tres pasos más abajo.
    if not revisar_estructura_secrets(inf):
        inf.p("")
        inf.p("ABORTADO en el pre-flight: hay que arreglar secrets.json antes de seguir.")
        inf.p("No se tocó la base ni se intentó conectar.")
        return 4

    inf.p("")
    inf.p("[1] Universo activo en la base")
    universo = leer_universo_activo()
    inf.p(f"    {len(universo)} símbolos activos (broker='primary').")

    inf.p("")
    inf.p("[2] Conectando a produccion BIND (read-only)")
    info = conectar_primary_produccion()
    inf.p(f"    cuenta autenticada: {info['cuenta']}  |  host: {info['host_rest']}")
    if str(info["cuenta"]) != "17169":
        inf.p("    AVISO: la cuenta autenticada NO es la piloto (17169).")
        inf.p("           Para un collector es inocuo (solo escucha), pero el default")
        inf.p("           de secrets.json apunta a una cuenta que NO debe operarse.")

    inf.p("")
    inf.p("[3] Pidiendo catalogo vivo")
    catalogo = obtener_catalogo_primary(inf)
    inf.p(f"    catálogo: {len(catalogo)} símbolos.")

    inf.p("")
    inf.p("[4] Cruce")
    faltantes = [u for u in universo if u["symbol_externo"] not in catalogo]
    presentes = [u for u in universo if u["symbol_externo"] in catalogo]
    inf.p(f"    existen en el catálogo : {len(presentes)}")
    inf.p(f"    NO existen             : {len(faltantes)}")

    if faltantes:
        inf.p("")
        inf.p("    --- símbolos a dar de baja ---")
        for f in faltantes:
            inf.p(f"    id={f['id']:<5} {f['symbol_externo']:<34} "
                  f"ticker={f['ticker']}  {f['moneda_liquidacion']}")
        # Tickers base afectados: sirve para ver si se cayó un instrumento entero
        # o solo una variante (D/C).
        bases = sorted({(f["ticker"] or "?") for f in faltantes})
        inf.p("")
        inf.p(f"    tickers base afectados ({len(bases)}): {', '.join(bases)}")
    else:
        inf.p("    Todo el universo activo existe en el catálogo. Nada que hacer.")

    # Detalle a JSON para trazabilidad.
    DIR_PROCESSED.mkdir(parents=True, exist_ok=True)
    ruta_json = DIR_PROCESSED / f"validacion_universo_{stamp}.json"
    with open(ruta_json, "w", encoding="utf-8") as f:
        json.dump(
            {
                "generado_utc": datetime.now(timezone.utc).isoformat(),
                "cuenta_autenticada": info["cuenta"],
                "total_activos": len(universo),
                "total_catalogo": len(catalogo),
                "faltantes": faltantes,
                "aplicado": bool(args.aplicar),
            },
            f,
            indent=2,
            ensure_ascii=False,
            default=str,
        )
        f.write("\n")

    if args.aplicar and faltantes:
        inf.p("")
        inf.p("[5] Aplicando: activo=0 a los faltantes, fecha_validacion a los válidos")
        aplicar_cambios([f["id"] for f in faltantes], [x["id"] for x in presentes])
        inf.p(f"    {len(faltantes)} desactivados, {len(presentes)} sellados.")
        inf.p(f"    Universo activo resultante: {len(presentes)} símbolos.")
        inf.p("    Reiniciar el collector para que tome el universo nuevo:")
        inf.p("        sudo systemctl restart argo-collector")
    elif faltantes:
        inf.p("")
        inf.p("[5] NO se aplicó nada (falta el flag --aplicar).")

    inf.p("")
    inf.p(f"Detalle JSON: {ruta_json}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--aplicar",
        action="store_true",
        help="Desactiva (activo=0) los símbolos inexistentes y sella "
             "fecha_validacion en los válidos. Sin este flag no toca nada.",
    )
    _args = ap.parse_args()

    _stamp_corrida = _stamp()
    _ruta_log = DIR_LOGS / f"validacion_universo_{_stamp_corrida}.txt"
    _inf = Informe(_ruta_log)

    try:
        _codigo = main(_inf, _args, _stamp_corrida)
    except ErrorConexionPrimary as e:
        _inf.p("")
        _inf.p("!! ERROR DE CONEXION CONTRA PRIMARY/BIND")
        _inf.p(f"   {e}")
        _inf.p("")
        _inf.p("   Traceback completo:")
        _inf.p(traceback.format_exc())
        _codigo = 2
    except SystemExit as e:
        # leer_universo_activo() usa SystemExit cuando falta la base.
        _inf.p("")
        _inf.p(f"!! ABORTADO: {e}")
        _codigo = int(e.code) if isinstance(e.code, int) else 1
    except Exception:  # noqa: BLE001
        _inf.p("")
        _inf.p("!! EXCEPCION NO PREVISTA")
        _inf.p(traceback.format_exc())
        _codigo = 3
    finally:
        _inf.p("")
        _inf.p(f"Informe: {_ruta_log}")
        _inf.cerrar()

    sys.exit(_codigo)
