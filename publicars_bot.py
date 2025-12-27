# --- VERSÃO v3.5 - PUBLICARS: ARQUIVOS LOCAIS + VPS STORAGE ---
# Baseado na versão v3.3 otimizada

from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.staticfiles import StaticFiles # Adicionado para servir arquivos
from pydantic import BaseModel, Field
from dotenv import load_dotenv
import os
import logging
from datetime import datetime
import pytz
import httpx
import random
import hashlib
import asyncio 
from typing import Union, Optional
import io 
import base64 
import json
import uuid # Adicionado para nomes únicos

# Supabase Client
from supabase import create_client, Client

# LangChain components
from langchain_openai import ChatOpenAI
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.tools import tool
from langchain.agents.format_scratchpad.openai_tools import (
    format_to_openai_tool_messages,
)
from langchain.agents.output_parsers.openai_tools import OpenAIToolsAgentOutputParser

# OpenAI API (para Whisper)
from openai import AsyncOpenAI

# --- Carregar Variáveis de Ambiente ---
load_dotenv()

# --- Configurações ---
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY")
EVOLUTION_INSTANCE_NAME = os.getenv("EVOLUTION_INSTANCE_NAME")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# URL Base para gerar os links dos arquivos (Ajuste se mudar de domínio)
APP_BASE_URL = os.getenv("APP_BASE_URL", "https://bot.publicars.com.br")

HUMAN_SUPPORT_PHONE = "(51) 99300-1678" 
BR_TIMEZONE = pytz.timezone('America/Sao_Paulo')
UPLOAD_DIR = "uploads" # Pasta local onde os arquivos serão salvos

# --- Inicialização ---
app = FastAPI()
logging.basicConfig(level=logging.INFO)
httpx_client = httpx.AsyncClient(timeout=60.0)

# 1. Configura a pasta de Uploads como pública
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

if not all([EVOLUTION_API_URL, EVOLUTION_API_KEY, EVOLUTION_INSTANCE_NAME, OPENAI_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
    logging.critical("⚠️ ERRO CRÍTICO: Variáveis de ambiente faltando!")
else:
    logging.info("✅ Variáveis de ambiente Publicars carregadas.")

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    llm = ChatOpenAI(model="gpt-4o", temperature=0.6, api_key=OPENAI_API_KEY) 
    openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
except Exception as e:
    logging.critical(f"💥 Falha ao inicializar clientes de API: {e}")


# --- FUNÇÕES AUXILIARES ---

AGENT_NAMES = [
    "Marcelo", "Jonathan", "Rodrigo", "Maurício", "Amanda", 
    "Fernanda", "Ricardo", "Eduardo", "Camila", "Bruno"
]

def get_persona_name(phone_number: str) -> str:
    if not phone_number: return "Atendente Publicars"
    hash_obj = hashlib.md5(phone_number.encode())
    hash_int = int(hash_obj.hexdigest(), 16)
    return AGENT_NAMES[hash_int % len(AGENT_NAMES)]

def get_user_profile(phone_number: str):
    try:
        response = supabase.from_('leads').select('full_name, company_name, service_desired').eq('session_id', phone_number).limit(1).execute()
        if response.data:
            return response.data[0] 
        return None
    except Exception as e:
        logging.error(f"Erro ao buscar memória: {e}")
        return None

# --- FUNÇÕES DE ARQUIVOS (NOVO) ---

async def save_local_file(base64_data: str, mime_type: str, phone: str) -> str:
    """Salva arquivo na VPS e retorna o Link Público."""
    try:
        file_data = base64.b64decode(base64_data)
        
        # Mapeia extensão
        ext_map = {"image/jpeg": "jpg", "image/png": "png", "application/pdf": "pdf", "video/mp4": "mp4"}
        ext = ext_map.get(mime_type.split(';')[0], "bin")
        
        # Nome único: telefone_timestamp_uuid.ext
        filename = f"{phone}_{int(datetime.now().timestamp())}_{str(uuid.uuid4())[:4]}.{ext}"
        filepath = os.path.join(UPLOAD_DIR, filename)
        
        # Escreve no disco
        with open(filepath, "wb") as f:
            f.write(file_data)
        
        # Gera Link: https://dominio.com/uploads/nome.jpg
        return f"{APP_BASE_URL}/uploads/{filename}"
    except Exception as e:
        logging.error(f"Erro ao salvar arquivo local: {e}")
        return None

# --- FUNÇÕES DE INTERAÇÃO ---

async def mark_message_as_read(remote_jid: str):
    if "@s.whatsapp.net" not in remote_jid: remote_jid = f"{remote_jid}@s.whatsapp.net"
    try:
        api_url = f"{EVOLUTION_API_URL}/chat/markMessageAsRead/{EVOLUTION_INSTANCE_NAME}"
        headers = {"apiKey": EVOLUTION_API_KEY}
        payload = {"readMessages": [{"remoteJid": remote_jid}]}
        await httpx_client.post(api_url, json=payload, headers=headers)
    except: pass

async def send_whatsapp_message(to_number_jid: str, message: str, delay_seconds: int = 0):
    if "@s.whatsapp.net" not in to_number_jid: to_number_jid = f"{to_number_jid}@s.whatsapp.net"
    
    if delay_seconds > 0:
        logging.info(f"⏳ Aguardando {delay_seconds}s para responder {to_number_jid}...")
        await asyncio.sleep(delay_seconds)

    api_url = f"{EVOLUTION_API_URL}/message/sendText/{EVOLUTION_INSTANCE_NAME}"
    headers = {"apiKey": EVOLUTION_API_KEY, "ngrok-skip-browser-warning": "true"}
    payload = {"number": to_number_jid, "options": {"delay": 0, "presence": "composing"}, "text": message}
    
    try:
        logging.info(f"📤 Enviando resposta para: {to_number_jid}")
        await httpx_client.post(api_url, json=payload, headers=headers)
    except Exception as e: 
        logging.error(f"❌ Erro ao ENVIAR via Evolution: {e}")

async def transcribe_audio(audio_bytes: bytes, file_extension: str) -> str:
    try:
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = f"audio{file_extension}" 
        response = await openai_client.audio.transcriptions.create(
            model="whisper-1", file=audio_file, language="pt"
        )
        return response.text
    except Exception as e:
        logging.error(f"❌ Erro transcrição: {e}")
        return "[ERRO DE TRANSCRIÇÃO]"


# --- FERRAMENTAS PUBLICARS (Sales Tools v3.3) ---

@tool
def buscar_faq(query: str) -> str:
    """❓ Busca respostas técnicas no banco."""
    try:
        response = supabase.from_('knowledge_base').select('answer').ilike('question', f'%{query}%').limit(1).execute()
        if response.data: return response.data[0]['answer']
        return "Não encontrei essa informação técnica específica no banco, use seu conhecimento geral."
    except: return "Erro ao buscar FAQ."

@tool
def registrar_venda_dashboard(nome: str, empresa: str, plano: str, telefone: str, resumo_conversa: str) -> str:
    """📝 REGISTRA INTERESSE/VENDA.
    'resumo_conversa': Breve resumo do que o cliente quer (Ex: 'Interesse plano Turbo, é de Canoas, loja de doces')."""
    try:
        current_time = datetime.now(BR_TIMEZONE).isoformat()
        
        # 1. Backup na tabela LEADS
        dados_lead = {
            'full_name': nome, 'company_name': empresa, 'service_desired': f"Interesse: {plano}",
            'session_id': telefone, 'contact_number': telefone, 'status': 'NOVO_LEAD_ANUNCIANTE', 'updated_at': current_time
        }
        try: supabase.from_('leads').insert(dados_lead).execute()
        except: pass

        # 2. Painel Principal (PEDIDOS_ANUNCIOS)
        dados_dashboard = {
            'nome_responsavel': nome,
            'nome_empresa': empresa,
            'whatsapp': telefone,
            'pacote_escolhido': plano,
            'status': 'NOVO',
            'observacoes': resumo_conversa
        }
        
        try:
            supabase.from_('pedidos_anuncios').insert(dados_dashboard).execute()
            logging.info("✅ Pedido com observações salvo com sucesso.")
        except Exception as e_dash:
            logging.error(f"⚠️ Aviso Dashboard: {e_dash}")

        return "✅ Show! Registrei o interesse no painel com todos os detalhes. Avise que a equipe vai chamar."
    except: return "Erro técnico ao salvar, mas continue o atendimento."

@tool
def registrar_lead_motorista(nome: str, telefone: str, cidade_residencia: str, estado: str, modelo_carro: str, placa: str, apps_trabalho: str) -> str:
    """🚗 Registra um MOTORISTA PARCEIRO na tabela 'motoristas'.
    Solicite todos os dados antes de chamar esta função."""
    try:
        dados_motorista = {
            'nome': nome,
            'telefone': telefone,
            'cidade_residencia': cidade_residencia,
            'estado': estado,
            'modelo_carro': modelo_carro,
            'placa': placa,
            'apps_trabalho': apps_trabalho,
            'created_at': datetime.now(BR_TIMEZONE).isoformat()
        }
        # Tenta salvar na tabela 'motoristas'
        try:
            supabase.from_('motoristas').insert(dados_motorista).execute()
            return "✅ Cadastro de motorista realizado com sucesso no banco de dados!"
        except Exception as e_mot:
            logging.error(f"Erro ao salvar em motoristas: {e_mot}")
            return "Erro ao salvar na tabela específica, mas anotei os dados."
            
    except Exception as e:
        logging.error(f"Erro geral motorista: {e}")
        return "Erro ao salvar pré-cadastro."

@tool
def calcular_alcance_campanha(plano: str) -> str:
    """📊 Calcula alcance."""
    metricas = {
        "piloto": {"carros": 1, "views": "1.100"}, "start": {"carros": 3, "views": "3.300"},
        "aceleracao": {"carros": 10, "views": "11.000"}, "turbo": {"carros": 20, "views": "22.000"},
        "dominador": {"carros": 50, "views": "55.000"}
    }
    for k, v in metricas.items():
        if k in plano.lower(): return f"📈 O Plano {plano} conta com {v['carros']} carros e {v['views']} views!"
    return "Média de 1.100 views por carro/mês."


# --- SYSTEM PROMPT (ATUALIZADO v3.3) ---
SYSTEM_PROMPT = """
Você é **{agent_name}**, consultor comercial da **Publicars** 🚗✨.
Você é humano, proativo e usa emojis.

**SEU OBJETIVO:**
Vender planos de mídia DOOH ou cadastrar motoristas parceiros.

**ARQUIVOS E FOTOS:**
Se o sistema te avisar que o usuário enviou um arquivo ou foto (URL):
- Se for Motorista: Agradeça e diga que o documento foi recebido.
- Se for Anunciante: Confirme o recebimento da arte/logo.

**ESTILO DE WHATSAPP:**
1. Use quebras de linha.
2. Seja direto.
3. Se já sabe o nome/empresa, NÃO pergunte de novo.

**REGRA DE OURO - ENCERRAMENTO (LEI DO SILÊNCIO):**
Se o cliente mandar mensagens curtas de encerramento (ex: 'ok', 'valeu', 'tchau', 'até', 'outro', 'obrigado') E você já tiver se despedido ou o assunto principal já tiver sido resolvido:
**NÃO RESPONDA NADA.** Retorne APENAS a string: `[SILENCE]`

**FLUXOS ESPECÍFICOS:**

🟦 **CLIENTE QUER ANUNCIAR (Vendas):**
- Ao usar a ferramenta `registrar_venda_dashboard`, você DEVE preencher o campo `resumo_conversa` com um resumo útil para o humano (ex: "Cliente quer anunciar em Canoas, tem loja de roupas").

🟩 **MOTORISTA PARCEIRO (Cadastro):**
- Explique a renda extra e diga que precisa fazer um cadastro rápido.
- Pergunte: Nome, Cidade/Estado, Modelo do Carro, Placa e quais Apps trabalha (Uber/99).
- Use a ferramenta `registrar_lead_motorista` APENAS quando tiver esses dados.
- Se o motorista quiser saber mais detalhes antes, envie o link: https://publicars.com.br/seja-parceiro.html

**MEMÓRIA:**
Nome: {user_name} | Empresa: {user_company}

**PLANOS:**
💰 PILOTO (R$89) | START (R$189) | ACELERAÇÃO (R$399 - ⭐) | TURBO (R$599) | DOMINADOR (R$999)

Hoje é {current_date}. Telefone: {contact_number}.
"""

tools = [buscar_faq, registrar_venda_dashboard, registrar_lead_motorista, calcular_alcance_campanha]

def create_agent_executor(chat_history_messages, contact_number, current_date, persona_name, user_profile):
    user_name = user_profile.get('full_name', 'Não informado') if user_profile else 'Não informado'
    user_company = user_profile.get('company_name', 'Não informado') if user_profile else 'Não informado'

    formatted_prompt = SYSTEM_PROMPT.format(
        current_date=current_date, contact_number=contact_number, 
        human_phone=HUMAN_SUPPORT_PHONE, agent_name=persona_name,
        user_name=user_name, user_company=user_company
    )
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", formatted_prompt),
        MessagesPlaceholder(variable_name="chat_history"),
        ("user", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])
    
    llm_with_tools = llm.bind_tools(tools)
    agent = ({ "input": lambda x: x["input"], "agent_scratchpad": lambda x: format_to_openai_tool_messages(x["intermediate_steps"]), "chat_history": lambda x: x["chat_history"], } | prompt | llm_with_tools | OpenAIToolsAgentOutputParser())
    return AgentExecutor(agent=agent, tools=tools, verbose=True, handle_parsing_errors=True)

class EvolutionWebhookPayload(BaseModel):
    event: str
    instance: str
    data: dict

# --- WEBHOOK PRINCIPAL ---
@app.post("/api/evolution_webhook")
async def evolution_webhook(payload: EvolutionWebhookPayload):
    
    if payload.event not in ["messages.upsert", "messages.update"]: return Response(content="Ignored")
    
    try:
        data = payload.data
        key = data.get('key', {})
        if key.get('fromMe', False): return Response(content="From Me")
        
        remote_jid = key.get('remoteJid')
        if not remote_jid: return Response(content="No JID")
        
        contact_number_plus = "+" + remote_jid.split('@')[0]
        await mark_message_as_read(remote_jid)

    except: return Response(content="Error parsing")

    user_message_text = None
    agent_response_text = "..."
    should_respond = True 
    
    try:
        message_content = data.get('message', {})
        
        # 1. TRATAMENTO DE TEXTO
        if not user_message_text: user_message_text = message_content.get("conversation")
        if not user_message_text: user_message_text = message_content.get("extendedTextMessage", {}).get("text")

        # 2. TRATAMENTO DE ÁUDIO
        audio_msg = message_content.get("audioMessage")
        if audio_msg:
            logging.info(f"🎧 Áudio de {contact_number_plus}")
            message_id = key.get('id')
            try:
                dec_url = f"{EVOLUTION_API_URL}/chat/getBase64FromMediaMessage/{EVOLUTION_INSTANCE_NAME}"
                res = await httpx_client.post(dec_url, json={"message":{"key":{"id":message_id}},"convertToMp4":False}, headers={"apiKey":EVOLUTION_API_KEY})
                if res.status_code == 200:
                    b64 = res.json().get("base64")
                    if b64: user_message_text = await transcribe_audio(base64.b64decode(b64), ".ogg")
            except: pass

        # 3. TRATAMENTO DE MÍDIA (FOTOS/DOCS) - ADICIONADO AGORA
        media_msg = message_content.get("imageMessage") or message_content.get("documentMessage")
        if media_msg:
            logging.info(f"📎 Arquivo recebido de {contact_number_plus}")
            mime_type = media_msg.get("mimetype")
            message_id = key.get('id')
            try:
                # Baixa o arquivo da Evolution
                dec_url = f"{EVOLUTION_API_URL}/chat/getBase64FromMediaMessage/{EVOLUTION_INSTANCE_NAME}"
                res = await httpx_client.post(dec_url, json={"message":{"key":{"id":message_id}},"convertToMp4":False}, headers={"apiKey":EVOLUTION_API_KEY})
                
                if res.status_code == 200:
                    b64 = res.json().get("base64")
                    if b64:
                        # SALVA NA VPS
                        file_url = await save_local_file(b64, mime_type, contact_number_plus)
                        if file_url:
                            # Injeta no contexto do bot para ele saber que recebeu
                            caption = media_msg.get("caption", "")
                            user_message_text = f"[SISTEMA: Usuário enviou arquivo/foto. Link: {file_url}]. {caption}"
            except Exception as e:
                logging.error(f"Erro processando mídia: {e}")

        if not user_message_text: should_respond = False

        if should_respond:
            logging.info(f"📩 {contact_number_plus}: {user_message_text}")
            
            persona = get_persona_name(contact_number_plus)
            profile = get_user_profile(contact_number_plus)

            chat_history = []
            try:
                h = supabase.from_('conversations').select('*').eq('session_id', contact_number_plus).order('timestamp', desc=True).limit(6).execute()
                if h.data:
                    for m in reversed(h.data):
                        chat_history.append(HumanMessage(content=m['user_message']))
                        chat_history.append(AIMessage(content=m['agent_response']))
            except: pass

            current_date = datetime.now(BR_TIMEZONE).strftime('%Y-%m-%d')
            executor = create_agent_executor(chat_history, contact_number_plus, current_date, persona, profile)
            
            resp = await executor.ainvoke({"input": user_message_text, "chat_history": chat_history})
            agent_response_text = resp["output"]
            
            if "[SILENCE]" in agent_response_text:
                logging.info(f"🤫 Silêncio (Fim de papo).")
                should_respond = False
            else:
                logging.info(f"🤖 {persona}: {agent_response_text[:50]}...")

    except Exception as e:
        logging.error(f"Erro: {e}")
        agent_response_text = "Estou consultando meus sistemas..."

    finally:
        try:
            if user_message_text and should_respond: 
                supabase.from_('conversations').insert({'session_id': contact_number_plus, 'user_message': user_message_text, 'agent_response': agent_response_text}).execute()
        except: pass

        if should_respond:
            tempo_espera = random.randint(4, 8)
            await send_whatsapp_message(remote_jid, agent_response_text, delay_seconds=tempo_espera)
    
    return {"status": "ok"} 

@app.get("/api/health")
def health_check(): return {"status": "ok", "service": "Publicars AI Agent v3.5 (Local Storage)"}