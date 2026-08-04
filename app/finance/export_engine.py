"""
CRN Finance Intelligence — Excel & PDF Export Engine
=====================================================
Generates multi-sheet Excel workbooks (.xlsx) with active filters and PDF report tables (.pdf)
for complete transaction history, account breakdowns, and daily/weekly/monthly cashflow.
"""

import sqlite3
import logging
from pathlib import Path
from datetime import datetime
import pandas as pd

logger = logging.getLogger(__name__)
from app.finance.finance_db import _DB_PATH


def generate_excel_export(output_path: str = "/tmp/CRN_Financial_Breakdown.xlsx") -> str:
    """
    Generates a multi-tab Excel workbook with AutoFilters, formatted headers, and column widths.
    """
    with sqlite3.connect(_DB_PATH) as conn:
        # 1. Executive Summary & Account Balances
        df_accounts = pd.read_sql_query("SELECT id, name, account_type, current_balance, updated_at FROM fin_accounts ORDER BY current_balance DESC", conn)
        
        # Check if extended plugin table exists
        _PLUGIN_TABLE = "fin_debts"
        has_ext = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (_PLUGIN_TABLE,)).fetchone() is not None
        if has_ext:
            df_ext = pd.read_sql_query(f"SELECT id, provider_name, service_type, total_outstanding, current_installment, due_day, tenure_unpaid, owner FROM {_PLUGIN_TABLE} ORDER BY total_outstanding DESC", conn)
        else:
            df_ext = pd.DataFrame()

        # 2. Complete Transaction Ledger
        df_tx = pd.read_sql_query("""
            SELECT id, timestamp, amount, account_name, payment_method, merchant, category, ingestion_source, external_id
            FROM fin_transactions 
            ORDER BY id DESC
        """, conn)

    # Convert timestamp to date/time format
    if not df_tx.empty:
        df_tx['datetime'] = pd.to_datetime(df_tx['timestamp'], format='mixed', utc=True)
        df_tx['date'] = df_tx['datetime'].dt.strftime('%Y-%m-%d')
        df_tx['time'] = df_tx['datetime'].dt.strftime('%H:%M:%S')
        df_tx['day_of_week'] = df_tx['datetime'].dt.day_name()
        df_tx['month_year'] = df_tx['datetime'].dt.strftime('%Y-%m')
        
        # Reorder columns
        df_tx_formatted = df_tx[['id', 'date', 'time', 'day_of_week', 'amount', 'account_name', 'payment_method', 'merchant', 'category', 'ingestion_source']].copy()
        df_tx_formatted.columns = ['Tx ID', 'Date', 'Time', 'Day', 'Amount (IDR)', 'Account', 'Payment Method', 'Merchant / Note', 'Category', 'Source']
    else:
        df_tx_formatted = pd.DataFrame()

    # 3. Monthly & Daily Aggregations
    if not df_tx.empty:
        df_daily = df_tx.groupby('date')['amount'].agg(
            Total_Inflow=lambda x: x[x > 0].sum(),
            Total_Outflow=lambda x: x[x < 0].sum(),
            Net_Cashflow='sum',
            Tx_Count='count'
        ).reset_index()
        df_daily.columns = ['Date', 'Inflow (IDR)', 'Outflow (IDR)', 'Net Cashflow (IDR)', 'Tx Count']
        df_daily = df_daily.sort_values(by='Date', ascending=False)
    else:
        df_daily = pd.DataFrame()

    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    with pd.ExcelWriter(out_file, engine='openpyxl') as writer:
        df_tx_formatted.to_excel(writer, sheet_name='Transaction Ledger', index=False)
        df_accounts.to_excel(writer, sheet_name='Account Balances', index=False)
        if not df_ext.empty:
            df_ext.to_excel(writer, sheet_name='Obligations', index=False)
        df_daily.to_excel(writer, sheet_name='Daily Summary', index=False)

        # Style with openpyxl
        wb = writer.book
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            ws.views.sheetView[0].showGridLines = True
            
            # Enable AutoFilter on header row
            if ws.max_row > 1:
                ws.auto_filter.ref = ws.dimensions

            # Auto-fit column widths
            for col in ws.columns:
                max_len = max(len(str(cell.value or '')) for cell in col)
                col_letter = col[0].column_letter
                ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

    logger.info("Excel financial breakdown generated successfully at %s", out_file)
    return str(out_file)


def generate_pdf_export(output_path: str = "/tmp/CRN_Financial_Report.pdf", owner_name: str = "System User") -> str:
    """
    Generates a structured PDF report document containing executive summary tables,
    account breakdowns, active obligations (if plugin present), and transaction logs using ReportLab.
    """
    from reportlab.lib.pagesizes import letter, A4, landscape
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors

    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    # Fetch Data
    with sqlite3.connect(_DB_PATH) as conn:
        df_accounts = pd.read_sql_query("SELECT name, account_type, current_balance FROM fin_accounts ORDER BY current_balance DESC", conn)
        
        _PLUGIN_TABLE = "fin_debts"
        has_ext = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (_PLUGIN_TABLE,)).fetchone() is not None
        if has_ext:
            df_ext = pd.read_sql_query(f"SELECT provider_name, total_outstanding, current_installment, due_day FROM {_PLUGIN_TABLE} ORDER BY total_outstanding DESC", conn)
        else:
            df_ext = pd.DataFrame()

        df_tx = pd.read_sql_query("SELECT id, timestamp, amount, account_name, merchant, category FROM fin_transactions ORDER BY id DESC LIMIT 60", conn)

    doc = SimpleDocTemplate(str(out_file), pagesize=landscape(A4), rightMargin=20, leftMargin=20, topMargin=20, bottomMargin=20)
    styles = getSampleStyleSheet()
    story = []

    # Title Banner
    title_style = ParagraphStyle('TitleStyle', parent=styles['Heading1'], fontSize=18, textColor=colors.HexColor("#1e293b"), spaceAfter=6)
    sub_style = ParagraphStyle('SubStyle', parent=styles['Normal'], fontSize=10, textColor=colors.HexColor("#64748b"), spaceAfter=14)
    story.append(Paragraph("<b>CRN FINANCIAL AUDIT & BREAKDOWN REPORT</b>", title_style))
    story.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')} | Owner: {owner_name}", sub_style))

    # Section 1: Liquid Accounts Table
    story.append(Paragraph("<b>1. Liquid Account Balances</b>", styles['Heading2']))
    acc_data = [["Account Name", "Account Type", "Current Balance (IDR)"]]
    tot_liquid = 0.0
    for _, r in df_accounts.iterrows():
        acc_data.append([r['name'], r['account_type'], f"Rp {r['current_balance']:,.2f}"])
        tot_liquid += r['current_balance']
    acc_data.append(["TOTAL LIQUID ASSETS", "-", f"Rp {tot_liquid:,.2f}"])

    t_acc = Table(acc_data, colWidths=[200, 150, 180])
    t_acc.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#0f172a")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor("#f1f5f9")),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
    ]))
    story.append(t_acc)
    story.append(Spacer(1, 14))

    # Section 2: Active Obligations Table (Only if plugin present)
    if not df_ext.empty:
        story.append(Paragraph("<b>2. Active Obligations & Monthly Installments</b>", styles['Heading2']))
        ext_data = [["Provider / Lender", "Total Outstanding (IDR)", "Current Due (IDR)", "Due Day"]]
        tot_ext = 0.0
        for _, r in df_ext.iterrows():
            ext_data.append([r['provider_name'], f"Rp {r['total_outstanding']:,.2f}", f"Rp {r['current_installment']:,.2f}", f"Day {r['due_day']}"])
            tot_ext += r['total_outstanding']
        ext_data.append(["TOTAL OBLIGATIONS", f"Rp {tot_ext:,.2f}", "-", "-"])

        t_ext = Table(ext_data, colWidths=[200, 180, 150, 80])
        t_ext.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#7f1d1d")),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#fca5a5")),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor("#fef2f2")),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ]))
        story.append(t_ext)
        story.append(Spacer(1, 14))

    # Section 3: Recent Transaction Ledger
    story.append(Paragraph("<b>3. Complete Transaction Ledger</b>", styles['Heading2']))
    tx_data = [["ID", "Timestamp", "Amount (IDR)", "Account", "Merchant / Note", "Category"]]
    for _, r in df_tx.iterrows():
        amt_fmt = f"+Rp {r['amount']:,.2f}" if r['amount'] > 0 else f"-Rp {abs(r['amount']):,.2f}"
        ts_short = r['timestamp'][:19].replace("T", " ")
        tx_data.append([str(r['id']), ts_short, amt_fmt, r['account_name'], str(r['merchant'])[:25], r['category']])

    t_tx = Table(tx_data, colWidths=[35, 120, 110, 140, 200, 110])
    t_tx.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#1e293b")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
    ]))
    story.append(t_tx)

    doc.build(story)
    logger.info("PDF financial report generated successfully at %s", out_file)
    return str(out_file)

