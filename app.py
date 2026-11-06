"""Sistema de pedidos médicos de exames para pesquisa clínica (OncoClínicas).

Rodar local:  python app.py   ->  http://127.0.0.1:5050
"""

import hashlib
import json
import re
import os
import secrets
import tempfile
from datetime import date
from functools import wraps

from flask import (Flask, Response, abort, flash, jsonify, redirect, render_template, request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import db as D
import ia_protocolo
import importadores as IMP
from pdf_pedido import CATEGORIAS, CAT_TITULO, data_br, gerar_pdf

PASTA = os.path.dirname(os.path.abspath(__file__))
UPLOAD_TMP = os.path.join(tempfile.gettempdir(), "pedidos_oncoclinicas_upload")
os.makedirs(UPLOAD_TMP, exist_ok=True)


def _segredo():
    s = os.environ.get("SECRET_KEY", "").strip()
    if s:
        return s
    arq = os.path.join(PASTA, ".secret_key")
    if not os.path.exists(arq):
        with open(arq, "w") as f:
            f.write(secrets.token_hex(32))
    return open(arq).read().strip()


app = Flask(__name__)
app.secret_key = _segredo()
app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024
app.teardown_appcontext(D.fechar)
app.jinja_env.filters["data_br"] = data_br
app.jinja_env.globals.update(CATEGORIAS=CATEGORIAS, CAT_TITULO=CAT_TITULO)


# ------------------------------------------------------------------ login

def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not session.get("usuario_id"):
            return redirect(url_for("login", next=request.path))
        return f(*a, **k)
    return w


def admin_required(f):
    @wraps(f)
    @login_required
    def w(*a, **k):
        if not session.get("is_admin"):
            abort(403)
        return f(*a, **k)
    return w


@app.before_request
def _exige_banco_no_servidor():
    """No Render (RENDER=true) sem DATABASE_URL o SQLite seria apagado a cada atualização: bloqueia o uso."""
    if os.environ.get("RENDER") and not D.DATABASE_URL and request.endpoint != "static":
        return ("<div style='font-family:sans-serif;max-width:560px;margin:80px auto;line-height:1.5'>"
                "<h2>Sistema em configuração</h2><p>O banco de dados permanente ainda não foi ligado "
                "(variável <code>DATABASE_URL</code> no Render). Assim que for configurado, esta página "
                "libera o primeiro acesso.</p></div>"), 503


@app.context_processor
def _ctx():
    return {"usuario_nome": session.get("usuario_nome"), "is_admin": session.get("is_admin"),
            "ia_disponivel": ia_protocolo.disponivel()}


@app.route("/login", methods=["GET", "POST"])
def login():
    conn = D.db()
    if not conn.execute("SELECT 1 FROM usuarios LIMIT 1").fetchone():
        return redirect(url_for("primeiro_acesso"))
    if request.method == "POST":
        u = conn.execute("SELECT * FROM usuarios WHERE email=? AND ativo=1",
                         (request.form.get("email", "").strip().lower(),)).fetchone()
        if u and check_password_hash(u["senha_hash"], request.form.get("senha", "")):
            session.clear()
            session.update(usuario_id=u["id"], usuario_nome=u["nome"], is_admin=bool(u["is_admin"]))
            D.registrar("login", "usuario", u["id"])
            conn.commit()
            return redirect(request.args.get("next") or url_for("inicio"))
        flash("E-mail ou senha inválidos.", "erro")
    return render_template("login.html")


@app.route("/primeiro-acesso", methods=["GET", "POST"])
def primeiro_acesso():
    conn = D.db()
    if conn.execute("SELECT 1 FROM usuarios LIMIT 1").fetchone():
        return redirect(url_for("login"))
    if request.method == "POST":
        nome, email, senha = (request.form.get(k, "").strip() for k in ("nome", "email", "senha"))
        if not nome or "@" not in email or len(senha) < 8:
            flash("Preencha nome, e-mail e uma senha com pelo menos 8 caracteres.", "erro")
        else:
            cur = conn.execute("INSERT INTO usuarios (nome,email,senha_hash,is_admin,criado_em) VALUES (?,?,?,1,?)",
                               (nome, email.lower(), generate_password_hash(senha), D.agora()))
            session.update(usuario_id=cur.lastrowid, usuario_nome=nome, is_admin=True)
            D.registrar("criou administrador inicial", "usuario", cur.lastrowid)
            conn.commit()
            return redirect(url_for("inicio"))
    return render_template("primeiro_acesso.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/usuarios", methods=["GET", "POST"])
@admin_required
def usuarios():
    conn = D.db()
    if request.method == "POST":
        acao = request.form.get("acao")
        if acao == "criar":
            nome, email, senha = (request.form.get(k, "").strip() for k in ("nome", "email", "senha"))
            if not nome or "@" not in email or len(senha) < 8:
                flash("Preencha nome, e-mail e senha (mín. 8 caracteres).", "erro")
            elif conn.execute("SELECT 1 FROM usuarios WHERE email=?", (email.lower(),)).fetchone():
                flash("Já existe usuário com esse e-mail.", "erro")
            else:
                cur = conn.execute("INSERT INTO usuarios (nome,email,senha_hash,is_admin,criado_em) VALUES (?,?,?,?,?)",
                                   (nome, email.lower(), generate_password_hash(senha),
                                    1 if request.form.get("is_admin") else 0, D.agora()))
                D.registrar("criou usuário", "usuario", cur.lastrowid, {"nome": nome, "email": email})
                flash("Usuário criado.", "ok")
        elif acao in ("desativar", "ativar"):
            uid = int(request.form["id"])
            if uid == session["usuario_id"]:
                flash("Você não pode desativar o seu próprio usuário.", "erro")
            else:
                conn.execute("UPDATE usuarios SET ativo=? WHERE id=?", (1 if acao == "ativar" else 0, uid))
                D.registrar(f"{acao} usuário", "usuario", uid)
        conn.commit()
        return redirect(url_for("usuarios"))
    lista = conn.execute("SELECT * FROM usuarios ORDER BY nome").fetchall()
    return render_template("usuarios.html", usuarios=lista)


# ------------------------------------------------------------------ início

@app.route("/")
@login_required
def inicio():
    conn = D.db()
    c = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    numeros = {
        "pacientes": c("SELECT COUNT(*) FROM pacientes WHERE ativo=1"),
        "estudos": c("SELECT COUNT(*) FROM estudos"),
        "protocolos_validados": c("SELECT COUNT(*) FROM protocolos WHERE status='validado'"),
        "protocolos_rascunho": c("SELECT COUNT(*) FROM protocolos WHERE status='rascunho'"),
        "pedidos_emitidos": c("SELECT COUNT(*) FROM pedidos WHERE status='emitido'"),
        "pedidos_rascunho": c("SELECT COUNT(*) FROM pedidos WHERE status='rascunho'"),
        "modelos": c("SELECT COUNT(*) FROM modelos"),
    }
    recentes = conn.execute("""SELECT p.*, pa.nome AS paciente, e.codigo AS estudo FROM pedidos p
        JOIN pacientes pa ON pa.id=p.paciente_id JOIN protocolos pr ON pr.id=p.protocolo_id
        JOIN estudos e ON e.id=pr.estudo_id ORDER BY p.id DESC LIMIT 8""").fetchall()
    return render_template("inicio.html", n=numeros, recentes=recentes)


# ------------------------------------------------------------------ estudos

def estudo_por_codigo(conn, codigo, criar=True):
    codigo = (codigo or "").strip()
    if not codigo:
        return None
    r = conn.execute("SELECT id FROM estudos WHERE UPPER(codigo)=UPPER(?)", (codigo,)).fetchone()
    if r:
        return r["id"]
    if not criar:
        return None
    cur = conn.execute("INSERT INTO estudos (codigo, criado_em) VALUES (?,?)", (codigo, D.agora()))
    D.registrar("criou estudo", "estudo", cur.lastrowid, {"codigo": codigo}, conn=conn)
    return cur.lastrowid


@app.route("/estudos", methods=["GET", "POST"])
@login_required
def estudos():
    conn = D.db()
    if request.method == "POST":
        codigo = request.form.get("codigo", "").strip()
        if not codigo:
            flash("Informe o código do estudo.", "erro")
        else:
            eid = request.form.get("id")
            dados = (codigo, request.form.get("nome", "").strip() or None,
                     request.form.get("patrocinador", "").strip() or None)
            try:
                if eid:
                    conn.execute("UPDATE estudos SET codigo=?, nome=?, patrocinador=? WHERE id=?", dados + (int(eid),))
                    D.registrar("editou estudo", "estudo", int(eid), dict(zip(("codigo", "nome", "patrocinador"), dados)))
                else:
                    cur = conn.execute("INSERT INTO estudos (codigo,nome,patrocinador,criado_em) VALUES (?,?,?,?)",
                                       dados + (D.agora(),))
                    D.registrar("criou estudo", "estudo", cur.lastrowid, dict(zip(("codigo", "nome", "patrocinador"), dados)))
                conn.commit()
                flash("Estudo salvo.", "ok")
            except Exception:
                flash("Já existe um estudo com esse código.", "erro")
        return redirect(url_for("estudos"))
    lista = conn.execute("""SELECT e.*,
        (SELECT COUNT(*) FROM pacientes p WHERE p.estudo_id=e.id AND p.ativo=1) AS n_pac
        FROM estudos e ORDER BY e.codigo""").fetchall()
    protos = conn.execute("SELECT id, estudo_id, versao, status, arquivo_nome, validado_em, validado_por, criado_em "
                          "FROM protocolos ORDER BY id DESC").fetchall()
    por_estudo = {}
    for p in protos:
        por_estudo.setdefault(p["estudo_id"], []).append(p)
    return render_template("estudos.html", estudos=lista, protocolos=por_estudo)


# ------------------------------------------------------------------ pacientes

@app.route("/pacientes")
@login_required
def pacientes():
    conn = D.db()
    q = request.args.get("q", "").strip()
    estudo = request.args.get("estudo", "")
    sql = """SELECT p.*, e.codigo AS estudo_codigo FROM pacientes p LEFT JOIN estudos e ON e.id=p.estudo_id
             WHERE p.ativo=1"""
    args = []
    if q:
        sql += " AND (p.nome LIKE ? OR p.participante LIKE ? OR p.cpf LIKE ?)"
        args += [f"%{q}%"] * 3
    if estudo:
        sql += " AND p.estudo_id=?"
        args.append(int(estudo))
    sql += " ORDER BY p.nome LIMIT 500"
    lista = conn.execute(sql, args).fetchall()
    estudos_l = conn.execute("SELECT id, codigo FROM estudos ORDER BY codigo").fetchall()
    return render_template("pacientes.html", pacientes=lista, estudos=estudos_l, q=q, estudo=estudo)


CAMPOS_EDIT = ("nome", "nascimento", "cpf", "participante", "telefone", "medico", "observacao")


@app.route("/pacientes/novo", methods=["GET", "POST"])
@app.route("/pacientes/<int:pid>", methods=["GET", "POST"])
@login_required
def paciente_form(pid=None):
    conn = D.db()
    p = conn.execute("SELECT * FROM pacientes WHERE id=?", (pid,)).fetchone() if pid else None
    if pid and not p:
        abort(404)
    if request.method == "POST":
        if request.form.get("acao") == "arquivar" and p:
            conn.execute("UPDATE pacientes SET ativo=0, atualizado_em=? WHERE id=?", (D.agora(), pid))
            D.registrar("arquivou paciente", "paciente", pid)
            conn.commit()
            flash("Paciente arquivado (os pedidos dele continuam no histórico).", "ok")
            return redirect(url_for("pacientes"))
        dados = {k: (request.form.get(k, "").strip() or None) for k in CAMPOS_EDIT}
        dados["nascimento"] = IMP.parse_data(dados["nascimento"]) if dados["nascimento"] else None
        dados["cpf"] = IMP.formatar_cpf(dados["cpf"]) if dados["cpf"] else None
        est = request.form.get("estudo_id")
        dados["estudo_id"] = int(est) if est else None
        if not dados["nome"]:
            flash("Nome é obrigatório.", "erro")
        else:
            if p:
                conn.execute("UPDATE pacientes SET " + ",".join(f"{k}=?" for k in dados) + ", atualizado_em=? WHERE id=?",
                             list(dados.values()) + [D.agora(), pid])
                mud = {k: [p[k], v] for k, v in dados.items() if p[k] != v}
                D.registrar("editou paciente", "paciente", pid, mud)
            else:
                cur = conn.execute("INSERT INTO pacientes (" + ",".join(dados) + ", criado_em) VALUES (" +
                                   ",".join("?" * len(dados)) + ",?)", list(dados.values()) + [D.agora()])
                pid = cur.lastrowid
                D.registrar("cadastrou paciente", "paciente", pid, dados)
            conn.commit()
            flash("Paciente salvo.", "ok")
            return redirect(url_for("pacientes"))
    estudos_l = conn.execute("SELECT id, codigo, nome FROM estudos ORDER BY codigo").fetchall()
    pedidos_l = conn.execute("SELECT * FROM pedidos WHERE paciente_id=? ORDER BY id DESC", (pid,)).fetchall() if pid else []
    return render_template("paciente_form.html", p=p, estudos=estudos_l, pedidos=pedidos_l)


@app.route("/pacientes/importar", methods=["GET", "POST"])
@login_required
def importar_pacientes():
    conn = D.db()
    estudos_l = conn.execute("SELECT id, codigo FROM estudos ORDER BY codigo").fetchall()
    etapa = request.form.get("etapa")

    if request.method == "POST" and etapa is None:
        arq = request.files.get("arquivo")
        if not arq or not arq.filename:
            flash("Escolha a planilha.", "erro")
            return redirect(url_for("importar_pacientes"))
        conteudo = arq.read()
        try:
            colunas, linhas = IMP.ler_planilha(arq.filename, conteudo)
        except Exception as e:
            flash(f"Não consegui ler a planilha: {e}", "erro")
            return redirect(url_for("importar_pacientes"))
        if not linhas:
            flash("A planilha está vazia.", "erro")
            return redirect(url_for("importar_pacientes"))
        token = secrets.token_hex(12)
        with open(os.path.join(UPLOAD_TMP, token), "wb") as f:
            f.write(conteudo)
        session["import_arquivo"] = {"token": token, "nome": arq.filename}
        return render_template("importar_mapear.html", colunas=colunas, amostra=linhas[:8], total=len(linhas),
                               palpite=IMP.palpite_mapeamento(colunas), campos=IMP.CAMPOS_PACIENTE,
                               estudos=estudos_l, arquivo=arq.filename)

    if request.method == "POST" and etapa == "importar":
        info = session.get("import_arquivo")
        caminho = os.path.join(UPLOAD_TMP, info["token"]) if info else None
        if not caminho or not os.path.exists(caminho):
            flash("Envie a planilha de novo (o arquivo temporário expirou).", "erro")
            return redirect(url_for("importar_pacientes"))
        with open(caminho, "rb") as f:
            colunas, linhas = IMP.ler_planilha(info["nome"], f.read())
        mapa = {c: request.form.get(f"map_{c}") for c, _, _ in IMP.CAMPOS_PACIENTE if request.form.get(f"map_{c}")}
        if "nome" not in mapa:
            flash("Escolha qual coluna é o NOME do paciente.", "erro")
            return redirect(url_for("importar_pacientes"))
        estudo_fixo = request.form.get("estudo_fixo")
        atualizar = request.form.get("atualizar") == "1"
        cur = conn.execute("INSERT INTO importacoes (arquivo_nome, mapeamento, usuario, criado_em) VALUES (?,?,?,?)",
                           (info["nome"], json.dumps({"colunas": mapa, "estudo_fixo": estudo_fixo}, ensure_ascii=False),
                            D.usuario_atual(), D.agora()))
        imp_id = cur.lastrowid
        criados = atualizados = ignorados = 0
        problemas = []
        for n, ln in enumerate(linhas, start=2):
            v = {c: ln.get(col) for c, col in mapa.items()}
            nome = IMP.limpar_texto(v.get("nome"))
            if not nome:
                ignorados += 1
                continue
            reg = {
                "nome": nome,
                "nascimento": IMP.parse_data(v.get("nascimento")),
                "cpf": IMP.formatar_cpf(v.get("cpf")) if v.get("cpf") not in (None, "") else None,
                "participante": IMP.limpar_texto(v.get("participante")),
                "telefone": IMP.limpar_texto(v.get("telefone")),
                "medico": IMP.limpar_texto(v.get("medico")),
                "observacao": IMP.limpar_texto(v.get("observacao")),
            }
            if v.get("nascimento") not in (None, "") and not reg["nascimento"]:
                problemas.append(f"Linha {n} ({nome}): data de nascimento não reconhecida '{v.get('nascimento')}'")
            if estudo_fixo:
                reg["estudo_id"] = int(estudo_fixo)
            else:
                reg["estudo_id"] = estudo_por_codigo(conn, IMP.limpar_texto(v.get("estudo")))
            # duplicado? mesmo participante no mesmo estudo, ou mesmo CPF, ou mesmo nome+nascimento
            existe = None
            if reg["participante"] and reg["estudo_id"]:
                existe = conn.execute("SELECT * FROM pacientes WHERE participante=? AND estudo_id=? AND ativo=1",
                                      (reg["participante"], reg["estudo_id"])).fetchone()
            if not existe and reg["cpf"]:
                existe = conn.execute("SELECT * FROM pacientes WHERE cpf=? AND ativo=1", (reg["cpf"],)).fetchone()
            if not existe and reg["nascimento"]:
                existe = conn.execute("SELECT * FROM pacientes WHERE UPPER(nome)=UPPER(?) AND nascimento=? AND ativo=1",
                                      (nome, reg["nascimento"])).fetchone()
            if existe:
                if not atualizar:
                    ignorados += 1
                    continue
                novos = {k: val for k, val in reg.items() if val not in (None, "") and existe[k] != val}
                if novos:
                    conn.execute("UPDATE pacientes SET " + ",".join(f"{k}=?" for k in novos) + ", atualizado_em=? WHERE id=?",
                                 list(novos.values()) + [D.agora(), existe["id"]])
                    D.registrar("atualizou paciente (importação)", "paciente", existe["id"],
                                {"importacao": imp_id, "campos": {k: [existe[k], val] for k, val in novos.items()}}, conn=conn)
                    atualizados += 1
                else:
                    ignorados += 1
            else:
                reg["importacao_id"] = imp_id
                cur = conn.execute("INSERT INTO pacientes (" + ",".join(reg) + ", criado_em) VALUES (" +
                                   ",".join("?" * len(reg)) + ",?)", list(reg.values()) + [D.agora()])
                criados += 1
        conn.execute("UPDATE importacoes SET criados=?, atualizados=?, ignorados=? WHERE id=?",
                     (criados, atualizados, ignorados, imp_id))
        D.registrar("importou planilha de pacientes", "importacao", imp_id,
                    {"arquivo": info["nome"], "criados": criados, "atualizados": atualizados, "ignorados": ignorados})
        conn.commit()
        try:
            os.remove(caminho)
        except OSError:
            pass
        session.pop("import_arquivo", None)
        return render_template("importar_resultado.html", criados=criados, atualizados=atualizados,
                               ignorados=ignorados, problemas=problemas[:50], arquivo=info["nome"])

    historico = conn.execute("SELECT * FROM importacoes ORDER BY id DESC LIMIT 10").fetchall()
    return render_template("importar.html", historico=historico)


# ------------------------------------------------------------------ protocolos

def protocolo_ou_404(pid):
    p = D.db().execute("""SELECT p.*, e.codigo AS estudo_codigo, e.nome AS estudo_nome FROM protocolos p
                          JOIN estudos e ON e.id=p.estudo_id WHERE p.id=?""", (pid,)).fetchone()
    if not p:
        abort(404)
    return p


def exige_rascunho(p):
    if p["status"] != "rascunho":
        flash("Protocolo validado não pode ser alterado. Crie uma nova versão (botão 'Nova versão a partir desta').", "erro")
        return False
    return True


@app.route("/protocolos/detectar", methods=["POST"])
@login_required
def protocolo_detectar():
    """Lê a capa do PDF e devolve sugestões (versão, código, estudo já cadastrado que bate)."""
    arq = request.files.get("arquivo")
    if not arq or not arq.filename.lower().endswith(".pdf"):
        return jsonify({"erro": "envie um PDF"}), 400
    try:
        d = IMP.detectar_dados_protocolo(arq.read(), arq.filename)
    except Exception as e:
        return jsonify({"erro": f"não consegui ler o PDF: {e}"}), 400
    conn = D.db()
    chaves = [IMP.sem_acento(x) for x in (d.get("codigo"), d.get("titulo_curto"), d.get("nct")) if x]
    nome_arq = IMP.sem_acento(arq.filename)
    for e in conn.execute("SELECT id, codigo, nome FROM estudos").fetchall():
        cod, nome = IMP.sem_acento(e["codigo"]), IMP.sem_acento(e["nome"] or "")
        cod_solto = cod.replace("-", "_")
        if any(k and (k == cod or k.startswith(cod) or cod.startswith(k) or (nome and k == nome)) for k in chaves) \
                or (len(cod) >= 5 and (cod in nome_arq or cod_solto in nome_arq)):
            d["estudo_id"] = e["id"]
            break
    return jsonify(d)


@app.route("/protocolos/novo", methods=["GET", "POST"])
@login_required
def protocolo_novo():
    conn = D.db()
    estudos_l = conn.execute("SELECT id, codigo, nome FROM estudos ORDER BY codigo").fetchall()
    if request.method == "POST":
        arq = request.files.get("arquivo")
        estudo_id = request.form.get("estudo_id")
        versao = request.form.get("versao", "").strip()
        if estudo_id == "novo":
            codigo = request.form.get("novo_codigo", "").strip()
            if not codigo:
                flash("Informe o código do novo estudo.", "erro")
                return redirect(request.url)
            if estudo_por_codigo(conn, codigo, criar=False):
                estudo_id = estudo_por_codigo(conn, codigo, criar=False)
            else:
                cur = conn.execute("INSERT INTO estudos (codigo,nome,patrocinador,criado_em) VALUES (?,?,?,?)",
                                   (codigo, request.form.get("novo_nome", "").strip() or None,
                                    request.form.get("novo_patrocinador", "").strip() or None, D.agora()))
                estudo_id = cur.lastrowid
                D.registrar("criou estudo (ao importar protocolo)", "estudo", estudo_id, {"codigo": codigo})
        if not estudo_id or not versao:
            flash("Escolha o estudo e informe a versão do protocolo.", "erro")
            return redirect(request.url)
        conteudo = arq.read() if arq and arq.filename else None
        info = {"sha256": None, "paginas": []}
        if conteudo:
            if not arq.filename.lower().endswith(".pdf"):
                flash("O protocolo precisa estar em PDF.", "erro")
                return redirect(request.url)
            try:
                info = IMP.ler_protocolo_pdf(conteudo)
            except Exception as e:
                flash(f"Não consegui ler o PDF: {e}", "erro")
                return redirect(request.url)
        cur = conn.execute("""INSERT INTO protocolos (estudo_id, versao, arquivo_nome, arquivo, sha256, paginas,
                              criado_por, criado_em) VALUES (?,?,?,?,?,?,?,?)""",
                           (int(estudo_id), versao, arq.filename if conteudo else None, conteudo, info["sha256"],
                            len(info["paginas"]), D.usuario_atual(), D.agora()))
        pid = cur.lastrowid
        conn.executemany("INSERT INTO protocolo_paginas (protocolo_id, pagina, texto) VALUES (?,?,?)",
                         [(pid, n, t) for n, t in info["paginas"]])
        D.registrar("importou protocolo", "protocolo", pid,
                    {"arquivo": arq.filename if conteudo else None, "versao": versao, "sha256": info["sha256"],
                     "paginas": len(info["paginas"])})
        conn.commit()
        sem_texto = info["paginas"] and sum(len(t.strip()) for _, t in info["paginas"]) < 200
        if sem_texto:
            flash("O PDF parece ser digitalizado (quase sem texto). A busca de termos não vai funcionar; "
                  "cadastre a matriz olhando o PDF.", "erro")
        flash("Protocolo importado. Agora monte o cronograma (visitas × exames) e valide.", "ok")
        return redirect(url_for("protocolo", pid=pid))
    return render_template("protocolo_novo.html", estudos=estudos_l, estudo_sel=request.args.get("estudo"))


def carregar_matriz(conn, pid):
    visitas = conn.execute("SELECT * FROM visitas WHERE protocolo_id=? ORDER BY ordem, id", (pid,)).fetchall()
    ordem_cat = {c: i for i, (c, _) in enumerate(CATEGORIAS)}
    itens = sorted(conn.execute("SELECT * FROM itens WHERE protocolo_id=?", (pid,)).fetchall(),
                   key=lambda i: (ordem_cat.get(i["categoria"], 9), i["ordem"], i["id"]))
    marc = {}
    for r in conn.execute("""SELECT vi.* FROM visita_itens vi JOIN visitas v ON v.id=vi.visita_id
                             WHERE v.protocolo_id=?""", (pid,)):
        marc[(r["visita_id"], r["item_id"])] = r["nota"] or ""
    return visitas, itens, marc


def rotulo_visita(v, ciclo=None):
    nome = v["nome"]
    if v["ciclo_de"] is None:
        return nome
    if ciclo is None:
        if v["ciclo_ate"] is None:
            faixa = f"C{v['ciclo_de']}+"
        elif v["ciclo_ate"] == v["ciclo_de"]:
            faixa = f"C{v['ciclo_de']}"
        else:
            faixa = f"C{v['ciclo_de']}–{v['ciclo_ate']}"
        return f"{faixa} {nome}"
    if nome.upper().startswith("D") and nome[1:].strip().isdigit():
        return f"C{ciclo}{nome.upper().replace(' ', '')}"
    return f"C{ciclo} {nome}"


app.jinja_env.globals["rotulo_visita"] = rotulo_visita


@app.route("/protocolos/<int:pid>")
@login_required
def protocolo(pid):
    conn = D.db()
    p = protocolo_ou_404(pid)
    visitas, itens, marc = carregar_matriz(conn, pid)
    paginas = conn.execute("SELECT pagina, texto FROM protocolo_paginas WHERE protocolo_id=? ORDER BY pagina",
                           (pid,)).fetchall()
    busca = IMP.localizar_termos([(r["pagina"], r["texto"]) for r in paginas]) if paginas else None
    outras = conn.execute("SELECT id, versao, status FROM protocolos WHERE estudo_id=? AND id<>? ORDER BY id DESC",
                          (p["estudo_id"], pid)).fetchall()
    return render_template("protocolo.html", p=p, visitas=visitas, itens=itens, marc=marc, busca=busca,
                           outras=outras, tem_pdf=bool(p["arquivo"]))


@app.route("/protocolos/<int:pid>/arquivo")
@login_required
def protocolo_arquivo(pid):
    p = protocolo_ou_404(pid)
    if not p["arquivo"]:
        abort(404)
    return Response(p["arquivo"], mimetype="application/pdf",
                    headers={"Content-Disposition": f"inline; filename=\"{p['arquivo_nome'] or 'protocolo.pdf'}\""})


@app.route("/protocolos/<int:pid>/pagina/<int:n>")
@login_required
def protocolo_pagina(pid, n):
    r = D.db().execute("SELECT texto FROM protocolo_paginas WHERE protocolo_id=? AND pagina=?", (pid, n)).fetchone()
    return jsonify({"pagina": n, "texto": r["texto"] if r else ""})


@app.route("/protocolos/<int:pid>/visitas", methods=["POST"])
@login_required
def protocolo_visitas(pid):
    conn = D.db()
    p = protocolo_ou_404(pid)
    if not exige_rascunho(p):
        return redirect(url_for("protocolo", pid=pid))
    acao = request.form.get("acao")
    if acao == "excluir":
        vid = int(request.form["id"])
        conn.execute("DELETE FROM visita_itens WHERE visita_id=?", (vid,))
        conn.execute("DELETE FROM visitas WHERE id=? AND protocolo_id=?", (vid, pid))
        D.registrar("excluiu visita", "protocolo", pid, {"visita_id": vid})
    else:
        nome = request.form.get("nome", "").strip()
        if not nome:
            flash("Informe o nome da visita (ex.: Triagem, D1, D15, Fim de tratamento).", "erro")
            return redirect(url_for("protocolo", pid=pid))
        tipo = request.form.get("tipo")  # fora | ciclos
        cde = cate = None
        if tipo == "ciclos":
            try:
                cde = int(request.form.get("ciclo_de") or 1)
                cate = int(request.form["ciclo_ate"]) if request.form.get("ciclo_ate") else None
            except ValueError:
                flash("Ciclo inválido.", "erro")
                return redirect(url_for("protocolo", pid=pid))
            if cate is not None and cate < cde:
                flash("O ciclo final não pode ser menor que o inicial.", "erro")
                return redirect(url_for("protocolo", pid=pid))
        ordem = conn.execute("SELECT COALESCE(MAX(ordem),0)+1 FROM visitas WHERE protocolo_id=?", (pid,)).fetchone()[0]
        cur = conn.execute("INSERT INTO visitas (protocolo_id,nome,ciclo_de,ciclo_ate,janela,ordem) VALUES (?,?,?,?,?,?)",
                           (pid, nome, cde, cate, request.form.get("janela", "").strip() or None, ordem))
        D.registrar("adicionou visita", "protocolo", pid, {"visita_id": cur.lastrowid, "nome": nome, "ciclo_de": cde,
                                                           "ciclo_ate": cate})
    conn.commit()
    return redirect(url_for("protocolo", pid=pid) + "#matriz")


@app.route("/protocolos/<int:pid>/itens", methods=["POST"])
@login_required
def protocolo_itens(pid):
    conn = D.db()
    p = protocolo_ou_404(pid)
    if not exige_rascunho(p):
        return redirect(url_for("protocolo", pid=pid))
    acao = request.form.get("acao")
    if acao == "excluir":
        iid = int(request.form["id"])
        conn.execute("DELETE FROM visita_itens WHERE item_id=?", (iid,))
        conn.execute("DELETE FROM itens WHERE id=? AND protocolo_id=?", (iid, pid))
        D.registrar("excluiu item", "protocolo", pid, {"item_id": iid})
    else:
        nomes = [n.strip() for n in request.form.get("nome", "").replace(";", "\n").split("\n") if n.strip()]
        if not nomes:
            flash("Informe o nome do exame/procedimento.", "erro")
            return redirect(url_for("protocolo", pid=pid))
        cat = request.form.get("categoria", "laboratorial")
        grupo = request.form.get("grupo", "").strip().upper() or None
        ordem = conn.execute("SELECT COALESCE(MAX(ordem),0) FROM itens WHERE protocolo_id=?", (pid,)).fetchone()[0]
        for nome in nomes:
            ordem += 1
            cur = conn.execute("""INSERT INTO itens (protocolo_id,categoria,grupo,nome,pagina_ref,trecho_ref,origem,ordem)
                                  VALUES (?,?,?,?,?,?,?,?)""",
                               (pid, cat, grupo, nome, request.form.get("pagina_ref", "").strip() or None,
                                request.form.get("trecho_ref", "").strip() or None,
                                request.form.get("origem") or "manual", ordem))
            D.registrar("adicionou item", "protocolo", pid, {"item_id": cur.lastrowid, "nome": nome, "categoria": cat,
                                                             "grupo": grupo})
    conn.commit()
    return redirect(url_for("protocolo", pid=pid) + "#itens")


@app.route("/protocolos/<int:pid>/matriz", methods=["POST"])
@login_required
def protocolo_matriz(pid):
    conn = D.db()
    p = protocolo_ou_404(pid)
    if not exige_rascunho(p):
        return redirect(url_for("protocolo", pid=pid))
    visitas, itens, antes = carregar_matriz(conn, pid)
    depois = {}
    for v in visitas:
        for it in itens:
            if request.form.get(f"m_{v['id']}_{it['id']}"):
                depois[(v["id"], it["id"])] = request.form.get(f"n_{v['id']}_{it['id']}", "").strip()
    conn.execute("DELETE FROM visita_itens WHERE visita_id IN (SELECT id FROM visitas WHERE protocolo_id=?)", (pid,))
    conn.executemany("INSERT INTO visita_itens (visita_id,item_id,nota) VALUES (?,?,?)",
                     [(v, i, n or None) for (v, i), n in depois.items()])
    add = [f"{v}:{i}" for k in depois if k not in antes for v, i in [k]]
    rem = [f"{v}:{i}" for k in antes if k not in depois for v, i in [k]]
    if add or rem:
        D.registrar("alterou matriz visita×exame", "protocolo", pid, {"marcou": add, "desmarcou": rem})
    conn.commit()
    flash("Matriz salva.", "ok")
    return redirect(url_for("protocolo", pid=pid) + "#matriz")


@app.route("/protocolos/<int:pid>/validar", methods=["POST"])
@login_required
def protocolo_validar(pid):
    conn = D.db()
    p = protocolo_ou_404(pid)
    if p["status"] != "rascunho":
        return redirect(url_for("protocolo", pid=pid))
    visitas, itens, marc = carregar_matriz(conn, pid)
    if not visitas or not marc:
        flash("Cadastre pelo menos uma visita e marque os exames na matriz antes de validar.", "erro")
        return redirect(url_for("protocolo", pid=pid))
    if not request.form.get("confirmo"):
        flash("Marque a confirmação de que a matriz foi conferida com o protocolo.", "erro")
        return redirect(url_for("protocolo", pid=pid))
    anteriores = conn.execute("SELECT id FROM protocolos WHERE estudo_id=? AND status='validado'", (p["estudo_id"],)).fetchall()
    for a in anteriores:
        conn.execute("UPDATE protocolos SET status='substituido' WHERE id=?", (a["id"],))
        D.registrar("protocolo substituído por nova versão", "protocolo", a["id"], {"nova_versao": pid})
    conn.execute("UPDATE protocolos SET status='validado', validado_por=?, validado_em=? WHERE id=?",
                 (D.usuario_atual(), D.agora(), pid))
    D.registrar("validou protocolo", "protocolo", pid, {"visitas": len(visitas), "itens": len(itens), "marcacoes": len(marc)})
    conn.commit()
    flash("Protocolo validado — já pode ser usado nos pedidos.", "ok")
    return redirect(url_for("protocolo", pid=pid))


@app.route("/protocolos/<int:pid>/nova-versao", methods=["POST"])
@login_required
def protocolo_nova_versao(pid):
    """Copia visitas, itens e matriz para um novo rascunho (ex.: emenda do protocolo)."""
    conn = D.db()
    p = protocolo_ou_404(pid)
    versao = request.form.get("versao", "").strip() or f"{p['versao']} (rev.)"
    arq = request.files.get("arquivo")
    conteudo = arq.read() if arq and arq.filename else p["arquivo"]
    nome_arq = arq.filename if arq and arq.filename else p["arquivo_nome"]
    info = IMP.ler_protocolo_pdf(conteudo) if conteudo else {"sha256": None, "paginas": []}
    cur = conn.execute("""INSERT INTO protocolos (estudo_id, versao, arquivo_nome, arquivo, sha256, paginas, criado_por,
                          criado_em) VALUES (?,?,?,?,?,?,?,?)""",
                       (p["estudo_id"], versao, nome_arq, conteudo, info["sha256"], len(info["paginas"]),
                        D.usuario_atual(), D.agora()))
    novo = cur.lastrowid
    conn.executemany("INSERT INTO protocolo_paginas (protocolo_id, pagina, texto) VALUES (?,?,?)",
                     [(novo, n, t) for n, t in info["paginas"]])
    mapa_v, mapa_i = {}, {}
    for v in conn.execute("SELECT * FROM visitas WHERE protocolo_id=?", (pid,)).fetchall():
        c = conn.execute("INSERT INTO visitas (protocolo_id,nome,ciclo_de,ciclo_ate,janela,ordem,observacao) VALUES (?,?,?,?,?,?,?)",
                         (novo, v["nome"], v["ciclo_de"], v["ciclo_ate"], v["janela"], v["ordem"], v["observacao"]))
        mapa_v[v["id"]] = c.lastrowid
    for i in conn.execute("SELECT * FROM itens WHERE protocolo_id=?", (pid,)).fetchall():
        c = conn.execute("""INSERT INTO itens (protocolo_id,categoria,grupo,nome,pagina_ref,trecho_ref,origem,ordem)
                            VALUES (?,?,?,?,?,?,?,?)""",
                         (novo, i["categoria"], i["grupo"], i["nome"], i["pagina_ref"], i["trecho_ref"], i["origem"], i["ordem"]))
        mapa_i[i["id"]] = c.lastrowid
    for r in conn.execute("SELECT vi.* FROM visita_itens vi JOIN visitas v ON v.id=vi.visita_id WHERE v.protocolo_id=?",
                          (pid,)).fetchall():
        conn.execute("INSERT INTO visita_itens (visita_id,item_id,nota) VALUES (?,?,?)",
                     (mapa_v[r["visita_id"]], mapa_i[r["item_id"]], r["nota"]))
    D.registrar("criou nova versão do protocolo", "protocolo", novo, {"copiado_de": pid, "versao": versao})
    conn.commit()
    flash("Nova versão criada como rascunho, com a matriz copiada. Revise as mudanças da emenda e valide.", "ok")
    return redirect(url_for("protocolo", pid=novo))


@app.route("/protocolos/<int:pid>/ia", methods=["POST"])
@login_required
def protocolo_ia(pid):
    conn = D.db()
    p = protocolo_ou_404(pid)
    if not exige_rascunho(p):
        return redirect(url_for("protocolo", pid=pid))
    if not ia_protocolo.disponivel():
        flash("Extração com IA não configurada (falta ANTHROPIC_API_KEY ou o pacote anthropic).", "erro")
        return redirect(url_for("protocolo", pid=pid))
    if not p["arquivo"]:
        flash("Este protocolo não tem PDF anexado.", "erro")
        return redirect(url_for("protocolo", pid=pid))
    try:
        res = ia_protocolo.extrair(p["arquivo"])
    except Exception as e:
        flash(f"A extração com IA falhou: {e}", "erro")
        return redirect(url_for("protocolo", pid=pid))
    chave_vid = {}
    ordem = conn.execute("SELECT COALESCE(MAX(ordem),0) FROM visitas WHERE protocolo_id=?", (pid,)).fetchone()[0]
    for v in res.get("visitas", []):
        ordem += 1
        c = conn.execute("INSERT INTO visitas (protocolo_id,nome,ciclo_de,ciclo_ate,janela,ordem,observacao) VALUES (?,?,?,?,?,?,?)",
                         (pid, v["nome"], v.get("ciclo_de"), v.get("ciclo_ate"), v.get("janela"), ordem,
                          f"IA – pág. {v.get('pagina')}" if v.get("pagina") else "IA"))
        chave_vid[v["chave"]] = c.lastrowid
    oi = conn.execute("SELECT COALESCE(MAX(ordem),0) FROM itens WHERE protocolo_id=?", (pid,)).fetchone()[0]
    n_marc = 0
    for it in res.get("itens", []):
        oi += 1
        c = conn.execute("""INSERT INTO itens (protocolo_id,categoria,grupo,nome,pagina_ref,origem,ordem)
                            VALUES (?,?,?,?,?, 'ia', ?)""",
                         (pid, it["categoria"], (it.get("grupo") or "").upper() or None, it["nome"],
                          str(it["pagina"]) if it.get("pagina") else None, oi))
        for chave in it.get("visitas", []):
            if chave in chave_vid:
                conn.execute("INSERT OR IGNORE INTO visita_itens (visita_id,item_id,nota) VALUES (?,?,?)",
                             (chave_vid[chave], c.lastrowid, it.get("nota")))
                n_marc += 1
    D.registrar("extraiu cronograma com IA", "protocolo", pid,
                {"modelo": ia_protocolo.MODELO, "visitas": len(chave_vid), "itens": len(res.get("itens", [])),
                 "marcacoes": n_marc, "observacoes": res.get("observacoes")})
    conn.commit()
    flash(f"IA sugeriu {len(chave_vid)} visitas e {len(res.get('itens', []))} itens. CONFIRA tudo com o PDF antes de validar. "
          f"Observações da IA: {res.get('observacoes') or '—'}", "ok")
    return redirect(url_for("protocolo", pid=pid) + "#matriz")


# ------------------------------------------------------------------ médicos

def garantir_medico(conn, nome, crm=None, rotulo=None):
    """Cadastra o médico se ainda não existir (pelo CRM ou pelo nome). Retorna o id."""
    nome = (nome or "").strip()
    if not nome:
        return None
    for m in conn.execute("SELECT * FROM medicos").fetchall():
        if (crm and m["crm"] and IMP.sem_acento(m["crm"]).replace(" ", "") == IMP.sem_acento(crm).replace(" ", "")) \
                or IMP.sem_acento(m["nome"]) == IMP.sem_acento(nome):
            return m["id"]
    cur = conn.execute("INSERT INTO medicos (nome, crm, rotulo, criado_em) VALUES (?,?,?,?)",
                       (nome, crm, rotulo or ("Médica solicitante" if nome.lower().startswith("dra") else "Médico solicitante"),
                        D.agora()))
    D.registrar("cadastrou médico", "medico", cur.lastrowid, {"nome": nome, "crm": crm}, conn=conn)
    return cur.lastrowid


def medico_sugerido(conn, paciente_id):
    """1) médico do último pedido do paciente; 2) médico responsável da planilha (por nome); 3) médico do modelo padrão."""
    r = conn.execute("SELECT medico_id FROM pedidos WHERE paciente_id=? AND medico_id IS NOT NULL ORDER BY id DESC LIMIT 1",
                     (paciente_id,)).fetchone()
    if r:
        return r["medico_id"]
    pac = conn.execute("SELECT medico FROM pacientes WHERE id=?", (paciente_id,)).fetchone()
    ativos = conn.execute("SELECT * FROM medicos WHERE ativo=1").fetchall()
    if pac and pac["medico"]:
        alvo = IMP.sem_acento(re.sub(r"^(dra?\.?|dr\.?)\s+", "", pac["medico"], flags=re.I))
        for m in ativos:
            nm = IMP.sem_acento(re.sub(r"^(dra?\.?|dr\.?)\s+", "", m["nome"], flags=re.I))
            if alvo and (alvo in nm or nm in alvo):
                return m["id"]
    mod = conn.execute("SELECT medico, crm FROM modelos WHERE padrao=1").fetchone()
    if mod and mod["medico"]:
        for m in ativos:
            if IMP.sem_acento(m["nome"]) == IMP.sem_acento(mod["medico"]):
                return m["id"]
    return ativos[0]["id"] if len(ativos) == 1 else None


@app.route("/medicos", methods=["GET", "POST"])
@login_required
def medicos():
    conn = D.db()
    if request.method == "POST":
        acao = request.form.get("acao")
        mid = request.form.get("id", type=int)
        if acao in ("ativar", "desativar") and mid:
            conn.execute("UPDATE medicos SET ativo=? WHERE id=?", (1 if acao == "ativar" else 0, mid))
            D.registrar(f"{acao} médico", "medico", mid)
        else:
            nome = request.form.get("nome", "").strip()
            crm = request.form.get("crm", "").strip() or None
            rotulo = request.form.get("rotulo", "").strip() or None
            if not nome:
                flash("Informe o nome do médico.", "erro")
                return redirect(url_for("medicos"))
            if mid:
                conn.execute("UPDATE medicos SET nome=?, crm=?, rotulo=? WHERE id=?", (nome, crm, rotulo, mid))
                D.registrar("editou médico", "medico", mid, {"nome": nome, "crm": crm, "rotulo": rotulo})
            else:
                garantir_medico(conn, nome, crm, rotulo)
            flash("Médico salvo.", "ok")
        conn.commit()
        return redirect(url_for("medicos"))
    lista = conn.execute("""SELECT m.*, (SELECT COUNT(*) FROM pedidos p WHERE p.medico_id=m.id) AS n_ped
                            FROM medicos m ORDER BY m.ativo DESC, m.nome""").fetchall()
    return render_template("medicos.html", medicos=lista)


# ------------------------------------------------------------------ modelos

CAMPOS_MODELO = ("nome", "titulo", "instituicao", "endereco", "medico_rotulo", "medico", "crm",
                 "cor_destaque", "cor_texto", "cor_fundo", "rodape", "instrucao_lab")
INSTRUCAO_LAB_PADRAO = "COLETAR NO AMAIS – Laboratório Amais"


@app.route("/modelos")
@login_required
def modelos():
    lista = D.db().execute("SELECT id, nome, instituicao, medico, crm, padrao, arquivo_nome, criado_em, "
                           "cabecalho_img IS NOT NULL AS tem_img FROM modelos ORDER BY padrao DESC, nome").fetchall()
    return render_template("modelos.html", modelos=lista)


@app.route("/modelos/importar", methods=["POST"])
@login_required
def modelo_importar():
    arq = request.files.get("arquivo")
    if not arq or not arq.filename:
        flash("Escolha o arquivo do modelo.", "erro")
        return redirect(url_for("modelos"))
    conteudo = arq.read()
    try:
        info = IMP.ler_modelo(arq.filename, conteudo)
    except Exception as e:
        flash(f"Não consegui ler o modelo: {e}", "erro")
        return redirect(url_for("modelos"))
    conn = D.db()
    padrao = 0 if conn.execute("SELECT 1 FROM modelos WHERE padrao=1").fetchone() else 1
    nome = os.path.splitext(arq.filename)[0]
    cols = {"nome": nome, "arquivo_original": conteudo, "padrao": padrao, "criado_em": D.agora(),
            "instrucao_lab": INSTRUCAO_LAB_PADRAO, "mostrar_assinatura": 0}
    for k in ("titulo", "instituicao", "endereco", "medico_rotulo", "medico", "crm", "cor_destaque", "cor_texto",
              "cor_fundo", "cabecalho_img", "cabecalho_mime", "arquivo_nome"):
        if info.get(k):
            cols[k] = info[k]
    cur = conn.execute("INSERT INTO modelos (" + ",".join(cols) + ") VALUES (" + ",".join("?" * len(cols)) + ")",
                       list(cols.values()))
    D.registrar("importou modelo de pedido", "modelo", cur.lastrowid,
                {k: v for k, v in info.items() if k not in ("cabecalho_img",)})
    if info.get("medico"):
        garantir_medico(conn, info["medico"], info.get("crm"), info.get("medico_rotulo"))
    conn.commit()
    flash("Modelo importado. Confira os campos lidos do arquivo e ajuste o que precisar.", "ok")
    return redirect(url_for("modelo_form", mid=cur.lastrowid))


@app.route("/modelos/<int:mid>", methods=["GET", "POST"])
@login_required
def modelo_form(mid):
    conn = D.db()
    m = conn.execute("SELECT * FROM modelos WHERE id=?", (mid,)).fetchone()
    if not m:
        abort(404)
    if request.method == "POST":
        if request.form.get("acao") == "padrao":
            conn.execute("UPDATE modelos SET padrao=0")
            conn.execute("UPDATE modelos SET padrao=1 WHERE id=?", (mid,))
            D.registrar("definiu modelo padrão", "modelo", mid)
        else:
            dados = {k: request.form.get(k, "").strip() or None for k in CAMPOS_MODELO if k in request.form}
            dados["nome"] = dados.get("nome") or m["nome"]
            if "titulo" in dados and not dados["titulo"]:
                dados["titulo"] = "SOLICITAÇÃO DE EXAMES – PESQUISA CLÍNICA"
            dados["mostrar_assinatura"] = 1 if request.form.get("mostrar_assinatura") else 0
            img = request.files.get("cabecalho")
            if img and img.filename:
                dados["cabecalho_img"] = img.read()
                dados["cabecalho_mime"] = img.mimetype
            if request.form.get("remover_img"):
                dados["cabecalho_img"] = None
            conn.execute("UPDATE modelos SET " + ",".join(f"{k}=?" for k in dados) + " WHERE id=?",
                         list(dados.values()) + [mid])
            D.registrar("editou modelo", "modelo", mid, {k: v for k, v in dados.items() if k != "cabecalho_img"})
        conn.commit()
        flash("Modelo salvo.", "ok")
        return redirect(url_for("modelo_form", mid=mid))
    return render_template("modelo_form.html", m=m)


@app.route("/modelos/<int:mid>/cabecalho")
@login_required
def modelo_cabecalho(mid):
    m = D.db().execute("SELECT cabecalho_img, cabecalho_mime FROM modelos WHERE id=?", (mid,)).fetchone()
    if not m or not m["cabecalho_img"]:
        abort(404)
    return Response(m["cabecalho_img"], mimetype=m["cabecalho_mime"] or "image/jpeg")


@app.route("/modelos/<int:mid>/exemplo.pdf")
@login_required
def modelo_exemplo(mid):
    m = D.db().execute("SELECT * FROM modelos WHERE id=?", (mid,)).fetchone()
    if not m:
        abort(404)
    exemplo = {
        "paciente": {"nome": "Paciente Exemplo", "nascimento": "1970-01-01", "participante": "0000-000"},
        "estudo": {"codigo": "ESTUDO-EXEMPLO", "nome": "Droga X"},
        "rotulo": "C1D1", "ciclo_visita": "Ciclo 1 – D1", "data_coleta": date.today().isoformat(),
        "itens": [{"categoria": "laboratorial", "grupo": "HEMATOLOGIA", "nome": n} for n in ("Hemoglobina", "Plaquetas", "Leucócitos")],
        "rastreio": "Pré-visualização do modelo – sem validade.",
    }
    return Response(gerar_pdf(dict(m), exemplo, rascunho=True), mimetype="application/pdf")


# ------------------------------------------------------------------ pedidos

def visitas_do_ciclo(visitas, ciclo):
    out = []
    for v in visitas:
        if ciclo is None:
            if v["ciclo_de"] is None:
                out.append(v)
        elif v["ciclo_de"] is not None and v["ciclo_de"] <= ciclo and (v["ciclo_ate"] is None or v["ciclo_ate"] >= ciclo):
            out.append(v)
    return out


@app.route("/api/paciente/<int:pid>/contexto")
@login_required
def api_contexto(pid):
    conn = D.db()
    pac = conn.execute("""SELECT p.*, e.codigo AS estudo_codigo FROM pacientes p LEFT JOIN estudos e ON e.id=p.estudo_id
                          WHERE p.id=?""", (pid,)).fetchone()
    if not pac:
        abort(404)
    estudo_id = request.args.get("estudo_id", type=int) or pac["estudo_id"]
    proto = conn.execute("SELECT * FROM protocolos WHERE estudo_id=? AND status='validado' ORDER BY id DESC LIMIT 1",
                         (estudo_id,)).fetchone() if estudo_id else None
    visitas = []
    if proto:
        visitas = [{"id": v["id"], "nome": v["nome"], "ciclo_de": v["ciclo_de"], "ciclo_ate": v["ciclo_ate"],
                    "janela": v["janela"], "rotulo": rotulo_visita(v)}
                   for v in conn.execute("SELECT * FROM visitas WHERE protocolo_id=? ORDER BY ordem, id", (proto["id"],))]
    rascunhos = conn.execute("SELECT COUNT(*) FROM protocolos WHERE estudo_id=? AND status='rascunho'", (estudo_id,)).fetchone()[0] if estudo_id else 0
    return jsonify({
        "paciente": {"id": pac["id"], "nome": pac["nome"], "nascimento": data_br(pac["nascimento"]),
                     "participante": pac["participante"], "estudo_id": pac["estudo_id"], "estudo": pac["estudo_codigo"]},
        "estudo_id": estudo_id,
        "protocolo": {"id": proto["id"], "versao": proto["versao"], "validado_em": proto["validado_em"]} if proto else None,
        "protocolos_rascunho": rascunhos,
        "medico_sugerido": medico_sugerido(conn, pid),
        "visitas": visitas,
    })


@app.route("/pedidos")
@login_required
def pedidos():
    conn = D.db()
    status = request.args.get("status", "")
    q = request.args.get("q", "").strip()
    sql = """SELECT p.*, pa.nome AS paciente, pa.participante, e.codigo AS estudo FROM pedidos p
             JOIN pacientes pa ON pa.id=p.paciente_id JOIN protocolos pr ON pr.id=p.protocolo_id
             JOIN estudos e ON e.id=pr.estudo_id WHERE 1=1"""
    args = []
    if status:
        sql += " AND p.status=?"
        args.append(status)
    if q:
        sql += " AND (pa.nome LIKE ? OR pa.participante LIKE ? OR p.rotulo LIKE ?)"
        args += [f"%{q}%"] * 3
    lista = conn.execute(sql + " ORDER BY p.id DESC LIMIT 300", args).fetchall()
    return render_template("pedidos.html", pedidos=lista, status=status, q=q)


@app.route("/pedidos/novo", methods=["GET", "POST"])
@login_required
def pedido_novo():
    conn = D.db()
    if request.method == "POST":
        try:
            pac_id = int(request.form["paciente_id"])
            vis_id = int(request.form["visita_id"])
        except (KeyError, ValueError):
            flash("Selecione paciente, ciclo e visita.", "erro")
            return redirect(url_for("pedido_novo"))
        ciclo = request.form.get("ciclo", type=int)
        data_coleta = IMP.parse_data(request.form.get("data_coleta"))
        if not data_coleta:
            flash("Informe a data desejada para coleta/realização.", "erro")
            return redirect(url_for("pedido_novo", paciente=pac_id))
        v = conn.execute("""SELECT v.*, p.status AS p_status, p.id AS proto_id FROM visitas v
                            JOIN protocolos p ON p.id=v.protocolo_id WHERE v.id=?""", (vis_id,)).fetchone()
        if not v or v["p_status"] != "validado":
            flash("A visita escolhida não pertence a um protocolo validado.", "erro")
            return redirect(url_for("pedido_novo"))
        if v["ciclo_de"] is not None and (ciclo is None or ciclo < v["ciclo_de"] or (v["ciclo_ate"] and ciclo > v["ciclo_ate"])):
            flash("O ciclo informado não corresponde a essa visita do protocolo.", "erro")
            return redirect(url_for("pedido_novo", paciente=pac_id))
        if v["ciclo_de"] is None:
            ciclo = None
        pac = conn.execute("SELECT * FROM pacientes WHERE id=?", (pac_id,)).fetchone()
        if not pac:
            abort(404)
        est_proto = conn.execute("SELECT estudo_id FROM protocolos WHERE id=?", (v["proto_id"],)).fetchone()["estudo_id"]
        if pac["estudo_id"] is None:
            conn.execute("UPDATE pacientes SET estudo_id=?, atualizado_em=? WHERE id=?", (est_proto, D.agora(), pac_id))
            D.registrar("vinculou paciente ao estudo (ao criar pedido)", "paciente", pac_id, {"estudo_id": est_proto})
        elif pac["estudo_id"] != est_proto:
            flash("Este protocolo é de outro estudo que não o do paciente.", "erro")
            return redirect(url_for("pedido_novo", paciente=pac_id))
        itens = conn.execute("""SELECT i.*, vi.nota FROM visita_itens vi JOIN itens i ON i.id=vi.item_id
                                WHERE vi.visita_id=?""", (vis_id,)).fetchall()
        ordem_cat = {c: n for n, (c, _) in enumerate(CATEGORIAS)}
        itens = sorted(itens, key=lambda i: (ordem_cat.get(i["categoria"], 9), i["ordem"], i["id"]))
        lista = [{"item_id": i["id"], "categoria": i["categoria"], "grupo": i["grupo"], "nome": i["nome"],
                  "nota": i["nota"], "pagina_ref": i["pagina_ref"], "origem": "protocolo", "ordem": n}
                 for n, i in enumerate(itens)]
        modelo = conn.execute("SELECT id FROM modelos WHERE padrao=1").fetchone()
        medico_id = request.form.get("medico_id", type=int) or medico_sugerido(conn, pac_id)
        cats = sorted({i["categoria"] for i in lista}, key=lambda c: ordem_cat.get(c, 9))
        cur = conn.execute("""INSERT INTO pedidos (paciente_id, protocolo_id, visita_id, modelo_id, medico_id, ciclo, rotulo,
                              data_coleta, categorias, itens_json, removidos_json, status, criado_por, criado_em)
                              VALUES (?,?,?,?,?,?,?,?,?,?,?, 'rascunho', ?, ?)""",
                           (pac_id, v["proto_id"], vis_id, modelo["id"] if modelo else None, medico_id, ciclo,
                            rotulo_visita(v, ciclo), data_coleta, ",".join(cats), json.dumps(lista, ensure_ascii=False),
                            "[]", D.usuario_atual(), D.agora()))
        D.registrar("criou pedido (rascunho)", "pedido", cur.lastrowid,
                    {"paciente_id": pac_id, "protocolo_id": v["proto_id"], "visita_id": vis_id, "ciclo": ciclo,
                     "itens_do_protocolo": len(lista)})
        conn.commit()
        return redirect(url_for("pedido", pid=cur.lastrowid))
    pacs = conn.execute("""SELECT p.id, p.nome, p.participante, p.nascimento, e.codigo AS estudo FROM pacientes p
                           LEFT JOIN estudos e ON e.id=p.estudo_id WHERE p.ativo=1 ORDER BY p.nome""").fetchall()
    estudos_l = conn.execute("""SELECT e.id, e.codigo FROM estudos e WHERE EXISTS
                                (SELECT 1 FROM protocolos p WHERE p.estudo_id=e.id AND p.status='validado') ORDER BY e.codigo""").fetchall()
    medicos_l = conn.execute("SELECT id, nome, crm FROM medicos WHERE ativo=1 ORDER BY nome").fetchall()
    return render_template("pedido_novo.html", pacientes=pacs, estudos=estudos_l, medicos=medicos_l,
                           paciente_sel=request.args.get("paciente", type=int), hoje=date.today().isoformat())


def pedido_completo(conn, pid):
    p = conn.execute("""SELECT pe.*, pa.nome AS pac_nome, pa.nascimento AS pac_nasc, pa.participante AS pac_part,
                        pa.cpf AS pac_cpf, pr.versao AS proto_versao, pr.validado_em AS proto_validado_em,
                        pr.validado_por AS proto_validado_por, pr.sha256 AS proto_sha, pr.arquivo_nome AS proto_arquivo,
                        e.codigo AS estudo_codigo, e.nome AS estudo_nome, v.nome AS visita_nome, v.janela AS visita_janela,
                        m.nome AS med_nome, m.crm AS med_crm, m.rotulo AS med_rotulo
                        FROM pedidos pe JOIN pacientes pa ON pa.id=pe.paciente_id
                        JOIN protocolos pr ON pr.id=pe.protocolo_id JOIN estudos e ON e.id=pr.estudo_id
                        JOIN visitas v ON v.id=pe.visita_id LEFT JOIN medicos m ON m.id=pe.medico_id
                        WHERE pe.id=?""", (pid,)).fetchone()
    if not p:
        abort(404)
    return p


def modelo_para_pdf(modelo, p):
    """O médico escolhido no pedido substitui o médico padrão do modelo (quadro do topo e assinatura)."""
    m = dict(modelo)
    if p["med_nome"]:
        m.update(medico=p["med_nome"], crm=p["med_crm"], medico_rotulo=p["med_rotulo"] or m.get("medico_rotulo"))
    return m


def montar_dados_pdf(p, itens, modelo):
    cats = [c for c in (p["categorias"] or "").split(",") if c]
    itens = [i for i in itens if i["categoria"] in cats]
    ciclo_visita = f"Ciclo {p['ciclo']} – {p['visita_nome']}" if p["ciclo"] else p["visita_nome"]
    rastreio = (f"Pedido nº {p['id']}  ·  Protocolo {p['estudo_codigo']} versão {p['proto_versao']} "
                f"(validado em {data_br(p['proto_validado_em'])} por {p['proto_validado_por']})")
    if p["status"] == "emitido":
        rastreio += f"\nEmitido em {data_br(p['emitido_em'])} {p['emitido_em'][11:16]} por {p['emitido_por']} após conferência"
    else:
        rastreio += "\nPRÉ-VISUALIZAÇÃO – pedido ainda não conferido/emitido"
    return {
        "paciente": {"nome": p["pac_nome"], "nascimento": p["pac_nasc"], "participante": p["pac_part"]},
        "estudo": {"codigo": p["estudo_codigo"], "nome": p["estudo_nome"]},
        "rotulo": p["rotulo"], "ciclo_visita": ciclo_visita, "data_coleta": p["data_coleta"],
        "itens": itens, "observacao": p["observacao"], "rastreio": rastreio,
        "paginas_por_categoria": True,
    }


@app.route("/pedidos/<int:pid>", methods=["GET", "POST"])
@login_required
def pedido(pid):
    conn = D.db()
    p = pedido_completo(conn, pid)
    itens = json.loads(p["itens_json"])
    removidos = json.loads(p["removidos_json"] or "[]")
    if request.method == "POST":
        if p["status"] != "rascunho":
            flash("Pedido já emitido/cancelado não pode ser alterado.", "erro")
            return redirect(url_for("pedido", pid=pid))
        acao = request.form.get("acao")
        if acao == "adicionar":
            nome = request.form.get("nome", "").strip()
            just = request.form.get("justificativa", "").strip()
            if not nome or len(just) < 5:
                flash("Para incluir um item fora do protocolo, informe o nome e a justificativa/autorização.", "erro")
                return redirect(url_for("pedido", pid=pid) + "#adicionar")
            novo = {"item_id": None, "categoria": request.form.get("categoria", "laboratorial"),
                    "grupo": request.form.get("grupo", "").strip().upper() or "OUTROS", "nome": nome,
                    "nota": request.form.get("nota", "").strip() or None, "origem": "manual",
                    "justificativa": just, "incluido_por": D.usuario_atual(), "incluido_em": D.agora()}
            itens.append(novo)
            cats = set((p["categorias"] or "").split(",")) - {""}
            cats.add(novo["categoria"])
            conn.execute("UPDATE pedidos SET itens_json=?, categorias=? WHERE id=?",
                         (json.dumps(itens, ensure_ascii=False), ",".join(c for c, _ in CATEGORIAS if c in cats), pid))
            D.registrar("incluiu item manual no pedido", "pedido", pid, novo)
            conn.commit()
            return redirect(url_for("pedido", pid=pid) + "#itens")
        if acao == "remover_manual":
            idx = int(request.form["idx"])
            if 0 <= idx < len(itens) and itens[idx]["origem"] == "manual":
                it = itens.pop(idx)
                conn.execute("UPDATE pedidos SET itens_json=? WHERE id=?", (json.dumps(itens, ensure_ascii=False), pid))
                D.registrar("retirou item manual do pedido", "pedido", pid, it)
                conn.commit()
            return redirect(url_for("pedido", pid=pid) + "#itens")
        if acao == "cancelar":
            motivo = request.form.get("motivo", "").strip()
            conn.execute("UPDATE pedidos SET status='cancelado', cancelado_motivo=? WHERE id=?", (motivo or None, pid))
            D.registrar("cancelou pedido", "pedido", pid, {"motivo": motivo})
            conn.commit()
            flash("Pedido cancelado.", "ok")
            return redirect(url_for("pedidos"))

        # salvar conferência (e opcionalmente emitir)
        todos = itens + [dict(r, _removido=True) for r in removidos]
        novos_itens, novos_rem, erros = [], [], []
        for k, it in enumerate(todos):
            if it["origem"] == "manual":
                novos_itens.append({x: y for x, y in it.items() if x != "_removido"})
                continue
            marcado = bool(request.form.get(f"item_{k}"))
            if marcado:
                novos_itens.append({x: y for x, y in it.items() if x not in ("_removido", "justificativa_remocao")})
            else:
                just = request.form.get(f"just_{k}", "").strip() or it.get("justificativa_remocao", "")
                if len(just) < 5:
                    erros.append(it["nome"])
                novos_rem.append({x: y for x, y in it.items() if x != "_removido"} | {"justificativa_remocao": just})
        ordem_cat = {c: n for n, (c, _) in enumerate(CATEGORIAS)}
        novos_itens.sort(key=lambda i: (ordem_cat.get(i["categoria"], 9), i["origem"] == "manual", i.get("ordem", 9999)))
        novos_rem.sort(key=lambda i: i.get("ordem", 9999))
        cats = [c for c, _ in CATEGORIAS if request.form.get(f"cat_{c}")]
        data_coleta = IMP.parse_data(request.form.get("data_coleta")) or p["data_coleta"]
        obs = request.form.get("observacao", "").strip() or None
        modelo_id = request.form.get("modelo_id", type=int) or p["modelo_id"]
        medico_id = request.form.get("medico_id", type=int) or p["medico_id"]
        conn.execute("""UPDATE pedidos SET itens_json=?, removidos_json=?, categorias=?, data_coleta=?, observacao=?,
                        modelo_id=?, medico_id=? WHERE id=?""",
                     (json.dumps(novos_itens, ensure_ascii=False), json.dumps(novos_rem, ensure_ascii=False),
                      ",".join(cats), data_coleta, obs, modelo_id, medico_id, pid))
        D.registrar("salvou conferência do pedido", "pedido", pid,
                    {"desmarcados": [r["nome"] for r in novos_rem], "categorias": cats, "data_coleta": data_coleta,
                     "medico_id": medico_id})
        conn.commit()
        if erros:
            flash("Explique por que estes itens do protocolo foram desmarcados: " + ", ".join(erros), "erro")
            return redirect(url_for("pedido", pid=pid) + "#itens")
        if acao == "emitir":
            if not request.form.get("confirmo"):
                flash("Marque a confirmação de conferência antes de emitir.", "erro")
                return redirect(url_for("pedido", pid=pid) + "#emitir")
            if not cats or not any(i["categoria"] in cats for i in novos_itens):
                flash("Não há exames para imprimir nas categorias escolhidas.", "erro")
                return redirect(url_for("pedido", pid=pid))
            modelo = conn.execute("SELECT * FROM modelos WHERE id=?", (modelo_id,)).fetchone() if modelo_id else None
            if not modelo:
                flash("Cadastre/escolha um modelo de pedido antes de emitir.", "erro")
                return redirect(url_for("pedido", pid=pid))
            if not medico_id and not modelo["medico"]:
                flash("Escolha o médico solicitante antes de emitir.", "erro")
                return redirect(url_for("pedido", pid=pid))
            conn.execute("UPDATE pedidos SET status='emitido', emitido_por=?, emitido_em=? WHERE id=?",
                         (D.usuario_atual(), D.agora(), pid))
            p2 = pedido_completo(conn, pid)
            pdf = gerar_pdf(modelo_para_pdf(modelo, p2), montar_dados_pdf(p2, novos_itens, modelo))
            sha = hashlib.sha256(pdf).hexdigest()
            conn.execute("UPDATE pedidos SET pdf=?, pdf_sha256=? WHERE id=?", (pdf, sha, pid))
            D.registrar("emitiu pedido", "pedido", pid, {"pdf_sha256": sha, "modelo_id": modelo_id, "itens": len(novos_itens),
                                                         "categorias": cats})
            conn.commit()
            flash("Pedido emitido. O PDF ficou guardado no histórico.", "ok")
            return redirect(url_for("pedido", pid=pid))
        if acao == "previa":
            return redirect(url_for("pedido_pdf", pid=pid))
        flash("Conferência salva.", "ok")
        return redirect(url_for("pedido", pid=pid))

    modelos_l = conn.execute("SELECT id, nome, padrao FROM modelos ORDER BY padrao DESC, nome").fetchall()
    trilha = conn.execute("SELECT * FROM auditoria WHERE entidade='pedido' AND entidade_id=? ORDER BY id", (pid,)).fetchall()
    todos = itens + [dict(r, _removido=True) for r in removidos]
    medicos_l = conn.execute("SELECT id, nome, crm, ativo FROM medicos WHERE ativo=1 OR id=? ORDER BY nome",
                             (p["medico_id"],)).fetchall()
    return render_template("pedido.html", p=p, todos=todos, modelos=modelos_l, trilha=trilha, medicos=medicos_l,
                           cats=set((p["categorias"] or "").split(",")))


@app.route("/pedidos/<int:pid>/pdf")
@login_required
def pedido_pdf(pid):
    conn = D.db()
    p = pedido_completo(conn, pid)
    nome = f"{p['rotulo']}_{(p['pac_nome'] or '').split(' ')[0]}_pedido{p['id']}.pdf".replace(" ", "_")
    if p["status"] == "emitido" and p["pdf"]:
        D.registrar("baixou PDF emitido", "pedido", pid)
        conn.commit()
        return Response(p["pdf"], mimetype="application/pdf", headers={"Content-Disposition": f"inline; filename=\"{nome}\""})
    modelo = conn.execute("SELECT * FROM modelos WHERE id=?", (p["modelo_id"],)).fetchone() if p["modelo_id"] else None
    if not modelo:
        modelo = conn.execute("SELECT * FROM modelos WHERE padrao=1").fetchone()
    if not modelo:
        flash("Cadastre um modelo de pedido primeiro (menu Modelos).", "erro")
        return redirect(url_for("pedido", pid=pid))
    pdf = gerar_pdf(modelo_para_pdf(modelo, p), montar_dados_pdf(p, json.loads(p["itens_json"]), modelo), rascunho=True)
    return Response(pdf, mimetype="application/pdf", headers={"Content-Disposition": f"inline; filename=\"PREVIA_{nome}\""})


# ------------------------------------------------------------------ auditoria

@app.route("/auditoria")
@login_required
def auditoria():
    conn = D.db()
    ent = request.args.get("entidade", "")
    sql, args = "SELECT * FROM auditoria", []
    if ent:
        sql += " WHERE entidade=?"
        args.append(ent)
    lista = conn.execute(sql + " ORDER BY id DESC LIMIT 500", args).fetchall()
    return render_template("auditoria.html", registros=lista, entidade=ent)


@app.errorhandler(403)
def proibido(e):
    return render_template("erro.html", msg="Apenas administradores podem acessar esta página."), 403


@app.errorhandler(404)
def nao_achado(e):
    return render_template("erro.html", msg="Página ou registro não encontrado."), 404


if __name__ == "__main__":
    app.run(debug=False, host="127.0.0.1", port=int(os.environ.get("PORT", 5050)))
