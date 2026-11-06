"""Extração OPCIONAL do cronograma (Schedule of Activities) com a API da Anthropic.

Só fica disponível se ANTHROPIC_API_KEY estiver configurada E o pacote `anthropic`
estiver instalado. O PDF do protocolo é enviado à API — confirmar com a coordenação
se isso é permitido pelo patrocinador/contrato antes de ativar.

O resultado NUNCA vai direto para os pedidos: entra no protocolo como rascunho
(origem = 'ia') e precisa ser revisado e validado pela equipe.
"""

import base64
import json
import os

MODELO = os.environ.get("PEDIDOS_IA_MODELO", "claude-opus-5-5").strip()

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["visitas", "itens", "observacoes"],
    "properties": {
        "visitas": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["chave", "nome", "ciclo_de", "ciclo_ate", "janela", "pagina"],
                "properties": {
                    "chave": {"type": "string", "description": "identificador curto único, ex. 'TRIAGEM', 'C1D1', 'C2+D1'"},
                    "nome": {"type": "string", "description": "nome da visita sem o ciclo, ex. 'Triagem', 'D1', 'D15', 'EOT'"},
                    "ciclo_de": {"type": ["integer", "null"]},
                    "ciclo_ate": {"type": ["integer", "null"], "description": "null = em diante (se ciclo_de preenchido)"},
                    "janela": {"type": ["string", "null"]},
                    "pagina": {"type": ["integer", "null"]},
                },
            },
        },
        "itens": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["categoria", "grupo", "nome", "pagina", "visitas", "nota"],
                "properties": {
                    "categoria": {"type": "string", "enum": ["laboratorial", "imagem", "procedimento", "outro"]},
                    "grupo": {"type": "string", "description": "ex. 'HEMATOLOGIA – HEMOGRAMA COMPLETO', 'BIOQUÍMICA SÉRICA'"},
                    "nome": {"type": "string", "description": "nome do exame em português, como num pedido médico"},
                    "pagina": {"type": ["integer", "null"]},
                    "visitas": {"type": "array", "items": {"type": "string"}, "description": "chaves das visitas em que é exigido"},
                    "nota": {"type": ["string", "null"], "description": "condição do protocolo, ex. 'se clinicamente indicado'"},
                },
            },
        },
        "observacoes": {"type": "string"},
    },
}

PROMPT = """Você está lendo o protocolo de um estudo clínico oncológico para montar os pedidos médicos de exames.

Extraia do cronograma de atividades (Schedule of Activities / Tabela de avaliações) e das seções de avaliações laboratoriais/imagem:
1. Todas as visitas (triagem, cada dia de cada ciclo, ciclos repetidos como "ciclo 3 em diante", fim de tratamento, seguimento).
2. Cada exame laboratorial (detalhando os analitos listados no protocolo, ex. painel de bioquímica item a item), exame de imagem
   (com a região: tórax, abdômen, pelve...) e procedimento (ECG, biópsia, ecocardiograma...) e em quais visitas ele é exigido.

Regras:
- Use SOMENTE o que está escrito no protocolo. Não acrescente exames "de rotina" que o protocolo não pede.
- Se a exigência for condicional (ex. "se clinicamente indicado", "apenas mulheres com potencial de engravidar", "a cada 8 semanas"),
  registre a condição em `nota`.
- Informe a página do PDF de onde tirou cada item.
- Escreva os nomes dos exames em português, no estilo de um pedido médico brasileiro, mantendo siglas entre parênteses (ex. "Aspartato aminotransferase (AST)").
- Em `observacoes`, liste dúvidas ou trechos ambíguos que a equipe precisa conferir.
"""


def disponivel():
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def extrair(pdf_bytes):
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"].strip())
    b64 = base64.standard_b64encode(pdf_bytes).decode("ascii")
    with client.beta.messages.stream(
        model=MODELO,
        max_tokens=64000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{
            "role": "user",
            "content": [
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}},
                {"type": "text", "text": PROMPT},
            ],
        }],
    ) as stream:
        resp = stream.get_final_message()
    if resp.stop_reason == "refusal":
        raise RuntimeError("A IA recusou a solicitação. Cadastre o cronograma manualmente.")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("O protocolo é grande demais para uma única extração. Cadastre manualmente.")
    texto = "".join(b.text for b in resp.content if b.type == "text").strip()
    if texto.startswith("```"):
        texto = texto.strip("`").split("\n", 1)[1]
    return json.loads(texto)
