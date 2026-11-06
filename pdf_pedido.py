"""Gera o PDF do pedido no layout do modelo (cabeçalho com imagem, quadro da instituição,
quadro do paciente, seções com caixas marcadas em 2 colunas)."""

import io
import os
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Flowable, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

CATEGORIAS = [
    ("laboratorial", "PEDIDO LABORATORIAL"),
    ("imagem", "PEDIDO DE EXAMES DE IMAGEM"),
    ("procedimento", "PEDIDO DE PROCEDIMENTOS"),
    ("outro", "OUTRAS SOLICITAÇÕES"),
]
CAT_TITULO = dict(CATEGORIAS)


def _fontes():
    candidatos = [
        (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ]
    import reportlab

    base = os.path.join(os.path.dirname(reportlab.__file__), "fonts")
    candidatos.append((os.path.join(base, "Vera.ttf"), os.path.join(base, "VeraBd.ttf")))
    for normal, negrito in candidatos:
        if os.path.exists(normal) and os.path.exists(negrito):
            try:
                pdfmetrics.registerFont(TTFont("PedidoSans", normal))
                pdfmetrics.registerFont(TTFont("PedidoSans-Bold", negrito))
                return "PedidoSans", "PedidoSans-Bold"
            except Exception:
                continue
    return "Helvetica", "Helvetica-Bold"


FONTE, FONTE_B = _fontes()


def esc(s):
    return (str(s or "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def data_br(iso):
    if not iso:
        return ""
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return iso


class CaixaMarcada(Flowable):
    def __init__(self, cor, tam=8.5):
        super().__init__()
        self.cor, self.tam = cor, tam
        self.width = self.height = tam

    def draw(self):
        c, t = self.canv, self.tam
        c.setStrokeColor(self.cor)
        c.setLineWidth(0.7)
        c.rect(0, 0, t, t)
        c.setLineWidth(1.1)
        p = c.beginPath()
        p.moveTo(t * 0.2, t * 0.5)
        p.lineTo(t * 0.42, t * 0.22)
        p.lineTo(t * 0.82, t * 0.82)
        c.drawPath(p, stroke=1, fill=0)


def gerar_pdf(modelo, pedido, rascunho=False):
    """modelo: dict do modelo. pedido: dict com paciente, estudo, rotulo, ciclo_visita, data_coleta,
    itens (lista de dicts categoria/grupo/nome/nota/origem), observacao, rastreio (texto de rodapé),
    paginas_por_categoria (bool)."""
    cor_txt = colors.HexColor(modelo.get("cor_texto") or "#1B3742")
    cor_dest = colors.HexColor(modelo.get("cor_destaque") or "#008080")
    cor_fundo = colors.HexColor(modelo.get("cor_fundo") or "#EAF4F3")

    st = {
        "titulo": ParagraphStyle("t", fontName=FONTE_B, fontSize=14, leading=17, alignment=TA_CENTER, textColor=cor_txt),
        "cx_b": ParagraphStyle("cb", fontName=FONTE_B, fontSize=10.5, leading=12.5, textColor=cor_txt),
        "cx": ParagraphStyle("c", fontName=FONTE, fontSize=9.5, leading=11.5, textColor=cor_txt),
        "rot": ParagraphStyle("r", fontName=FONTE_B, fontSize=8.5, leading=10, textColor=cor_txt),
        "grande": ParagraphStyle("g", fontName=FONTE_B, fontSize=12.5, leading=15, textColor=cor_txt),
        "secao": ParagraphStyle("s", fontName=FONTE_B, fontSize=13.5, leading=17, textColor=cor_dest, spaceBefore=10, spaceAfter=4),
        "grupo": ParagraphStyle("gr", fontName=FONTE_B, fontSize=11, leading=14, textColor=cor_txt, spaceBefore=8, spaceAfter=3),
        "item": ParagraphStyle("i", fontName=FONTE, fontSize=10, leading=12.5, textColor=cor_txt),
        "peq": ParagraphStyle("p", fontName=FONTE, fontSize=8, leading=10, textColor=colors.HexColor("#4b5563")),
        "instr": ParagraphStyle("in", fontName=FONTE_B, fontSize=11.5, leading=14, textColor=cor_txt),
        "ass": ParagraphStyle("a", fontName=FONTE, fontSize=10, leading=12.5, textColor=cor_txt, alignment=TA_CENTER),
    }

    buf = io.BytesIO()
    larg_pag, alt_pag = letter
    margem = 17 * mm
    img_alt = 0
    img = None
    if modelo.get("cabecalho_img"):
        try:
            img = ImageReader(io.BytesIO(modelo["cabecalho_img"]))
            iw, ih = img.getSize()
            img_larg = larg_pag - 2 * margem
            img_alt = img_larg * ih / iw
        except Exception:
            img = None
    topo = 12 * mm + img_alt + 4 * mm

    rodape = pedido.get("rastreio", "")

    def na_pagina(c, doc):
        c.saveState()
        if img is not None:
            c.drawImage(img, margem, alt_pag - 12 * mm - img_alt, width=larg_pag - 2 * margem, height=img_alt,
                        mask="auto")
        c.setFont(FONTE, 7)
        c.setFillColor(colors.HexColor("#6b7280"))
        y = 10 * mm
        for linha in reversed(rodape.split("\n")):
            c.drawString(margem, y, linha)
            y += 8.5
        c.drawRightString(larg_pag - margem, 10 * mm, f"Página {doc.page}")
        if rascunho:
            c.setFillColor(colors.Color(0.85, 0.1, 0.1, alpha=0.13))
            c.setFont(FONTE_B, 46)
            c.translate(larg_pag / 2, alt_pag / 2)
            c.rotate(35)
            c.drawCentredString(0, 0, "RASCUNHO – NÃO VÁLIDO")
        c.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=margem, rightMargin=margem, topMargin=topo,
                            bottomMargin=24 * mm, title=f"Pedido {pedido.get('rotulo', '')}",
                            author=modelo.get("instituicao") or "")
    util = larg_pag - 2 * margem

    def cabecalho():
        out = [Paragraph(esc(modelo.get("titulo") or "SOLICITAÇÃO DE EXAMES – PESQUISA CLÍNICA"), st["titulo"]),
               Spacer(1, 4 * mm)]
        linhas_inst = []
        if modelo.get("endereco"):
            linhas_inst.append(esc(modelo["endereco"]))
        if modelo.get("medico"):
            med = f"{esc(modelo.get('medico_rotulo') or 'Médico solicitante')}: {esc(modelo['medico'])}"
            if modelo.get("crm"):
                med += f"&nbsp;&nbsp;|&nbsp;&nbsp;{esc(modelo['crm'])}"
            linhas_inst.append(med)
        t = Table([[Paragraph(esc(modelo.get("instituicao") or ""), st["cx_b"])],
                   [Paragraph("<br/>".join(linhas_inst), st["cx"])]], colWidths=[util])
        t.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.6, colors.black), ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0), cor_fundo), ("TOPPADDING", (0, 0), (-1, -1), 1.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2), ("LEFTPADDING", (0, 0), (-1, -1), 4)]))
        out += [t, Spacer(1, 4 * mm)]

        pac = pedido["paciente"]
        est = pedido["estudo"]
        estudo_txt = f"Estudo: {esc(est.get('codigo'))}"
        if est.get("nome"):
            estudo_txt += f"&nbsp;&nbsp;|&nbsp;&nbsp;{esc(est['nome'])}"
        cw = [util * 0.5, util * 0.5]
        t = Table([
            [Paragraph("PACIENTE", st["rot"]), Paragraph("DATA DE NASCIMENTO", st["rot"])],
            [Paragraph(esc(pac.get("nome")), st["grande"]), Paragraph(esc(data_br(pac.get("nascimento"))) or "—", st["grande"])],
            [Paragraph(estudo_txt, st["cx"]), Paragraph(f"Participante: {esc(pac.get('participante') or '—')}", st["cx"])],
            [Paragraph(f"Ciclo / visita: <b>{esc(pedido.get('ciclo_visita'))}</b>", st["cx"]),
             Paragraph(f"Data para coleta/realização: <b>{esc(data_br(pedido.get('data_coleta')))}</b>", st["cx"])],
        ], colWidths=cw)
        t.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.6, colors.black), ("BACKGROUND", (0, 0), (-1, 0), cor_fundo),
            ("TOPPADDING", (0, 0), (-1, -1), 1.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
        out += [t, Spacer(1, 2 * mm)]
        return out

    def celula(it):
        txt = esc(it["nome"])
        if it.get("origem") == "manual":
            txt += " <font size=8>†</font>"
        if it.get("nota"):
            txt += f"<br/><font size=8 color='#4b5563'>{esc(it['nota'])}</font>"
        t = Table([[CaixaMarcada(cor_txt), Paragraph(txt, st["item"])]], colWidths=[13, util / 2 - 19])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("RIGHTPADDING", (0, 0), (-1, -1), 2), ("TOPPADDING", (0, 0), (0, 0), 2.2),
                               ("TOPPADDING", (1, 0), (1, 0), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
        return t

    def instrucao(texto):
        t = Table([[Paragraph(esc(texto), st["instr"])]], colWidths=[util])
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), cor_fundo), ("BOX", (0, 0), (-1, -1), 0.8, cor_dest),
                               ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                               ("LEFTPADDING", (0, 0), (-1, -1), 6)]))
        return [t, Spacer(1, 1 * mm)]

    def bloco_categoria(cat, itens):
        out = [Paragraph(f"{esc(pedido.get('rotulo'))}&nbsp;&nbsp;|&nbsp;&nbsp;{CAT_TITULO.get(cat, cat.upper())}", st["secao"])]
        if cat == "laboratorial" and (modelo.get("instrucao_lab") or "").strip():
            out += instrucao(modelo["instrucao_lab"].strip())
        grupos = {}
        for it in itens:  # agrupa mantendo a ordem da 1ª aparição; manuais vão para o fim do seu grupo
            grupos.setdefault(it.get("grupo") or "", []).append(it)
        for g, its in grupos.items():
            its = sorted(its, key=lambda i: i.get("origem") == "manual")
            linhas = []
            for i in range(0, len(its), 2):
                par = its[i:i + 2]
                linhas.append([celula(par[0]), celula(par[1]) if len(par) > 1 else ""])
            t = Table(linhas, colWidths=[util / 2, util / 2])
            t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 2),
                                   ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)]))
            bloco = [Paragraph(esc(g.upper()), st["grupo"])] if g else []
            out.append(KeepTogether(bloco + [t]))
        return out

    def assinatura():
        if not modelo.get("mostrar_assinatura"):
            return []
        out = [Spacer(1, 12 * mm)]
        nome = esc(modelo.get("medico") or "")
        crm = esc(modelo.get("crm") or "")
        t = Table([[Paragraph(f"______________________________________<br/>{nome}<br/>{crm}", st["ass"])]],
                  colWidths=[util * 0.6])
        t.hAlign = "CENTER"
        out.append(t)
        return out

    def rodape_itens(itens):
        out = []
        if any(i.get("origem") == "manual" for i in itens):
            out.append(Spacer(1, 3 * mm))
            out.append(Paragraph("† Item incluído manualmente pela equipe (não listado na matriz do protocolo para esta visita) – "
                                 "justificativa registrada no sistema.", st["peq"]))
        if pedido.get("observacao"):
            out.append(Spacer(1, 3 * mm))
            out.append(Paragraph(f"<b>Observações:</b> {esc(pedido['observacao'])}", st["cx"]))
        return out

    historia = []
    por_cat = {}
    for it in pedido["itens"]:
        por_cat.setdefault(it["categoria"], []).append(it)
    cats = [c for c, _ in CATEGORIAS if c in por_cat]
    separar = pedido.get("paginas_por_categoria", True)
    if not cats:
        historia += cabecalho() + [Paragraph("Nenhum exame selecionado.", st["cx"])]
    for n, cat in enumerate(cats):
        if n == 0 or separar:
            if n > 0:
                historia.append(PageBreak())
            historia += cabecalho()
        historia += bloco_categoria(cat, por_cat[cat])
        if separar or n == len(cats) - 1:
            historia += rodape_itens(por_cat[cat] if separar else pedido["itens"])
            historia += assinatura()
    doc.build(historia, onFirstPage=na_pagina, onLaterPages=na_pagina)
    return buf.getvalue()
