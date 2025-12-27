# --- VERSÃO v3.6 - PUBLICARS: TEXTO + ÁUDIO + FOTOS + PDF (VPS STORAGE) ---
# Baseado na versão v3.3 funcional

from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.staticfiles import StaticFiles # Importante para servir os arquivos
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
import uuid # Para nomes de arquivos únicos

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

# Domínio base para gerar os links (Ajuste se necessário)
APP_BASE_URL = os.getenv("APP_BASE_URL", "https://bot.publicars.com.br")

HUMAN_SUPPORT_PHONE = "(51) 99300-1678" 
BR_TIMEZONE = pytz.timezone('America/Sao_Paulo')
UPLOAD_DIR = "uploads" # Pasta onde vamos salvar

# --- Inicialização ---
app = FastAPI()
logging.basicConfig(level=logging.INFO)
httpx_client = httpx.AsyncClient(timeout=60.0)

# 1. Cria a pasta e monta o servidor de arquivos
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

AGENT_NAMES = ["Marcelo", "Jonathan", "Rodrigo", "Maurício", "Amanda", "Fernanda", "Ricardo", "Eduardo", "Camila", "Bruno"]

def get_persona_name(phone_number: str) -> str:
    if not phone_number: return "Atendente Publicars"
    hash_obj = hashlib.md5(phone_number.encode())
    return AGENT_NAMES[int(hash_obj.hexdigest(), 16) % len(AGENT_NAMES)]

def get_user_profile(phone_number: str):
    try:
        response = supabase.from_('leads').select('full_name, company_name, service_desired').eq('session_id', phone_number).limit(1).execute()
        if response.data: return response.data[0] 
        return None
    except Exception as e:
        logging.error(f"Erro ao buscar memória: {e}")
        return None

# --- FUNÇÃO NOVA: SALVAR ARQUIVO LOCAL ---
async def save_local_file(base64_data: str, mime_type: str, phone: str) -> str:
    """Salva Base64 como arquivo na VPS e retorna URL pública."""
    try:
        file_data = base64.b64decode(base64_data)
        
        # Mapa simples de extensão
        ext = "bin"
        if "pdf" in mime_type: ext = "pdf"
        elif "jpeg" in mime_type or "jpg" in mime_type: ext = "jpg"
        elif "png" in mime_type: ext = "png"
        elif "mp4" in mime_type: ext = "mp4"
        elif "word" in mime_type: ext = "docx"
        
        # Nome único: numero_timestamp.ext
        filename = f"{phone}_{int(datetime.now().timestamp())}_{str(uuid.uuid4())[:4]}.{ext}"
        filepath = os.path.join(UPLOAD_DIR, filename)
        
        with open(filepath, "wb") as f:
            f.write(file_data)
            
        return f"{APP_BASE_URL}/uploads/{filename}"
    except Exception as e:
        logging.error(f"Erro salvar arquivo: {e}")
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
        logging.info(f"⏳ Aguardando {delay_seconds}s...")
        await asyncio.sleep(delay_seconds)

    api_url = f"{EVOLUTION_API_URL}/message/sendText/{EVOLUTION_INSTANCE_NAME}"
    headers = {"apiKey": EVOLUTION_API_KEY, "ngrok-skip-browser-warning": "true"}
    payload = {"number": to_number_jid, "options": {"delay": 0, "presence": "composing"}, "text": message}
    try:
        await httpx_client.post(api_url, json=payload, headers=headers)
    except Exception as e: 
        logging.error(f"❌ Erro Envio: {e}")

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


# --- FERRAMENTAS PUBLICARS ---

@tool
def buscar_faq(query: str) -> str:
    try:
        response = supabase.from_('knowledge_base').select('answer').ilike('question', f'%{query}%').limit(1).execute()
        if response.data: return response.data[0]['answer']
        return "Não encontrei essa informação técnica específica no banco, use seu conhecimento geral."
    except: return "Erro ao buscar FAQ."

@tool
def registrar_venda_dashboard(nome: str, empresa: str, plano: str, telefone: str, resumo_conversa: str) -> str:
    try:
        current_time = datetime.now(BR_TIMEZONE).isoformat()
        # Backup
        dados_lead = { 'full_name': nome, 'company_name': empresa, 'service_desired': f"Interesse: {plano}", 'session_id': telefone, 'contact_number': telefone, 'status': 'NOVO_LEAD_ANUNCIANTE', 'updated_at': current_time }
        try: supabase.from_('leads').insert(dados_lead).execute()
        except: pass

        # Painel
        dados_dashboard = { 'nome_responsavel': nome, 'nome_empresa': empresa, 'whatsapp': telefone, 'pacote_escolhido': plano, 'status': 'NOVO', 'observacoes': resumo_conversa }
        try:
            supabase.from_('pedidos_anuncios').insert(dados_dashboard).execute()
            logging.info("✅ Pedido salvo.")
        except Exception as e_dash:
            logging.error(f"⚠️ Aviso Dashboard: {e_dash}")
        return "✅ Show! Registrei o interesse no painel."
    except: return "Erro técnico ao salvar."

@tool
def registrar_lead_motorista(nome: str, telefone: str, cidade_residencia: str, estado: str, modelo_carro: str, placa: str, apps_trabalho: str) -> str:
    try:
        dados_motorista = { 'nome': nome, 'telefone': telefone, 'cidade_residencia': cidade_residencia, 'estado': estado, 'modelo_carro': modelo_carro, 'placa': placa, 'apps_trabalho': apps_trabalho, 'created_at': datetime.now(BR_TIMEZONE).isoformat() }
        try:
            supabase.from_('motoristas').insert(dados_motorista).execute()
            return "✅ Cadastro de motorista realizado com sucesso!"
        except Exception as e_mot:
            logging.error(f"Erro motorista: {e_mot}")
            return "Erro ao salvar na tabela."
    except: return "Erro ao salvar pré-cadastro."

@tool
def calcular_alcance_campanha(plano: str) -> str:
    metricas = { "piloto": {"carros": 1, "views": "1.100"}, "start": {"carros": 3, "views": "3.300"}, "aceleracao": {"carros": 10, "views": "11.000"}, "turbo": {"carros": 20, "views": "22.000"}, "dominador": {"carros": 50, "views": "55.000"} }
    for k, v in metricas.items():
        if k in plano.lower(): return f"📈 O Plano {plano} conta com {v['carros']} carros e {v['views']} views!"
    return "Média de 1.100 views por carro/mês."


# --- SYSTEM PROMPT ---
SYSTEM_PROMPT = """
Você é **{agent_name}**, consultor comercial da **Publicars** 🚗✨.
Você é humano, proativo e usa emojis.

**ESTILO:** Use quebras de linha. Seja direto.

**ARQUIVOS RECEBIDOS:**
Se a mensagem disser "[SISTEMA: Usuário enviou arquivo...]", confirme o recebimento imediatamente!
- Motorista: "Recebi seu documento/foto. Obrigado!"
- Anunciante: "Recebi sua arte/arquivo. Vamos analisar."

**REGRA DE OURO (SILÊNCIO):**
Se a conversa acabou (tchau, valeu, ok): Responda `[SILENCE]`.

**FERRAMENTAS:**
- `registrar_venda_dashboard` (Preencha resumo!).
- `registrar_lead_motorista` (Peça dados antes).
- Link: https://publicars.com.br/seja-parceiro.html

**MEMÓRIA:** Nome: {user_name} | Empresa: {user_company}
Hoje: {current_date}. Telefone: {contact_number}.
"""

tools = [buscar_faq, registrar_venda_dashboard, registrar_lead_motorista, calcular_alcance_campanha]

def create_agent_executor(chat_history, phone, date, persona, profile):
    u_name = profile.get('full_name', 'Não inf.') if profile else 'Não inf.'
    u_comp = profile.get('company_name', 'Não inf.') if profile else 'Não inf.'
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT.format(current_date=date, contact_number=phone, agent_name=persona, user_name=u_name, user_company=u_comp)),
        MessagesPlaceholder("chat_history"), ("user", "{input}"), MessagesPlaceholder("agent_scratchpad")
    ])
    agent = ({ "input": lambda x: x["input"], "agent_scratchpad": lambda x: format_to_openai_tool_messages(x["intermediate_steps"]), "chat_history": lambda x: x["chat_history"] } | prompt | llm.bind_tools(tools) | OpenAIToolsAgentOutputParser())
    return AgentExecutor(agent=agent, tools=tools, verbose=True, handle_parsing_errors=True)

class EvolutionWebhookPayload(BaseModel):
    event: str
    instance: str
    data: dict

# --- WEBHOOK PRINCIPAL (AGORA VÊ TUDO) ---
@app.post("/api/evolution_webhook")
async def evolution_webhook(payload: EvolutionWebhookPayload):
    if payload.event not in ["messages.upsert", "messages.update"]: return Response(content="Ignored")
    
    try:
        data = payload.data
        key = data.get('key', {})
        if key.get('fromMe', False): return Response(content="From Me")
        
        remote_jid = key.get('remoteJid')
        contact_number_plus = "+" + remote_jid.split('@')[0]
        await mark_message_as_read(remote_jid)

    except: return Response(content="Error parsing")

    user_message_text = None
    agent_response_text = "..."
    should_respond = True 
    
    try:
        message_content = data.get('message', {})
        
        # 1. TEXTO
        if not user_message_text: user_message_text = message_content.get("conversation")
        if not user_message_text: user_message_text = message_content.get("extendedTextMessage", {}).get("text")

        # 2. ÁUDIO (Whisper)
        audio_msg = message_content.get("audioMessage")
        if audio_msg:
            logging.info(f"🎧 Áudio de {contact_number_plus}")
            msg_id = key.get('id')
            try:
                dec_url = f"{EVOLUTION_API_URL}/chat/getBase64FromMediaMessage/{EVOLUTION_INSTANCE_NAME}"
                res = await httpx_client.post(dec_url, json={"message":{"key":{"id":msg_id}},"convertToMp4":False}, headers={"apiKey":EVOLUTION_API_KEY})
                if res.status_code == 200:
                    b64 = res.json().get("base64")
                    if b64: user_message_text = await transcribe_audio(base64.b64decode(b64), ".ogg")
            except: pass

        # 3. DOCUMENTOS E IMAGENS (NOVO!!) 📎📸
        media_msg = message_content.get("imageMessage") or message_content.get("documentMessage")
        if media_msg:
            logging.info(f"📎 Arquivo recebido de {contact_number_plus}")
            mime_type = media_msg.get("mimetype")
            msg_id = key.get('id')
            try:
                # Baixa da Evolution
                dec_url = f"{EVOLUTION_API_URL}/chat/getBase64FromMediaMessage/{EVOLUTION_INSTANCE_NAME}"
                res = await httpx_client.post(dec_url, json={"message":{"key":{"id":msg_id}},"convertToMp4":False}, headers={"apiKey":EVOLUTION_API_KEY})
                
                if res.status_code == 200:
                    b64 = res.json().get("base64")
                    if b64:
                        # Salva na VPS
                        file_url = await save_local_file(b64, mime_type, contact_number_plus)
                        if file_url:
                            # INJETA NO CONTEXTO DO BOT
                            caption = media_msg.get("caption", "")
                            filename = media_msg.get("fileName", "arquivo")
                            user_message_text = f"[SISTEMA: Usuário enviou arquivo ({filename}). Link salvo: {file_url}]. {caption}"
                            
            except Exception as e:
                logging.error(f"Erro processando mídia: {e}")

        # Se não achou texto, áudio ou arquivo, ignora
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
                logging.info(f"🤫 Silêncio.")
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
def health_check(): return {"status": "ok", "service": "Publicars AI Agent v3.6 (Files+PDF)"}