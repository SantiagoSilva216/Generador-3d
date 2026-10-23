@echo off
cd /d "%~dp0"
echo === Instalando img2blend (solo hace falta una vez) ===
if not exist venv python -m venv venv
call venv\Scripts\activate.bat

pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
pip install pillow diffusers transformers accelerate safetensors einops omegaconf trimesh scikit-image opencv-python onnxruntime rembg pyyaml tqdm huggingface_hub

if not exist Hunyuan3D-2 git clone https://github.com/Tencent/Hunyuan3D-2.git
pip install -e Hunyuan3D-2 --no-deps

echo.
echo === Comprobando ===
python -c "import torch, PIL, hy3dgen; print('GPU disponible:', torch.cuda.is_available())"
echo.
echo Si dice GPU disponible: True, ya puedes usar ejecutar.bat
pause
