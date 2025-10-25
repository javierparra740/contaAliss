#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
from datetime import datetime
from pathlib import Path

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.units import inch

def exportar_a_excel(df_asientos: pd.DataFrame, archivo_salida: str | Path = None) -> str:
    """Exporta los asientos contables a un archivo Excel con formato."""
    if archivo_salida is None:
        fecha = datetime.now().strftime("%Y%m%d_%H%M%S")
        archivo_salida = f"asientos_contables_{fecha}.xlsx"
    
    # Crear un writer de Excel
    writer = pd.ExcelWriter(archivo_salida, engine='openpyxl')
    
    # Formatear las columnas de montos
    df_asientos = df_asientos.copy()
    df_asientos['debit'] = df_asientos['debit'].apply(lambda x: f"${x:,.2f}" if x != 0 else "")
    df_asientos['credit'] = df_asientos['credit'].apply(lambda x: f"${x:,.2f}" if x != 0 else "")
    
    # Renombrar columnas para mejor presentación
    columnas = {
        'date': 'Fecha',
        'account_code': 'Cuenta',
        'description': 'Descripción',
        'debit': 'Debe',
        'credit': 'Haber',
        'currency': 'Moneda'
    }
    df_asientos = df_asientos.rename(columns=columnas)
    
    # Exportar a Excel
    df_asientos.to_excel(writer, index=False, sheet_name='Asientos Contables')
    
    # Obtener la hoja activa
    worksheet = writer.sheets['Asientos Contables']
    
    # Ajustar ancho de columnas
    for idx, col in enumerate(df_asientos.columns):
        max_length = max(
            df_asientos[col].astype(str).apply(len).max(),
            len(col)
        )
        worksheet.column_dimensions[chr(65 + idx)].width = max_length + 2
    
    # Guardar el archivo
    writer.close()
    return archivo_salida

def exportar_a_pdf(df_asientos: pd.DataFrame, archivo_salida: str | Path = None) -> str:
    """Exporta los asientos contables a un archivo PDF con formato."""
    if archivo_salida is None:
        fecha = datetime.now().strftime("%Y%m%d_%H%M%S")
        archivo_salida = f"asientos_contables_{fecha}.pdf"
    
    # Crear el documento PDF
    doc = SimpleDocTemplate(
        archivo_salida,
        pagesize=landscape(letter),
        rightMargin=50,
        leftMargin=50,
        topMargin=50,
        bottomMargin=50
    )
    
    # Preparar los elementos del documento
    elements = []
    
    # Estilos
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=14,
        spaceAfter=30
    )
    
    # Título del documento
    title = Paragraph("Libro Diario - Asientos Contables", title_style)
    elements.append(title)
    elements.append(Spacer(1, 12))
    
    # Preparar los datos para la tabla
    df = df_asientos.copy()
    df['debit'] = df['debit'].apply(lambda x: f"${x:,.2f}" if x != 0 else "")
    df['credit'] = df['credit'].apply(lambda x: f"${x:,.2f}" if x != 0 else "")
    
    # Renombrar columnas
    columnas = {
        'date': 'Fecha',
        'account_code': 'Cuenta',
        'description': 'Descripción',
        'debit': 'Debe',
        'credit': 'Haber',
        'currency': 'Moneda'
    }
    df = df.rename(columns=columnas)
    
    # Crear la tabla
    data = [df.columns.tolist()] + df.values.tolist()
    table = Table(data, repeatRows=1)
    
    # Estilo de la tabla
    table.setStyle(TableStyle([
        ('FONT', (0, 0), (-1, 0), 'Helvetica-Bold', 10),
        ('FONT', (0, 1), (-1, -1), 'Helvetica', 10),
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
        ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('ALIGN', (-3, 0), (-1, -1), 'RIGHT'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    
    elements.append(table)
    
    # Generar el PDF
    doc.build(elements)
    return archivo_salida