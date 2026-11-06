# Pedidos de Exames – Pesquisa Clínica (OncoClínicas)

Sistema para montar pedidos médicos de exames/procedimentos a partir do **protocolo validado** de cada estudo.
Independente da plataforma Hapvida (banco, login e deploy próprios).

## Como usar (rotina)
1. **Modelos** → importar o modelo de pedido (DOCX/PDF). O sistema lê logo/faixa, título, unidade, endereço, médico e CRM; você confere.
2. **Pacientes → Importar planilha** (.xlsx/.csv) → escolher qual coluna é nome, nascimento, participante, estudo… Reimportar não duplica (atualiza).
3. **Estudos e protocolos** → importar o PDF do protocolo → montar o cronograma:
   visitas (Triagem, D1, D15, ciclos X em diante…), exames (por categoria/grupo) e a **matriz visita × exame**.
   A busca de termos mostra em que páginas o PDF cita cada exame. Ao final, **Validar** (fica registrado quem/quando).
4. **+ Novo pedido** → paciente, ciclo, visita, data → o sistema traz só o que a matriz marca naquela visita →
   conferir (desmarcar exige justificativa; incluir exame fora do protocolo exige justificativa e sai com † no PDF) → **Emitir**.

Regras de segurança: protocolo em rascunho não gera pedido; protocolo validado e pedido emitido ficam bloqueados
(emenda = "Nova versão a partir desta"); o PDF emitido fica guardado com SHA-256; tudo vai para **Auditoria**.

## Rodar
```
python -m pip install -r requirements.txt
python app.py                      # http://127.0.0.1:5050 – banco real pedidos.sqlite3 (1º acesso cria o admin)
python demo_seed.py && python demo_server.py   # demonstração fictícia em :5051 (demo@demo.local / demo12345)
python -m unittest discover -s tests
```

## IA (opcional)
Com `ANTHROPIC_API_KEY` configurada e `pip install anthropic`, aparece o botão **Sugerir cronograma com IA** no protocolo
em rascunho. O PDF é enviado à API da Anthropic — confirmar antes se o contrato com o patrocinador permite.
O resultado entra como rascunho (origem "IA") e precisa ser conferido e validado pela equipe.

## Arquivos
- `app.py` rotas/telas · `db.py` banco SQLite + auditoria · `importadores.py` planilha/modelo/protocolo
- `pdf_pedido.py` gera o PDF no layout do modelo · `ia_protocolo.py` extração opcional com IA
- Banco: `pedidos.sqlite3` (não versionar — contém dados de pacientes). Variável `PEDIDOS_DB` muda o caminho.
