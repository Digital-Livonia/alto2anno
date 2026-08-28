FROM python:3.11-slim

# xsltproc (libxslt) is the runtime dependency alto2anno.py shells out to.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libxslt1-dev xsltproc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY service/requirements.txt ./service/requirements.txt
RUN pip install --no-cache-dir -r service/requirements.txt

# The existing CLI tool and its stylesheets, used unmodified via subprocess.
COPY alto2anno.py annotationListNoArt.xsl alto2annosv3.xsl ./
COPY service ./service

EXPOSE 8000

CMD ["uvicorn", "service.main:app", "--host", "0.0.0.0", "--port", "8000"]
