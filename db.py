"""Banco de dados do sistema de pedidos (SQLite local).

Arquivo: pedidos.sqlite3 na mesma pasta (ou caminho em PEDIDOS_DB).
Tudo que é gravado fica rastreável na tabela `auditoria`.
"""

import json
import os
import re
import sqlite3
from datetime import datetime

from flask import g, has_request_context, session

PASTA = os.path.dirname(os.path.abspath(__file__))
CAMINHO = (os.environ.get("PEDIDOS_DB") or os.path.join(PASTA, "pedidos.sqlite3")).strip()

SCHEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    senha_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS estudos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo TEXT NOT NULL UNIQUE,          -- ex.: MCLA-158-CL02
    nome TEXT,                            -- ex.: Petosemtamabe
    patrocinador TEXT,
    criado_em TEXT NOT NULL
);

-- cada versão de protocolo é um registro; só um 'validado' é usado nos pedidos
CREATE TABLE IF NOT EXISTS protocolos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    estudo_id INTEGER NOT NULL REFERENCES estudos(id),
    versao TEXT NOT NULL,
    arquivo_nome TEXT,
    arquivo BLOB,
    sha256 TEXT,
    paginas INTEGER,
    status TEXT NOT NULL DEFAULT 'rascunho',   -- rascunho | validado | substituido
    validado_por TEXT,
    validado_em TEXT,
    criado_por TEXT,
    criado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS protocolo_paginas (
    protocolo_id INTEGER NOT NULL REFERENCES protocolos(id),
    pagina INTEGER NOT NULL,
    texto TEXT,
    PRIMARY KEY (protocolo_id, pagina)
);

-- visitas do cronograma (Schedule of Activities)
-- ciclo_de/ciclo_ate NULL = visita fora de ciclo (Triagem, Fim de tratamento...)
-- ciclo_ate NULL com ciclo_de preenchido = "do ciclo X em diante"
CREATE TABLE IF NOT EXISTS visitas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    protocolo_id INTEGER NOT NULL REFERENCES protocolos(id),
    nome TEXT NOT NULL,                  -- ex.: D1, D15, Triagem, EOT
    ciclo_de INTEGER,
    ciclo_ate INTEGER,
    janela TEXT,                         -- ex.: ±3 dias
    ordem INTEGER NOT NULL DEFAULT 0,
    observacao TEXT
);

-- itens (exames/procedimentos) previstos no protocolo
CREATE TABLE IF NOT EXISTS itens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    protocolo_id INTEGER NOT NULL REFERENCES protocolos(id),
    categoria TEXT NOT NULL,            -- laboratorial | imagem | procedimento | outro
    grupo TEXT,                         -- ex.: HEMATOLOGIA – HEMOGRAMA COMPLETO
    nome TEXT NOT NULL,
    pagina_ref TEXT,                    -- página(s) do protocolo de onde saiu
    trecho_ref TEXT,
    origem TEXT NOT NULL DEFAULT 'manual',  -- manual | ia | sugestao
    ordem INTEGER NOT NULL DEFAULT 0
);

-- matriz visita × item
CREATE TABLE IF NOT EXISTS visita_itens (
    visita_id INTEGER NOT NULL REFERENCES visitas(id),
    item_id INTEGER NOT NULL REFERENCES itens(id),
    nota TEXT,                          -- ex.: "se clinicamente indicado"
    PRIMARY KEY (visita_id, item_id)
);

CREATE TABLE IF NOT EXISTS modelos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    titulo TEXT NOT NULL DEFAULT 'SOLICITAÇÃO DE EXAMES – PESQUISA CLÍNICA',
    instituicao TEXT,                   -- ex.: ONCOCLÍNICAS  |  CPO/SP
    endereco TEXT,
    medico TEXT,                        -- ex.: Dra. Nathalia Gimenes Afonso
    medico_rotulo TEXT DEFAULT 'Médica solicitante',
    crm TEXT,                           -- ex.: CRM/SP 186752
    cor_destaque TEXT DEFAULT '#008080',
    cor_texto TEXT DEFAULT '#1B3742',
    cor_fundo TEXT DEFAULT '#EAF4F3',
    cabecalho_img BLOB,
    cabecalho_mime TEXT,
    arquivo_original BLOB,
    arquivo_nome TEXT,
    rodape TEXT,
    padrao INTEGER NOT NULL DEFAULT 0,
    criado_em TEXT NOT NULL
);

-- médicos solicitantes (assinam o pedido)
CREATE TABLE IF NOT EXISTS medicos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,                 -- ex.: Dra. Nathalia Gimenes Afonso
    crm TEXT,                           -- ex.: CRM/SP 186752
    rotulo TEXT DEFAULT 'Médica solicitante',
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pacientes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    nascimento TEXT,                    -- ISO yyyy-mm-dd
    cpf TEXT,
    participante TEXT,                  -- nº do participante no estudo
    estudo_id INTEGER REFERENCES estudos(id),
    telefone TEXT,
    medico TEXT,
    observacao TEXT,
    ativo INTEGER NOT NULL DEFAULT 1,
    importacao_id INTEGER,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT
);

CREATE TABLE IF NOT EXISTS importacoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    arquivo_nome TEXT,
    mapeamento TEXT,
    criados INTEGER,
    atualizados INTEGER,
    ignorados INTEGER,
    usuario TEXT,
    criado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pedidos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paciente_id INTEGER NOT NULL REFERENCES pacientes(id),
    protocolo_id INTEGER NOT NULL REFERENCES protocolos(id),
    visita_id INTEGER NOT NULL REFERENCES visitas(id),
    modelo_id INTEGER REFERENCES modelos(id),
    ciclo INTEGER,
    rotulo TEXT,                        -- ex.: C10D15
    tipo TEXT,                          -- ex.: PEDIDO LABORATORIAL
    data_coleta TEXT,
    categorias TEXT,                    -- categorias impressas neste pedido (ex.: laboratorial,imagem)
    itens_json TEXT NOT NULL,          -- itens finais (com origem protocolo/manual)
    removidos_json TEXT,                -- itens do protocolo desmarcados + justificativa
    observacao TEXT,
    status TEXT NOT NULL DEFAULT 'rascunho',  -- rascunho | emitido | cancelado
    criado_por TEXT,
    criado_em TEXT NOT NULL,
    emitido_por TEXT,
    emitido_em TEXT,
    pdf BLOB,
    pdf_sha256 TEXT,
    cancelado_motivo TEXT
);

CREATE TABLE IF NOT EXISTS auditoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quando TEXT NOT NULL,
    usuario TEXT,
    acao TEXT NOT NULL,
    entidade TEXT,
    entidade_id INTEGER,
    detalhes TEXT
);
"""


def agora():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip()  # no Render: Postgres. Sem ela: SQLite local.
_schema_ok = False


def conectar(caminho=None):
    global _schema_ok
    if DATABASE_URL and caminho is None:
        conn = PgConn(DATABASE_URL)
        if not _schema_ok:
            conn.criar_schema()
            migrar(conn)
            _schema_ok = True
        return conn
    conn = sqlite3.connect(caminho or CAMINHO)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    migrar(conn)
    return conn


# ---------------------------------------------------------------- Postgres (mesma interface do sqlite3)

TABELAS_COM_ID = {"usuarios", "estudos", "protocolos", "visitas", "itens", "modelos", "medicos", "pacientes",
                  "importacoes", "pedidos", "auditoria"}


class Linha(dict):
    """Aceita linha["coluna"] e linha[0], como sqlite3.Row."""

    def __getitem__(self, k):
        if isinstance(k, int):
            return list(self.values())[k]
        return dict.__getitem__(self, k)

    def keys(self):
        return list(dict.keys(self))


class _Cursor:
    def __init__(self, cur, lastrowid=None):
        self._cur, self.lastrowid = cur, lastrowid
        self._cols = [d[0] for d in cur.description] if cur.description else None
        self._pre = None

    def _linha(self, t):
        return Linha(zip(self._cols, (bytes(v) if isinstance(v, memoryview) else v for v in t)))

    def fetchone(self):
        if self._pre is not None:
            r, self._pre = self._pre, None
            return r
        t = self._cur.fetchone() if self._cols else None
        return self._linha(t) if t is not None else None

    def fetchall(self):
        return [self._linha(t) for t in self._cur.fetchall()] if self._cols else []

    def __iter__(self):
        return iter(self.fetchall())


class PgConn:
    def __init__(self, url):
        import psycopg2

        self._pg = psycopg2
        self.conn = psycopg2.connect(url)

    @staticmethod
    def traduzir(sql):
        sql = sql.replace("%", "%%").replace("?", "%s")
        sql = re.sub(r"\bLIKE\b", "ILIKE", sql)
        if re.match(r"\s*INSERT\s+OR\s+IGNORE", sql, re.I):
            sql = re.sub(r"INSERT\s+OR\s+IGNORE", "INSERT", sql, flags=re.I) + " ON CONFLICT DO NOTHING"
        return sql

    def execute(self, sql, params=()):
        sql_pg = self.traduzir(sql)
        m = re.match(r"\s*INSERT\s+(?:OR\s+IGNORE\s+)?INTO\s+(\w+)", sql, re.I)
        com_id = bool(m and m.group(1) in TABELAS_COM_ID and "RETURNING" not in sql_pg.upper())
        if com_id:
            sql_pg += " RETURNING id"
        cur = self.conn.cursor()
        try:
            cur.execute(sql_pg, tuple(self._param(p) for p in params))
        except Exception:
            self.conn.rollback()
            raise
        if com_id:
            row = cur.fetchone()
            c = _Cursor(cur, row[0] if row else None)
            return c
        return _Cursor(cur)

    def _param(self, p):
        return self._pg.Binary(p) if isinstance(p, (bytes, bytearray)) else p

    def executemany(self, sql, seq):
        for params in seq:
            self.execute(sql, params)

    def criar_schema(self):
        ddl = re.sub(r"--[^\n]*", "", SCHEMA)
        ddl = ddl.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY").replace("BLOB", "BYTEA")
        cur = self.conn.cursor()
        for stmt in [s.strip() for s in ddl.split(";") if s.strip()]:
            cur.execute(stmt)
        self.conn.commit()

    def colunas(self, tabela):
        cur = self.conn.cursor()
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", (tabela,))
        return {r[0] for r in cur.fetchall()}

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()


COLUNAS_NOVAS = {
    "pedidos": {"medico_id": "INTEGER REFERENCES medicos(id)"},
    "modelos": {
        "mostrar_assinatura": "INTEGER NOT NULL DEFAULT 0",   # campo de carimbo/assinatura no fim do pedido
        "instrucao_lab": "TEXT",                               # ex.: COLETAR NO AMAIS – Laboratório Amais
    },
}


def migrar(conn):
    for tabela, cols in COLUNAS_NOVAS.items():
        if isinstance(conn, PgConn):
            existentes = conn.colunas(tabela)
        else:
            existentes = {r["name"] for r in conn.execute(f"PRAGMA table_info({tabela})")}
        for col, tipo in cols.items():
            if col not in existentes:
                conn.execute(f"ALTER TABLE {tabela} ADD COLUMN {col} {tipo}")
    conn.commit()


def db():
    if "db" not in g:
        g.db = conectar()
    return g.db


def fechar(exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def usuario_atual():
    if has_request_context():
        return session.get("usuario_nome") or "?"
    return "sistema"


def registrar(acao, entidade=None, entidade_id=None, detalhes=None, conn=None):
    conn = conn or db()
    if detalhes is not None and not isinstance(detalhes, str):
        detalhes = json.dumps(detalhes, ensure_ascii=False, default=str)
    conn.execute(
        "INSERT INTO auditoria (quando, usuario, acao, entidade, entidade_id, detalhes) VALUES (?,?,?,?,?,?)",
        (agora(), usuario_atual(), acao, entidade, entidade_id, detalhes),
    )
