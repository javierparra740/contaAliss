#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Excel / CSV → Asientos contables Argentinos
NORMAS:  RT 54 (inflación) – RG 4115/2017 – Ley 27.430 – Ley 25.326
Adaptado a la cabecera AFIP "comprobantes_consulta_csv_recibidos"
"""
import os
import logging
from pathlib import Path
from typing import Tuple

import pandas as pd
from cryptography.fernet import Fernet

# ---------- CONFIGURACIÓN ----------
logging.basicConfig(
    filename="auditoria.log",
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

# Clave simétrica para cifrar CUIT (guardar en .env en producción)
KEY = os.getenv("CUIT_KEY", Fernet.generate_key())
FERNET = Fernet(KEY)

# Plan de cuentas base (puede venir de JSON/YAML)
CUENTAS = {
    "proveedores": "2.1.01",
    "iva_credito_fiscal": "1.1.04",
    "compras": "5.1.01",
    "ajuste_rt54": "6.1.01",          # Resultado por exposición a inflación
}

# Coeficiente mensual IPC (5 % anual ≈ 0.407 % mensual)
IPC_MENSUAL = 0.00407

# ---------- FUNCIONES AUXILIARES ----------
def _normalizar_numero(col: pd.Series) -> pd.Series:
    """Convierte str con separadores de miles a float."""
    return (
        col.astype(str)
        .str.replace(r"\$|\s", "", regex=True)
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
        .astype(float)
    )

def _validar_cae(cae: str) -> bool:
    return bool(cae) and cae.isdigit() and len(cae) == 14

def _extraer_neto_iva(row: pd.Series) -> Tuple[float, float]:
    """
    Devuelve (neto_gravado, iva) según la alícuota que esté cargada.
    Si hay más de una columna con valor -> se suman (facturas de varias tasas).
    """
    tasas = {
        21: ("Imp. Neto Gravado IVA 21%", "IVA 21%"),
        27: ("Imp. Neto Gravado IVA 27%", "IVA 27%"),
        10.5: ("Imp. Neto Gravado IVA 10,5%", "IVA 10,5%"),
        5: ("Imp. Neto Gravado IVA 5%", "IVA 5%"),
        2.5: ("Imp. Neto Gravado IVA 2,5%", "IVA 2,5%"),
    }
    neto_tot = 0.0
    iva_tot = 0.0
    for _, (col_base, col_iva) in tasas.items():
        base = pd.to_numeric(row[col_base], errors="coerce") or 0.0
        iva = pd.to_numeric(row[col_iva], errors="coerce") or 0.0
        neto_tot += base
        iva_tot += iva
    return neto_tot, iva_tot

def ajustar_rt54(monto: float, meses: int) -> float:
    """Ajusta por inflación según RT 54 (interés compuesto)."""
    return round(monto * ((1 + IPC_MENSUAL) ** meses), 2)

def validar_iva_deducible(tipo: str, cod_afip: int, neto: float, iva: float) -> bool:
    """
    RG 4115/2017 + Ley 27.430
    Reglas simplificadas:
      - FC A / M / B con código 5 (21 %) → 100 % deducible
      - FC C / otros → no deduce IVA
    """
    DEDUC_CODES = (5, 6, 8, 9)          # 21 %, 27 %, 10,5 %, 2,5 %
    if tipo in ("FC A", "FC M", "FC B") and cod_afip in DEDUC_CODES:
        return True
    return False

def cifrar_cuit(cuit: str) -> str:
    """Devuelve CUIT cifrado en base64 (Ley 25.326)."""
    return FERNET.encrypt(cuit.encode()).decode()

# ---------- NÚCLEO ----------
def cargar_para_asientos(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    logging.info(f"Leyendo archivo {path.name}")
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path, dtype=str , sep=";", encoding="latin-1").fillna("")
    else:
        df = pd.read_excel(path, dtype=str).fillna("")

    # ---------- MAPEO DE COLUMNAS ----------
    df.columns = df.columns.str.strip()  # por las dudas
    rename_map = {
        "Fecha de Emisión": "fecha",
        "Tipo de Comprobante": "tipo_comprobante",
        "Punto de Venta": "punto_venta",
        "Número Desde": "numero",
        "Cód. Autorización": "cae",
        "Nro. Doc. Emisor": "cuit_proveedor",
    }
    df = df.rename(columns=rename_map)

    # Crear columnas que el resto del código espera
    df[["monto_neto", "iva"]] = df.apply(
        lambda r: pd.Series(_extraer_neto_iva(r)), axis=1
    )

    # Normalizar punto de venta y número → 4 y 8 dígitos
    df["punto_venta"] = df["punto_venta"].str.zfill(4)
    df["numero"] = df["numero"].str.zfill(8)

    # cod_tributo: tomamos la tasa principal (21 → 5, 27 → 6, 10.5 → 4, etc.)
    rate_to_code = {21: 5, 27: 6, 10.5: 4, 5: 7, 2.5: 9}
    df["cod_tributo"] = 0
    for rate, code in rate_to_code.items():
        col_iva = f"IVA {rate}%".replace(".", ",")  # “IVA 21%”, “IVA 10,5%” …
        mask = pd.to_numeric(df[col_iva], errors="coerce").fillna(0) > 0
        df.loc[mask, "cod_tributo"] = code

    # Seguir igual que antes
    required = {
    "Fecha de Emisión",
    "Tipo de Comprobante",
    "Punto de Venta",
    "Número Desde",
    "Cód. Autorización",
    "Nro. Doc. Emisor",
    }
    faltan = required - set(df.columns)
    if faltan:
        raise ValueError(f"Faltan columnas: {faltan}")

    # conversiones
    df["monto_neto"] = _normalizar_numero(df["monto_neto"])
    df["iva"] = _normalizar_numero(df["iva"])
    df["cod_tributo"] = df["cod_tributo"].astype(int)
    df["fecha"] = pd.to_datetime(df["fecha"], dayfirst=True)

    # validaciones básicas
    df["cae_ok"] = df["cae"].apply(_validar_cae)
    df = df[df["cae_ok"]]
    logging.info(f"Registros tras validar CAE: {len(df)}")

    # --- RT 54: ajuste por inflación (ej. 3 meses de retraso) ---
    df["meses_rt54"] = 3
    df["neto_aj"] = df.apply(lambda r: ajustar_rt54(r["monto_neto"], r["meses_rt54"]), axis=1)
    df["iva_aj"] = df.apply(lambda r: ajustar_rt54(r["iva"], r["meses_rt54"]), axis=1)

    # --- RG 4115/2017: deducibilidad ---
    df["iva_deducible"] = df.apply(
        lambda r: r["iva_aj"] if validar_iva_deducible(r["tipo_comprobante"], r["cod_tributo"], r["neto_aj"], r["iva_aj"]) else 0.0,
        axis=1
    )
    df["iva_no_ded"] = df["iva_aj"] - df["iva_deducible"]

    # --- Ley 25.326: cifrar CUIT ---
    df["cuit_enc"] = df["cuit_proveedor"].apply(cifrar_cuit)

    # --- Generación de líneas de asiento ---
    asientos = []
    for _, row in df.iterrows():
        leyenda = f"{row['tipo_comprobante']} {row['punto_venta']}-{row['numero']} CAE {row['cae']}"
        fecha = row["fecha"].date()

        # 1) Compras (ajustadas) – DEBE
        asientos.append({
            "date": fecha, "description": leyenda,
            "account_code": CUENTAS["compras"],
            "debit": row["neto_aj"], "credit": 0.0, "currency": "ARS"
        })
        # 2) IVA crédito fiscal deducible – DEBE
        asientos.append({
            "date": fecha, "description": leyenda,
            "account_code": CUENTAS["iva_credito_fiscal"],
            "debit": row["iva_deducible"], "credit": 0.0, "currency": "ARS"
        })
        # 3) Ajuste RT 54 (solo la parte inflacionaria) – DEBE
        delta_neto = row["neto_aj"] - row["monto_neto"]
        delta_iva = row["iva_aj"] - row["iva"]
        if delta_neto:
            asientos.append({
                "date": fecha, "description": f"Ajuste RT54 {leyenda}",
                "account_code": CUENTAS["ajuste_rt54"],
                "debit": delta_neto + delta_iva, "credit": 0.0, "currency": "ARS"
            })
        # 4) Proveedores – HABER (total a pagar)
        total = row["neto_aj"] + row["iva_aj"]
        asientos.append({
            "date": fecha, "description": leyenda,
            "account_code": CUENTAS["proveedores"],
            "debit": 0.0, "credit": total, "currency": "ARS"
        })

    df_asientos = pd.DataFrame(asientos)

    # --- Validación de partida doble ---
    if not cuadrar_asiento(df_asientos):
        raise RuntimeError("El asiento no cuadra")

    # --- Auditoría en BD (opcional) ---
    #insertar_auditoria(df, df_asientos)

    logging.info(f"Asientos generados: {len(df_asientos)}")
    return df_asientos

def cuadrar_asiento(df: pd.DataFrame) -> bool:
    return df["debit"].sum() == df["credit"].sum()

# ---------- CLI RÁPIDO ----------
if __name__ == "__main__":
    import argparse, sys

    #parser = argparse.ArgumentParser(description="Genera asientos contables AFIP (RT54 / RG4115)")
    #parser.add_argument("archivo.csv", help="CSV o XLSX descargado de AFIP")
    #parser.add_argument("-o", "--output", default="archivo.csv", help="Archivo de salida (CSV)")
    #args = parser.parse_args()

    try:
        asientos = cargar_para_asientos("archivo.csv")
        asientos.to_csv(args.output, index=False, date_format="%d/%m/%Y")
        print(f"Asientos guardados en {args.output} ({len(asientos)} líneas)")
    except Exception as e:
        logging.exception("Error en la generación de asientos")
        sys.exit(str(e))