@echo off
echo Iniciando o Date Night...

if not exist "venv\Scripts\python.exe" (
    echo Criando ambiente virtual...
    python -m venv venv
    if errorlevel 1 (
        echo Nao foi possivel criar o ambiente virtual.
        pause
        exit /b 1
    )
)

call venv\Scripts\activate.bat
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo Nao foi possivel instalar as dependencias.
    pause
    exit /b 1
)

if not exist ".env" (
    echo Configure o arquivo .env usando .env.example como modelo.
    pause
    exit /b 1
)

echo Iniciando o servidor web na porta 5000...
python app.py

pause
