@echo off
echo Iniciando o Date Night...

:: Verifica se a pasta venv existe
if not exist "venv\" (
    echo A pasta do ambiente virtual (venv) nao foi encontrada!
    pause
    exit /b
)

:: Ativa o ambiente virtual
call venv\Scripts\activate.bat

:: Inicia o servidor Flask
echo Iniciando o servidor web na porta 5000...
python app.py

pause
