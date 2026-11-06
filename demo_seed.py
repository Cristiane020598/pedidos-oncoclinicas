"""Cria um banco de DEMONSTRAÇÃO (pedidos_demo.sqlite3) com dados 100% fictícios.

Uso:
    python demo_seed.py
    set PEDIDOS_DB=pedidos_demo.sqlite3 && python app.py      (Windows cmd)
Login da demo: demo@demo.local / demo12345
"""

import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
CAMINHO = os.path.join(AQUI, "pedidos_demo.sqlite3")
if os.path.exists(CAMINHO):
    os.remove(CAMINHO)
os.environ["PEDIDOS_DB"] = CAMINHO
sys.path.insert(0, AQUI)

from werkzeug.security import generate_password_hash  # noqa: E402

import db as D  # noqa: E402
import importadores as IMP  # noqa: E402

conn = D.conectar(CAMINHO)
agora = D.agora()
conn.execute("INSERT INTO usuarios (nome,email,senha_hash,is_admin,criado_em) VALUES (?,?,?,1,?)",
             ("Equipe Demo", "demo@demo.local", generate_password_hash("demo12345"), agora))
eid = conn.execute("INSERT INTO estudos (codigo,nome,patrocinador,criado_em) VALUES (?,?,?,?)",
                   ("ESTUDO-DEMO-01", "Droga Fictícia", "Patrocinador Exemplo", agora)).lastrowid

for nome, nasc, part in [("Paciente Demonstração Um", "1966-05-20", "1001-001"),
                         ("Paciente Demonstração Dois", "1958-11-02", "1001-002"),
                         ("Paciente Demonstração Três", "1971-07-15", "1001-003")]:
    conn.execute("INSERT INTO pacientes (nome,nascimento,participante,estudo_id,criado_em) VALUES (?,?,?,?,?)",
                 (nome, nasc, part, eid, agora))

pid = conn.execute("""INSERT INTO protocolos (estudo_id,versao,status,validado_por,validado_em,criado_por,criado_em)
                      VALUES (?,?, 'validado', 'Equipe Demo', ?, 'Equipe Demo', ?)""",
                   (eid, "v1.0 (demonstração)", agora, agora)).lastrowid
visitas = {}
for ordem, (nome, de, ate, jan) in enumerate([("Triagem", None, None, "-28 a -1 dias"), ("D1", 1, None, None),
                                              ("D8", 1, 1, "±1 dia"), ("D15", 1, None, "±3 dias"),
                                              ("Fim de tratamento", None, None, "até 30 dias")]):
    visitas[nome] = conn.execute("INSERT INTO visitas (protocolo_id,nome,ciclo_de,ciclo_ate,janela,ordem) VALUES (?,?,?,?,?,?)",
                                 (pid, nome, de, ate, jan, ordem)).lastrowid
itens = {}
lista = [
    ("laboratorial", "HEMATOLOGIA – HEMOGRAMA COMPLETO", ["Hemoglobina", "Eritrócitos", "Hematócrito",
                                                          "Leucócitos totais e diferencial", "Neutrófilos absolutos (ANC)", "Plaquetas"]),
    ("laboratorial", "BIOQUÍMICA SÉRICA", ["Sódio", "Potássio", "Cálcio total e cálcio corrigido pela albumina", "Magnésio",
                                           "Fósforo", "Cloro", "Creatinina", "Proteína total", "Albumina", "Fosfatase alcalina",
                                           "Bilirrubina total", "Aspartato aminotransferase (AST)",
                                           "Alanina aminotransferase (ALT)", "Lactato desidrogenase (LDH)"]),
    ("laboratorial", "COAGULAÇÃO", ["Tempo de protrombina (TP/INR)", "Tempo de tromboplastina parcial ativada (TTPa)"]),
    ("laboratorial", "SOROLOGIAS", ["HBsAg", "Anti-HBc", "Anti-HCV", "Anti-HIV 1 e 2"]),
    ("laboratorial", "GRAVIDEZ", ["Beta-hCG sérico"]),
    ("imagem", "TOMOGRAFIA COMPUTADORIZADA", ["TC de tórax com contraste", "TC de abdômen e pelve com contraste"]),
    ("procedimento", "CARDIOLÓGICO", ["Eletrocardiograma (ECG) de 12 derivações", "Ecocardiograma"]),
]
o = 0
for cat, grupo, nomes in lista:
    for n in nomes:
        o += 1
        itens[n] = conn.execute("INSERT INTO itens (protocolo_id,categoria,grupo,nome,origem,ordem) VALUES (?,?,?,?, 'manual', ?)",
                                (pid, cat, grupo, n, o)).lastrowid
hemo = lista[0][2]
bioq = lista[1][2]
marc = []
for n in hemo + bioq + lista[2][2] + lista[3][2] + lista[5][2] + lista[6][2]:
    marc.append((visitas["Triagem"], itens[n], None))
marc.append((visitas["Triagem"], itens["Beta-hCG sérico"], "apenas mulheres com potencial de engravidar"))
for v in ("D1", "D15"):
    for n in hemo + bioq:
        marc.append((visitas[v], itens[n], None))
marc.append((visitas["D1"], itens["Eletrocardiograma (ECG) de 12 derivações"], "se clinicamente indicado"))
for n in hemo:
    marc.append((visitas["D8"], itens[n], None))
for n in hemo + bioq + lista[5][2]:
    marc.append((visitas["Fim de tratamento"], itens[n], None))
conn.executemany("INSERT INTO visita_itens (visita_id,item_id,nota) VALUES (?,?,?)", marc)

docx = r"C:\Users\Cristiane\Desktop\C10D15_Laboratorial.docx"
if os.path.exists(docx):
    with open(docx, "rb") as f:
        info = IMP.ler_modelo("C10D15_Laboratorial.docx", f.read())
    cols = {k: info[k] for k in ("titulo", "instituicao", "endereco", "medico_rotulo", "medico", "crm", "cor_destaque",
                                 "cor_texto", "cor_fundo", "cabecalho_img", "cabecalho_mime", "arquivo_nome") if info.get(k)}
    cols.update(nome="Modelo CPO/SP (importado do DOCX)", padrao=1, criado_em=agora,
                instrucao_lab="COLETAR NO AMAIS – Laboratório Amais", mostrar_assinatura=0)
    conn.execute("INSERT INTO modelos (" + ",".join(cols) + ") VALUES (" + ",".join("?" * len(cols)) + ")", list(cols.values()))
    if info.get("medico"):
        conn.execute("INSERT INTO medicos (nome, crm, rotulo, criado_em) VALUES (?,?,?,?)",
                     (info["medico"], info.get("crm"), info.get("medico_rotulo") or "Médica solicitante", agora))
conn.execute("INSERT INTO medicos (nome, crm, rotulo, criado_em) VALUES (?,?,?,?)",
             ("Dr. Médico Fictício", "CRM/SP 000001", "Médico solicitante", agora))
conn.commit()
print("Banco de demonstração criado:", CAMINHO)
