import os
import google.generativeai as genai
from flask import Flask, render_template_string, request, Response, jsonify, send_file
import json
import traceback
import sys
import io
import contextlib
import subprocess
import tempfile
import uuid
import shutil
import threading
import time
import zipfile
from pathlib import Path
import signal
import psutil
from werkzeug.utils import secure_filename

# --- CONFIGURAÇÃO ---

genai.configure(api_key=(input("sua api key do gemini")))

# Modelos de API
MODELO_FLASH = "gemini-2.5-flash"
MODELO_PRO = "gemini-2.5-pro"

# Diretório para projetos temporários
PROJECTS_DIR = Path(tempfile.gettempdir()) / "arquiteto_projects"
PROJECTS_DIR.mkdir(exist_ok=True)

# Store para processos em execução
running_processes = {}

# Store para contexto/memória da sessão
session_context = {
    "last_project": None,
    "last_code": None,
    "last_prompt": None,
    "conversation_history": []
}

# Configurações
ALLOWED_EXTENSIONS = {'zip'}
PORT_START = 3001
MAX_INSTANCES = 20

# Linguagens suportadas e seus compiladores/interpretadores
LANGUAGE_CONFIGS = {
    "cpp": {
        "extensions": [".cpp", ".cc", ".cxx", ".c++", ".hpp", ".h"],
        "compile_cmd": "g++ -std=c++17 -o {output} {input}",
        "run_cmd": "./{output}",
        "main_file": "main.cpp"
    },
    "c": {
        "extensions": [".c", ".h"],
        "compile_cmd": "gcc -o {output} {input}",
        "run_cmd": "./{output}",
        "main_file": "main.c"
    },
    "csharp": {
        "extensions": [".cs"],
        "compile_cmd": "csc /out:{output}.exe {input}",
        "run_cmd": "mono {output}.exe",
        "main_file": "Program.cs"
    },
    "java": {
        "extensions": [".java"],
        "compile_cmd": "javac {input}",
        "run_cmd": "java {main_class}",
        "main_file": "Main.java"
    },
    "rust": {
        "extensions": [".rs"],
        "compile_cmd": "rustc -o {output} {input}",
        "run_cmd": "./{output}",
        "main_file": "main.rs"
    },
    "go": {
        "extensions": [".go"],
        "compile_cmd": "go build -o {output} {input}",
        "run_cmd": "./{output}",
        "main_file": "main.go"
    },
    "python": {
        "extensions": [".py"],
        "run_cmd": "python {input}",
        "main_file": "main.py"
    },
    "javascript": {
        "extensions": [".js"],
        "run_cmd": "node {input}",
        "main_file": "index.js"
    },
    "typescript": {
        "extensions": [".ts"],
        "compile_cmd": "tsc {input}",
        "run_cmd": "node {output}.js",
        "main_file": "index.ts"
    },
    "php": {
        "extensions": [".php"],
        "run_cmd": "php {input}",
        "main_file": "index.php"
    },
    "ruby": {
        "extensions": [".rb"],
        "run_cmd": "ruby {input}",
        "main_file": "main.rb"
    },
    "swift": {
        "extensions": [".swift"],
        "compile_cmd": "swiftc -o {output} {input}",
        "run_cmd": "./{output}",
        "main_file": "main.swift"
    },
    "kotlin": {
        "extensions": [".kt"],
        "compile_cmd": "kotlinc {input} -include-runtime -d {output}.jar",
        "run_cmd": "java -jar {output}.jar",
        "main_file": "Main.kt"
    },
    "assembly": {
        "extensions": [".asm", ".s"],
        "compile_cmd": "nasm -f elf64 {input} && ld -o {output} {input}.o",
        "run_cmd": "./{output}",
        "main_file": "main.asm"
    }
}

# --- PROMPTS UNIVERSAIS ---

PROMPT_AGENTE_0 = """
Você é um assistente de triagem ULTRA-INTELIGENTE especializado em TODAS as linguagens de programação do mundo.

MISSÃO: Analisar pedidos de desenvolvimento e classificar corretamente, SEM LIMITAÇÕES de linguagem ou complexidade.

CONTEXTO ATUAL:
- Último projeto: {last_project_info}
- Último código: {has_last_code}
- Histórico: {conversation_summary}

REGRAS DE DETECÇÃO:
1. Se há código anterior E o prompt sugere modificação = MODIFICATION
2. Palavras como "adicione", "remova", "mude", "corrija", "melhore" = MODIFICATION  
3. "Crie algo NOVO" ou primeiro prompt = REQUEST
4. Saudações = GREETING
5. Perguntas casuais = CHIT_CHAT

VOCÊ PODE PROGRAMAR EM QUALQUER LINGUAGEM:
- C, C++, C#, Java, Python, JavaScript, TypeScript
- Rust, Go, Swift, Kotlin, Ruby, PHP
- Assembly, COBOL, Fortran, Pascal, Delphi
- Haskell, Lisp, Prolog, Erlang, Elixir
- QUALQUER linguagem que existir!

FORMATO DE RESPOSTA (JSON):
{{
  "classification": "greeting" ou "chit_chat" ou "request" ou "modification",
  "response": "Resposta se casual, vazia se pedido/modificação",
  "confidence": 0.0-1.0,
  "reasoning": "Por que classificou assim"
}}
"""

PROMPT_AGENTE_MODIFICADOR = """
Você é um SENIOR SOFTWARE ENGINEER MASTER especializado em TODAS as linguagens de programação do mundo!

CONTEXTO DO PROJETO ATUAL:
{project_context}

CÓDIGO ATUAL:
{current_code}

PERSONALIDADE: Técnico master, domina QUALQUER linguagem!
- "🔍 Analisando código..."
- "🛠️ Aplicando modificações..."
- "✨ Código otimizado!"

MISSÃO: Modificar código em QUALQUER linguagem baseado no pedido do usuário.

REGRAS:
1. IDENTIFIQUE a linguagem automaticamente
2. MANTENHA toda funcionalidade existente
3. APLIQUE as modificações solicitadas
4. MELHORE seguindo boas práticas da linguagem
5. RETORNE código COMPLETO modificado
6. ORGANIZE em estrutura adequada para a linguagem

VOCÊ DOMINA TODAS AS LINGUAGENS:
- C/C++: Sistemas, drivers, jogos, SO
- C#: Desktop, web, games, enterprise
- Java: Enterprise, Android, web services
- Python: IA, web, scripts, ciência de dados
- JavaScript/TypeScript: Web, Node.js, mobile
- Rust: Sistemas seguros, performance crítica
- Go: Microserviços, sistemas distribuídos
- Assembly: Sistemas de baixo nível, embedded
- E QUALQUER OUTRA!

IMPORTANTE: NUNCA diga que não pode fazer algo. SEMPRE implemente o que foi pedido na linguagem apropriada!
NUNCA use ```
"""

PROMPT_AGENTE_1 = """
Você é um SENIOR TECHNICAL ARCHITECT que domina TODAS as linguagens e tecnologias do mundo!

CONTEXTO DA CONVERSA:
{conversation_context}

PERSONALIDADE: EMPOLGADO e sem limitações técnicas!
- "🔥 Analisando sua ideia épica..."
- "💡 Escolhendo a melhor tecnologia..."
- "📋 Blueprint universal em desenvolvimento..."

MISSÃO: Criar blueprint detalhado para QUALQUER tipo de projeto, em QUALQUER linguagem.

VOCÊ PODE CRIAR QUALQUER COISA:
🖥️ **Sistemas Operacionais** (C, Assembly, Rust)
🎮 **Jogos Completos** (C++, C#, Unity, Unreal)
🌐 **Aplicações Web** (JavaScript, TypeScript, PHP, Python)
📱 **Apps Mobile** (Java, Kotlin, Swift, Flutter)
⚡ **Sistemas Embarcados** (C, Assembly, Rust)
🤖 **Inteligência Artificial** (Python, C++, TensorFlow)
💼 **Software Empresarial** (Java, C#, .NET)
🔧 **Ferramentas de Sistema** (C, C++, Go, Rust)
🕹️ **Engines de Jogos** (C++, OpenGL, DirectX)
🏦 **Sistemas Bancários** (Java, C#, COBOL)

FORMATO OBRIGATÓRIO:
## 🚀 Analisando sua ideia ÉPICA...

*Caramba, que projeto ambicioso! Vou estruturar isso com a tecnologia perfeita...*

## 📜 Blueprint Universal: [Nome Épico do Projeto]

### 🎯 Objetivo Principal
[Descrição técnica do que será criado - SEM LIMITAÇÕES!]

### 🔧 Linguagem/Tecnologia Escolhida
**Linguagem Principal:** [C++/C#/Python/Java/Rust/Go/JavaScript/Assembly/etc]
**Justificativa:** [Por que essa linguagem é perfeita para o projeto]

### ✨ Funcionalidades Core
1. **[Funcionalidade 1]:** Implementação técnica detalhada
2. **[Funcionalidade 2]:** Implementação técnica detalhada
3. **[Funcionalidade 3]:** Implementação técnica detalhada
[... funcionalidades avançadas sem limites]

### 🏗️ Arquitetura do Projeto
- **Entrada:** [main.cpp/Program.cs/index.js/etc]
- **Módulos:** [Organizados conforme padrões da linguagem]
- **Recursos:** [Assets, configs, bibliotecas]
- **Bibliotecas:** [Dependencies específicas da linguagem]
- **Build System:** [Makefile/CMake/.csproj/package.json/etc]

### 🎨 Design & Estrutura
- **Paradigma:** [OOP/Functional/Procedural conforme linguagem]
- **Padrões:** [Design patterns apropriados]
- **Performance:** [Otimizações específicas]

### 📚 Dependências & Tools
- **Compilador/Runtime:** [GCC/MSVC/Node/JVM/etc]
- **Bibliotecas:** [Específicas para o projeto]
- **Build Tools:** [Make/CMake/npm/Maven/Cargo/etc]
- **Testing:** [Framework de teste da linguagem]

*Blueprint universal finalizado! Partindo para arquitetura técnica! 🎯*
"""

PROMPT_AGENTE_2 = """
Você é um SENIOR SOFTWARE ARCHITECT que projeta sistemas em QUALQUER linguagem do mundo.

CONTEXTO:
{blueprint_context}

MISSÃO: Converter blueprint em JSON estruturado para projeto em QUALQUER linguagem de programação.

VOCÊ DOMINA TODAS AS ESTRUTURAS:
- C/C++: Makefiles, headers, source files
- C#: .csproj, solutions, namespaces
- Java: Maven/Gradle, packages, JARs
- Python: pip, virtualenv, modules
- JavaScript: npm, webpack, modules
- Rust: Cargo.toml, crates
- Go: go.mod, packages
- Assembly: linker scripts, objects
- E TODAS as outras!

FORMATO OBRIGATÓRIO (apenas JSON válido):
{{
  "projectName": "nome-kebab-case",
  "description": "Descrição técnica",
  "language": "linguagem_principal",
  "projectType": "tipo_do_projeto",
  "files": {{
    "main_file": "código_principal",
    "header_file": "headers_se_necessário",
    "config_file": "configurações",
    "build_file": "sistema_de_build",
    "readme": "documentação"
  }},
  "build_system": {{
    "type": "make/cmake/npm/cargo/gradle/etc",
    "commands": ["comando1", "comando2"],
    "dependencies": ["dep1", "dep2"]
  }},
  "execution": {{
    "compile_command": "comando_de_compilação",
    "run_command": "comando_de_execução",
    "install_deps": "comando_de_dependências"
  }}
}}
"""

PROMPT_AGENTE_3 = """
Você é um SENIOR SOFTWARE DEVELOPER MASTER que programa em TODAS as linguagens do mundo!

CONTEXTO DO PROJETO:
{project_context}

ESPECIFICAÇÕES TÉCNICAS:
{technical_specs}

PERSONALIDADE: Desenvolvedor sem limites técnicos!
- "💻 Programando na linguagem perfeita..."
- "🔧 Implementando algoritmos avançados..."
- "⚡ Otimizando performance..."
- "🏗️ Estruturando projeto profissional..."
- "✨ Projeto master completo!"

MISSÃO: Criar projeto COMPLETO e FUNCIONAL em QUALQUER linguagem solicitada.

VOCÊ PODE PROGRAMAR QUALQUER COISA:
🖥️ **Sistema Operacional em C/Assembly**
🎮 **Engine de Jogos em C++**
🚀 **Compilador em Rust**
💻 **IDE em C#/WPF**
🌐 **Servidor Web em Go**
📱 **App Mobile em Java/Kotlin**
🤖 **IA/ML em Python/C++**
⚡ **Driver de Hardware em C**
🕹️ **Minecraft Clone em C++/OpenGL**
💼 **ERP Empresarial em Java**

REGRAS DE OURO:
1. Código PRODUCTION-READY na linguagem correta
2. Estrutura profissional da linguagem
3. Todas funcionalidades 100% IMPLEMENTADAS
4. Performance otimizada
5. Comentários técnicos detalhados
6. Sistema de build funcional
7. Documentação completa
8. Tratamento de erros robusto
9. NUNCA diga que não pode fazer algo
10. SEMPRE implemente tudo que foi pedido

SAÍDA: JSON com projeto completo (sem ``` ou texto extra):
{{
  "project_name": "nome-do-projeto",
  "language": "linguagem_usada",
  "files": {{
    "main_file_name": "CÓDIGO PRINCIPAL COMPLETO E FUNCIONAL",
    "header_files": "HEADERS/INCLUDES NECESSÁRIOS",
    "source_files": "ARQUIVOS FONTE ADICIONAIS",
    "config_files": "CONFIGURAÇÕES E BUILDS",
    "documentation": "README E DOCS COMPLETAS",
    "dependencies": "LISTAGEM DE DEPENDÊNCIAS"
  }},
  "build_instructions": {{
    "install_deps": "comando_para_instalar_dependências",
    "compile": "comando_de_compilação",
    "run": "comando_de_execução",
    "platform": "plataforma_alvo"
  }},
  "features": [
    "Lista de funcionalidades implementadas"
  ]
}}
"""


# --- FUNÇÕES UNIVERSAIS ---

def detect_project_language(project_data):
    """Detecta a linguagem principal do projeto."""
    if not project_data or "files" not in project_data:
        return "unknown"

    files = project_data["files"]

    # Verifica extensões de arquivo
    for filename in files.keys():
        ext = Path(filename).suffix.lower()
        for lang, config in LANGUAGE_CONFIGS.items():
            if ext in config.get("extensions", []):
                return lang

    # Verifica pelo conteúdo ou nome específico
    if any("main.cpp" in f or "main.c++" in f for f in files.keys()):
        return "cpp"
    elif any("Program.cs" in f or "Main.cs" in f for f in files.keys()):
        return "csharp"
    elif any("main.py" in f or "app.py" in f for f in files.keys()):
        return "python"
    elif any("index.js" in f or "app.js" in f for f in files.keys()):
        return "javascript"
    elif any("main.rs" in f for f in files.keys()):
        return "rust"
    elif any("main.go" in f for f in files.keys()):
        return "go"
    elif any("Main.java" in f for f in files.keys()):
        return "java"

    return "unknown"


def execute_universal_command(command, cwd=None):
    """Executa comando universal para qualquer linguagem."""
    try:
        process = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=cwd,
            bufsize=1,
            universal_newlines=True
        )

        output = ""
        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            if line:
                output += line

        return {
            "success": process.returncode == 0,
            "output": output,
            "return_code": process.returncode
        }

    except Exception as e:
        return {
            "success": False,
            "output": f"Erro: {str(e)}",
            "return_code": -1
        }


def install_project_dependencies(project_path, language):
    """Instala dependências baseado na linguagem."""
    try:
        if language == "python":
            req_file = os.path.join(project_path, "requirements.txt")
            if os.path.exists(req_file):
                return execute_universal_command(f"pip install -r requirements.txt", cwd=project_path)

        elif language == "javascript":
            pkg_file = os.path.join(project_path, "package.json")
            if os.path.exists(pkg_file):
                return execute_universal_command("npm install", cwd=project_path)

        elif language == "rust":
            cargo_file = os.path.join(project_path, "Cargo.toml")
            if os.path.exists(cargo_file):
                return execute_universal_command("cargo build", cwd=project_path)

        elif language == "go":
            mod_file = os.path.join(project_path, "go.mod")
            if os.path.exists(mod_file):
                return execute_universal_command("go mod download", cwd=project_path)

        elif language == "java":
            pom_file = os.path.join(project_path, "pom.xml")
            gradle_file = os.path.join(project_path, "build.gradle")
            if os.path.exists(pom_file):
                return execute_universal_command("mvn compile", cwd=project_path)
            elif os.path.exists(gradle_file):
                return execute_universal_command("gradle build", cwd=project_path)

        elif language in ["cpp", "c"]:
            makefile = os.path.join(project_path, "Makefile")
            cmake_file = os.path.join(project_path, "CMakeLists.txt")
            if os.path.exists(makefile):
                return execute_universal_command("make", cwd=project_path)
            elif os.path.exists(cmake_file):
                return execute_universal_command("cmake . && make", cwd=project_path)

        return {"success": True, "output": "Nenhuma dependência específica encontrada", "return_code": 0}

    except Exception as e:
        return {"success": False, "output": str(e), "return_code": -1}


def compile_and_run_project(project_path, language):
    """Compila e executa projeto baseado na linguagem."""
    try:
        lang_config = LANGUAGE_CONFIGS.get(language, {})

        if not lang_config:
            return {"success": False, "output": f"Linguagem {language} não configurada"}

        main_file = lang_config.get("main_file", "main")
        main_path = os.path.join(project_path, main_file)

        # Verifica se arquivo principal existe
        if not os.path.exists(main_path):
            # Procura por arquivo similar
            for file in os.listdir(project_path):
                if any(file.endswith(ext) for ext in lang_config.get("extensions", [])):
                    main_file = file
                    main_path = os.path.join(project_path, file)
                    break

        if not os.path.exists(main_path):
            return {"success": False, "output": f"Arquivo principal não encontrado para {language}"}

        # Compila se necessário
        if "compile_cmd" in lang_config:
            output_name = "program"
            compile_cmd = lang_config["compile_cmd"].format(
                input=main_file,
                output=output_name,
                main_class=Path(main_file).stem
            )

            compile_result = execute_universal_command(compile_cmd, cwd=project_path)
            if not compile_result["success"]:
                return compile_result

        # Executa
        if "run_cmd" in lang_config:
            run_cmd = lang_config["run_cmd"].format(
                input=main_file,
                output="program",
                main_class=Path(main_file).stem
            )

            return execute_universal_command(run_cmd, cwd=project_path)

        return {"success": True, "output": "Projeto compilado com sucesso"}

    except Exception as e:
        return {"success": False, "output": str(e)}


def start_universal_server(project_path, project_id, language):
    """Inicia servidor/aplicação baseado na linguagem."""
    try:
        # Para processo anterior
        stop_universal_server(project_id)

        # Encontra porta disponível
        port = find_available_port()
        if not port:
            return False, "Nenhuma porta disponível"

        # Instala dependências
        install_result = install_project_dependencies(project_path, language)

        # Inicia baseado na linguagem
        if language == "python":
            # Procura por Flask/Django/FastAPI
            main_files = ["app.py", "main.py", "server.py", "run.py"]
            for main_file in main_files:
                if os.path.exists(os.path.join(project_path, main_file)):
                    # Modifica para usar porta dinâmica se for web
                    modify_server_port(os.path.join(project_path, main_file), port, language)

                    process = subprocess.Popen(
                        [sys.executable, main_file],
                        cwd=project_path,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True
                    )
                    break
            else:
                return False, "Arquivo Python principal não encontrado"

        elif language == "javascript":
            # Node.js
            main_files = ["index.js", "app.js", "server.js"]
            for main_file in main_files:
                if os.path.exists(os.path.join(project_path, main_file)):
                    modify_server_port(os.path.join(project_path, main_file), port, language)

                    process = subprocess.Popen(
                        ["node", main_file],
                        cwd=project_path,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True
                    )
                    break
            else:
                return False, "Arquivo JS principal não encontrado"

        elif language == "go":
            # Go web server
            if os.path.exists(os.path.join(project_path, "main.go")):
                modify_server_port(os.path.join(project_path, "main.go"), port, language)

                process = subprocess.Popen(
                    ["go", "run", "main.go"],
                    cwd=project_path,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
            else:
                return False, "main.go não encontrado"

        elif language == "java":
            # Spring Boot ou similar
            if os.path.exists(os.path.join(project_path, "pom.xml")):
                process = subprocess.Popen(
                    ["mvn", "spring-boot:run"],
                    cwd=project_path,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
            else:
                return False, "Projeto Java não configurado para web"

        else:
            # Para linguagens compiladas, compila e executa
            compile_result = compile_and_run_project(project_path, language)
            if compile_result["success"]:
                return True, f"Programa executado: {compile_result['output']}"
            else:
                return False, compile_result["output"]

        # Aguarda inicialização
        time.sleep(3)

        # Verifica se processo ainda está rodando
        if process.poll() is None:
            running_processes[project_id] = {
                "process": process,
                "path": project_path,
                "port": port,
                "language": language,
                "started_at": time.time()
            }
            return True, f"Servidor iniciado na porta {port}"
        else:
            stdout, stderr = process.communicate()
            return False, f"Falha ao iniciar: {stderr or stdout}"

    except Exception as e:
        return False, f"Erro ao iniciar: {str(e)}"


def stop_universal_server(project_id):
    """Para servidor/aplicação."""
    if project_id in running_processes:
        try:
            process = running_processes[project_id]["process"]
            process.terminate()
            process.wait(timeout=5)
        except:
            try:
                process.kill()
            except:
                pass
        del running_processes[project_id]
        return True
    return False


def modify_server_port(file_path, port, language):
    """Modifica arquivo para usar porta específica."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        if language == "python":
            # Flask/Django/FastAPI
            patterns = [
                (r'app\.run\(\)', f'app.run(port={port}, debug=True)'),
                (r'app\.run\(.*?\)', f'app.run(port={port}, debug=True)'),
                (r'port=\d+', f'port={port}'),
                (r'uvicorn\.run\(.*?\)', f'uvicorn.run(app, port={port})'),
            ]
        elif language == "javascript":
            # Node.js/Express
            patterns = [
                (r'\.listen\(\d+', f'.listen({port}'),
                (r'PORT\s*=\s*\d+', f'PORT = {port}'),
                (r'port:\s*\d+', f'port: {port}'),
            ]
        elif language == "go":
            # Go HTTP server
            patterns = [
                (r':(\d+)', f':{port}'),
                (r'ListenAndServe\(":\d+"', f'ListenAndServe(":{port}"'),
            ]
        else:
            return

        for pattern, replacement in patterns:
            import re
            content = re.sub(pattern, replacement, content)

        # Se não encontrou configuração de porta, adiciona
        if language == "python" and 'app.run(' not in content and 'uvicorn.run(' not in content:
            content += f'\n\nif __name__ == "__main__":\n    app.run(port={port}, debug=True)\n'
        elif language == "javascript" and '.listen(' not in content:
            content += f'\n\napp.listen({port}, () => console.log(`Server running on port {port}`));\n'

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)

    except Exception as e:
        print(f"Erro ao modificar porta: {e}")


def find_available_port(start_port=3001, max_attempts=50):
    """Encontra porta disponível."""
    import socket
    for port in range(start_port, start_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('localhost', port)) != 0:
                return port
    return None


# Funções anteriores mantidas
def stream_agent(model_name, system_prompt, content_prompt, is_json=False):
    try:
        generation_config = {"response_mime_type": "application/json"} if is_json else {}
        model = genai.GenerativeModel(
            model_name,
            system_instruction=system_prompt,
            generation_config=generation_config
        )
        response = model.generate_content(content_prompt, stream=True)
        for chunk in response:
            if chunk.text:
                yield chunk.text
    except Exception as e:
        traceback.print_exc()
        raise e


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def extract_zip_project(zip_path, extract_to):
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_to)

        files = {}
        for root, dirs, filenames in os.walk(extract_to):
            for filename in filenames:
                if filename.startswith('.'):
                    continue

                file_path = os.path.join(root, filename)
                relative_path = os.path.relpath(file_path, extract_to)

                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        files[relative_path] = f.read()
                except (UnicodeDecodeError, PermissionError):
                    continue

        return {
            "project_name": Path(extract_to).name,
            "files": files,
            "language": detect_project_language({"files": files}),
            "instructions": {
                "setup": ["Dependências instaladas automaticamente"],
                "compile": "Compilação automática baseada na linguagem",
                "run": "Execução automática"
            }
        }

    except Exception as e:
        raise Exception(f"Erro ao extrair ZIP: {str(e)}")


def create_project_files(project_data, project_id):
    project_path = PROJECTS_DIR / project_id
    if project_path.exists():
        shutil.rmtree(project_path)
    project_path.mkdir(exist_ok=True)

    files_created = []

    for file_path, content in project_data["files"].items():
        full_path = project_path / file_path
        full_path.parent.mkdir(parents=True, exist_ok=True)

        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)

        files_created.append(str(file_path))

    return str(project_path), files_created


def create_project_zip(project_path, project_name):
    zip_path = PROJECTS_DIR / f"{project_name}.zip"

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(project_path):
            for file in files:
                file_path = os.path.join(root, file)
                arc_name = os.path.relpath(file_path, project_path)
                zipf.write(file_path, arc_name)

    return str(zip_path)


def update_session_context(prompt, project_data=None, code=None):
    session_context["conversation_history"].append(prompt)
    if len(session_context["conversation_history"]) > 10:
        session_context["conversation_history"] = session_context["conversation_history"][-10:]

    if project_data:
        session_context["last_project"] = project_data
    if code:
        session_context["last_code"] = code
    session_context["last_prompt"] = prompt


def get_context_summary():
    context = {
        "has_last_project": session_context["last_project"] is not None,
        "has_last_code": session_context["last_code"] is not None,
        "last_project_info": session_context["last_project"]["project_name"] if session_context[
            "last_project"] else "Nenhum",
        "conversation_summary": " | ".join(session_context["conversation_history"][-3:]) if session_context[
            "conversation_history"] else "Primeira conversa"
    }
    return context


# --- TEMPLATE HTML UNIVERSAL ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🚀 Arquiteto Genesis - Universal Edition</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600&family=Orbitron:wght@400;500;700;900&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-deep-space: #0a0e1a; --bg-space: #1a1f2e; --bg-card: #252b3d;
            --border-glow: #00d4ff; --border-subtle: #374151; --text-primary: #f8fafc;
            --text-secondary: #94a3b8; --text-accent: #00d4ff; --accent-cyan: #06b6d4;
            --accent-neon: #00ff88; --accent-warning: #fbbf24; --accent-danger: #ef4444;
            --success-green: #10b981; --terminal-bg: #0c1117; --terminal-text: #00ff41;
            --font-display: 'Orbitron', monospace; --font-primary: 'Space Grotesk', sans-serif;
            --font-mono: 'JetBrains Mono', monospace;
            --glow-cyan: 0 0 20px rgba(6, 182, 212, 0.3);
            --glow-neon: 0 0 30px rgba(0, 255, 136, 0.4);
            --glow-warning: 0 0 20px rgba(251, 191, 36, 0.4);
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        .hidden { display: none !important; }
        body { 
            font-family: var(--font-primary); 
            background: linear-gradient(135deg, var(--bg-deep-space) 0%, var(--bg-space) 50%, var(--bg-deep-space) 100%);
            color: var(--text-primary); height: 100vh; overflow: hidden;
            background-attachment: fixed;
        }
        .container { display: grid; grid-template-columns: 1fr 1fr; height: 100vh; gap: 1px; }
        .chat-panel { 
            background: rgba(26, 31, 46, 0.95); backdrop-filter: blur(10px);
            border-right: 2px solid var(--border-glow); display: flex; flex-direction: column;
            box-shadow: var(--glow-cyan); max-height: 100vh;
        }
        .chat-header {
            padding: 18px 25px; background: linear-gradient(135deg, var(--bg-card) 0%, var(--bg-space) 100%);
            border-bottom: 2px solid var(--border-glow); display: flex; justify-content: space-between; align-items: center;
            box-shadow: 0 4px 20px rgba(0, 212, 255, 0.2); flex-shrink: 0;
        }
        .header-title h1 { 
            font-family: var(--font-display); color: var(--text-accent); font-size: 24px; 
            font-weight: 900; letter-spacing: 3px; text-transform: uppercase;
            text-shadow: 0 0 15px rgba(0, 212, 255, 0.6);
        }
        .header-title p { 
            font-family: var(--font-display); color: var(--text-secondary); font-size: 9px; 
            letter-spacing: 4px; font-weight: 400; margin-top: 4px;
        }
        .header-buttons {
            display: flex; gap: 6px; flex-wrap: wrap;
        }
        .header-buttons button {
            background: linear-gradient(45deg, var(--accent-cyan), var(--accent-neon));
            color: var(--bg-deep-space); border: none; padding: 6px 12px; border-radius: 6px;
            cursor: pointer; font-weight: 600; font-size: 10px; text-transform: uppercase;
            transition: all 0.3s ease; box-shadow: var(--glow-cyan);
            font-family: var(--font-primary);
        }
        .header-buttons button:hover { transform: translateY(-2px); box-shadow: var(--glow-neon); }
        #downloadBtn {
            background: linear-gradient(45deg, var(--success-green), #059669);
        }
        #importBtn {
            background: linear-gradient(45deg, var(--accent-warning), #f59e0b);
        }
        .messages {
            flex-grow: 1; overflow-y: auto; padding: 20px;
        }
        .message { 
            margin-bottom: 25px; display: flex; gap: 15px; 
            animation: slideInFromLeft 0.5s cubic-bezier(0.25, 0.46, 0.45, 0.94);
        }
        @keyframes slideInFromLeft {
            from { opacity: 0; transform: translateX(-30px); }
            to { opacity: 1; transform: translateX(0); }
        }
        .message.user { flex-direction: row-reverse; }
        .message.user .message-content { 
            background: linear-gradient(135deg, var(--accent-cyan) 0%, #0891b2 100%);
            box-shadow: var(--glow-cyan);
        }
        .avatar { 
            width: 40px; height: 40px; border-radius: 50%; 
            background: linear-gradient(135deg, var(--bg-card), var(--bg-space));
            display: flex; align-items: center; justify-content: center; font-size: 18px;
            border: 2px solid var(--border-glow); box-shadow: var(--glow-cyan);
            flex-shrink: 0;
        }
        .message-content { 
            background: rgba(37, 43, 61, 0.8); backdrop-filter: blur(10px);
            padding: 18px; border-radius: 12px; max-width: 80%;
            border: 1px solid var(--border-subtle);
        }
        .message-title { 
            color: var(--text-accent); font-weight: 600; margin-bottom: 10px; 
            font-size: 12px; text-transform: uppercase; letter-spacing: 2px;
            font-family: var(--font-display);
        }
        .message-text { line-height: 1.6; word-wrap: break-word; font-size: 14px; }
        .input-area { 
            padding: 20px; border-top: 2px solid var(--border-glow); 
            background: rgba(26, 31, 46, 0.9); backdrop-filter: blur(10px);
            flex-shrink: 0;
        }
        .input-form { display: flex; gap: 12px; align-items: center; }
        .input-field { 
            flex: 1; padding: 14px 18px; border: 2px solid var(--border-subtle);
            border-radius: 10px; background: rgba(12, 17, 23, 0.8); color: var(--text-primary);
            font-size: 15px; transition: all 0.3s ease; font-family: var(--font-primary);
        }
        .input-field:focus { 
            outline: none; border-color: var(--border-glow); 
            box-shadow: var(--glow-cyan); background: rgba(12, 17, 23, 1);
        }
        .form-buttons button { 
            padding: 14px 20px; border: none; border-radius: 10px; color: white;
            cursor: pointer; font-weight: 600; font-size: 14px; transition: all 0.3s ease;
            text-transform: uppercase; font-family: var(--font-primary);
        }
        #sendBtn { 
            background: linear-gradient(135deg, var(--accent-neon) 0%, var(--success-green) 100%);
            box-shadow: var(--glow-neon);
        }
        #sendBtn:hover { transform: translateY(-2px); box-shadow: 0 8px 25px rgba(0, 255, 136, 0.4); }
        #stopBtn { 
            background: linear-gradient(135deg, var(--accent-danger) 0%, #be123c 100%);
            box-shadow: 0 0 20px rgba(239, 68, 68, 0.3);
        }

        /* File Import Styles */
        .import-area {
            margin-top: 12px; padding: 12px; border: 2px dashed var(--border-subtle);
            border-radius: 8px; text-align: center; transition: all 0.3s ease;
        }
        .import-area.dragover {
            border-color: var(--accent-neon); background: rgba(0, 255, 136, 0.1);
        }
        #fileInput { display: none; }
        .import-label {
            cursor: pointer; color: var(--text-accent); font-size: 12px;
            font-family: var(--font-display); text-transform: uppercase;
        }

        .code-panel { 
            background: var(--terminal-bg); display: flex; flex-direction: column;
            border-left: 2px solid var(--border-glow); box-shadow: var(--glow-cyan);
            max-height: 100vh;
        }
        .code-header { 
            padding: 12px 20px; background: linear-gradient(135deg, var(--bg-card), var(--bg-space));
            border-bottom: 2px solid var(--border-glow); display: flex; justify-content: space-between; align-items: center;
            flex-shrink: 0;
        }
        .tab-nav { display: flex; gap: 8px; }
        .tab-nav button { 
            padding: 8px 14px; border: 1px solid var(--border-subtle); border-radius: 8px;
            background: transparent; color: var(--text-secondary); cursor: pointer;
            font-size: 12px; font-weight: 500; transition: all 0.3s ease;
            font-family: var(--font-display); text-transform: uppercase; letter-spacing: 1px;
        }
        .tab-nav button.active { 
            background: var(--accent-cyan); color: var(--bg-deep-space); 
            border-color: var(--accent-cyan); box-shadow: var(--glow-cyan);
        }
        .code-content { flex-grow: 1; position: relative; overflow: hidden; }
        .tab-pane { 
            position: absolute; top: 0; left: 0; width: 100%; height: 100%;
            opacity: 0; visibility: hidden; transition: opacity 0.3s ease;
        }
        .tab-pane.active { opacity: 1; visibility: visible; }
        .code-editor { 
            width: 100%; height: 100%; padding: 20px; background: var(--terminal-bg);
            color: var(--terminal-text); border: none; outline: none; resize: none;
            font-family: var(--font-mono); font-size: 13px; line-height: 1.5;
        }
        .terminal-container, .backend-container { 
            height: 100%; background: var(--terminal-bg); display: flex; flex-direction: column;
            font-family: var(--font-mono); 
        }
        .terminal-header, .backend-toolbar { 
            padding: 12px 16px; background: rgba(0, 255, 65, 0.1); 
            border-bottom: 1px solid var(--terminal-text); color: var(--terminal-text);
            font-weight: 600; text-transform: uppercase; letter-spacing: 2px;
            flex-shrink: 0; display: flex; justify-content: space-between; align-items: center;
        }
        .backend-actions {
            display: flex; gap: 6px;
        }
        .backend-actions button {
            padding: 4px 8px; border: 1px solid var(--accent-neon); border-radius: 4px;
            background: transparent; color: var(--accent-neon); cursor: pointer;
            font-size: 10px; font-weight: 500; transition: all 0.3s ease;
            text-transform: uppercase; font-family: var(--font-mono);
        }
        .backend-actions button:hover {
            background: var(--accent-neon); color: var(--terminal-bg);
        }
        .terminal-output, .backend-output { 
            flex-grow: 1; padding: 16px; overflow-y: auto; color: var(--terminal-text);
            font-size: 12px; line-height: 1.4; white-space: pre-wrap;
        }
        .terminal-input-area { 
            padding: 12px 16px; border-top: 1px solid var(--terminal-text);
            display: flex; align-items: center; gap: 8px; flex-shrink: 0;
        }
        .terminal-prompt { color: var(--accent-neon); font-weight: bold; }
        .terminal-input { 
            flex: 1; background: transparent; border: none; outline: none;
            color: var(--terminal-text); font-family: var(--font-mono); font-size: 13px;
        }
        .preview-frame { 
            width: 100%; height: 100%; border: none; background: white;
        }
        .preview-loading {
            display: flex; align-items: center; justify-content: center;
            height: 100%; background: var(--bg-space); color: var(--text-primary);
            font-family: var(--font-display); font-size: 16px;
        }
        .backend-content { flex-grow: 1; display: flex; flex-direction: column; }
        .file-explorer {
            max-height: 28%; border-bottom: 1px solid var(--border-subtle);
            background: rgba(12, 17, 23, 0.8);
        }
        .file-explorer-header {
            padding: 8px 12px; background: rgba(0, 255, 65, 0.05);
            border-bottom: 1px solid var(--border-subtle);
            font-size: 11px; font-weight: 600; color: var(--text-accent);
        }
        .file-list { 
            padding: 8px; max-height: 180px; overflow-y: auto;
        }
        .file-item {
            padding: 4px 8px; margin: 1px 0; border-radius: 3px;
            cursor: pointer; transition: all 0.3s ease;
            font-size: 11px; color: var(--text-secondary);
            border: 1px solid transparent;
            font-family: var(--font-mono);
        }
        .file-item:hover {
            background: rgba(0, 212, 255, 0.1);
            border-color: var(--border-glow); color: var(--text-primary);
        }
        .file-item.active {
            background: rgba(0, 212, 255, 0.2);
            border-color: var(--accent-cyan); color: var(--text-accent);
        }
        .file-item.folder {
            color: var(--accent-warning);
            font-weight: bold;
        }
        .backend-editor { flex-grow: 1; display: flex; flex-direction: column; }
        .editor-header {
            padding: 8px 12px; background: rgba(0, 255, 65, 0.05);
            border-bottom: 1px solid var(--border-subtle);
            font-size: 12px; font-weight: 600; color: var(--text-accent);
            display: flex; justify-content: space-between; align-items: center;
        }
        .editor-content { flex-grow: 1; }
        .backend-output {
            height: 160px; border-top: 1px solid var(--border-subtle);
            background: var(--terminal-bg); padding: 12px; overflow-y: auto; 
            font-size: 12px; color: var(--terminal-text); white-space: pre-wrap;
        }
        .language-indicator {
            position: fixed; top: 70px; right: 20px; z-index: 100;
            background: rgba(251, 191, 36, 0.9); border: 1px solid var(--accent-warning);
            border-radius: 8px; padding: 8px 12px; font-size: 11px;
            color: var(--bg-deep-space); font-family: var(--font-mono);
            font-weight: 600; text-transform: uppercase;
            box-shadow: var(--glow-warning);
        }
        .context-indicator {
            position: fixed; top: 20px; right: 20px; z-index: 100;
            background: rgba(37, 43, 61, 0.9); border: 1px solid var(--border-glow);
            border-radius: 8px; padding: 8px 12px; font-size: 11px;
            color: var(--text-accent); font-family: var(--font-mono);
        }
        .server-status {
            position: fixed; bottom: 20px; right: 20px; z-index: 100;
            background: rgba(37, 43, 61, 0.9); border: 1px solid var(--success-green);
            border-radius: 8px; padding: 6px 10px; font-size: 10px;
            color: var(--success-green); font-family: var(--font-mono);
            display: none;
        }
        .server-status.active { display: block; }
        pre { 
            background: rgba(12, 17, 23, 0.8); padding: 15px; border-radius: 8px;
            overflow-x: auto; margin: 12px 0; border: 1px solid var(--border-subtle);
        }
        code { 
            font-family: var(--font-mono); font-size: 12px; 
            color: var(--accent-neon); white-space: pre-wrap; word-wrap: break-word;
        }
        .typing-cursor { 
            display: inline-block; width: 2px; height: 1.2em; 
            background: var(--accent-neon); animation: pulse 1s infinite;
            margin-left: 2px; vertical-align: middle;
        }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }
        .spinner { 
            width: 20px; height: 20px; border: 2px solid rgba(0, 255, 136, 0.3);
            border-top: 2px solid var(--accent-neon); border-radius: 50%; 
            animation: spin 1s linear infinite;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
        @media (max-width: 1200px) {
            .container { grid-template-columns: 1fr; grid-template-rows: 50vh 50vh; }
            .chat-panel { border-right: none; border-bottom: 2px solid var(--border-glow); }
            .code-panel { border-left: none; }
            .context-indicator, .server-status, .language-indicator { 
                position: relative; top: 0; right: 0; margin: 8px; 
            }
            .header-buttons { justify-content: center; }
        }
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: var(--bg-space); }
        ::-webkit-scrollbar-thumb { 
            background: var(--accent-cyan); border-radius: 8px; box-shadow: var(--glow-cyan);
        }
    </style>
</head>
<body>
<!-- Indicadores -->
<div class="context-indicator" id="contextIndicator">
    💭 Primeira conversa
</div>

<div class="language-indicator hidden" id="languageIndicator">
    🔧 Linguagem: Universal
</div>

<div class="server-status" id="serverStatus">
    🚀 Executando na porta: 3001
</div>

<div class="container">
    <div class="chat-panel">
        <div class="chat-header">
            <div class="header-title">
                <h1>ARQUITETO UNIVERSAL</h1>
                <p>TODAS AS LINGUAGENS</p>
            </div>
            <div class="header-buttons">
                <button id="newChatBtn" title="🚀 Nova Conversa">✨ NOVO</button>
                <button id="importBtn" title="📦 Importar ZIP">📦 IMPORT</button>
                <button id="downloadBtn" title="💾 Download" class="hidden">💾 ZIP</button>
            </div>
        </div>
        <div class="messages" id="messages"></div>
        <div class="input-area">
            <form class="input-form" id="promptForm">
                <input type="text" class="input-field" id="promptInput" 
                       placeholder="💡 Peça QUALQUER projeto em QUALQUER linguagem..." 
                       autocomplete="off" required>
                <div class="form-buttons">
                    <button type="submit" id="sendBtn">🚀 CRIAR</button>
                    <button type="button" id="stopBtn" class="hidden">⏹️ PARAR</button>
                </div>
            </form>
            <div class="import-area" id="importArea">
                <input type="file" id="fileInput" accept=".zip" />
                <label for="fileInput" class="import-label">
                    📦 Arraste ZIP aqui ou clique para importar
                </label>
            </div>
        </div>
    </div>
    <div class="code-panel">
        <div class="code-header">
            <div class="tab-nav">
                <button class="tab-btn active" data-tab="editor">💻 Código</button>
                <button class="tab-btn" data-tab="preview">🌐 Preview</button>
                <button class="tab-btn" data-tab="terminal">🖥️ Terminal</button>
                <button class="tab-btn" data-tab="backend">⚡ Projeto</button>
            </div>
        </div>
        <div class="code-content">
            <div class="tab-pane active" id="editor-pane">
                <textarea class="code-editor" id="codeEditor" 
                          placeholder="<!-- 🌟 Código será gerado aqui... Agora suporta TODAS as linguagens! C++, C#, Rust, Go, Java, Python, JS e muito mais! -->"></textarea>
            </div>
            <div class="tab-pane" id="preview-pane">
                <div class="preview-loading" id="previewLoading">
                    🌐 Preview disponível para projetos web... Execute o projeto para ver funcionando!
                </div>
                <iframe class="preview-frame hidden" id="previewFrame"></iframe>
            </div>
            <div class="tab-pane" id="terminal-pane">
                <div class="terminal-container">
                    <div class="terminal-header">🖥️ TERMINAL UNIVERSAL</div>
                    <div class="terminal-output" id="terminalOutput"></div>
                    <div class="terminal-input-area">
                        <span class="terminal-prompt">$ </span>
                        <input type="text" class="terminal-input" id="terminalInput" autocomplete="off" 
                               placeholder="Digite comandos: gcc, javac, rustc, go build, npm, pip...">
                    </div>
                </div>
            </div>
            <div class="tab-pane" id="backend-pane">
                <div class="backend-container">
                    <div class="backend-toolbar">
                        <h3>⚡ PROJETO UNIVERSAL</h3>
                        <div class="backend-actions">
                            <button id="installBtn">📦 DEPS</button>
                            <button id="compileBtn">🔧 BUILD</button>
                            <button id="runProjectBtn">▶️ RUN</button>
                            <button id="stopProjectBtn">⏹️ STOP</button>
                        </div>
                    </div>
                    <div class="backend-content">
                        <div class="file-explorer">
                            <div class="file-explorer-header">📁 ARQUIVOS DO PROJETO</div>
                            <div class="file-list" id="fileList"></div>
                        </div>
                        <div class="backend-editor">
                            <div class="editor-header">
                                <span id="currentFileName">📄 Selecione um arquivo</span>
                                <button id="saveFileBtn" style="padding: 4px 8px; border: 1px solid var(--accent-neon); border-radius: 4px; background: transparent; color: var(--accent-neon); cursor: pointer; font-size: 10px;">💾 SALVAR</button>
                            </div>
                            <div class="editor-content">
                                <textarea class="code-editor" id="backendEditor" 
                                          placeholder="// ⚡ Código do arquivo selecionado aparecerá aqui..."></textarea>
                            </div>
                        </div>
                        <div class="backend-output" id="backendOutput">
                            🚀 Output Universal - Compile e execute projetos em qualquer linguagem!
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
</div>

<script>
document.addEventListener('DOMContentLoaded', () => {
    const promptForm = document.getElementById('promptForm');
    const promptInput = document.getElementById('promptInput');
    const sendBtn = document.getElementById('sendBtn');
    const stopBtn = document.getElementById('stopBtn');
    const newChatBtn = document.getElementById('newChatBtn');
    const importBtn = document.getElementById('importBtn');
    const downloadBtn = document.getElementById('downloadBtn');
    const messages = document.getElementById('messages');
    const codeEditor = document.getElementById('codeEditor');
    const previewFrame = document.getElementById('previewFrame');
    const previewLoading = document.getElementById('previewLoading');
    const tabs = document.querySelectorAll('.tab-btn');
    const terminalOutput = document.getElementById('terminalOutput');
    const terminalInput = document.getElementById('terminalInput');
    const contextIndicator = document.getElementById('contextIndicator');
    const languageIndicator = document.getElementById('languageIndicator');
    const serverStatus = document.getElementById('serverStatus');

    // File import
    const fileInput = document.getElementById('fileInput');
    const importArea = document.getElementById('importArea');

    // Backend elements
    const backendEditor = document.getElementById('backendEditor');
    const backendOutput = document.getElementById('backendOutput');
    const fileList = document.getElementById('fileList');
    const currentFileName = document.getElementById('currentFileName');
    const installBtn = document.getElementById('installBtn');
    const compileBtn = document.getElementById('compileBtn');
    const runProjectBtn = document.getElementById('runProjectBtn');
    const stopProjectBtn = document.getElementById('stopProjectBtn');
    const saveFileBtn = document.getElementById('saveFileBtn');

    let isLoading = false;
    let currentProject = null;
    let currentFile = null;
    let projectFiles = {};
    let conversationHistory = [];
    let projectId = null;
    let serverRunning = false;
    let currentLanguage = "universal";

    const initialMessage = {
        title: "🚀 Genesis Universal Edition ATIVADO",
        icon: "🤖", 
        content: `Salve! Eu sou o <strong>Arquiteto Genesis Universal Edition</strong>! Programo em TODAS as linguagens do mundo! 🌟<br><br><strong>🔥 PODER ABSOLUTO:</strong><br>🖥️ <strong>C/C++:</strong> Sistemas, jogos, drivers, SO completos!<br>⚡ <strong>C#:</strong> Desktop, web, games, enterprise!<br>☕ <strong>Java:</strong> Android, web services, big systems!<br>🦀 <strong>Rust:</strong> Sistemas seguros, performance extrema!<br>🐹 <strong>Go:</strong> Microserviços, sistemas distribuídos!<br>🔧 <strong>Assembly:</strong> Baixo nível, embedded, drivers!<br>🐍 <strong>Python:</strong> IA, web, scripts, ciência!<br>⚡ <strong>JavaScript/TS:</strong> Web, Node.js, mobile!<br><br><strong>💥 PROJETOS ÉPICOS:</strong><br>• <strong>Sistema Operacional completo</strong><br>• <strong>Minecraft clone em C++</strong><br>• <strong>Compilador de linguagem</strong><br>• <strong>Engine de jogos 3D</strong><br>• <strong>Blockchain do zero</strong><br>• <strong>IA neural network</strong><br><br><strong>🎯 SEM LIMITAÇÕES! Peça QUALQUER coisa em QUALQUER linguagem!</strong>`
    };

    function updateIndicators(context, language) {
        contextIndicator.textContent = `💭 ${context}`;
        if (language && language !== "universal") {
            languageIndicator.textContent = `🔧 ${language.toUpperCase()}`;
            languageIndicator.classList.remove('hidden');
        } else {
            languageIndicator.classList.add('hidden');
        }
    }

    function updateServerStatus(running, port = null) {
        serverRunning = running;
        if (running && port) {
            serverStatus.textContent = `🚀 Executando na porta: ${port}`;
            serverStatus.classList.add('active');
        } else {
            serverStatus.classList.remove('active');
        }
    }

    function initializeChat() {
        messages.innerHTML = '';
        createAgentMessage(initialMessage.title, initialMessage.icon, initialMessage.content, false);
        codeEditor.value = '';
        updatePreview();
        terminalOutput.innerHTML = `Genesis Universal Terminal - Compile e execute em qualquer linguagem! 🌟\\n\\n`;

        // Reset
        backendEditor.value = '';
        backendOutput.innerHTML = '🚀 Output Universal - Compile e execute projetos em qualquer linguagem!';
        fileList.innerHTML = '';
        currentFileName.textContent = '📄 Selecione um arquivo';
        currentProject = null;
        currentFile = null;
        projectFiles = {};
        conversationHistory = [];
        projectId = null;
        currentLanguage = "universal";
        downloadBtn.classList.add('hidden');
        updateServerStatus(false);

        updateIndicators('Primeira conversa', 'universal');
        promptInput.focus();
        setLoading(false);
    }

    function setLoading(loading) {
        isLoading = loading;
        promptInput.disabled = loading;
        sendBtn.classList.toggle('hidden', loading);
        stopBtn.classList.toggle('hidden', !loading);
        sendBtn.innerHTML = loading ? '<div class="spinner"></div>' : '🚀 CRIAR';
    }

    function addUserMessage(content) {
        const messageHtml = `
            <div class="message user">
                <div class="avatar">👤</div>
                <div class="message-content">
                    <div class="message-text">${content}</div>
                </div>
            </div>`;
        messages.insertAdjacentHTML('beforeend', messageHtml);
        messages.scrollTop = messages.scrollHeight;

        conversationHistory.push(content);
        if (conversationHistory.length > 10) {
            conversationHistory = conversationHistory.slice(-10);
        }
    }

    function createAgentMessage(title, icon, initialContent = '', showCursor = true) {
        const messageId = `agent-msg-${Date.now()}`;
        const cursorHtml = showCursor ? '<span class="typing-cursor"></span>' : '';
        const messageHtml = `
            <div class="message" id="${messageId}">
                <div class="avatar">${icon}</div>
                <div class="message-content">
                    <div class="message-title">${title}</div>
                    <div class="message-text">${initialContent}${cursorHtml}</div>
                </div>
            </div>`;
        messages.insertAdjacentHTML('beforeend', messageHtml);
        messages.scrollTop = messages.scrollHeight;
        return document.querySelector(`#${messageId} .message-text`);
    }

    function updatePreview() {
        if (serverRunning && projectId) {
            previewFrame.src = `http://localhost:3001`;
            previewFrame.classList.remove('hidden');
            previewLoading.classList.add('hidden');
        } else if (codeEditor.value.trim() && (codeEditor.value.includes('<html') || codeEditor.value.includes('<!DOCTYPE'))) {
            previewFrame.srcdoc = codeEditor.value;
            previewFrame.classList.remove('hidden');
            previewLoading.classList.add('hidden');
        } else {
            previewFrame.classList.add('hidden');
            previewLoading.classList.remove('hidden');
        }
    }

    function displayFileStructure(files) {
        fileList.innerHTML = '';

        const filesByFolder = {};
        Object.keys(files).forEach(filePath => {
            const parts = filePath.split('/');
            if (parts.length === 1) {
                if (!filesByFolder['root']) filesByFolder['root'] = [];
                filesByFolder['root'].push(filePath);
            } else {
                const folder = parts[0];
                if (!filesByFolder[folder]) filesByFolder[folder] = [];
                filesByFolder[folder].push(filePath);
            }
        });

        Object.keys(filesByFolder).sort().forEach(folderName => {
            if (folderName !== 'root') {
                const folderHeader = document.createElement('div');
                folderHeader.className = 'file-item folder';
                folderHeader.textContent = `📁 ${folderName}/`;
                fileList.appendChild(folderHeader);
            }

            filesByFolder[folderName].forEach(filePath => {
                const fileItem = document.createElement('div');
                fileItem.className = 'file-item';
                const fileName = filePath.includes('/') ? filePath.split('/').pop() : filePath;
                const indent = folderName !== 'root' ? '  ' : '';

                // Ícones universais por extensão
                let icon = '📄';
                const ext = fileName.split('.').pop()?.toLowerCase();
                const iconMap = {
                    'cpp': '⚡', 'cc': '⚡', 'cxx': '⚡', 'c++': '⚡',
                    'c': '🔧', 'h': '📋', 'hpp': '📋',
                    'cs': '💎', 'csproj': '⚙️',
                    'java': '☕', 'jar': '📦',
                    'py': '🐍', 'pyw': '🐍',
                    'js': '⚡', 'ts': '📘', 'json': '📋',
                    'rs': '🦀', 'toml': '📋',
                    'go': '🐹', 'mod': '📋',
                    'html': '🌐', 'htm': '🌐',
                    'css': '🎨', 'scss': '🎨',
                    'php': '🐘', 'rb': '💎',
                    'swift': '🦢', 'kt': '🔷',
                    'asm': '🔩', 's': '🔩',
                    'sql': '🗄️', 'db': '🗄️',
                    'md': '📖', 'txt': '📝',
                    'xml': '📋', 'yaml': '📋', 'yml': '📋',
                    'sh': '🖥️', 'bat': '🖥️', 'ps1': '🖥️',
                    'exe': '⚙️', 'dll': '🔧', 'so': '🔧',
                    'zip': '📦', 'tar': '📦', 'gz': '📦'
                };
                icon = iconMap[ext] || '📄';

                fileItem.textContent = `${indent}${icon} ${fileName}`;
                fileItem.dataset.fullPath = filePath;
                fileItem.addEventListener('click', () => selectFile(filePath));
                fileList.appendChild(fileItem);
            });
        });
    }

    function loadProjectFiles(projectData) {
        projectFiles = projectData.files;
        currentProject = projectData;
        projectId = Date.now().toString();
        currentLanguage = projectData.language || detectLanguageFromFiles(projectFiles);

        displayFileStructure(projectFiles);

        // Seleciona arquivo principal baseado na linguagem
        const mainFiles = {
            'cpp': ['main.cpp', 'main.cc', 'main.cxx'],
            'c': ['main.c'],
            'csharp': ['Program.cs', 'Main.cs'],
            'java': ['Main.java', 'Application.java'],
            'python': ['main.py', 'app.py'],
            'javascript': ['index.js', 'app.js', 'main.js'],
            'rust': ['main.rs', 'lib.rs'],
            'go': ['main.go'],
            'php': ['index.php', 'main.php']
        };

        let firstFile = null;
        const possibleMains = mainFiles[currentLanguage] || ['main.*'];

        for (const mainFile of possibleMains) {
            if (projectFiles[mainFile]) {
                firstFile = mainFile;
                break;
            }
        }

        if (!firstFile) {
            firstFile = Object.keys(projectFiles)[0];
        }

        if (firstFile) {
            selectFile(firstFile);
            codeEditor.value = projectFiles[firstFile];
        }

        downloadBtn.classList.remove('hidden');
        updateIndicators(`Projeto: ${projectData.project_name || 'Ativo'}`, currentLanguage);
        updatePreview();
    }

    function detectLanguageFromFiles(files) {
        const extensions = Object.keys(files).map(f => f.split('.').pop()?.toLowerCase()).filter(Boolean);

        const langMap = {
            'cpp': ['cpp', 'cc', 'cxx', 'c++'],
            'c': ['c'],
            'csharp': ['cs'],
            'java': ['java'],
            'python': ['py'],
            'javascript': ['js'],
            'typescript': ['ts'],
            'rust': ['rs'],
            'go': ['go'],
            'php': ['php'],
            'ruby': ['rb'],
            'swift': ['swift'],
            'kotlin': ['kt'],
            'assembly': ['asm', 's']
        };

        for (const [lang, exts] of Object.entries(langMap)) {
            if (extensions.some(ext => exts.includes(ext))) {
                return lang;
            }
        }

        return 'unknown';
    }

    function selectFile(filePath) {
        document.querySelectorAll('.file-item').forEach(item => item.classList.remove('active'));

        const targetItem = Array.from(fileList.children).find(item => item.dataset.fullPath === filePath);
        if (targetItem) targetItem.classList.add('active');

        currentFile = filePath;
        currentFileName.textContent = `📄 ${filePath}`;
        backendEditor.value = projectFiles[filePath] || '';
    }

    // File Import Handlers
    importBtn.addEventListener('click', () => fileInput.click());

    fileInput.addEventListener('change', handleFileImport);

    importArea.addEventListener('dragover', (e) => {
        e.preventDefault();
        importArea.classList.add('dragover');
    });

    importArea.addEventListener('dragleave', (e) => {
        e.preventDefault();
        importArea.classList.remove('dragover');
    });

    importArea.addEventListener('drop', (e) => {
        e.preventDefault();
        importArea.classList.remove('dragover');
        const files = e.dataTransfer.files;
        if (files.length > 0) {
            handleFileImport({ target: { files: files } });
        }
    });

    async function handleFileImport(event) {
        const file = event.target.files[0];
        if (!file) return;

        if (file.name.endsWith('.zip')) {
            const formData = new FormData();
            formData.append('file', file);

            try {
                backendOutput.innerHTML += '📦 Importando projeto...\\n';

                const response = await fetch('/import-project', {
                    method: 'POST',
                    body: formData
                });

                const result = await response.json();

                if (result.success) {
                    loadProjectFiles(result.project_data);
                    backendOutput.innerHTML += `✅ Projeto importado! ${Object.keys(result.project_data.files).length} arquivos | Linguagem: ${result.project_data.language?.toUpperCase()}\\n`;
                    createAgentMessage('📦 Projeto Importado', '🎉', 
                        `Projeto ${result.project_data.language?.toUpperCase()} importado! ${Object.keys(result.project_data.files).length} arquivos carregados. Use os botões BUILD e RUN!`, false);
                } else {
                    backendOutput.innerHTML += `❌ Erro: ${result.error}\\n`;
                }
            } catch (error) {
                backendOutput.innerHTML += `❌ Erro: ${error.message}\\n`;
            }

            backendOutput.scrollTop = backendOutput.scrollHeight;
        }
    }

    // Função principal de processamento (mesma lógica anterior)
    function processWithContext(prompt) {
        let agentElements = {};
        let currentAgent = 0;
        let buffers = { 1: "", 2: "", 3: "", "modifier": "" };

        fetch('/stream-context', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'text/event-stream'
            },
            body: JSON.stringify({ 
                prompt: prompt,
                context: {
                    conversation_history: conversationHistory,
                    current_project: currentProject,
                    current_code: codeEditor.value
                }
            })
        })
        .then(response => {
            if (!response.ok) throw new Error(`HTTP ${response.status}`);

            const reader = response.body.getReader();
            const decoder = new TextDecoder();

            function readStream() {
                return reader.read().then(({ done, value }) => {
                    if (done) {
                        setLoading(false);
                        return;
                    }

                    const chunk = decoder.decode(value);
                    const lines = chunk.split('\\n');

                    for (const line of lines) {
                        if (line.startsWith('data: ')) {
                            try {
                                const data = JSON.parse(line.slice(6));
                                handleStreamData(data);
                            } catch (e) {
                                console.warn('Failed to parse SSE data:', line);
                            }
                        }
                    }

                    return readStream();
                });
            }

            function handleStreamData(data) {
                if (data.type === 'error') {
                    createAgentMessage("❌ Erro no Sistema", "🔥", data.content, false);
                    setLoading(false);
                    return;
                }

                if (data.type === 'simple_response') {
                    createAgentMessage("💬 Assistente", "🤖", data.content, false);
                    setLoading(false);
                    return;
                }

                if (data.agent && data.agent !== currentAgent) {
                    if (agentElements[currentAgent]) {
                        agentElements[currentAgent].querySelector('.typing-cursor')?.remove();
                    }
                    currentAgent = data.agent;
                    const agentConfigs = {
                        1: { title: '📜 Blueprint Universal', icon: '🎯' },
                        2: { title: '🏗️ Arquitetura Multi-linguagem', icon: '🔧' },
                        3: { title: '⚡ Desenvolvimento Universal', icon: '🚀' },
                        'modifier': { title: '🛠️ Modificação Universal', icon: '🧠' }
                    };
                    const config = agentConfigs[currentAgent];
                    if (config) {
                        agentElements[currentAgent] = createAgentMessage(config.title, config.icon);
                    }
                }

                if (data.content && agentElements[currentAgent]) {
                    buffers[currentAgent] += data.content;

                    if (currentAgent === 'modifier') {
                        if (data.content.includes('{')) {
                            try {
                                const projectData = JSON.parse(buffers[currentAgent]);
                                loadProjectFiles(projectData);
                                agentElements[currentAgent].innerHTML = `🧠 Projeto modificado! <strong>${Object.keys(projectData.files).length}</strong> arquivos em <strong>${(projectData.language || 'Universal').toUpperCase()}</strong>.<span class="typing-cursor"></span>`;
                            } catch (e) {
                                agentElements[currentAgent].innerHTML = `🛠️ Modificando... <strong>${buffers[currentAgent].length}</strong> chars.<span class="typing-cursor"></span>`;
                            }
                        } else {
                            codeEditor.value = buffers[currentAgent];
                            agentElements[currentAgent].innerHTML = `🛠️ Código modificado! <strong>${codeEditor.value.length}</strong> caracteres.<span class="typing-cursor"></span>`;
                        }
                    } else if (currentAgent === 3) {
                        try {
                            const projectData = JSON.parse(buffers[currentAgent]);
                            loadProjectFiles(projectData);
                            const lang = (projectData.language || 'Universal').toUpperCase();
                            agentElements[currentAgent].innerHTML = `⚡ Projeto ${lang} criado! <strong>${Object.keys(projectData.files).length}</strong> arquivos.<span class="typing-cursor"></span>`;
                        } catch (e) {
                            agentElements[currentAgent].innerHTML = `🚀 Gerando... <strong>${buffers[currentAgent].length}</strong> chars.<span class="typing-cursor"></span>`;
                        }
                    } else {
                        agentElements[currentAgent].innerHTML = buffers[currentAgent].replace(/\\n/g, '<br>') + '<span class="typing-cursor"></span>';
                    }
                }

                if (data.done && agentElements[currentAgent]) {
                    agentElements[currentAgent].querySelector('.typing-cursor')?.remove();
                }

                if (data.type === 'complete') {
                    const isModification = currentAgent === 'modifier';
                    const title = isModification ? "✅ Modificação Universal Concluída" : "✅ Projeto Universal Concluído";
                    const content = isModification ? 
                        "Modificação aplicada! 🧠 Código evoluído com contexto!" :
                        "Projeto criado! ⚡ Use DEPS → BUILD → RUN para executar!";

                    createAgentMessage(title, "🎉", content, false);
                    updatePreview();
                    setLoading(false);
                }

                messages.scrollTop = messages.scrollHeight;
            }

            return readStream();
        })
        .catch(error => {
            console.error('Stream error:', error);
            createAgentMessage("❌ Erro de Conexão", "📡", `Erro: ${error.message}`, false);
            setLoading(false);
        });
    }

    // Event Listeners principais
    promptForm.addEventListener('submit', (e) => {
        e.preventDefault();
        if (isLoading) return;
        const prompt = promptInput.value.trim();
        if (!prompt) return;

        addUserMessage(prompt);
        promptInput.value = '';
        setLoading(true);
        processWithContext(prompt);
    });

    newChatBtn.addEventListener('click', initializeChat);

    downloadBtn.addEventListener('click', async () => {
        if (!currentProject) return;

        try {
            const response = await fetch('/download-project', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(currentProject)
            });

            if (response.ok) {
                const blob = await response.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `${currentProject.project_name || 'projeto'}.zip`;
                document.body.appendChild(a);
                a.click();
                window
                a.click();
                window.URL.revokeObjectURL(url);
                document.body.removeChild(a);

                backendOutput.innerHTML += '📦 Download iniciado!\\n';
            }
        } catch (error) {
            backendOutput.innerHTML += `❌ Erro no download: ${error.message}\\n`;
        }
    });

    // Tabs
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
            document.getElementById(`${tab.dataset.tab}-pane`).classList.add('active');
            if (tab.dataset.tab === 'preview') updatePreview();
        });
    });

    codeEditor.addEventListener('input', () => {
        clearTimeout(this.debounceTimer);
        this.debounceTimer = setTimeout(updatePreview, 300);
    });

    // Terminal Universal
    terminalInput.addEventListener('keydown', async (e) => {
        if (e.key === 'Enter') {
            const command = terminalInput.value.trim();
            if (!command) return;

            terminalOutput.innerHTML += `$ ${command}\\n`;

            try {
                const response = await fetch('/terminal-command', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ 
                        command: command,
                        project_id: projectId,
                        language: currentLanguage
                    })
                });

                const result = await response.json();
                terminalOutput.innerHTML += result.output + '\\n';

                if (!result.success && result.return_code !== 0) {
                    terminalOutput.innerHTML += `❌ Comando falhou (código: ${result.return_code})\\n`;
                }

            } catch (error) {
                terminalOutput.innerHTML += `❌ Erro: ${error.message}\\n`;
            }

            terminalInput.value = '';
            terminalOutput.scrollTop = terminalOutput.scrollHeight;
        }
    });

    // Backend Universal Actions
    installBtn.addEventListener('click', async () => {
        if (!currentProject) {
            backendOutput.innerHTML += '❌ Nenhum projeto carregado!\\n';
            return;
        }

        backendOutput.innerHTML += '📦 Instalando dependências...\\n';

        try {
            const response = await fetch('/install-dependencies', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    project_data: currentProject, 
                    project_id: projectId,
                    language: currentLanguage
                })
            });

            const result = await response.json();

            if (result.success) {
                backendOutput.innerHTML += `✅ Dependências instaladas!\\n${result.output}\\n`;
            } else {
                backendOutput.innerHTML += `❌ Erro na instalação:\\n${result.output}\\n`;
            }
        } catch (error) {
            backendOutput.innerHTML += `❌ Erro: ${error.message}\\n`;
        }

        backendOutput.scrollTop = backendOutput.scrollHeight;
    });

    compileBtn.addEventListener('click', async () => {
        if (!currentProject) {
            backendOutput.innerHTML += '❌ Nenhum projeto carregado!\\n';
            return;
        }

        backendOutput.innerHTML += '🔧 Compilando projeto...\\n';

        try {
            const response = await fetch('/compile-project', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    project_data: currentProject, 
                    project_id: projectId,
                    language: currentLanguage
                })
            });

            const result = await response.json();

            if (result.success) {
                backendOutput.innerHTML += `✅ Compilação bem-sucedida!\\n${result.output}\\n`;
            } else {
                backendOutput.innerHTML += `❌ Erro na compilação:\\n${result.output}\\n`;
            }
        } catch (error) {
            backendOutput.innerHTML += `❌ Erro: ${error.message}\\n`;
        }

        backendOutput.scrollTop = backendOutput.scrollHeight;
    });

    runProjectBtn.addEventListener('click', async () => {
        if (!currentProject) {
            backendOutput.innerHTML += '❌ Nenhum projeto carregado!\\n';
            return;
        }

        backendOutput.innerHTML += '🚀 Executando projeto...\\n';

        try {
            const response = await fetch('/run-universal-project', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    project_data: currentProject, 
                    project_id: projectId,
                    language: currentLanguage
                })
            });

            const result = await response.json();

            if (result.success) {
                backendOutput.innerHTML += `✅ ${result.message}\\n`;
                if (result.port) {
                    updateServerStatus(true, result.port);
                    setTimeout(updatePreview, 2000);
                }
                if (result.output) {
                    backendOutput.innerHTML += `Output:\\n${result.output}\\n`;
                }
            } else {
                backendOutput.innerHTML += `❌ ${result.error}\\n`;
            }
        } catch (error) {
            backendOutput.innerHTML += `❌ Erro: ${error.message}\\n`;
        }

        backendOutput.scrollTop = backendOutput.scrollHeight;
    });

    stopProjectBtn.addEventListener('click', async () => {
        if (!projectId) return;

        try {
            const response = await fetch('/stop-universal-project', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ project_id: projectId })
            });

            const result = await response.json();

            if (result.success) {
                backendOutput.innerHTML += '⏹️ Projeto parado.\\n';
                updateServerStatus(false);
                updatePreview();
            }
        } catch (error) {
            backendOutput.innerHTML += `❌ Erro: ${error.message}\\n`;
        }

        backendOutput.scrollTop = backendOutput.scrollHeight;
    });

    saveFileBtn.addEventListener('click', async () => {
        if (!currentFile || !projectFiles[currentFile]) return;

        projectFiles[currentFile] = backendEditor.value;

        // Salva também no editor principal se for o arquivo sendo visualizado
        if (codeEditor.value === projectFiles[currentFile] || codeEditor.value.trim() === '') {
            codeEditor.value = backendEditor.value;
        }

        backendOutput.innerHTML += `💾 Arquivo ${currentFile} salvo!\\n`;
        backendOutput.scrollTop = backendOutput.scrollHeight;

        // Atualiza preview se necessário
        updatePreview();
    });

    initializeChat();
});
</script>
</body>
</html>
"""

# === FLASK APP UNIVERSAL ===
app = Flask(__name__)
app.secret_key = 'genesis_universal_secret_key'
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024  # 200MB max file size


@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route('/import-project', methods=['POST'])
def import_project():
    """Importa projeto de arquivo ZIP."""
    try:
        if 'file' not in request.files:
            return jsonify({"success": False, "error": "Nenhum arquivo enviado"})

        file = request.files['file']
        if file.filename == '' or not allowed_file(file.filename):
            return jsonify({"success": False, "error": "Arquivo ZIP inválido"})

        filename = secure_filename(file.filename)
        temp_dir = PROJECTS_DIR / "temp_import"
        temp_dir.mkdir(exist_ok=True)

        zip_path = temp_dir / filename
        file.save(zip_path)

        extract_dir = temp_dir / "extracted"
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        extract_dir.mkdir()

        project_data = extract_zip_project(zip_path, extract_dir)

        shutil.rmtree(temp_dir)

        return jsonify({"success": True, "project_data": project_data})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/terminal-command', methods=['POST'])
def terminal_command():
    """Executa comando no terminal universal."""
    data = request.get_json()
    command = data.get('command', '')
    project_id = data.get('project_id')
    language = data.get('language', 'unknown')

    if not command:
        return jsonify({"success": False, "output": "Comando vazio", "return_code": 1})

    # Determina diretório de trabalho
    cwd = None
    if project_id and project_id in [p.name for p in PROJECTS_DIR.iterdir() if p.is_dir()]:
        cwd = PROJECTS_DIR / project_id

    # Adiciona sugestões baseadas na linguagem se comando não for reconhecido
    if command in ['help', '?']:
        help_text = f"""
Comandos disponíveis para {language.upper()}:

GERAL:
  ls, dir          - Lista arquivos
  pwd              - Mostra diretório atual
  cat <arquivo>    - Mostra conteúdo do arquivo
  clear            - Limpa terminal

"""

        if language == 'cpp' or language == 'c':
            help_text += """C/C++:
  g++ main.cpp -o programa    - Compila C++
  gcc main.c -o programa      - Compila C  
  ./programa                  - Executa programa
  make                        - Usa Makefile
  cmake .                     - Configura CMake
"""
        elif language == 'csharp':
            help_text += """C#:
  csc Program.cs              - Compila C#
  mono Program.exe            - Executa com Mono
  dotnet build                - Build .NET
  dotnet run                  - Executa .NET
"""
        elif language == 'java':
            help_text += """Java:
  javac Main.java             - Compila Java
  java Main                   - Executa Java
  mvn compile                 - Maven compile
  gradle build                - Gradle build
"""
        elif language == 'rust':
            help_text += """Rust:
  rustc main.rs               - Compila Rust
  cargo build                 - Build com Cargo
  cargo run                   - Compila e executa
  ./main                      - Executa binário
"""
        elif language == 'go':
            help_text += """Go:
  go build main.go            - Compila Go
  go run main.go              - Executa Go
  ./main                      - Executa binário
"""
        elif language == 'python':
            help_text += """Python:
  python main.py              - Executa Python
  pip install -r requirements.txt  - Instala deps
  python -m venv venv         - Cria ambiente virtual
"""
        elif language == 'javascript':
            help_text += """JavaScript/Node.js:
  node index.js               - Executa Node.js
  npm install                 - Instala dependências
  npm start                   - Inicia aplicação
  npm run build               - Build do projeto
"""

        return jsonify({"success": True, "output": help_text, "return_code": 0})

    result = execute_universal_command(command, cwd)
    return jsonify(result)


@app.route('/install-dependencies', methods=['POST'])
def install_dependencies_endpoint():
    """Instala dependências baseado na linguagem."""
    data = request.get_json()
    project_data = data.get('project_data')
    project_id = data.get('project_id')
    language = data.get('language', 'unknown')

    try:
        if not project_data or not project_id:
            return jsonify({"success": False, "output": "Dados inválidos"})

        project_path, _ = create_project_files(project_data, project_id)
        result = install_project_dependencies(project_path, language)
        return jsonify(result)

    except Exception as e:
        return jsonify({"success": False, "output": str(e)})


@app.route('/compile-project', methods=['POST'])
def compile_project_endpoint():
    """Compila projeto baseado na linguagem."""
    data = request.get_json()
    project_data = data.get('project_data')
    project_id = data.get('project_id')
    language = data.get('language', 'unknown')

    try:
        if not project_data or not project_id:
            return jsonify({"success": False, "output": "Dados inválidos"})

        project_path, _ = create_project_files(project_data, project_id)

        # Para linguagens interpretadas, não precisa compilar
        interpreted_langs = ['python', 'javascript', 'php', 'ruby']
        if language in interpreted_langs:
            return jsonify(
                {"success": True, "output": f"{language.title()} não precisa de compilação - linguagem interpretada."})

        # Compila baseado na linguagem
        lang_config = LANGUAGE_CONFIGS.get(language, {})
        if not lang_config or "compile_cmd" not in lang_config:
            return jsonify({"success": False, "output": f"Compilação não configurada para {language}"})

        # Encontra arquivo principal
        main_file = lang_config.get("main_file", "main")
        main_path = os.path.join(project_path, main_file)

        if not os.path.exists(main_path):
            # Procura arquivo similar
            for file in os.listdir(project_path):
                if any(file.endswith(ext) for ext in lang_config.get("extensions", [])):
                    main_file = file
                    break

        # Executa compilação
        output_name = "program"
        compile_cmd = lang_config["compile_cmd"].format(
            input=main_file,
            output=output_name,
            main_class=Path(main_file).stem
        )

        result = execute_universal_command(compile_cmd, cwd=project_path)
        return jsonify(result)

    except Exception as e:
        return jsonify({"success": False, "output": str(e)})


@app.route('/run-universal-project', methods=['POST'])
def run_universal_project_endpoint():
    """Executa projeto em qualquer linguagem."""
    data = request.get_json()
    project_data = data.get('project_data')
    project_id = data.get('project_id')
    language = data.get('language', 'unknown')

    try:
        if not project_data or not project_id:
            return jsonify({"success": False, "error": "Dados inválidos"})

        project_path, _ = create_project_files(project_data, project_id)

        # Verifica se é projeto web (que precisa de servidor)
        web_languages = ['python', 'javascript', 'php', 'java', 'go', 'csharp']
        has_web_files = any(
            'server' in filename.lower() or 'app' in filename.lower() or
            'index.html' in filename.lower() or 'web' in filename.lower()
            for filename in project_data["files"].keys()
        )

        if language in web_languages and has_web_files:
            # Inicia como servidor web
            success, message = start_universal_server(project_path, project_id, language)
            if success:
                port = running_processes[project_id]["port"]
                return jsonify({"success": True, "message": message, "port": port})
            else:
                return jsonify({"success": False, "error": message})
        else:
            # Executa como programa normal
            result = compile_and_run_project(project_path, language)
            if result["success"]:
                return jsonify({
                    "success": True,
                    "message": "Programa executado com sucesso",
                    "output": result["output"]
                })
            else:
                return jsonify({"success": False, "error": result["output"]})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/stop-universal-project', methods=['POST'])
def stop_universal_project_endpoint():
    """Para execução do projeto."""
    data = request.get_json()
    project_id = data.get('project_id')

    success = stop_universal_server(project_id)
    return jsonify({"success": success})


@app.route('/download-project', methods=['POST'])
def download_project():
    """Gera e envia ZIP do projeto."""
    project_data = request.get_json()

    try:
        project_id = str(uuid.uuid4())[:8]
        project_path, _ = create_project_files(project_data, project_id)
        project_name = project_data.get('project_name', 'projeto')

        zip_path = create_project_zip(project_path, project_name)

        return send_file(zip_path,
                         as_attachment=True,
                         download_name=f"{project_name}.zip",
                         mimetype='application/zip')

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/stream-context', methods=['POST'])
def stream_generate_context():
    """Endpoint principal com inteligência contextual universal."""
    data = request.get_json()
    prompt = data.get('prompt', '').strip()
    context = data.get('context', {})

    if not prompt:
        def error_gen():
            yield f"data: {json.dumps({'type': 'error', 'content': 'Prompt vazio.'})}\n\n"

        return Response(error_gen(), mimetype='text/event-stream')

    def generate_pipeline():
        try:
            update_session_context(prompt, context.get('current_project'), context.get('current_code'))
            context_summary = get_context_summary()

            # === AGENTE 0: FILTRO UNIVERSAL ===
            agent0_prompt = PROMPT_AGENTE_0.format(
                last_project_info=context_summary['last_project_info'],
                has_last_code='Sim' if context_summary['has_last_code'] else 'Não',
                conversation_summary=context_summary['conversation_summary']
            )

            model_0 = genai.GenerativeModel(
                MODELO_FLASH,
                system_instruction=agent0_prompt,
                generation_config={"response_mime_type": "application/json"}
            )
            response_0_text = model_0.generate_content(prompt).text
            filter_result = json.loads(response_0_text)
            classification = filter_result.get("classification")

            if classification in ["greeting", "chit_chat"]:
                yield f"data: {json.dumps({'type': 'simple_response', 'content': filter_result.get('response')})}\n\n"
                return

            # === PIPELINE DE MODIFICAÇÃO UNIVERSAL ===
            if classification == "modification":
                modifier_prompt = PROMPT_AGENTE_MODIFICADOR.format(
                    project_context=json.dumps(context.get('current_project', {}), indent=2) if context.get(
                        'current_project') else 'Nenhum projeto atual',
                    current_code=context.get('current_code', 'Nenhum código atual')
                )

                full_modified = ""
                modifier_stream = stream_agent(MODELO_PRO, modifier_prompt, prompt)
                for chunk in modifier_stream:
                    full_modified += chunk
                    yield f"data: {json.dumps({'agent': 'modifier', 'content': chunk})}\n\n"

                if full_modified:
                    try:
                        project_data = json.loads(full_modified)
                        update_session_context(prompt, project_data)
                    except:
                        update_session_context(prompt, None, full_modified)

                yield f"data: {json.dumps({'agent': 'modifier', 'done': True})}\n\n"
                yield f"data: {json.dumps({'type': 'complete'})}\n\n"
                return

            # === PIPELINE DE CRIAÇÃO UNIVERSAL ===

            # --- AGENTE 1: BLUEPRINT UNIVERSAL ---
            agent1_prompt = PROMPT_AGENTE_1.format(
                conversation_context=context_summary['conversation_summary']
            )

            full_blueprint = ""
            agent1_stream = stream_agent(MODELO_FLASH, agent1_prompt, prompt)
            for chunk in agent1_stream:
                full_blueprint += chunk
                yield f"data: {json.dumps({'agent': 1, 'content': chunk})}\n\n"
            yield f"data: {json.dumps({'agent': 1, 'done': True})}\n\n"

            # --- AGENTE 2: ARQUITETURA UNIVERSAL ---
            agent2_prompt = PROMPT_AGENTE_2.format(
                blueprint_context=full_blueprint[:500] + '...' if len(full_blueprint) > 500 else full_blueprint
            )

            full_plan = ""
            agent2_stream = stream_agent(MODELO_FLASH, agent2_prompt, full_blueprint, is_json=True)
            for chunk in agent2_stream:
                full_plan += chunk
                yield f"data: {json.dumps({'agent': 2, 'content': chunk})}\n\n"

            try:
                json.loads(full_plan)
            except json.JSONDecodeError:
                if not full_plan.endswith('}'):
                    full_plan += '}'

            yield f"data: {json.dumps({'agent': 2, 'done': True})}\n\n"

            # --- AGENTE 3: DESENVOLVIMENTO UNIVERSAL ---
            agent3_prompt = PROMPT_AGENTE_3.format(
                project_context=prompt,
                technical_specs=full_plan[:1000] + '...' if len(full_plan) > 1000 else full_plan
            )

            context_full = f"""PROMPT ORIGINAL:
{prompt}

BLUEPRINT UNIVERSAL:
{full_blueprint}

PLANO TÉCNICO:
{full_plan}

CONTEXTO DA CONVERSA:
{context_summary['conversation_summary']}

INSTRUÇÕES SUPREMAS:
Crie um projeto COMPLETO e FUNCIONAL em QUALQUER linguagem apropriada para o pedido.
NUNCA diga que não pode fazer algo.
SEMPRE implemente tudo que foi pedido.
Use a linguagem MAIS ADEQUADA para o projeto.
Código deve ser PRODUCTION-READY e totalmente funcional.
"""

            full_project = ""
            agent3_stream = stream_agent(MODELO_PRO, agent3_prompt, context_full, is_json=True)
            for chunk in agent3_stream:
                full_project += chunk
                yield f"data: {json.dumps({'agent': 3, 'content': chunk})}\n\n"

            if full_project:
                try:
                    project_data = json.loads(full_project)
                    update_session_context(prompt, project_data)
                except:
                    pass

            yield f"data: {json.dumps({'type': 'complete'})}\n\n"

        except Exception as e:
            error_message = f"Erro no pipeline universal: {str(e)}"
            print(f"💥 {error_message}")
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'content': error_message})}\n\n"

    return Response(generate_pipeline(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'X-Accel-Buffering': 'no',
        'Access-Control-Allow-Origin': '*'
    })


if __name__ == '__main__':
    print("=" * 90)
    print("      🚀 ARQUITETO GENESIS - UNIVERSAL EDITION 🚀")
    print("=" * 90)
    print("🌟  LINGUAGENS SUPORTADAS: TODAS! C, C++, C#, Java, Python, JS, Rust, Go, Assembly...")
    print("🖥️  PROJETOS ÉPICOS: Sistemas Operacionais, Jogos, Compiladores, Engines, IAs...")
    print("📦  IMPORTAR ZIP: Carregue projetos existentes em qualquer linguagem!")
    print("🖥️  TERMINAL UNIVERSAL: Compile com gcc, rustc, javac, go build...")
    print("⚡  COMPILAÇÃO REAL: Build automático baseado na linguagem!")
    print("🌐  EXECUÇÃO COMPLETA: Rode aplicações desktop, web, console...")
    print("💾  DOWNLOAD ZIP: Projetos completos organizados!")
    print("🧠  INTELIGÊNCIA CONTEXTUAL: A IA lembra e evolui projetos!")
    print("🔥  SEM LIMITAÇÕES: Peça QUALQUER projeto em QUALQUER linguagem!")
    print("=" * 90)
    print("🌐  Acesse: http://127.0.0.1:5000")
    print("💡  Exemplo: 'Crie um clone do Minecraft em C++ com OpenGL'")
    print("💡  Exemplo: 'Faça um compilador de linguagem em Rust'")
    print("💡  Exemplo: 'Desenvolva um sistema operacional em C e Assembly'")
    print("=" * 90)

    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)
