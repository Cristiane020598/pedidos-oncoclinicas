"""Sobe o app com um banco VAZIO (manual_treino.sqlite3) na porta 5052 — usado para gravar o manual de treinamento.
Uso: python manual_server.py   (apague manual_treino.sqlite3 para começar do zero de novo)
"""
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
os.environ["PEDIDOS_DB"] = os.path.join(AQUI, "manual_treino.sqlite3")
sys.path.insert(0, AQUI)

from app import app  # noqa: E402

if __name__ == "__main__":
    app.run(debug=False, host="127.0.0.1", port=5052)
