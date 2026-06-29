import logging
import streamlit as st
import pandas as pd
import hashlib
from datetime import datetime, timedelta
from pymongo import MongoClient
import certifi
import streamlit.components.v1 as components

# =====================================================
# CONFIGURAÇÃO DA PÁGINA (Deve ser o primeiro comando)
# =====================================================
st.set_page_config(
    page_title="Bolão Mata-Mata - Copa 2026",
    page_icon="🏆",
    layout="wide"
)

FASES_ORDEM = ["Oitavas de Final", "Quartas de Final", "Semifinal", "Disputa de 3º Lugar", "Final"]

# =====================================================
# INICIALIZAÇÃO DO ESTADO DA SESSÃO
# =====================================================
if "logado" not in st.session_state:
    st.session_state["logado"] = False
if "nome" not in st.session_state:
    st.session_state["nome"] = ""
if "is_admin" not in st.session_state:
    st.session_state["is_admin"] = False
if "tentativas_login" not in st.session_state:
    st.session_state["tentativas_login"] = 0

# =====================================================
# CONEXÃO COM O MONGODB Atlas (mesmo cluster do bolão de grupos)
# =====================================================
@st.cache_resource
def conectar_mongodb():
    try:
        uri = st.secrets["database"]["mongodb_uri"]
        client = MongoClient(
            uri,
            serverSelectionTimeoutMS=5000,
            tls=True,
            tlsCAFile=certifi.where()
        )
        client.admin.command('ping')
        db = client["bolao_copa"]
        return db
    except Exception as e:
        st.error(f"⚠️ Erro ao conectar ao MongoDB Atlas: {e}")
        return None

db = conectar_mongodb()

# =====================================================
# FUNÇÕES DE MANIPULAÇÃO DE DADOS
# Collections com prefixo mm_ para nunca colidir com o bolão de grupos.
# A collection "usuarios" é COMPARTILHADA entre os dois bolões (login único).
# =====================================================
def carregar_dados_mongo(colecao_nome):
    if db is None:
        return pd.DataFrame()
    try:
        colecao = db[colecao_nome]
        dados = list(colecao.find({}, {"_id": 0}))
        return pd.DataFrame(dados)
    except Exception:
        return pd.DataFrame()

def salvar_usuario_mongo(novo_usuario_dict):
    if db is not None:
        try:
            db["usuarios"].insert_one(novo_usuario_dict)
            return True
        except Exception as e:
            st.error(f"Erro ao salvar usuário: {e}")
    return False

def proximo_id_confronto():
    """Gera o próximo ID sequencial para um novo confronto."""
    confrontos = carregar_dados_mongo("mm_confrontos")
    if confrontos.empty:
        return 1
    return int(confrontos["id"].max()) + 1

def salvar_confronto_mongo(id_confronto, fase, casa, cod_casa, fora, cod_fora, data_hora):
    if db is not None:
        try:
            db["mm_confrontos"].update_one(
                {"id": int(id_confronto)},
                {"$set": {
                    "id": int(id_confronto),
                    "fase": fase,
                    "casa": casa,
                    "cod_casa": cod_casa,
                    "fora": fora,
                    "cod_fora": cod_fora,
                    "data_hora": data_hora
                }},
                upsert=True
            )
            return True
        except Exception as e:
            st.error(f"Erro ao salvar confronto: {e}")
    return False

def excluir_confronto_mongo(id_confronto):
    if db is not None:
        try:
            db["mm_confrontos"].delete_one({"id": int(id_confronto)})
            db["mm_resultados_oficiais"].delete_one({"confronto_id": int(id_confronto)})
            db["mm_palpites"].delete_many({"confronto_id": int(id_confronto)})
            return True
        except Exception as e:
            st.error(f"Erro ao excluir confronto: {e}")
    return False

def salvar_palpite_mongo(usuario, confronto_id, palpite_casa, palpite_fora):
    if db is not None:
        try:
            db["mm_palpites"].update_one(
                {"usuario": usuario, "confronto_id": int(confronto_id)},
                {"$set": {
                    "usuario": usuario,
                    "confronto_id": int(confronto_id),
                    "palpite_casa": int(palpite_casa),
                    "palpite_fora": int(palpite_fora),
                    "atualizado_em": datetime.now()
                }},
                upsert=True
            )
            return True
        except Exception as e:
            st.error(f"Erro ao salvar palpite do confronto {confronto_id}: {e}")
    return False

def salvar_resultado_oficial_mongo(confronto_id, res_casa, res_fora):
    if db is not None:
        try:
            db["mm_resultados_oficiais"].update_one(
                {"confronto_id": int(confronto_id)},
                {"$set": {
                    "confronto_id": int(confronto_id),
                    "res_casa": int(res_casa),
                    "res_fora": int(res_fora)
                }},
                upsert=True
            )
            return True
        except Exception as e:
            st.error(f"Erro ao salvar resultado oficial: {e}")
    return False

def hash_senha(senha):
    return hashlib.sha256(senha.encode()).hexdigest()

def palpite_liberado_para_confronto(data_hora_jogo):
    """
    Regra do mata-mata: cada confronto trava individualmente 1 hora antes
    do seu próprio horário. Confrontos sem data/hora cadastrada ficam
    sempre liberados até o admin definir um horário.
    """
    if data_hora_jogo is None or pd.isna(data_hora_jogo):
        return True
    limite = data_hora_jogo - timedelta(hours=1)
    return datetime.now() < limite

# =====================================================
# CREDENCIAIS DO ADMIN (via Secrets — compartilhado com o bolão de grupos)
# =====================================================
ADMIN_LOGIN = st.secrets["admin"]["login"]
ADMIN_SENHA_HASH = hash_senha(st.secrets["admin"]["senha"])

# =====================================================
# CARREGAMENTO DOS DADOS
# =====================================================
usuarios = carregar_dados_mongo("usuarios")
confrontos_raw = carregar_dados_mongo("mm_confrontos")
palpites = carregar_dados_mongo("mm_palpites")
resultados_oficiais = carregar_dados_mongo("mm_resultados_oficiais")

# Garante que o admin existe no banco (mesma collection do bolão de grupos)
if not usuarios.empty and "login" in usuarios.columns:
    if not (usuarios["login"].astype(str).str.lower() == ADMIN_LOGIN).any():
        salvar_usuario_mongo({"login": ADMIN_LOGIN, "senha": ADMIN_SENHA_HASH, "nome": "Administrador"})
        usuarios = carregar_dados_mongo("usuarios")
elif db is not None:
    salvar_usuario_mongo({"login": ADMIN_LOGIN, "senha": ADMIN_SENHA_HASH, "nome": "Administrador"})
    usuarios = carregar_dados_mongo("usuarios")

# =====================================================
# MONTAGEM DO DATAFRAME DE CONFRONTOS
# =====================================================
if confrontos_raw.empty:
    confrontos = pd.DataFrame(columns=["id", "fase", "casa", "cod_casa", "fora", "cod_fora", "data_hora", "res_casa", "res_fora"])
else:
    confrontos = confrontos_raw.copy()
    confrontos["res_casa"] = None
    confrontos["res_fora"] = None
    # Garante que data_hora é datetime (o Mongo retorna datetime nativo, mas reforça por segurança)
    if "data_hora" in confrontos.columns:
        confrontos["data_hora"] = pd.to_datetime(confrontos["data_hora"], errors="coerce")

    if not resultados_oficiais.empty and "confronto_id" in resultados_oficiais.columns:
        for _, res in resultados_oficiais.iterrows():
            try:
                confrontos.loc[confrontos["id"] == int(res["confronto_id"]), "res_casa"] = res["res_casa"]
                confrontos.loc[confrontos["id"] == int(res["confronto_id"]), "res_fora"] = res["res_fora"]
            except Exception:
                pass

# =====================================================
# FUNÇÃO DE CÁLCULO DO RANKING
# Regras: cravou o placar (tempo normal/prorrogação, ignora pênaltis) = 3 pts
#         acertou o vencedor (ou empate) sem cravar = 1 pt
#         errou tudo = 0 pts
# =====================================================
def calcular_tabela_ranking_mm(df_palpites, df_confrontos):
    if df_palpites.empty:
        return pd.DataFrame(columns=["Posição", "Apostador", "Pontos Obtidos"])

    resultados = {}
    for _, cf in df_confrontos.iterrows():
        if pd.notna(cf["res_casa"]) and pd.notna(cf["res_fora"]):
            resultados[int(cf["id"])] = (int(cf["res_casa"]), int(cf["res_fora"]))

    pontuacao = {}
    for _, palpite in df_palpites.iterrows():
        user = palpite["usuario"]
        cf_id = int(palpite["confronto_id"])

        if cf_id in resultados:
            res_c, res_f = resultados[cf_id]
            pal_c = int(palpite["palpite_casa"])
            pal_f = int(palpite["palpite_fora"])

            if user not in pontuacao:
                pontuacao[user] = 0

            if pal_c == res_c and pal_f == res_f:
                pontuacao[user] += 3
            elif (
                (res_c > res_f and pal_c > pal_f) or
                (res_c < res_f and pal_c < pal_f) or
                (res_c == res_f and pal_c == pal_f)
            ):
                pontuacao[user] += 1

    if not pontuacao:
        apostadores_unicos = df_palpites["usuario"].unique()
        df_rank = pd.DataFrame([{"Apostador": u, "Pontos Obtidos": 0} for u in apostadores_unicos])
    else:
        df_rank = pd.DataFrame(list(pontuacao.items()), columns=["Apostador", "Pontos Obtidos"])

    df_rank = df_rank.sort_values(by="Pontos Obtidos", ascending=False).reset_index(drop=True)
    df_rank.insert(0, "Posição", df_rank.index + 1)
    return df_rank

# =====================================================
# LOGO FIFA NA SIDEBAR
# =====================================================
st.sidebar.markdown("""
<style>
.fifa-logo {
    display: flex; align-items: flex-end; justify-content: center; gap: 3px;
    font-size: 32px; font-weight: 900; line-height: 1; color: white;
    background: rgba(0,0,0,0.85); padding: 38px 12px 10px 12px;
    border-radius: 10px; font-family: Arial, sans-serif;
    overflow: visible; width: 100%; box-sizing: border-box; margin-bottom: 12px;
}
.soccer-wrapper { position: relative; display: inline-block; }
@keyframes letter-bounce {
    0%   { transform: scaleX(1.2) scaleY(0.75); }
    45%  { transform: scaleX(1) scaleY(1); }
    100% { transform: scaleX(1) scaleY(1); }
}
.letter-i { display: inline-block; transform-origin: bottom center; animation: letter-bounce 0.35s ease infinite alternate; }
@keyframes rotate-ball {
    from { transform: translateX(-50%) rotate(0deg); }
    to   { transform: translateX(-50%) rotate(360deg); }
}
@keyframes bounce-ball {
    from { bottom: 100%; }
    to   { bottom: calc(100% + 30px); }
}
.soccer {
    position: absolute; bottom: 100%; left: 50%; transform: translateX(-50%);
    width: 26px; height: 26px; font-size: 20px; margin-bottom: -15px;
    display: flex; align-items: center; justify-content: center;
    animation: rotate-ball 4s linear infinite, bounce-ball 0.35s ease-out infinite alternate;
}
</style>
<div class="fifa-logo">
    <span>F</span>
    <span class="soccer-wrapper">
        <span class="letter-i">I</span>
        <span class="soccer">&#x26BD;</span>
    </span>
    <span>FA</span>
</div>
""", unsafe_allow_html=True)

# =====================================================
# PLACAR NA SIDEBAR (pré-seleciona o confronto mais relevante)
# =====================================================
if not confrontos.empty:
    fases_existentes = [f for f in FASES_ORDEM if f in confrontos["fase"].unique()]

    def detectar_fase_relevante(df_confrontos):
        for fase in fases_existentes:
            cf_fase = df_confrontos[df_confrontos["fase"] == fase]
            if cf_fase["res_casa"].isna().any():
                return fase
        return fases_existentes[-1] if fases_existentes else None

    def detectar_confronto_relevante(df_confrontos_fase):
        sem_resultado = df_confrontos_fase[df_confrontos_fase["res_casa"].isna()]
        if not sem_resultado.empty:
            return sem_resultado.iloc[0]["id"]
        return df_confrontos_fase.iloc[-1]["id"]

    fase_default = detectar_fase_relevante(confrontos)

    if fase_default:
        idx_fase_default = fases_existentes.index(fase_default)
        fase_ativa_sb = st.sidebar.selectbox("📺 Fase em Destaque:", fases_existentes, index=idx_fase_default, key="sb_fase_placar")
        confrontos_sb_filtrados = confrontos[confrontos["fase"] == fase_ativa_sb]

        if not confrontos_sb_filtrados.empty:
            opcoes_sb = [f"{cf['casa']} X {cf['fora']}" for _, cf in confrontos_sb_filtrados.iterrows()]
            id_default_sb = detectar_confronto_relevante(confrontos_sb_filtrados)
            idx_confronto_default = list(confrontos_sb_filtrados["id"]).index(id_default_sb)

            confronto_sb_sel = st.sidebar.selectbox("Escolha o Confronto:", opcoes_sb, index=idx_confronto_default, key="sb_confronto_placar")
            idx_sel = opcoes_sb.index(confronto_sb_sel)
            cf_sb_dados = confrontos_sb_filtrados.iloc[idx_sel]

            res_c_sb = cf_sb_dados["res_casa"]
            res_f_sb = cf_sb_dados["res_fora"]
            gols_c_sb = int(res_c_sb) if pd.notna(res_c_sb) else 0
            gols_f_sb = int(res_f_sb) if pd.notna(res_f_sb) else 0
            status_sb = "ENCERRADO" if pd.notna(res_c_sb) else "AGENDADO"

            st.sidebar.markdown(f"""
                <style>
                @import url('https://fonts.googleapis.com/css?family=Days+One');
                .sb-score-container {{ width: 100%; display: flex; justify-content: center; margin-bottom: 35px; }}
                .sb-board {{ font-family: 'Days One', sans-serif; list-style: none; padding: 0; margin: 0; position: relative; width: 100%; height: 40px; border-radius: 6px; color: #ffffff; background: linear-gradient(to bottom, #0d1b2a 0%, #1b263b 100%); display: flex; align-items: center; justify-content: space-between; }}
                .sb-board li {{ display: flex; align-items: center; justify-content: center; height: 40px; }}
                .sb-board .team-home {{ justify-content: flex-end; width: 38%; padding-right: 6px; }}
                .sb-board .team-visitor {{ justify-content: flex-start; width: 38%; padding-left: 6px; }}
                .sb-board li img {{ max-height: 24px; width: auto; }}
                .sb-board .score {{ color: #ffffff; width: 24%; height: 40px; background: linear-gradient(to bottom, #00b4d8 0%, #0077b6 100%); font-weight: bold; font-size: 15px; }}
                .sb-board .time {{ color: #ffffff; position: absolute; top: 40px; left: 50%; margin-left: -50px; width: 100px; height: 18px; font-size: 9px; line-height: 18px; border-radius: 0px 0px 6px 6px; background: linear-gradient(to bottom, #7209b7 0%, #560bad 100%); text-align: center; }}
                </style>
                <div class="sb-score-container">
                    <ul class="sb-board">
                        <li class="team-home"><img src="https://flagcdn.com/h40/{cf_sb_dados['cod_casa']}.png" /> {cf_sb_dados['casa'][:3].upper()}</li>
                        <li class="score">{gols_c_sb} - {gols_f_sb}</li>
                        <li class="team-visitor">{cf_sb_dados['fora'][:3].upper()} <img src="https://flagcdn.com/h40/{cf_sb_dados['cod_fora']}.png" /></li>
                        <li class="time">{status_sb}</li>
                    </ul>
                </div>
            """, unsafe_allow_html=True)
else:
    st.sidebar.info("Nenhum confronto cadastrado ainda.")

st.sidebar.write("---")

# =====================================================
# MENU LATERAL — AUTENTICAÇÃO (compartilhada com o bolão de grupos)
# =====================================================
st.sidebar.title("🔐 Menu do Bolão")

if not st.session_state["logado"]:
    menu = st.sidebar.selectbox("Escolha uma opção", ["Entrar", "Cadastrar"])

    if menu == "Cadastrar":
        novo_login = st.sidebar.text_input("Login").strip().lower()
        novo_nome  = st.sidebar.text_input("Nome no bolão").strip()
        nova_senha = st.sidebar.text_input("Senha", type="password")
        if st.sidebar.button("Cadastrar"):
            if not novo_login or not novo_nome or not nova_senha:
                st.sidebar.error("Preencha todos os campos.")
            elif not usuarios.empty and (usuarios["login"].astype(str).str.lower() == novo_login).any():
                st.sidebar.error("Este login já está em uso.")
            else:
                if salvar_usuario_mongo({"login": novo_login, "senha": hash_senha(nova_senha), "nome": novo_nome}):
                    st.sidebar.success("Cadastrado! Mude para 'Entrar'.")
                    st.rerun()
    else:
        login = st.sidebar.text_input("Login").strip().lower()
        senha = st.sidebar.text_input("Senha", type="password")
        if st.sidebar.button("Entrar"):
            if st.session_state["tentativas_login"] >= 5:
                st.sidebar.error("Muitas tentativas. Recarregue a página para tentar novamente.")
            elif login == ADMIN_LOGIN and hash_senha(senha) == ADMIN_SENHA_HASH:
                st.session_state.update({"logado": True, "nome": "Administrador", "is_admin": True, "tentativas_login": 0})
                st.rerun()
            else:
                if not usuarios.empty and "login" in usuarios.columns:
                    user = usuarios[
                        (usuarios["login"].astype(str).str.lower() == login) &
                        (usuarios["senha"].astype(str) == hash_senha(senha))
                    ]
                    if not user.empty:
                        st.session_state.update({"logado": True, "nome": user.iloc[0]["nome"], "is_admin": False, "tentativas_login": 0})
                        st.rerun()
                    else:
                        st.session_state["tentativas_login"] += 1
                        restantes = 5 - st.session_state["tentativas_login"]
                        if restantes > 0:
                            st.sidebar.error(f"Usuário ou senha incorretos. {restantes} tentativa(s) restante(s).")
                        else:
                            st.sidebar.error("Muitas tentativas. Recarregue a página para tentar novamente.")
else:
    st.sidebar.success(f"🏃‍♂️ Apostador: {st.session_state['nome']}")
    if st.sidebar.button("🚪 Sair"):
        st.session_state.update({"logado": False, "nome": "", "is_admin": False, "tentativas_login": 0})
        st.rerun()

# Card de pontuação na sidebar
pontos_usuario, posicao_usuario = 0, "-"
if st.session_state["logado"]:
    try:
        rk = calcular_tabela_ranking_mm(palpites, confrontos)
        l_user = rk[rk["Apostador"] == st.session_state["nome"]]
        if not l_user.empty:
            pontos_usuario  = int(l_user.iloc[0]["Pontos Obtidos"])
            posicao_usuario = int(l_user.iloc[0]["Posição"])
    except Exception:
        pass

st.sidebar.markdown(f"""
<div style="background: linear-gradient(135deg, #0B1F3A, #133C7A); color: white; border-radius: 12px; padding: 15px; margin-top: 20px;">
    <div>👤 <b>{st.session_state['nome'] if st.session_state['logado'] else 'Visitante'}</b></div>
    <div style="margin-top: 5px;">⭐ <b>{pontos_usuario}</b> Pontos</div>
    <div style="margin-top: 5px;">📊 Ranking: <b>{posicao_usuario}º</b></div>
</div>
""", unsafe_allow_html=True)

# =====================================================
# INTERFACE PRINCIPAL
# =====================================================
st.title("🏆 BOLÃO MATA-MATA - COPA DO MUNDO 2026")

st.markdown("""
<style>
    @media (max-width: 768px) {
        .linha-jogo > div [data-testid="stHorizontalBlock"] {
            display: flex !important; flex-direction: row !important;
            flex-wrap: nowrap !important; align-items: center !important;
            justify-content: space-between !important; gap: 4px !important;
        }
        .linha-jogo [data-testid="column"] { min-width: 0 !important; padding: 0 !important; }
        .pais-texto { font-size: 11px !important; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .linha-jogo .stNumberInput input { text-align: center; padding: 2px !important; font-size: 13px !important; height: 32px !important; }
        .texto-vs { margin-top: 0px !important; font-size: 13px !important; }
    }
    .linha-jogo [data-testid="stHorizontalBlock"] { align-items: center !important; }
</style>
""", unsafe_allow_html=True)

# Aviso de regras do mata-mata, sempre visível no topo
st.info(
    "📌 **Regras do Mata-Mata:** Placar cravado (tempo normal ou prorrogação, "
    "desconsiderando pênaltis) = **3 pontos**. Acertar o vencedor sem cravar o "
    "placar = **1 ponto**. Errar tudo = **0 pontos**. Cada confronto trava para "
    "edição **1 hora antes** do seu horário de início."
)

if db is not None:
    abas = ["⚔️ Confrontos e Palpites", "📈 Ranking Geral", "👁️ Area de Secagem"]
    if st.session_state["is_admin"]:
        abas.append("👑 Painel do Admin")
    aba_ativa = st.radio("Navegação:", abas, horizontal=True)

    # ==================================================
    # ABA: CONFRONTOS E PALPITES
    # ==================================================
    if aba_ativa == "⚔️ Confrontos e Palpites":
        if confrontos.empty:
            st.warning("⏳ Nenhum confronto foi cadastrado ainda. Volte mais tarde!")
        else:
            fases_disponiveis = [f for f in FASES_ORDEM if f in confrontos["fase"].unique()]
            fase_sel = st.selectbox("⚔️ Escolha a Fase:", fases_disponiveis)
            confrontos_da_fase = confrontos[confrontos["fase"] == fase_sel].sort_values("data_hora", na_position="last")

            if st.session_state["logado"]:
                for _, cf in confrontos_da_fase.iterrows():
                    st.markdown("<div class='linha-jogo'>", unsafe_allow_html=True)

                    prazo_aberto_cf = palpite_liberado_para_confronto(cf["data_hora"])
                    key_salvo = f"salvo_{st.session_state['nome']}_{cf['id']}"

                    val_c, val_f, ja_tem_palpite = 0, 0, False
                    if not palpites.empty:
                        ex = palpites[
                            (palpites["usuario"] == st.session_state["nome"]) &
                            (palpites["confronto_id"] == cf["id"])
                        ]
                        if not ex.empty:
                            val_c, val_f = int(ex.iloc[0]["palpite_casa"]), int(ex.iloc[0]["palpite_fora"])
                            ja_tem_palpite = True
                            if key_salvo not in st.session_state:
                                st.session_state[key_salvo] = True

                    if key_salvo not in st.session_state:
                        st.session_state[key_salvo] = False

                    # Exibe no horário de Brasília (UTC-3)
                    if pd.notna(cf["data_hora"]):
                        horario_brasilia = pd.to_datetime(cf["data_hora"]) - timedelta(hours=3)
                        horario_fmt = horario_brasilia.strftime("%d/%m/%Y %H:%M")
                        fechamento_fmt = (horario_brasilia - timedelta(hours=1)).strftime("%d/%m %H:%M")
                        st.caption(f"🗓️ {horario_fmt}  •  Fecha às {fechamento_fmt} (horário de Brasília)")
                    else:
                        st.caption("🗓️ Horário a definir pelo administrador")

                    c_casa, c_vs, c_fora, c_acao = st.columns([3.5, 1.5, 3.5, 1.5])

                    with c_casa:
                        st.markdown(f"<div class='pais-texto' style='display:flex;align-items:center;gap:4px;justify-content:flex-end;'><b>{cf['casa']}</b> <img src='https://flagcdn.com/h40/{cf['cod_casa']}.png' width='20'></div>", unsafe_allow_html=True)
                        num_casa = st.number_input("", min_value=0, value=val_c, key=f"c_{cf['id']}", label_visibility="collapsed", disabled=(st.session_state[key_salvo] or not prazo_aberto_cf))

                    with c_vs:
                        txt = f"{int(cf['res_casa'])}x{int(cf['res_fora'])}" if pd.notna(cf["res_casa"]) else "X"
                        st.markdown(f"<p class='texto-vs' style='text-align:center;font-weight:bold;margin-bottom:0;'>{txt}</p>", unsafe_allow_html=True)

                    with c_fora:
                        st.markdown(f"<div class='pais-texto' style='display:flex;align-items:center;gap:4px;justify-content:flex-start;'><img src='https://flagcdn.com/h40/{cf['cod_fora']}.png' width='20'> <b>{cf['fora']}</b></div>", unsafe_allow_html=True)
                        num_fora = st.number_input("", min_value=0, value=val_f, key=f"f_{cf['id']}", label_visibility="collapsed", disabled=(st.session_state[key_salvo] or not prazo_aberto_cf))

                    with c_acao:
                        if prazo_aberto_cf:
                            if st.session_state[key_salvo]:
                                if st.button("✏️ Editar", key=f"btn_edit_{cf['id']}", use_container_width=True):
                                    st.session_state[key_salvo] = False
                                    st.rerun()
                            else:
                                if st.button("💾 Salvar", key=f"btn_save_{cf['id']}", use_container_width=True):
                                    salvar_palpite_mongo(st.session_state["nome"], cf["id"], num_casa, num_fora)
                                    st.session_state[key_salvo] = True
                                    st.success("Palpite salvo!")
                                    st.rerun()
                        else:
                            if ja_tem_palpite:
                                st.markdown("<p style='text-align:center;margin:0;font-size:18px;'>🔒</p>", unsafe_allow_html=True)
                            else:
                                st.markdown("<p style='text-align:center;margin:0;font-size:12px;color:gray;'>Encerrado</p>", unsafe_allow_html=True)

                    st.markdown("</div>", unsafe_allow_html=True)
                    st.write("---")
            else:
                st.info("Faça login para apostar.")
                st.dataframe(confrontos_da_fase[["casa", "fora"]], use_container_width=True, hide_index=True)

    # ==================================================
    # ABA: RANKING GERAL
    # ==================================================
    elif aba_ativa == "📈 Ranking Geral":
        st.dataframe(calcular_tabela_ranking_mm(palpites, confrontos), use_container_width=True, hide_index=True)

    # ==================================================
    # ABA: ÁREA DE SECAGEM
    # ==================================================
    elif aba_ativa == "👁️ Area de Secagem":
        st.markdown("### 👁️ Palpites dos Participantes")
        st.write("---")

        if confrontos.empty:
            st.info("Nenhum confronto cadastrado ainda.")
        else:
            # Um confronto só fica visível na área de secagem após o SEU PRÓPRIO prazo encerrar
            confrontos_encerrados = confrontos[confrontos["data_hora"].apply(
                lambda dh: not palpite_liberado_para_confronto(dh) if pd.notna(dh) else False
            )]

            if confrontos_encerrados.empty:
                st.warning("⏳ Nenhum confronto teve o prazo de palpites encerrado ainda!")
                st.info("Os palpites de cada confronto só ficam visíveis publicamente 1 hora antes do respectivo jogo começar, para evitar cópias de última hora.")
            else:
                lista_confrontos_formatada = [f"{row['fase']} — {row['casa']} x {row['fora']}" for _, row in confrontos_encerrados.iterrows()]

                # Pré-seleciona o último confronto com resultado, senão o mais recente encerrado
                com_resultado = confrontos_encerrados[confrontos_encerrados["res_casa"].notna()]
                if not com_resultado.empty:
                    id_default_secagem = com_resultado.sort_values("data_hora").iloc[-1]["id"]
                else:
                    id_default_secagem = confrontos_encerrados.sort_values("data_hora").iloc[-1]["id"]

                idx_default_secagem = list(confrontos_encerrados["id"]).index(id_default_secagem)
                confronto_escolhido_texto = st.selectbox(
                    "Selecione o confronto para ver o palpite de todos:",
                    lista_confrontos_formatada,
                    index=idx_default_secagem
                )
                idx_sel_secagem = lista_confrontos_formatada.index(confronto_escolhido_texto)
                id_confronto_selecionado = int(confrontos_encerrados.iloc[idx_sel_secagem]["id"])

                dados_do_confronto = confrontos[confrontos["id"] == id_confronto_selecionado].iloc[0]
                todos_palpites = carregar_dados_mongo("mm_palpites")
                df_filtrado = todos_palpites[todos_palpites["confronto_id"] == id_confronto_selecionado] if not todos_palpites.empty else pd.DataFrame()

                if not df_filtrado.empty:
                    df_exibicao = df_filtrado[["usuario", "palpite_casa", "palpite_fora"]].copy()
                    df_exibicao.columns = ["🚨 Participante", f"Gols {dados_do_confronto['casa']} 🏠", f"Gols {dados_do_confronto['fora']} 🚌"]
                    st.dataframe(df_exibicao, use_container_width=True, hide_index=True)
                else:
                    st.info("Ninguém cadastrou palpite para este confronto.")

    # ==================================================
    # ABA: PAINEL DO ADMIN
    # ==================================================
    elif aba_ativa == "👑 Painel do Admin" and st.session_state["is_admin"]:
        st.markdown("<h3 style='text-align:center;'>👑 Painel de Administração — Mata-Mata</h3>", unsafe_allow_html=True)
        st.write("---")

        # --- SEÇÃO 1: Cadastrar novo confronto ---
        st.subheader("➕ Cadastrar Novo Confronto")
        st.caption("Cadastre cada confronto conforme as fases anteriores forem definidas.")

        with st.form("form_novo_confronto", clear_on_submit=True):
            col_fase, col_data, col_hora = st.columns([2, 1.5, 1])
            with col_fase:
                fase_novo = st.selectbox("Fase:", FASES_ORDEM)
            with col_data:
                data_novo = st.date_input("Data do jogo:")
            with col_hora:
                hora_novo = st.time_input("Hora do jogo:")

            col_casa, col_cod_casa, col_fora, col_cod_fora = st.columns(4)
            with col_casa:
                casa_novo = st.text_input("Time da Casa:")
            with col_cod_casa:
                cod_casa_novo = st.text_input("Código bandeira (casa):", help="Código ISO de 2 letras, ex: br, ar, fr").strip().lower()
            with col_fora:
                fora_novo = st.text_input("Time Visitante:")
            with col_cod_fora:
                cod_fora_novo = st.text_input("Código bandeira (fora):", help="Código ISO de 2 letras, ex: de, pt, nl").strip().lower()

            if st.form_submit_button("Cadastrar Confronto", use_container_width=True):
                if not casa_novo or not fora_novo or not cod_casa_novo or not cod_fora_novo:
                    st.error("Preencha todos os campos antes de cadastrar.")
                else:
                    # Compensa o fuso UTC-3 (Brasília): o Streamlit Cloud roda em UTC,
                    # então somamos 3h para que "16:00 de Brasília" seja salvo como 19:00 UTC
                    data_hora_novo = datetime.combine(data_novo, hora_novo) + timedelta(hours=3)
                    novo_id = proximo_id_confronto()
                    if salvar_confronto_mongo(novo_id, fase_novo, casa_novo.strip(), cod_casa_novo, fora_novo.strip(), cod_fora_novo, data_hora_novo):
                        st.success(f"Confronto '{casa_novo} x {fora_novo}' cadastrado na fase {fase_novo}!")
                        st.rerun()

        st.write("---")

        # --- SEÇÃO 2: Editar / Excluir confrontos existentes ---
        st.subheader("✏️ Editar ou Excluir Confronto")

        if confrontos.empty:
            st.info("Nenhum confronto cadastrado ainda.")
        else:
            opcoes_edicao = [f"#{cf['id']} — {cf['fase']} — {cf['casa']} x {cf['fora']}" for _, cf in confrontos.iterrows()]
            confronto_edicao_sel = st.selectbox("Selecione o confronto:", opcoes_edicao, key="sb_editar_confronto")
            id_confronto_edicao = int(confronto_edicao_sel.split("—")[0].replace("#", "").strip())
            cf_edicao = confrontos[confrontos["id"] == id_confronto_edicao].iloc[0]

            with st.form(f"form_editar_confronto_{id_confronto_edicao}"):
                col_fase_e, col_data_e, col_hora_e = st.columns([2, 1.5, 1])
                with col_fase_e:
                    fase_idx = FASES_ORDEM.index(cf_edicao["fase"]) if cf_edicao["fase"] in FASES_ORDEM else 0
                    fase_edit = st.selectbox("Fase:", FASES_ORDEM, index=fase_idx, key=f"fase_edit_{id_confronto_edicao}")
                with col_data_e:
                    data_atual = pd.to_datetime(cf_edicao["data_hora"]).date() if pd.notna(cf_edicao["data_hora"]) else datetime.now().date()
                    data_edit = st.date_input("Data do jogo:", value=data_atual, key=f"data_edit_{id_confronto_edicao}")
                with col_hora_e:
                    hora_atual = pd.to_datetime(cf_edicao["data_hora"]).time() if pd.notna(cf_edicao["data_hora"]) else datetime.now().time()
                    hora_edit = st.time_input("Hora do jogo:", value=hora_atual, key=f"hora_edit_{id_confronto_edicao}")

                col_casa_e, col_cod_casa_e, col_fora_e, col_cod_fora_e = st.columns(4)
                with col_casa_e:
                    casa_edit = st.text_input("Time da Casa:", value=cf_edicao["casa"], key=f"casa_edit_{id_confronto_edicao}")
                with col_cod_casa_e:
                    cod_casa_edit = st.text_input("Código bandeira (casa):", value=cf_edicao["cod_casa"], key=f"cod_casa_edit_{id_confronto_edicao}").strip().lower()
                with col_fora_e:
                    fora_edit = st.text_input("Time Visitante:", value=cf_edicao["fora"], key=f"fora_edit_{id_confronto_edicao}")
                with col_cod_fora_e:
                    cod_fora_edit = st.text_input("Código bandeira (fora):", value=cf_edicao["cod_fora"], key=f"cod_fora_edit_{id_confronto_edicao}").strip().lower()

                col_btn_salvar, col_btn_excluir = st.columns(2)
                with col_btn_salvar:
                    salvar_edicao = st.form_submit_button("💾 Salvar Alterações", use_container_width=True)
                with col_btn_excluir:
                    excluir_edicao = st.form_submit_button("🗑️ Excluir Confronto", use_container_width=True)

                if salvar_edicao:
                    # Compensa o fuso UTC-3 (Brasília)
                    data_hora_edit = datetime.combine(data_edit, hora_edit) + timedelta(hours=3)
                    if salvar_confronto_mongo(id_confronto_edicao, fase_edit, casa_edit.strip(), cod_casa_edit, fora_edit.strip(), cod_fora_edit, data_hora_edit):
                        st.success("Confronto atualizado!")
                        st.rerun()

                if excluir_edicao:
                    if excluir_confronto_mongo(id_confronto_edicao):
                        st.success("Confronto excluído, junto com seus palpites e resultado.")
                        st.rerun()

        st.write("---")

        # --- SEÇÃO 3: Lançar resultado oficial ---
        st.subheader("⚽ Lançar Resultado Oficial")
        st.caption("Lance aqui o placar final (tempo normal ou prorrogação). Não inclua o resultado dos pênaltis.")

        if confrontos.empty:
            st.info("Cadastre um confronto primeiro.")
        else:
            opcoes_resultado = [f"#{cf['id']} — {cf['fase']} — {cf['casa']} x {cf['fora']}" for _, cf in confrontos.iterrows()]
            confronto_resultado_sel = st.selectbox("Selecione o confronto:", opcoes_resultado, key="sb_resultado_confronto")
            id_confronto_resultado = int(confronto_resultado_sel.split("—")[0].replace("#", "").strip())
            cf_resultado = confrontos[confrontos["id"] == id_confronto_resultado].iloc[0]

            res_c_atual = int(cf_resultado["res_casa"]) if pd.notna(cf_resultado["res_casa"]) else 0
            res_f_atual = int(cf_resultado["res_fora"]) if pd.notna(cf_resultado["res_fora"]) else 0

            with st.form(f"form_resultado_{id_confronto_resultado}"):
                c1, c2, c3 = st.columns([4, 1.5, 4])
                with c1:
                    st.markdown(f"<p style='text-align:right;'><b>{cf_resultado['casa']}</b></p>", unsafe_allow_html=True)
                    gols_casa = st.number_input("", min_value=0, value=res_c_atual, key=f"res_c_{id_confronto_resultado}", label_visibility="collapsed")
                with c2:
                    st.markdown("<p style='text-align:center;font-weight:bold;'>X</p>", unsafe_allow_html=True)
                with c3:
                    st.markdown(f"<p style='text-align:left;'><b>{cf_resultado['fora']}</b></p>", unsafe_allow_html=True)
                    gols_fora = st.number_input("", min_value=0, value=res_f_atual, key=f"res_f_{id_confronto_resultado}", label_visibility="collapsed")

                if st.form_submit_button("🚀 PUBLICAR RESULTADO", use_container_width=True):
                    if salvar_resultado_oficial_mongo(id_confronto_resultado, gols_casa, gols_fora):
                        st.success("Resultado publicado!")
                        st.rerun()

        st.write("---")

        # --- SEÇÃO 4: Resetar senha (compartilhado com o bolão de grupos) ---
        st.subheader("🔑 Resetar Senha de Usuários")

        if not usuarios.empty and "login" in usuarios.columns:
            lista_usuarios = sorted([str(u) for u in usuarios["login"].unique() if pd.notna(u) and u != ADMIN_LOGIN])

            if lista_usuarios:
                usuario_alvo = st.selectbox("Selecione o usuário que esqueceu a senha:", lista_usuarios)
                nova_senha_admin = st.text_input("Digite a Nova Senha para este usuário:", type="password")

                if st.button("Alterar Senha", use_container_width=True):
                    if not nova_senha_admin:
                        st.error("Por favor, digite uma nova senha válida.")
                    else:
                        try:
                            db["usuarios"].update_one(
                                {"login": usuario_alvo},
                                {"$set": {"senha": hash_senha(nova_senha_admin)}}
                            )
                            st.success(f"Senha de '{usuario_alvo}' alterada com sucesso.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Erro ao atualizar banco: {e}")
            else:
                st.info("Nenhum usuário comum cadastrado ainda.")
        else:
            st.info("Nenhum usuário encontrado no banco de dados.")
