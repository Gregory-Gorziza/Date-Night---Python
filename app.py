import hmac
import os
import shutil
from datetime import date, datetime, timezone
from pathlib import Path
from secrets import token_urlsafe
from uuid import uuid4

import httpx
from flask import Flask, abort, jsonify, render_template, request, redirect, url_for, session, flash, send_from_directory
from postgrest.exceptions import APIError
from werkzeug.datastructures import FileStorage
from werkzeug.exceptions import HTTPException
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
app.config["RESTAURANT_ACCESS_TOKEN"] = os.environ.get("RESTAURANT_ACCESS_TOKEN", "")
SUPABASE_ERRORS = (APIError, httpx.HTTPError, SupabaseConfigurationError)
TEST_PROFILE_EMAIL_PATTERN = "date-night-test-%@example.test"


def _supabase_error_message(error, action):
    return 'Erro no banco de dados.'


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
    if 'user_id' in session:
        return redirect(url_for('home'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        senha = request.form.get('senha', '')
        try:
            users = get_supabase_client().table("user").select("id,senha").eq("email", email).limit(1).execute().data
            user = users[0] if users else None
            if user and _verify_password(user['senha'], senha):
                if not _is_password_hash(user['senha']):
                    get_supabase_client().table("user").update({"senha": generate_password_hash(senha)}).eq("id", user['id']).execute()
                session.clear()
                session['user_id'] = user['id']
                return redirect(url_for('home'))
        except:
            flash('Erro no banco de dados.', 'danger')
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
        flash('Coloque uma foto na pasta teste.', 'danger')
        return redirect(url_for('login'))

    client = get_supabase_client()
    upload_folder = app.config['PHOTO_UPLOAD_FOLDER']
    generated_photo_names = []
    existing_profiles = client.table('user').select('id,fotos').like('email', TEST_PROFILE_EMAIL_PATTERN).execute().data or []
    with photo_path.open('rb') as source:
        master_name = save_uploaded_photos([FileStorage(stream=source, filename=photo_path.name)], upload_folder)[0]
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
        client.table('ficha').delete().in_('id_user_a', existing_ids).execute()
        client.table('ficha').delete().in_('id_user_b', existing_ids).execute()
        client.table('user').delete().in_('id', existing_ids).execute()
        old_photos = []
        for profile in existing_profiles:
            for photo in profile.get('fotos') or []:
                old_photos.append(photo)
        delete_uploaded_photos(old_photos, upload_folder)

    flash('100 perfis de teste foram gerados.', 'success')
    return redirect(url_for('login'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        values = parse_profile_form(request.form)
        password = request.form.get('senha', '')
        if len(password) < 6:
            flash('A senha deve ter pelo menos seis caracteres.', 'danger')
            return render_template('register.html', form_data=request.form)
        if password != request.form.get('confirmar_senha', ''):
            flash('As senhas não coincidem.', 'danger')
            return render_template('register.html', form_data=request.form)
        client = get_supabase_client()
        photo_names = save_uploaded_photos(request.files.getlist('fotos'), app.config['PHOTO_UPLOAD_FOLDER'])
        values['imc'] = calculate_imc(values['peso'], values['altura'])
        values['fotos'] = photo_names
        values['senha'] = generate_password_hash(password)
        try:
            client.table("user").insert(values).execute()
        except:
            flash('Erro ao salvar no banco de dados.', 'danger')
            return render_template('register.html', form_data=request.form)
        flash('Cadastro realizado. Entre com seu e-mail e senha.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html', form_data={})

def _load_match_candidates(user_id, for_feed=False):
    client = get_supabase_client()
    Compatibilidade().calculate_compatibility(user_id)
    fichas_a = client.table("ficha").select("*").eq("id_user_a", user_id).execute().data or []
    fichas_b = client.table("ficha").select("*").eq("id_user_b", user_id).execute().data or []
    fichas = list({ficha['id']: ficha for ficha in [*fichas_a, *fichas_b]}.values())
    candidate_ids = sorted({
        ficha['id_user_b'] if ficha['id_user_a'] == user_id else ficha['id_user_a']
        for ficha in fichas
        if (ficha['id_user_b'] if ficha['id_user_a'] == user_id else ficha['id_user_a']) != user_id
    })
    candidate_users = (
        client.table("user").select("*").in_("id", candidate_ids).execute().data
        if candidate_ids else []
    ) or []
    users_by_id = {user['id']: user for user in candidate_users}
    current_users = client.table("user").select("id,nome").eq("id", user_id).limit(1).execute().data or []
    current_user = current_users[0] if current_users else None
    view_counts = {}
    if for_feed:
        view_rows = client.table("visualizacoes_perfil").select(
            "usuario_alvo_id,contador_aparicoes"
        ).eq("usuario_leitor_id", user_id).execute().data or []
        view_counts = {
            row['usuario_alvo_id']: row.get('contador_aparicoes', 0) or 0
            for row in view_rows
        }
    candidates = []
    for ficha in fichas:
        side = 'a' if ficha['id_user_a'] == user_id else 'b'
        has_reacted = bool(ficha.get(f'gostei_{side}', 0) or ficha.get(f'amei_{side}', 0))
        if has_reacted or (ficha.get(f'recusou_{side}', 0) and not for_feed):
            continue
        other_id = ficha['id_user_b'] if side == 'a' else ficha['id_user_a']
        if other_id == user_id:
            continue
        appearance_count = view_counts.get(other_id, 0)
        if for_feed and appearance_count >= 3:
            continue
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
            'appearance_count': appearance_count,
        })
        side = 'a' if candidate['id_user_a'] == user_id else 'b'
        other_side = 'b' if side == 'a' else 'a'
        candidate['my_like'] = candidate[f'gostei_{side}']
        candidate['my_love'] = candidate[f'amei_{side}']
        candidate['their_like'] = candidate[f'gostei_{other_side}']
        candidate['their_love'] = candidate[f'amei_{other_side}']
        candidate['summary'] = profile_summary(other_user)
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
    if 'user_id' not in session:
        return redirect(url_for('login'))
    try:
        current_user, eligible_candidates = _load_match_candidates(session['user_id'], for_feed=True)
    except Exception:
        app.logger.exception("Falha ao carregar a Home para o usuário %s.", session['user_id'])
        flash('Não foi possível carregar a Home agora. Tente novamente.', 'danger')
        return render_template('home.html', current_user=None, candidates=[]), 503
    if not current_user:
        session.clear()
        return redirect(url_for('login'))
    candidates = []
    for appearance_index in range(3):
        for candidate in eligible_candidates:
            if appearance_index < candidate['appearance_count']:
                continue
            feed_candidate = dict(candidate)
            photos = candidate.get('fotos') or []
            photo_index = min(appearance_index, len(photos) - 1) if photos else None
            feed_candidate['feed_photo'] = photos[photo_index] if photo_index is not None else None
            feed_candidate['appearance_index'] = appearance_index
            feed_candidate['appearance_number'] = appearance_index + 1
            candidates.append(feed_candidate)
    return render_template('home.html', current_user=current_user, candidates=candidates)


@app.route('/profiles/<int:profile_id>/view', methods=['POST'])
def record_profile_view(profile_id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Autenticação necessária.'}), 401
    reader_id = session['user_id']
    expected_count = request.form.get('expected_count', type=int)
    if profile_id == reader_id or expected_count is None or not 0 <= expected_count < 3:
        return jsonify({'success': False, 'error': 'Visualização inválida.'}), 400
    try:
        client = get_supabase_client()
        user_a, user_b = sorted((reader_id, profile_id))
        pair_rows = client.table("ficha").select("*").eq("id_user_a", user_a).eq("id_user_b", user_b).limit(1).execute().data or []
        ficha = pair_rows[0] if pair_rows else None
        if not ficha:
            return jsonify({'success': False, 'error': 'Perfil não disponível no feed.'}), 404
        side = 'a' if ficha['id_user_a'] == reader_id else 'b'
        if ficha.get(f'gostei_{side}') or ficha.get(f'amei_{side}'):
            return jsonify({'success': False, 'error': 'Este perfil já recebeu uma reação.'}), 409
        result = client.rpc("record_profile_view", {
            "p_reader_id": reader_id,
            "p_target_id": profile_id,
            "p_expected_count": expected_count,
        }).execute().data
        if isinstance(result, list):
            result = result[0] if result else expected_count
        if isinstance(result, dict):
            result = result.get('record_profile_view', expected_count + 1)
        return jsonify({'success': True, 'contador_aparicoes': int(result)})
    except Exception:
        app.logger.exception("Falha ao registrar visualização do perfil %s pelo usuário %s.", profile_id, reader_id)
        return jsonify({'success': False, 'error': 'Não foi possível registrar a visualização.'}), 503


@app.route('/encounter')
def encounter():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    current_user, candidates = _load_match_candidates(session['user_id'])
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
    return render_template('encounter.html', current_user=current_user, candidate=candidate, details=details, position=position, total=len(candidates))


def _match_state(ficha):
    if ficha.get('status') == 'declined':
        return 'declined'
    if ficha.get('user1_confirmed') and ficha.get('user2_confirmed'):
        return 'confirmed'
    if ficha.get('user1_confirmed') or ficha.get('user2_confirmed'):
        return 'waiting'
    return 'pending'


def _active_event(client):
    try:
        events = client.table("evento").select("*").eq("status", "active").limit(1).execute().data or []
        return events[0] if events else None
    except SUPABASE_ERRORS:
        app.logger.exception("Não foi possível consultar o evento ativo; a página de matches será exibida sem dados do evento.")
        return None


def _user_match_side(ficha, user_id):
    if ficha['id_user_a'] == user_id:
        return 'user1_confirmed'
    if ficha['id_user_b'] == user_id:
        return 'user2_confirmed'
    abort(403)


@app.route('/matches')
def matches():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    try:
        client = get_supabase_client()
        own_a = client.table("ficha").select("*").eq("id_user_a", user_id).eq("match", 1).execute().data or []
        own_b = client.table("ficha").select("*").eq("id_user_b", user_id).eq("match", 1).execute().data or []
        fichas = list({ficha['id']: ficha for ficha in [*own_a, *own_b] if ficha.get('id') is not None}.values())
        partner_ids = sorted({
            ficha.get('id_user_b') if ficha.get('id_user_a') == user_id else ficha.get('id_user_a')
            for ficha in fichas
            if ficha.get('id_user_a') is not None and ficha.get('id_user_b') is not None
        })
        partners = client.table("user").select("id,nome").in_("id", partner_ids).execute().data if partner_ids else []
        names = {person['id']: person.get('nome') or 'Perfil' for person in (partners or []) if person.get('id') is not None}
        event = _active_event(client)
        confirmed_count = 0
        if event and event.get('id') is not None:
            confirmed_count = sum(
                1 for ficha in client.table("ficha").select("id").eq("event_id", event['id']).eq("status", "confirmed").execute().data or []
            )
        match_items = []
        for ficha in fichas:
            if ficha.get('id_user_a') is None or ficha.get('id_user_b') is None:
                continue
            partner_id = ficha['id_user_b'] if ficha['id_user_a'] == user_id else ficha['id_user_a']
            item = dict(ficha)
            item['partner_name'] = names.get(partner_id, 'Perfil')
            item['state'] = _match_state(ficha)
            item['compatibilidade'] = ficha.get('compatibilidade') or 0
            match_items.append(item)

        debug_matches = []
        if _is_local_request():
            try:
                all_matches = client.table("ficha").select("*").eq("match", 1).execute().data or []
                debug_ids = sorted({person_id for ficha in all_matches for person_id in (ficha.get('id_user_a'), ficha.get('id_user_b')) if person_id is not None})
                debug_users = client.table("user").select("id,nome").in_("id", debug_ids).execute().data if debug_ids else []
                debug_names = {person['id']: person.get('nome') or 'Perfil' for person in (debug_users or []) if person.get('id') is not None}
                for ficha in all_matches:
                    debug_matches.append({
                        'name_a': debug_names.get(ficha.get('id_user_a'), 'Perfil'),
                        'name_b': debug_names.get(ficha.get('id_user_b'), 'Perfil'),
                        'score': ficha.get('compatibilidade') or 0,
                        'liked_by_a': bool(ficha.get('gostei_a') or ficha.get('amei_a')),
                        'liked_by_b': bool(ficha.get('gostei_b') or ficha.get('amei_b')),
                    })
            except SUPABASE_ERRORS:
                app.logger.exception("Não foi possível carregar o painel local de matches.")

        return render_template(
            'matches.html', matches=match_items, event=event,
            confirmed_count=confirmed_count,
            remaining=max((event or {}).get('capacidade_casais', 0) - confirmed_count, 0),
            debug_matches=debug_matches,
        )
    except Exception:
        app.logger.exception("Falha ao carregar a página de matches para o usuário %s.", user_id)
        return render_template(
            'matches.html', matches=[], event=None,
            confirmed_count=0, remaining=0, debug_matches=[],
        )


@app.route('/matches/status')
def matches_status():
    if 'user_id' not in session:
        return jsonify({'error': 'Autenticação necessária.'}), 401
    user_id = session['user_id']
    try:
        client = get_supabase_client()
        own_a = client.table("ficha").select("id,status,user1_confirmed,user2_confirmed,event_id").eq("id_user_a", user_id).eq("match", 1).execute().data or []
        own_b = client.table("ficha").select("id,status,user1_confirmed,user2_confirmed,event_id").eq("id_user_b", user_id).eq("match", 1).execute().data or []
        states = [
            {'id': ficha['id'], 'state': _match_state(ficha)}
            for ficha in {item['id']: item for item in [*own_a, *own_b] if item.get('id') is not None}.values()
        ]
        event = _active_event(client)
        confirmed_count = 0
        if event and event.get('id') is not None:
            confirmed_count = len(
                client.table("ficha").select("id").eq("event_id", event['id']).eq("status", "confirmed").execute().data or []
            )
        return jsonify({
            'matches': states,
            'event_date': event.get('data_hora') if event else None,
            'confirmed_count': confirmed_count,
            'capacity': event.get('capacidade_casais', 0) if event else 0,
        })
    except Exception:
        app.logger.exception("Falha ao consultar estados dos matches para o usuário %s.", user_id)
        return jsonify({'error': 'Não foi possível atualizar os matches.'}), 503


@app.route('/matches/<int:ficha_id>/confirmation', methods=['POST'])
def match_confirmation(ficha_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Autenticação necessária.'}), 401
    try:
        client = get_supabase_client()
        fichas = client.table("ficha").select("*").eq("id", ficha_id).limit(1).execute().data or []
        ficha = fichas[0] if fichas else None
        if not ficha or not ficha.get('match'):
            abort(404)
        side_column = _user_match_side(ficha, session['user_id'])
        action = request.form.get('action')
        if action not in {'confirm', 'decline'}:
            return jsonify({'error': 'Ação inválida.'}), 400
        if ficha.get('status') == 'declined':
            return jsonify({'error': 'Este encontro foi recusado.'}), 409
        changes = {'data_interacao': datetime.now(timezone.utc).isoformat()}
        if action == 'decline':
            changes.update({'status': 'declined', side_column: 0})
        else:
            changes[side_column] = 1
            updated = {**ficha, **changes}
            changes['status'] = 'confirmed' if updated.get('user1_confirmed') and updated.get('user2_confirmed') else 'waiting'
        client.table("ficha").update(changes).eq("id", ficha_id).execute()
        updated = {**ficha, **changes}
        state = _match_state(updated)
        message = {
            'pending': 'Confirme sua participação no encontro.',
            'waiting': 'Você confirmou! Aguardando a confirmação do seu par.',
            'confirmed': 'Encontro confirmado! Vocês dois aceitaram.',
            'declined': 'Você recusou este encontro.',
        }[state]
        response = {'state': state, 'message': message}
        if state == 'confirmed' and updated.get('event_id'):
            event_rows = client.table("evento").select("id,data_hora,capacidade_casais").eq("id", updated['event_id']).limit(1).execute().data or []
            event = event_rows[0] if event_rows else None
            if event:
                confirmed = client.table("ficha").select("id").eq("event_id", event['id']).eq("status", "confirmed").execute().data or []
                response.update({
                    'event_date': event.get('data_hora'),
                    'confirmed_count': len(confirmed),
                    'capacity': event.get('capacidade_casais', 0),
                })
        return jsonify(response)
    except SUPABASE_ERRORS:
        app.logger.exception("Falha ao atualizar confirmação de match.")
        return jsonify({'error': 'Não foi possível atualizar o encontro. Tente novamente.'}), 503


@app.route('/matches/<int:ficha_id>/action', methods=['POST'])
def match_action(ficha_id):
    wants_json = (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or request.accept_mimetypes.best == 'application/json'
    )
    if 'user_id' not in session:
        if wants_json:
            return jsonify({'error': 'Autenticação necessária.'}), 401
        return redirect(url_for('login'))
    try:
        reaction_value = request.form.get('tipo_reacao')
        action = {'gostei': 'like', 'amei': 'love'}.get(
            reaction_value, request.form.get('action')
        )
        user_id = session['user_id']
        client = get_supabase_client()
        fichas = client.table("ficha").select("*").eq("id", ficha_id).limit(1).execute().data or []
        ficha = fichas[0] if fichas else None
        if not ficha:
            if wants_json:
                return jsonify({'error': 'Perfil não encontrado.'}), 404
            abort(404)
        side = 'a' if ficha.get('id_user_a') == user_id else 'b' if ficha.get('id_user_b') == user_id else None
        if not side:
            if wants_json:
                return jsonify({'error': 'Você não pode reagir a este perfil.'}), 403
            abort(403)
        profile_id = ficha.get('id_user_b') if side == 'a' else ficha.get('id_user_a')
        supplied_profile_id = request.form.get('perfil_id')
        if supplied_profile_id and str(profile_id) != supplied_profile_id:
            if wants_json:
                return jsonify({'error': 'O perfil enviado não corresponde a este match.'}), 400
            abort(400)
        if action not in {'skip', 'like', 'love'}:
            return jsonify({'error': 'Ação inválida.'}), 400

        changes = {"data_interacao": datetime.now(timezone.utc).isoformat()}
        if action == 'skip':
            changes[f'recusou_{side}'] = 1
        else:
            selected_column = f'gostei_{side}' if action == 'like' else f'amei_{side}'
            selected_value = 0 if ficha.get(selected_column) else 1
            changes.update({
                f'gostei_{side}': int(action == 'like' and selected_value),
                f'amei_{side}': int(action == 'love' and selected_value),
                f'recusou_{side}': 0,
            })
            updated_ficha = {**ficha, **changes}
            matched = bool(
                (updated_ficha.get('gostei_a') or updated_ficha.get('amei_a'))
                and (updated_ficha.get('gostei_b') or updated_ficha.get('amei_b'))
            )
            changes['match'] = int(matched)
            if matched and not ficha.get('match'):
                event = _active_event(client)
                changes.update({
                    'data_match': datetime.now(timezone.utc).isoformat(),
                    'user1_confirmed': 0,
                    'user2_confirmed': 0,
                    'status': 'pending',
                    'event_id': event['id'] if event else None,
                })
            elif not matched and ficha.get('match'):
                changes.update({
                    'user1_confirmed': 0,
                    'user2_confirmed': 0,
                    'status': 'pending',
                    'event_id': None,
                })

        client.table("ficha").update(changes).eq("id", ficha_id).execute()
        if wants_json:
            updated_ficha = {**ficha, **changes}
            final_reaction = (
                'like' if updated_ficha.get(f'gostei_{side}')
                else 'love' if updated_ficha.get(f'amei_{side}')
                else None
            )
            return jsonify({
                'success': True,
                'ficha_id': ficha_id,
                'perfil_id': profile_id,
                'reaction': final_reaction,
                'liked': bool(updated_ficha.get(f'gostei_{side}')),
                'loved': bool(updated_ficha.get(f'amei_{side}')),
                'matched': bool(updated_ficha.get('match')),
            })

        position = request.form.get('position', 0, type=int)
        next_position = position if action == 'skip' else position + 1
        if changes.get('match'):
            return redirect(url_for('matches'))
        return redirect(url_for('encounter', position=next_position))
    except HTTPException as error:
        if wants_json:
            return jsonify({'success': False, 'error': error.description}), error.code
        raise
    except SUPABASE_ERRORS:
        app.logger.exception("Falha ao salvar reação no perfil %s.", ficha_id)
        if wants_json:
            return jsonify({'success': False, 'error': 'Não foi possível salvar sua reação. Tente novamente.'}), 503
        flash('Não foi possível salvar sua reação. Tente novamente.', 'danger')
        return redirect(url_for('home'))
    except Exception:
        app.logger.exception("Erro inesperado ao salvar reação no perfil %s.", ficha_id)
        if wants_json:
            return jsonify({'success': False, 'error': 'Erro inesperado ao salvar sua reação.'}), 500
        flash('Não foi possível salvar sua reação. Tente novamente.', 'danger')
        return redirect(url_for('home'))


@app.route('/restaurant', methods=['GET', 'POST'])
def restaurant_dashboard():
    access_token = app.config.get('RESTAURANT_ACCESS_TOKEN', '')
    if not access_token:
        abort(404)
    if request.method == 'POST':
        supplied_token = request.form.get('access_token', '')
        if not hmac.compare_digest(access_token, supplied_token):
            flash('Credencial inválida.', 'danger')
        else:
            session['restaurant_authenticated'] = True
            return redirect(url_for('restaurant_dashboard'))
    if not session.get('restaurant_authenticated'):
        return render_template('restaurant.html', authenticated=False)

    client = get_supabase_client()
    event = _active_event(client)
    match_items = []
    confirmed_count = 0
    if event:
        fichas = client.table("ficha").select("id,id_user_a,id_user_b,status,user1_confirmed,user2_confirmed").eq("event_id", event['id']).eq("match", 1).execute().data or []
        confirmed_fichas = [ficha for ficha in fichas if _match_state(ficha) == 'confirmed']
        confirmed_count = len(confirmed_fichas)
        user_ids = sorted({person_id for ficha in confirmed_fichas for person_id in (ficha['id_user_a'], ficha['id_user_b'])})
        users = client.table("user").select("id,nome").in_("id", user_ids).execute().data if user_ids else []
        users_by_id = {person['id']: person for person in (users or [])}
        for ficha in confirmed_fichas:
            first_names = []
            for person_id in (ficha['id_user_a'], ficha['id_user_b']):
                person = users_by_id.get(person_id, {})
                first_names.append((person.get('nome') or 'Participante').split()[0])
            match_items.append({'names': first_names})
    return render_template(
        'restaurant.html', authenticated=True, event=event,
        confirmed_count=confirmed_count,
        remaining=max((event or {}).get('capacidade_casais', 0) - confirmed_count, 0),
        matches=match_items,
    )


@app.route('/restaurant/logout', methods=['POST'])
def restaurant_logout():
    session.pop('restaurant_authenticated', None)
    return redirect(url_for('restaurant_dashboard'))

@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    if request.method == 'POST':
        values = parse_profile_form(request.form)
        password = request.form.get('nova_senha', '')
        confirmation = request.form.get('confirmar_senha', '')
        if password or confirmation:
            if len(password) < 6 or password != confirmation:
                flash('A nova senha deve ter seis caracteres e coincidir com a confirmação.', 'danger')
                return render_template('profile.html', user=request.form, form_data=request.form)
            values['senha'] = generate_password_hash(password)
        values['imc'] = calculate_imc(values['peso'], values['altura'])
        client = get_supabase_client()
        current_users = client.table("user").select("fotos").eq("id", user_id).limit(1).execute().data or []
        current_user = current_users[0] if current_users else None
        if not current_user:
            session.clear()
            return redirect(url_for('login'))
        current_photos = list(current_user.get('fotos') or [])
        remove_requested = set(request.form.getlist('remover_fotos'))
        removed_photos = []
        retained_photos = []
        for photo in current_photos:
            if photo in remove_requested:
                removed_photos.append(photo)
            else:
                retained_photos.append(photo)
        new_photos = save_uploaded_photos(request.files.getlist('fotos'), app.config['PHOTO_UPLOAD_FOLDER'], existing_count=len(retained_photos))
        values['fotos'] = retained_photos + new_photos
        try:
            client.rpc(
                "update_profile_and_reset_interactions",
                {"p_user_id": user_id, "p_profile": values},
            ).execute()
        except Exception:
            delete_uploaded_photos(new_photos, app.config['PHOTO_UPLOAD_FOLDER'])
            app.logger.exception("Falha ao atualizar perfil e resetar interações do usuário %s.", user_id)
            flash('Não foi possível atualizar o perfil. Tente novamente.', 'danger')
            return render_template(
                'profile.html', user=request.form, form_data=request.form,
                existing_photos=retained_photos,
            ), 503
        delete_uploaded_photos(removed_photos, app.config['PHOTO_UPLOAD_FOLDER'])
        flash('Perfil atualizado.', 'success')
        return redirect(url_for('profile'))
    users = get_supabase_client().table("user").select("*").eq("id", user_id).limit(1).execute().data or []
    user = users[0] if users else None
    if not user:
        session.clear()
        return redirect(url_for('login'))
    return render_template('profile.html', user=user, form_data=user, existing_photos=user.get('fotos') or [])


@app.route('/uploads/<filename>')
def uploaded_photo(filename):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return send_from_directory(app.config['PHOTO_UPLOAD_FOLDER'], filename, mimetype='image/jpeg')


@app.route('/profile/delete', methods=['POST'])
def delete_profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    client = get_supabase_client()
    users = client.table("user").select("fotos").eq("id", user_id).limit(1).execute().data or []
    user = users[0] if users else None
    client.table("ficha").delete().eq("id_user_a", user_id).execute()
    client.table("ficha").delete().eq("id_user_b", user_id).execute()
    client.table("user").delete().eq("id", user_id).execute()
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
