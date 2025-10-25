#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#Excel / CSV → Asientos contables Argentinos
#NORMAS:  RT 54 (inflación) – RG 4115/2017 – Ley 27.430 – Ley 25.326

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
# esta variable puede ser capturada de algun lugar 
IPC_MENSUAL = 0.00407

# ---------- FUNCIONES AUXILIARES ----------
def _normalizar_numero(valor) -> float:
    """Convierte str con separadores de miles a float."""
    if isinstance(valor, pd.Series):
        return valor.fillna("0").astype(str).apply(_normalizar_numero)
    
    if pd.isna(valor) or str(valor).strip() == "":
        return 0.0
    
    try:
        # Si ya es un número, devolverlo directamente
        if isinstance(valor, (int, float)):
            return float(valor)
            
        # Limpiar y convertir string
        valor_str = (str(valor)
            .replace("$", "")
            .replace(" ", "")
            .strip())
            
        # Si usa punto como separador de miles y coma como decimal
        if "," in valor_str and "." in valor_str:
            valor_str = valor_str.replace(".", "").replace(",", ".")
        # Si solo usa coma
        elif "," in valor_str:
            valor_str = valor_str.replace(",", ".")
            
        return float(valor_str or "0")
    except:
        print(f"Error al convertir valor: '{valor}' de tipo {type(valor)}")
        return 0.0

def _validar_cae(cae: str) -> bool:
    return bool(cae) and cae.isdigit() and len(cae) == 14

def ajustar_rt54(monto: float, meses: int) -> float:
    """Ajusta por inflación según RT 54 (interés compuesto)."""
    try:
        if pd.isna(monto) or float(monto) == 0:
            return 0.0
        factor = (1 + IPC_MENSUAL) ** meses
        return round(float(monto) * factor, 2)
    except Exception as e:
        print(f"Error al ajustar monto {monto}: {e}")
        return 0.0

def validar_iva_deducible(tipo: str, cod_afip: int, neto: float, iva: float) -> Tuple[bool, int]:
    """
    RG 4115/2017 + Ley 27.430
    Reglas simplificadas:
      - Tipo 1 (FC A) con códigos de IVA deducibles → 100 % deducible
      - Tipo 11 (NC A) con códigos de IVA deducibles → 100 % deducible (pero con signo negativo)
      - Otros tipos → no deduce IVA
    Retorna: (es_deducible, multiplicador) donde multiplicador es 1 para FC y -1 para NC
    """
    try:
        DEDUC_CODES = (4, 5, 6, 8, 9)  # 5%, 21%, 27%, 10,5%, 2,5%
        tipo = str(tipo).strip()
        
        if tipo == "FC 1" and int(cod_afip) in DEDUC_CODES:
            return True, 1
        elif tipo == "NC 11" and int(cod_afip) in DEDUC_CODES:
            return True, -1
            
        return False, 1
    except:
        return False, 1

def cifrar_cuit(cuit: str) -> str:
    """Devuelve CUIT cifrado en base64 (Ley 25.326)."""
    return FERNET.encrypt(cuit.encode()).decode()

# ---------- NÚCLEO ----------
def _transformar_datos(df: pd.DataFrame) -> pd.DataFrame:
    """Transforma los datos del CSV al formato requerido."""
    # Imprimir las columnas disponibles para diagnóstico
    print("Columnas disponibles en el CSV:")
    for col in df.columns:
        print(f"- {col}")
    
    # Mapeo de columnas
    df_trans = pd.DataFrame()
    
    # Mapeo seguro de columnas con verificación
    columnas_requeridas = {
        "Tipo de Comprobante": "tipo_comprobante",
        "Punto de Venta": "punto_venta",
        "Número Desde": "numero",
        "Fecha de Emisión": "fecha",
        "Nro. Doc. Emisor": "cuit_proveedor",
        "Cód. Autorización": "cae"
    }
    
    for col_orig, col_dest in columnas_requeridas.items():
        if col_orig not in df.columns:
            raise ValueError(f"Columna requerida '{col_orig}' no encontrada en el CSV")
    
    # Mapeo de tipo de comprobante
    def map_tipo_comprobante(x):
        x = str(x).strip()
        if x == "1":
            return "FC 1"
        elif x == "11":
            return "NC 11"
        else:
            return f"ND {x}"
            
    df_trans["tipo_comprobante"] = df["Tipo de Comprobante"].apply(map_tipo_comprobante)
    df_trans["punto_venta"] = df["Punto de Venta"].astype(str)
    df_trans["numero"] = df["Número Desde"].astype(str)
    df_trans["fecha"] = df["Fecha de Emisión"]
    df_trans["cuit_proveedor"] = df["Nro. Doc. Emisor"].astype(str)
    df_trans["cae"] = df["Cód. Autorización"].astype(str)
    
    # Calcular monto neto total e IVA según el tipo de comprobante
    def obtener_montos(row):
        total = _normalizar_numero(row["Imp. Total"])
        iva = _normalizar_numero(row["Total IVA"])
        
        if total == 0:
            return pd.Series({"monto_neto": 0, "iva": 0})
            
        if row["Tipo de Comprobante"] == "11":  # Nota de Crédito
            # Para NC usamos el importe total como neto negativo
            return pd.Series({
                "monto_neto": -total,  # NC siempre es negativa
                "iva": 0  # El IVA ya está incluido en el total para NC
            })
        else:
            # Para facturas validamos los montos
            neto = _normalizar_numero(row["Imp. Neto Gravado Total"])
            if abs(neto + iva - total) > 1:  # Si hay discrepancia mayor a $1
                # Recalculamos el neto basado en el total
                neto = total - iva
            return pd.Series({
                "monto_neto": neto,
                "iva": iva
            })
    
    montos = df.apply(obtener_montos, axis=1)
    df_trans["monto_neto"] = montos["monto_neto"]
    df_trans["iva"] = montos["iva"]
    
    print("\nMuestra de transformación de montos:")
    print("Original:")
    print(df[["Tipo de Comprobante", "Imp. Neto Gravado Total", "Total IVA", "Imp. Total"]].head())
    print("\nTransformado:")
    print(df_trans[["tipo_comprobante", "monto_neto", "iva"]].head())
    
    # Determinar código de tributo basado en el IVA más alto utilizado
    def get_cod_tributo(row):
        """Determina el código de tributo basado en el IVA más alto presente"""
        iva_total = _normalizar_numero(row["Total IVA"])
        if iva_total > 0:
            # Si hay IVA, asumimos la alícuota más común (21%)
            return 5
        return 0
    
    df_trans["cod_tributo"] = df.apply(get_cod_tributo, axis=1)
    
    return df_trans

def cargar_para_asientos(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    logging.info(f"Leyendo archivo {path.name}")
    if path.suffix.lower() == ".csv":
        # Intentar diferentes codificaciones
        encodings = ["utf-8", "latin-1", "iso-8859-1", "cp1252"]
        df = None
        last_error = None
        
        for encoding in encodings:
            try:
                df = pd.read_csv(path, dtype=str, sep=";", encoding=encoding).fillna("")
                print(f"Archivo leído correctamente con codificación: {encoding}")
                break
            except UnicodeDecodeError as e:
                last_error = e
                continue
        
        if df is None:
            raise ValueError(f"No se pudo leer el archivo con ninguna codificación. Último error: {last_error}")
    else:
        df = pd.read_excel(path, dtype=str).fillna("")

    print(f"\nPrimeras 5 filas del CSV:")
    print(df.head())
    
    # Transformar datos al formato requerido
    df = _transformar_datos(df)

    # conversiones
    df["monto_neto"] = _normalizar_numero(df["monto_neto"])
    df["iva"] = _normalizar_numero(df["iva"])
    df["cod_tributo"] = pd.to_numeric(df["cod_tributo"], errors="coerce").fillna(0).astype(int)
    df["fecha"] = pd.to_datetime(df["fecha"])

    # validaciones básicas
    df["cae_ok"] = df["cae"].apply(_validar_cae)
    df = df[df["cae_ok"]]
    logging.info(f"Registros tras validar CAE: {len(df)}")

    # --- RT 54: ajuste por inflación (ej. 3 meses de retraso) ---
    df["meses_rt54"] = 3
    
    # Convertir a números antes de ajustar
    df["monto_neto"] = pd.to_numeric(df["monto_neto"], errors="coerce").fillna(0)
    df["iva"] = pd.to_numeric(df["iva"], errors="coerce").fillna(0)
    
    print("\nMuestra de montos antes de ajuste:")
    print(df[["monto_neto", "iva"]].head())
    
    df["neto_aj"] = df.apply(lambda r: ajustar_rt54(r["monto_neto"], r["meses_rt54"]), axis=1)
    df["iva_aj"] = df.apply(lambda r: ajustar_rt54(r["iva"], r["meses_rt54"]), axis=1)
    
    print("\nMuestra de montos después de ajuste:")
    print(df[["neto_aj", "iva_aj"]].head())

    # --- RG 4115/2017: deducibilidad ---
    def calcular_iva_deducible(row):
        es_deducible, multiplicador = validar_iva_deducible(
            row["tipo_comprobante"], row["cod_tributo"], 
            row["neto_aj"], row["iva_aj"]
        )
        return row["iva_aj"] * multiplicador if es_deducible else 0.0
    
    # Las NC ya vienen con signo negativo desde obtener_montos
    
    df["iva_deducible"] = df.apply(calcular_iva_deducible, axis=1)
    df["iva_no_ded"] = df["iva_aj"] - df["iva_deducible"]
    
    print("\nMuestra de comprobantes y montos:")
    muestra = df[["tipo_comprobante", "neto_aj", "iva_aj", "iva_deducible"]].head(10)
    print(muestra.to_string())

    # --- Ley 25.326: cifrar CUIT ---
    df["cuit_enc"] = df["cuit_proveedor"].apply(cifrar_cuit)

    # --- Generación de líneas de asiento ---
    asientos = []
    for _, row in df.iterrows():
        leyenda = f"{row['tipo_comprobante']} {row['punto_venta']}-{row['numero']} CAE {row['cae']}"
        fecha = row["fecha"].date()
        es_nc = row["tipo_comprobante"] == "NC 11"
        
        # Para NC invertimos debe y haber
        neto_debe = 0.0 if es_nc else abs(row["neto_aj"])
        neto_haber = abs(row["neto_aj"]) if es_nc else 0.0
        iva_debe = 0.0 if es_nc else abs(row["iva_deducible"])
        iva_haber = abs(row["iva_deducible"]) if es_nc else 0.0
        
        # 1) Compras (por el valor original, no ajustado)
        # Para NC, va al crédito de compras
        # Para FC, va al débito de compras
        asientos.append({
            "date": fecha, "description": leyenda,
            "account_code": CUENTAS["compras"],
            "debit": abs(row["monto_neto"]) if not es_nc else 0,
            "credit": abs(row["monto_neto"]) if es_nc else 0,
            "currency": "ARS"
        })
        
        # 2) IVA crédito fiscal deducible (por el valor original)
        if row["iva"] != 0:  # Usamos el IVA original, no el ajustado
            asientos.append({
                "date": fecha, "description": leyenda,
                "account_code": CUENTAS["iva_credito_fiscal"],
                "debit": abs(row["iva"]) if not es_nc else 0,
                "credit": abs(row["iva"]) if es_nc else 0,
                "currency": "ARS"
            })
        
        # 3) Ajuste RT 54 (solo la parte inflacionaria)
        # Calculamos el ajuste por inflación como la diferencia entre montos ajustados y originales
        # Para ambos tipos de comprobante, queremos la diferencia positiva entre ajustado y original
        delta_neto = abs(row["neto_aj"]) - abs(row["monto_neto"])
        delta_iva = abs(row["iva_aj"]) - abs(row["iva"])
        
        # El total_delta siempre será positivo, la dirección (debe/haber) se determina por el tipo de comprobante
        total_delta = abs(delta_neto + delta_iva)
        if abs(total_delta) > 0.01:  # Ignorar ajustes muy pequeños
            es_nc = row["tipo_comprobante"] == "NC 11"
            
            # El asiento espejo va a la cuenta de compras (por el ajuste)
            asientos.append({
                "date": fecha, "description": f"Ajuste RT54 {leyenda}",
                "account_code": CUENTAS["compras"],
                "debit": total_delta if not es_nc else 0,  # FC al debe
                "credit": total_delta if es_nc else 0,     # NC al haber
                "currency": "ARS"
            })
            
            # La contrapartida va a la cuenta de ajuste RT54
            asientos.append({
                "date": fecha, "description": f"Ajuste RT54 {leyenda}",
                "account_code": CUENTAS["ajuste_rt54"],
                "debit": total_delta if es_nc else 0,     # FC al haber (-)
                "credit": total_delta if not es_nc else 0, # NC al debe (+)
                "currency": "ARS"
            })
        
        # 4) Proveedores (por el valor original, no ajustado)
        # Para NC, va al débito de proveedores (reduce el pasivo)
        # Para FC, va al crédito de proveedores (aumenta el pasivo)
        total = abs(row["monto_neto"]) + abs(row["iva"])
        asientos.append({
            "date": fecha, "description": leyenda,
            "account_code": CUENTAS["proveedores"],
            "debit": total if es_nc else 0,
            "credit": total if not es_nc else 0,
            "currency": "ARS"
        })

    df_asientos = pd.DataFrame(asientos)

    # --- Validación de partida doble ---
    debe = df_asientos["debit"].sum()
    haber = df_asientos["credit"].sum()
    diferencia = debe - haber
    
    print("\nResumen de asientos:")
    print(f"Total DEBE: {debe:,.2f}")
    print(f"Total HABER: {haber:,.2f}")
    print(f"Diferencia: {diferencia:,.2f}")
    
    print("\nResumen por tipo de cuenta:")
    for cuenta in CUENTAS.values():
        asientos_cuenta = df_asientos[df_asientos["account_code"] == cuenta]
        debe_cuenta = asientos_cuenta["debit"].sum()
        haber_cuenta = asientos_cuenta["credit"].sum()
        print(f"\nCuenta {cuenta}:")
        print(f"DEBE: {debe_cuenta:,.2f}")
        print(f"HABER: {haber_cuenta:,.2f}")
        print(f"NETO: {debe_cuenta - haber_cuenta:,.2f}")
    
    if abs(diferencia) > 1.0:  # Permitir diferencias de hasta $1 por redondeos
        raise RuntimeError(f"El asiento no cuadra. Diferencia: {diferencia:,.2f}")

    # --- Auditoría en BD (opcional) ---
    #insertar_auditoria(df, df_asientos)

    logging.info(f"Asientos generados: {len(df_asientos)}")
    return df_asientos

def cuadrar_asiento(df: pd.DataFrame) -> bool:
    return df["debit"].sum() == df["credit"].sum()

#def insertar_auditoria(df_original: pd.DataFrame, df_asientos: pd.DataFrame):
#    """Ejemplo de inserción en PostgreSQL con cifrado (Ley 25.326)."""
#    import psycopg2
#    conn = None
#    try:
#        conn = psycopg2.connect(
#            dbname=os.getenv("DB_NAME", "contabilidad"),
#            user=os.getenv("DB_USER", "postgres"),
#            password=os.getenv("DB_PASS", "postgres"),
#            host=os.getenv("DB_HOST", "localhost"),
#        )
#        cur = conn.cursor()
#        for _, row in df_original.iterrows():
#            cur.execute("""
#                INSERT INTO auditoria(cuit_enc, cae, neto_original, neto_aj, iva_deducible, fecha_carga)
#               VALUES (%s, %s, %s, %s, %s, NOW())
#            """, (
#                row["cuit_enc"],
#                row["cae"],
#                row["monto_neto"],
#                row["neto_aj"],
#                row["iva_deducible"],
#            ))
#        conn.commit()
#        cur.close()
#    except Exception as e:
#        logging.exception("Error al insertar auditoría")
#    finally:
#        if conn:
#            conn.close()

# ---------- DEMO ----------
if __name__ == "__main__":
    # Usar el archivo CSV existente directamente
    archivo_csv = "archivo.csv"
    
    try:
        asientos = cargar_para_asientos(archivo_csv)
        print("\nPrimeros 10 asientos generados:")
        print(asientos.head(10))
        
        # Mostrar totales
        print("\nResumen de totales:")
        print(f"Total DEBE: {asientos['debit'].sum():,.2f}")
        print(f"Total HABER: {asientos['credit'].sum():,.2f}")
        print(f"Diferencia: {asientos['debit'].sum() - asientos['credit'].sum():,.2f}")
        
        # Exportar resultados
        try:
            from exportar import exportar_a_excel, exportar_a_pdf
            
            # Generar nombres de archivo con timestamp
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            excel_file = f"asientos_contables_{timestamp}.xlsx"
            pdf_file = f"asientos_contables_{timestamp}.pdf"
            
            print("\nExportando resultados...")
            
            # Exportar a Excel
            try:
                excel_path = exportar_a_excel(asientos, excel_file)
                print(f"✓ Archivo Excel generado: {excel_path}")
            except Exception as e:
                print(f"⚠ Error al exportar a Excel: {str(e)}")
                excel_path = None
            
            # Exportar a PDF
            try:
                pdf_path = exportar_a_pdf(asientos, pdf_file)
                print(f"✓ Archivo PDF generado: {pdf_path}")
            except Exception as e:
                print(f"⚠ Error al exportar a PDF: {str(e)}")
                pdf_path = None
                
            if excel_path or pdf_path:
                print("\nPuedes encontrar los archivos exportados en:")
                print(os.path.dirname(os.path.abspath(__file__)))
        except ImportError as e:
            print("\n⚠ No se pudieron cargar los módulos de exportación:")
            print(f"  {str(e)}")
            print("  Asegúrate de tener instalados los paquetes 'openpyxl' y 'reportlab'")
        
    except Exception as e:
        print(f"Error al procesar el archivo: {str(e)}")
        print("\nDetalles del error:")
        import traceback
        traceback.print_exc()
        logging.exception("Error en la ejecución del script")