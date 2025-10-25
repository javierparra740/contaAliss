# 📘 Documentación técnica – `calculos_rt54_rg4115.py`

## 1. Propósito general
Transforma un archivo de compras (CSV / Excel AFIP) en un **DataFrame de asientos contables** que cumplen:
- **RT 54** – ajuste por inflación (interés compuesto)  
- **RG 4115/2017** – deducibilidad de IVA  
- **Ley 27.430** – reglas de deducibilidad  
- **Ley 25.326** – protección de datos personales (CUIT cifrado)

---

## 2. Diagrama de flujo (resumen)

CSV/Excel ─► _transformar_datos ─► cargar_para_asientos ─► DataFrame de asientos
│                        │
▼                        ▼
normaliza montos          ajusta RT 54
│                        │
▼                        ▼
valida CAE              calcula IVA deducible
│                        │
▼                        ▼
cifra CUIT (25.326)     genera partida doble

---

## 3. Funciones públicas (API)

| Función | Entrada | Salida | Descripción breve |
|---------|---------|--------|---------------------|
| `cargar_para_asientos(path: str \| Path)` | Ruta al archivo CSV/XLS/XLSX | `pd.DataFrame` con cols: `date, description, account_code, debit, credit, currency` | Pipeline completo: lectura, transformación, ajustes, asientos. |
| `validar_iva_deducible(tipo: str, cod_afip: int, neto: float, iva: float)` | tipo="FC 1" / "NC 11", código AFIP, neto, iva | `(bool, int)` → (es_deducible, multiplicador ±1) | RG 4115/2017. |
| `ajustar_rt54(monto: float, meses: int)` | monto original, meses de atraso | `float` → monto ajustado por inflación | RT 54 (interés compuesto). |
| `cifrar_cuit(cuit: str)` | CUIT sin guiones | `str` → CUIT cifrado en base64 | Ley 25.326. |
| `cuadrar_asiento(df: pd.DataFrame)` | DataFrame de asientos | `bool` → True si `debit.sum() == credit.sum()` | Validación de partida doble. |

---

## 4. Funciones auxiliares (privadas)

| Función | Entrada | Salida | Notas |
|---------|---------|--------|-------|
| `_normalizar_numero(valor)` | `str/int/float/Series` | `float/Series` | Limpia $, puntos, comas; convierte a float. |
| `_validar_cae(cae: str)` | string | `bool` | 14 dígitos numéricos. |
| `_transformar_datos(df: pd.DataFrame)` | Raw CSV/Excel | DataFrame normalizado | Renombra, tipifica, calcula neto/iva, asigna `cod_tributo`. |

---

## 5. Esquema de columnas (intermedias)

DataFrame después de `_transformar_datos` (entrada al núcleo contable):

| Columna | Tipo | Ejemplo | Uso |
|---------|------|---------|-----|
| `tipo_comprobante` | str | "FC 1" / "NC 11" | Decide signo y deducibilidad. |
| `punto_venta` | str | "0005" | Parte del asiento. |
| `numero` | str | "00012345" | Parte del asiento. |
| `fecha` | datetime | 2025-03-15 | Fecha asiento. |
| `cuit_proveedor` | str | "30712345678" | Se cifra antes de auditoría. |
| `cae` | str | "12345678901234" | Se valida. |
| `monto_neto` | float | 1000.00 | Neto **original** (sin inflación). |
| `iva` | float | 210.00 | IVA **original**. |
| `cod_tributo` | int | 5 (21 %) | RG 4115. |
| `meses_rt54` | int | 3 | Atraso para ajuste. |
| `neto_aj` | float | 1012.23 | Neto ajustado RT 54. |
| `iva_aj` | float | 212.57 | IVA ajustado RT 54. |
| `iva_deducible` | float | 212.57 | Monto que finalmente se toma. |
| `cuit_enc` | str | `gAAAAABh...` | CUIT cifrado. |

---

## 6. Generación de asientos (partida doble)

Por cada fila transformada se crean **hasta 4 pares** de asientos:

1. **Compras**  
   - FC → **Debe** Compras (monto original)  
   - NC → **Haber** Compras (monto original)

2. **IVA crédito fiscal**  
   - FC → **Debe** IVA (monto original)  
   - NC → **Haber** IVA (monto original)

3. **Ajuste RT 54** *(solo si Δ ≠ 0)*  
   - FC → **Debe** Compras / **Haber** Ajuste RT 54  
   - NC → **Haber** Compras / **Debe** Ajuste RT 54  
   *(Δ = |ajustado| – |original|)*

4. **Proveedores** *(siempre)*  
   - FC → **Haber** Proveedores (total original)  
   - NC → **Debe** Proveedores (total original)

**Salida final**: DataFrame con columnas  
`date, description, account_code, debit, credit, currency`

---

## 7. Integración con otros módulos

| Módulo cliente | Cómo usa `calculos_rt54_rg4115` | Datos que recibe de vuelta |
|----------------|----------------------------------|-----------------------------|
| `interface_GUIv3.py` | `cargar_para_asientos(file_path)` | DataFrame listo para mostrar/exportar. |
| `exportar.py` | Recibe el DataFrame de asientos | Genera `.xlsx` o `.pdf`. |

---

## 8. Validaciones & seguridad

| Checkpoint | Regla | Acción si falla |
|------------|-------|-----------------|
| Existencia archivo | `Path(path).exists()` | `FileNotFoundError` |
| CAE | 14 dígitos numéricos | Fila descartada + log. |
| Partida doble | `abs(debe-haber) ≤ 1` | `RuntimeError` antes de retornar. |
| CUIT | Se cifra con Fernet (AES-128) | Nunca se almacena en texto plano. |

---

## 9. Logging & auditoría
- Archivo `auditoria.log` – nivel INFO.  
- Opcional: inserción en PostgreSQL (función comentada `insertar_auditoria`).

---

## 10. Demo / CLI
Al ejecutar directamente:
```bash
python calculos_rt54_rg4115.py