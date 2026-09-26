import hmac
import os
import shutil
from datetime import date, datetime, timezone
from pathlib import Path
from secrets import token_urlsafe
from uuid import uuid4

import httpx
from flask import Flask, abort, render_template, request, redirect, url_for, session, flash, send_from_directory
from postgrest.exceptions import APIError
from werkzeug.datastructures import FileStorage
from werkzeug.security import check_password_hash, generate_password_hash

from db import SupabaseConfigurationError, get_supabase_client
from compatibility import Compatibilidade
from photo_storage import MAX_PHOTOS, MAX_PHOTO_BYTES, PHOTO_NAME_PATTERN, delete_uploaded_photos, save_uploaded_photos
from profile_data import (
    APPEARANCE_PREFERENCES,
    FIELD_GROUPS,
    INTEREST_FIELDS,
    PROFILE_INTEGER_DEFAULTS,
    PROFILE_OPTIONS,
    PREFERENCE_OPTIONS,
    calculate_imc,
    parse_profile_form,
    profile_summary,
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(32)
app.config["MAX_CONTENT_LENGTH"] = MAX_PHOTOS * MAX_PHOTO_BYTES + 1024 * 1024
app.config["PHOTO_UPLOAD_FOLDER"] = os.path.join(app.instance_path, "uploads")
app.config["TEST_PROFILE_BATCH_FOLDER"] = os.path.join(app.root_path, "teste")
app.config["ENABLE_TEST_PROFILE_GENERATION"] = os.environ.get(
    "ENABLE_TEST_PROFILE_GENERATION", "1"
) == "1"
SUPABASE_ERRORS = (APIError, httpx.HTTPError, SupabaseConfigurationError)
TEST_PROFILE_EMAIL_PATTERN = "date-night-test-%@example.test"


def _supabase_error_message(error, action):
    code = getattr(error, "code", None)
    if code == "42501":
        return f"O Supabase negou acesso ao tentar {action} (RLS/permissão)."
    if code in {"PGRST205", "PGRST204", "42P01", "42703"}:
        return f"Tabela ou coluna ausente ao tentar {action}. Execute schema_postgresql.sql."
    if isinstance(error, httpx.HTTPError):
        return f"Falha de rede ao tentar {action}. Confira SUPABASE_URL e a conexão."
    if isinstance(error, SupabaseConfigurationError):
        return str(error)
    return f"Falha ao tentar {action} no Supabase (código {code or 'API'}); confira o log do Flask."


def _is_local_request():
    return request.remote_addr in {"127.0.0.1", "::1"}


def _find_test_batch_photo():
    folder = Path(app.config["TEST_PROFILE_BATCH_FOLDER"])
    if not folder.is_dir():
        return None
    supported = {".jpg", ".jpeg", ".png", ".webp"}
    return next(
        (path for path in sorted(folder.iterdir()) if path.is_file() and path.suffix.lower() in supported),
        None,
    )


@app.context_processor
def profile_form_options():
    return {
        "appearance_preferences": APPEARANCE_PREFERENCES,
        "binary_options": ((0, "Não"), (1, "Sim")),
        "field_groups": FIELD_GROUPS,
        "interest_fields": INTEREST_FIELDS,
        "current_year": date.today().year,
        "profile_defaults": PROFILE_INTEGER_DEFAULTS,
        "profile_options": PROFILE_OPTIONS,
        "preference_options": PREFERENCE_OPTIONS,
        "test_profiles_available": (
            app.config["ENABLE_TEST_PROFILE_GENERATION"]
            and _is_local_request()
        ),
        "test_profiles_csrf_token": _test_profiles_csrf_token(),
    }


def _test_profiles_csrf_token():
    if not _is_local_request():
        return ""
    if "test_profiles_csrf_token" not in session:
        session["test_profiles_csrf_token"] = token_urlsafe(32)
    return session["test_profiles_csrf_token"]

@app.route('/')
def index():
    """Início: redirecionamento."""
    if 'user_id' in session:
        return redirect(url_for('home'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login: entrada."""
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        senha = request.form.get('senha', '')
        try:
            users = (
                get_supabase_client()
                .table("user")
                .select("id,senha")
                .eq("email", email)
                .limit(1)
                .execute()
                .data
            )
            user = users[0] if users else None
            if user and _verify_password(user['senha'], senha):
                if not _is_password_hash(user['senha']):
                    get_supabase_client().table("user").update(
                        {"senha": generate_password_hash(senha)}
                    ).eq("id", user['id']).execute()
                session.clear()
                session['user_id'] = user['id']
                return redirect(url_for('home'))
        except SUPABASE_ERRORS:
            app.logger.exception('Falha ao consultar login no Supabase')
            flash('Não foi possível acessar o banco de dados.', 'danger')
            return render_template('login.html')

        flash('E-mail ou senha incorretos.', 'danger')
    return render_template('login.html')


@app.route('/test-profiles/generate', methods=['POST'])
def generate_test_profiles():
    if not app.config["ENABLE_TEST_PROFILE_GENERATION"] or not _is_local_request():
        abort(404)
    expected_token = session.get("test_profiles_csrf_token", "")
    supplied_token = request.form.get("csrf_token", "")
    if not expected_token or not hmac.compare_digest(expected_token, supplied_token):
        abort(400)

    photo_path = _find_test_batch_photo()
    if not photo_path:
        flash('Coloque uma foto JPG, PNG ou WebP na pasta teste.', 'danger')
        return redirect(url_for('login'))

    try:
        client = get_supabase_client()
    except SupabaseConfigurationError as error:
        flash(str(error), 'database')
        return redirect(url_for('login'))

    upload_folder = app.config['PHOTO_UPLOAD_FOLDER']
    generated_photo_names = []
    try:
        existing_profiles = (
            client.table('user')
            .select('id,fotos')
            .like('email', TEST_PROFILE_EMAIL_PATTERN)
            .execute()
            .data
            or []
        )
        with photo_path.open('rb') as source:
            master_name = save_uploaded_photos(
                [FileStorage(stream=source, filename=photo_path.name)],
                upload_folder,
            )[0]
        generated_photo_names.append(master_name)
        master_path = Path(upload_folder) / master_name
        profile_rows = []
        photo_names = []
        password_hash = generate_password_hash('DateNight-Teste-2026!')
        for index in range(1, 101):
            photo_name = f'{uuid4().hex}.jpg'
            shutil.copyfile(master_path, Path(upload_folder) / photo_name)
            photo_names.append(photo_name)
            generated_photo_names.append(photo_name)

            profile = dict(PROFILE_INTEGER_DEFAULTS)
            gender = index % 2
            profile.update({
                'nome': f'Perfil de teste {index:03d}',
                'email': f'date-night-test-{uuid4().hex[:10]}-{index:03d}@example.test',
                'senha': password_hash,
                'dia': (index % 28) + 1,
                'mes': (index % 12) + 1,
                'ano': 1988 + (index % 15),
                'genero': gender,
                'pref_genero': 1 - gender,
                'altura': 155 + (index % 45),
                'peso': 50 + (index % 45),
                'fisico': index % len(PROFILE_OPTIONS['fisico']),
                'cabelo': index % len(PROFILE_OPTIONS['cabelo']),
                'pele': index % len(PROFILE_OPTIONS['pele']),
                'tatuagens': index % 2,
                'fumar': 0,
                'salario': index % len(PROFILE_OPTIONS['salario']),
                'certificacao': index % len(PROFILE_OPTIONS['certificacao']),
                'tracos': index % len(PROFILE_OPTIONS['tracos']),
                'pref_fisico': 0,
                'pref_tatuagens': 0,
                'pref_fumar': 1,
                'preto': 1,
                'castanho': 1,
                'loiro': 1,
                'ruivo': 1,
                'escura': 1,
                'morena': 1,
                'clara': 1,
                'fotos': [photo_name],
            })
            profile['imc'] = calculate_imc(profile['peso'], profile['altura'])
            for interest_index, (field, _label) in enumerate(INTEREST_FIELDS):
                profile[field] = int((index + interest_index) % 3 == 0)
            profile_rows.append(profile)
        delete_uploaded_photos([master_name], upload_folder)
        generated_photo_names.remove(master_name)
        client.table('user').insert(profile_rows).execute()

        existing_ids = [profile['id'] for profile in existing_profiles]
        if existing_ids:
            try:
                client.table('ficha').delete().in_('id_user_a', existing_ids).execute()
                client.table('ficha').delete().in_('id_user_b', existing_ids).execute()
                client.table('user').delete().in_('id', existing_ids).execute()
                old_photos = [
                    photo
                    for profile in existing_profiles
                    for photo in profile.get('fotos') or []
                ]
                delete_uploaded_photos(old_photos, upload_folder)
            except SUPABASE_ERRORS:
                app.logger.exception('Os novos perfis foram criados, mas não foi possível remover o lote anterior')
                flash('100 perfis criados; não foi possível remover o lote de teste anterior.', 'warning')
                return redirect(url_for('login'))
    except ValueError as error:
        delete_uploaded_photos(generated_photo_names, upload_folder)
        flash(str(error), 'danger')
        return redirect(url_for('login'))
    except SUPABASE_ERRORS:
        app.logger.exception('Falha ao gerar perfis de teste no Supabase')
        delete_uploaded_photos(generated_photo_names, upload_folder)
        flash('Não foi possível gerar os perfis. Confira o terminal do Flask.', 'danger')
        return redirect(url_for('login'))

    flash('100 perfis de teste foram gerados.', 'success')
    return redirect(url_for('login'))

@app.route('/logout')
def logout():
    """Logout: saída."""
    session.clear()
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    """Cadastro: novo usuário."""
    if request.method == 'POST':
        try:
            values = parse_profile_form(request.form)
        except ValueError as error:
            flash(str(error), 'danger')
            return render_template('register.html', form_data=request.form)

        password = request.form.get('senha', '')
        if len(password) < 6:
            flash('A senha deve ter pelo menos seis caracteres.', 'danger')
            return render_template('register.html', form_data=request.form)
        if password != request.form.get('confirmar_senha', ''):
            flash('As senhas não coincidem.', 'danger')
            return render_template('register.html', form_data=request.form)

        try:
            client = get_supabase_client()
        except SupabaseConfigurationError as error:
            flash(str(error), 'database')
            return render_template('register.html', form_data=request.form)

        try:
            photo_names = save_uploaded_photos(
                request.files.getlist('fotos'), app.config['PHOTO_UPLOAD_FOLDER']
            )
        except ValueError as error:
            flash(str(error), 'danger')
            return render_template('register.html', form_data=request.form)

        values['imc'] = calculate_imc(values['peso'], values['altura'])
        values['fotos'] = photo_names
        values['senha'] = generate_password_hash(password)

        try:
            client.table("user").insert(values).execute()
        except APIError as error:
            delete_uploaded_photos(photo_names, app.config['PHOTO_UPLOAD_FOLDER'])
            app.logger.exception('Falha ao inserir cadastro no Supabase')
            error_code = getattr(error, 'code', None)
            if error_code == '23505':
                flash('Já existe uma conta com esse e-mail.', 'danger')
            elif error_code == '42501':
                flash('O Supabase bloqueou o cadastro por permissão ou política RLS. Confira a chave server-side e as políticas da tabela user.', 'danger')
            elif error_code in {'PGRST205', 'PGRST204', '42P01', '42703'}:
                flash('Tabela ou coluna do Date Night não encontrada. Aplique schema_postgresql.sql no Supabase.', 'danger')
            else:
                flash(f'Falha ao salvar no Supabase (código {error_code or "API"}). Confira o terminal do Flask.', 'danger')
            return render_template('register.html', form_data=request.form)
        except httpx.HTTPError:
            app.logger.exception('Falha de conexão ao inserir cadastro no Supabase')
            delete_uploaded_photos(photo_names, app.config['PHOTO_UPLOAD_FOLDER'])
            flash('Sem conexão com o Supabase. Confira a URL e a conexão de rede.', 'database')
            return render_template('register.html', form_data=request.form)
        except SupabaseConfigurationError as error:
            delete_uploaded_photos(photo_names, app.config['PHOTO_UPLOAD_FOLDER'])
            flash(str(error), 'database')
            return render_template('register.html', form_data=request.form)

        flash('Cadastro realizado. Entre com seu e-mail e senha.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html', form_data={})

def _load_match_candidates(user_id):
    client = get_supabase_client()
    Compatibilidade().calculate_compatibility(user_id)
    fichas_a = client.table("ficha").select("*").eq("id_user_a", user_id).execute().data or []
    fichas_b = client.table("ficha").select("*").eq("id_user_b", user_id).execute().data or []
    fichas = list({ficha['id']: ficha for ficha in [*fichas_a, *fichas_b]}.values())
    candidate_ids = sorted({
        ficha['id_user_b'] if ficha['id_user_a'] == user_id else ficha['id_user_a']
        for ficha in fichas
    })
    candidate_users = (
        client.table("user").select("*").in_("id", candidate_ids).execute().data
        if candidate_ids else []
    ) or []
    users_by_id = {user['id']: user for user in candidate_users}
    current_users = client.table("user").select("id,nome").eq("id", user_id).limit(1).execute().data or []
    current_user = current_users[0] if current_users else None
    candidates = []
    for ficha in fichas:
        side = 'a' if ficha['id_user_a'] == user_id else 'b'
        if ficha.get(f'recusou_{side}', 0):
            continue
        other_id = ficha['id_user_b'] if side == 'a' else ficha['id_user_a']
        other_user = users_by_id.get(other_id)
        if not other_user:
            continue
        candidate = dict(other_user)
        candidate.update({
            'ficha_id': ficha['id'],
            'id_user_a': ficha['id_user_a'],
            'id_user_b': ficha['id_user_b'],
            'compatibilidade': ficha['compatibilidade'],
            'gostei_a': ficha.get('gostei_a', 0),
            'amei_a': ficha.get('amei_a', 0),
            'recusou_a': ficha.get('recusou_a', 0),
            'gostei_b': ficha.get('gostei_b', 0),
            'amei_b': ficha.get('amei_b', 0),
            'recusou_b': ficha.get('recusou_b', 0),
            'match': ficha.get('match', 0),
        })
        side = 'a' if candidate['id_user_a'] == user_id else 'b'
        other_side = 'b' if side == 'a' else 'a'
        candidate['my_like'] = candidate[f'gostei_{side}']
        candidate['my_love'] = candidate[f'amei_{side}']
        candidate['their_like'] = candidate[f'gostei_{other_side}']
        candidate['their_love'] = candidate[f'amei_{other_side}']
        candidates.append(candidate)
    candidates.sort(
        key=lambda item: (
            item['their_love'], item['my_love'], item['their_like'],
            item['my_like'], item['compatibilidade'],
        ),
        reverse=True,
    )
    return current_user, candidates


@app.route('/home')
def home():
    """Lista os perfis compatíveis."""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    try:
        current_user, candidates = _load_match_candidates(session['user_id'])
    except SUPABASE_ERRORS as error:
        app.logger.exception('Falha ao carregar perfis pelo Supabase')
        flash(_supabase_error_message(error, 'carregar perfis'), 'database')
        return render_template('home.html', current_user=None, candidates=[])

    if not current_user:
        session.clear()
        return redirect(url_for('login'))
    return render_template('home.html', current_user=current_user, candidates=candidates)


@app.route('/encounter')
def encounter():
    """Mostra um perfil por vez para o encontro."""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    try:
        current_user, candidates = _load_match_candidates(session['user_id'])
    except SUPABASE_ERRORS:
        app.logger.exception('Falha ao carregar encontro pelo Supabase')
        flash('Não foi possível carregar o encontro. Confira a configuração do Supabase.', 'database')
        return render_template('encounter.html', current_user=None, candidate=None)

    if not current_user:
        session.clear()
        return redirect(url_for('login'))

    position = request.args.get('position', 0, type=int)
    if candidates:
        position = max(0, min(position, len(candidates) - 1))
        candidate = candidates[position]
        details = profile_summary(candidate)
    else:
        position = 0
        candidate = None
        details = []
    return render_template(
        'encounter.html', current_user=current_user, candidate=candidate,
        details=details, position=position, total=len(candidates),
    )


@app.route('/matches/<int:ficha_id>/action', methods=['POST'])
def match_action(ficha_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    action = request.form.get('action')
    if action not in {'like', 'love', 'skip'}:
        abort(400)

    user_id = session['user_id']
    try:
        client = get_supabase_client()
        fichas = client.table("ficha").select("*").eq("id", ficha_id).limit(1).execute().data or []
        ficha = fichas[0] if fichas else None
        if not ficha or user_id not in (ficha['id_user_a'], ficha['id_user_b']):
            abort(404)

        side = 'a' if ficha['id_user_a'] == user_id else 'b'
        changes = {"data_interacao": datetime.now(timezone.utc).isoformat()}
        if action == 'skip':
            changes[f'recusou_{side}'] = 1
        else:
            column = 'gostei' if action == 'like' else 'amei'
            changes[f'{column}_{side}'] = 1
            updated_ficha = {**ficha, **changes}
            matched = bool(
                (updated_ficha.get('gostei_a') and updated_ficha.get('gostei_b'))
                or (updated_ficha.get('amei_a') and updated_ficha.get('amei_b'))
            )
            changes['match'] = int(matched)
            changes['data_match'] = ficha.get('data_match') or datetime.now(timezone.utc).isoformat() if matched else None
        client.table("ficha").update(changes).eq("id", ficha_id).execute()
    except SUPABASE_ERRORS:
        app.logger.exception('Falha ao registrar interação no Supabase')
        flash('Não foi possível registrar sua escolha.', 'danger')

    position = request.form.get('position', 0, type=int)
    next_position = position if action == 'skip' else position + 1
    return redirect(url_for('encounter', position=next_position))

@app.route('/profile', methods=['GET', 'POST'])
def profile():
    """Perfil: visualização e edição."""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']

    if request.method == 'POST':
        try:
            values = parse_profile_form(request.form)
        except ValueError as error:
            flash(str(error), 'danger')
            return render_template('profile.html', user=request.form, form_data=request.form)

        password = request.form.get('nova_senha', '')
        confirmation = request.form.get('confirmar_senha', '')
        if password or confirmation:
            if len(password) < 6 or password != confirmation:
                flash('A nova senha deve ter seis caracteres e coincidir com a confirmação.', 'danger')
                return render_template('profile.html', user=request.form, form_data=request.form)
            values['senha'] = generate_password_hash(password)
        values['imc'] = calculate_imc(values['peso'], values['altura'])
        new_photos = []
        removed_photos = []
        try:
            client = get_supabase_client()
            current_users = client.table("user").select("fotos").eq("id", user_id).limit(1).execute().data or []
            current_user = current_users[0] if current_users else None
            if not current_user:
                session.clear()
                return redirect(url_for('login'))
            current_photos = list(current_user.get('fotos') or [])
            remove_requested = set(request.form.getlist('remover_fotos'))
            removed_photos = [photo for photo in current_photos if photo in remove_requested]
            retained_photos = [photo for photo in current_photos if photo not in remove_requested]
            new_photos = save_uploaded_photos(
                request.files.getlist('fotos'),
                app.config['PHOTO_UPLOAD_FOLDER'],
                existing_count=len(retained_photos),
            )
            values['fotos'] = retained_photos + new_photos
            client.table("user").update(values).eq("id", user_id).execute()
        except ValueError as error:
            delete_uploaded_photos(new_photos, app.config['PHOTO_UPLOAD_FOLDER'])
            flash(str(error), 'danger')
            return render_template('profile.html', user=request.form, form_data=request.form)
        except APIError as error:
            delete_uploaded_photos(new_photos, app.config['PHOTO_UPLOAD_FOLDER'])
            app.logger.exception('Falha ao atualizar perfil no Supabase')
            if getattr(error, 'code', None) == '23505':
                flash('Esse e-mail já pertence a outra conta.', 'danger')
            else:
                flash('Não foi possível atualizar o perfil no Supabase.', 'danger')
            return render_template('profile.html', user=request.form, form_data=request.form)
        except (httpx.HTTPError, SupabaseConfigurationError):
            delete_uploaded_photos(new_photos, app.config['PHOTO_UPLOAD_FOLDER'])
            app.logger.exception('Falha de conexão ao atualizar perfil no Supabase')
            flash('Não foi possível conectar ao Supabase.', 'database')
            return render_template('profile.html', user=request.form, form_data=request.form)
        delete_uploaded_photos(removed_photos, app.config['PHOTO_UPLOAD_FOLDER'])
        flash('Perfil atualizado.', 'success')
        return redirect(url_for('profile'))

    try:
        users = get_supabase_client().table("user").select("*").eq("id", user_id).limit(1).execute().data or []
        user = users[0] if users else None
    except SUPABASE_ERRORS:
        app.logger.exception('Falha ao carregar perfil do Supabase')
        flash('Não foi possível carregar o perfil.', 'danger')
        return redirect(url_for('home'))
    if not user:
        session.clear()
        return redirect(url_for('login'))
    return render_template(
        'profile.html', user=user, form_data=user,
        existing_photos=user.get('fotos') or [],
    )


@app.route('/uploads/<filename>')
def uploaded_photo(filename):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if not PHOTO_NAME_PATTERN.fullmatch(filename):
        abort(404)

    user_id = session['user_id']
    try:
        client = get_supabase_client()
        photo_owners = client.table("user").select("id").contains(
            "fotos", [filename]
        ).execute().data or []
        photo_owner_ids = {owner['id'] for owner in photo_owners}
        authorized = user_id in photo_owner_ids
        if not authorized and photo_owner_ids:
            fichas_a = client.table("ficha").select("id_user_b").eq(
                "id_user_a", user_id
            ).in_("id_user_b", list(photo_owner_ids)).limit(1).execute().data or []
            fichas_b = client.table("ficha").select("id_user_a").eq(
                "id_user_b", user_id
            ).in_("id_user_a", list(photo_owner_ids)).limit(1).execute().data or []
            authorized = bool(fichas_a or fichas_b)
    except SUPABASE_ERRORS:
        app.logger.exception('Falha ao verificar acesso à foto no Supabase')
        abort(404)
    if not authorized:
        abort(404)
    response = send_from_directory(
        app.config['PHOTO_UPLOAD_FOLDER'], filename, mimetype='image/jpeg'
    )
    response.headers['Cache-Control'] = 'private, no-store'
    return response


@app.route('/profile/delete', methods=['POST'])
def delete_profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    try:
        client = get_supabase_client()
        users = client.table("user").select("fotos").eq("id", user_id).limit(1).execute().data or []
        user = users[0] if users else None
        client.table("ficha").delete().eq("id_user_a", user_id).execute()
        client.table("ficha").delete().eq("id_user_b", user_id).execute()
        client.table("user").delete().eq("id", user_id).execute()
    except SUPABASE_ERRORS:
        app.logger.exception('Falha ao excluir perfil no Supabase')
        flash('Não foi possível excluir a conta.', 'danger')
        return redirect(url_for('profile'))
    if user:
        delete_uploaded_photos(user.get('fotos') or [], app.config['PHOTO_UPLOAD_FOLDER'])
    session.clear()
    flash('Conta excluída.', 'success')
    return redirect(url_for('login'))

def _is_password_hash(value):
    return value.startswith(('scrypt:', 'pbkdf2:'))


def _verify_password(stored, supplied):
    if _is_password_hash(stored):
        return check_password_hash(stored, supplied)
    return hmac.compare_digest(stored, supplied)


if __name__ == '__main__':
    app.run(debug=os.environ.get('FLASK_DEBUG') == '1', port=5000)
