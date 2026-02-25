from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


# ==================================================
# FUNCIONÁRIO
# ==================================================
class Funcionario(db.Model):
    __tablename__ = "funcionario"

    id = db.Column(db.Integer, primary_key=True)

    nome = db.Column(db.String(120), nullable=False)
    cpf = db.Column(db.String(14), unique=True, nullable=False, index=True)
    senha = db.Column(db.String(50), nullable=False)

    funcao = db.Column(db.String(50), nullable=False)  # "Direção" / "Funcionário"
    status = db.Column(db.String(20), nullable=False, default="Ativo")

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
    escala_tipo = db.Column(db.String(30), default="SEG_SEX", nullable=False)

    # Base do ciclo 24x96 (YYYY-MM-DD): data do PRIMEIRO PLANTÃO.
    plantao_base = db.Column(db.String(10), nullable=True)

    # Relacionamentos úteis
    escalas_itens = db.relationship(
        "EscalaItem",
        backref="funcionario",
        lazy=True,
        cascade="all, delete-orphan"
    )

    # Relacionamentos do chat (evita ambiguidade por ter 2 FKs na Mensagem)
    mensagens_enviadas = db.relationship(
        "Mensagem",
        foreign_keys="Mensagem.remetente_id",
        backref="remetente",
        lazy=True,
        cascade="all, delete-orphan"
    )
    mensagens_recebidas = db.relationship(
        "Mensagem",
        foreign_keys="Mensagem.destinatario_id",
        backref="destinatario",
        lazy=True,
        cascade="all, delete-orphan"
    )


# ==================================================
# MENSAGEM (CHAT)
# ==================================================
class Mensagem(db.Model):
    __tablename__ = "mensagem"

    id = db.Column(db.Integer, primary_key=True)

    remetente_id = db.Column(
        db.Integer,
        db.ForeignKey("funcionario.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    destinatario_id = db.Column(
        db.Integer,
        db.ForeignKey("funcionario.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    texto = db.Column(db.Text)
    arquivo = db.Column(db.String(200), nullable=True)

    data_envio = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    expira_em = db.Column(db.DateTime, nullable=False, index=True)

    __table_args__ = (
        # acelera listagens do chat por par de usuários + ordenação
        db.Index("ix_msg_pair_time", "remetente_id", "destinatario_id", "data_envio"),
    )


# ==================================================
# ESCALA DO MÊS (NOVO)
# ==================================================
class EscalaMes(db.Model):
    __tablename__ = "escala_mes"

    id = db.Column(db.Integer, primary_key=True)

    ano = db.Column(db.Integer, nullable=False)
    mes = db.Column(db.Integer, nullable=False)  # 1-12

    setor = db.Column(db.String(80), nullable=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    criado_por_id = db.Column(
        db.Integer,
        db.ForeignKey("funcionario.id", ondelete="SET NULL"),
        nullable=True
    )

    itens = db.relationship(
        "EscalaItem",
        backref="escala_mes",
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        db.UniqueConstraint("ano", "mes", "setor", name="uq_escala_mes_ano_mes_setor"),
        db.Index("ix_escala_mes_ano_mes_setor", "ano", "mes", "setor"),
    )


# ==================================================
# ITENS DA ESCALA (NOVO)
# ==================================================
class EscalaItem(db.Model):
    __tablename__ = "escala_item"

    id = db.Column(db.Integer, primary_key=True)

    escala_mes_id = db.Column(
        db.Integer,
        db.ForeignKey("escala_mes.id", ondelete="CASCADE"),
        nullable=False
    )
    funcionario_id = db.Column(
        db.Integer,
        db.ForeignKey("funcionario.id", ondelete="CASCADE"),
        nullable=False
    )

    inicio = db.Column(db.DateTime, nullable=False)
    fim = db.Column(db.DateTime, nullable=False)

    # "PLANTAO_24H" / "EXPEDIENTE" / "FOLGA" / "FERIAS" etc
    tipo = db.Column(db.String(30), nullable=False)

    observacao = db.Column(db.String(200), nullable=True)

    __table_args__ = (
        db.Index("ix_escala_item_mes_func", "escala_mes_id", "funcionario_id"),
        db.Index("ix_escala_item_mes_tipo", "escala_mes_id", "tipo"),
    )