# -*- coding: utf-8 -*-
"""
===============================================================================
 PAINEL GERENCIAL DE ESTUDOS E REPETIÇÃO ESPAÇADA
-------------------------------------------------------------------------------
 Aplicativo web (Streamlit) que lê a exportação do aplicativo de estudos e
 transforma os dados brutos em dashboard de desempenho, cronograma de revisões
 espaçadas (1/7/28 dias) e diagnóstico de metas por disciplina.

 COMO EXECUTAR
   1) pip install -r requirements.txt
   2) streamlit run painel_estudos.py
   O navegador abre sozinho em http://localhost:8501

 As marcações de revisão e as metas são gravadas em um arquivo JSON ao lado
 deste script, então continuam salvas quando você fecha e abre o app de novo.
===============================================================================
"""

from __future__ import annotations

import io
import json
import os
import unicodedata
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# =============================================================================
# CONFIGURAÇÃO GERAL
# =============================================================================

APP_TITULO = "Painel Gerencial de Estudos e Repetição Espaçada"
ARQUIVO_ESTADO = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              ".painel_estudos_estado.json")

CICLOS = [("Rev. 1D", 1), ("Rev. 7D", 7), ("Rev. 28D", 28)]

# paleta (mesma identidade visual da planilha)
NAVY, BLUE, CYAN = "#1F3864", "#2E75B6", "#4FA3D1"
VERDE, AMBAR, VERMELHO, CINZA = "#2E7D46", "#D48806", "#B02418", "#8C8C8C"

CORES_SITUACAO = {
    "Excelente": VERDE,
    "Bom": BLUE,
    "Atenção": VERMELHO,
    "Sem exercícios": CINZA,
}
CORES_STATUS = {
    "🟢 Revisado": VERDE,
    "🟡 Parcial": AMBAR,
    "🔴 Pendente/Atrasada": VERMELHO,
    "🔵 Agendada": BLUE,
}
OPCOES_MARCACAO = ["— não feita —", "🟢 Revisado", "🟡 Parcial"]

st.set_page_config(page_title=APP_TITULO, page_icon="📚", layout="wide",
                   initial_sidebar_state="expanded")

CSS = """
<style>
  .block-container {padding-top: 1.6rem; padding-bottom: 2rem; max-width: 1500px;}
  #MainMenu, footer {visibility: hidden;}

  .cabecalho {
      background: linear-gradient(90deg, #1F3864 0%, #2E75B6 100%);
      color: #fff; padding: 18px 26px; border-radius: 12px; margin-bottom: 18px;
  }
  .cabecalho h1 {font-size: 1.55rem; margin: 0; font-weight: 700; letter-spacing: .3px;}
  .cabecalho p  {margin: 4px 0 0; font-size: .88rem; opacity: .88;}

  .kpi {
      background: #fff; border: 1px solid #E3E8EF; border-left: 6px solid #2E75B6;
      border-radius: 10px; padding: 12px 16px; height: 100%;
      box-shadow: 0 1px 3px rgba(16,24,40,.06);
  }
  .kpi .rotulo {font-size: .70rem; letter-spacing: .09em; text-transform: uppercase;
                color: #64748B; font-weight: 700;}
  .kpi .valor  {font-size: 1.75rem; font-weight: 800; color: #1F3864; line-height: 1.25;}
  .kpi .nota   {font-size: .74rem; color: #64748B;}

  .aviso {
      background: #FFF8E6; border: 1px solid #F0D9A0; border-radius: 12px;
      padding: 26px 30px; color: #6B4E00;
  }
  .legenda {font-size: .80rem; color: #64748B; margin-top: -6px;}
  div[data-testid="stMetricValue"] {font-size: 1.4rem;}
  .stTabs [data-baseweb="tab"] {font-size: 1rem; font-weight: 600; padding: 8px 14px;}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# =============================================================================
# PERSISTÊNCIA (marcações de revisão + metas)
# =============================================================================

def carregar_estado() -> dict:
    if os.path.exists(ARQUIVO_ESTADO):
        try:
            with open(ARQUIVO_ESTADO, "r", encoding="utf-8") as f:
                dados = json.load(f)
            dados.setdefault("marcacoes", {})
            dados.setdefault("metas", {})
            return dados
        except (json.JSONDecodeError, OSError):
            pass
    return {"marcacoes": {}, "metas": {}}


def salvar_estado(estado: dict) -> None:
    try:
        with open(ARQUIVO_ESTADO, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False, indent=2)
    except OSError as erro:
        st.sidebar.warning(f"Não consegui gravar o arquivo de marcações: {erro}")


if "estado" not in st.session_state:
    st.session_state.estado = carregar_estado()


# =============================================================================
# LEITURA E PREPARO DOS DADOS
# =============================================================================

COLUNAS_ESPERADAS = {
    "inicio": "Início",
    "materia": "Matéria",
    "conteudo": "Conteúdo",
    "duracaominutos": "Duração (minutos)",
    "duracao": "Duração",
    "tipo": "Tipo",
    "exerciciosfeitos": "Exercicios feitos",
    "acertos": "Acertos",
    "anotacoes": "Anotações",
}


def _chave(texto: str) -> str:
    """Normaliza o nome da coluna: sem acento, sem espaço, minúsculo."""
    texto = unicodedata.normalize("NFKD", str(texto))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return "".join(c for c in texto.lower() if c.isalnum())


def _minutos_do_texto(valor) -> float:
    """Converte '1h 5m 3s' / '43m 8s' / '56m' em minutos. Usado só como reserva."""
    if pd.isna(valor):
        return 0.0
    texto = str(valor).lower().replace(",", ".")
    total, numero = 0.0, ""
    for caractere in texto:
        if caractere.isdigit() or caractere == ".":
            numero += caractere
        elif caractere in "hms" and numero:
            fator = {"h": 60.0, "m": 1.0, "s": 1 / 60}[caractere]
            total += float(numero) * fator
            numero = ""
        elif not caractere.isdigit():
            numero = numero if caractere == "." else ""
    return round(total, 4)


@st.cache_data(show_spinner=False)
def ler_base(conteudo_binario: bytes, nome_arquivo: str) -> tuple[pd.DataFrame, str]:
    """Lê o .xlsx, localiza a aba da base e devolve o DataFrame já tratado."""
    planilha = pd.ExcelFile(io.BytesIO(conteudo_binario))

    # 1) procura a aba correta: "4. Base_Dados", depois qualquer uma com "base"
    abas = planilha.sheet_names
    aba = next((a for a in abas if _chave(a) == _chave("4. Base_Dados")), None)
    if aba is None:
        aba = next((a for a in abas if "base" in _chave(a)), None)
    if aba is None:
        aba = abas[0]

    bruto = planilha.parse(aba)

    # 2) descarta colunas auxiliares e sem nome
    bruto = bruto.loc[:, [c for c in bruto.columns if not str(c).startswith("Unnamed")]]
    mapa = {}
    for coluna in bruto.columns:
        alvo = COLUNAS_ESPERADAS.get(_chave(coluna))
        if alvo:
            mapa[coluna] = alvo
    df = bruto[list(mapa.keys())].rename(columns=mapa)

    faltando = [c for c in ("Início", "Matéria") if c not in df.columns]
    if faltando:
        raise ValueError(
            f"A aba '{aba}' não tem as colunas obrigatórias: {', '.join(faltando)}."
        )

    # 3) tipagem
    df["Início"] = pd.to_datetime(df["Início"], errors="coerce", dayfirst=True)
    df = df.dropna(subset=["Início"])
    df["Matéria"] = df["Matéria"].astype(str).str.strip()
    df = df[df["Matéria"].str.len() > 0]

    if "Duração (minutos)" in df.columns:
        df["Minutos"] = pd.to_numeric(df["Duração (minutos)"], errors="coerce")
    else:
        df["Minutos"] = pd.NA
    if "Duração" in df.columns:  # completa o que vier vazio a partir do texto
        reserva = df["Duração"].map(_minutos_do_texto)
        df["Minutos"] = df["Minutos"].fillna(reserva)
    df["Minutos"] = pd.to_numeric(df["Minutos"], errors="coerce").fillna(0.0).clip(lower=0)
    df["Horas"] = df["Minutos"] / 60.0

    for coluna in ("Exercicios feitos", "Acertos"):
        if coluna not in df.columns:
            df[coluna] = 0
        df[coluna] = pd.to_numeric(df[coluna], errors="coerce").fillna(0).clip(lower=0)
    # acertos nunca podem passar das questões feitas
    df["Acertos"] = df[["Acertos", "Exercicios feitos"]].min(axis=1)
    df = df.rename(columns={"Exercicios feitos": "Questões"})

    for coluna in ("Conteúdo", "Tipo"):
        if coluna not in df.columns:
            df[coluna] = ""
        df[coluna] = df[coluna].fillna("").astype(str).str.strip()
    df.loc[df["Conteúdo"] == "", "Conteúdo"] = "(sem conteúdo informado)"
    df.loc[df["Tipo"].isin(["", "nan", "Não definido"]), "Tipo"] = "Não definido"

    df["Data"] = df["Início"].dt.normalize()
    df["Dia"] = df["Início"].dt.date
    df = df.sort_values("Início").reset_index(drop=True)
    return df, aba


def taxa(acertos: float, questoes: float) -> float:
    return float(acertos) / float(questoes) if questoes else 0.0


def situacao(linha) -> str:
    if linha["Questões"] <= 0:
        return "Sem exercícios"
    if linha["Taxa de acerto"] >= 0.85:
        return "Excelente"
    if linha["Taxa de acerto"] >= 0.70:
        return "Bom"
    return "Atenção"


def consolidar_por_materia(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["Matéria", "Horas", "Sessões", "Questões",
                                     "Acertos", "Taxa de acerto", "Situação"])
    tabela = (df.groupby("Matéria", as_index=False)
                .agg(Horas=("Horas", "sum"),
                     Sessões=("Início", "count"),
                     Questões=("Questões", "sum"),
                     Acertos=("Acertos", "sum"),
                     Último_estudo=("Data", "max")))
    tabela["Taxa de acerto"] = tabela.apply(
        lambda l: taxa(l["Acertos"], l["Questões"]), axis=1)
    tabela["Situação"] = tabela.apply(situacao, axis=1)
    tabela["% das horas"] = tabela["Horas"] / tabela["Horas"].sum() if tabela["Horas"].sum() else 0
    return tabela.sort_values("Horas", ascending=False).reset_index(drop=True)


def montar_revisoes(df: pd.DataFrame, marcacoes: dict) -> pd.DataFrame:
    """Gera uma linha por ciclo de revisão (1D, 7D, 28D) de cada sessão."""
    if df.empty:
        return pd.DataFrame(columns=["ID", "Data do estudo", "Matéria", "Conteúdo",
                                     "Tipo", "Ciclo", "Data da revisão",
                                     "Marcação", "Status", "Atraso (dias)"])
    hoje = date.today()
    linhas = []
    for _, sessao in df.iterrows():
        dia_estudo = sessao["Dia"]
        for rotulo, dias in CICLOS:
            data_revisao = dia_estudo + timedelta(days=dias)
            # o carimbo de data/hora torna o identificador único mesmo quando
            # há duas sessões da mesma matéria e conteúdo no mesmo dia
            identificador = (f"{sessao['Início']:%Y-%m-%dT%H:%M}|{sessao['Matéria']}"
                             f"|{sessao['Conteúdo']}|{rotulo}")
            marcacao = marcacoes.get(identificador, OPCOES_MARCACAO[0])
            if marcacao in ("🟢 Revisado", "🟡 Parcial"):
                status = marcacao
            elif data_revisao <= hoje:
                status = "🔴 Pendente/Atrasada"
            else:
                status = "🔵 Agendada"
            linhas.append({
                "ID": identificador,
                "Data do estudo": dia_estudo,
                "Matéria": sessao["Matéria"],
                "Conteúdo": sessao["Conteúdo"],
                "Tipo": sessao["Tipo"],
                "Ciclo": rotulo,
                "Data da revisão": data_revisao,
                "Marcação": marcacao,
                "Status": status,
                "Atraso (dias)": max(0, (hoje - data_revisao).days)
                                 if status == "🔴 Pendente/Atrasada" else 0,
            })
    return pd.DataFrame(linhas).sort_values(
        ["Data da revisão", "Matéria"]).reset_index(drop=True)


def cartao(rotulo: str, valor: str, nota: str = "", cor: str = BLUE) -> str:
    return (f'<div class="kpi" style="border-left-color:{cor}">'
            f'<div class="rotulo">{rotulo}</div>'
            f'<div class="valor">{valor}</div>'
            f'<div class="nota">{nota}</div></div>')


def layout_grafico(figura, altura=340, titulo=""):
    figura.update_layout(
        title=dict(text=titulo, font=dict(size=15, color=NAVY)),
        height=altura, margin=dict(l=10, r=10, t=48, b=10),
        font=dict(family="Segoe UI, Arial", size=12),
        plot_bgcolor="#FFFFFF", paper_bgcolor="#FFFFFF",
        hoverlabel=dict(font_size=12),
    )
    figura.update_xaxes(showgrid=True, gridcolor="#EEF2F7", zeroline=False)
    figura.update_yaxes(showgrid=True, gridcolor="#EEF2F7", zeroline=False)
    return figura


# =============================================================================
# BARRA LATERAL — IMPORTAÇÃO, FILTROS E METAS
# =============================================================================

st.sidebar.markdown(f"### 📚 {APP_TITULO}")
arquivo = st.sidebar.file_uploader("Importar Base de Dados (.xlsx)", type=["xlsx", "xlsm"],
                                   help="Selecione a pasta de trabalho do Excel que contém a aba "
                                        "“4. Base_Dados” exportada do seu aplicativo de estudos.")

if arquivo is None:
    st.markdown(f'<div class="cabecalho"><h1>📚 {APP_TITULO}</h1>'
                f'<p>Dashboard de desempenho • Repetição espaçada 1/7/28 dias • '
                f'Diagnóstico de metas por disciplina</p></div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="aviso">'
        '<h3>👋 Vamos começar: importe a sua planilha</h3>'
        '<p>Use o botão <b>“Importar Base de Dados (.xlsx)”</b> na barra lateral, à esquerda, '
        'e escolha o arquivo do Excel com a aba <b>“4. Base_Dados”</b>.</p>'
        '<p style="margin-bottom:4px"><b>Colunas que o app aproveita:</b></p>'
        '<ul style="margin-top:0">'
        '<li><b>Início</b> — data e hora da sessão (obrigatória)</li>'
        '<li><b>Matéria</b> — disciplina estudada (obrigatória)</li>'
        '<li><b>Conteúdo</b>, <b>Tipo</b> — assunto e formato do estudo</li>'
        '<li><b>Duração (minutos)</b> — se faltar, o app calcula a partir da coluna <b>Duração</b></li>'
        '<li><b>Exercicios feitos</b> e <b>Acertos</b> — base do desempenho</li>'
        '</ul>'
        '<p style="margin-bottom:0">Nada é enviado para a internet: o arquivo é lido na memória '
        'do seu próprio computador.</p>'
        '</div>', unsafe_allow_html=True)
    st.stop()

try:
    dados, aba_lida = ler_base(arquivo.getvalue(), arquivo.name)
except Exception as erro:  # noqa: BLE001 — queremos mostrar qualquer falha ao usuário
    st.error(f"❌ Não consegui ler a planilha: {erro}")
    st.stop()

if dados.empty:
    st.error("A base foi lida, mas não há nenhuma sessão de estudo válida nela.")
    st.stop()

data_min = dados["Dia"].min()
data_max = dados["Dia"].max()
hoje = date.today()

st.sidebar.success(f"✅ {len(dados):,} sessões lidas da aba “{aba_lida}”".replace(",", "."))
st.sidebar.markdown("---")
st.sidebar.markdown("#### 📅 Intervalo de análise")

atalho = st.sidebar.radio(
    "Atalhos de período", ["Últimos 30 dias", "Últimos 90 dias", "Ano atual", "Tudo", "Personalizado"],
    index=3, horizontal=False, label_visibility="collapsed")

if atalho == "Últimos 30 dias":
    padrao = (max(data_min, hoje - timedelta(days=29)), max(data_max, hoje))
elif atalho == "Últimos 90 dias":
    padrao = (max(data_min, hoje - timedelta(days=89)), max(data_max, hoje))
elif atalho == "Ano atual":
    padrao = (max(data_min, date(hoje.year, 1, 1)), max(data_max, hoje))
else:
    padrao = (data_min, max(data_max, hoje))

coluna_a, coluna_b = st.sidebar.columns(2)
inicio = coluna_a.date_input("Data Inicial", value=padrao[0],
                             min_value=data_min, max_value=date(2100, 12, 31),
                             format="DD/MM/YYYY",
                             disabled=(atalho != "Personalizado"))
fim = coluna_b.date_input("Data Final", value=padrao[1],
                          min_value=data_min, max_value=date(2100, 12, 31),
                          format="DD/MM/YYYY",
                          disabled=(atalho != "Personalizado"))
if inicio > fim:
    st.sidebar.error("A data inicial não pode ser maior que a final.")
    st.stop()

materias_disponiveis = sorted(dados["Matéria"].unique())
materias = st.sidebar.multiselect("Disciplinas", materias_disponiveis,
                                  default=materias_disponiveis,
                                  placeholder="Todas as disciplinas")
tipos_disponiveis = sorted(dados["Tipo"].unique())
tipos = st.sidebar.multiselect("Tipos de estudo", tipos_disponiveis,
                               default=tipos_disponiveis,
                               placeholder="Todos os tipos")

st.sidebar.markdown("---")
st.sidebar.markdown("#### 🎯 Prova e ritmo")
data_prova = st.sidebar.date_input("Data da Prova/Meta",
                                   value=date(hoje.year, 12, 31),
                                   format="DD/MM/YYYY")
horas_por_dia = st.sidebar.number_input("Horas de estudo por dia (plano)",
                                        min_value=0.0, max_value=24.0, value=3.0, step=0.5)
dias_para_prova = (data_prova - hoje).days
if dias_para_prova > 0:
    st.sidebar.success(f"⏳ Faltam **{dias_para_prova} dias** para a prova\n\n"
                       f"No seu ritmo planejado: **{dias_para_prova * horas_por_dia:.0f} h** disponíveis")
elif dias_para_prova == 0:
    st.sidebar.warning("🎯 **A prova é hoje.** Boa prova!")
else:
    st.sidebar.info(f"A data da prova passou há {abs(dias_para_prova)} dias.")

st.sidebar.markdown("---")
with st.sidebar.expander("⚙️ Manutenção dos dados salvos"):
    st.caption("Marcações de revisão e metas ficam gravadas no arquivo "
               "`.painel_estudos_estado.json`, na mesma pasta do app.")
    if st.button("Apagar marcações de revisão"):
        st.session_state.estado["marcacoes"] = {}
        salvar_estado(st.session_state.estado)
        st.rerun()
    if st.button("Restaurar metas padrão"):
        st.session_state.estado["metas"] = {}
        salvar_estado(st.session_state.estado)
        st.rerun()

# ---------------------------------------------------------------- filtragem
filtro = (dados["Dia"] >= inicio) & (dados["Dia"] <= fim)
if materias:
    filtro &= dados["Matéria"].isin(materias)
if tipos:
    filtro &= dados["Tipo"].isin(tipos)
periodo = dados[filtro].copy()

# =============================================================================
# CABEÇALHO
# =============================================================================

st.markdown(
    f'<div class="cabecalho"><h1>📚 {APP_TITULO}</h1>'
    f'<p>Período analisado: <b>{inicio.strftime("%d/%m/%Y")}</b> a '
    f'<b>{fim.strftime("%d/%m/%Y")}</b> &nbsp;•&nbsp; '
    f'{len(periodo)} de {len(dados)} sessões &nbsp;•&nbsp; '
    f'{"⏳ faltam <b>" + str(dias_para_prova) + " dias</b> para a prova" if dias_para_prova > 0 else "prova já realizada"}'
    f'</p></div>', unsafe_allow_html=True)

if periodo.empty:
    st.warning("Nenhuma sessão de estudo dentro dos filtros escolhidos. "
               "Amplie o intervalo de datas ou marque mais disciplinas na barra lateral.")
    st.stop()

aba1, aba2, aba3, aba4 = st.tabs([
    "📈 Dashboard de Desempenho",
    "🔁 Cronograma de Revisões",
    "🎯 Metas & Diagnóstico",
    "🗂️ Base de Dados",
])

# =============================================================================
# ABA 1 — DASHBOARD
# =============================================================================
with aba1:
    horas_total = periodo["Horas"].sum()
    sessoes = len(periodo)
    questoes = periodo["Questões"].sum()
    acertos = periodo["Acertos"].sum()
    taxa_global = taxa(acertos, questoes)
    dias_distintos = periodo["Dia"].nunique()
    dias_corridos = (fim - inicio).days + 1
    constancia = dias_distintos / dias_corridos if dias_corridos else 0

    # sequência atual de dias consecutivos estudando
    dias_ordenados = sorted(periodo["Dia"].unique(), reverse=True)
    sequencia, referencia = 0, hoje
    if dias_ordenados and dias_ordenados[0] in (hoje, hoje - timedelta(days=1)):
        referencia = dias_ordenados[0]
        for dia in dias_ordenados:
            if dia == referencia:
                sequencia += 1
                referencia -= timedelta(days=1)
            elif dia < referencia:
                break

    colunas = st.columns(5)
    valores = [
        ("Total de horas estudadas", f"{horas_total:,.1f} h".replace(",", "."),
         f"{horas_total / dias_corridos:.1f} h por dia corrido", BLUE),
        ("Sessões registradas", f"{sessoes:,}".replace(",", "."),
         f"{horas_total * 60 / sessoes:.0f} min por sessão", BLUE),
        ("Questões resolvidas", f"{questoes:,.0f}".replace(",", "."),
         f"{acertos:,.0f} acertos".replace(",", "."), CYAN),
        ("Taxa global de acerto", f"{taxa_global:.1%}",
         "meta saudável: 85%", VERDE if taxa_global >= 0.85 else (AMBAR if taxa_global >= 0.7 else VERMELHO)),
        ("Dias distintos com estudo", f"{dias_distintos:,}".replace(",", "."),
         f"{constancia:.0%} dos {dias_corridos} dias do período", NAVY),
    ]
    for coluna, (rotulo, valor, nota, cor) in zip(colunas, valores):
        coluna.markdown(cartao(rotulo, valor, nota, cor), unsafe_allow_html=True)

    colunas = st.columns(5)
    media_dia_estudado = horas_total / dias_distintos if dias_distintos else 0
    conteudos = periodo["Conteúdo"].nunique()
    melhor = consolidar_por_materia(periodo)
    mais_estudada = melhor.iloc[0]["Matéria"] if not melhor.empty else "—"
    com_exercicios = melhor[melhor["Questões"] > 0]
    ponto_fraco = (com_exercicios.sort_values("Taxa de acerto").iloc[0]["Matéria"]
                   if not com_exercicios.empty else "—")
    valores = [
        ("Média por dia estudado", f"{media_dia_estudado:,.1f} h".replace(",", "."),
         "considera só os dias em que você estudou", NAVY),
        ("Sequência atual", f"{sequencia} dia(s)",
         "dias consecutivos até hoje", VERDE if sequencia >= 3 else AMBAR),
        ("Conteúdos distintos", f"{conteudos:,}".replace(",", "."),
         f"{len(materias_disponiveis)} disciplinas na base", CYAN),
        ("Disciplina com mais horas", mais_estudada, "concentração de esforço", BLUE),
        ("Menor taxa de acerto", ponto_fraco, "candidata natural a prioridade", VERMELHO),
    ]
    for coluna, (rotulo, valor, nota, cor) in zip(colunas, valores):
        coluna.markdown(cartao(rotulo, valor, nota, cor), unsafe_allow_html=True)

    st.markdown("")
    esquerda, direita = st.columns([3, 2])

    # ---- evolução no tempo
    with esquerda:
        granularidade = st.radio("Agrupar a evolução por:", ["Dia", "Semana", "Mês"],
                                 index=1, horizontal=True, key="granularidade")
        regra = {"Dia": "D", "Semana": "W-SUN", "Mês": "MS"}[granularidade]
        serie = (periodo.set_index("Data")
                 .resample(regra)
                 .agg(Horas=("Horas", "sum"), Questões=("Questões", "sum"),
                      Acertos=("Acertos", "sum"))
                 .reset_index())
        serie["Taxa"] = serie.apply(lambda l: taxa(l["Acertos"], l["Questões"]), axis=1)

        figura = go.Figure()
        figura.add_trace(go.Bar(x=serie["Data"], y=serie["Horas"], name="Horas",
                                marker_color=BLUE, hovertemplate="%{x|%d/%m/%Y}<br>%{y:.1f} h<extra></extra>"))
        figura.add_trace(go.Scatter(x=serie["Data"], y=serie["Questões"], name="Questões",
                                    yaxis="y2", mode="lines+markers",
                                    line=dict(color=AMBAR, width=2.5),
                                    hovertemplate="%{x|%d/%m/%Y}<br>%{y:.0f} questões<extra></extra>"))
        figura.update_layout(
            yaxis=dict(title="Horas"),
            yaxis2=dict(title="Questões", overlaying="y", side="right", showgrid=False),
            legend=dict(orientation="h", y=1.12, x=0),
            bargap=0.25)
        st.plotly_chart(layout_grafico(figura, 360, f"Evolução do esforço por {granularidade.lower()}"),
                        width="stretch")

    # ---- distribuição das horas
    with direita:
        tabela = consolidar_por_materia(periodo)
        topo = tabela.head(8)
        figura = px.pie(topo, values="Horas", names="Matéria", hole=0.55,
                        color_discrete_sequence=px.colors.sequential.Blues_r)
        figura.update_traces(textposition="inside", texttemplate="%{percent:.0%}",
                             hovertemplate="%{label}<br>%{value:.1f} h (%{percent})<extra></extra>")
        figura.update_layout(legend=dict(orientation="v", font=dict(size=11)))
        st.plotly_chart(layout_grafico(figura, 360, "Distribuição das horas (8 maiores)"),
                        width="stretch")

    esquerda, direita = st.columns(2)

    # ---- ranking de horas
    with esquerda:
        topo = tabela.head(12).sort_values("Horas")
        figura = px.bar(topo, x="Horas", y="Matéria", orientation="h",
                        text=topo["Horas"].map(lambda v: f"{v:.1f} h"),
                        color="Situação", color_discrete_map=CORES_SITUACAO)
        figura.update_traces(textposition="outside", cliponaxis=False)
        figura.update_layout(legend=dict(orientation="h", y=1.1, x=0, title=""),
                             xaxis_title="Horas no período", yaxis_title="")
        st.plotly_chart(layout_grafico(figura, 420, "Horas por disciplina e situação"),
                        width="stretch")

    # ---- esforço x rendimento
    with direita:
        bolhas = tabela[tabela["Questões"] > 0].copy()
        if bolhas.empty:
            st.info("Sem questões registradas no período: o gráfico de esforço x rendimento "
                    "aparece assim que houver exercícios na base.")
        else:
            figura = px.scatter(bolhas, x="Horas", y="Taxa de acerto", size="Questões",
                                color="Situação", color_discrete_map=CORES_SITUACAO,
                                text="Matéria", size_max=45,
                                hover_data={"Sessões": True, "Questões": True})
            figura.add_hline(y=0.85, line_dash="dot", line_color=VERDE,
                             annotation_text="85% — domínio", annotation_position="top left")
            figura.add_hline(y=0.70, line_dash="dot", line_color=VERMELHO,
                             annotation_text="70% — alerta", annotation_position="bottom left")
            figura.update_traces(textposition="top center", textfont_size=10)
            figura.update_layout(yaxis_tickformat=".0%", legend=dict(orientation="h", y=1.1, x=0, title=""),
                                 xaxis_title="Horas investidas", yaxis_title="Taxa de acerto")
            st.plotly_chart(layout_grafico(figura, 420, "Esforço x rendimento (tamanho = nº de questões)"),
                            width="stretch")

    # ---- mapa de calor de constância
    calendario = (periodo.groupby("Dia", as_index=False)["Horas"].sum())
    calendario["Data"] = pd.to_datetime(calendario["Dia"])
    calendario["Semana"] = calendario["Data"].dt.strftime("%Y-%U")
    calendario["DiaSemana"] = calendario["Data"].dt.dayofweek.map(
        {6: "Dom", 0: "Seg", 1: "Ter", 2: "Qua", 3: "Qui", 4: "Sex", 5: "Sáb"})
    calendario["DiaSemana"] = calendario["Data"].dt.strftime("%w").map(
        {"0": "Dom", "1": "Seg", "2": "Ter", "3": "Qua", "4": "Qui", "5": "Sex", "6": "Sáb"})
    ordem = ["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"]
    matriz = calendario.pivot_table(index="DiaSemana", columns="Semana",
                                    values="Horas", aggfunc="sum").reindex(ordem)
    figura = px.imshow(matriz, color_continuous_scale=["#EEF3FA", BLUE, NAVY],
                       aspect="auto", labels=dict(color="Horas"))
    figura.update_traces(hovertemplate="Semana %{x}<br>%{y}<br>%{z:.1f} h<extra></extra>")
    figura.update_layout(xaxis_title="Semana do ano", yaxis_title="",
                         coloraxis_colorbar=dict(title="Horas"))
    st.plotly_chart(layout_grafico(figura, 300, "Mapa de constância — onde estão os buracos na rotina"),
                    width="stretch")

    # ---- tabela consolidada
    st.markdown("#### 📋 Tabela consolidada por disciplina")
    visao = tabela[["Matéria", "Horas", "% das horas", "Sessões", "Questões",
                    "Acertos", "Taxa de acerto", "Situação", "Último_estudo"]].copy()
    visao = visao.rename(columns={"Último_estudo": "Último estudo"})
    visao["Último estudo"] = pd.to_datetime(visao["Último estudo"]).dt.strftime("%d/%m/%Y")
    st.dataframe(
        visao, width="stretch", hide_index=True,
        column_config={
            "Horas": st.column_config.ProgressColumn(
                "Horas", format="%.1f h", min_value=0.0,
                max_value=float(max(visao["Horas"].max(), 1))),
            "% das horas": st.column_config.NumberColumn("% das horas", format="%.1f%%"),
            "Taxa de acerto": st.column_config.NumberColumn("Taxa de acerto", format="%.1f%%"),
            "Questões": st.column_config.NumberColumn(format="%d"),
            "Acertos": st.column_config.NumberColumn(format="%d"),
            "Sessões": st.column_config.NumberColumn(format="%d"),
        })
    st.markdown('<p class="legenda">Situação: <b>Excelente</b> ≥ 85% de acerto • '
                '<b>Bom</b> ≥ 70% • <b>Atenção</b> abaixo de 70% • '
                '<b>Sem exercícios</b> quando a disciplina ainda não tem questões registradas.</p>',
                unsafe_allow_html=True)

# =============================================================================
# ABA 2 — CRONOGRAMA DE REVISÕES
# =============================================================================
with aba2:
    revisoes = montar_revisoes(periodo, st.session_state.estado["marcacoes"])

    atrasadas = int((revisoes["Status"] == "🔴 Pendente/Atrasada").sum())
    para_hoje = int(((revisoes["Data da revisão"] == hoje) &
                     (revisoes["Marcação"] == OPCOES_MARCACAO[0])).sum())
    proximos7 = int(((revisoes["Data da revisão"] > hoje) &
                     (revisoes["Data da revisão"] <= hoje + timedelta(days=7))).sum())
    feitas = int(revisoes["Status"].isin(["🟢 Revisado", "🟡 Parcial"]).sum())
    total_vencidas = int((revisoes["Data da revisão"] <= hoje).sum())
    aderencia = feitas / total_vencidas if total_vencidas else 0

    colunas = st.columns(5)
    for coluna, (rotulo, valor, nota, cor) in zip(colunas, [
        ("Revisões atrasadas", f"{atrasadas}", "data já passou e não foi marcada", VERMELHO),
        ("Para fazer hoje", f"{para_hoje}", hoje.strftime("%d/%m/%Y"), AMBAR),
        ("Próximos 7 dias", f"{proximos7}", "carga que está chegando", BLUE),
        ("Revisões concluídas", f"{feitas}", "marcadas como revisadas ou parciais", VERDE),
        ("Aderência ao método", f"{aderencia:.0%}",
         f"{feitas} de {total_vencidas} revisões já vencidas", NAVY if aderencia >= .6 else VERMELHO),
    ]):
        coluna.markdown(cartao(rotulo, valor, nota, cor), unsafe_allow_html=True)

    st.markdown("")
    st.markdown("##### 📆 Carga de revisões dos próximos 21 dias")
    janela = revisoes[(revisoes["Data da revisão"] >= hoje) &
                      (revisoes["Data da revisão"] <= hoje + timedelta(days=21))]
    if janela.empty:
        st.info("Nenhuma revisão agendada para os próximos 21 dias dentro dos filtros atuais.")
    else:
        carga = janela.groupby(["Data da revisão", "Ciclo"], as_index=False).size()
        figura = px.bar(carga, x="Data da revisão", y="size", color="Ciclo",
                        color_discrete_map={"Rev. 1D": CYAN, "Rev. 7D": BLUE, "Rev. 28D": NAVY},
                        labels={"size": "Revisões"})
        figura.update_layout(barmode="stack", legend=dict(orientation="h", y=1.15, x=0, title=""),
                             xaxis_title="", yaxis_title="Nº de revisões")
        st.plotly_chart(layout_grafico(figura, 280), width="stretch")

    st.markdown("##### ✍️ Marque aqui o que você já revisou")
    st.caption("Escolha **🟢 Revisado** ou **🟡 Parcial** na coluna *Marcação*. O que ficar como "
               "*— não feita —* vira 🔴 Pendente quando a data chega e 🔵 Agendada enquanto não chega. "
               "As marcações ficam salvas no seu computador e valem também nas próximas aberturas do app.")

    filtro_a, filtro_b, filtro_c = st.columns([2, 2, 3])
    visao_escolhida = filtro_a.selectbox(
        "O que exibir", ["Pendentes e de hoje", "Só atrasadas", "Próximos 7 dias",
                         "Já concluídas", "Tudo"], index=0)
    ciclos_escolhidos = filtro_b.multiselect("Ciclos", [c for c, _ in CICLOS],
                                             default=[c for c, _ in CICLOS])
    busca = filtro_c.text_input("Buscar por conteúdo ou matéria", placeholder="ex.: Gestão Democrática")

    filtrado = revisoes[revisoes["Ciclo"].isin(ciclos_escolhidos)].copy()
    if visao_escolhida == "Pendentes e de hoje":
        filtrado = filtrado[filtrado["Status"] == "🔴 Pendente/Atrasada"]
    elif visao_escolhida == "Só atrasadas":
        filtrado = filtrado[(filtrado["Status"] == "🔴 Pendente/Atrasada") &
                            (filtrado["Data da revisão"] < hoje)]
    elif visao_escolhida == "Próximos 7 dias":
        filtrado = filtrado[(filtrado["Data da revisão"] >= hoje) &
                            (filtrado["Data da revisão"] <= hoje + timedelta(days=7))]
    elif visao_escolhida == "Já concluídas":
        filtrado = filtrado[filtrado["Status"].isin(["🟢 Revisado", "🟡 Parcial"])]
    if busca.strip():
        alvo = busca.strip().lower()
        filtrado = filtrado[filtrado["Matéria"].str.lower().str.contains(alvo) |
                            filtrado["Conteúdo"].str.lower().str.contains(alvo)]

    if filtrado.empty:
        st.success("🎉 Nada aqui com os filtros atuais — aproveite para adiantar matéria nova.")
    else:
        editor = filtrado[["ID", "Data do estudo", "Matéria", "Conteúdo", "Tipo",
                           "Ciclo", "Data da revisão", "Atraso (dias)", "Status", "Marcação"]]
        editado = st.data_editor(
            editor, width="stretch", hide_index=True, height=460,
            key=f"editor_revisoes_{visao_escolhida}",
            disabled=["ID", "Data do estudo", "Matéria", "Conteúdo", "Tipo",
                      "Ciclo", "Data da revisão", "Atraso (dias)", "Status"],
            column_config={
                "ID": None,
                "Data do estudo": st.column_config.DateColumn("Estudo", format="DD/MM/YYYY", width="small"),
                "Data da revisão": st.column_config.DateColumn("Revisar em", format="DD/MM/YYYY", width="small"),
                "Atraso (dias)": st.column_config.NumberColumn("Atraso", format="%d d", width="small"),
                "Conteúdo": st.column_config.TextColumn("Conteúdo", width="large"),
                "Marcação": st.column_config.SelectboxColumn(
                    "Marcação", options=OPCOES_MARCACAO, required=True, width="medium"),
            })

        mudou = False
        for _, linha in editado.iterrows():
            atual = st.session_state.estado["marcacoes"].get(linha["ID"], OPCOES_MARCACAO[0])
            if linha["Marcação"] != atual:
                if linha["Marcação"] == OPCOES_MARCACAO[0]:
                    st.session_state.estado["marcacoes"].pop(linha["ID"], None)
                else:
                    st.session_state.estado["marcacoes"][linha["ID"]] = linha["Marcação"]
                mudou = True
        if mudou:
            salvar_estado(st.session_state.estado)
            st.rerun()

    with st.expander("ℹ️ Por que revisar em 1, 7 e 28 dias?"):
        st.markdown(
            "A curva do esquecimento de Ebbinghaus mostra que a maior perda de retenção acontece "
            "nas primeiras 24 horas. Revisar **no dia seguinte** segura essa queda; revisar de novo "
            "**uma semana depois** transfere o conteúdo para a memória de médio prazo; e a revisão de "
            "**28 dias** consolida o que já está quase automático.\n\n"
            "Cada revisão pode ser curta: reler o resumo, refazer as questões que você errou ou "
            "explicar o tema em voz alta por cinco minutos já cumpre o papel. O que importa é o "
            "espaçamento, não a duração."
        )

# =============================================================================
# ABA 3 — METAS E DIAGNÓSTICO
# =============================================================================
with aba3:
    tabela = consolidar_por_materia(periodo)
    metas_salvas = st.session_state.estado["metas"]

    st.markdown("##### 1️⃣ Defina as metas do período")
    coluna_a, coluna_b, coluna_c = st.columns([1, 1, 2])
    meta_horas_padrao = coluna_a.number_input("Meta de horas por matéria",
                                              min_value=0.0, value=20.0, step=1.0)
    meta_questoes_padrao = coluna_b.number_input("Meta de questões por matéria",
                                                 min_value=0, value=100, step=10)
    coluna_c.caption("Estes valores preenchem a tabela abaixo de uma vez. "
                     "Depois você pode ajustar disciplina por disciplina — o app guarda as suas metas "
                     "individuais e as reaproveita nas próximas aberturas.")

    metas = tabela[["Matéria", "Horas", "Questões", "Acertos", "Taxa de acerto"]].copy()
    metas["Meta de horas"] = metas["Matéria"].map(
        lambda m: float(metas_salvas.get(m, {}).get("horas", meta_horas_padrao)))
    metas["Meta de questões"] = metas["Matéria"].map(
        lambda m: int(metas_salvas.get(m, {}).get("questoes", meta_questoes_padrao)))

    editado = st.data_editor(
        metas[["Matéria", "Meta de horas", "Meta de questões"]],
        width="stretch", hide_index=True, height=min(60 + 35 * len(metas), 320),
        key="editor_metas",
        disabled=["Matéria"],
        column_config={
            "Meta de horas": st.column_config.NumberColumn(format="%.1f h", min_value=0.0, step=1.0),
            "Meta de questões": st.column_config.NumberColumn(format="%d", min_value=0, step=10),
        })

    mudou = False
    for _, linha in editado.iterrows():
        anterior = metas_salvas.get(linha["Matéria"], {})
        novo = {"horas": float(linha["Meta de horas"]), "questoes": int(linha["Meta de questões"])}
        if anterior != novo:
            metas_salvas[linha["Matéria"]] = novo
            mudou = True
    if mudou:
        salvar_estado(st.session_state.estado)

    metas["Meta de horas"] = editado["Meta de horas"].values
    metas["Meta de questões"] = editado["Meta de questões"].values
    metas["% horas"] = metas.apply(
        lambda l: l["Horas"] / l["Meta de horas"] if l["Meta de horas"] else 0, axis=1)
    metas["% questões"] = metas.apply(
        lambda l: l["Questões"] / l["Meta de questões"] if l["Meta de questões"] else 0, axis=1)

    def diagnostico(linha) -> str:
        if linha["% horas"] < 0.5 and linha["% questões"] < 0.5:
            return "🔴 Prioridade Alta (Aumentar Estudo)"
        if linha["Taxa de acerto"] < 0.70 or linha["% questões"] < 0.70:
            return "🟡 Reforçar Exercícios"
        return "🟢 Meta no Caminho"

    metas["Diagnóstico de Prioridade"] = metas.apply(diagnostico, axis=1)
    metas["Horas faltando"] = (metas["Meta de horas"] - metas["Horas"]).clip(lower=0)
    metas["Questões faltando"] = (metas["Meta de questões"] - metas["Questões"]).clip(lower=0)

    st.markdown("##### 2️⃣ Onde você está em relação ao que planejou")
    colunas = st.columns(4)
    horas_faltando = metas["Horas faltando"].sum()
    capacidade = max(dias_para_prova, 0) * horas_por_dia
    prioridades = int((metas["Diagnóstico de Prioridade"].str.startswith("🔴")).sum())
    for coluna, (rotulo, valor, nota, cor) in zip(colunas, [
        ("Atingimento geral de horas",
         f"{(metas['Horas'].sum() / metas['Meta de horas'].sum()) if metas['Meta de horas'].sum() else 0:.0%}",
         f"{metas['Horas'].sum():.1f} h de {metas['Meta de horas'].sum():.0f} h", BLUE),
        ("Atingimento geral de questões",
         f"{(metas['Questões'].sum() / metas['Meta de questões'].sum()) if metas['Meta de questões'].sum() else 0:.0%}",
         f"{metas['Questões'].sum():.0f} de {metas['Meta de questões'].sum():.0f}", CYAN),
        ("Horas ainda faltando", f"{horas_faltando:,.0f} h".replace(",", "."),
         f"cabem {capacidade:.0f} h até a prova no ritmo atual",
         VERDE if horas_faltando <= capacidade else VERMELHO),
        ("Disciplinas em prioridade alta", f"{prioridades}",
         "menos de 50% das duas metas", VERMELHO if prioridades else VERDE),
    ]):
        coluna.markdown(cartao(rotulo, valor, nota, cor), unsafe_allow_html=True)

    if horas_faltando > capacidade and dias_para_prova > 0:
        necessario = horas_faltando / dias_para_prova
        st.warning(f"⚠️ Para fechar todas as metas em {dias_para_prova} dias você precisaria de "
                   f"**{necessario:.1f} h por dia**, e o seu plano prevê {horas_por_dia:.1f} h. "
                   f"Vale reduzir as metas das disciplinas menos cobradas ou aumentar o ritmo.")

    st.markdown("")
    esquerda, direita = st.columns([3, 2])
    with esquerda:
        grafico = metas.sort_values("% horas")
        figura = go.Figure()
        figura.add_trace(go.Bar(y=grafico["Matéria"], x=grafico["% horas"] * 100, orientation="h",
                                name="% da meta de horas", marker_color=BLUE,
                                hovertemplate="%{y}<br>%{x:.0f}% da meta de horas<extra></extra>"))
        figura.add_trace(go.Bar(y=grafico["Matéria"], x=grafico["% questões"] * 100, orientation="h",
                                name="% da meta de questões", marker_color=CYAN,
                                hovertemplate="%{y}<br>%{x:.0f}% da meta de questões<extra></extra>"))
        figura.add_vline(x=100, line_dash="dash", line_color=VERDE,
                         annotation_text="meta", annotation_position="top")
        figura.update_layout(barmode="group", xaxis_title="% atingido", yaxis_title="",
                             legend=dict(orientation="h", y=1.1, x=0, title=""))
        st.plotly_chart(layout_grafico(figura, 60 + 34 * max(len(grafico), 4),
                                       "Atingimento das metas por disciplina"),
                        width="stretch")

    with direita:
        contagem = (metas["Diagnóstico de Prioridade"].value_counts().rename_axis("Diagnóstico")
                    .reset_index(name="Disciplinas"))
        figura = px.bar(contagem, x="Disciplinas", y="Diagnóstico", orientation="h",
                        color="Diagnóstico", text="Disciplinas",
                        color_discrete_map={
                            "🔴 Prioridade Alta (Aumentar Estudo)": VERMELHO,
                            "🟡 Reforçar Exercícios": AMBAR,
                            "🟢 Meta no Caminho": VERDE})
        figura.update_traces(textposition="outside", cliponaxis=False)
        figura.update_layout(showlegend=False, xaxis_title="", yaxis_title="")
        st.plotly_chart(layout_grafico(figura, 260, "Resumo do diagnóstico"),
                        width="stretch")
        st.markdown(
            '<p class="legenda">'
            '<b>🔴 Prioridade Alta</b>: menos de 50% da meta de horas <i>e</i> de questões.<br>'
            '<b>🟡 Reforçar Exercícios</b>: taxa de acerto ou volume de questões abaixo de 70%.<br>'
            '<b>🟢 Meta no Caminho</b>: os demais casos.</p>', unsafe_allow_html=True)

    st.markdown("##### 3️⃣ Tabela comparativa")
    saida = metas[["Matéria", "Meta de horas", "Horas", "% horas", "Horas faltando",
                   "Meta de questões", "Questões", "% questões", "Questões faltando",
                   "Taxa de acerto", "Diagnóstico de Prioridade"]].copy()
    st.dataframe(
        saida.sort_values("% horas"), width="stretch", hide_index=True,
        column_config={
            "Meta de horas": st.column_config.NumberColumn(format="%.1f h"),
            "Horas": st.column_config.NumberColumn(format="%.1f h"),
            "Horas faltando": st.column_config.NumberColumn(format="%.1f h"),
            "% horas": st.column_config.ProgressColumn("% horas", format="%.0f%%",
                                                       min_value=0.0, max_value=1.0),
            "% questões": st.column_config.ProgressColumn("% questões", format="%.0f%%",
                                                          min_value=0.0, max_value=1.0),
            "Taxa de acerto": st.column_config.NumberColumn(format="%.1f%%"),
            "Meta de questões": st.column_config.NumberColumn(format="%d"),
            "Questões": st.column_config.NumberColumn(format="%d"),
            "Questões faltando": st.column_config.NumberColumn(format="%d"),
        })

# =============================================================================
# ABA 4 — BASE DE DADOS
# =============================================================================
with aba4:
    st.markdown("##### 🗂️ Dados brutos carregados")
    st.caption(f"Arquivo: **{arquivo.name}** • aba **{aba_lida}** • "
               f"{len(dados)} sessões no total, {len(periodo)} dentro dos filtros atuais.")

    mostrar_tudo = st.toggle("Mostrar a base inteira (ignorar os filtros da barra lateral)",
                             value=False)
    exibir = dados if mostrar_tudo else periodo
    colunas_visiveis = ["Início", "Matéria", "Conteúdo", "Tipo", "Minutos", "Horas",
                        "Questões", "Acertos"]
    tabela_bruta = exibir[colunas_visiveis].sort_values("Início", ascending=False)
    st.dataframe(
        tabela_bruta, width="stretch", hide_index=True, height=520,
        column_config={
            "Início": st.column_config.DatetimeColumn("Início", format="DD/MM/YYYY HH:mm"),
            "Minutos": st.column_config.NumberColumn(format="%.1f"),
            "Horas": st.column_config.NumberColumn(format="%.2f h"),
            "Questões": st.column_config.NumberColumn(format="%d"),
            "Acertos": st.column_config.NumberColumn(format="%d"),
        })

    st.markdown("##### ⬇️ Exportar a análise")
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as escritor:
        tabela_bruta.to_excel(escritor, sheet_name="Base filtrada", index=False)
        consolidar_por_materia(periodo).to_excel(escritor, sheet_name="Por disciplina", index=False)
        montar_revisoes(periodo, st.session_state.estado["marcacoes"]).drop(columns=["ID"]).to_excel(
            escritor, sheet_name="Revisões", index=False)
    st.download_button(
        "📥 Baixar Excel com a análise do período",
        data=buffer.getvalue(),
        file_name=f"analise_estudos_{inicio:%Y%m%d}_{fim:%Y%m%d}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    with st.expander("🔍 Conferência da importação (o que o app entendeu do arquivo)"):
        diagnostico_colunas = pd.DataFrame({
            "Informação": ["Sessões válidas", "Período coberto", "Disciplinas",
                           "Sessões sem conteúdo informado", "Sessões sem tipo informado",
                           "Sessões com exercícios", "Horas totais na base"],
            "Valor": [
                f"{len(dados)}",
                f"{data_min:%d/%m/%Y} a {data_max:%d/%m/%Y}",
                f"{dados['Matéria'].nunique()}",
                f"{(dados['Conteúdo'] == '(sem conteúdo informado)').sum()}",
                f"{(dados['Tipo'] == 'Não definido').sum()}",
                f"{(dados['Questões'] > 0).sum()}",
                f"{dados['Horas'].sum():.1f} h",
            ]})
        st.dataframe(diagnostico_colunas, width="stretch", hide_index=True)
