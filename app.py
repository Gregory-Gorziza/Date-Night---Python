from flask import Flask, render_template, request, redirect, url_for, session, flash
from db import get_db_connection
from compatibility import Compatibilidade
import bcrypt

app = Flask(__name__)
app.secret_key = 'super_secret_key_datenight'

@app.route('/')
def index():
    """
    Rota principal do aplicativo.
    Verifica se o usuário já está logado (tem 'user_id' na sessão).
    Se estiver, redireciona para a página principal (home).
    Caso contrário, redireciona para a tela de login.
    """
    if 'user_id' in session:
        return redirect(url_for('home'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    """
    Rota para autenticação do usuário.
    Aceita requisições GET (exibir o formulário) e POST (processar os dados de login).
    """
    if request.method == 'POST':
        email = request.form['email']
        senha = request.form['senha']
        
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # In the original Java code, it was raw text. 
                # Let's check against plain text for backward compatibility, but we can upgrade to bcrypt if needed.
                cursor.execute("SELECT id, senha FROM user WHERE email = %s", (email,))
                user = cursor.fetchone()
                
                if user and user['senha'] == senha: # Keeping plain text as original Java had it
                    session['user_id'] = user['id']
                    flash('Login bem-sucedido!', 'success')
                    return redirect(url_for('home'))
                else:
                    flash('Email ou senha incorretos.', 'danger')
                    
    return render_template('login.html')

@app.route('/logout')
def logout():
    """
    Rota para encerrar a sessão do usuário (Logout).
    Remove o 'user_id' da sessão e redireciona de volta ao login.
    """
    session.pop('user_id', None)
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    """
    Rota para cadastro de novos usuários.
    Processa os dados do formulário, calcula o IMC e insere no banco de dados.
    """
    if request.method == 'POST':
        # Get all fields from form
        data = request.form
        
        # Calculate IMC
        try:
            peso = int(data['peso'])
            altura = int(data['altura'])
            altura_m = altura / 100.0
            imc = round(peso / (altura_m * altura_m))
        except (ValueError, KeyError):
            imc = 0
            
        sql = """INSERT INTO user (nome, email, senha, dia, mes, ano, genero, altura, fisico, peso, imc, 
                 cabelo, pele, tatuagens, fumar, salario, certificacao, tracos, animais, musica, artes, 
                 esportes, jogos, livros, natureza, viagens, gastronomia, causa, pref_Genero, dinamica, 
                 pref_fisico, pref_Tatuagens, pref_Fumar, ruivo, preto, castanho, loiro, escura, morena, clara) 
                 VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 
                 %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
                 
        params = (
            data.get('nome'), data.get('email'), data.get('senha'), data.get('dia', 1), data.get('mes', 1), data.get('ano', 2000),
            data.get('genero', 0), data.get('altura', 170), data.get('fisico', 0), data.get('peso', 70), imc,
            data.get('cabelo', 0), data.get('pele', 0), data.get('tatuagens', 0), data.get('fumar', 0),
            data.get('salario', 0), data.get('certificacao', 0), data.get('tracos', 0),
            data.get('animais', 0), data.get('musica', 0), data.get('artes', 0), data.get('esportes', 0),
            data.get('jogos', 0), data.get('livros', 0), data.get('natureza', 0), data.get('viagens', 0),
            data.get('gastronomia', 0), data.get('causasSociais', 0), data.get('prefGenero', 0), data.get('dinamica', 0),
            data.get('preffisico', 0), data.get('prefTatuagens', 0), data.get('prefFumar', 0),
            data.get('ruivo', 0), data.get('preto', 0), data.get('castanho', 0), data.get('loiro', 0),
            data.get('escuro', 0), data.get('moreno', 0), data.get('claro', 0)
        )
        
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
            conn.commit()
            
        flash('Cadastro realizado com sucesso!', 'success')
        return redirect(url_for('login'))
        
    return render_template('register.html')

@app.route('/home')
def home():
    """
    Página inicial do usuário autenticado (Home).
    Recalcula a compatibilidade do usuário com os outros e exibe os 'matches' (combinações) ordenados por afinidade.
    """
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    user_id = session['user_id']
    
    # Recalculate compatibility for the current user
    comp = Compatibilidade()
    comp.calculate_compatibility(user_id)
    
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT u.nome, u.email, f.compatibilidade, u.id 
                FROM ficha f
                JOIN user u ON (f.id_user_A = u.id OR f.id_user_B = u.id)
                WHERE (f.id_user_A = %s OR f.id_user_B = %s) AND u.id != %s
                ORDER BY f.compatibilidade DESC
            """, (user_id, user_id, user_id))
            matches = cursor.fetchall()
            
            cursor.execute("SELECT nome FROM user WHERE id = %s", (user_id,))
            current_user = cursor.fetchone()
            
    return render_template('home.html', matches=matches, current_user=current_user)

@app.route('/profile', methods=['GET', 'POST'])
def profile():
    """
    Rota para visualizar e atualizar o perfil do usuário logado.
    """
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    user_id = session['user_id']
    
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            if request.method == 'POST':
                # Simplified update for now
                nome = request.form['nome']
                cursor.execute("UPDATE user SET nome = %s WHERE id = %s", (nome, user_id))
                conn.commit()
                flash('Perfil atualizado!', 'success')
                
            cursor.execute("SELECT * FROM user WHERE id = %s", (user_id,))
            user = cursor.fetchone()
            
    return render_template('profile.html', user=user)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
