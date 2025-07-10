from fastapi import FastAPI, File, UploadFile, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import pdfplumber
import re
import os
from dotenv import load_dotenv

# Carrega as variáveis de ambiente do arquivo .env
load_dotenv()

ENVIRONMENT = os.getenv("ENVIRONMENT", "development") # Default para 'development'

# Inicializa a API
app = FastAPI()

# Libera o CORS para o frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://www.menvo.com.br"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Token de segurança
security = HTTPBearer()

TOKEN_SEGREDO = os.getenv("TOKEN_SEGREDO")
if not TOKEN_SEGREDO:
    raise ValueError("TOKEN_SEGREDO não está configurado nas variáveis de ambiente.")

def verificar_token(credenciais: HTTPAuthorizationCredentials = Depends(security)):
    if credenciais.credentials != TOKEN_SEGREDO:
        raise HTTPException(status_code=401, detail="Token inválido")

# Remove títulos como Sr., Dr., etc., e divide nome e sobrenome
def extrair_nome_e_sobrenome(linha_nome):
    linha_nome = re.sub(r"^(Dr\.|Dra\.|Prof\.|Sr\.|Sra\.)\s+", "", linha_nome)
    partes = re.findall(r"\b[\wÀ-ÿ'-]+\b", linha_nome)
    nome = partes[0] if partes else "Desconhecido"
    sobrenome = " ".join(partes[1:]) if len(partes) > 1 else ""
    return nome, sobrenome

# Extrai experiências profissionais do currículo
def extrair_experiencias(linhas):
    bloco_experiencia = []
    coletando = False
    # Palavras-chave para iniciar a seção de experiência (aprimoradas)
    secoes_inicio = ["experiência", "experiências profissionais", "experiências relevantes", "historico profissional", "experiência profissional"]
    # Palavras-chave para parar a seção de experiência (aprimoradas)
    secoes_fim = ["idioma", "idiomas", "cursos", "formação", "certificações", "educação", "formação acadêmica", "habilidades", "competências técnicas"]

    for i, linha in enumerate(linhas):
        l = linha.lower().strip()
        # Verificar se a linha é um cabeçalho de seção de início e não é um cabeçalho de fim
        if any(s in l for s in secoes_inicio) and not any(s in l for s in secoes_fim):
            coletando = True
            continue # Não adiciona o próprio cabeçalho ao bloco de experiência
        
        # Se estamos coletando e encontramos um cabeçalho de fim, ou uma linha toda em maiúsculas que parece um novo cabeçalho
        if coletando and (any(f in l for f in secoes_fim) or (len(l) > 3 and l.isupper() and i > 0 and not linhas[i-1].strip().isupper())):
            break # Paramos de coletar

        if coletando and linha.strip():
            bloco_experiencia.append(linha.strip())

    return bloco_experiencia

#Extrai informações do texto do currículo
def extrair_informacoes(texto):
    linhas = texto.splitlines()
    linha_nome = next((l.strip() for l in linhas if l.strip()), "Desconhecido")
    nome, sobrenome = extrair_nome_e_sobrenome(linha_nome)

    # Email
    email_match = re.search(r"[\w\.-]+@[\w\.-]+", texto)
    email = email_match.group(0) if email_match else ""

    # Telefone (padrão brasileiro)
    telefone_match = re.search(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}", texto)
    telefone = telefone_match.group(0) if telefone_match else ""

    # Cargo atual e empresa (a partir da seção de experiências)
    cargo_atual = ""
    empresa = ""
    localizacao = ""
    biografia = ""

    # Localização (ex: Paulista/PE)
    local_match = re.search(r"\[(.*?)\]", texto)
    localizacao = local_match.group(1) if local_match else ""

    # Biografia
    for i, linha in enumerate(linhas):
        if "resumo profissional" in linha.lower():
            biografia_linhas = []
            for j in range(i + 1, len(linhas)):
                if linhas[j].strip() == "" or linhas[j].isupper():
                    break  # Para se encontrar uma linha vazia ou um novo título
                biografia_linhas.append(linhas[j])
            biografia = "\n".join(biografia_linhas).strip()
            break

    # Habilidades técnicas
    habilidades = []
    for i, linha in enumerate(linhas):
        if "competências técnicas" in linha.lower():
            habilidades = re.findall(r"\b[A-Z][a-zA-Z0-9#+.]*\b", " ".join(linhas[i+1:i+3]))
            break

    # --- INÍCIO DO AJUSTE PARA CARGO ATUAL E EMPRESA ---
    experiencias = extrair_experiencias(linhas)

    # Regex para capturar "Cargo – Empresa" ou "Cargo na/em Empresa" ou "Cargo @ Empresa"
    # Prioriza o primeiro match encontrado na seção de experiências
    # Considera também a possibilidade de datas ou localização depois da empresa
    # Usamos re.IGNORECASE para ser flexível com "at", "em", "na"
    pattern_cargo_empresa = re.compile(
        r"(.+?)\s*(?:–|-|at|em|na|@)\s*(.+?)(?:\s*(?:\[.*\]|\(?\d{4}.*?\)?|\s*\|\s*.*))?$",
        re.IGNORECASE
    )

    # Regex para casos onde a empresa está toda em maiúsculas em uma linha e o cargo na próxima
    # ou vice-versa, com verificações mais robustas
    
    # Tentativa de extrair Cargo e Empresa da seção de experiências
    for i, linha in enumerate(experiencias):
        # Tenta o padrão "Cargo - Empresa" na linha atual
        match = pattern_cargo_empresa.search(linha)
        if match:
            potential_cargo = match.group(1).strip()
            potential_empresa = match.group(2).strip()

            # Pequenas heurísticas para verificar a validade:
            # 1. O "cargo" não deve ser muito longo (e.g., mais de 10 palavras)
            # 2. A "empresa" não deve ser muito curta (e.g., menos de 2 caracteres)
            # 3. O "cargo" não deve conter termos que geralmente indicam uma empresa
            if (len(potential_cargo.split()) <= 10 and 
                len(potential_empresa) > 1 and
                not any(k in potential_cargo.lower() for k in ["ltda", "s.a", "inc", "corp", "group"])): # Adicione mais se necessário
                
                cargo_atual = potential_cargo
                empresa = potential_empresa
                break # Encontramos o primeiro cargo/empresa válido, saímos do loop
    
    # Se não encontrou no loop acima, tenta o fallback: Empresa em uma linha, Cargo em outra
    if not cargo_atual and not empresa and experiencias:
        for i in range(len(experiencias)):
            linha_atual = experiencias[i].strip()
            # Heurística para identificar uma linha que pode ser uma Empresa:
            # - Toda em maiúsculas E com mais de 2 caracteres
            # - OU contém termos comuns de empresas
            if (len(linha_atual) > 2 and linha_atual.isupper()) or \
               any(k in linha_atual.lower() for k in ["ltda", "s.a", "inc", "corp", "group", "do brasil"]):
                
                potential_empresa = linha_atual
                
                # Procura o cargo na linha seguinte (ou algumas linhas seguintes)
                for j in range(i + 1, min(i + 3, len(experiencias))): # Verifica a próxima ou as duas próximas linhas
                    potential_cargo_line = experiencias[j].strip()
                    # Heurística para identificar uma linha que pode ser um Cargo:
                    # - Não é uma linha vazia
                    # - Não é outra empresa (toda em maiúsculas ou com termos de empresa)
                    # - É razoavelmente curta (menos de 10 palavras, para evitar pegar descrições)
                    if potential_cargo_line and \
                       not (len(potential_cargo_line) > 2 and potential_cargo_line.isupper()) and \
                       not any(k in potential_cargo_line.lower() for k in ["ltda", "s.a", "inc", "corp", "group"]) and \
                       len(potential_cargo_line.split()) <= 10:
                        
                        cargo_atual = potential_cargo_line
                        empresa = potential_empresa
                        break # Encontrou um par empresa/cargo, sai do loop interno
                if cargo_atual and empresa: # Se encontrou no loop interno, sai do externo
                    break

    # --- FIM DO AJUSTE PARA CARGO ATUAL E EMPRESA ---

    return {
        "nome": nome,
        "sobrenome": sobrenome,
        "email": email,
        "telefone": telefone,
        "cargo_atual": cargo_atual,
        "empresa": empresa,
        "localizacao": localizacao,
        "biografia": biografia,
        "habilidades": habilidades
    }

# Endpoint principal da API
@app.post("/extrair")
async def extrair_curriculo(
    file: UploadFile = File(...),
    # Descomente abaixo se quiser exigir token:
    credenciais: HTTPAuthorizationCredentials = Depends(verificar_token)
):
    texto_extraido = ""

    try:
        with pdfplumber.open(file.file) as pdf:
            for pagina in pdf.pages:
                texto = pagina.extract_text()
                if texto:
                    texto_extraido += texto + "\n"
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao processar PDF: {str(e)}")

    dados = extrair_informacoes(texto_extraido)
    return dados    