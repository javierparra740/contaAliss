#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Excel / CSV → Asientos contables Argentinos
NORMAS:  RT 54 (inflación) – RG 4115/2017 – Ley 27.430 – Ley 25.326
Mejora: genera histórico, ajustado y ajuste por inflación por separado.
"""
import os
import json
import hashlib
import logging
import datetime as dt
from pathlib import Path
from typing import List, Tuple

import pandas as pd
from cryptography.fernet import Fernet
import shutil
# ---------- CONFIGURACIÓN ----------
logging.basicConfig(
    filename="auditoria.log",
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

KEY = os.getenv("CUIT_KEY", Fernet.generate_key())
FERNET = Fernet(KEY)

CUENTAS = {
    "proveedores": "2.1.01",
    "iva_credito_fiscal": "1.1.04",
    "compras": "5.1.01",
    "ajuste_rt54": "6.1.01",  # Resultado por exposición a inflación
}

IPC_MENSUAL = 0.00407  # 5 % anual ≈ 0.407 % mensual
OUTPUT_ROOT = Path("output")

# ---------- FUNCIONES AUXILIARES ----------
def _normalizar_numero(valor) -> float:
    if isinstance(valor, pd.Series):
        return valor.fillna("0").astype(str).apply(_normalizar_numero)
    if pd.isna(valor) or str(valor).strip() == "":
        return 0.0
    try:
        if isinstance(valor, (int, float)):
            return float(valor)
        valor_str = str(valor).replace("$", "").replace(" ", "")
        if "," in valor_str and "." in valor_str:
            valor_str = valor_str.replace(".", "").replace(",", ".")
        elif "," in valor_str:
            valor_str = valor_str.replace(",", ".")
        return float(valor_str or "0")
    except Exception as e:
        print(f"Error al convertir valor: '{valor}' {e}")
        return 0.0

# ---------- dentro de _transformar_datos ----------
def _limpiar_fecha(fecha_str: str) -> str:
    """Elimina todo lo que no sea dígito, barra o guión."""
    if pd.isna(fecha_str):
        return ""
    return str(fecha_str).strip()

def _parse_fecha(fecha_str: str):          # sin anotación o con pd.Timestamp | None
    fecha_limpia = _limpiar_fecha(fecha_str)
    if not fecha_limpia:
        return pd.NaT
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return pd.to_datetime(fecha_limpia, format=fmt, dayfirst=True)
        except ValueError:
            continue
    return pd.to_datetime(fecha_limpia, dayfirst=True, errors="coerce")

# Reemplazá la línea problemática por:

def _validar_cae(cae: str) -> bool:
    return bool(cae) and cae.isdigit() and len(cae) == 14

def ajustar_rt54(monto: float, meses: int) -> float:
    try:
        if pd.isna(monto) or float(monto) == 0:
            return 0.0
        factor = (1 + IPC_MENSUAL) ** meses
        return round(float(monto) * factor, 2)
    except Exception as e:
        print(f"Error al ajustar monto {monto}: {e}")
        return 0.0

def validar_iva_deducible(tipo: str, cod_afip: int, neto: float, iva: float) -> Tuple[bool, int]:
    DEDUC_CODES = (4, 5, 6, 8, 9)
    try:
        tipo = str(tipo).strip()
        if tipo == "FC 1" and int(cod_afip) in DEDUC_CODES:
            return True, 1
        elif tipo == "NC 11" and int(cod_afip) in DEDUC_CODES:
            return True, -1
        return False, 1
    except:
        return False, 1

def cifrar_cuit(cuit: str) -> str:
    return FERNET.encrypt(cuit.encode()).decode()

def comprobante_id(row: pd.Series) -> str:
    return f"{row['tipo_comprobante']}_{row['punto_venta']}-{row['numero']}"

# ---------- LECTURA Y TRANSFORMACIÓN ----------
def _transformar_datos(df: pd.DataFrame) -> pd.DataFrame:
    columnas_requeridas = {
        "Tipo de Comprobante": "tipo_comprobante",
        "Punto de Venta": "punto_venta",
        "Número Desde": "numero",
        "Fecha de Emisión": "fecha",
        "Nro. Doc. Emisor": "cuit_proveedor",
        "Cód. Autorización": "cae",
        "Imp. Neto Gravado Total": "imp_neto",
        "Total IVA": "iva_total",
        "Imp. Total": "imp_total",
    }
    for col in columnas_requeridas:
        if col not in df.columns:
            raise ValueError(f"Columna requerida '{col}' no encontrada en el CSV")

    df_trans = pd.DataFrame()
    df_trans["tipo_comprobante"] = df["Tipo de Comprobante"].apply(
        lambda x: "FC 1" if str(x).strip() == "1" else ("NC 11" if str(x).strip() == "11" else f"ND {x}")
    )
    df_trans["punto_venta"] = df["Punto de Venta"].astype(str)
    df_trans["numero"] = df["Número Desde"].astype(str)
    df_trans["fecha"] = df["Fecha de Emisión"].apply(_parse_fecha)
    df_trans["cuit_proveedor"] = df["Nro. Doc. Emisor"].astype(str)
    df_trans["cae"] = df["Cód. Autorización"].astype(str)

    def obtener_montos(row):
        total = _normalizar_numero(row["Imp. Total"])
        iva = _normalizar_numero(row["Total IVA"])
        if row["Tipo de Comprobante"] == "11":  # Nota de Crédito
            return pd.Series({"monto_neto": -total, "iva": 0})
        else:
            neto = _normalizar_numero(row["Imp. Neto Gravado Total"])
            if abs(neto + iva - total) > 1:
                neto = total - iva
            return pd.Series({"monto_neto": neto, "iva": iva})

    montos = df.apply(obtener_montos, axis=1)
    df_trans["monto_neto"] = montos["monto_neto"]
    df_trans["iva"] = montos["iva"]
    df_trans["cod_tributo"] = 5  # 21 % por defecto
    return df_trans

def cargar_datos(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    encodings = ["utf-8", "latin-1", "iso-8859-1", "cp1252"]
    df = None
    for enc in encodings:
        try:
            df = pd.read_csv(path, dtype=str, sep=";", encoding=enc).fillna("")
            break
        except UnicodeDecodeError:
            continue
    if df is None:
        raise ValueError("No se pudo leer el archivo con ninguna codificación")
    
    df = _transformar_datos(df)  # <-- acá se crea la columna 'cae'

    # ✅ Validar CAE **después** de transformar
    df["cae_ok"] = df["cae"].apply(_validar_cae)
    df = df[df["cae_ok"]].copy()
    logging.info(f"Registros tras validar CAE: {len(df)}")

    # ... resto del procesamiento ...
    return df

# ---------- ASIENTOS ----------
def _crear_asiento(fecha, leyenda, neto, iva, es_nc, ajuste):
    """Devuelve lista de diccionarios con líneas de asiento."""
    items = []

    if ajuste:
        # --- Partida de ajuste por inflación (siempre dos líneas) ----------
        delta_total = neto + iva
        if delta_total == 0:
            return items
        # 1) Cuenta de compras (o IVA) en el sentido que corresponda
        items.append({
            "date": fecha,
            "description": leyenda,
            "account_code": CUENTAS["compras"],
            "debit": delta_total if not es_nc else 0,
            "credit": delta_total if es_nc else 0,
            "currency": "ARS"
        })
        # 2) Contrapartida en RT 54 (sentido opuesto)
        items.append({
            "date": fecha,
            "description": leyenda,
            "account_code": CUENTAS["ajuste_rt54"],
            "debit": delta_total if es_nc else 0,
            "credit": delta_total if not es_nc else 0,
            "currency": "ARS"
        })
        return items

    # --- Partida normal (histórica o ajustada) -----------------------------
    sign = -1 if es_nc else 1
    # Compras
    items.append({
        "date": fecha,
        "description": leyenda,
        "account_code": CUENTAS["compras"],
        "debit": neto if not es_nc else 0,
        "credit": neto if es_nc else 0,
        "currency": "ARS"
    })
    # IVA
    if iva:
        items.append({
            "date": fecha,
            "description": leyenda,
            "account_code": CUENTAS["iva_credito_fiscal"],
            "debit": iva if not es_nc else 0,
            "credit": iva if es_nc else 0,
            "currency": "ARS"
        })
    # Proveedores
    total = neto + iva
    items.append({
        "date": fecha,
        "description": leyenda,
        "account_code": CUENTAS["proveedores"],
        "debit": total if es_nc else 0,
        "credit": total if not es_nc else 0,
        "currency": "ARS"
    })
    return items

def generar_asientos(df: pd.DataFrame):
    hist, ajus, delta = [], [], []
    df["meses_rt54"] = 3
    df["monto_neto"] = pd.to_numeric(df["monto_neto"])
    df["iva"] = pd.to_numeric(df["iva"])
    df["neto_aj"] = df.apply(lambda r: ajustar_rt54(r["monto_neto"], r["meses_rt54"]), axis=1)
    df["iva_aj"] = df.apply(lambda r: ajustar_rt54(r["iva"], r["meses_rt54"]), axis=1)
    df["iva_deducible"] = df.apply(
        lambda r: r["iva_aj"] * (validar_iva_deducible(r["tipo_comprobante"], r["cod_tributo"], r["neto_aj"], r["iva_aj"])[1])
        if validar_iva_deducible(r["tipo_comprobante"], r["cod_tributo"], r["neto_aj"], r["iva_aj"])[0] else 0, axis=1
    )

    for _, row in df.iterrows():
        cid = comprobante_id(row)
        leyenda = f"{cid} CAE {row['cae']}"
        fecha = row["fecha"].date()
        es_nc = row["tipo_comprobante"] == "NC 11"

        # Valores históricos
        neto_hist = abs(row["monto_neto"])
        iva_hist = abs(row["iva"])
        hist += _crear_asiento(fecha, leyenda, neto_hist, iva_hist, es_nc, ajuste=False)

        # Valores ajustados
        neto_aj = abs(row["neto_aj"])
        iva_aj = abs(row["iva_deducible"])
        ajus += _crear_asiento(fecha, leyenda, neto_aj, iva_aj, es_nc, ajuste=False)

        # Ajuste puro
        d_neto = neto_aj - neto_hist
        d_iva = iva_aj - iva_hist
        if abs(d_neto + d_iva) > 0.01:
            delta += _crear_asiento(fecha, f"Ajuste RT54 {leyenda}", d_neto, d_iva, es_nc, ajuste=True)

    df_hist = pd.DataFrame(hist)
    df_ajus = pd.DataFrame(ajus)
    df_delt = pd.DataFrame(delta)

    for nombre, df in (("histórico", df_hist), ("ajustado", df_ajus), ("ajuste", df_delt)):
        if not cuadrar_asiento(df):
            raise RuntimeError(f"El asiento {nombre} no cuadra")
    return df_hist, df_ajus, df_delt

def cuadrar_asiento(df: pd.DataFrame) -> bool:
    return abs(df["debit"].sum() - df["credit"].sum()) < 1.0

# ---------- HISTÓRICOS ----------
def guardar_raw_y_meta(csv_path: Path) -> dict:
    OUTPUT_ROOT.mkdir(exist_ok=True)
    raw_dir = OUTPUT_ROOT / "raw"
    raw_dir.mkdir(exist_ok=True)
    dest = raw_dir / f"{csv_path.stem}_{dt.date.today():%Y%m%d}.csv"
    shutil.copy(csv_path, dest)
    h = hashlib.sha256(dest.read_bytes()).hexdigest()
    return {
        "csv_orig": str(dest),
        "sha256": h,
        "ipc_mensual": IPC_MENSUAL,
        "procesado_el": dt.datetime.now().isoformat(),
    }

def exportar_tres_libros(hist: pd.DataFrame, ajus: pd.DataFrame, delta: pd.DataFrame, meta: dict):
    dia = dt.date.today().strftime("%Y-%m")
    out = OUTPUT_ROOT / dia
    out.mkdir(parents=True, exist_ok=True)
    hist.to_excel(out / "historico.xlsx", index=False)
    ajus.to_excel(out / "ajustado.xlsx", index=False)
    delta.to_excel(out / "ajuste_rt54.xlsx", index=False)
    (out / "metadata.json").write_text(json.dumps(meta, indent=2))
    return out

# ---------- MAIN ----------
if __name__ == "__main__":
    csv_path = Path("archivo.csv")
    try:
        meta = guardar_raw_y_meta(csv_path)
        df = cargar_datos(csv_path)
        hist, ajus, delta = generar_asientos(df)
        out_dir = exportar_tres_libros(hist, ajus, delta, meta)
        print("✅ Libros generados en:", out_dir.resolve())
    except Exception as e:
        logging.exception("Error en la ejecución")
        print("❌", e)