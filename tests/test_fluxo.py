"""Teste de ponta a ponta com dados fictícios.  Rodar:  python -m unittest discover -s tests"""

import io
import json
import os
import sys
import tempfile
import unittest

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
_tmp = tempfile.mkdtemp()
os.environ["PEDIDOS_DB"] = os.path.join(_tmp, "teste.sqlite3")
os.environ.pop("ANTHROPIC_API_KEY", None)

import app as A  # noqa: E402
import importadores as IMP  # noqa: E402


def planilha_xlsx():
    import openpyxl
    from datetime import datetime

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Relatório de pacientes"])  # linha de título antes do cabeçalho
    ws.append(["Nome do Paciente", "Dt. Nasc.", "Protocolo", "Nº Participante", "Celular"])
    ws.append(["Maria Teste da Silva", datetime(1961, 3, 4), "MCLA-158-CL02", "13103-00001", "11999990000"])
    ws.append(["João Fictício Souza", "20/05/1966", "MCLA-158-CL02", "13103-00002", None])
    ws.append(["Ana Exemplo", 23456, "OUTRO-ESTUDO-01", 1003, None])
    ws.append([None, None, None, None, None])
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def protocolo_pdf():
    from reportlab.pdfgen import canvas

    b = io.BytesIO()
    c = canvas.Canvas(b)
    c.drawString(50, 800, "Protocol MCLA-158-CL02")
    c.showPage()
    c.drawString(50, 800, "Schedule of Activities")
    c.drawString(50, 780, "Hematology: hemoglobin, platelets, neutrophils (ANC). Chemistry: sodium, potassium, creatinine, ALT, AST")
    c.drawString(50, 760, "CT scan of chest, abdomen and pelvis every 6 weeks. ECG at screening.")
    c.showPage()
    c.save()
    return b.getvalue()


class Fluxo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        A.app.config["TESTING"] = True
        cls.c = A.app.test_client()

    def test_fluxo_completo(self):
        c = self.c
        # 1. primeiro acesso
        r = c.get("/")
        self.assertIn("/login", r.headers["Location"])
        r = c.post("/primeiro-acesso", data={"nome": "Equipe Teste", "email": "t@t.com", "senha": "senha1234"})
        self.assertEqual(r.status_code, 302)

        # 2. importa planilha (mapeamento automático)
        r = c.post("/pacientes/importar", data={"arquivo": (io.BytesIO(planilha_xlsx()), "pacientes.xlsx")},
                   content_type="multipart/form-data")
        html = r.get_data(as_text=True)
        self.assertIn("3 linha(s)", html)
        cols, _ = IMP.ler_planilha("p.xlsx", planilha_xlsx())
        palpite = IMP.palpite_mapeamento(cols)
        self.assertEqual(palpite["nome"], "Nome do Paciente")
        self.assertEqual(palpite["nascimento"], "Dt. Nasc.")
        self.assertEqual(palpite["participante"], "Nº Participante")
        self.assertEqual(palpite["estudo"], "Protocolo")
        dados = {"etapa": "importar", "atualizar": "1"}
        dados.update({f"map_{k}": v for k, v in palpite.items()})
        r = c.post("/pacientes/importar", data=dados)
        html = r.get_data(as_text=True)
        self.assertIn("pacientes novos", html)
        conn = A.D.conectar()
        pacs = conn.execute("SELECT * FROM pacientes ORDER BY id").fetchall()
        self.assertEqual(len(pacs), 3)
        self.assertEqual(pacs[0]["nascimento"], "1961-03-04")
        self.assertEqual(pacs[1]["nascimento"], "1966-05-20")
        self.assertEqual(pacs[2]["participante"], "1003")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM estudos").fetchone()[0], 2)

        # reimportar não duplica
        c.post("/pacientes/importar", data={"arquivo": (io.BytesIO(planilha_xlsx()), "pacientes.xlsx")},
               content_type="multipart/form-data")
        c.post("/pacientes/importar", data=dados)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM pacientes").fetchone()[0], 3)

        # 3. modelo a partir do DOCX real (se existir) ou imagem
        docx_path = r"C:\Users\Cristiane\Desktop\C10D15_Laboratorial.docx"
        if os.path.exists(docx_path):
            with open(docx_path, "rb") as f:
                r = c.post("/modelos/importar", data={"arquivo": (io.BytesIO(f.read()), "C10D15_Laboratorial.docx")},
                           content_type="multipart/form-data")
            m = conn.execute("SELECT * FROM modelos").fetchone()
            self.assertIn("ONCOCL", m["instituicao"])
            self.assertIn("Nathalia", m["medico"])
            self.assertEqual(m["crm"], "CRM/SP 186752")
            self.assertIsNotNone(m["cabecalho_img"])
            self.assertEqual(m["padrao"], 1)
        else:
            conn.execute("INSERT INTO modelos (nome, instituicao, medico, crm, padrao, criado_em) VALUES "
                         "('t','ONCOCLÍNICAS | CPO/SP','Dra. Teste','CRM 1',1,'x')")
            conn.commit()

        # 4. protocolo
        estudo = conn.execute("SELECT id FROM estudos WHERE codigo='MCLA-158-CL02'").fetchone()["id"]
        r = c.post("/protocolos/novo", data={"estudo_id": estudo, "versao": "v3.0",
                                             "arquivo": (io.BytesIO(protocolo_pdf()), "prot.pdf")},
                   content_type="multipart/form-data")
        pid = int(r.headers["Location"].rstrip("/").split("/")[-1])
        html = c.get(f"/protocolos/{pid}").get_data(as_text=True)
        self.assertIn("Schedule", conn.execute("SELECT texto FROM protocolo_paginas WHERE protocolo_id=? AND pagina=2",
                                               (pid,)).fetchone()[0])
        self.assertIn("hemograma", html.lower())
        # visitas
        c.post(f"/protocolos/{pid}/visitas", data={"nome": "Triagem", "tipo": "fora"})
        c.post(f"/protocolos/{pid}/visitas", data={"nome": "D1", "tipo": "ciclos", "ciclo_de": "1", "ciclo_ate": ""})
        c.post(f"/protocolos/{pid}/visitas", data={"nome": "D15", "tipo": "ciclos", "ciclo_de": "1", "ciclo_ate": ""})
        # itens
        c.post(f"/protocolos/{pid}/itens", data={"categoria": "laboratorial", "grupo": "Hematologia – hemograma completo",
                                                 "nome": "Hemoglobina\nPlaquetas\nNeutrófilos absolutos (ANC)", "pagina_ref": "2"})
        c.post(f"/protocolos/{pid}/itens", data={"categoria": "laboratorial", "grupo": "BIOQUÍMICA SÉRICA",
                                                 "nome": "Sódio\nAlanina aminotransferase (ALT)"})
        c.post(f"/protocolos/{pid}/itens", data={"categoria": "imagem", "grupo": "TOMOGRAFIA COMPUTADORIZADA",
                                                 "nome": "TC de tórax, abdômen e pelve"})
        c.post(f"/protocolos/{pid}/itens", data={"categoria": "procedimento", "grupo": "CARDIOLÓGICO",
                                                 "nome": "Eletrocardiograma (ECG)"})
        vis = {v["nome"]: v["id"] for v in conn.execute("SELECT * FROM visitas WHERE protocolo_id=?", (pid,))}
        its = {i["nome"]: i["id"] for i in conn.execute("SELECT * FROM itens WHERE protocolo_id=?", (pid,))}
        self.assertEqual(len(its), 7)
        matriz = {}
        for n in ("Hemoglobina", "Plaquetas", "Neutrófilos absolutos (ANC)", "Sódio", "Alanina aminotransferase (ALT)"):
            matriz[f"m_{vis['D15']}_{its[n]}"] = "1"
            matriz[f"m_{vis['Triagem']}_{its[n]}"] = "1"
        matriz[f"m_{vis['Triagem']}_{its['TC de tórax, abdômen e pelve']}"] = "1"
        matriz[f"m_{vis['Triagem']}_{its['Eletrocardiograma (ECG)']}"] = "1"
        matriz[f"n_{vis['D15']}_{its['Sódio']}"] = "se clinicamente indicado"
        c.post(f"/protocolos/{pid}/matriz", data=matriz)

        # pedido não pode usar protocolo em rascunho
        pac = conn.execute("SELECT * FROM pacientes WHERE participante='13103-00002'").fetchone()
        ctx = c.get(f"/api/paciente/{pac['id']}/contexto").get_json()
        self.assertIsNone(ctx["protocolo"])

        # validar
        r = c.post(f"/protocolos/{pid}/validar", data={})
        self.assertEqual(conn.execute("SELECT status FROM protocolos WHERE id=?", (pid,)).fetchone()[0], "rascunho")
        c.post(f"/protocolos/{pid}/validar", data={"confirmo": "1"})
        self.assertEqual(conn.execute("SELECT status FROM protocolos WHERE id=?", (pid,)).fetchone()[0], "validado")
        # validado = bloqueado
        c.post(f"/protocolos/{pid}/itens", data={"categoria": "laboratorial", "nome": "Lipase"})
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM itens WHERE protocolo_id=?", (pid,)).fetchone()[0], 7)

        # 5. novo pedido C10D15
        ctx = c.get(f"/api/paciente/{pac['id']}/contexto").get_json()
        self.assertEqual(ctx["protocolo"]["id"], pid)
        r = c.post("/pedidos/novo", data={"paciente_id": pac["id"], "ciclo": "10", "visita_id": vis["D15"],
                                          "data_coleta": "2026-11-12"})
        ped = int(r.headers["Location"].rstrip("/").split("/")[-1])
        p = conn.execute("SELECT * FROM pedidos WHERE id=?", (ped,)).fetchone()
        self.assertEqual(p["rotulo"], "C10D15")
        itens = json.loads(p["itens_json"])
        self.assertEqual([i["nome"] for i in itens][:3], ["Hemoglobina", "Plaquetas", "Neutrófilos absolutos (ANC)"])
        self.assertEqual(len(itens), 5)  # só o que o protocolo marca pra D15 (sem TC/ECG)
        self.assertEqual(p["categorias"], "laboratorial")

        # visita de triagem com ciclo errado é recusada
        r = c.post("/pedidos/novo", data={"paciente_id": pac["id"], "ciclo": "", "visita_id": vis["D15"],
                                          "data_coleta": "2026-11-12"})
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM pedidos").fetchone()[0], 1)

        # 6. conferência: desmarcar sem justificativa -> erro ; com justificativa -> ok
        form = {f"item_{k}": "1" for k in range(5) if k != 1}
        form.update({"cat_laboratorial": "1", "data_coleta": "2026-11-12", "acao": "salvar"})
        c.post(f"/pedidos/{ped}", data=form)
        p = conn.execute("SELECT * FROM pedidos WHERE id=?", (ped,)).fetchone()
        rem = json.loads(p["removidos_json"])
        self.assertEqual(rem[0]["nome"], "Plaquetas")
        # o formulário é re-renderizado: agora a ordem é [Hemo, Neut, Sódio, ALT, Plaquetas(desmarcado)]
        form = {f"item_{k}": "1" for k in range(4)}
        form.update({"just_4": "coletado ontem no hospital, resultado anexado", "cat_laboratorial": "1",
                     "data_coleta": "2026-11-12", "acao": "salvar"})
        c.post(f"/pedidos/{ped}", data=form)
        self.assertEqual(json.loads(conn.execute("SELECT removidos_json FROM pedidos WHERE id=?", (ped,)).fetchone()[0])[0]
                         ["justificativa_remocao"], "coletado ontem no hospital, resultado anexado")
        # re-marcar e desmarcar de novo mantém a ordem do protocolo
        form2 = {f"item_{k}": "1" for k in range(5)}
        form2.update({"cat_laboratorial": "1", "acao": "salvar"})
        c.post(f"/pedidos/{ped}", data=form2)
        self.assertEqual([i["nome"] for i in json.loads(conn.execute("SELECT itens_json FROM pedidos WHERE id=?",
                                                                     (ped,)).fetchone()[0])][:2], ["Hemoglobina", "Plaquetas"])
        c.post(f"/pedidos/{ped}", data={"item_0": "1", "item_2": "1", "item_3": "1", "item_4": "1",
                                        "just_1": "coletado ontem no hospital, resultado anexado",
                                        "cat_laboratorial": "1", "acao": "salvar"})
        # inclusão manual exige justificativa
        c.post(f"/pedidos/{ped}", data={"acao": "adicionar", "categoria": "laboratorial", "grupo": "bioquímica sérica",
                                        "nome": "Lipase", "justificativa": ""})
        self.assertEqual(len(json.loads(conn.execute("SELECT itens_json FROM pedidos WHERE id=?", (ped,)).fetchone()[0])), 4)
        c.post(f"/pedidos/{ped}", data={"acao": "adicionar", "categoria": "laboratorial", "grupo": "bioquímica sérica",
                                        "nome": "Lipase", "justificativa": "autorizado pela Dra. Teste – dor abdominal"})
        itens = json.loads(conn.execute("SELECT itens_json FROM pedidos WHERE id=?", (ped,)).fetchone()[0])
        self.assertEqual(itens[-1]["origem"], "manual")

        # prévia
        r = c.get(f"/pedidos/{ped}/pdf")
        self.assertEqual(r.mimetype, "application/pdf")
        # 7. emitir: sem confirmação não emite
        # ordem do formulário: itens (4 protocolo + 1 manual) + removidos (Plaquetas)
        form = {f"item_{k}": "1" for k in range(4)}
        form.update({"just_5": "coletado ontem no hospital, resultado anexado", "cat_laboratorial": "1",
                     "data_coleta": "2026-11-12", "acao": "emitir", "observacao": "Jejum de 8h"})
        c.post(f"/pedidos/{ped}", data=form)
        self.assertEqual(conn.execute("SELECT status FROM pedidos WHERE id=?", (ped,)).fetchone()[0], "rascunho")
        form["confirmo"] = "1"
        c.post(f"/pedidos/{ped}", data=form)
        p = conn.execute("SELECT * FROM pedidos WHERE id=?", (ped,)).fetchone()
        self.assertEqual(p["status"], "emitido")
        self.assertTrue(p["pdf"].startswith(b"%PDF"))
        saida = os.path.join(_tmp, "pedido_emitido.pdf")
        with open(saida, "wb") as f:
            f.write(p["pdf"])
        print("\nPDF de teste:", saida)

        from pypdf import PdfReader
        txt = PdfReader(io.BytesIO(p["pdf"])).pages[0].extract_text()
        self.assertIn("C10D15", txt)
        self.assertIn("João Fictício Souza", txt)
        self.assertIn("20/05/1966", txt)
        self.assertIn("12/11/2026", txt)
        self.assertIn("Lipase", txt)
        self.assertNotIn("Plaquetas", txt)
        self.assertNotIn("Tórax", txt)
        if os.path.exists(docx_path):
            self.assertIn("COLETAR NO AMAIS", txt)
            self.assertNotIn("_____", txt)  # sem campo de carimbo/assinatura por padrão
        # com a opção ligada no modelo, o campo volta
        c.post("/modelos/1", data={"nome": "m", "mostrar_assinatura": "1", "instrucao_lab": "COLETAR NO AMAIS"})
        txt_b = PdfReader(io.BytesIO(c.get(f"/modelos/1/exemplo.pdf").data)).pages[0].extract_text()
        self.assertIn("_____", txt_b)
        c.post("/modelos/1", data={"nome": "m", "instrucao_lab": "COLETAR NO AMAIS – Laboratório Amais"})

        # emitido = bloqueado
        c.post(f"/pedidos/{ped}", data={"acao": "adicionar", "nome": "X", "justificativa": "teste teste"})
        self.assertEqual(len(json.loads(conn.execute("SELECT itens_json FROM pedidos WHERE id=?", (ped,)).fetchone()[0])), 5)

        # pedido de triagem com 3 categorias
        r = c.post("/pedidos/novo", data={"paciente_id": pac["id"], "ciclo": "", "visita_id": vis["Triagem"],
                                          "data_coleta": "2026-11-20"})
        ped2 = int(r.headers["Location"].rstrip("/").split("/")[-1])
        self.assertEqual(conn.execute("SELECT categorias FROM pedidos WHERE id=?", (ped2,)).fetchone()[0],
                         "laboratorial,imagem,procedimento")
        r = c.get(f"/pedidos/{ped2}/pdf")
        from pypdf import PdfReader as PR
        self.assertEqual(len(PR(io.BytesIO(r.data)).pages), 3)
        with open(os.path.join(_tmp, "pedido_triagem_previa.pdf"), "wb") as f:
            f.write(r.data)

        # médicos: o do modelo entra no cadastro; outro médico escolhido no pedido substitui no PDF
        if os.path.exists(docx_path):
            self.assertEqual(conn.execute("SELECT crm FROM medicos WHERE nome LIKE '%Nathalia%'").fetchone()[0], "CRM/SP 186752")
        c.post("/medicos", data={"nome": "Dr. Fulano Exemplo", "crm": "CRM/SP 999999", "rotulo": "Médico solicitante"})
        dr = conn.execute("SELECT id FROM medicos WHERE crm='CRM/SP 999999'").fetchone()["id"]
        r = c.post("/pedidos/novo", data={"paciente_id": pac["id"], "ciclo": "3", "visita_id": vis["D15"],
                                          "data_coleta": "2026-11-25", "medico_id": dr})
        ped3 = int(r.headers["Location"].rstrip("/").split("/")[-1])
        txt3 = PdfReader(io.BytesIO(c.get(f"/pedidos/{ped3}/pdf").data)).pages[0].extract_text()
        self.assertIn("Dr. Fulano Exemplo", txt3)
        self.assertIn("CRM/SP 999999", txt3)
        self.assertIn("Médico solicitante", txt3)
        self.assertNotIn("Nathalia", txt3)
        # próximo pedido do mesmo paciente já sugere o último médico
        self.assertEqual(c.get(f"/api/paciente/{pac['id']}/contexto").get_json()["medico_sugerido"], dr)
        # trocar o médico na conferência
        nat = conn.execute("SELECT id FROM medicos WHERE id<>?", (dr,)).fetchone()
        if nat:
            c.post(f"/pedidos/{ped3}", data={**{f"item_{k}": "1" for k in range(5)}, "cat_laboratorial": "1",
                                             "medico_id": nat["id"], "acao": "salvar"})
            self.assertEqual(conn.execute("SELECT medico_id FROM pedidos WHERE id=?", (ped3,)).fetchone()[0], nat["id"])

        # leitura da versão na capa do protocolo
        vel = r"C:\Users\Cristiane\Desktop\Trabalho\Protocolos\Pulmão\Velocity.pdf"
        if os.path.exists(vel):
            with open(vel, "rb") as f:
                d = c.post("/protocolos/detectar", data={"arquivo": (io.BytesIO(f.read()), "Velocity.pdf")},
                           content_type="multipart/form-data").get_json()
            self.assertEqual(d["versao"], "Emenda 4 (25/06/2024)")
            self.assertEqual(d["codigo"], "GS-US-624-6376-01")
        d = c.post("/protocolos/detectar", data={"arquivo": (io.BytesIO(protocolo_pdf()), "MCLA-158-CL02_v3_0.pdf")},
                   content_type="multipart/form-data").get_json()
        self.assertEqual(d.get("estudo_id"), estudo)  # reconhece o estudo pelo nome do arquivo
        self.assertEqual(d["versao"], "v3.0")
        # criar estudo novo direto na importação do protocolo
        c.post("/protocolos/novo", data={"estudo_id": "novo", "novo_codigo": "GS-US-624-6376-01", "novo_nome": "VELOCITY-Lung",
                                         "versao": "Emenda 4 (25/06/2024)"})
        self.assertIsNotNone(conn.execute("SELECT id FROM estudos WHERE codigo='GS-US-624-6376-01'").fetchone())

        # nova versão copia a matriz
        r = c.post(f"/protocolos/{pid}/nova-versao", data={"versao": "v4.0"})
        novo = int(r.headers["Location"].split("/")[-1].split("#")[0])
        self.assertEqual(conn.execute("""SELECT COUNT(*) FROM visita_itens vi JOIN visitas v ON v.id=vi.visita_id
                                         WHERE v.protocolo_id=?""", (novo,)).fetchone()[0], 12)

        # páginas renderizam sem erro
        for url in ("/", "/pacientes", "/estudos", "/modelos", "/medicos", "/pedidos", "/auditoria", "/usuarios", "/pedidos/novo",
                    f"/protocolos/{pid}", f"/protocolos/{novo}", f"/pedidos/{ped}", f"/pedidos/{ped2}",
                    f"/pacientes/{pac['id']}", "/pacientes/importar", "/protocolos/novo", "/modelos/1", "/modelos/1/exemplo.pdf"):
            self.assertEqual(c.get(url).status_code, 200, url)
        self.assertGreater(conn.execute("SELECT COUNT(*) FROM auditoria").fetchone()[0], 20)

        # excluir modelo: nunca usado → apaga; já usado → some da lista, pedidos continuam abrindo
        conn.execute("INSERT INTO modelos (nome, padrao, criado_em) VALUES ('Sem uso', 0, '2026-01-01')")
        conn.commit()
        sem_uso = conn.execute("SELECT id FROM modelos WHERE nome='Sem uso'").fetchone()[0]
        c.post(f"/modelos/{sem_uso}/excluir")
        self.assertIsNone(conn.execute("SELECT id FROM modelos WHERE id=?", (sem_uso,)).fetchone())
        conn.execute("INSERT INTO modelos (nome, padrao, criado_em) VALUES ('Outro modelo', 0, '2026-01-01')")
        conn.commit()
        usado = conn.execute("SELECT modelo_id FROM pedidos WHERE modelo_id IS NOT NULL").fetchone()[0]
        nome_usado = conn.execute("SELECT nome FROM modelos WHERE id=?", (usado,)).fetchone()[0]
        c.post(f"/modelos/{usado}/excluir")
        self.assertIsNotNone(conn.execute("SELECT excluido_em FROM modelos WHERE id=?", (usado,)).fetchone()[0])
        self.assertNotIn(f'href="/modelos/{usado}"', c.get("/modelos").get_data(as_text=True), nome_usado)
        self.assertEqual(conn.execute("SELECT nome FROM modelos WHERE padrao=1").fetchone()[0], "Outro modelo")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM pedidos WHERE status='rascunho' AND modelo_id=?",
                                      (usado,)).fetchone()[0], 0)
        for url in (f"/pedidos/{ped}", f"/pedidos/{ped2}", "/modelos", "/"):
            self.assertEqual(c.get(url).status_code, 200, url)
        self.assertEqual(c.post(f"/modelos/{usado}/excluir").status_code, 404)

        # excluir cadastros feitos por engano
        conta = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731
        emitido = conn.execute("SELECT id, paciente_id, protocolo_id, medico_id FROM pedidos WHERE status='emitido'").fetchone()
        c.post(f"/excluir/pedido/{emitido['id']}")                      # emitido: não apaga
        self.assertEqual(conta("SELECT COUNT(*) FROM pedidos WHERE id=?", emitido["id"]), 1)
        c.post(f"/excluir/paciente/{emitido['paciente_id']}")           # tem pedido: não apaga
        self.assertEqual(conta("SELECT COUNT(*) FROM pacientes WHERE id=?", emitido["paciente_id"]), 1)
        c.post(f"/excluir/protocolo/{emitido['protocolo_id']}")         # usado em pedido: não apaga
        self.assertEqual(conta("SELECT COUNT(*) FROM protocolos WHERE id=?", emitido["protocolo_id"]), 1)
        rasc = conn.execute("SELECT id FROM pedidos WHERE status='rascunho'").fetchone()
        if rasc:
            c.post(f"/excluir/pedido/{rasc['id']}")
            self.assertEqual(conta("SELECT COUNT(*) FROM pedidos WHERE id=?", rasc["id"]), 0)
        # paciente, médico, estudo e protocolo novos (sem uso) → apagam
        c.post("/pacientes/novo", data={"nome": "Cadastrado Por Engano"})
        pe = conta("SELECT id FROM pacientes WHERE nome='Cadastrado Por Engano'")
        self.assertIn("Cadastrado Por Engano", c.get("/pacientes").get_data(as_text=True))
        c.post(f"/excluir/paciente/{pe}")
        self.assertEqual(conta("SELECT COUNT(*) FROM pacientes WHERE id=?", pe), 0)
        c.post("/medicos", data={"nome": "Dr. Engano", "crm": "CRM 1"})
        me = conta("SELECT id FROM medicos WHERE nome='Dr. Engano'")
        c.post(f"/excluir/medico/{me}")
        self.assertEqual(conta("SELECT COUNT(*) FROM medicos WHERE id=?", me), 0)
        c.post(f"/excluir/protocolo/{novo}")  # rascunho "v4.0" criado acima, sem pedidos
        self.assertEqual(conta("SELECT COUNT(*) FROM protocolos WHERE id=?", novo), 0)
        self.assertEqual(conta("SELECT COUNT(*) FROM visitas WHERE protocolo_id=?", novo), 0)
        c.post("/estudos", data={"codigo": "ESTUDO-ENGANO"})
        ee = conta("SELECT id FROM estudos WHERE codigo='ESTUDO-ENGANO'")
        c.post(f"/excluir/estudo/{ee}")
        self.assertEqual(conta("SELECT COUNT(*) FROM estudos WHERE id=?", ee), 0)
        c.post(f"/excluir/estudo/{estudo}")                              # tem pacientes: não apaga
        self.assertEqual(conta("SELECT COUNT(*) FROM estudos WHERE id=?", estudo), 1)
        self.assertGreaterEqual(conta("SELECT COUNT(*) FROM auditoria WHERE acao LIKE 'excluiu %'"), 5)
        self.assertEqual(c.post("/excluir/usuario/1").status_code, 404)
        for url in ("/pacientes", "/estudos", "/medicos", "/pedidos", f"/pedidos/{emitido['id']}",
                    f"/pacientes/{emitido['paciente_id']}"):
            self.assertEqual(c.get(url).status_code, 200, url)


class Datas(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(IMP.parse_data("04/03/61"), "1961-03-04")
        self.assertEqual(IMP.parse_data("2026-11-12"), "2026-11-12")
        self.assertEqual(IMP.parse_data(23456), "1964-03-20")
        self.assertIsNone(IMP.parse_data("ontem"))
        self.assertEqual(IMP.formatar_cpf("12345678901"), "123.456.789-01")


if __name__ == "__main__":
    unittest.main()
