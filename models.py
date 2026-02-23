from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


# ==================================================
# FUNCIONÁRIO
# ==================================================
class Funcionario(db.Model):
    __tablename__ = "funcionario"

    id = db.Column(db.Integer, primary_key=True)

    nome = db.Column(db.String(120))
    cpf = db.Column(db.String(14), unique=True)
    senha = db.Column(db.String(50))

    funcao = db.Column(db.String(50))  # "Direção" / "Funcionário"
    status = db.Column(db.String(20))

    telefone = db.Column(db.String(20))
    email = db.Column(db.String(120))

    matricula = db.Column(db.String(30))
    setor = db.Column(db.String(80))
    cargo = db.Column(db.String(80))

    data_admissao = db.Column(db.String(20))
    nascimento = db.Column(db.String(20))

    turno = db.Column(db.String(20))
    tipo_vinculo = db.Column(db.String(30))
    carga_horaria = db.Column(db.String(20))

    observacoes = db.Column(db.Text)

    # =========================
    # ESCALA (NOVO)
    # =========================
    # "PLANTONISTA_24_96" ou "SEG_SEX"
    escala_tipo = db.Column(db.String(30), default="SEG_SEX")

    # Base do ciclo 24x96 (YYYY-MM-DD): data do PRIMEIRO PLANTÃO.
    plantao_base = db.Column(db.String(10), nullable=True)

    # Relacionamentos úteis
    escalas_itens = db.relationship("EscalaItem", backref="funcionario", lazy=True)


# ==================================================
# MENSAGEM (CHAT)
# ==================================================
class Mensagem(db.Model):
    __tablename__ = "mensagem"

    id = db.Column(db.Integer, primary_key=True)

    remetente_id = db.Column(db.Integer, db.ForeignKey("funcionario.id"))
    destinatario_id = db.Column(db.Integer, db.ForeignKey("funcionario.id"))

    texto = db.Column(db.Text)
    arquivo = db.Column(db.String(200), nullable=True)

    data_envio = db.Column(db.DateTime, default=datetime.utcnow)
    expira_em = db.Column(db.DateTime)


# ==================================================
# ESCALA DO MÊS (NOVO)
# ==================================================
class EscalaMes(db.Model):
    __tablename__ = "escala_mes"

    id = db.Column(db.Integer, primary_key=True)

    ano = db.Column(db.Integer, nullable=False)
    mes = db.Column(db.Integer, nullable=False)  # 1-12

    setor = db.Column(db.String(80), nullable=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    criado_por_id = db.Column(db.Integer, db.ForeignKey("funcionario.id"), nullable=True)

    itens = db.relationship("EscalaItem", backref="escala_mes", cascade="all, delete-orphan")

    __table_args__ = (
        db.UniqueConstraint("ano", "mes", "setor", name="uq_escala_mes_ano_mes_setor"),
    )

class EscalaItem(db.Model):
    __tablename__ = "escala_item"

    id = db.Column(db.Integer, primary_key=True)

    escala_mes_id = db.Column(db.Integer, db.ForeignKey("escala_mes.id"), nullable=False)
    funcionario_id = db.Column(db.Integer, db.ForeignKey("funcionario.id"), nullable=False)

    inicio = db.Column(db.DateTime, nullable=False)
    fim = db.Column(db.DateTime, nullable=False)

    # "PLANTAO_24H" / "EXPEDIENTE" / "FOLGA" / "FERIAS" etc
    tipo = db.Column(db.String(30), nullable=False)

    observacao = db.Column(db.String(200), nullable=True)

    __table_args__ = (
        db.Index("ix_escala_item_mes_func", "escala_mes_id", "funcionario_id"),
    )
