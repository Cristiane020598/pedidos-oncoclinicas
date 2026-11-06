"""Sobe o app com o banco de DEMONSTRAÇÃO (dados fictícios) na porta 5051.
Uso: python demo_seed.py   (uma vez)  e depois  python demo_server.py
Login: demo@demo.local / demo12345
"""
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
os.environ["PEDIDOS_DB"] = os.path.join(AQUI, "pedidos_demo.sqlite3")
sys.path.insert(0, AQUI)

from app import app  # noqa: E402

if __name__ == "__main__":
    app.run(debug=False, host="127.0.0.1", port=5051)
