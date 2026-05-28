from flask import Flask, render_template, request, redirect, session, send_file
from flask_sqlalchemy import SQLAlchemy
from openpyxl import Workbook, load_workbook
from sqlalchemy import inspect, text
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from io import BytesIO
import os

app = Flask(__name__)

# ==================================
# CONFIGURAÇÕES
# ==================================

app.secret_key = os.getenv("ALTITUDE_SECRET_KEY", "altitude_secret_123")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.getenv("ALTITUDE_DB_PATH", os.path.join(BASE_DIR, "altitude.db"))
UPLOAD_FOLDER = os.getenv("ALTITUDE_UPLOAD_FOLDER", os.path.join(BASE_DIR, "uploads"))

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
else:
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
# ==================================
# MODELO O.S
# ==================================

class OrdemServico(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    os = db.Column(db.String(50), unique=True, nullable=False)
    operacao = db.Column(db.String(100))
    data_os = db.Column(db.String(50))
    fazenda = db.Column(db.String(100))
    setor = db.Column(db.String(100))
    unidade = db.Column(db.String(100))
    area_os = db.Column(db.Float)
    finalizado = db.Column(db.Boolean, default=False, nullable=False)

    def total_area_relatada(self):
        total = db.session.query(db.func.coalesce(db.func.sum(ExecucaoOS.area), 0)).filter(ExecucaoOS.os_id == self.id).scalar()
        return float(total or 0)

    def status_label(self):
        total = self.total_area_relatada()
        if self.finalizado:
            return "FINALIZADO"
        if self.area_os and total >= self.area_os:
            return "ÁREA ATINGIDA"
        if total > 0:
            return "EM ANDAMENTO"
        return "AGUARDANDO"

    def status_icon(self):
        if self.finalizado:
            return "🟢"
        if self.area_os and self.total_area_relatada() >= self.area_os:
            return "🔵"
        if self.total_area_relatada() > 0:
            return "🟡"
        return "⚪"

# ==================================
# AUXILIAR
# ==================================

class Auxiliar(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), unique=True, nullable=False)

# ==================================
# PILOTO
# ==================================

class Piloto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    usuario = db.Column(db.String(100), unique=True, nullable=False)
    senha = db.Column(db.String(100), nullable=False)
    perfil = db.Column(db.String(30), nullable=False, default="PILOTO")


def seed_demo_users():
    """Cria usuários iniciais apenas se ainda não existirem."""
    if not Piloto.query.filter_by(usuario="admin").first():
        db.session.add(Piloto(nome="Administrador", usuario="admin", senha="admin", perfil="ADMINISTRADOR"))

    if not Piloto.query.filter_by(usuario="pilot1").first():
        db.session.add(Piloto(nome="Piloto Demo", usuario="pilot1", senha="pilot123", perfil="PILOTO"))

    db.session.commit()

# ==================================
# APONTAMENTO (RELATO)
# ==================================

class ExecucaoOS(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    os_id = db.Column(db.Integer, db.ForeignKey("ordem_servico.id"), nullable=False)
    piloto_id = db.Column(db.Integer, db.ForeignKey("piloto.id"), nullable=False)

    auxiliar = db.Column(db.String(100), nullable=False)
    area = db.Column(db.Float, nullable=False)
    observacao = db.Column(db.Text)

    data_hora = db.Column(db.DateTime, default=datetime.now)

# ==================================
# IMPORTAÇÃO O.S
# ==================================

@app.route("/", methods=["GET", "POST"])
def login_page():
    if session.get("piloto_id"):
        return redirect("/admin" if session.get("perfil") == "ADMINISTRADOR" else "/piloto")

    return render_template("login.html", erro=None)


@app.route("/admin", methods=["GET", "POST"])
def importar_os():
    if session.get("piloto_id") is None or session.get("perfil") != "ADMINISTRADOR":
        return redirect("/login")

    if request.method == "POST":

        arquivo = request.files.get("arquivo")

        if arquivo and arquivo.filename.endswith(".xlsx"):

            caminho = os.path.join(app.config["UPLOAD_FOLDER"], arquivo.filename)
            arquivo.save(caminho)

            try:
                wb = load_workbook(caminho, data_only=True)
                sheet = wb.active

                for row in sheet.iter_rows(min_row=2, values_only=True):
                    if not any(cell not in (None, "") for cell in row):
                        continue

                    os_excel = str(row[0]).strip() if row[0] is not None else ""
                    if not os_excel:
                        continue

                    area_valor = row[5] if len(row) > 5 else 0
                    try:
                        area_num = float(area_valor)
                    except (TypeError, ValueError):
                        area_num = 0.0

                    unidade = str(row[6]).strip() if len(row) > 6 and row[6] is not None else ""

                    registro = OrdemServico.query.filter_by(os=os_excel).first()

                    if registro:
                        registro.operacao = str(row[1]) if len(row) > 1 else ""
                        registro.data_os = str(row[2]) if len(row) > 2 else ""
                        registro.fazenda = str(row[3]) if len(row) > 3 else ""
                        registro.setor = str(row[4]) if len(row) > 4 else ""
                        registro.unidade = unidade
                        registro.area_os = area_num
                    else:
                        novo = OrdemServico(
                            os=os_excel,
                            operacao=str(row[1]) if len(row) > 1 else "",
                            data_os=str(row[2]) if len(row) > 2 else "",
                            fazenda=str(row[3]) if len(row) > 3 else "",
                            setor=str(row[4]) if len(row) > 4 else "",
                            unidade=unidade,
                            area_os=area_num,
                        )
                        db.session.add(novo)

                db.session.commit()
            except Exception:
                db.session.rollback()

    dados_os = OrdemServico.query.order_by(OrdemServico.os).all()
    pilotos = Piloto.query.order_by(Piloto.nome).all()
    auxiliares = Auxiliar.query.order_by(Auxiliar.nome).all()

    return render_template(
        "importar_os.html",
        dados_os=dados_os,
        is_admin=True,
        pilotos=pilotos,
        auxiliares=auxiliares,
    )

@app.route("/admin/os/<int:os_id>/excluir", methods=["POST"])
def excluir_os(os_id):
    if session.get("piloto_id") is None or session.get("perfil") != "ADMINISTRADOR":
        return redirect("/login")

    os_item = OrdemServico.query.get_or_404(os_id)
    ExecucaoOS.query.filter_by(os_id=os_item.id).delete()
    db.session.delete(os_item)
    db.session.commit()

    return redirect("/admin")

@app.route("/admin/pilotos/<int:piloto_id>/excluir", methods=["POST"])
def excluir_piloto(piloto_id):
    if session.get("piloto_id") is None or session.get("perfil") != "ADMINISTRADOR":
        return redirect("/login")

    piloto = Piloto.query.get_or_404(piloto_id)
    ExecucaoOS.query.filter_by(piloto_id=piloto.id).delete()
    db.session.delete(piloto)
    db.session.commit()

    return redirect("/admin")

@app.route("/admin/auxiliares/<int:auxiliar_id>/excluir", methods=["POST"])
def excluir_auxiliar(auxiliar_id):
    if session.get("piloto_id") is None or session.get("perfil") != "ADMINISTRADOR":
        return redirect("/login")

    auxiliar = Auxiliar.query.get_or_404(auxiliar_id)
    db.session.delete(auxiliar)
    db.session.commit()

    return redirect("/admin")

# ==================================
# LOGIN
# ==================================

@app.route("/login", methods=["GET", "POST"])
def login():

    erro = None

    if request.method == "POST":

        usuario = request.form.get("usuario", "").strip().lower()
        senha = request.form.get("senha", "")
        perfil = (request.form.get("perfil") or "PILOTO").strip().upper()

        piloto = Piloto.query.filter_by(usuario=usuario, senha=senha, perfil=perfil).first()

        if piloto:
            session["piloto_id"] = piloto.id
            session["perfil"] = piloto.perfil
            return redirect("/admin" if piloto.perfil == "ADMINISTRADOR" else "/piloto")
        else:
            erro = "Usuário, senha ou ambiente inválidos"

    return render_template("login.html", erro=erro)

# ==================================
# LISTA O.S (MOBILE)
# ==================================

@app.route("/piloto")
def piloto():

    piloto_id = session.get("piloto_id")

    if not piloto_id or session.get("perfil") != "PILOTO":
        return redirect("/login")

    piloto = Piloto.query.get(piloto_id)
    lista_os = OrdemServico.query.order_by(OrdemServico.os).all()

    for os_item in lista_os:
        os_item.total_relatado = os_item.total_area_relatada()
        os_item.status = os_item.status_label()
        os_item.icon = os_item.status_icon()
        if os_item.finalizado:
            os_item.status_class = "status-finalizado"
        elif os_item.area_os and os_item.total_relatado >= os_item.area_os:
            os_item.status_class = "status-atingida"
        elif os_item.total_relatado > 0:
            os_item.status_class = "status-andamento"
        else:
            os_item.status_class = "status-aguardando"

    return render_template(
        "piloto_mobile.html",
        piloto=piloto,
        lista_os=lista_os,
    )

# ==================================
# TELA O.S (RELATOS)
# ==================================

@app.route("/os/<int:os_id>", methods=["GET", "POST"])
def os_mobile(os_id):

    piloto_id = session.get("piloto_id")

    if not piloto_id:
        return redirect("/login")

    os_item = OrdemServico.query.get_or_404(os_id)

    auxiliares = Auxiliar.query.order_by(Auxiliar.nome).all()

    if request.method == "POST":
        auxiliar = request.form.get("auxiliar")
        area = request.form.get("area")
        observacao = request.form.get("observacao")
        finalizar_os = request.form.get("finalizar_os") == "on"

        if auxiliar and area:
            novo = ExecucaoOS(
                os_id=os_id,
                piloto_id=piloto_id,
                auxiliar=auxiliar.strip(),
                area=float(area),
                observacao=observacao
            )
            db.session.add(novo)

        os_item.finalizado = finalizar_os
        db.session.commit()

        return redirect(f"/os/{os_id}")

    historico = ExecucaoOS.query.filter_by(
        os_id=os_id
    ).order_by(
        ExecucaoOS.data_hora.desc()
    ).all()

    # Anexa o nome do piloto a cada registro de histórico para exibição
    for h in historico:
        piloto = Piloto.query.get(h.piloto_id)
        h.piloto_nome = piloto.nome if piloto else "N/A"

    return render_template(
        "os_mobile.html",
        os=os_item,
        auxiliares=auxiliares,
        historico=historico
    )

# ==================================
# 🟢 ADMIN RELATÓRIOS (CORRIGIDO)
# ==================================

@app.route("/admin/relatorios")
def admin_relatorios():
    piloto_id = session.get("piloto_id")
    if not piloto_id:
        return redirect("/login")

    dados_raw = ExecucaoOS.query.order_by(
        ExecucaoOS.data_hora.desc()
    ).all()

    dados = []

    for r in dados_raw:

        os_item = OrdemServico.query.get(r.os_id)
        piloto = Piloto.query.get(r.piloto_id)

        dados.append({
            "os": os_item.os if os_item else "N/A",
            "operacao": os_item.operacao if os_item else "",
            "fazenda": os_item.fazenda if os_item else "",
            "setor": os_item.setor if os_item else "",
            "unidade": os_item.unidade if os_item else "",
            "piloto": piloto.nome if piloto else "N/A",
            "auxiliar": r.auxiliar,
            "area": r.area,
            "observacao": r.observacao,
            "status": os_item.status_label() if os_item else "N/A",
            "data": format_brasilia(r.data_hora)
        })

    return render_template("admin_relatorios.html", dados=dados)


def format_brasilia(value):
    if not value:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m/%Y %H:%M")


@app.route("/admin/exportar_excel")
def exportar_excel():
    piloto_id = session.get("piloto_id")
    if not piloto_id:
        return redirect("/login")

    dados_raw = ExecucaoOS.query.order_by(ExecucaoOS.data_hora.desc()).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Relatorios"
    ws.append(["OS", "Operação", "Fazenda", "Setor", "Unidade", "Piloto", "Auxiliar", "Área", "Status", "Observação", "Data"])

    for r in dados_raw:
        os_item = OrdemServico.query.get(r.os_id)
        piloto = Piloto.query.get(r.piloto_id)
        ws.append([
            os_item.os if os_item else "N/A",
            os_item.operacao if os_item else "",
            os_item.fazenda if os_item else "",
            os_item.setor if os_item else "",
            os_item.unidade if os_item else "",
            piloto.nome if piloto else "N/A",
            r.auxiliar,
            r.area,
            os_item.status_label() if os_item else "N/A",
            r.observacao or "",
            format_brasilia(r.data_hora),
        ])

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name="relatorios_altitude.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/admin/pilotos", methods=["POST"])
def criar_piloto():
    if session.get("piloto_id") is None or session.get("perfil") != "ADMINISTRADOR":
        return redirect("/login")

    nome = (request.form.get("nome") or "").strip()
    usuario = (request.form.get("usuario") or "").strip().lower()
    senha = (request.form.get("senha") or "").strip()

    if nome and usuario and senha and not Piloto.query.filter_by(usuario=usuario).first():
        db.session.add(Piloto(nome=nome, usuario=usuario, senha=senha, perfil="PILOTO"))
        db.session.commit()

    return redirect("/admin")


@app.route("/admin/auxiliares", methods=["POST"])
def criar_auxiliar_admin():
    if session.get("piloto_id") is None or session.get("perfil") != "ADMINISTRADOR":
        return redirect("/login")

    nome = (request.form.get("nome") or "").strip()
    if nome and not Auxiliar.query.filter_by(nome=nome).first():
        db.session.add(Auxiliar(nome=nome))
        db.session.commit()

    return redirect("/admin")


@app.route("/auxiliares", methods=["GET", "POST"])
def auxiliares():
    piloto_id = session.get("piloto_id")
    if not piloto_id:
        return redirect("/login")

    if request.method == "POST":
        nome = request.form.get("nome")
        if nome and not Auxiliar.query.filter_by(nome=nome.strip()).first():
            auxiliar = Auxiliar(nome=nome.strip())
            db.session.add(auxiliar)
            db.session.commit()

    auxiliares = Auxiliar.query.order_by(Auxiliar.nome).all()
    return render_template("auxiliares.html", auxiliares=auxiliares)

# ==================================
# LOGOUT
# ==================================

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# ==================================
# START
# ==================================

def ensure_schema():
    with app.app_context():
        inspector = inspect(db.engine)
        columns = {col['name'] for col in inspector.get_columns('ordem_servico')}
        if 'unidade' not in columns:
            db.session.execute(text('ALTER TABLE ordem_servico ADD COLUMN unidade VARCHAR(100)'))
            db.session.commit()
        if 'finalizado' not in columns:
            db.session.execute(text('ALTER TABLE ordem_servico ADD COLUMN finalizado BOOLEAN DEFAULT 0 NOT NULL'))
            db.session.commit()


def init_db():
    with app.app_context():
        db.create_all()
        ensure_schema()
        seed_demo_users()


if __name__ == "__main__":
    init_db()
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "False").lower() in ("1", "true", "yes")
    )