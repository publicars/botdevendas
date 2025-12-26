# --- VERSÃO v2.0 - PUBLICARS SALES AGENT (Sem Agendamento | Foco em Vendas) ---

from fastapi import FastAPI, Request, HTTPException, Response
from pydantic import BaseModel, Field
from dotenv import load_dotenv
import os
import logging
from datetime import datetime
import pytz
import httpx
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
from langchain_core.messages import HumanMessage, AIMessage
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

# Telefone Comercial para transbordo (Falar com Humano)
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
    # Modelo GPT-4o para máxima persuasão e inteligência
    llm = ChatOpenAI(model="gpt-4o", temperature=0.2, api_key=OPENAI_API_KEY) 
    openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
except Exception as e:
    logging.critical(f"💥 Falha ao inicializar clientes de API: {e}")

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

# --- FERRAMENTAS PUBLICARS (Sales Tools) ---

@tool
def buscar_faq(query: str) -> str:
    """❓ Busca respostas técnicas específicas no banco de dados (ex: dimensões do tablet, especificações de vídeo, detalhes de contrato)."""
    try:
        # Assume que existe uma tabela 'knowledge_base' no Supabase
        response = supabase.from_('knowledge_base').select('answer').ilike('question', f'%{query}%').limit(1).execute()
        if response.data:
            return response.data[0]['answer']
        return "Não encontrei essa informação técnica específica no banco, use seu conhecimento geral sobre a Publicars."
    except Exception as e:
        return "Erro ao buscar FAQ."

@tool
def registrar_lead_anunciante(nome: str, empresa: str, interesse_plano: str, telefone: str) -> str:
    """📝 Registra um LEAD DE ANUNCIANTE interessado em comprar mídia.
    'nome': Nome do contato. 'empresa': Nome da empresa. 'interesse_plano': Qual plano (Piloto, Start, etc) ele gostou. 'telefone': O número do WhatsApp."""
    try:
        dados = {
            'full_name': nome,
            'company_name': empresa, # Certifique-se que sua tabela 'leads' tem essa coluna ou adapte
            'service_desired': f"Interesse Anúncio: {interesse_plano}",
            'session_id': telefone,
            'contact_number': telefone,
            'status': 'NOVO_LEAD_ANUNCIANTE',
            'updated_at': datetime.now(BR_TIMEZONE).isoformat()
        }
        # Usando a tabela 'leads' existente, adaptando os campos
        supabase.from_('leads').insert(dados).execute()
        return "✅ Lead de Anunciante registrado com sucesso! Informe ao cliente que um especialista entrará em contato para fechar o contrato."
    except Exception as e:
        logging.error(f"Erro ao salvar lead anunciante: {e}")
        return "Erro ao salvar seus dados, mas anotei aqui manualmente."

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
    # Dados baseados no site da Publicars
    metricas = {
        "piloto": {"carros": 1, "views": "1.100"},
        "start": {"carros": 3, "views": "3.300"},
        "aceleracao": {"carros": 10, "views": "11.000"},
        "turbo": {"carros": 20, "views": "22.000"},
        "dominador": {"carros": 50, "views": "55.000"}
    }
    key = plano.lower().split()[0] # Pega a primeira palavra (ex: "plano")
    if key in ["plano"]: key = plano.lower().split()[1] # Tenta pegar a segunda palavra se a primeira for plano
    
    # Busca aproximada
    dados = None
    for k, v in metricas.items():
        if k in plano.lower():
            dados = v
            break
    
    if dados:
        return f"📈 O Plano {plano} conta com {dados['carros']} carros e gera aproximadamente {dados['views']} visualizações de impacto mensalmente com o cliente a menos de 50cm da tela!"
    return "Para este plano personalizado, o alcance depende da quantidade de carros. A média é de 1.100 views por carro/mês."

# --- SYSTEM PROMPT (O CÉREBRO DA IA) ---
SYSTEM_PROMPT = """
Você é o **Assistente Virtual da Publicars**, a maior plataforma de DOOH (Mídia Digital Out-of-Home) em carros de aplicativo do Rio Grande do Sul 🚀.
Seu objetivo é **VENDER** planos de publicidade e captar motoristas parceiros. Você é simpático, profissional, usa emojis na medida certa e tem alto poder de persuasão.

**INFORMAÇÕES CHAVE DA EMPRESA (Use isso para vender!):**
1.  **O Produto:** Tablets de alta definição instalados no encosto de cabeça de Ubers e 99s.
2.  **O Diferencial:** O passageiro está "preso" na viagem, a menos de 50cm da tela. Atenção garantida! Mídia geolocalizada (anuncie só no bairro que quiser).
3.  **Região de Atuação:** Porto Alegre, Canoas, Novo Hamburgo, São Leopoldo, Gravataí, Esteio, Sapucaia, Campo Bom, Cachoeirinha, Alvorada, Viamão, Eldorado, Guaíba.
4.  **Métricas:** Média de 30 a 45 pessoas impactadas por dia/carro.

**TABELA DE PREÇOS (PLANOS MENSAIS):**
💰 **Plano PILOTO:** R$ 89,90/mês (1 Carro). Ideal para testar. (~1.100 views).
💰 **Plano START:** R$ 189,00/mês (3 Carros). Validação para pequenos negócios. (~3.300 views).
💰 **Plano ACELERAÇÃO:** R$ 399,00/mês (10 Carros). **Melhor Custo-Benefício!** (~11.000 views).
💰 **Plano TURBO:** R$ 599,00/mês (20 Carros). Domínio de bairro. (~22.000 views).
💰 **Plano DOMINADOR:** R$ 999,00/mês (50 Carros). Domínio da cidade. (~55.000 views).

**SEUS FLUXOS DE CONVERSA:**

🟦 **FLUXO 1: CLIENTE QUER ANUNCIAR (Foco total em fechar negócio)**
1.  Explique brevemente a vantagem (ex: "Imagine sua marca aparecendo para o passageiro durante toda a viagem!").
2.  Pergunte o nome e a empresa.
3.  Apresente os planos (Destaque o 'Aceleração' como favorito).
4.  Se o cliente mostrar interesse em um plano, use a ferramenta `registrar_lead_anunciante`.
5.  Finalize dizendo que o comercial vai chamar no WhatsApp para pegar a arte/vídeo.

🟩 **FLUXO 2: MOTORISTA QUER SER PARCEIRO**
1.  Explique que ele ganha uma renda extra apenas por ter o tablet ligado enquanto trabalha.
2.  Pergunte: Nome, Modelo/Ano do Carro e Cidade onde roda.
3.  Use a ferramenta `registrar_lead_motorista`.
4.  Avise que entraremos em contato assim que houver disponibilidade de tablets para a região dele.

🟥 **FLUXO 3: DÚVIDAS GERAIS / SUPORTE**
1.  Responda com base no seu conhecimento.
2.  Se for algo muito complexo ou reclamação, instrua a ligar para o suporte humano: {human_phone}.

**REGRAS DE OURO:**
- Se o usuário mandar ÁUDIO, você entende perfeitamente (graças ao Whisper). Responda em texto de forma natural.
- **NÃO INVENTE** dados que não estão aqui.
- Se perguntarem sobre contrato: "Nossos planos são mensais, sem fidelidade amarrada! Liberdade total."
- O número do cliente é {contact_number}.

Hoje é {current_date}.
"""

# === Lista de ferramentas ===
tools = [
    buscar_faq,
    registrar_lead_anunciante,
    registrar_lead_motorista,
    calcular_alcance_campanha
]

def create_agent_executor(chat_history_messages, contact_number, current_date):
    formatted_prompt = SYSTEM_PROMPT.format(
        current_date=current_date, 
        contact_number=contact_number, 
        human_phone=HUMAN_SUPPORT_PHONE
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

# === Modelos Pydantic para Webhook ===
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

# --- WEBHOOK PRINCIPAL (Mantendo a correção de áudio v1.45) ---
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
            # 1. PROCESSAMENTO DE ÁUDIO (Lógica v1.45 mantida)
            audio_message_data = payload.data.message.get("audioMessage")
            if audio_message_data:
                logging.info(f"🎧 Áudio recebido de {contact_number_plus}.")
                # await send_whatsapp_message(session_id_jid, "Ouvindo seu áudio... 🎙️") # Opcional: Feedback imediato
                
                mimetype = audio_message_data.get("mimetype", "").split(';')[0]
                file_extension = mimetype_map.get(mimetype)
                message_id = payload.data.key.id 

                if message_id:
                    try:
                        decrypt_url = f"{EVOLUTION_API_URL}/chat/getBase64FromMediaMessage/{EVOLUTION_INSTANCE_NAME}"
                        decrypt_headers = {"apiKey": EVOLUTION_API_KEY}
                        decrypt_payload = {
                            "message": { "key": { "id": message_id } },
                            "convertToMp4": False 
                        }
                        
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
        
        # 3. EXECUÇÃO DO AGENTE
        if should_respond and not parsing_error:
            logging.info(f"📩 Cliente ({contact_number_plus}): {user_message_text}")

            # Histórico
            chat_history_messages = []
            history_response = supabase.from_('conversations').select('*').eq('session_id', contact_number_plus).order('timestamp', desc=True).limit(6).execute()
            if history_response.data:
                for msg in reversed(history_response.data):
                    chat_history_messages.append(HumanMessage(content=msg['user_message']))
                    chat_history_messages.append(AIMessage(content=msg['agent_response']))

            current_date = datetime.now(BR_TIMEZONE).strftime('%Y-%m-%d')
            agent_executor = create_agent_executor(chat_history_messages, contact_number_plus, current_date)
            
            response = await agent_executor.ainvoke({
                "input": user_message_text,
                "chat_history": chat_history_messages
            })
            agent_response_text = response["output"]
            logging.info(f"🤖 Publicars Bot: {agent_response_text[:50]}...")

    except Exception as e:
        logging.error(f"💥 Erro no Webhook: {e}", exc_info=True)
        agent_response_text = "Desculpe, estou atualizando meus sistemas. Tente novamente em 1 minuto! 🛠️"

    finally:
        try:
            if user_message_text: 
                supabase.from_('conversations').insert({'session_id': contact_number_plus, 'user_message': user_message_text, 'agent_response': agent_response_text}).execute()
        except: pass

        if should_respond:
            await send_whatsapp_message(session_id_jid, agent_response_text)
    
    return {"status": "ok"} 

@app.get("/api/health")
def health_check():
    return {"status": "ok", "service": "Publicars AI Sales Agent v2.0"}