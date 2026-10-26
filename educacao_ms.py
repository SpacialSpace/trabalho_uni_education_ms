"""Matrículas por unidade escolar em Mato Grosso do Sul

Uso:
  python educacao_ms.py                  importa os CSVs da pasta atual e gera dashboard.html
  python educacao_ms.py --pasta dados/   CSVs em outra pasta
  python educacao_ms.py --saida x.html   nome do HTML gerado
Precisa do arquivo template.html na mesma pasta deste script.
"""

# 2025 sem dados. 2024 e 2026 foram feitos ainda durante o período letivo.

import argparse
import csv
import datetime
import glob
import json
import os
import re
import sqlite3
import urllib.request

BANCO = "educacao_ms.db"
AQUI = os.path.dirname(os.path.abspath(__file__))

COLUNAS = ["ano", "municipio", "escola", "tipo", "matriculas_total", "apos_censo",
           "transferidos", "cancelados", "abandono", "aprovados", "reprovados",
           "cursando", "ajustado"]


def classificar_tipo(nome):
  """Deduz o tipo da unidade pelo nome."""
  u = nome.upper()
  if "INDÍG" in u or "INDIG" in u:
    return "Indígena"
  if "PROFISSIONAL" in u:
    return "Educação profissional"
  if "CEEJA" in u or "JOVENS E ADULTOS" in u:
    return "EJA"
  prefixo = re.split(r"[\s.\-–]", u.strip())[0]
  return {"EE": "Escola estadual", "ESCOLA": "Escola estadual", "CEEP": "Educação profissional",
          "CEI": "CEI", "CENTRO": "Centro de educação"}.get(prefixo, "Outros")


# Criando a classe (POO)
class RegistroEscolar:

  def __init__(self, linha):
    n = lambda campo: int(linha.get(campo) or 0)
    self.ano = n("AnoReferencia")
    self.municipio = (linha.get("NomeMunicipio") or "").strip()
    self.escola = (linha.get("NomeUnidadeEscolar") or "").strip()
    self.tipo = classificar_tipo(self.escola)
    self.matriculas_total = n("MatriculasTotal")
    self.apos_censo = n("MatriculaAposCenso")
    self.transferidos = n("Transferidos")
    self.cancelados = n("Cancelados")
    self.abandono = n("Abandono")
    self.aprovados = n("Aprovados")
    self.reprovados = n("Reprovados")
    self.cursando = n("Cursando")
    # EJA não tem reprovação, reprovados sem nenhum aprovado é erro de lançamento
    self.ajustado = 0
    if self.tipo == "EJA" and self.aprovados == 0 and self.reprovados > 0:
      self.reprovados, self.ajustado = 0, 1

  def valores(self):
    return tuple(getattr(self, c) for c in COLUNAS)


# Config do banco de dados
def criar_banco():
  conexao = sqlite3.connect(BANCO)
  cursor = conexao.cursor()
  existentes = [c[1] for c in cursor.execute("PRAGMA table_info(escolas)")]
  if existentes and existentes != ["id"] + COLUNAS:
    print("Banco antigo detectado: recriando a tabela com as novas colunas.")
    cursor.execute("DROP TABLE escolas")
  cursor.execute(f"""
      CREATE TABLE IF NOT EXISTS escolas (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ano INTEGER, municipio TEXT, escola TEXT, tipo TEXT,
          matriculas_total INTEGER, apos_censo INTEGER, transferidos INTEGER,
          cancelados INTEGER, abandono INTEGER, aprovados INTEGER,
          reprovados INTEGER, cursando INTEGER, ajustado INTEGER
      )
  """)
  cursor.execute("CREATE INDEX IF NOT EXISTS idx_ano_mun ON escolas (ano, municipio)")
  conexao.commit()
  conexao.close()


def ano_ja_importado(cursor, ano):
  cursor.execute("SELECT COUNT(*) FROM escolas WHERE ano = ?", (ano,))
  return cursor.fetchone()[0] > 0


def inserir(cursor, linhas):
  registros = [RegistroEscolar(l) for l in linhas]
  cursor.executemany(
      f"INSERT INTO escolas ({', '.join(COLUNAS)}) VALUES ({', '.join('?' * len(COLUNAS))})",
      [r.valores() for r in registros])
  return len(registros)


# Importação a partir de arquivo CSV
def importar_csv_para_banco(nome_arquivo):
  conexao = sqlite3.connect(BANCO)
  cursor = conexao.cursor()
  try:
    ano = int(nome_arquivo.split("-")[-1].replace(".csv", ""))
    if ano_ja_importado(cursor, ano):
      print(f"{nome_arquivo}: já importado anteriormente.")
      return
    # utf-8-sig remove o marcador (BOM) que aparece no início desses arquivos
    with open(nome_arquivo, mode="r", encoding="utf-8-sig") as arquivo:
      total = inserir(cursor, csv.DictReader(arquivo))
    conexao.commit()
    print(f"{nome_arquivo}: {total} registros importados.")
  except Exception as e:
    print(f"Erro ao ler {nome_arquivo}: {e}")
  finally:
    conexao.close()

# Criando a função de filtragem
def filtrar_dinamico(municipio=None, ano=None, escola=None, tipo=None, min_aprovados=None):
  conexao = sqlite3.connect(BANCO)
  cursor = conexao.cursor()
  query = "SELECT * FROM escolas WHERE 1=1"
  parametros = []
  if municipio:
    query += " AND municipio = ?"
    parametros.append(municipio)
  if ano:
    query += " AND ano = ?"
    parametros.append(ano)
  if escola:
    query += " AND escola LIKE ?"
    parametros.append(f"%{escola}%")
  if tipo:
    query += " AND tipo = ?"
    parametros.append(tipo)
  if min_aprovados is not None:
    query += " AND aprovados >= ?"
    parametros.append(min_aprovados)
  cursor.execute(query, parametros)
  resultados = cursor.fetchall()
  conexao.close()
  return resultados


# Gera o dashboard HTML a partir do que está no banco
def gerar_dashboard(saida):
  conexao = sqlite3.connect(BANCO)
  linhas = conexao.execute(
      "SELECT ano, municipio, escola, tipo, matriculas_total, apos_censo, transferidos,"
      " cancelados, aprovados, reprovados, cursando, ajustado FROM escolas ORDER BY id").fetchall()
  conexao.close()
  gerar_html(linhas, saida)


def gerar_html(linhas, saida):
  """Monta o HTML a partir de linhas (ano, município, escola, tipo, total, apos, tr, ca, ap, rp, cur, ajustado)."""
  tabelas = {"m": {}, "u": {}, "t": {}}
  def indice(chave, valor):
    return tabelas[chave].setdefault(valor, len(tabelas[chave]))
  rows, tot_ano, cur_ano = [], {}, {}
  for ano, mun, esc, tipo, total, apos, tr, ca, ap, rp, cur, _ in linhas:
    tot_ano[ano] = tot_ano.get(ano, 0) + total
    cur_ano[ano] = cur_ano.get(ano, 0) + cur
    rows.append([ano, indice("m", mun), indice("u", esc), indice("t", tipo), total, apos, tr, ca, ap, rp])
  # Ano parcial: mais de 20% dos estudantes ainda constam como "cursando"
  parciais = sorted(a for a in tot_ano if tot_ano[a] and cur_ano[a] / tot_ano[a] > 0.2)
  nomes = lambda k: [x for x, _ in sorted(tabelas[k].items(), key=lambda kv: kv[1])]
  dados = {"MUN": nomes("m"), "UNI": nomes("u"), "TIPO": nomes("t"), "ROWS": rows, "PARC": parciais,
           "AJ": sum(l[-1] for l in linhas), "GER": datetime.date.today().strftime("%d/%m/%Y")}
  with open(os.path.join(AQUI, "template.html"), encoding="utf-8") as f:
    modelo = f.read()
  html = modelo.replace("/*DATA*/", "const DB=" + json.dumps(dados, ensure_ascii=False, separators=(",", ":")) + ";")
  with open(saida, "w", encoding="utf-8") as f:
    f.write(html)
  print(f"Dashboard gerado: {saida} ({len(rows)} linhas, anos parciais: {parciais}).")


if __name__ == "__main__":
  p = argparse.ArgumentParser()
  p.add_argument("--api", action="store_true", help="importa pela API em vez dos CSVs")
  p.add_argument("--pasta", default=".", help="pasta com os CSVs")
  p.add_argument("--saida", default="dashboard.html")
  a = p.parse_args()

  criar_banco()
  if a.api:
    for ano in range(2018, datetime.date.today().year + 1):
      importar_api_para_banco(ano)
  else:
    for arquivo in sorted(glob.glob(os.path.join(a.pasta, "matriculas-por-unidade-escolar-*.csv"))):
      importar_csv_para_banco(arquivo)
  gerar_dashboard(a.saida)
