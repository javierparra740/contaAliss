#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GUI RT-54 / RG-4115 – sin drag-and-drop (FilePicker)
Flet 0.22+
"""
import io
import logging
import sys
import threading
from pathlib import Path

import flet as ft
import pandas as pd

# ---------- back-end ---------------
from calculos_rt54_rg4115 import cargar_para_asientos
from exportar import exportar_a_excel


class LogPipe(io.StringIO):
    """Redirige logging -> ListView"""
    def __init__(self, write_callback):
        super().__init__()
        self.write_cb = write_callback

    def write(self, s: str) -> int:
        if s and s != "\n":
            self.write_cb(s.rstrip())
        return len(s)


def main(page: ft.Page):
    page.title = "Generador de asientos RT-54 / RG-4115"
    page.padding = 20
    page.window.width = 900
    page.window.height = 700

    # -------------- estado -----------------
    df_asientos = None

    # -------------- widgets ----------------
    log_list = ft.ListView(expand=True, spacing=4, padding=10)
    data_table = ft.DataTable(
        columns=[
            ft.DataColumn(ft.Text("Fecha")),
            ft.DataColumn(ft.Text("Cuenta")),
            ft.DataColumn(ft.Text("Descripción")),
            ft.DataColumn(ft.Text("Debe")),
            ft.DataColumn(ft.Text("Haber")),
        ],
        rows=[],
        expand=True,
    )
    totals_text = ft.Text(size=16, weight=ft.FontWeight.BOLD)
    pr_bar = ft.ProgressBar(width=400, visible=False)
    save_btn = ft.ElevatedButton("Guardar Excel", icon=ft.icons.SAVE_AS, visible=False)

    # FilePicker + botón
    file_picker = ft.FilePicker()

    def pick_file_result(e: ft.FilePickerResultEvent):
        if e.files:
            process_file(e.files[0].path)

    file_picker.on_result = pick_file_result
    page.overlay.append(file_picker)   # lo oculta Flet

    pick_btn = ft.ElevatedButton(
        "Examinar…",
        icon=ft.icons.FOLDER_OPEN,
        on_click=lambda _: file_picker.pick_files(
            allowed_extensions=["csv", "xls", "xlsx"]
        ),
    )

    # -------------- manejadores ------------
    def add_log(msg: str):
        log_list.controls.append(ft.Text(msg, size=13))
        page.update()

    def process_file(file_path: str):
        nonlocal df_asientos
        df_asientos = None
        data_table.rows.clear()
        save_btn.visible = False
        pr_bar.visible = True
        page.update()

        # redirigir logging
        log_handler = LogPipe(add_log)
        logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(log_handler)])

        def _heavy_work():
            try:
                add_log(f"Procesando: {Path(file_path).name}")
                df = cargar_para_asientos(file_path)
                add_log(f"Asientos generados: {len(df)}")

                # llenar tabla
                for _, row in df.iterrows():
                    data_table.rows.append(
                        ft.DataRow(
                            cells=[
                                ft.DataCell(ft.Text(str(row["date"]))),
                                ft.DataCell(ft.Text(row["account_code"])),
                                ft.DataCell(ft.Text(row["description"])),
                                ft.DataCell(ft.Text(f"${row['debit']:,.2f}" if row["debit"] else "")),
                                ft.DataCell(ft.Text(f"${row['credit']:,.2f}" if row["credit"] else "")),
                            ]
                        )
                    )
                # totales
                tot_debe = df["debit"].sum()
                tot_haber = df["credit"].sum()
                totals_text.value = (
                    f"TOTAL DEBE: ${tot_debe:,.2f}   |   TOTAL HABER: ${tot_haber:,.2f}   |   DIF: ${tot_debe-tot_haber:,.2f}"
                )
                nonlocal df_asientos
                df_asientos = df
                save_btn.visible = True

            except Exception as exc:
                add_log(f"❌ Error: {exc}")
            finally:
                pr_bar.visible = False
                page.update()

        threading.Thread(target=_heavy_work, daemon=True).start()

    def save_excel(e):
        if df_asientos is None:
            return
        try:
            file_name = exportar_a_excel(df_asientos)
            add_log(f"✅ Guardado: {file_name}")
        except Exception as exc:
            add_log(f"❌ Error al guardar Excel: {exc}")

    save_btn.on_click = save_excel

    # -------------- armado UI --------------
    page.add(
        ft.Column(
            [
                ft.Text("Generador de asientos contables – RT-54 / RG-4115", size=22, weight=ft.FontWeight.BOLD),
                pick_btn,
                pr_bar,
                ft.Row([save_btn, totals_text], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.Divider(),
                ft.Row(
                    [
                        ft.Container(content=log_list, width=300, bgcolor=ft.colors.GREY_50, border_radius=8, padding=10),
                        ft.VerticalDivider(),
                        ft.Container(content=data_table, expand=True, padding=10),
                    ],
                    expand=True,
                ),
            ],
            expand=True,
            spacing=12,
        )
    )


if __name__ == "__main__":
    ft.app(target=main)