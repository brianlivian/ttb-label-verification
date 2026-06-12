FROM python:3.12-slim

# Run as a non-root user.
RUN useradd --create-home appuser
WORKDIR /home/appuser/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-bake the LinkTransformer embedding model so the container never
# downloads from Hugging Face at runtime (cold starts stay fast, and a
# locked-down network can't break matching).
ENV HF_HOME=/opt/hf
RUN python -c "from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')" \
    && chmod -R a+rX /opt/hf

COPY app ./app

USER appuser

ENV PORT=8080
EXPOSE 8080

# Shell form so $PORT (set by Azure Container Apps and similar) is honored.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
