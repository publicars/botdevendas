# --- VERSÃO v3.0 - PUBLICARS HUMANIZADO + MEMÓRIA + DASHBOARD INTEGRADO ---
# Baseado na v2.0 estável (Audio+Texto OK)

from fastapi import FastAPI, Request, HTTPException, Response
from pydantic import BaseModel, Field
from dotenv import load_dotenv
import os
import logging
from datetime import datetime
import pytz
import httpx
import random
import hashlib
from typing import Union, Optional
import io 
import base64 
import json

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

HUMAN_SUPPORT_PHONE = "(51) 99300-1678" 
BR_TIMEZONE = pytz.timezone('America/Sao_Paulo')

# --- Inicialização ---
app = FastAPI()
logging.basicConfig(level=logging.INFO)
httpx_client = httpx.AsyncClient(timeout=30.0)

if not all([EVOLUTION_API_URL, EVOLUTION_API_KEY, EVOLUTION_INSTANCE_NAME, OPENAI_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
    logging.critical("⚠️ ERRO CRÍTICO: Variáveis de ambiente faltando!")
else:
    logging.info("✅ Variáveis de ambiente Publicars carregadas.")

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    # Temp=0.6 para dar mais "jogo de cintura" e criatividade nas respostas humanas
    llm = ChatOpenAI(model="gpt-4o", temperature=0.6, api_key=OPENAI_API_KEY) 
    openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
except Exception as e:
    logging.critical(f"💥 Falha ao inicializar clientes de API: {e}")


# --- FUNÇÕES AUXILIARES (NOVO v3.0) ---

# Lista de nomes para a equipe comercial
AGENT_NAMES = [
    "Marcelo", "Jonathan", "Rodrigo", "Maurício", "Amanda", 
    "Fernanda", "Ricardo", "Eduardo", "Camila", "Bruno"
]

def get_persona_name(phone_number: str) -> str:
    """Escolhe um nome fixo para o atendente baseado no número do cliente (Hash)."""
    if not phone_number: return "Atendente Publicars"
    # Transforma o telefone em um número único e usa para escolher o nome na lista
    hash_obj = hashlib.md5(phone_number.encode())
    hash_int = int(hash_obj.hexdigest(), 16)
    return AGENT_NAMES[hash_int % len(AGENT_NAMES)]

def get_user_profile(phone_number: str):
    """🧠 MEMÓRIA: Busca no banco se já conhecemos este cliente (Nome/Empresa)."""
    try:
        # Tenta buscar na tabela 'leads' se já temos cadastro desse número
        response = supabase.from_('leads').select('full_name, company_name, service_desired').eq('session_id', phone_number).limit(1).execute()
        if response.data:
            return response.data[0] # Retorna o objeto com {full_name, company_name}
        return None
    except Exception as e:
        logging.error(f"Erro ao buscar memória do usuário: {e}")
        return None


# --- Função de Envio de Mensagem (Evolution API) ---
async def send_whatsapp_message(to_number_jid: str, message: str):
    if "@s.whatsapp.net" not in to_number_jid: to_number_jid = f"{to_number_jid}@s.whatsapp.net"
    api_url = f"{EVOLUTION_API_URL}/message/sendText/{EVOLUTION_INSTANCE_NAME}"
    headers = {"apiKey": EVOLUTION_API_KEY, "ngrok-skip-browser-warning": "true"}
    payload = {"number": to_number_jid, "options": {"delay": 1200, "presence": "composing"}, "text": message}
    try:
        logging.info(f"📤 Enviando resposta para: {to_number_jid}")
        await httpx_client.post(api_url, json=payload, headers=headers)
    except Exception as e: 
        logging.error(f"❌ Erro ao ENVIAR via Evolution: {e}")

# --- Função de Transcrição de Áudio (Whisper) ---
async def transcribe_audio(audio_bytes: bytes, file_extension: str) -> str:
    try:
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = f"audio{file_extension}" 
        response = await openai_client.audio.transcriptions.create(
            model="whisper-1", file=audio_file, language="pt"
        )
        return response.text
    except Exception as e:
        logging.error(f"❌ Erro ao transcrever áudio: {e}")
        return "[ERRO DE TRANSCRIÇÃO]"


# --- FERRAMENTAS PUBLICARS (Sales Tools v3.0) ---

@tool
def buscar_faq(query: str) -> str:
    """❓ Busca respostas técnicas específicas no banco de dados (ex: dimensões do tablet, especificações de vídeo, detalhes de contrato)."""
    try:
        response = supabase.from_('knowledge_base').select('answer').ilike('question', f'%{query}%').limit(1).execute()
        if response.data:
            return response.data[0]['answer']
        return "Não encontrei essa informação técnica específica no banco, use seu conhecimento geral sobre a Publicars."
    except Exception as e:
        return "Erro ao buscar FAQ."

@tool
def registrar_venda_dashboard(nome: str, empresa: str, plano: str, telefone: str) -> str:
    """📝 REGISTRA INTERESSE/VENDA. Use quando o cliente demonstra interesse claro em um plano.
    Salva diretamente no painel administrativo da Publicars (pedidos_anuncios)."""
    try:
        current_time = datetime.now(BR_TIMEZONE).isoformat()
        
        # 1. Salva na tabela LEADS (Backup e Memória do Bot)
        dados_lead = {
            'full_name': nome,
            'company_name': empresa,
            'service_desired': f"Interesse: {plano}",
            'session_id': telefone,
            'contact_number': telefone,
            'status': 'NOVO_LEAD_ANUNCIANTE',
            'updated_at': current_time
        }
        supabase.from_('leads').insert(dados_lead).execute()

        # 2. Tenta salvar na tabela PEDIDOS_ANUNCIOS (Integração com Dashboard)
        dados_dashboard = {
            'responsavel': nome,         # Nome do cliente vai na coluna "responsavel"
            'empresa': empresa,          # Coluna "empresa"
            'pacote': plano,             # Coluna "pacote"
            'status': 'NOVO',            # Status padrão
            'telefone': telefone,        # Telefone para contato
            'data_criacao': current_time
        }
        
        try:
            supabase.from_('pedidos_anuncios').insert(dados_dashboard).execute()
            logging.info("✅ Pedido inserido na tabela pedidos_anuncios com sucesso.")
        except Exception as e_dash:
            logging.error(f"⚠️ Aviso: Não consegui gravar na tabela do dashboard (pedidos_anuncios): {e_dash}")

        return "✅ Show! Registrei o interesse no painel. Avise que a equipe vai chamar."
    except Exception as e:
        logging.error(f"Erro critico ao salvar lead: {e}")
        return "Erro ao salvar, mas continue o atendimento."

@tool
def registrar_lead_motorista(nome: str, modelo_carro: str, cidade: str, telefone: str) -> str:
    """🚗 Registra um LEAD DE MOTORISTA (Parceiro) interessado em instalar o tablet.
    'nome': Nome do motorista. 'modelo_carro': Carro e Ano. 'cidade': Cidade onde roda. 'telefone': WhatsApp."""
    try:
        dados = {
            'full_name': nome,
            'service_desired': f"Motorista Parc: {modelo_carro} - {cidade}",
            'session_id': telefone,
            'contact_number': telefone,
            'status': 'NOVO_LEAD_MOTORISTA',
            'updated_at': datetime.now(BR_TIMEZONE).isoformat()
        }
        supabase.from_('leads').insert(dados).execute()
        return "✅ Pré-cadastro de Motorista realizado! Informe que entraremos em contato quando houver instalação na cidade dele."
    except Exception as e:
        logging.error(f"Erro ao salvar lead motorista: {e}")
        return "Erro ao salvar pré-cadastro."

@tool
def calcular_alcance_campanha(plano: str) -> str:
    """📊 Calcula estimativa de alcance baseado no plano escolhido (Piloto, Start, Aceleração, Turbo, Dominador)."""
    metricas = {
        "piloto": {"carros": 1, "views": "1.100"},
        "start": {"carros": 3, "views": "3.300"},
        "aceleracao": {"carros": 10, "views": "11.000"},
        "turbo": {"carros": 20, "views": "22.000"},
        "dominador": {"carros": 50, "views": "55.000"}
    }
    # Tenta achar o plano na string
    dados = None
    for k, v in metricas.items():
        if k in plano.lower():
            dados = v
            break
    
    if dados:
        return f"📈 O Plano {plano} conta com {dados['carros']} carros e gera aproximadamente {dados['views']} visualizações de impacto mensalmente com o cliente a menos de 50cm da tela!"
    return "Para este plano personalizado, o alcance depende da quantidade de carros. A média é de 1.100 views por carro/mês."


# --- SYSTEM PROMPT HUMANIZADO (O CÉREBRO DA IA v3.0) ---
SYSTEM_PROMPT = """
Você é **{agent_name}**, consultor comercial da **Publicars** 🚗✨.
Você NÃO é um robô chato. Você é um vendedor humano, proativo, simpático e usa emojis para dar vida à conversa.

**SEU ESTILO DE ESCRITA (MUITO IMPORTANTE):**
1.  **Use quebras de linha:** Nunca mande blocos de texto gigantes. Pule linhas para facilitar a leitura no WhatsApp.
2.  **Seja direto:** Responda o que foi perguntado, depois venda.
3.  **Humanize:** Use expressões como "Show de bola", "Com certeza", "Olha só", "Deixa eu te explicar".
4.  **Memória:** Se você já sabe o nome ou a empresa do cliente (pelos dados de contexto abaixo), **NÃO PERGUNTE DE NOVO**. Apenas confirme: "Como vai a [Nome da Empresa], [Nome]?"

**DADOS DO CLIENTE (MEMÓRIA):**
Nome Conhecido: {user_name}
Empresa Conhecida: {user_company}
(Se estes dados estiverem como 'Não informado', você deve descobri-los sutilmente durante a conversa para fechar a venda).

**TABELA DE PREÇOS (Seu guia de vendas):**
💰 **Plano PILOTO:** R$ 89,90/mês (1 Carro). Ideal para testar. (~1.100 views).
💰 **Plano START:** R$ 189,00/mês (3 Carros). Validação para pequenos negócios. (~3.300 views).
💰 **Plano ACELERAÇÃO:** R$ 399,00/mês (10 Carros). **Melhor Custo-Benefício!** (~11.000 views).
💰 **Plano TURBO:** R$ 599,00/mês (20 Carros). Domínio de bairro. (~22.000 views).
💰 **Plano DOMINADOR:** R$ 999,00/mês (50 Carros). Domínio da cidade. (~55.000 views).

**SEUS FLUXOS DE CONVERSA:**

🟦 **FLUXO 1: CLIENTE QUER ANUNCIAR**
1. Explique a vantagem (atenção garantida no Uber).
2. Se não souber o nome/empresa, pergunte. Se já souber, pule esta etapa.
3. Apresente os planos.
4. Se houver interesse, use a ferramenta `registrar_venda_dashboard` IMEDIATAMENTE.

🟩 **FLUXO 2: MOTORISTA PARCEIRO**
1. Explique a renda extra.
2. Pegue os dados (Carro, Cidade).
3. Use `registrar_lead_motorista`.

🟥 **FLUXO 3: SUPORTE**
1. Tente ajudar.
2. Se não der, mande ligar para {human_phone}.

**REGRAS DE OURO:**
- Se o usuário mandar ÁUDIO, você entende perfeitamente. Responda em texto.
- O número do cliente é {contact_number}.

Hoje é {current_date}.
"""

# === Lista de ferramentas ===
tools = [
    buscar_faq,
    registrar_venda_dashboard, # Trocamos a antiga por esta nova integrada
    registrar_lead_motorista,
    calcular_alcance_campanha
]

def create_agent_executor(chat_history_messages, contact_number, current_date, persona_name, user_profile):
    # Prepara dados da memória para injetar no prompt
    user_name = user_profile.get('full_name', 'Não informado') if user_profile else 'Não informado'
    user_company = user_profile.get('company_name', 'Não informado') if user_profile else 'Não informado'

    formatted_prompt = SYSTEM_PROMPT.format(
        current_date=current_date, 
        contact_number=contact_number, 
        human_phone=HUMAN_SUPPORT_PHONE,
        agent_name=persona_name,
        user_name=user_name,
        user_company=user_company
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

# === Modelos Pydantic para Webhook (IDÊNTICOS AO V2.0) ===
class EvolutionMessageKey(BaseModel):
    remoteJid: str
    fromMe: bool
    id: str

class EvolutionMessageData(BaseModel):
    key: EvolutionMessageKey
    message: Union[dict, None] = None 

class EvolutionMessageDataWithContent(EvolutionMessageData):
    message: Union[dict, None] = None

class EvolutionWebhookPayload(BaseModel):
    event: str
    instance: str
    data: EvolutionMessageDataWithContent

# --- WEBHOOK PRINCIPAL (Mantendo a estrutura segura v2.0) ---
@app.post("/api/evolution_webhook")
async def evolution_webhook(payload: EvolutionWebhookPayload):
    
    if payload.event not in ["messages.upsert", "messages.update"] or payload.data.key.fromMe:
        return Response(status_code=200, content="Event ignored")

    session_id_jid = payload.data.key.remoteJid
    contact_number_plus = "+" + session_id_jid.split('@')[0]
    
    user_message_text = None
    agent_response_text = "Desculpe, tive um lapso de memória. Pode repetir? 🤖"
    should_respond = True 
    parsing_error = False 

    try:
        mimetype_map = {
            "audio/ogg": ".ogg", "audio/aac": ".aac", "audio/mp4": ".m4a",
            "audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/webm": ".webm",
        }
        
        if payload.data.message:
            # 1. PROCESSAMENTO DE ÁUDIO (IDÊNTICO AO V2.0 - FUNCIONANDO)
            audio_message_data = payload.data.message.get("audioMessage")
            if audio_message_data:
                logging.info(f"🎧 Áudio recebido de {contact_number_plus}.")
                
                mimetype = audio_message_data.get("mimetype", "").split(';')[0]
                file_extension = mimetype_map.get(mimetype)
                message_id = payload.data.key.id 

                if message_id:
                    try:
                        decrypt_url = f"{EVOLUTION_API_URL}/chat/getBase64FromMediaMessage/{EVOLUTION_INSTANCE_NAME}"
                        decrypt_headers = {"apiKey": EVOLUTION_API_KEY}
                        decrypt_payload = { "message": { "key": { "id": message_id } }, "convertToMp4": False }
                        
                        response = await httpx_client.post(decrypt_url, json=decrypt_payload, headers=decrypt_headers)
                        response.raise_for_status()
                        
                        base64_data = response.json().get("base64")
                        if base64_data:
                            audio_bytes = base64.b64decode(base64_data)
                            user_message_text = await transcribe_audio(audio_bytes, file_extension)
                            if "[ERRO" in user_message_text: parsing_error = True
                        else: parsing_error = True
                    except Exception as e:
                        logging.error(f"Erro processando áudio: {e}")
                        parsing_error = True
            
            # 2. PROCESSAMENTO DE TEXTO
            else:
                user_message_text = payload.data.message.get("conversation")
                if not user_message_text: 
                    user_message_text = payload.data.message.get("extendedTextMessage", {}).get("text")
        
        if not user_message_text: should_respond = False 
        
        # 3. EXECUÇÃO DO AGENTE (AQUI MUDA PARA v3.0)
        if should_respond and not parsing_error:
            logging.info(f"📩 Cliente ({contact_number_plus}): {user_message_text}")

            # --- NOVO: Define Persona e Busca Memória ---
            persona_name = get_persona_name(contact_number_plus)
            user_profile = get_user_profile(contact_number_plus)
            if user_profile:
                logging.info(f"🧠 Memória ativada: {user_profile['full_name']} da {user_profile['company_name']}")

            # Histórico
            chat_history_messages = []
            try:
                history_response = supabase.from_('conversations').select('*').eq('session_id', contact_number_plus).order('timestamp', desc=True).limit(6).execute()
                if history_response.data:
                    for msg in reversed(history_response.data):
                        chat_history_messages.append(HumanMessage(content=msg['user_message']))
                        chat_history_messages.append(AIMessage(content=msg['agent_response']))
            except Exception as e:
                logging.error(f"Erro ao buscar histórico: {e}")

            current_date = datetime.now(BR_TIMEZONE).strftime('%Y-%m-%d')
            
            # Cria o agente com as novas variáveis
            agent_executor = create_agent_executor(chat_history_messages, contact_number_plus, current_date, persona_name, user_profile)
            
            response = await agent_executor.ainvoke({
                "input": user_message_text,
                "chat_history": chat_history_messages
            })
            agent_response_text = response["output"]
            logging.info(f"🤖 {persona_name}: {agent_response_text[:50]}...")

    except Exception as e:
        logging.error(f"💥 Erro no Webhook: {e}", exc_info=True)
        agent_response_text = "Desculpe, estou atualizando meus sistemas. Tente novamente em 1 minuto! 🛠️"

    finally:
        try:
            if user_message_text: 
                # Salva na tabela conversations (correto)
                supabase.from_('conversations').insert({'session_id': contact_number_plus, 'user_message': user_message_text, 'agent_response': agent_response_text}).execute()
        except: pass

        if should_respond:
            await send_whatsapp_message(session_id_jid, agent_response_text)
    
    return {"status": "ok"} 

@app.get("/api/health")
def health_check():
    return {"status": "ok", "service": "Publicars AI Agent v3.0 (Human + Memory)"}