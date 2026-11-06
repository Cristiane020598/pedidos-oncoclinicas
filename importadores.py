"""Leitura de arquivos: planilha de pacientes, modelo de pedido (DOCX/PDF/imagem)
e protocolo do estudo (PDF)."""

import csv
import os
import hashlib
import io
import re
import unicodedata
from datetime import date, datetime, timedelta


def sem_acento(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


# ---------------------------------------------------------------- planilha

CAMPOS_PACIENTE = [
    ("nome", "Nome do paciente", True),
    ("nascimento", "Data de nascimento", False),
    ("cpf", "CPF", False),
    ("participante", "Nº do participante (ID no estudo)", False),
    ("estudo", "Estudo (código)", False),
    ("telefone", "Telefone", False),
    ("medico", "Médico responsável", False),
    ("observacao", "Observação", False),
]

PALPITES = {
    "nome": ["nome", "paciente", "nome do paciente", "nome completo", "participante nome", "name", "patient"],
    "nascimento": ["nascimento", "data de nascimento", "dt nasc", "dt_nasc", "data nasc", "dn", "birth", "dob"],
    "cpf": ["cpf"],
    "participante": ["participante", "n participante", "numero participante", "id participante", "subject",
                     "subject id", "screening number", "id estudo", "randomizacao", "numero do participante"],
    "estudo": ["estudo", "protocolo", "codigo estudo", "study", "codigo do estudo"],
    "telefone": ["telefone", "celular", "fone", "whatsapp", "contato", "phone"],
    "medico": ["medico", "medica", "investigador", "pi", "medico responsavel"],
    "observacao": ["observacao", "obs", "observacoes", "comentario"],
}


def ler_planilha(nome_arquivo, conteudo):
    """Retorna (colunas, linhas[list[dict]]) de um .xlsx/.xlsm/.csv."""
    nome = nome_arquivo.lower()
    if nome.endswith(".csv"):
        texto = None
        for enc in ("utf-8-sig", "latin-1"):
            try:
                texto = conteudo.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        amostra = texto[:2000]
        sep = ";" if amostra.count(";") > amostra.count(",") else ","
        leitor = csv.reader(io.StringIO(texto), delimiter=sep)
        brutas = [r for r in leitor]
    else:
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        brutas = [list(r) for r in ws.iter_rows(values_only=True)]
        wb.close()

    brutas = [r for r in brutas if any(v not in (None, "") for v in r)]
    if not brutas:
        return [], []
    # cabeçalho = primeira linha com pelo menos 2 células de texto
    idx = 0
    for i, r in enumerate(brutas[:10]):
        if sum(1 for v in r if isinstance(v, str) and v.strip()) >= 2:
            idx = i
            break
    cab = []
    for j, v in enumerate(brutas[idx]):
        nomecol = str(v).strip() if v not in (None, "") else f"Coluna {j + 1}"
        while nomecol in cab:
            nomecol += "_"
        cab.append(nomecol)
    linhas = []
    for r in brutas[idx + 1:]:
        linhas.append({cab[j]: (r[j] if j < len(r) else None) for j in range(len(cab))})
    return cab, linhas


def palpite_mapeamento(colunas):
    mapa = {}
    usadas = set()
    norm = {c: sem_acento(re.sub(r"[_\-\.º°#]", " ", c)).replace("  ", " ") for c in colunas}
    for campo, chaves in PALPITES.items():
        for c in colunas:
            if c in usadas:
                continue
            if norm[c] in chaves:
                mapa[campo] = c
                usadas.add(c)
                break
        if campo in mapa:
            continue
        for c in colunas:
            if c in usadas:
                continue
            if any(k in norm[c] for k in chaves if len(k) > 3):
                mapa[campo] = c
                usadas.add(c)
                break
    return mapa


def parse_data(v):
    """Aceita datetime, date, número serial do Excel, dd/mm/aaaa, aaaa-mm-dd. Retorna ISO ou None."""
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, (int, float)) and 1 < v < 80000:
        return (date(1899, 12, 30) + timedelta(days=int(v))).isoformat()
    s = str(v).strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            d = datetime.strptime(s, fmt).date()
            if d.year > date.today().year:  # 2 dígitos virando futuro
                d = d.replace(year=d.year - 100)
            return d.isoformat()
        except ValueError:
            continue
    return None


def limpar_texto(v):
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = re.sub(r"\s+", " ", str(v)).strip()
    return s or None


def formatar_cpf(v):
    s = re.sub(r"\D", "", str(v or ""))
    if not s:
        return None
    s = s.zfill(11)[-11:]
    return f"{s[:3]}.{s[3:6]}.{s[6:9]}-{s[9:]}"


# ---------------------------------------------------------------- modelo de pedido

RE_MEDICO = re.compile(r"(M[ée]dic[oa]\s+solicitante|Solicitante|M[ée]dic[oa])\s*:\s*(.+?)(?:\s*\|\s*|\s{2,}|$)", re.I)
RE_CRM = re.compile(r"(CRM\s*[/\-]?\s*[A-Z]{0,2}\s*[:\-]?\s*\d{3,7}(?:\s*[A-Z]{2})?)", re.I)


def _info_de_linhas(linhas):
    info = {}
    for ln in linhas:
        m = RE_MEDICO.search(ln)
        if m and "medico" not in info:
            info["medico_rotulo"] = m.group(1).strip().capitalize()
            info["medico"] = m.group(2).strip()
        m = RE_CRM.search(ln)
        if m and "crm" not in info:
            info["crm"] = re.sub(r"\s+", " ", m.group(1)).strip()
    return info


def ler_modelo(nome_arquivo, conteudo):
    """Extrai do modelo: título, instituição, endereço, médico, CRM, cores e a imagem de cabeçalho.
    Tudo fica editável na tela antes de salvar."""
    nome = nome_arquivo.lower()
    info = {"arquivo_nome": nome_arquivo}
    if nome.endswith(".docx"):
        import docx

        d = docx.Document(io.BytesIO(conteudo))
        paras = [p.text.strip() for p in d.paragraphs if p.text.strip()]
        if paras:
            info["titulo"] = paras[0]
        linhas_tab = []
        for t in d.tables:
            for r in t.rows:
                for c in r.cells:
                    linhas_tab.extend([x.strip() for x in c.text.split("\n") if x.strip()])
        if d.tables:
            t0 = d.tables[0]
            cel = [c.text.strip() for r in t0.rows for c in r.cells]
            if cel:
                info["instituicao"] = cel[0].split("\n")[0]
            resto = "\n".join(cel[1:]).split("\n")
            end = [x for x in resto if x and not RE_MEDICO.search(x) and not RE_CRM.search(x)]
            if end:
                info["endereco"] = end[0]
        info.update(_info_de_linhas(linhas_tab + paras))
        xml = d.element.xml
        cores = re.findall(r'w:color w:val="([0-9A-Fa-f]{6})"', xml)
        fundos = re.findall(r'w:fill="([0-9A-Fa-f]{6})"', xml)
        if cores:
            from collections import Counter

            cont = Counter(c.upper() for c in cores)
            info["cor_texto"] = "#" + cont.most_common(1)[0][0]
            outras = [c for c, _ in cont.most_common() if c != cont.most_common(1)[0][0]]
            if outras:
                info["cor_destaque"] = "#" + outras[0]
        if fundos:
            info["cor_fundo"] = "#" + fundos[0].upper()
        for rel in d.part.rels.values():
            if "image" in rel.reltype:
                info["cabecalho_img"] = rel.target_part.blob
                info["cabecalho_mime"] = rel.target_part.content_type
                break
    elif nome.endswith(".pdf"):
        from pypdf import PdfReader

        r = PdfReader(io.BytesIO(conteudo))
        p0 = r.pages[0]
        linhas = [x.strip() for x in (p0.extract_text() or "").split("\n") if x.strip()]
        for ln in linhas:
            if re.search(r"SOLICITA|PEDIDO", ln, re.I):
                info["titulo"] = ln
                break
        for ln in linhas:
            if "|" in ln and not RE_MEDICO.search(ln) and "titulo" in info and ln != info["titulo"]:
                info["instituicao"] = ln
                break
        info.update(_info_de_linhas(linhas))
        try:
            imgs = list(p0.images)
            if imgs:
                maior = max(imgs, key=lambda im: len(im.data))
                info["cabecalho_img"] = maior.data
                info["cabecalho_mime"] = "image/png" if maior.name.lower().endswith(".png") else "image/jpeg"
        except Exception:
            pass
    elif nome.endswith((".png", ".jpg", ".jpeg")):
        info["cabecalho_img"] = conteudo
        info["cabecalho_mime"] = "image/png" if nome.endswith(".png") else "image/jpeg"
    else:
        raise ValueError("Formato não suportado. Use DOCX, PDF, PNG ou JPG.")
    return info


# ---------------------------------------------------------------- protocolo (PDF)

def ler_protocolo_pdf(conteudo):
    from pypdf import PdfReader

    r = PdfReader(io.BytesIO(conteudo))
    paginas = []
    for i, p in enumerate(r.pages, start=1):
        try:
            txt = p.extract_text() or ""
        except Exception:
            txt = ""
        paginas.append((i, txt))
    return {"sha256": hashlib.sha256(conteudo).hexdigest(), "paginas": paginas}


MESES = {"janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
         "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12, "january": 1, "february": 2, "march": 3,
         "april": 4, "may": 5, "june": 6, "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
         "december": 12, "jan": 1, "feb": 2, "fev": 2, "mar": 3, "apr": 4, "abr": 4, "jun": 6, "jul": 7, "aug": 8,
         "ago": 8, "sep": 9, "set": 9, "oct": 10, "out": 10, "nov": 11, "dec": 12, "dez": 12}
_MES_RX = "|".join(sorted({k for k in MESES} | {"março"}, key=len, reverse=True))
RE_DATA_EXT = re.compile(rf"(\d{{1,2}})(?:\s+de)?[\s\-]+({_MES_RX})\.?(?:\s+de)?[\s\-,]+(\d{{4}})", re.I)
RE_DATA_EN = re.compile(rf"({_MES_RX})\.?\s+(\d{{1,2}}),?\s+(\d{{4}})", re.I)
RE_DATA_NUM = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b")


def _data_br_de_texto(s):
    m = RE_DATA_EXT.search(s)
    if m:
        d, mes, a = m.group(1), MESES.get(sem_acento(m.group(2)).rstrip(".")), m.group(3)
        if mes:
            return f"{int(d):02d}/{mes:02d}/{a}"
    m = RE_DATA_EN.search(s)
    if m:
        mes = MESES.get(sem_acento(m.group(1)))
        if mes:
            return f"{int(m.group(2)):02d}/{mes:02d}/{m.group(3)}"
    m = RE_DATA_NUM.search(s)
    if m:
        return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{m.group(3)}"
    return None


def _valor_rotulado(texto, rotulos):
    """Procura 'Rótulo: valor' (valor na mesma linha ou na seguinte). Exige os dois-pontos."""
    for rot in rotulos:
        m = re.search(r"(?:^|\n)[^\n]{0,30}?" + rot + r"\s*:\s*\n?\s*([^\n]{2,160})", texto, re.I)
        if m and m.group(1).strip(" :"):
            return m.group(1).strip(" :.;")
    return None


def _versao_do_nome_arquivo(nome):
    """ex.: PIEMONTE_Protocolo_V6.pdf, ..._v4_00_20250220..., ..._versao_3_06Maio2025..."""
    n = sem_acento(os.path.splitext(nome or "")[0]).replace("-", "_")
    m = re.search(r"(?:^|_)(?:v|versao|version|ver)_?(\d+)(?:[_.](\d{1,2})(?=_|$))?(?=_|$|[a-z])", n)
    if not m:
        return None
    v = m.group(1) + (f".{m.group(2)}" if m.group(2) else "")
    resto = n[m.end():]
    data = None
    m2 = re.search(r"(20\d{2})(\d{2})(\d{2})", resto)
    if m2:
        data = f"{m2.group(3)}/{m2.group(2)}/{m2.group(1)}"
    else:
        m2 = re.search(rf"(\d{{1,2}})({_MES_RX})(\d{{2,4}})", resto, re.I)
        if m2 and MESES.get(m2.group(2).lower()):
            a = m2.group(3) if len(m2.group(3)) == 4 else "20" + m2.group(3)
            data = f"{int(m2.group(1)):02d}/{MESES[m2.group(2).lower()]:02d}/{a}"
    return f"v{v}" + (f" ({data})" if data else "")


SIGLAS_COMUNS = {"ECG", "TC", "RM", "CPCNP", "CPCNPM", "IMC", "PK", "PD", "IST", "CEP", "TCLE", "FDA", "EMA", "ANVISA",
                 "RECIST", "ECOG", "SAE", "EA", "EAG", "IV", "VO", "DNA", "RNA", "HIV", "CRF", "ECRF", "IRB", "IEC"}


def detectar_dados_protocolo(conteudo, nome_arquivo=None, paginas_max=4):
    """Lê a capa do protocolo e sugere versão, código, título curto, patrocinador e NCT.
    É só sugestão: o usuário confere antes de salvar."""
    from pypdf import PdfReader

    r = PdfReader(io.BytesIO(conteudo))
    texto = "\n".join((r.pages[i].extract_text() or "") for i in range(min(paginas_max, len(r.pages))))
    texto = re.sub(r"[ \t]+", " ", texto)
    out = {"paginas": len(r.pages)}

    bruto = _valor_rotulado(texto, [r"Vers[ãa]o\s*/\s*Data do Protocolo", r"Vers[ãa]o e Data do Protocolo",
                                    r"Protocol Version\s*(?:and|/)\s*Date", r"Vers[ãa]o e data",
                                    r"Vers[ãa]o do Protocolo", r"Vers[ãa]o n[úu]mero", r"Protocol Version",
                                    r"Version Number", r"Document Version", r"Vers[ãa]o do documento",
                                    r"Vers[ãa]o", r"Version"])
    if bruto and not re.search(r"\d", bruto):
        bruto = None
    if not bruto:
        m = re.search(r"\b((?:Emenda|Amendment)\s*(?:n[ºo°.]\s*)?\d+[^\n]{0,40})", texto, re.I) or \
            re.search(r"\b((?:Vers[ãa]o|Version)\s*(?:Final\s*)?v?\d+(?:\.\d+)*[^\n]{0,40})", texto, re.I)
        bruto = m.group(1).strip() if m else None
    if bruto and not re.search(r"(?:Vers[ãa]o|Version|Emenda|Amendment|\bv)\s*", bruto, re.I):
        bruto = "Versão " + bruto
    if bruto:
        data = _data_br_de_texto(bruto)
        emenda = re.search(r"(?:Emenda|Amendment)\s*(?:n[ºo°.]\s*)?(\d+)", bruto, re.I)
        versao = re.search(r"(?:Vers[ãa]o|Version|\bv)\s*(?:Final\s*)?(\d+(?:\.\d+)*)", bruto, re.I)
        partes = []
        if versao:
            partes.append(f"v{versao.group(1)}")
        if emenda:
            partes.append(f"Emenda {emenda.group(1)}")
        if not partes:
            partes.append(re.sub(RE_DATA_EXT.pattern, "", bruto, flags=re.I).strip(" :–-,") or bruto)
        out["versao"] = " – ".join(partes) + (f" ({data})" if data else "")
        out["versao_texto"] = bruto
    pelo_nome = _versao_do_nome_arquivo(nome_arquivo)
    if pelo_nome:
        out["versao_arquivo"] = pelo_nome
        # número do texto não aparece no nome do arquivo (ex.: marcas de revisão "23.0") -> preferir o nome
        num_txt = re.search(r"v(\d+(?:\.\d+)*)", out.get("versao", ""))
        num_arq = re.search(r"v(\d+(?:\.\d+)*)", pelo_nome)
        if "versao" not in out or (num_txt and num_arq and float(num_txt.group(1).split(".")[0]) !=
                                   float(num_arq.group(1).split(".")[0]) and not re.search("Emenda", out["versao"])):
            out["versao_alternativa"] = out.get("versao")
            out["versao"] = pelo_nome
            out["versao_origem"] = "nome do arquivo"
        elif "(" not in out["versao"] and "(" in pelo_nome:  # mesmo número: aproveita a data do nome do arquivo
            out["versao"] += " " + pelo_nome[pelo_nome.index("("):]

    cod = _valor_rotulado(texto, [r"ID do Protocolo", r"N[úu]mero do Protocolo", r"C[óo]digo do Protocolo",
                                  r"C[óo]digo (?:do|de) Estudo", r"Protocol (?:Number|No\.?|ID|Identifier|Code)",
                                  r"Study (?:Number|Code|ID)", r"Protocolo n[ºo°]"])
    if cod:
        cod = re.split(r"\s{2,}|\s+(?=[A-ZÁ-Ú][a-zá-ú]+:)", cod)[0].strip()
        out["codigo"] = cod.split()[0] if len(cod.split()) > 1 and re.search(r"\d", cod.split()[0]) else cod
    nct = re.search(r"\bNCT\d{8}\b", texto)
    if nct:
        out["nct"] = nct.group(0)
    pat = _valor_rotulado(texto, [r"Patrocinador", r"Sponsor"])
    if pat:
        partes = [x.strip() for x in pat.split(",")]
        if len(partes) > 1 and re.search(r"\d", ",".join(partes[1:])):  # corta endereço
            pat = partes[0]
        out["patrocinador"] = pat
    for m in re.finditer(r"\(([A-Z][A-Z0-9\-]{2,}(?:-[A-Za-z0-9]+)?)\)", texto):
        tok = m.group(1)
        if tok.upper() not in SIGLAS_COMUNS and len(re.sub(r"[^A-Za-z]", "", tok)) >= 4 and (len(tok) >= 5 or "-" in tok):
            out["titulo_curto"] = tok
            break
    if "titulo_curto" not in out:
        m = re.search(r"\b[A-Z]{2,}\d{2,}\s*[–-]\s*([A-Z][A-Z\-]{3,})\s*[–-]", texto)  # EMS1020 – PIEMONTE – ...
        if m:
            out["titulo_curto"] = m.group(1)
    return out


# Termos usados apenas para LOCALIZAR no texto do protocolo onde cada exame aparece.
# Não viram pedido sozinhos: a equipe decide o que entra na matriz do protocolo.
TERMOS = {
    "laboratorial": {
        "HEMATOLOGIA": ["hemograma|complete blood count|cbc|hematology", "hemoglobin[a]?", "hematocrit|hematócrito",
                        "plaquetas|platelet", "neutr[oó]filos|neutrophil|anc\\b", "leuc[oó]citos|white blood cell|wbc",
                        "eritr[oó]citos|red blood cell|rbc"],
        "BIOQUÍMICA SÉRICA": ["bioqu[ií]mica|chemistry", "s[oó]dio|sodium", "pot[aá]ssio|potassium", "creatinina|creatinine",
                              "ureia|bun\\b|blood urea", "albumina|albumin", "bilirrubina|bilirubin", "\\bast\\b|aspartate",
                              "\\balt\\b|alanine", "fosfatase alcalina|alkaline phosphatase", "\\bldh\\b|lactate dehydrogenase",
                              "magn[eé]sio|magnesium", "f[oó]sforo|phosph(or|at)", "c[aá]lcio|calcium", "glicose|glucose",
                              "amilase|amylase", "lipase", "\\bggt\\b|gamma-glutamyl", "cloro|chloride", "prote[ií]na total|total protein"],
        "COAGULAÇÃO": ["coagula[cç][aã]o|coagulation", "\\binr\\b", "\\btp\\b|prothrombin", "ttpa|aptt|\\bptt\\b", "fibrinog[eê]nio|fibrinogen"],
        "ENDÓCRINO": ["\\btsh\\b", "t4 livre|free t4|\\bft4\\b", "\\bt3\\b", "cortisol"],
        "URINA": ["urin[aá]lise|urinalysis|urina tipo|eas\\b", "propor[cç][aã]o prote[ií]na|protein.creatinine ratio|upcr"],
        "SOROLOGIAS": ["hepatite b|hepatitis b|hbsag|anti-hbc", "hepatite c|hepatitis c|hcv", "\\bhiv\\b"],
        "GRAVIDEZ": ["beta.?hcg|pregnancy test|teste de gravidez"],
        "MARCADORES": ["\\bcea\\b", "ca ?19-9", "ca ?125", "\\bpsa\\b"],
        "FARMACOCINÉTICA/BIOMARCADORES": ["farmacocin[eé]tica|pharmacokinetic|\\bpk\\b", "antidrug|anti-drug|\\bada\\b|imunogenicidade|immunogenicity",
                                          "biomarc|biomarker", "ctdna|circulating tumor dna"],
    },
    "imagem": {
        "TOMOGRAFIA / RESSONÂNCIA": ["tomografia|computed tomography|\\bct\\b scan|\\bct\\b", "resson[aâ]ncia|magnetic resonance|\\bmri\\b",
                                     "t[oó]rax|chest", "abd[oô]men|abdomen", "pelve|pelvis", "cr[aâ]nio|brain|head"],
        "OUTROS EXAMES DE IMAGEM": ["pet.?ct|pet scan", "cintilografia|bone scan", "ecocardiograma|echocardiogram|muga",
                                    "mamografia|mammogra", "ultrassom|ultrasound", "recist"],
    },
    "procedimento": {
        "CARDIOLÓGICO": ["eletrocardiograma|electrocardiogram|\\becg\\b|\\bekg\\b"],
        "BIÓPSIA / TECIDO": ["bi[oó]psia|biopsy", "tecido tumoral|tumor tissue|archival tissue"],
        "AVALIAÇÕES": ["sinais vitais|vital signs", "exame f[ií]sico|physical exam", "ecog", "oftalmol|ophthalm"],
    },
}

RE_SOA = re.compile(r"schedule of (activities|assessments|events)|cronograma de (atividades|avalia)|"
                    r"tabela de (atividades|avalia)|fluxograma do estudo|study flow ?chart", re.I)


def localizar_termos(paginas):
    """paginas: list[(n, texto)]. Retorna páginas do cronograma e ocorrências de termos por página."""
    soa = [n for n, t in paginas if RE_SOA.search(t or "")]
    achados = []
    for cat, grupos in TERMOS.items():
        for grupo, termos in grupos.items():
            for termo in termos:
                rx = re.compile(termo, re.I)
                pags = []
                trecho = None
                for n, t in paginas:
                    m = rx.search(t or "")
                    if m:
                        pags.append(n)
                        if trecho is None:
                            a, b = max(0, m.start() - 60), min(len(t), m.end() + 60)
                            trecho = re.sub(r"\s+", " ", t[a:b])
                if pags:
                    rotulo = termo.split("|")[0].replace("\\b", "").replace("[a]?", "a").replace("[oó]", "ó") \
                        .replace("[eé]", "é").replace("[ií]", "í").replace("[aá]", "á").replace("[aâ]", "â") \
                        .replace("[cç][aã]o", "ção").replace("[eê]", "ê").replace("[oô]", "ô").replace(".?", " ")
                    achados.append({"categoria": cat, "grupo": grupo, "termo": rotulo, "paginas": pags,
                                    "no_cronograma": [p for p in pags if p in soa], "trecho": trecho})
    return {"paginas_cronograma": soa, "achados": achados}
