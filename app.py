from flask import Flask, render_template, request, redirect, session, send_file, url_for
from werkzeug.utils import secure_filename
from flask_socketio import SocketIO, emit, join_room
from functools import wraps
from datetime import date, datetime, timedelta, time
import io
import os
import qrcode
import calendar

from models import db, Funcionario, Mensagem, EscalaMes, EscalaItem  # <-- garanta que existem no models.py

# PDF
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.lib.units import cm

# SQLite (anti lock)
from sqlalchemy import event
from sqlalchemy.engine import Engine
import sqlite3

# ==================================================
# APP
# ==================================================

app = Flask(__name__)

# ✅ Produção: segredo via variável de ambiente (fallback para dev/local)
app.secret_key = os.getenv("SECRET_KEY", "hospital2026_dev_troque_em_producao")

# ✅ Banco: preferir instance/hospital.db (fallback para o antigo)
os.makedirs(app.instance_path, exist_ok=True)
default_db_path = os.path.join(app.instance_path, "hospital.db")
db_path = os.getenv("SQLITE_PATH", default_db_path)

# aceita também quem ainda usa sqlite:///hospital.db
if db_path.startswith("sqlite:"):
    app.config["SQLALCHEMY_DATABASE_URI"] = db_path
else:
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# ✅ SocketIO: em produção, setar SOCKETIO_CORS com seu domínio (ex: https://plataforma.seudominio.com.br)
socketio_cors = os.getenv("SOCKETIO_CORS", "*")
socketio = SocketIO(app, cors_allowed_origins=socketio_cors)

db.init_app(app)

# ==================================================
# SQLITE - PRAGMAS ANTI LOCK (WAL + timeout)
# ==================================================

@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA busy_timeout=5000;")
        cursor.close()

# ==================================================
# PATHS / UPLOADS
# ==================================================

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

UPLOAD_CHAT = os.path.join(BASE_DIR, "static", "uploads", "chat")
os.makedirs(UPLOAD_CHAT, exist_ok=True)

# ✅ UPLOAD COMUNICADOS (PDF)
UPLOAD_COMUNICADOS = os.path.join(BASE_DIR, "static", "uploads", "comunicados")
os.makedirs(UPLOAD_COMUNICADOS, exist_ok=True)

def _save_pdf_comunicado(file_storage):
    if not file_storage or not file_storage.filename:
        return None

    filename = secure_filename(file_storage.filename)

    # só PDF
    if not filename.lower().endswith(".pdf"):
        return None

    base, ext = os.path.splitext(filename)
    filename_final = f"{base}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"

    file_storage.save(os.path.join(UPLOAD_COMUNICADOS, filename_final))
    return filename_final

# ==================================================
# USER GLOBAL (TODAS TELAS)
# ==================================================

@app.context_processor
def inject_user():
    user = None
    if "user_id" in session:
        user = Funcionario.query.get(session["user_id"])
    return dict(user=user)

# ==================================================
# DADOS EM MEMÓRIA
# ==================================================

comunicados_lista = []
cursos_lista = []
setores_lista = []
conclusoes = []
pedidos_materiais = []

# ==================================================
# BANCO / USUÁRIOS PADRÃO
# ==================================================

with app.app_context():
    db.create_all()

    if not Funcionario.query.filter_by(cpf="12345678900").first():
        db.session.add(Funcionario(
            nome="Diretor Teste",
            cpf="12345678900",
            senha="1234",
            funcao="Direção",
            telefone="(21)99999-9999",
            email="direcao@hospital.com",
            status="Ativo"
        ))

    if not Funcionario.query.filter_by(cpf="11111111111").first():
        db.session.add(Funcionario(
            nome="Funcionário Teste",
            cpf="11111111111",
            senha="1234",
            funcao="Funcionário",
            telefone="(21)98888-8888",
            email="funcionario@hospital.com",
            status="Ativo"
        ))

    db.session.commit()

# ==================================================
# PERMISSÕES
# ==================================================

def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped_view

def direcao_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if session.get("funcao") != "Direção":
            return redirect(url_for("acesso_negado"))
        return view(*args, **kwargs)
    return wrapped_view

def funcionario_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if session.get("funcao") != "Funcionário":
            return redirect(url_for("acesso_negado"))
        return view(*args, **kwargs)
    return wrapped_view

# ==================================================
# LIMPAR MENSAGENS
# ==================================================

def limpar_mensagens_vencidas():
    agora = datetime.utcnow()
    msgs = Mensagem.query.filter(Mensagem.expira_em < agora).all()

    for m in msgs:
        if m.arquivo:
            path = os.path.join(UPLOAD_CHAT, m.arquivo)
            if os.path.exists(path):
                os.remove(path)
        db.session.delete(m)

    db.session.commit()

# ==================================================
# LOGIN / LOGOUT
# ==================================================

@app.route("/")
def index():
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = Funcionario.query.filter_by(
            cpf=request.form["cpf"],
            senha=request.form["senha"],
            status="Ativo"
        ).first()

        if user:
            session.clear()
            session["user_id"] = user.id
            session["nome"] = user.nome
            session["funcao"] = user.funcao

            if user.funcao == "Direção":
                return redirect(url_for("admin_dashboard"))

            return redirect(url_for("dashboard"))

        return render_template("login.html", erro="Login inválido")

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ==================================================
# DASHBOARD FUNCIONÁRIO
# ==================================================

@app.route("/dashboard")
@login_required
@funcionario_required
def dashboard():
    return render_template("dashboard.html")

# ==================================================
# PERFIL
# ==================================================

@app.route("/meu-perfil")
@login_required
@funcionario_required
def meu_perfil():
    return render_template("meu_perfil.html")

# ==================================================
# ESCALA (ANTIGA - PDF) - (pode manter, mesmo que tenha tirado do menu)
# ==================================================

@app.route("/minha-escala")
@login_required
@funcionario_required
def minha_escala():
    setores = [
        {"nome": "ASG", "arquivo": "asg.pdf"},
        {"nome": "Recepção", "arquivo": "recepcao.pdf"},
        {"nome": "Enfermagem", "arquivo": "enfermagem.pdf"},
        {"nome": "Nutrição", "arquivo": "nutricao.pdf"},
        {"nome": "Administrativo", "arquivo": "administrativo.pdf"},
    ]
    return render_template("minha_escala.html", setores=setores)

# ==================================================
# COMUNICADOS (FUNCIONÁRIO)
# ==================================================

@app.route("/comunicados")
@login_required
@funcionario_required
def comunicados():
    comunicados = list(reversed(comunicados_lista)) if comunicados_lista else []
    return render_template("comunicados.html", comunicados=comunicados)

# ==================================================
# CURSOS FUNCIONÁRIO
# ==================================================

@app.route("/meus-cursos")
@login_required
@funcionario_required
def meus_cursos():
    lista = []

    for curso in cursos_lista:
        status = "Pendente"

        for c in conclusoes:
            if c["user_id"] == session["user_id"] and c["curso_id"] == curso["id"]:
                status = "Realizado"

        lista.append({**curso, "status": status})

    return render_template("meus_cursos.html", cursos=lista)

# ==================================================
# CONCLUIR CURSO
# ==================================================

@app.route("/curso/<int:id>/concluir")
@login_required
@funcionario_required
def concluir_curso(id):
    user = Funcionario.query.get(session["user_id"])
    curso = next((c for c in cursos_lista if c["id"] == id), None)

    if not curso:
        return "Curso não encontrado"

    if not any(
        c for c in conclusoes
        if c["user_id"] == user.id and c["curso_id"] == id
    ):
        conclusoes.append({
            "user_id": user.id,
            "curso_id": id,
            "data": date.today().strftime("%d/%m/%Y")
        })

    return gerar_certificado_pdf(user, curso)

# ==================================================
# PROGRESSO
# ==================================================

@app.route("/meu-progresso")
@login_required
@funcionario_required
def meu_progresso():
    total = sum(int(c["carga"]) for c in cursos_lista) if cursos_lista else 0
    feito = 0

    for curso in cursos_lista:
        if any(
            c for c in conclusoes
            if c["user_id"] == session["user_id"] and c["curso_id"] == curso["id"]
        ):
            feito += int(curso["carga"])

    progresso = int((feito / total) * 100) if total else 0

    return render_template(
        "meu_progresso.html",
        progresso=progresso,
        carga_concluida=feito,
        total_carga=total
    )

# ==================================================
# CERTIFICADOS FUNCIONÁRIO
# ==================================================

@app.route("/meus-certificados")
@login_required
@funcionario_required
def meus_certificados():
    lista = []

    for c in conclusoes:
        if c["user_id"] == session["user_id"]:
            curso = next((x for x in cursos_lista if x["id"] == c["curso_id"]), None)
            if curso:
                lista.append({
                    "curso": curso["titulo"],
                    "data": c["data"],
                    "id": curso["id"]
                })

    return render_template("meus_certificados.html", certificados=lista)

# ==================================================
# ALTERAR SENHA
# ==================================================

@app.route("/alterar-senha", methods=["GET", "POST"])
@login_required
@funcionario_required
def alterar_senha():
    user = Funcionario.query.get(session["user_id"])

    erro = None
    sucesso = None

    if request.method == "POST":
        if request.form["senha_atual"] != user.senha:
            erro = "Senha atual incorreta"
        elif request.form["nova_senha"] != request.form["confirmar"]:
            erro = "Senhas não conferem"
        else:
            user.senha = request.form["nova_senha"]
            db.session.commit()
            sucesso = "Senha alterada com sucesso"

    return render_template("alterar_senha.html", erro=erro, sucesso=sucesso)

# ==================================================
# DASHBOARD DIREÇÃO
# ==================================================

@app.route("/admin")
@login_required
@direcao_required
def admin_dashboard():
    funcionarios = Funcionario.query.all()

    return render_template(
        "dashboard_admin.html",
        total_funcionarios=len(funcionarios),
        ativos=len([f for f in funcionarios if f.status == "Ativo"]),
        inativos=len([f for f in funcionarios if f.status == "Inativo"]),
        total_cursos=len(cursos_lista),
        total_comunicados=len(comunicados_lista)
    )

# ==================================================
# ADMIN - FUNCIONÁRIOS
# ==================================================

@app.route("/admin/funcionarios")
@login_required
@direcao_required
def admin_funcionarios():
    busca = request.args.get("busca")

    if busca:
        funcionarios = Funcionario.query.filter(
            (Funcionario.nome.ilike(f"%{busca}%")) |
            (Funcionario.cpf.ilike(f"%{busca}%")) |
            (Funcionario.funcao.ilike(f"%{busca}%")) |
            (Funcionario.email.ilike(f"%{busca}%"))
        ).all()
    else:
        funcionarios = Funcionario.query.all()

    return render_template("admin/funcionarios.html", funcionarios=funcionarios, busca=busca)

# ==================================================
# ADMIN - CURSOS
# ==================================================

@app.route("/admin/cursos")
@login_required
@direcao_required
def admin_cursos():
    return render_template("admin/cursos.html", cursos=cursos_lista)

@app.route("/admin/cursos/novo", methods=["GET", "POST"])
@login_required
@direcao_required
def admin_novo_curso():
    if request.method == "POST":
        pdf = request.files.get("pdf")
        nome = None

        if pdf:
            cursos_dir = os.path.join(BASE_DIR, "static", "uploads", "cursos")
            os.makedirs(cursos_dir, exist_ok=True)
            nome = secure_filename(pdf.filename)
            pdf.save(os.path.join(cursos_dir, nome))

        cursos_lista.append({
            "id": len(cursos_lista) + 1,
            "titulo": request.form["titulo"],
            "descricao": request.form["descricao"],
            "video": request.form["video"],
            "pdf": nome,
            "carga": request.form["carga"],
            "data": date.today().strftime("%d/%m/%Y")
        })

        return redirect(url_for("admin_cursos"))

    return render_template("admin/novo_curso.html")

# ==================================================
# ADMIN - RELATÓRIOS
# ==================================================

@app.route("/admin/relatorios")
@login_required
@direcao_required
def admin_relatorios():
    return render_template("admin_relatorios.html")

@app.route("/admin/relatorio/funcionarios")
@login_required
@direcao_required
def relatorio_funcionarios():
    busca = request.args.get("busca", "").strip()
    query = Funcionario.query

    if busca:
        query = query.filter(
            (Funcionario.nome.ilike(f"%{busca}%")) |
            (Funcionario.cpf.ilike(f"%{busca}%")) |
            (Funcionario.funcao.ilike(f"%{busca}%")) |
            (Funcionario.email.ilike(f"%{busca}%"))
        )

    funcionarios = query.order_by(Funcionario.nome).all()

    return render_template(
        "relatorio_funcionarios.html",
        funcionarios=funcionarios,
        busca=busca
    )

@app.route("/admin/funcionarios/pdf")
@login_required
@direcao_required
def gerar_pdf_funcionarios():
    nome = request.args.get("nome")
    setor = request.args.get("setor")
    cargo = request.args.get("cargo")
    status = request.args.get("status")
    campos = request.args.getlist("campos")

    if not campos:
        campos = ["nome", "cpf", "setor", "cargo"]

    query = Funcionario.query

    if nome:
        query = query.filter(Funcionario.nome.ilike(f"%{nome}%"))
    if setor:
        query = query.filter(Funcionario.setor.ilike(f"%{setor}%"))
    if cargo:
        query = query.filter(Funcionario.cargo.ilike(f"%{cargo}%"))
    if status:
        query = query.filter(Funcionario.status == status)

    funcionarios = query.order_by(Funcionario.nome).all()

    nome_arquivo = f"relatorio_funcionarios_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    caminho = os.path.join(BASE_DIR, "static", nome_arquivo)

    c = canvas.Canvas(caminho, pagesize=A4)
    largura, altura = A4
    y = altura - 2 * cm

    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(largura / 2, y, "Hospital Municipal da Mulher")
    y -= 20

    c.setFont("Helvetica", 11)
    c.drawCentredString(largura / 2, y, "Secretaria Municipal de Saúde de Cabo Frio")
    y -= 30

    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(largura / 2, y, "Relatório de Funcionários")
    y -= 30

    c.setFont("Helvetica", 9)
    c.drawString(2 * cm, y, f"Data: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    y -= 20

    c.setFont("Helvetica-Bold", 9)

    x = 2 * cm
    colunas = {
        "nome": "Nome",
        "cpf": "CPF",
        "setor": "Setor",
        "cargo": "Cargo",
        "turno": "Turno",
        "carga_horaria": "Carga Horária",
        "tipo_vinculo": "Vínculo"
    }
    largura_coluna = 7 * cm

    for campo in campos:
        titulo = colunas.get(campo, campo)
        c.drawString(x, y, titulo)
        x += largura_coluna

    y -= 15
    c.line(2 * cm, y, largura - 2 * cm, y)
    y -= 15

    c.setFont("Helvetica", 9)

    for f in funcionarios:
        x = 2 * cm
        for campo in campos:
            valor = getattr(f, campo, "") or ""
            valor = str(valor)
            if len(valor) > 35:
                valor = valor[:35] + "..."
            c.drawString(x, y, valor)
            x += largura_coluna

        y -= 15

        if y < 2 * cm:
            c.showPage()
            c.setFont("Helvetica", 9)
            y = altura - 2 * cm

    c.save()
    return redirect(f"/static/{nome_arquivo}")

# ==================================================
# ADMIN - SETORES (lista em memória)
# ==================================================

@app.route("/admin/setores", methods=["GET", "POST"])
@login_required
@direcao_required
def admin_setores():
    if request.method == "POST":
        nome = request.form.get("nome")
        if nome and nome not in setores_lista:
            setores_lista.append(nome)
        return redirect(url_for("admin_setores"))

    return render_template("admin/setores.html", setores=setores_lista)

# ==================================================
# ADMIN - ADICIONAR FUNCIONÁRIO (COM ESCALA)
# ==================================================

@app.route("/admin/funcionario/novo", methods=["GET", "POST"])
@login_required
@direcao_required
def admin_novo_funcionario():
    erro = None

    if request.method == "POST":
        cpf = request.form["cpf"]
        existe = Funcionario.query.filter_by(cpf=cpf).first()

        if existe:
            erro = "Já existe funcionário com esse CPF."
        else:
            func = Funcionario(
                nome=request.form["nome"],
                cpf=cpf,
                senha=request.form["senha"],

                funcao=request.form["funcao"],
                status=request.form.get("status", "Ativo"),

                telefone=request.form["telefone"],
                email=request.form["email"],

                matricula=request.form.get("matricula"),
                setor=request.form.get("setor"),
                cargo=request.form.get("cargo"),

                data_admissao=request.form.get("data_admissao"),
                nascimento=request.form.get("nascimento"),

                turno=request.form.get("turno"),
                tipo_vinculo=request.form.get("tipo_vinculo"),
                carga_horaria=request.form.get("carga_horaria"),

                observacoes=request.form.get("observacoes"),

                # ========= ESCALA (NOVO) =========
                escala_tipo=request.form.get("escala_tipo", "SEG_SEX"),
                plantao_base=request.form.get("plantao_base") or None
            )

            db.session.add(func)
            db.session.commit()
            return redirect(url_for("admin_funcionarios"))

    return render_template("admin/novo_funcionario.html", erro=erro)

# ==================================================
# ADMIN - CERTIFICADOS / COMUNICADOS
# ==================================================

@app.route("/admin/certificados")
@login_required
@direcao_required
def admin_certificados():
    lista = []

    for c in conclusoes:
        funcionario = Funcionario.query.get(c["user_id"])
        curso = next((x for x in cursos_lista if x["id"] == c["curso_id"]), None)

        if funcionario and curso:
            lista.append({
                "funcionario": funcionario.nome,
                "curso": curso["titulo"],
                "data": c["data"],
                "codigo": f'{c["user_id"]}-{c["curso_id"]}-{c["data"]}'
            })

    return render_template("admin/certificados.html", certificados=lista)

# ✅ ADMIN COMUNICADOS (GET + POST) — texto com edição (HTML) + upload PDF
@app.route("/admin/comunicados", methods=["GET", "POST"])
@login_required
@direcao_required
def admin_comunicados():
    erro = None
    sucesso = None

    if request.method == "POST":
        titulo = (request.form.get("titulo") or "").strip()
        conteudo_html = (request.form.get("conteudo_html") or "").strip()

        pdf = request.files.get("pdf")
        pdf_nome = _save_pdf_comunicado(pdf) if pdf else None

        if not titulo:
            erro = "Informe o título do comunicado."
        elif not conteudo_html and not pdf_nome:
            erro = "Informe um texto ou envie um PDF."
        else:
            comunicados_lista.append({
                "id": len(comunicados_lista) + 1,
                "titulo": titulo,
                "conteudo_html": conteudo_html,
                "pdf": pdf_nome,
                "data": datetime.now().strftime("%d/%m/%Y %H:%M")
            })
            sucesso = "Comunicado publicado com sucesso!"

    comunicados = list(reversed(comunicados_lista)) if comunicados_lista else []
    return render_template(
        "admin/comunicados.html",
        comunicados=comunicados,
        erro=erro,
        sucesso=sucesso
    )

# ==================================================
# CERTIFICADO PDF
# ==================================================

def gerar_certificado_pdf(funcionario, curso):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    w, h = A4

    pdf.setFillColorRGB(0.97, 0.97, 0.97)
    pdf.rect(0, 0, w, h, fill=1)

    pdf.setStrokeColorRGB(0.13, 0.32, 0.65)
    pdf.setLineWidth(4)
    pdf.rect(30, 30, w - 60, h - 60)

    pdf.setFont("Helvetica-Bold", 30)
    pdf.drawCentredString(w / 2, h - 150, "CERTIFICADO")

    pdf.setFont("Helvetica", 14)
    pdf.drawCentredString(w / 2, h - 220, "Certificamos que")

    pdf.setFont("Helvetica-Bold", 22)
    pdf.drawCentredString(w / 2, h - 260, funcionario.nome.upper())

    pdf.setFont("Helvetica", 14)
    pdf.drawCentredString(w / 2, h - 300, "concluiu o curso")

    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawCentredString(w / 2, h - 330, curso["titulo"])

    data_str = date.today().strftime("%d/%m/%Y")
    pdf.setFont("Helvetica", 12)
    pdf.drawCentredString(w / 2, h - 370, f"Concluído em {data_str}")

    codigo = f"{funcionario.id}-{curso['id']}-{data_str}"

    # ✅ URL correta em produção
    base_url = os.getenv("BASE_URL", "http://localhost:5000").rstrip("/")
    url = f"{base_url}/validar-certificado/{codigo}"

    qr = qrcode.make(url)
    qr_io = io.BytesIO()
    qr.save(qr_io)
    qr_io.seek(0)

    pdf.drawImage(ImageReader(qr_io), w - 150, 120, 100, 100)

    pdf.line(w / 2 - 120, 200, w / 2 + 120, 200)
    pdf.drawCentredString(w / 2, 180, "Direção Administrativa")
    pdf.drawCentredString(w / 2, 160, "Hospital da Mulher")

    pdf.save()
    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"certificado_{curso['titulo'].replace(' ', '_')}.pdf",
        mimetype="application/pdf"
    )

# ==================================================
# VALIDAR CERTIFICADO
# ==================================================

@app.route("/validar-certificado/<codigo>")
def validar_certificado(codigo):
    dados = codigo.split("-")

    if len(dados) != 3:
        return render_template("validar_certificado.html", valido=False)

    user_id, curso_id, data_str = dados

    valido = False
    funcionario = None
    curso = None

    for c in conclusoes:
        if (
            str(c["user_id"]) == user_id and
            str(c["curso_id"]) == curso_id and
            c["data"] == data_str
        ):
            valido = True
            funcionario = Funcionario.query.get(int(user_id))
            curso = next((x for x in cursos_lista if x["id"] == int(curso_id)), None)
            break

    return render_template(
        "validar_certificado.html",
        valido=valido,
        funcionario=funcionario,
        curso=curso,
        data=data_str,
        codigo=codigo
    )

# ==================================================
# PEDIDO DE MATERIAIS (EM MEMÓRIA)
# ==================================================

@app.route("/pedido-materiais", methods=["GET", "POST"])
@login_required
@funcionario_required
def pedido_materiais():
    if request.method == "POST":
        novo = {
            "id": len(pedidos_materiais) + 1,
            "funcionario": session["nome"],
            "setor": request.form["setor"],
            "material": request.form["material"],
            "quantidade": request.form["quantidade"],
            "data": date.today().strftime("%d/%m/%Y"),
            "status": "Pendente"
        }
        pedidos_materiais.append(novo)
        return redirect(url_for("pedido_materiais"))

    return render_template("pedido_materiais.html")

@app.route("/admin/pedidos-materiais")
@login_required
@direcao_required
def admin_pedidos_materiais():
    return render_template("admin/pedidos_materiais.html", pedidos=pedidos_materiais)

@app.route("/admin/pedido/<int:id>/aprovar")
@login_required
@direcao_required
def aprovar_pedido(id):
    for p in pedidos_materiais:
        if p["id"] == id:
            p["status"] = "Aprovado"
            break
    return redirect(url_for("admin_pedidos_materiais"))

@app.route("/admin/pedido/<int:id>/rejeitar")
@login_required
@direcao_required
def rejeitar_pedido(id):
    for p in pedidos_materiais:
        if p["id"] == id:
            p["status"] = "Rejeitado"
            break
    return redirect(url_for("admin_pedidos_materiais"))

# ==================================================
# ENVIAR MENSAGEM (HTTP)
# ==================================================

@app.route("/enviar-mensagem", methods=["POST"])
@login_required
def enviar_mensagem():
    texto = request.form.get("texto")
    room = request.form.get("room")
    destino = int(request.form.get("destino"))

    arquivo = request.files.get("arquivo")
    nome_arquivo = None

    if arquivo and arquivo.filename:
        os.makedirs(UPLOAD_CHAT, exist_ok=True)
        nome_arquivo = secure_filename(arquivo.filename)

        # ✅ evita sobrescrever arquivo com mesmo nome
        base, ext = os.path.splitext(nome_arquivo)
        nome_final = f"{base}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
        arquivo.save(os.path.join(UPLOAD_CHAT, nome_final))
        nome_arquivo = nome_final

    user = Funcionario.query.get(session["user_id"])

    msg = Mensagem(
        remetente_id=user.id,
        destinatario_id=destino,
        texto=texto,
        arquivo=nome_arquivo,
        data_envio=datetime.utcnow(),
        expira_em=datetime.utcnow() + timedelta(hours=15)
    )

    db.session.add(msg)
    db.session.commit()

    if not room:
        room = f"{min(user.id, destino)}_{max(user.id, destino)}"

    socketio.emit("nova_mensagem", {
        "remetente": user.nome,
        "texto": texto,
        "arquivo": nome_arquivo,
        "hora": msg.data_envio.strftime("%H:%M")
    }, room=room)

    return "", 204

# ==================================================
# SOCKET.IO
# ==================================================

@socketio.on("join")
def handle_join(data):
    join_room(data["room"])

@socketio.on("send_message")
def handle_message(data):
    limpar_mensagens_vencidas()

    remetente = int(data["from"])
    destino = int(data["to"])
    texto = data["text"]
    arquivo = data.get("file")
    room = data["room"]

    msg = Mensagem(
        remetente_id=remetente,
        destinatario_id=destino,
        texto=texto,
        arquivo=arquivo,
        data_envio=datetime.utcnow(),
        expira_em=datetime.utcnow() + timedelta(hours=15)
    )

    db.session.add(msg)
    db.session.commit()

    emit("receive", {
        "from": remetente,
        "text": texto,
        "file": arquivo,
        "time": msg.data_envio.strftime("%H:%M")
    }, room=room)

# ==================================================
# UPLOAD CHAT
# ==================================================

@app.route("/upload-chat", methods=["POST"])
@login_required
def upload_chat():
    file = request.files["file"]
    os.makedirs(UPLOAD_CHAT, exist_ok=True)

    name = secure_filename(file.filename)
    base, ext = os.path.splitext(name)
    name_final = f"{base}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
    file.save(os.path.join(UPLOAD_CHAT, name_final))

    return {"filename": name_final}

# ==================================================
# LISTA DE CONVERSAS (SIDEBAR)
# ==================================================

@app.route("/conversas")
@login_required
def conversas():
    user_id = session["user_id"]

    mensagens = Mensagem.query.filter(
        (Mensagem.remetente_id == user_id) |
        (Mensagem.destinatario_id == user_id)
    ).order_by(Mensagem.data_envio.desc()).all()

    contatos_ids = set()

    for m in mensagens:
        if m.remetente_id == user_id:
            contatos_ids.add(m.destinatario_id)
        else:
            contatos_ids.add(m.remetente_id)

    contatos = Funcionario.query.filter(
        Funcionario.id.in_(contatos_ids)
    ).all()

    return render_template("conversas.html", contatos=contatos)

# ==================================================
# LISTA DE CONTATOS PARA CHAT
# ==================================================

@app.route("/contatos-chat")
@login_required
def contatos_chat():
    user_id = session["user_id"]

    contatos = Funcionario.query.filter(
        Funcionario.id != user_id
    ).order_by(Funcionario.nome).all()

    return render_template("contatos_chat.html", contatos=contatos)

# ==================================================
# CHAT PRINCIPAL (ESTILO WHATSAPP)
# ==================================================

@app.route("/chat", methods=["GET"])
@app.route("/chat/<int:destino_id>", methods=["GET"])
@login_required
def chat(destino_id=None):
    limpar_mensagens_vencidas()

    user = Funcionario.query.get(session["user_id"])

    contatos = Funcionario.query.filter(
        Funcionario.id != user.id
    ).order_by(Funcionario.nome).all()

    mensagens = []
    outro = None
    sala = None

    if destino_id:
        outro = Funcionario.query.get_or_404(destino_id)

        mensagens = Mensagem.query.filter(
            ((Mensagem.remetente_id == user.id) & (Mensagem.destinatario_id == destino_id)) |
            ((Mensagem.remetente_id == destino_id) & (Mensagem.destinatario_id == user.id))
        ).order_by(Mensagem.data_envio).all()

        sala = f"{min(user.id, destino_id)}_{max(user.id, destino_id)}"

    return render_template(
        "chat.html",
        contatos=contatos,
        mensagens=mensagens,
        outro=outro,
        sala=sala
    )

# ==================================================
# ACESSO NEGADO
# ==================================================

@app.route("/acesso-negado")
def acesso_negado():
    return render_template("acesso_negado.html")

# ==================================================
# HELPERS - ESCALA (CORRIGIDOS + FALLBACKS)
# ==================================================

def parse_date_yyyy_mm_dd(s: str):
    try:
        y, m, d = s.split("-")
        return date(int(y), int(m), int(d))
    except Exception:
        return None

def month_range(ano: int, mes: int):
    first_day = date(ano, mes, 1)
    last_day = date(ano, mes, calendar.monthrange(ano, mes)[1])
    return first_day, last_day

def generate_items_for_funcionario(func: Funcionario, escala_mes: EscalaMes, ano: int, mes: int):
    """
    Gera EscalaItem para 1 funcionário no mês:
    - SEG_SEX: seg-sex 08:00-17:00, sáb/dom FOLGA
    - PLANTONISTA_24_96: 24h a cada 5 dias (1 plantão + 4 folgas) baseado em plantao_base
    """
    inicio_mes, fim_mes = month_range(ano, mes)

    EscalaItem.query.filter_by(
        escala_mes_id=escala_mes.id,
        funcionario_id=func.id
    ).delete()

    escala_tipo = (func.escala_tipo or "SEG_SEX").strip().upper()

    # ✅ fallback para imports antigos
    if escala_tipo in ("DIURNO", "DIARIO"):
        escala_tipo = "SEG_SEX"

    if escala_tipo == "SEG_SEX":
        d = inicio_mes
        while d <= fim_mes:
            if d.weekday() <= 4:
                ini = datetime.combine(d, time(8, 0))
                fim = datetime.combine(d, time(17, 0))
                tipo = "EXPEDIENTE"
            else:
                ini = datetime.combine(d, time(0, 0))
                fim = datetime.combine(d, time(23, 59))
                tipo = "FOLGA"

            db.session.add(EscalaItem(
                escala_mes_id=escala_mes.id,
                funcionario_id=func.id,
                inicio=ini,
                fim=fim,
                tipo=tipo,
                observacao=None
            ))
            d += timedelta(days=1)
        return

    if escala_tipo == "PLANTONISTA_24_96":
        base = parse_date_yyyy_mm_dd(func.plantao_base) if func.plantao_base else None
        if not base:
            return

        start_hour = time(7, 0)

        d = inicio_mes
        while d <= fim_mes:
            delta_days = (d - base).days

            if delta_days >= 0 and (delta_days % 5 == 0):
                ini = datetime.combine(d, start_hour)
                fim = ini + timedelta(hours=24)
                tipo = "PLANTAO_24H"
            else:
                ini = datetime.combine(d, time(0, 0))
                fim = datetime.combine(d, time(23, 59))
                tipo = "FOLGA"

            db.session.add(EscalaItem(
                escala_mes_id=escala_mes.id,
                funcionario_id=func.id,
                inicio=ini,
                fim=fim,
                tipo=tipo,
                observacao=None
            ))
            d += timedelta(days=1)
        return

    return

# ==================================================
# ADMIN - ESCALAS
# ==================================================

@app.route("/admin/escalas")
@login_required
@direcao_required
def admin_escalas():
    escalas = EscalaMes.query.order_by(EscalaMes.ano.desc(), EscalaMes.mes.desc()).all()
    hoje = datetime.now()

    return render_template(
        "admin/escalas.html",
        escalas=escalas,
        default_ano=hoje.year,
        default_mes=hoje.month
    )

@app.route("/admin/escalas/gerar", methods=["POST"])
@login_required
@direcao_required
def admin_escalas_gerar():
    ano = int(request.form.get("ano"))
    mes = int(request.form.get("mes"))

    setor_raw = (request.form.get("setor") or "").strip()
    setor = setor_raw or None

    escala_mes = EscalaMes.query.filter_by(ano=ano, mes=mes, setor=setor).first()
    if not escala_mes:
        escala_mes = EscalaMes(
            ano=ano,
            mes=mes,
            setor=setor,
            criado_por_id=session.get("user_id")
        )
        db.session.add(escala_mes)
        db.session.commit()

    q = Funcionario.query.filter_by(status="Ativo")
    if setor:
        q = q.filter(Funcionario.setor == setor)

    funcionarios = q.order_by(Funcionario.nome).all()

    for f in funcionarios:
        generate_items_for_funcionario(f, escala_mes, ano, mes)

    db.session.commit()
    return redirect(url_for("admin_escala_mes", escala_mes_id=escala_mes.id))

@app.route("/admin/escalas/<int:escala_mes_id>")
@login_required
@direcao_required
def admin_escala_mes(escala_mes_id):
    escala = EscalaMes.query.get_or_404(escala_mes_id)

    itens = (
        EscalaItem.query
        .filter_by(escala_mes_id=escala.id)
        .order_by(EscalaItem.funcionario_id.asc(), EscalaItem.inicio.asc())
        .all()
    )

    por_func = {}
    funcionarios_ids = sorted(set([i.funcionario_id for i in itens]))

    funcionarios = (
        Funcionario.query
        .filter(Funcionario.id.in_(funcionarios_ids))
        .order_by(Funcionario.nome)
        .all()
    )

    func_map = {f.id: f for f in funcionarios}

    for it in itens:
        por_func.setdefault(it.funcionario_id, []).append(it)

    dias_no_mes = calendar.monthrange(escala.ano, escala.mes)[1]
    dias = list(range(1, dias_no_mes + 1))

    return render_template(
        "admin/escala_mes.html",
        escala=escala,
        por_func=por_func,
        func_map=func_map,
        dias=dias
    )

# ==================================================
# FUNCIONÁRIO - ESCALAS (VISUALIZAÇÃO / PDF)
# ==================================================

@app.route("/escalas")
@login_required
@funcionario_required
def escalas_view():
    hoje = datetime.now()
    ano = request.args.get("ano", "").strip()
    mes = request.args.get("mes", "").strip()
    setor = request.args.get("setor", "").strip()

    query = EscalaMes.query

    if ano.isdigit():
        query = query.filter(EscalaMes.ano == int(ano))
    if mes.isdigit():
        query = query.filter(EscalaMes.mes == int(mes))
    if setor:
        if setor == "__TODOS__":
            query = query.filter(EscalaMes.setor.is_(None))
        else:
            query = query.filter(EscalaMes.setor == setor)

    escalas = query.order_by(EscalaMes.ano.desc(), EscalaMes.mes.desc(), EscalaMes.setor.asc()).all()

    setores_db = [
        s[0] for s in db.session.query(EscalaMes.setor)
        .distinct()
        .order_by(EscalaMes.setor.asc())
        .all()
        if s[0]
    ]

    return render_template(
        "escalas_view.html",
        escalas=escalas,
        setores=setores_db,
        default_ano=hoje.year,
        default_mes=hoje.month,
        filtros={"ano": ano, "mes": mes, "setor": setor}
    )

@app.route("/escalas/<int:escala_mes_id>")
@login_required
@funcionario_required
def escala_mes_view(escala_mes_id):
    escala = EscalaMes.query.get_or_404(escala_mes_id)

    itens = (
        EscalaItem.query
        .filter_by(escala_mes_id=escala.id)
        .order_by(EscalaItem.funcionario_id.asc(), EscalaItem.inicio.asc())
        .all()
    )

    por_func = {}
    funcionarios_ids = sorted(set([i.funcionario_id for i in itens]))

    funcionarios = (
        Funcionario.query
        .filter(Funcionario.id.in_(funcionarios_ids))
        .order_by(Funcionario.nome)
        .all()
    )

    func_map = {f.id: f for f in funcionarios}
    for it in itens:
        por_func.setdefault(it.funcionario_id, []).append(it)

    dias_no_mes = calendar.monthrange(escala.ano, escala.mes)[1]
    dias = list(range(1, dias_no_mes + 1))

    return render_template(
        "escala_mes_view.html",
        escala=escala,
        por_func=por_func,
        func_map=func_map,
        dias=dias
    )

@app.route("/escalas/<int:escala_mes_id>/pdf")
@login_required
@funcionario_required
def escala_mes_pdf(escala_mes_id):
    escala = EscalaMes.query.get_or_404(escala_mes_id)

    itens = (
        EscalaItem.query
        .filter_by(escala_mes_id=escala.id)
        .order_by(EscalaItem.funcionario_id.asc(), EscalaItem.inicio.asc())
        .all()
    )

    por_func = {}
    funcionarios_ids = sorted(set([i.funcionario_id for i in itens]))

    funcionarios = (
        Funcionario.query
        .filter(Funcionario.id.in_(funcionarios_ids))
        .order_by(Funcionario.nome)
        .all()
    )
    func_map = {f.id: f for f in funcionarios}

    for it in itens:
        por_func.setdefault(it.funcionario_id, []).append(it)

    dias_no_mes = calendar.monthrange(escala.ano, escala.mes)[1]
    dias = list(range(1, dias_no_mes + 1))

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    w, h = A4

    title = f"Escala {escala.mes:02d}/{escala.ano} — {escala.setor or 'Todos'}"

    y = h - 2 * cm
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(w / 2, y, "Hospital Municipal da Mulher")
    y -= 18
    c.setFont("Helvetica", 11)
    c.drawCentredString(w / 2, y, title)
    y -= 18

    c.setFont("Helvetica", 9)
    c.drawString(2 * cm, y, f"Emitido em: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    y -= 12
    c.line(2 * cm, y, w - 2 * cm, y)
    y -= 16

    col_name_w = 6.2 * cm
    block_size = 15
    day_blocks = [dias[i:i+block_size] for i in range(0, len(dias), block_size)]
    col_day_w = (w - 2*cm - 2*cm - col_name_w) / block_size

    def draw_header(block_days, y_):
        c.setFont("Helvetica-Bold", 9)
        c.drawString(2 * cm, y_, "Funcionário")
        x = 2 * cm + col_name_w
        for d in block_days:
            c.drawString(x + 2, y_, f"{d:02d}")
            x += col_day_w
        y_ -= 12
        c.setFont("Helvetica", 9)
        return y_

    def tipo_label(tipo):
        if tipo == "EXPEDIENTE": return "EXP"
        if tipo == "FOLGA": return "F"
        if tipo == "PLANTAO_24H": return "24H"
        return "-"

    ordered_ids = sorted(func_map.keys(), key=lambda fid: (func_map[fid].nome or "").lower())

    for block_days in day_blocks:
        if y < 4 * cm:
            c.showPage()
            y = h - 2 * cm

        y = draw_header(block_days, y)

        for fid in ordered_ids:
            f = func_map.get(fid)
            itens_f = por_func.get(fid, [])

            dia_tipo = {}
            for it in itens_f:
                dia_tipo[int(it.inicio.strftime("%d"))] = it.tipo

            nome = (f.nome if f else "Funcionário")
            if len(nome) > 35:
                nome = nome[:35] + "..."

            c.drawString(2 * cm, y, nome)

            x = 2 * cm + col_name_w
            for d in block_days:
                c.drawString(x + 6, y, tipo_label(dia_tipo.get(d)))
                x += col_day_w

            y -= 12
            if y < 2.5 * cm:
                c.showPage()
                y = h - 2 * cm
                y = draw_header(block_days, y)

        y -= 10
        c.setStrokeColorRGB(0.85, 0.85, 0.85)
        c.line(2 * cm, y, w - 2 * cm, y)
        c.setStrokeColorRGB(0, 0, 0)
        y -= 14

    c.save()
    buffer.seek(0)

    nome_setor = (escala.setor or "Todos").replace(" ", "_").replace("/", "_")
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"escala_{nome_setor}_{escala.mes:02d}_{escala.ano}.pdf",
        mimetype="application/pdf"
    )

# ==================================================
# ✅ PDF DA ESCALA DO MÊS (ADMIN)
# ==================================================

@app.route("/admin/escalas/<int:escala_mes_id>/pdf")
@login_required
@direcao_required
def admin_escala_mes_pdf(escala_mes_id):
    escala = EscalaMes.query.get_or_404(escala_mes_id)

    itens = (
        EscalaItem.query
        .filter_by(escala_mes_id=escala.id)
        .order_by(EscalaItem.funcionario_id.asc(), EscalaItem.inicio.asc())
        .all()
    )

    por_func = {}
    funcionarios_ids = sorted(set([i.funcionario_id for i in itens]))

    funcionarios = (
        Funcionario.query
        .filter(Funcionario.id.in_(funcionarios_ids))
        .order_by(Funcionario.nome.asc())
        .all()
    )
    func_map = {f.id: f for f in funcionarios}

    for it in itens:
        dia = int(it.inicio.strftime("%d"))
        por_func.setdefault(it.funcionario_id, {})[dia] = it.tipo

    dias_no_mes = calendar.monthrange(escala.ano, escala.mes)[1]
    dias = list(range(1, dias_no_mes + 1))

    def label(tipo):
        if tipo == "EXPEDIENTE":
            return "EXP"
        if tipo == "FOLGA":
            return "F"
        if tipo == "PLANTAO_24H":
            return "24H"
        return "-"

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=landscape(A4))
    W, H = landscape(A4)

    margem_x = 1.2 * cm
    margem_y = 1.0 * cm

    y = H - 1.2 * cm
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(margem_x, y, "Hospital Municipal da Mulher")
    y -= 0.6 * cm

    pdf.setFont("Helvetica", 11)
    titulo = f"Escala {escala.mes:02d}/{escala.ano}  |  Setor: {escala.setor or 'Todos'}"
    pdf.drawString(margem_x, y, titulo)
    y -= 0.6 * cm

    pdf.setFont("Helvetica", 9)
    pdf.drawString(margem_x, y, f"Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    y -= 0.5 * cm

    pdf.setFont("Helvetica", 9)
    pdf.drawString(margem_x, y, "Legenda: EXP=Expediente | F=Folga | 24H=Plantão 24h")
    y -= 0.7 * cm

    col_nome_w = 7.0 * cm
    col_dia_w = 0.55 * cm
    row_h = 0.55 * cm

    def desenhar_cabecalho_tabela(y_base):
        pdf.setFont("Helvetica-Bold", 8)
        x = margem_x
        pdf.rect(x, y_base - row_h + 2, col_nome_w, row_h, stroke=1, fill=0)
        pdf.drawString(x + 2, y_base - row_h + 4, "Funcionário")
        x += col_nome_w

        for d in dias:
            pdf.rect(x, y_base - row_h + 2, col_dia_w, row_h, stroke=1, fill=0)
            pdf.drawCentredString(x + col_dia_w / 2, y_base - row_h + 4, f"{d:02d}")
            x += col_dia_w

    def nova_pagina():
        nonlocal y
        pdf.showPage()

        y_top = H - 1.2 * cm
        pdf.setFont("Helvetica-Bold", 14)
        pdf.drawString(margem_x, y_top, "Hospital Municipal da Mulher")

        pdf.setFont("Helvetica", 11)
        pdf.drawString(margem_x, y_top - 0.6 * cm, titulo)

        pdf.setFont("Helvetica", 9)
        pdf.drawString(margem_x, y_top - 1.1 * cm, "Legenda: EXP=Expediente | F=Folga | 24H=Plantão 24h")

        y = y_top - 1.8 * cm
        desenhar_cabecalho_tabela(y)
        y -= row_h
        pdf.setFont("Helvetica", 8)

    desenhar_cabecalho_tabela(y)
    y -= row_h
    pdf.setFont("Helvetica", 8)

    for f in funcionarios:
        if y < margem_y + 2 * cm:
            nova_pagina()

        x = margem_x
        nome = (f.nome or "Funcionário")
        if len(nome) > 42:
            nome = nome[:42] + "..."

        pdf.rect(x, y - row_h + 2, col_nome_w, row_h, stroke=1, fill=0)
        pdf.drawString(x + 2, y - row_h + 4, nome)
        x += col_nome_w

        mapa = por_func.get(f.id, {})
        for d in dias:
            lab = label(mapa.get(d))
            pdf.rect(x, y - row_h + 2, col_dia_w, row_h, stroke=1, fill=0)
            pdf.drawCentredString(x + col_dia_w / 2, y - row_h + 4, lab)
            x += col_dia_w

        y -= row_h

    pdf.save()
    buffer.seek(0)

    nome_arquivo = f"escala_{escala.mes:02d}_{escala.ano}_{(escala.setor or 'TODOS').replace(' ', '_')}.pdf"

    return send_file(
        buffer,
        as_attachment=True,
        download_name=nome_arquivo,
        mimetype="application/pdf"
    )

@app.route("/admin/escalas/<int:escala_mes_id>/excluir", methods=["POST"])
@login_required
@direcao_required
def admin_excluir_escala(escala_mes_id):
    escala = EscalaMes.query.get_or_404(escala_mes_id)
    db.session.delete(escala)
    db.session.commit()
    return redirect(url_for("admin_escalas"))

# ==================================================
# ADMIN - VER / EDITAR / RESETAR / EXCLUIR
# ==================================================

@app.route("/admin/funcionario/<int:id>")
@login_required
@direcao_required
def admin_ver_funcionario(id):
    funcionario = Funcionario.query.get_or_404(id)
    return render_template("admin/ver_funcionario.html", funcionario=funcionario)

@app.route("/admin/funcionario/<int:id>/editar", methods=["GET", "POST"])
@login_required
@direcao_required
def admin_editar_funcionario(id):
    funcionario = Funcionario.query.get_or_404(id)

    if request.method == "POST":
        funcionario.nome = request.form["nome"]
        funcionario.cpf = request.form["cpf"]
        funcionario.funcao = request.form["funcao"]

        funcionario.setor = request.form.get("setor")

        funcionario.telefone = request.form["telefone"]
        funcionario.email = request.form["email"]
        funcionario.status = request.form["status"]

        funcionario.escala_tipo = request.form.get("escala_tipo", funcionario.escala_tipo)
        funcionario.plantao_base = request.form.get("plantao_base") or None

        db.session.commit()
        return redirect(url_for("admin_ver_funcionario", id=id))

    return render_template("admin/editar_funcionario.html", funcionario=funcionario)

@app.route("/admin/funcionario/<int:id>/resetar-senha", methods=["GET", "POST"])
@login_required
@direcao_required
def admin_resetar_senha(id):
    funcionario = Funcionario.query.get_or_404(id)

    if request.method == "POST":
        funcionario.senha = request.form["senha"]
        db.session.commit()
        return redirect(url_for("admin_ver_funcionario", id=id))

    return render_template("admin/resetar_senha.html", funcionario=funcionario)

@app.route("/admin/funcionario/<int:id>/excluir", methods=["POST"])
@login_required
@direcao_required
def admin_excluir_funcionario(id):
    user = Funcionario.query.get(session["user_id"])
    funcionario = Funcionario.query.get_or_404(id)

    if funcionario.id == user.id:
        return redirect(url_for("admin_funcionarios"))

    db.session.delete(funcionario)
    db.session.commit()
    return redirect(url_for("admin_funcionarios"))

# ==================================================
# ✅ EDITAR DIA DA ESCALA (CÉLULA CLICÁVEL)
# ==================================================

@app.route("/admin/escalas/<int:escala_mes_id>/editar-dia", methods=["POST"])
@login_required
@direcao_required
def admin_escala_editar_dia(escala_mes_id):
    escala = EscalaMes.query.get_or_404(escala_mes_id)

    funcionario_id = int(request.form.get("funcionario_id"))
    dia = int(request.form.get("dia"))
    novo_tipo = (request.form.get("tipo") or "").strip()

    TIPOS_VALIDOS = {"EXPEDIENTE", "FOLGA", "PLANTAO_24H"}
    if novo_tipo not in TIPOS_VALIDOS:
        return {"ok": False, "error": "Tipo inválido"}, 400

    d = date(escala.ano, escala.mes, dia)
    inicio_dia = datetime.combine(d, time(0, 0))
    fim_dia = inicio_dia + timedelta(days=1)

    item = (
        EscalaItem.query
        .filter(EscalaItem.escala_mes_id == escala.id)
        .filter(EscalaItem.funcionario_id == funcionario_id)
        .filter(EscalaItem.inicio >= inicio_dia)
        .filter(EscalaItem.inicio < fim_dia)
        .first()
    )

    if not item:
        item = EscalaItem(
            escala_mes_id=escala.id,
            funcionario_id=funcionario_id,
            inicio=inicio_dia,
            fim=inicio_dia + timedelta(hours=1),
            tipo="FOLGA",
            observacao=None
        )
        db.session.add(item)

    if novo_tipo == "EXPEDIENTE":
        item.inicio = datetime.combine(d, time(8, 0))
        item.fim = datetime.combine(d, time(17, 0))
    elif novo_tipo == "FOLGA":
        item.inicio = datetime.combine(d, time(0, 0))
        item.fim = datetime.combine(d, time(23, 59))
    elif novo_tipo == "PLANTAO_24H":
        item.inicio = datetime.combine(d, time(7, 0))
        item.fim = item.inicio + timedelta(hours=24)

    item.tipo = novo_tipo
    db.session.commit()

    label = "-"
    if novo_tipo == "EXPEDIENTE":
        label = "EXP"
    elif novo_tipo == "FOLGA":
        label = "F"
    elif novo_tipo == "PLANTAO_24H":
        label = "24H"

    return {"ok": True, "tipo": novo_tipo, "label": label}

# ==================================================
# IMPORTAR FUNCIONÁRIOS (CSV)
# ==================================================
import pandas as pd
import re
import unicodedata

def _norm_col(s: str) -> str:
    if s is None:
        return ""
    s = str(s).strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^a-z0-9_]", "", s)
    return s

def _only_digits(s: str) -> str:
    return re.sub(r"\D+", "", str(s or ""))

def _pick(row, keys_norm, mapping, default=""):
    for k in mapping:
        if k in keys_norm:
            return row.get(keys_norm[k], default)
    return default

@app.route("/admin/importar-funcionarios")
@login_required
@direcao_required
def importar_funcionarios():
    caminho = "imports/funcionarios.csv"
    if not os.path.exists(caminho):
        return "Arquivo não encontrado em imports/funcionarios.csv"

    try:
        df = pd.read_csv(caminho, sep=None, engine="python", dtype=str, encoding="utf-8-sig")
    except Exception:
        df = pd.read_csv(caminho, sep=";", dtype=str, encoding="utf-8-sig")

    if df.empty:
        return "CSV está vazio."

    keys_norm = {_norm_col(c): c for c in df.columns}

    MAP_CPF   = ["cpf", "cpf_do_funcionario", "cpf_funcionario", "documento", "cpf_numero"]
    MAP_NOME  = ["nome_completo", "nome", "funcionario", "servidor"]
    MAP_CARGO = ["cargo", "funcao", "ocupacao"]
    MAP_SETOR = ["setor_de_trabalho", "setor", "lotacao", "setor_trabalho"]
    MAP_TEL   = ["telefone", "telefone1", "celular", "contato", "telefone_whatsapp"]
    MAP_VINC  = ["vinculo", "vinculo_", "tipo_vinculo", "tipo_de_vinculo"]

    adicionados = 0
    duplicados = 0
    invalidos_cpf = 0
    sem_cpf = 0
    erros_exemplos = []

    for idx, row in df.iterrows():
        raw_cpf = _pick(row, keys_norm, MAP_CPF, default="")
        cpf = _only_digits(raw_cpf)

        if cpf.endswith("0") and ".0" in str(raw_cpf):
            cpf = _only_digits(str(raw_cpf).replace(".0", ""))

        if not cpf:
            sem_cpf += 1
            if len(erros_exemplos) < 5:
                erros_exemplos.append(f"Linha {idx+2}: sem CPF")
            continue

        if len(cpf) != 11:
            invalidos_cpf += 1
            if len(erros_exemplos) < 5:
                erros_exemplos.append(f"Linha {idx+2}: CPF inválido ({raw_cpf})")
            continue

        existe = Funcionario.query.filter_by(cpf=cpf).first()
        if existe:
            duplicados += 1
            continue

        nome = str(_pick(row, keys_norm, MAP_NOME, default="")).strip()
        cargo = str(_pick(row, keys_norm, MAP_CARGO, default="")).strip()
        setor = str(_pick(row, keys_norm, MAP_SETOR, default="")).strip()
        telefone = str(_pick(row, keys_norm, MAP_TEL, default="")).strip()
        vinculo = str(_pick(row, keys_norm, MAP_VINC, default="")).strip()

        cargo_up = cargo.upper()

        # ✅ escala automática
        if "MEDICO" in cargo_up or "MÉDICO" in cargo_up or "PLANT" in cargo_up:
            escala = "PLANTONISTA_24_96"
            plantao_base = date.today().strftime("%Y-%m-%d")
        else:
            escala = "SEG_SEX"
            plantao_base = None

        funcionario = Funcionario(
            nome=nome if nome else "SEM NOME",
            cpf=cpf,
            senha="1234",
            telefone=telefone,
            email=None,
            matricula=None,
            setor=setor if setor else None,
            cargo=cargo if cargo else None,
            tipo_vinculo=vinculo if vinculo else None,
            nascimento=None,
            funcao="Funcionário",
            status="Ativo",
            escala_tipo=escala,
            plantao_base=plantao_base
        )

        db.session.add(funcionario)
        adicionados += 1

    db.session.commit()

    colunas_detectadas = ", ".join(list(df.columns))

    msg = (
        f"{adicionados} funcionários importados com sucesso!<br><br>"
        f"<b>Resumo:</b><br>"
        f"- Duplicados (CPF já existia): {duplicados}<br>"
        f"- CPF inválido: {invalidos_cpf}<br>"
        f"- Sem CPF: {sem_cpf}<br><br>"
        f"<b>Colunas detectadas no CSV:</b><br>{colunas_detectadas}"
    )

    if erros_exemplos:
        msg += "<br><br><b>Exemplos de linhas ignoradas:</b><br>" + "<br>".join(erros_exemplos)

    return msg

# ==================================================
# START
# ==================================================

if __name__ == "__main__":
    # ✅ Local: roda em 0.0.0.0 para testar na rede também
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
