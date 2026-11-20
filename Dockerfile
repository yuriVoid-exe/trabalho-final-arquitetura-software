FROM python:3.11-slim

# Copia o binário oficial do uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Otimizações do uv dentro de containers
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app" \
    PATH="/app/.venv/bin:$PATH"

# Instala dependências primeiro (aproveita cache do Docker)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Copia o código-fonte do SGEA e cria diretório para volume de dados
COPY sgea ./sgea
RUN mkdir -p /app/data
