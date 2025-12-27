# --- VERSÃO v3.7 - PUBLICARS: CORREÇÃO CRÍTICA (DOCSTRINGS) + ARQUIVOS ---

from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.staticfiles import StaticFiles 
from pydantic import BaseModel
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
import uuid 

# Supabase Client
from supabase import create_client, Client

# LangChain components
from langchain_openai import ChatOpenAI
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.tools import tool
from langchain.agents.format_scratchpad.openai_tools import format_to_openai_tool_messages
from langchain.agents.output_parsers.openai_tools import OpenAIToolsAgentOutputParser

# OpenAI API
from openai import AsyncOpenAI

load_dotenv()

# --- Configurações ---
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY")
EVOLUTION_INSTANCE_NAME = os.getenv("EVOLUTION_INSTANCE_NAME")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
APP_BASE_URL = os.getenv("APP_BASE_URL", "https://bot.publicars.com.br")

HUMAN_SUPPORT_PHONE = "(51) 99300-1678" 
BR_TIMEZONE = pytz.timezone('America/Sao_Paulo')
UPLOAD_DIR = "uploads"

app = FastAPI()
logging.basicConfig(level=logging.INFO)
httpx_client = httpx.AsyncClient(timeout=60.0)

# Configura pasta de uploads
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

if not all([EVOLUTION_API_URL, EVOLUTION_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
    logging.critical("⚠️ ERRO: Faltam variáveis de ambiente.")

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    llm = ChatOpenAI(model="gpt-4o", temperature=0.6, api_key=OPENAI_API_KEY) 
    openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
except Exception as e:
    logging.critical(f"💥 Falha init: {e}")

# --- FUNÇÕES AUXILIARES ---

AGENT_NAMES = ["Marcelo", "Jonathan", "Rodrigo", "Maurício", "Amanda", "Fernanda", "Ricardo", "Eduardo", "Camila", "Bruno"]

def get_persona_name(phone: str) -> str:
    if not phone: return "Atendente Publicars"
    hash_obj = hashlib.md5(phone.encode())
    return AGENT_NAMES[int(hash_obj.hexdigest(), 16) % len(AGENT_NAMES)]

def get_user_profile(phone: str):
    try:
        res = supabase.from_('leads').select('full_name, company_name').eq('session_id', phone).limit(1).execute()
        return res.data[0] if res.data else None
    except: return None

async def save_local_file(base64_data: str, mime_type: str, phone: str) -> str:
    """Salva arquivo na VPS e retorna URL."""
    try:
        file_data = base64.b64decode(base64_data)
        ext = "bin"
        if "pdf" in mime_type: ext = "pdf"
        elif "image" in mime_type: ext = "jpg" if "jpeg" in mime_type else "png"
        
        filename = f"{phone}_{int(datetime.now().timestamp())}_{str(uuid.uuid4())[:4]}.{ext}"
        filepath = os.path.join(UPLOAD_DIR, filename)
        
        with open(filepath, "wb") as f:
            f.write(file_data)
        return f"{APP_BASE_URL}/uploads/{filename}"
    except Exception as e:
        logging.error(f"Erro salvar arquivo: {e}")
        return None

# --- FERRAMENTAS (COM DOCSTRINGS RESTAURADAS) ---

@tool
def buscar_faq(query: str) -> str:
    """Busca respostas técnicas sobre a Publicars no banco de dados de conhecimento."""
    try:
        res = supabase.from_('knowledge_base').select('answer').ilike('question', f'%{query}%').limit(1).execute()
        return res.data[0]['answer'] if res.data else "Não encontrei, use conhecimento geral."
    except: return "Erro ao buscar FAQ."

@tool
def registrar_venda_dashboard(nome: str, empresa: str, plano: str, telefone: str, resumo_conversa: str) -> str:
    """Registra um LEAD DE VENDAS no painel administrativo.
    Use quando o cliente demonstrar interesse claro em anunciar.
    'resumo_conversa': Breve descrição do negócio e necessidade do cliente."""
    try:
        now = datetime.now(BR_TIMEZONE).isoformat()
        # Backup
        try:
            supabase.from_('leads').insert({
                'full_name': nome, 'company_name': empresa, 'service_desired': f"Interesse: {plano}",
                'session_id': telefone, 'contact_number': telefone, 'status': 'NOVO_LEAD_ANUNCIANTE', 'updated_at': now
            }).execute()
        except: pass

        # Painel
        supabase.from_('pedidos_anuncios').insert({
            'nome_responsavel': nome, 'nome_empresa': empresa, 'whatsapp': telefone,
            'pacote_escolhido': plano, 'status': 'NOVO', 'observacoes': resumo_conversa
        }).execute()
        return "✅ Interesse registrado no painel com sucesso."
    except: return "Erro técnico ao salvar, mas continue o atendimento."

@tool
def registrar_lead_motorista(nome: str, telefone: str, cidade_residencia: str, estado: str, modelo_carro: str, placa: str, apps_trabalho: str) -> str:
    """Registra um MOTORISTA PARCEIRO interessado.
    Colete todos os dados (Nome, Cidade, Carro, Placa, Apps) antes de chamar."""
    try:
        supabase.from_('motoristas').insert({
            'nome': nome, 'telefone': telefone, 'cidade_residencia': cidade_residencia,
            'estado': estado, 'modelo_carro': modelo_carro, 'placa': placa,
            'apps_trabalho': apps_trabalho, 'created_at': datetime.now(BR_TIMEZONE).isoformat()
        }).execute()
        return "✅ Motorista cadastrado com sucesso!"
    except: return "Erro ao salvar cadastro."

@tool
def calcular_alcance_campanha(plano: str) -> str:
    """Calcula a estimativa de visualizações mensais baseado no nome do plano."""
    metricas = {"piloto": "1.100", "start": "3.300", "aceleracao": "11.000", "turbo": "22.000", "dominador": "55.000"}
    for k, v in metricas.items():
        if k in plano.lower(): return f"O Plano {plano} gera aprox. {v} visualizações/mês."
    return "Média de 1.100 views/carro."

# --- AGENTE ---

SYSTEM_PROMPT = """
Você é **{agent_name}**, da **Publicars** 🚗✨.
Vendedor humano, proativo e usa emojis.

**ARQUIVOS:**
Se o usuário mandar foto ou PDF, o sistema te avisará "Usuário enviou arquivo: [LINK]".
Confirme o recebimento ("Recebi sua CNH/Arte, obrigado!").

**REGRA DE OURO:**
Se a conversa acabou (tchau, valeu, ok): Responda `[SILENCE]`.

**FERRAMENTAS:**
- `registrar_venda_dashboard` (Preencha o resumo!).
- `registrar_lead_motorista` (Peça os dados antes).
- Link parceiro: https://publicars.com.br/seja-parceiro.html

Hoje: {current_date}.
"""

tools = [buscar_faq, registrar_venda_dashboard, registrar_lead_motorista, calcular_alcance_campanha]

def create_agent(chat_history, phone, date, persona, profile):
    u_name = profile.get('full_name', 'Não inf.') if profile else 'Não inf.'
    u_comp = profile.get('company_name', 'Não inf.') if profile else 'Não inf.'
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT.format(current_date=date, contact_number=phone, agent_name=persona, user_name=u_name, user_company=u_comp)),
        MessagesPlaceholder("chat_history"), ("user", "{input}"), MessagesPlaceholder("agent_scratchpad")
    ])
    agent = ({ "input": lambda x: x["input"], "agent_scratchpad": lambda x: format_to_openai_tool_messages(x["intermediate_steps"]), "chat_history": lambda x: x["chat_history"]} | prompt | llm.bind_tools(tools) | OpenAIToolsAgentOutputParser())
    return AgentExecutor(agent=agent, tools=tools, verbose=True, handle_parsing_errors=True)

class EvolutionWebhookPayload(BaseModel):
    event: str
    instance: str
    data: dict

# --- WEBHOOK ---
@app.post("/api/evolution_webhook")
async def evolution_webhook(payload: EvolutionWebhookPayload):
    if payload.event not in ["messages.upsert", "messages.update"]: return Response("Ignored")
    
    try:
        data = payload.data
        key = data.get('key', {})
        if key.get('fromMe', False): return Response("From Me")
        
        remote_jid = key.get('remoteJid')
        phone = "+" + remote_jid.split('@')[0]
        
        # Marca Lida
        try:
            await httpx_client.post(f"{EVOLUTION_API_URL}/chat/markMessageAsRead/{EVOLUTION_INSTANCE_NAME}", headers={"apiKey": EVOLUTION_API_KEY}, json={"readMessages": [{"remoteJid": remote_jid}]})
        except: pass

        msg_content = data.get('message', {})
        user_text = None
        
        # 1. Texto
        if not user_text: user_text = msg_content.get("conversation")
        if not user_text: user_text = msg_content.get("extendedTextMessage", {}).get("text")

        # 2. Áudio
        audio = msg_content.get("audioMessage")
        if audio:
            msg_id = key.get('id')
            try:
                dec_url = f"{EVOLUTION_API_URL}/chat/getBase64FromMediaMessage/{EVOLUTION_INSTANCE_NAME}"
                res = await httpx_client.post(dec_url, json={"message":{"key":{"id":msg_id}},"convertToMp4":False}, headers={"apiKey":EVOLUTION_API_KEY})
                if res.status_code == 200:
                    b64 = res.json().get("base64")
                    user_text = await transcribe_audio(base64.b64decode(b64), ".ogg")
            except: pass

        # 3. Mídia (FOTO/PDF)
        media_msg = msg_content.get("imageMessage") or msg_content.get("documentMessage")
        if media_msg:
            mime = media_msg.get("mimetype")
            msg_id = key.get('id')
            try:
                dec_url = f"{EVOLUTION_API_URL}/chat/getBase64FromMediaMessage/{EVOLUTION_INSTANCE_NAME}"
                res = await httpx_client.post(dec_url, json={"message":{"key":{"id":msg_id}},"convertToMp4":False}, headers={"apiKey":EVOLUTION_API_KEY})
                if res.status_code == 200:
                    b64 = res.json().get("base64")
                    if b64:
                        file_url = await save_local_file(b64, mime, phone)
                        if file_url:
                            caption = media_msg.get("caption", "")
                            filename = media_msg.get("fileName", "arquivo")
                            user_text = f"[SISTEMA: Usuário enviou arquivo ({filename}). Link salvo: {file_url}]. {caption}"
            except Exception as e:
                logging.error(f"Erro midia: {e}")

        if not user_text: return Response("No content")

        # Agente
        persona = get_persona_name(phone)
        profile = get_user_profile(phone)
        
        history = []
        try:
            h = supabase.from_('conversations').select('*').eq('session_id', phone).order('timestamp', desc=True).limit(6).execute()
            if h.data:
                for m in reversed(h.data):
                    history.append(HumanMessage(m['user_message']))
                    history.append(AIMessage(m['agent_response']))
        except: pass

        executor = create_agent(history, phone, datetime.now(BR_TIMEZONE).strftime('%Y-%m-%d'), persona, profile)
        resp = await executor.ainvoke({"input": user_text, "chat_history": history})
        response_text = resp["output"]

        if "[SILENCE]" not in response_text:
            supabase.from_('conversations').insert({'session_id': phone, 'user_message': user_text, 'agent_response': response_text}).execute()
            wait = random.randint(4, 8)
            await asyncio.sleep(wait)
            
            await httpx_client.post(
                f"{EVOLUTION_API_URL}/message/sendText/{EVOLUTION_INSTANCE_NAME}",
                headers={"apiKey": EVOLUTION_API_KEY},
                json={"number": remote_jid, "options": {"delay": 0, "presence": "composing"}, "text": response_text}
            )

    except Exception as e:
        logging.error(f"Geral: {e}")
    
    return Response("OK")

async def transcribe_audio(audio_bytes, ext):
    try:
        f = io.BytesIO(audio_bytes)
        f.name = f"audio{ext}"
        r = await openai_client.audio.transcriptions.create(model="whisper-1", file=f, language="pt")
        return r.text
    except: return "[Erro Transcrição]"

@app.get("/api/health")
def health(): return {"status": "ok", "service": "Publicars AI Agent v3.7 (Fix Docs)"}