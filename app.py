import csv
import io
import os
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import Flask, flash, redirect, render_template, request, send_file, session, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'contabilidad_web.db')}")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

APP_TITLE = "Contabilidad para alumnos"
SECRET_KEY = os.getenv("SECRET_KEY", "cambia-esta-clave-en-produccion")

CHART_OF_ACCOUNTS = {
    "100": "Capital social",
    "129": "Resultado del ejercicio",
    "170": "Deudas a largo plazo con entidades de crédito",
    "200": "Inmovilizado intangible",
    "210": "Terrenos y bienes naturales",
    "211": "Construcciones",
    "213": "Maquinaria",
    "216": "Mobiliario",
    "217": "Equipos para procesos de información",
    "300": "Mercaderías",
    "400": "Proveedores",
    "430": "Clientes",
    "472": "Hacienda Pública, IVA soportado",
    "477": "Hacienda Pública, IVA repercutido",
    "520": "Deudas a corto plazo con entidades de crédito",
    "570": "Caja",
    "572": "Bancos",
    "600": "Compras de mercaderías",
    "621": "Arrendamientos y cánones",
    "625": "Primas de seguros",
    "626": "Servicios bancarios y similares",
    "628": "Suministros",
    "629": "Otros servicios",
    "640": "Sueldos y salarios",
    "642": "Seguridad Social a cargo de la empresa",
    "700": "Ventas de mercaderías",
    "705": "Prestaciones de servicios",
    "752": "Ingresos por arrendamientos",
}
ACCOUNT_OPTIONS = [{"code": code, "name": name} for code, name in sorted(CHART_OF_ACCOUNTS.items())]


def create_app():
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = SECRET_KEY
    return app


app = create_app()
db = SQLAlchemy(app)


class Entry(db.Model):
    __tablename__ = "entries"

    id = db.Column(db.Integer, primary_key=True)
    entry_number = db.Column(db.Integer, nullable=False)
    entry_date = db.Column(db.Date, nullable=False)
    concept = db.Column(db.String(200), nullable=False)
    student_name = db.Column(db.String(120), nullable=False, default="General")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    lines = db.relationship(
        "EntryLine",
        backref="entry",
        cascade="all, delete-orphan",
        order_by="EntryLine.id",
        lazy=True,
    )


class EntryLine(db.Model):
    __tablename__ = "entry_lines"

    id = db.Column(db.Integer, primary_key=True)
    entry_id = db.Column(db.Integer, db.ForeignKey("entries.id"), nullable=False)
    account_code = db.Column(db.String(10), nullable=False)
    account_name = db.Column(db.String(150), nullable=False)
    line_concept = db.Column(db.String(200), nullable=False)
    debit = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    credit = db.Column(db.Numeric(12, 2), nullable=False, default=0)


def decimal_or_zero(value) -> Decimal:
    try:
        if value in (None, ""):
            return Decimal("0")
        return Decimal(str(value))
    except InvalidOperation:
        return Decimal("0")


def parse_amount(text: str) -> Decimal:
    cleaned = (text or "").strip().replace(",", ".")
    if not cleaned:
        return Decimal("0")
    return Decimal(cleaned)


def format_amount(value) -> str:
    return f"{Decimal(str(value)):.2f}"


def account_group(account_code: str):
    code = str(account_code).strip()
    if code and code[0] in "1234567":
        return int(code[0])
    return None


def current_student() -> str:
    student = (request.args.get("student") or session.get("student_name") or "General").strip()
    session["student_name"] = student
    return student


def next_entry_number(student_name: str) -> int:
    value = db.session.query(func.max(Entry.entry_number)).filter(Entry.student_name == student_name).scalar()
    return (value or 0) + 1


def validate_lines(form) -> tuple[list[dict], str | None]:
    account_codes = form.getlist("account_code[]")
    account_names = form.getlist("account_name[]")
    line_concepts = form.getlist("line_concept[]")
    debits = form.getlist("debit[]")
    credits = form.getlist("credit[]")

    lines = []
    for idx in range(len(account_codes)):
        code = account_codes[idx].strip()
        name = account_names[idx].strip()
        concept = line_concepts[idx].strip()
        try:
            debit = parse_amount(debits[idx])
            credit = parse_amount(credits[idx])
        except InvalidOperation:
            return [], f"Línea {idx + 1}: Debe y Haber deben ser números válidos."

        if not code and not name and not concept and debit == 0 and credit == 0:
            continue
        if not code:
            return [], f"Línea {idx + 1}: falta el código de cuenta."
        if not name:
            return [], f"Línea {idx + 1}: falta el nombre de la cuenta."
        if not concept:
            return [], f"Línea {idx + 1}: falta el concepto de la línea."
        if debit < 0 or credit < 0:
            return [], f"Línea {idx + 1}: no se admiten importes negativos."
        if debit == 0 and credit == 0:
            return [], f"Línea {idx + 1}: debes informar Debe o Haber."
        if debit > 0 and credit > 0:
            return [], f"Línea {idx + 1}: una misma línea no puede tener Debe y Haber a la vez."

        lines.append(
            {
                "account_code": code,
                "account_name": name,
                "line_concept": concept,
                "debit": debit,
                "credit": credit,
            }
        )

    if len(lines) < 2:
        return [], "El asiento debe tener al menos dos líneas."

    total_debit = sum(line["debit"] for line in lines)
    total_credit = sum(line["credit"] for line in lines)
    if total_debit != total_credit:
        return [], "El asiento no cuadra. La suma del Debe y del Haber debe ser la misma."

    return lines, None


def build_balances(student_name: str):
    balances = {}
    rows = (
        db.session.query(Entry, EntryLine)
        .join(EntryLine, Entry.id == EntryLine.entry_id)
        .filter(Entry.student_name == student_name)
        .order_by(Entry.entry_date, Entry.entry_number, EntryLine.id)
        .all()
    )

    for entry, line in rows:
        d = decimal_or_zero(line.debit)
        h = decimal_or_zero(line.credit)
        code = line.account_code
        if code not in balances:
            balances[code] = {
                "name": line.account_name,
                "group": account_group(code),
                "debit": Decimal("0"),
                "credit": Decimal("0"),
                "balance": Decimal("0"),
                "movements": [],
            }
        balances[code]["debit"] += d
        balances[code]["credit"] += h
        balances[code]["balance"] = balances[code]["debit"] - balances[code]["credit"]
        balances[code]["movements"].append(
            {
                "entry_number": entry.entry_number,
                "entry_date": entry.entry_date.strftime("%Y-%m-%d"),
                "general_concept": entry.concept,
                "line_concept": line.line_concept,
                "debit": d,
                "credit": h,
            }
        )
    return balances


def income_statement(balances):
    incomes, expenses = [], []
    total_income = Decimal("0")
    total_expense = Decimal("0")

    for code, data in sorted(balances.items()):
        group = data["group"]
        bal = data["balance"]
        if group == 7:
            amount = -bal if bal < 0 else Decimal("0")
            if amount:
                incomes.append((code, data["name"], amount))
                total_income += amount
        elif group == 6:
            amount = bal if bal > 0 else Decimal("0")
            if amount:
                expenses.append((code, data["name"], amount))
                total_expense += amount
    result = total_income - total_expense
    return incomes, expenses, total_income, total_expense, result


def balance_sheet(balances, result):
    asset, liability, equity = [], [], []
    total_asset = Decimal("0")
    total_liability = Decimal("0")
    total_equity = Decimal("0")

    for code, data in sorted(balances.items()):
        group = data["group"]
        bal = data["balance"]
        name = data["name"]

        if group in (2, 3, 4, 5):
            if bal > 0:
                asset.append((code, name, bal))
                total_asset += bal
            elif bal < 0:
                amount = -bal
                liability.append((code, name, amount))
                total_liability += amount
        elif group == 1:
            amount = -bal if bal < 0 else bal
            if amount:
                equity.append((code, name, amount))
                total_equity += amount

    if result != 0:
        equity.append(("129", "Resultado del ejercicio", result))
        total_equity += result

    return asset, liability, equity, total_asset, total_liability, total_equity


def inject_common_context():
    student = current_student()
    students = [row[0] for row in db.session.query(Entry.student_name).distinct().order_by(Entry.student_name).all()]
    if student not in students:
        students = [student] + students if student else students
    return {
        "app_title": APP_TITLE,
        "student_name": student,
        "students": students,
        "account_options": ACCOUNT_OPTIONS,
        "today": date.today().strftime("%Y-%m-%d"),
    }


@app.context_processor
def context_processor():
    return inject_common_context()


@app.route("/")
def index():
    student = current_student()
    entries = Entry.query.filter_by(student_name=student).order_by(Entry.entry_date.desc(), Entry.entry_number.desc()).all()
    total_entries = len(entries)
    total_lines = sum(len(entry.lines) for entry in entries)
    balances = build_balances(student)
    _, _, total_income, total_expense, result = income_statement(balances)
    return render_template(
        "index.html",
        entries=entries[:8],
        total_entries=total_entries,
        total_lines=total_lines,
        total_income=format_amount(total_income),
        total_expense=format_amount(total_expense),
        result=format_amount(result),
    )


@app.route("/entries")
def entries_list():
    student = current_student()
    entries = Entry.query.filter_by(student_name=student).order_by(Entry.entry_date, Entry.entry_number, Entry.id).all()
    return render_template("entries.html", entries=entries)


@app.route("/entries/new", methods=["GET", "POST"])
def new_entry():
    student = current_student()
    if request.method == "POST":
        student = (request.form.get("student_name") or student).strip() or "General"
        entry_number = request.form.get("entry_number", "").strip()
        entry_date_raw = request.form.get("entry_date", "").strip()
        concept = request.form.get("concept", "").strip()

        if not entry_number.isdigit():
            flash("El número de asiento debe ser numérico.", "danger")
            return render_template("entry_form.html", mode="new", entry=None)
        try:
            entry_date = datetime.strptime(entry_date_raw, "%Y-%m-%d").date()
        except ValueError:
            flash("La fecha debe tener formato AAAA-MM-DD.", "danger")
            return render_template("entry_form.html", mode="new", entry=None)
        if not concept:
            flash("Debes escribir el concepto general del asiento.", "danger")
            return render_template("entry_form.html", mode="new", entry=None)

        lines, error = validate_lines(request.form)
        if error:
            flash(error, "danger")
            return render_template("entry_form.html", mode="new", entry=None)

        entry = Entry(entry_number=int(entry_number), entry_date=entry_date, concept=concept, student_name=student)
        for line in lines:
            entry.lines.append(
                EntryLine(
                    account_code=line["account_code"],
                    account_name=line["account_name"],
                    line_concept=line["line_concept"],
                    debit=line["debit"],
                    credit=line["credit"],
                )
            )
        db.session.add(entry)
        db.session.commit()
        session["student_name"] = student
        flash("Asiento guardado correctamente.", "success")
        return redirect(url_for("entries_list", student=student))

    return render_template("entry_form.html", mode="new", entry=None, suggested_number=next_entry_number(student))


@app.route("/entries/<int:entry_id>/edit", methods=["GET", "POST"])
def edit_entry(entry_id):
    student = current_student()
    entry = Entry.query.get_or_404(entry_id)

    if request.method == "POST":
        student = (request.form.get("student_name") or student).strip() or entry.student_name
        entry_number = request.form.get("entry_number", "").strip()
        entry_date_raw = request.form.get("entry_date", "").strip()
        concept = request.form.get("concept", "").strip()

        if not entry_number.isdigit():
            flash("El número de asiento debe ser numérico.", "danger")
            return render_template("entry_form.html", mode="edit", entry=entry)
        try:
            entry_date = datetime.strptime(entry_date_raw, "%Y-%m-%d").date()
        except ValueError:
            flash("La fecha debe tener formato AAAA-MM-DD.", "danger")
            return render_template("entry_form.html", mode="edit", entry=entry)
        if not concept:
            flash("Debes escribir el concepto general del asiento.", "danger")
            return render_template("entry_form.html", mode="edit", entry=entry)

        lines, error = validate_lines(request.form)
        if error:
            flash(error, "danger")
            return render_template("entry_form.html", mode="edit", entry=entry)

        entry.entry_number = int(entry_number)
        entry.entry_date = entry_date
        entry.concept = concept
        entry.student_name = student
        entry.lines.clear()
        for line in lines:
            entry.lines.append(
                EntryLine(
                    account_code=line["account_code"],
                    account_name=line["account_name"],
                    line_concept=line["line_concept"],
                    debit=line["debit"],
                    credit=line["credit"],
                )
            )
        db.session.commit()
        session["student_name"] = student
        flash("Asiento actualizado correctamente.", "success")
        return redirect(url_for("entries_list", student=student))

    return render_template("entry_form.html", mode="edit", entry=entry)


@app.post("/entries/<int:entry_id>/delete")
def delete_entry(entry_id):
    student = current_student()
    entry = Entry.query.get_or_404(entry_id)
    db.session.delete(entry)
    db.session.commit()
    flash("Asiento eliminado correctamente.", "success")
    return redirect(url_for("entries_list", student=student))


@app.route("/journal")
def journal():
    student = current_student()
    rows = (
        db.session.query(Entry, EntryLine)
        .join(EntryLine, Entry.id == EntryLine.entry_id)
        .filter(Entry.student_name == student)
        .order_by(Entry.entry_date, Entry.entry_number, Entry.id, EntryLine.id)
        .all()
    )
    return render_template("journal.html", rows=rows)


@app.route("/ledger")
def ledger():
    student = current_student()
    balances = build_balances(student)
    selected_code = request.args.get("account_code", "")
    selected = balances.get(selected_code)
    return render_template("ledger.html", balances=balances, selected_code=selected_code, selected=selected, format_amount=format_amount)


@app.route("/income-statement")
def income_statement_view():
    student = current_student()
    balances = build_balances(student)
    incomes, expenses, total_income, total_expense, result = income_statement(balances)
    return render_template(
        "income_statement.html",
        incomes=incomes,
        expenses=expenses,
        total_income=total_income,
        total_expense=total_expense,
        result=result,
        format_amount=format_amount,
    )


@app.route("/balance-sheet")
def balance_sheet_view():
    student = current_student()
    balances = build_balances(student)
    _, _, _, _, result = income_statement(balances)
    asset, liability, equity, total_asset, total_liability, total_equity = balance_sheet(balances, result)
    return render_template(
        "balance_sheet.html",
        asset=asset,
        liability=liability,
        equity=equity,
        total_asset=total_asset,
        total_liability=total_liability,
        total_equity=total_equity,
        format_amount=format_amount,
    )


@app.route("/export/csv")
def export_csv():
    student = current_student()
    rows = (
        db.session.query(Entry, EntryLine)
        .join(EntryLine, Entry.id == EntryLine.entry_id)
        .filter(Entry.student_name == student)
        .order_by(Entry.entry_date, Entry.entry_number, Entry.id, EntryLine.id)
        .all()
    )
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Alumno", "ID Asiento", "Número Asiento", "Fecha", "Concepto Asiento", "Cuenta", "Nombre Cuenta", "Concepto Línea", "Debe", "Haber"])
    for entry, line in rows:
        writer.writerow([
            entry.student_name,
            entry.id,
            entry.entry_number,
            entry.entry_date.strftime("%Y-%m-%d"),
            entry.concept,
            line.account_code,
            line.account_name,
            line.line_concept,
            format_amount(line.debit),
            format_amount(line.credit),
        ])
    memory = io.BytesIO(output.getvalue().encode("utf-8-sig"))
    memory.seek(0)
    return send_file(memory, mimetype="text/csv", as_attachment=True, download_name=f"diario_{student}.csv")


@app.post("/seed")
def seed_demo_data():
    student = current_student()
    if Entry.query.filter_by(student_name=student).count() > 0:
        flash("Ese alumno ya tiene asientos. No se han cargado datos de ejemplo.", "warning")
        return redirect(url_for("index", student=student))

    examples = [
        {
            "entry_number": 1,
            "entry_date": date(2026, 1, 1),
            "concept": "Aportación inicial",
            "lines": [("572", "Bancos", "Entrada en banco", 10000, 0), ("100", "Capital social", "Aportación socios", 0, 10000)],
        },
        {
            "entry_number": 2,
            "entry_date": date(2026, 1, 3),
            "concept": "Compra de mercaderías",
            "lines": [("600", "Compras de mercaderías", "Compra género", 1200, 0), ("400", "Proveedores", "Factura proveedor", 0, 1200)],
        },
        {
            "entry_number": 3,
            "entry_date": date(2026, 1, 10),
            "concept": "Venta de mercaderías",
            "lines": [("430", "Clientes", "Derecho de cobro", 2000, 0), ("700", "Ventas de mercaderías", "Ingreso por venta", 0, 2000)],
        },
    ]

    for ex in examples:
        entry = Entry(entry_number=ex["entry_number"], entry_date=ex["entry_date"], concept=ex["concept"], student_name=student)
        for code, name, concept, debit, credit in ex["lines"]:
            entry.lines.append(EntryLine(account_code=code, account_name=name, line_concept=concept, debit=debit, credit=credit))
        db.session.add(entry)
    db.session.commit()
    flash("Se han cargado datos de ejemplo para practicar.", "success")
    return redirect(url_for("index", student=student))


with app.app_context():
    db.create_all()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
