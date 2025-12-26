# Usa Python leve
FROM python:3.9-slim

# Define pasta de trabalho
WORKDIR /app

# Instala git e utilitários básicos (caso precise)
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

# Copia requisitos e instala
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código do bot
COPY . .

# Expõe a porta 8000 (padrão FastAPI)
EXPOSE 8000

# Comando para iniciar o FastAPI com Uvicorn
CMD ["uvicorn", "publicars_bot:app", "--host", "0.0.0.0", "--port", "8000"]