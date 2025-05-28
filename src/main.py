import os
import json
import requests
import base64
import time
from flask import Flask, request, jsonify, send_from_directory, render_template
from werkzeug.utils import secure_filename
import logging
from datetime import datetime
import uuid
import subprocess
import shutil

# Configuração de caminhos
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'uploads')
OUTPUT_FOLDER = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'output')
TEMP_FOLDER = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'temp')

# Criar diretórios se não existirem
for folder in [UPLOAD_FOLDER, OUTPUT_FOLDER, TEMP_FOLDER]:
    if not os.path.exists(folder):
        os.makedirs(folder)

# Configuração do logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'app.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='static')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max upload

# Classe principal para geração de vídeo
class VideoGenerator:
    def __init__(self, prompt, openai_api_key, pi_api_key=None, high_quality=True):
        self.prompt = prompt
        self.openai_api_key = openai_api_key
        self.pi_api_key = pi_api_key
        self.high_quality = high_quality
        self.project_id = str(uuid.uuid4())
        self.project_folder = os.path.join(TEMP_FOLDER, self.project_id)
        self.scenes_folder = os.path.join(self.project_folder, 'scenes')
        self.audio_folder = os.path.join(self.project_folder, 'audio')
        self.output_folder = os.path.join(OUTPUT_FOLDER, self.project_id)
        
        # Criar estrutura de pastas para o projeto
        for folder in [self.project_folder, self.scenes_folder, self.audio_folder, self.output_folder]:
            if not os.path.exists(folder):
                os.makedirs(folder)
        
        self.script = None
        self.title = None
        self.video_path = None
        self.project_zip_path = None
        
        logger.info(f"Iniciando novo projeto: {self.project_id}")
    
    def generate_script(self):
        """Gera o roteiro em formato JSON usando a API da OpenAI"""
        logger.info("Gerando roteiro...")
        
        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.openai_api_key}"
            }
            
            prompt_text = f"""Crie um roteiro no estilo "Sussurros Clínicos" sobre {self.prompt} em formato JSON. 
            O roteiro deve ter no máximo 60 segundos quando narrado e seguir esta estrutura:

            {{
              "titulo": "Título do Vídeo",
              "cenas": [
                {{
                  "numero": 1,
                  "descricao": "Descrição visual detalhada da cena (stick-figure lilás, fundo preto)",
                  "texto": "Texto exato para narração em tom misterioso e científico"
                }},
                {{
                  "numero": 2,
                  "descricao": "...",
                  "texto": "..."
                }}
              ]
            }}

            O estilo deve ser misterioso, com linguagem científica mas acessível, e tom levemente sombrio.
            Retorne APENAS o JSON, sem texto adicional."""
            
            payload = {
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": prompt_text}],
                "temperature": 0.7
            }
            
            response = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=payload
            )
            
            if response.status_code != 200:
                logger.error(f"Erro na API OpenAI: {response.text}")
                raise Exception(f"Erro na API OpenAI: {response.status_code}")
            
            content = response.json()["choices"][0]["message"]["content"]
            
            # Extrair apenas o JSON da resposta
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
            
            self.script = json.loads(content)
            self.title = self.script["titulo"]
            
            # Salvar o roteiro no projeto
            with open(os.path.join(self.project_folder, 'script.json'), 'w', encoding='utf-8') as f:
                json.dump(self.script, f, ensure_ascii=False, indent=2)
            
            logger.info(f"Roteiro gerado com sucesso: {self.title}")
            return self.script
            
        except Exception as e:
            logger.error(f"Erro ao gerar roteiro: {str(e)}")
            raise
    
    def generate_images(self):
        """Gera imagens para cada cena usando a API PiAPI ou alternativa"""
        logger.info("Gerando imagens para as cenas...")
        
        if not self.script:
            raise Exception("Roteiro não foi gerado ainda")
        
        try:
            for scene in self.script["cenas"]:
                scene_num = scene["numero"]
                scene_desc = scene["descricao"]
                
                # Preparar prompt para geração de imagem
                image_prompt = f"""Imagem minimalista no estilo "Sussurros Clínicos": {scene_desc}. 
                Estilo: stick-figure lilás sobre fundo totalmente preto, linhas finas neon, 
                estética médica minimalista, sem texto."""
                
                # Usar PiAPI se disponível, caso contrário usar OpenAI
                if self.pi_api_key:
                    # Implementação da chamada para PiAPI
                    headers = {
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.pi_api_key}"
                    }
                    
                    payload = {
                        "prompt": image_prompt,
                        "negative_prompt": "texto, palavras, letras, cores vibrantes, fundo colorido",
                        "width": 1024,
                        "height": 576,  # 16:9 aspect ratio
                        "steps": 30 if self.high_quality else 20,
                        "guidance_scale": 7.5
                    }
                    
                    response = requests.post(
                        "https://api.getimg.ai/v1/stable-diffusion/text-to-image",
                        headers=headers,
                        json=payload
                    )
                    
                    if response.status_code != 200:
                        logger.error(f"Erro na API de imagem: {response.text}")
                        raise Exception(f"Erro na API de imagem: {response.status_code}")
                    
                    image_data = response.json().get("image")
                    image_bytes = base64.b64decode(image_data)
                    
                else:
                    # Usar OpenAI DALL-E como alternativa
                    headers = {
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.openai_api_key}"
                    }
                    
                    payload = {
                        "model": "dall-e-3",
                        "prompt": image_prompt,
                        "size": "1024x1792",
                        "quality": "hd" if self.high_quality else "standard",
                        "n": 1
                    }
                    
                    response = requests.post(
                        "https://api.openai.com/v1/images/generations",
                        headers=headers,
                        json=payload
                    )
                    
                    if response.status_code != 200:
                        logger.error(f"Erro na API DALL-E: {response.text}")
                        raise Exception(f"Erro na API DALL-E: {response.status_code}")
                    
                    image_url = response.json()["data"][0]["url"]
                    image_response = requests.get(image_url)
                    image_bytes = image_response.content
                
                # Salvar a imagem
                image_path = os.path.join(self.scenes_folder, f"scene_{scene_num}.png")
                with open(image_path, "wb") as f:
                    f.write(image_bytes)
                
                logger.info(f"Imagem gerada para cena {scene_num}")
                
                # Pequena pausa para evitar limites de taxa da API
                time.sleep(1)
            
            logger.info("Todas as imagens foram geradas com sucesso")
            return True
            
        except Exception as e:
            logger.error(f"Erro ao gerar imagens: {str(e)}")
            raise
    
    def generate_audio(self):
        """Gera arquivos de áudio para cada cena usando TTS"""
        logger.info("Gerando arquivos de áudio...")
        
        if not self.script:
            raise Exception("Roteiro não foi gerado ainda")
        
        try:
            for scene in self.script["cenas"]:
                scene_num = scene["numero"]
                scene_text = scene["texto"]
                
                # Usar OpenAI TTS
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.openai_api_key}"
                }
                
                payload = {
                    "model": "tts-1" if not self.high_quality else "tts-1-hd",
                    "input": scene_text,
                    "voice": "onyx",  # Voz masculina grave
                    "response_format": "mp3"
                }
                
                response = requests.post(
                    "https://api.openai.com/v1/audio/speech",
                    headers=headers,
                    json=payload
                )
                
                if response.status_code != 200:
                    logger.error(f"Erro na API TTS: {response.text}")
                    raise Exception(f"Erro na API TTS: {response.status_code}")
                
                # Salvar o áudio
                audio_path = os.path.join(self.audio_folder, f"audio_{scene_num}.mp3")
                with open(audio_path, "wb") as f:
                    f.write(response.content)
                
                logger.info(f"Áudio gerado para cena {scene_num}")
                
                # Pequena pausa para evitar limites de taxa da API
                time.sleep(1)
            
            logger.info("Todos os arquivos de áudio foram gerados com sucesso")
            return True
            
        except Exception as e:
            logger.error(f"Erro ao gerar áudio: {str(e)}")
            raise
    
    def create_video(self):
        """Monta o vídeo final usando FFmpeg"""
        logger.info("Montando vídeo final...")
        
        try:
            # Criar arquivo de lista para FFmpeg
            scenes_list_path = os.path.join(self.project_folder, 'scenes_list.txt')
            with open(scenes_list_path, 'w') as f:
                for scene in self.script["cenas"]:
                    scene_num = scene["numero"]
                    image_path = os.path.join(self.scenes_folder, f"scene_{scene_num}.png")
                    audio_path = os.path.join(self.audio_folder, f"audio_{scene_num}.mp3")
                    
                    # Obter duração do áudio
                    cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', 
                           '-of', 'default=noprint_wrappers=1:nokey=1', audio_path]
                    duration = float(subprocess.check_output(cmd).decode('utf-8').strip())
                    
                    # Criar vídeo temporário para cada cena
                    temp_video = os.path.join(self.project_folder, f'temp_scene_{scene_num}.mp4')
                    subprocess.run([
                        'ffmpeg', '-y', '-loop', '1', '-i', image_path, 
                        '-i', audio_path, '-c:v', 'libx264', '-tune', 'stillimage',
                        '-c:a', 'aac', '-b:a', '192k', '-pix_fmt', 'yuv420p',
                        '-shortest', '-t', str(duration), temp_video
                    ])
                    
                    f.write(f"file '{temp_video}'\n")
            
            # Concatenar todos os vídeos
            safe_title = secure_filename(self.title.lower().replace(' ', '_'))
            output_video = os.path.join(self.output_folder, f"{safe_title}.mp4")
            
            subprocess.run([
                'ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', scenes_list_path,
                '-c', 'copy', output_video
            ])
            
            self.video_path = output_video
            logger.info(f"Vídeo criado com sucesso: {self.video_path}")
            
            # Criar arquivo de projeto para CapCut
            self.create_capcut_project()
            
            return self.video_path
            
        except Exception as e:
            logger.error(f"Erro ao criar vídeo: {str(e)}")
            raise
    
    def create_capcut_project(self):
        """Cria um arquivo de projeto compatível com CapCut"""
        logger.info("Criando arquivo de projeto para CapCut...")
        
        try:
            # Criar pasta para projeto CapCut
            capcut_folder = os.path.join(self.project_folder, 'capcut_project')
            if not os.path.exists(capcut_folder):
                os.makedirs(capcut_folder)
            
            # Copiar todos os recursos para a pasta do projeto
            for scene in self.script["cenas"]:
                scene_num = scene["numero"]
                shutil.copy(
                    os.path.join(self.scenes_folder, f"scene_{scene_num}.png"),
                    os.path.join(capcut_folder, f"scene_{scene_num}.png")
                )
                shutil.copy(
                    os.path.join(self.audio_folder, f"audio_{scene_num}.mp3"),
                    os.path.join(capcut_folder, f"audio_{scene_num}.mp3")
                )
            
            # Salvar o roteiro em formato legível
            with open(os.path.join(capcut_folder, 'roteiro.txt'), 'w', encoding='utf-8') as f:
                f.write(f"TÍTULO: {self.title}\n\n")
                for scene in self.script["cenas"]:
                    f.write(f"CENA {scene['numero']}:\n")
                    f.write(f"Descrição: {scene['descricao']}\n")
                    f.write(f"Texto: {scene['texto']}\n\n")
            
            # Criar arquivo ZIP do projeto
            safe_title = secure_filename(self.title.lower().replace(' ', '_'))
            self.project_zip_path = os.path.join(self.output_folder, f"{safe_title}_projeto.zip")
            
            shutil.make_archive(
                os.path.splitext(self.project_zip_path)[0],  # Nome base sem extensão
                'zip',
                capcut_folder
            )
            
            logger.info(f"Projeto CapCut criado: {self.project_zip_path}")
            return self.project_zip_path
            
        except Exception as e:
            logger.error(f"Erro ao criar projeto CapCut: {str(e)}")
            raise
    
    def generate_complete_video(self):
        """Executa todo o pipeline de geração de vídeo"""
        try:
            self.generate_script()
            self.generate_images()
            self.generate_audio()
            self.create_video()
            
            # Retornar URLs relativas para os arquivos
            video_url = f"/download/{os.path.basename(self.output_folder)}/{os.path.basename(self.video_path)}"
            project_url = f"/download/{os.path.basename(self.output_folder)}/{os.path.basename(self.project_zip_path)}"
            
            return {
                "success": True,
                "title": self.title,
                "video_url": video_url,
                "project_url": project_url
            }
            
        except Exception as e:
            logger.error(f"Erro no pipeline de geração: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }

# Rotas da aplicação
@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/generate-video', methods=['POST'])
def generate_video():
    try:
        data = request.json
        prompt = data.get('prompt')
        api_key = data.get('api_key')
        pi_api_key = data.get('pi_api_key')
        high_quality = data.get('high_quality', True)
        
        if not prompt or not api_key:
            return jsonify({"success": False, "error": "Prompt e chave da API são obrigatórios"}), 400
        
        # Iniciar geração de vídeo
        generator = VideoGenerator(prompt, api_key, pi_api_key, high_quality)
        result = generator.generate_complete_video()
        
        if result["success"]:
            return jsonify(result)
        else:
            return jsonify(result), 500
            
    except Exception as e:
        logger.error(f"Erro na rota generate-video: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/download/<project_id>/<filename>')
def download_file(project_id, filename):
    return send_from_directory(os.path.join(OUTPUT_FOLDER, project_id), filename)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)
