import fnmatch
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
from compatibility import Compatibilidade
from PIL import Image
from postgrest.exceptions import APIError
from werkzeug.datastructures import FileStorage

from app import app
from db import SupabaseConfigurationError, get_supabase_client
from photo_storage import MAX_PHOTO_BYTES, delete_uploaded_photos, save_uploaded_photos
from profile_data import INTEREST_FIELDS, PROFILE_INTEGER_DEFAULTS, parse_profile_form


class FakeSupabaseQuery:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name
        self.action = "select"
        self.payload = None
        self.filters = []
        self.limit_value = None
        self.conflict_fields = ()

    def select(self, _columns="*"):
        return self

    def eq(self, field, value):
        self.filters.append(("eq", field, value))
        return self

    def neq(self, field, value):
        self.filters.append(("neq", field, value))
        return self

    def in_(self, field, values):
        self.filters.append(("in", field, values))
        return self

    def contains(self, field, values):
        self.filters.append(("contains", field, values))
        return self

    def like(self, field, pattern):
        self.filters.append(("like", field, pattern))
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def insert(self, payload):
        self.action = "insert"
        self.payload = [dict(row) for row in payload] if isinstance(payload, list) else dict(payload)
        return self

    def update(self, payload):
        self.action, self.payload = "update", dict(payload)
        return self

    def delete(self):
        self.action = "delete"
        return self

    def upsert(self, payload, on_conflict=""):
        self.action = "upsert"
        self.payload = [dict(row) for row in payload] if isinstance(payload, list) else dict(payload)
        self.conflict_fields = tuple(on_conflict.split(","))
        return self

    def execute(self):
        if self.client.error_on == self.action:
            raise self.client.error
        rows = self.client.tables.setdefault(self.table_name, [])

        def matches(row):
            for operation, field, expected in self.filters:
                if operation == "eq" and row.get(field) != expected:
                    return False
                if operation == "neq" and row.get(field) == expected:
                    return False
                if operation == "in" and row.get(field) not in expected:
                    return False
                if operation == "contains" and not all(value in (row.get(field) or []) for value in expected):
                    return False
                if operation == "like" and not fnmatch.fnmatchcase(row.get(field, ""), expected.replace("%", "*")):
                    return False
            return True

        matching_rows = [row for row in rows if matches(row)]
        self.client.calls.append((self.table_name, self.action, self.payload, list(self.filters)))
        if self.action == "select":
            result = matching_rows[:self.limit_value] if self.limit_value else matching_rows
        elif self.action == "insert":
            payloads = self.payload if isinstance(self.payload, list) else [self.payload]
            result = []
            for payload in payloads:
                row = dict(payload)
                row.setdefault("id", max((item.get("id", 0) for item in rows), default=0) + 1)
                rows.append(row)
                result.append(row)
        elif self.action == "update":
            for row in matching_rows:
                row.update(self.payload)
            result = matching_rows
        elif self.action == "delete":
            self.client.tables[self.table_name] = [row for row in rows if not matches(row)]
            result = matching_rows
        else:
            payloads = self.payload if isinstance(self.payload, list) else [self.payload]
            result = []
            for payload in payloads:
                row = next(
                    (item for item in rows if all(item.get(field) == payload.get(field) for field in self.conflict_fields)),
                    None,
                )
                if row:
                    row.update(payload)
                else:
                    row = dict(payload)
                    row.setdefault("id", max((item.get("id", 0) for item in rows), default=0) + 1)
                    rows.append(row)
                result.append(row)
        return SimpleNamespace(data=result)


class FakeSupabaseClient:
    def __init__(self, tables=None, error_on=None, error=None):
        self.tables = tables or {}
        self.error_on = error_on
        self.error = error
        self.calls = []

    def table(self, table_name):
        return FakeSupabaseQuery(self, table_name)

    def rpc(self, function_name, params):
        return FakeSupabaseRPC(self, function_name, params)


class FakeSupabaseRPC:
    def __init__(self, client, function_name, params):
        self.client = client
        self.function_name = function_name
        self.params = params

    def execute(self):
        client = self.client
        function_name = self.function_name
        params = self.params
        if client.error_on == "rpc":
            raise client.error
        client.calls.append(("rpc", function_name, dict(params), []))
        if function_name == "record_profile_view":
            reader_id = params["p_reader_id"]
            target_id = params["p_target_id"]
            expected_count = params["p_expected_count"]
            views = client.tables.setdefault("visualizacoes_perfil", [])
            view = next((
                row for row in views
                if row.get("usuario_leitor_id") == reader_id
                and row.get("usuario_alvo_id") == target_id
            ), None)
            current_count = view.get("contador_aparicoes", 0) if view else 0
            if current_count == expected_count:
                if view:
                    view["contador_aparicoes"] = current_count + 1
                else:
                    view = {
                        "usuario_leitor_id": reader_id,
                        "usuario_alvo_id": target_id,
                        "contador_aparicoes": 1,
                    }
                    views.append(view)
                current_count += 1
            return SimpleNamespace(data=current_count)
        if function_name != "update_profile_and_reset_interactions":
            raise AssertionError(f"RPC inesperada: {function_name}")
        user_id = params["p_user_id"]
        profile = params["p_profile"]
        users = client.tables.setdefault("user", [])
        user = next((row for row in users if row.get("id") == user_id), None)
        if not user:
            raise AssertionError("Perfil não encontrado")
        user.update(profile)
        client.tables["ficha"] = [
            row for row in client.tables.get("ficha", [])
            if row.get("id_user_a") != user_id and row.get("id_user_b") != user_id
        ]
        client.tables["visualizacoes_perfil"] = [
            row for row in client.tables.get("visualizacoes_perfil", [])
            if row.get("usuario_leitor_id") != user_id and row.get("usuario_alvo_id") != user_id
        ]
        return SimpleNamespace(data=[user])


def valid_form():
    form = {"nome": "Ana", "email": "ana@example.com"}
    form.update({key: str(value) for key, value in PROFILE_INTEGER_DEFAULTS.items()})
    for key, _label in INTEREST_FIELDS[:3]:
        form[key] = "1"
    form.update({"senha": "segredo123", "confirmar_senha": "segredo123"})
    return form


def user_row(user_id, name):
    row = dict(PROFILE_INTEGER_DEFAULTS)
    row.update({"id": user_id, "nome": name, "email": f"{name.lower()}@example.com"})
    return row


def image_upload(image_format="PNG"):
    contents = BytesIO()
    Image.new("RGBA", (8, 8), (220, 40, 70, 180)).save(contents, format=image_format)
    return FileStorage(stream=BytesIO(contents.getvalue()), filename=f"photo.{image_format.lower()}")


class PhotoStorageTests(unittest.TestCase):
    def test_upload_directory_is_outside_public_static_files(self):
        self.assertTrue(
            Path(app.config["PHOTO_UPLOAD_FOLDER"]).is_relative_to(app.instance_path)
        )
        self.assertFalse(
            Path(app.config["PHOTO_UPLOAD_FOLDER"]).is_relative_to(app.static_folder)
        )

    def test_converts_upload_to_jpeg_with_generated_name(self):
        with TemporaryDirectory() as folder:
            names = save_uploaded_photos([image_upload()], folder)
            self.assertEqual(len(names), 1)
            self.assertRegex(names[0], r"^[0-9a-f]{32}\.jpg$")
            with Image.open(Path(folder) / names[0]) as image:
                self.assertEqual(image.format, "JPEG")
                self.assertEqual(image.mode, "RGB")

    def test_rejects_invalid_file_and_more_than_five_photos(self):
        with TemporaryDirectory() as folder:
            invalid = FileStorage(stream=BytesIO(b"not an image"), filename="photo.png")
            with self.assertRaises(ValueError):
                save_uploaded_photos([invalid], folder)
            with self.assertRaises(ValueError):
                save_uploaded_photos([image_upload()], folder, existing_count=5)
            oversized = FileStorage(
                stream=BytesIO(b"x" * (MAX_PHOTO_BYTES + 1)), filename="large.png"
            )
            with self.assertRaises(ValueError):
                save_uploaded_photos([oversized], folder)

    def test_removes_only_generated_photo_names(self):
        with TemporaryDirectory() as folder:
            names = save_uploaded_photos([image_upload()], folder)
            delete_uploaded_photos([names[0], "../outside.jpg"], folder)
            self.assertFalse((Path(folder) / names[0]).exists())


class ProfileValidationTests(unittest.TestCase):
    def test_supabase_client_requires_server_secret_key(self):
        get_supabase_client.cache_clear()
        with patch.dict("os.environ", {"SUPABASE_URL": "https://example.supabase.co"}, clear=True):
            with self.assertRaises(SupabaseConfigurationError):
                get_supabase_client()
        get_supabase_client.cache_clear()

    def test_supabase_client_uses_server_secret_key(self):
        get_supabase_client.cache_clear()
        expected = object()
        with patch.dict(
            "os.environ",
            {"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_SECRET_KEY": "sb_secret_test"},
            clear=True,
        ), patch("db.create_client", return_value=expected) as create_client:
            self.assertIs(get_supabase_client(), expected)
        create_client.assert_called_once_with("https://example.supabase.co", "sb_secret_test")
        get_supabase_client.cache_clear()

    def test_supabase_client_rejects_publishable_key_for_server_database_access(self):
        get_supabase_client.cache_clear()
        with patch.dict(
            "os.environ",
            {"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_SECRET_KEY": "sb_publishable_test"},
            clear=True,
        ):
            with self.assertRaises(SupabaseConfigurationError):
                get_supabase_client()
        get_supabase_client.cache_clear()

    def test_accepts_valid_profile_and_defaults_appearance_preferences(self):
        values = parse_profile_form(valid_form())
        self.assertEqual(values["nome"], "Ana")
        self.assertEqual(values["preto"], 1)
        self.assertEqual(values["clara"], 1)

    def test_rejects_invalid_date_and_too_few_interests(self):
        form = valid_form()
        with self.assertRaises(ValueError):
            parse_profile_form(dict(form, mes="2", dia="30"))

        form.update({"viagens": "0", "livros": "0", "causa": "0"})
        with self.assertRaises(ValueError):
            parse_profile_form(form)


class SupabaseDataTests(unittest.TestCase):
    def test_compatibility_upserts_pair_without_resetting_interactions(self):
        user_a = user_row(1, "Ana")
        user_a.update({"genero": 0, "pref_genero": 1, "imc": 22})
        user_b = user_row(2, "Bea")
        user_b.update({"genero": 1, "pref_genero": 0, "imc": 23})
        user_c = user_row(3, "Cris")
        user_c.update({"genero": 1, "pref_genero": 0, "imc": 24})
        ficha = {
            "id": 7, "id_user_a": 1, "id_user_b": 2,
            "compatibilidade": 0, "gostei_a": 1, "amei_a": 0,
            "recusou_a": 0, "gostei_b": 0, "amei_b": 0,
            "recusou_b": 0, "match": 0,
        }
        client = FakeSupabaseClient({"user": [user_a, user_b, user_c], "ficha": [ficha]})

        with patch("compatibility.get_supabase_client", return_value=client):
            Compatibilidade().calculate_compatibility(1)

        self.assertEqual(len(client.tables["ficha"]), 2)
        self.assertEqual(client.tables["ficha"][0]["id_user_a"], 1)
        self.assertEqual(client.tables["ficha"][0]["id_user_b"], 2)
        self.assertEqual(client.tables["ficha"][0]["gostei_a"], 1)
        self.assertGreater(client.tables["ficha"][0]["compatibilidade"], 0)
        upsert_calls = [call for call in client.calls if call[0] == "ficha" and call[1] == "upsert"]
        self.assertEqual(len(upsert_calls), 1)
        self.assertEqual(len(upsert_calls[0][2]), 2)


class FlaskFlowTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, SECRET_KEY="test-secret")
        self.client = app.test_client()

    def test_register_page_renders_full_profile_form(self):
        response = self.client.get("/register")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Causas sociais", response.data)
        self.assertIn(b"Confirme a senha", response.data)
        nav = response.data.split(b"<nav", 1)[1].split(b"</nav>", 1)[0]
        self.assertIn(b">Entrar</a>", nav)
        self.assertNotIn(b">Cadastrar</a>", nav)
        self.assertIn(b"btn-complete", response.data)
        self.assertNotIn(b"Preencher dados de teste", response.data)
        self.assertIn(b'enctype="multipart/form-data"', response.data)
        self.assertIn(b'name="fotos"', response.data)
        self.assertLess(
            response.data.index("Informações básicas".encode()),
            response.data.index(b"Acesso"),
        )
        self.assertLess(
            response.data.index(b"Acesso"), response.data.index(b"Nascimento")
        )
        self.assertIn(b"Cabelos", response.data)
        self.assertIn(b"Tom de pele", response.data)
        self.assertIn(b">Preto</span>", response.data)
        self.assertIn(b">Clara</span>", response.data)
        self.assertNotIn(b"Cabelo preto", response.data)
        self.assertNotIn(b">Pele clara</span>", response.data)

    def test_login_keeps_registration_link_in_card_but_not_top_navigation(self):
        response = self.client.get("/login")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Cadastre-se", response.data)
        nav = response.data.split(b"<nav", 1)[1].split(b"</nav>", 1)[0]
        self.assertNotIn(b'href="/register"', nav)

    def test_login_shows_test_batch_button_when_local_photo_is_available(self):
        with TemporaryDirectory() as folder:
            Image.new("RGB", (8, 8), "green").save(Path(folder) / "sample.jpg", format="JPEG")
            with patch.dict(
                app.config,
                {"TEST_PROFILE_BATCH_FOLDER": folder, "ENABLE_TEST_PROFILE_GENERATION": True},
            ):
                response = self.client.get("/login")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Gerar 100 perfis de teste", response.data)

    def test_login_keeps_test_batch_button_visible_before_photo_is_added(self):
        with TemporaryDirectory() as empty_folder, patch.dict(
            app.config,
            {"TEST_PROFILE_BATCH_FOLDER": empty_folder, "ENABLE_TEST_PROFILE_GENERATION": True},
        ):
            response = self.client.get("/login")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Gerar 100 perfis de teste", response.data)

    def test_generate_100_test_profiles_uses_photo_and_replaces_only_test_batch(self):
        with TemporaryDirectory() as folder:
            source_path = Path(folder) / "sample.jpg"
            Image.new("RGB", (8, 8), "green").save(source_path, format="JPEG")
            upload_folder = Path(folder) / "uploads"
            old_photo = f"{'a' * 32}.jpg"
            upload_folder.mkdir()
            Image.new("RGB", (8, 8), "red").save(upload_folder / old_photo, format="JPEG")
            existing_test_user = user_row(3, "Old test")
            existing_test_user.update({"email": "date-night-test-old@example.test", "fotos": [old_photo]})
            real_user = user_row(1, "Ana")
            old_ficha = {"id": 12, "id_user_a": 1, "id_user_b": 3}
            client = FakeSupabaseClient(
                {"user": [real_user, existing_test_user], "ficha": [old_ficha]}
            )

            with patch.dict(
                app.config,
                {
                    "TEST_PROFILE_BATCH_FOLDER": folder,
                    "PHOTO_UPLOAD_FOLDER": str(upload_folder),
                    "ENABLE_TEST_PROFILE_GENERATION": True,
                },
            ), patch("app.get_supabase_client", return_value=client):
                with self.client.session_transaction() as session:
                    session["test_profiles_csrf_token"] = "test-token"
                response = self.client.post(
                    "/test-profiles/generate", data={"csrf_token": "test-token"}
                )

            test_users = [row for row in client.tables["user"] if row["email"].startswith("date-night-test-")]
            self.assertEqual(response.status_code, 302)
            self.assertEqual(len(test_users), 100)
            self.assertEqual(len({row["fotos"][0] for row in test_users}), 100)
            self.assertTrue(all((upload_folder / row["fotos"][0]).exists() for row in test_users))
            self.assertEqual(client.tables["ficha"], [])
            self.assertFalse((upload_folder / old_photo).exists())

    def test_test_profile_generation_requires_local_csrf_token(self):
        response = self.client.post(
            "/test-profiles/generate",
            data={"csrf_token": "wrong-token"},
            environ_overrides={"REMOTE_ADDR": "192.0.2.10"},
        )
        self.assertEqual(response.status_code, 404)

    def test_register_saves_profile_and_password_hash(self):
        client = FakeSupabaseClient({"user": []})
        with patch("app.get_supabase_client", return_value=client):
            response = self.client.post("/register", data=valid_form())

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/login")
        self.assertEqual(len(client.tables["user"]), 1)
        self.assertTrue(client.tables["user"][0]["senha"].startswith(("scrypt:", "pbkdf2:")))

    def test_register_reports_database_connection_timeout(self):
        client = FakeSupabaseClient(
            {"user": []}, error_on="insert", error=httpx.ConnectError("connection timed out")
        )
        with patch("app.get_supabase_client", return_value=client), patch("app.app.logger.exception"):
            response = self.client.post("/register", data=valid_form())

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Sem conex\xc3\xa3o com o Supabase", response.data)
        self.assertGreater(
            response.data.index(b"Sem conex\xc3\xa3o com o Supabase"),
            response.data.index(b"btn-complete"),
        )

    def test_register_explains_rls_permission_error(self):
        error = APIError({
            "code": "42501",
            "message": "new row violates row-level security policy",
            "details": None,
            "hint": None,
        })
        client = FakeSupabaseClient({"user": []}, error_on="insert", error=error)
        with patch("app.get_supabase_client", return_value=client), patch("app.app.logger.exception"):
            response = self.client.post("/register", data=valid_form())

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"permiss\xc3\xa3o ou pol\xc3\xadtica RLS", response.data)

    def test_register_explains_missing_supabase_schema(self):
        error = APIError({
            "code": "PGRST205",
            "message": "table not found in schema cache",
            "details": None,
            "hint": None,
        })
        client = FakeSupabaseClient({"user": []}, error_on="insert", error=error)
        with patch("app.get_supabase_client", return_value=client), patch("app.app.logger.exception"):
            response = self.client.post("/register", data=valid_form())

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Aplique schema_postgresql.sql", response.data)

    def test_register_persists_uploaded_photo_names(self):
        client = FakeSupabaseClient({"user": []})
        upload = image_upload()
        form = valid_form()
        form["fotos"] = (upload.stream, upload.filename, "image/png")
        with TemporaryDirectory() as folder, patch.dict(
            app.config, {"PHOTO_UPLOAD_FOLDER": folder}
        ), patch("app.get_supabase_client", return_value=client):
            response = self.client.post("/register", data=form)
            created_user = client.tables["user"][0]
            photo_names = created_user["fotos"]
            self.assertEqual(response.status_code, 302)
            self.assertEqual(len(photo_names), 1)
            self.assertTrue((Path(folder) / photo_names[0]).exists())

    def test_legacy_password_is_rehashed_after_login(self):
        client = FakeSupabaseClient({"user": [{"id": 1, "email": "ana@example.com", "senha": "legacy-secret"}]})
        with patch("app.get_supabase_client", return_value=client):
            response = self.client.post(
                "/login", data={"email": "ana@example.com", "senha": "legacy-secret"}
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/home")
        self.assertTrue(client.tables["user"][0]["senha"].startswith(("scrypt:", "pbkdf2:")))

    def test_home_lists_other_profiles_and_links_to_encounter(self):
        current_user = user_row(1, "Ana")
        candidate = user_row(2, "Bea")
        ficha = {
            "id": 8, "id_user_a": 1, "id_user_b": 2,
            "compatibilidade": 19, "gostei_a": 0, "amei_a": 0,
            "recusou_a": 0, "gostei_b": 0, "amei_b": 0,
            "recusou_b": 0, "match": 0,
        }
        candidate["fotos"] = [f"{'b' * 32}.jpg"]
        client = FakeSupabaseClient({"user": [current_user, candidate], "ficha": [ficha]})
        with self.client.session_transaction() as session:
            session["user_id"] = 1
        with patch("app.Compatibilidade") as compatibility, patch(
            "app.get_supabase_client", return_value=client
        ):
            compatibility.return_value.calculate_compatibility = MagicMock()
            response = self.client.get("/home")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Bea", response.data)
        self.assertIn(b"19 pontos de afinidade", response.data)
        self.assertIn(b"href=\"/encounter?position=0\"", response.data)
        self.assertIn(f"/uploads/{'b' * 32}.jpg".encode(), response.data)

    def test_home_does_not_repeat_profile_after_definitive_reaction(self):
        current_user = user_row(1, "Ana")
        candidate = user_row(2, "Bea")
        ficha = {
            "id": 8, "id_user_a": 1, "id_user_b": 2,
            "compatibilidade": 19, "gostei_a": 1, "amei_a": 0,
            "recusou_a": 0, "gostei_b": 0, "amei_b": 0,
            "recusou_b": 0, "match": 0,
        }
        client = FakeSupabaseClient({"user": [current_user, candidate], "ficha": [ficha]})
        with self.client.session_transaction() as session:
            session["user_id"] = 1
        with patch("app.Compatibilidade") as compatibility, patch(
            "app.get_supabase_client", return_value=client
        ):
            compatibility.return_value.calculate_compatibility = MagicMock()
            response = self.client.get("/home")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'data-profile-id="2"', response.data)
        self.assertIn(b"Nenhum perfil dispon\xc3\xadvel", response.data)

    def test_home_rotates_photos_for_three_appearances_and_filters_exhausted_profiles(self):
        current_user = user_row(1, "Ana")
        first_candidate = user_row(2, "Bea")
        first_candidate["fotos"] = ["bea-1.jpg", "bea-2.jpg", "bea-3.jpg"]
        fallback_candidate = user_row(3, "Cris")
        fallback_candidate["fotos"] = ["cris-1.jpg"]
        exhausted_candidate = user_row(4, "Dani")
        exhausted_candidate["fotos"] = ["dani-1.jpg", "dani-2.jpg", "dani-3.jpg"]
        reacted_candidate = user_row(5, "Eli")
        fichas = [
            {"id": 20 + index, "id_user_a": 1, "id_user_b": target_id,
             "compatibilidade": 20, "gostei_a": 0, "amei_a": 0, "recusou_a": 0,
             "gostei_b": 0, "amei_b": 0, "recusou_b": 0, "match": 0}
            for index, target_id in enumerate((2, 3, 4, 5))
        ]
        fichas[-1]["gostei_a"] = 1
        client = FakeSupabaseClient({
            "user": [current_user, first_candidate, fallback_candidate, exhausted_candidate, reacted_candidate],
            "ficha": fichas,
            "visualizacoes_perfil": [
                {"usuario_leitor_id": 1, "usuario_alvo_id": 3, "contador_aparicoes": 2},
                {"usuario_leitor_id": 1, "usuario_alvo_id": 4, "contador_aparicoes": 3},
            ],
        })
        with self.client.session_transaction() as session:
            session["user_id"] = 1
        with patch("app.Compatibilidade") as compatibility, patch(
            "app.get_supabase_client", return_value=client
        ):
            compatibility.return_value.calculate_compatibility = MagicMock()
            response = self.client.get("/home")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.count(b'data-profile-id="2"'), 3)
        self.assertEqual(response.data.count(b'data-profile-id="3"'), 1)
        self.assertNotIn(b'data-profile-id="4"', response.data)
        self.assertNotIn(b'data-profile-id="5"', response.data)
        self.assertLess(response.data.index(b"bea-1.jpg"), response.data.index(b"bea-2.jpg"))
        self.assertLess(response.data.index(b"bea-2.jpg"), response.data.index(b"bea-3.jpg"))
        self.assertIn(b"cris-1.jpg", response.data)

    def test_profile_view_counter_is_idempotent_per_appearance(self):
        ficha = {
            "id": 8, "id_user_a": 1, "id_user_b": 2,
            "gostei_a": 0, "amei_a": 0, "gostei_b": 0, "amei_b": 0,
        }
        client = FakeSupabaseClient({"ficha": [ficha]})
        with self.client.session_transaction() as session:
            session["user_id"] = 1
        with patch("app.get_supabase_client", return_value=client):
            first = self.client.post("/profiles/2/view", data={"expected_count": 0})
            duplicate = self.client.post("/profiles/2/view", data={"expected_count": 0})
            second = self.client.post("/profiles/2/view", data={"expected_count": 1})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json["contador_aparicoes"], 1)
        self.assertEqual(duplicate.json["contador_aparicoes"], 1)
        self.assertEqual(second.json["contador_aparicoes"], 2)
        self.assertEqual(client.tables["visualizacoes_perfil"][0]["contador_aparicoes"], 2)

    def test_home_shows_plain_dev_button_at_bottom_right(self):
        current_user = user_row(1, "Ana")
        client = FakeSupabaseClient({"user": [current_user], "ficha": []})
        with self.client.session_transaction() as session:
            session["user_id"] = 1
        with patch("app.Compatibilidade") as compatibility, patch(
            "app.get_supabase_client", return_value=client
        ):
            compatibility.return_value.calculate_compatibility = MagicMock()
            response = self.client.get("/home")

        stylesheet = (Path(app.static_folder) / "css" / "style.css").read_text(encoding="utf-8")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'class="btn btn-secondary debug-open"', response.data)
        self.assertIn(b"home.js?v=feed-views-v1", response.data)
        self.assertIn(b"\nDev\n</button>", response.data)
        self.assertNotIn("✧".encode(), response.data)
        self.assertIn("right: 15px;", stylesheet)
        self.assertIn("bottom: 15px;", stylesheet)

    def test_home_reaction_script_reads_form_action_attribute(self):
        script = (Path(app.static_folder) / "js" / "home.js").read_text(encoding="utf-8")
        self.assertIn("form.getAttribute('action')", script)
        self.assertIn("fetch(actionUrl", script)
        self.assertNotIn("fetch(form.action", script)

    def test_home_backend_error_is_logged_and_returns_retry_message(self):
        with self.client.session_transaction() as session:
            session["user_id"] = 1
        with patch("app._load_match_candidates", side_effect=RuntimeError("backend timeout")), patch(
            "app.app.logger.exception"
        ) as log_exception:
            response = self.client.get("/home")

        self.assertEqual(response.status_code, 503)
        self.assertIn(b"N\xc3\xa3o foi poss\xc3\xadvel carregar a Home agora", response.data)
        log_exception.assert_called_once()

    def test_home_explains_missing_supabase_table(self):
        error = APIError({
            "code": "PGRST205",
            "message": "table not found in schema cache",
            "details": None,
            "hint": None,
        })
        client = FakeSupabaseClient({"user": []}, error_on="select", error=error)
        with self.client.session_transaction() as session:
            session["user_id"] = 1
        with patch("app.Compatibilidade") as compatibility, patch(
            "app.get_supabase_client", return_value=client
        ), patch("app.app.logger.exception") as log_exception:
            compatibility.return_value.calculate_compatibility = MagicMock()
            response = self.client.get("/home")

        self.assertEqual(response.status_code, 503)
        self.assertIn(b"N\xc3\xa3o foi poss\xc3\xadvel carregar a Home agora", response.data)
        log_exception.assert_called_once()

    def test_encounter_shows_one_profile_with_navigation_and_actions(self):
        current_user = user_row(1, "Ana")
        candidate = user_row(2, "Bea")
        ficha = {
            "id": 8, "id_user_a": 1, "id_user_b": 2,
            "compatibilidade": 19, "gostei_a": 0, "amei_a": 0,
            "recusou_a": 0, "gostei_b": 0, "amei_b": 0,
            "recusou_b": 0, "match": 0,
        }
        client = FakeSupabaseClient({"user": [current_user, candidate], "ficha": [ficha]})
        with self.client.session_transaction() as session:
            session["user_id"] = 1
        with patch("app.Compatibilidade") as compatibility, patch(
            "app.get_supabase_client", return_value=client
        ):
            compatibility.return_value.calculate_compatibility = MagicMock()
            response = self.client.get("/encounter")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Bea", response.data)
        self.assertIn(b"Anterior", response.data)
        self.assertIn(b"Gostei", response.data)
        self.assertIn(b"Amei", response.data)
        self.assertIn(b"home.js", response.data)

    def test_uploaded_photo_is_only_available_to_logged_in_related_user(self):
        with TemporaryDirectory() as folder:
            photo_name = save_uploaded_photos([image_upload()], folder)[0]
            current_user = user_row(1, "Ana")
            other_user = user_row(2, "Bea")
            other_user["fotos"] = [photo_name]
            ficha = {"id": 8, "id_user_a": 1, "id_user_b": 2}
            client = FakeSupabaseClient({"user": [current_user, other_user], "ficha": [ficha]})
            with patch.dict(app.config, {"PHOTO_UPLOAD_FOLDER": folder}), patch(
                "app.get_supabase_client", return_value=client
            ):
                response = self.client.get(f"/uploads/{photo_name}")
            self.assertEqual(response.status_code, 302)
            with self.client.session_transaction() as session:
                session["user_id"] = 1
            with patch.dict(app.config, {"PHOTO_UPLOAD_FOLDER": folder}), patch(
                "app.get_supabase_client", return_value=client
            ):
                response = self.client.get(f"/uploads/{photo_name}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "image/jpeg")
            response.close()

    def test_like_action_updates_status_and_match(self):
        ficha = {"id": 8, "id_user_a": 1, "id_user_b": 2, "gostei_a": 0, "gostei_b": 0, "amei_a": 0, "amei_b": 0, "match": 0}
        client = FakeSupabaseClient({"ficha": [ficha]})
        with self.client.session_transaction() as session:
            session["user_id"] = 1
        with patch("app.get_supabase_client", return_value=client):
            response = self.client.post(
                "/matches/8/action", data={"action": "like", "position": "0"}
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(client.tables["ficha"][0]["gostei_a"], 1)
        self.assertEqual(client.tables["ficha"][0]["match"], 0)
        self.assertIn("/encounter?position=1", response.location)

    def test_ajax_reaction_returns_json_and_overwrites_previous_vote(self):
        ficha = {
            "id": 8, "id_user_a": 1, "id_user_b": 2,
            "gostei_a": 0, "amei_a": 1, "gostei_b": 0, "amei_b": 0, "match": 0,
        }
        client = FakeSupabaseClient({"ficha": [ficha]})
        with self.client.session_transaction() as session:
            session["user_id"] = 1
        headers = {"X-Requested-With": "XMLHttpRequest"}
        with patch("app.get_supabase_client", return_value=client):
            like_response = self.client.post(
                "/matches/8/action",
                data={"perfil_id": "2", "tipo_reacao": "gostei"},
                headers=headers,
            )
            love_response = self.client.post(
                "/matches/8/action",
                data={"perfil_id": "2", "tipo_reacao": "amei"},
                headers=headers,
            )
            toggle_response = self.client.post(
                "/matches/8/action",
                data={"perfil_id": "2", "tipo_reacao": "amei"},
                headers=headers,
            )

        self.assertEqual(like_response.status_code, 200)
        self.assertTrue(like_response.is_json)
        self.assertIsNone(like_response.location)
        self.assertEqual(like_response.json["reaction"], "like")
        self.assertFalse(like_response.json["loved"])
        self.assertEqual(love_response.status_code, 200)
        self.assertTrue(love_response.is_json)
        self.assertIsNone(love_response.location)
        self.assertEqual(love_response.json["reaction"], "love")
        self.assertTrue(love_response.json["loved"])
        self.assertFalse(love_response.json["liked"])
        self.assertEqual(toggle_response.status_code, 200)
        self.assertIsNone(toggle_response.json["reaction"])
        self.assertFalse(toggle_response.json["liked"])
        self.assertFalse(toggle_response.json["loved"])
        self.assertFalse(client.tables["ficha"][0]["gostei_a"])
        self.assertFalse(client.tables["ficha"][0]["amei_a"])
        self.assertEqual(len(client.tables["ficha"]), 1)

    def test_ajax_reaction_returns_json_for_unexpected_backend_errors(self):
        with self.client.session_transaction() as session:
            session["user_id"] = 1
        with patch("app.get_supabase_client", side_effect=RuntimeError("unexpected backend error")), patch(
            "app.app.logger.exception"
        ) as log_exception:
            response = self.client.post(
                "/matches/8/action",
                data={"perfil_id": "2", "tipo_reacao": "amei"},
                headers={"X-Requested-With": "XMLHttpRequest"},
            )

        self.assertEqual(response.status_code, 500)
        self.assertTrue(response.is_json)
        self.assertFalse(response.json["success"])
        log_exception.assert_called_once()

    def test_ajax_reaction_returns_json_404_when_match_does_not_exist(self):
        client = FakeSupabaseClient({"ficha": []})
        with self.client.session_transaction() as session:
            session["user_id"] = 1
        with patch("app.get_supabase_client", return_value=client):
            response = self.client.post(
                "/matches/404/action",
                data={"perfil_id": "2", "tipo_reacao": "amei"},
                headers={"X-Requested-With": "XMLHttpRequest"},
            )

        self.assertEqual(response.status_code, 404)
        self.assertTrue(response.is_json)
        self.assertEqual(response.json["error"], "Perfil não encontrado.")

    def test_match_confirmation_waits_for_both_participants(self):
        ficha = {
            "id": 8, "id_user_a": 1, "id_user_b": 2, "match": 1,
            "user1_confirmed": 0, "user2_confirmed": 0, "status": "pending", "event_id": 4,
        }
        event = {
            "id": 4, "data_hora": "2026-10-24T20:00:00-03:00",
            "capacidade_casais": 12, "status": "active",
        }
        client = FakeSupabaseClient({"ficha": [ficha], "evento": [event]})
        with self.client.session_transaction() as session:
            session["user_id"] = 1
        with patch("app.get_supabase_client", return_value=client):
            first_response = self.client.post(
                "/matches/8/confirmation", data={"action": "confirm"}
            )
        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(first_response.json["state"], "waiting")
        self.assertEqual(client.tables["ficha"][0]["status"], "waiting")
        self.assertEqual(client.tables["ficha"][0]["user1_confirmed"], 1)

        with self.client.session_transaction() as session:
            session["user_id"] = 2
        with patch("app.get_supabase_client", return_value=client):
            second_response = self.client.post(
                "/matches/8/confirmation", data={"action": "confirm"}
            )
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(second_response.json["state"], "confirmed")
        self.assertEqual(second_response.json["event_date"], event["data_hora"])
        self.assertEqual(second_response.json["confirmed_count"], 1)
        self.assertEqual(second_response.json["capacity"], 12)
        self.assertEqual(client.tables["ficha"][0]["status"], "confirmed")

    def test_match_confirmation_rejects_nonparticipant(self):
        client = FakeSupabaseClient({"ficha": [{
            "id": 8, "id_user_a": 1, "id_user_b": 2, "match": 1,
            "user1_confirmed": 0, "user2_confirmed": 0,
        }]})
        with self.client.session_transaction() as session:
            session["user_id"] = 3
        with patch("app.get_supabase_client", return_value=client):
            response = self.client.post(
                "/matches/8/confirmation", data={"action": "confirm"}
            )
        self.assertEqual(response.status_code, 403)

    def test_matches_page_shows_pending_confirmation_and_local_debug_panel(self):
        ficha = {
            "id": 8, "id_user_a": 1, "id_user_b": 2, "match": 1,
            "compatibilidade": 25, "status": "pending",
            "user1_confirmed": 0, "user2_confirmed": 0,
        }
        client = FakeSupabaseClient({
            "ficha": [ficha], "user": [user_row(2, "Bea")], "evento": [],
        })
        with self.client.session_transaction() as session:
            session["user_id"] = 1
        with patch("app.get_supabase_client", return_value=client):
            response = self.client.get("/matches")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Confirmar presen\xc3\xa7a", response.data)
        self.assertIn(b"Recusar", response.data)
        self.assertIn(b">Dev</button>", response.data)
        self.assertNotIn(b">Encontros</a>", response.data)
        self.assertLess(response.data.index(b">Home</a>"), response.data.index(b">Perfil</a>"))
        self.assertLess(response.data.index(b">Perfil</a>"), response.data.index(b">Sair</a>"))

    def test_matches_page_survives_missing_event_table(self):
        ficha = {
            "id": 8, "id_user_a": 1, "id_user_b": 2, "match": 1,
            "compatibilidade": 25, "status": "pending",
            "user1_confirmed": 0, "user2_confirmed": 0,
        }
        client = FakeSupabaseClient({
            "ficha": [ficha], "user": [user_row(2, "Bea")],
        })
        original_table = client.table
        schema_error = APIError({
            "code": "PGRST205", "message": "table not found in schema cache",
            "details": None, "hint": None,
        })

        def table(table_name):
            if table_name == "evento":
                raise schema_error
            return original_table(table_name)

        client.table = table
        with self.client.session_transaction() as session:
            session["user_id"] = 1
        with patch("app.get_supabase_client", return_value=client), patch(
            "app.app.logger.exception"
        ) as log_exception:
            response = self.client.get("/matches")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Houve um Match", response.data)
        self.assertNotIn(b"Pr\xc3\xb3ximo evento", response.data)
        log_exception.assert_called_once()

    def test_restaurant_dashboard_only_shows_first_names_and_reservation_data(self):
        event = {
            "id": 4, "nome": "Jantar de outubro", "data_hora": "2026-10-24T20:00:00Z",
            "capacidade_casais": 12, "status": "active", "restaurante": "Casa",
        }
        ficha = {
            "id": 8, "id_user_a": 1, "id_user_b": 2, "event_id": 4, "match": 1,
            "status": "confirmed", "user1_confirmed": 1, "user2_confirmed": 1,
        }
        first = user_row(1, "Ana Silva")
        first.update({"email": "ana.private@example.com"})
        second = user_row(2, "Bea Costa")
        second.update({"email": "bea.private@example.com"})
        client = FakeSupabaseClient({"evento": [event], "ficha": [ficha], "user": [first, second]})
        with self.client.session_transaction() as session:
            session["restaurant_authenticated"] = True
        with patch.dict(app.config, {"RESTAURANT_ACCESS_TOKEN": "restaurant-test-token"}), patch(
            "app.get_supabase_client", return_value=client
        ):
            response = self.client.get("/restaurant")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Ana + Bea", response.data)
        self.assertNotIn(b"Silva", response.data)
        self.assertNotIn(b"Costa", response.data)
        self.assertNotIn(b"private@example.com", response.data)

    def test_skip_keeps_position_after_removing_candidate(self):
        ficha = {"id": 8, "id_user_a": 1, "id_user_b": 2, "recusou_a": 0}
        client = FakeSupabaseClient({"ficha": [ficha]})
        with self.client.session_transaction() as session:
            session["user_id"] = 1
        with patch("app.get_supabase_client", return_value=client):
            response = self.client.post(
                "/matches/8/action", data={"action": "skip", "position": "2"}
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(client.tables["ficha"][0]["recusou_a"], 1)
        self.assertIn("/encounter?position=2", response.location)

    def test_delete_profile_removes_relationships_and_logs_out(self):
        client = FakeSupabaseClient({"user": [user_row(1, "Ana")], "ficha": [{"id": 8, "id_user_a": 1, "id_user_b": 2}]})
        with self.client.session_transaction() as session:
            session["user_id"] = 1
        with patch("app.get_supabase_client", return_value=client):
            response = self.client.post("/profile/delete")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/login")
        self.assertEqual(client.tables["user"], [])
        self.assertEqual(client.tables["ficha"], [])
        with self.client.session_transaction() as session:
            self.assertNotIn("user_id", session)

    def test_profile_page_renders_and_updates_all_fields(self):
        existing = user_row(1, "Ana")
        existing["fotos"] = [f"{'c' * 32}.jpg"]
        client = FakeSupabaseClient({"user": [existing]})
        with self.client.session_transaction() as session:
            session["user_id"] = 1
        with patch("app.get_supabase_client", return_value=client):
            response = self.client.get("/profile")

        self.assertEqual(response.status_code, 200)
        self.assertLess(response.data.index(b">Encontros</a>"), response.data.index(b">Home</a>"))
        self.assertLess(response.data.index(b">Home</a>"), response.data.index(b">Sair</a>"))
        self.assertNotIn(b">Perfil</a>", response.data)
        self.assertNotIn(b"restricoes_alimentares", response.data)
        self.assertIn(b"Salvar perfil", response.data)
        self.assertIn(f"/uploads/{'c' * 32}.jpg".encode(), response.data)
        self.assertIn(b'enctype="multipart/form-data"', response.data)
        self.assertIn(
            "Atenção: Ao modificar o seu perfil, todos os seus matches, reações ('Amei' e 'Gostei') e interações serão resetados".encode(),
            response.data,
        )

        updated_user = user_row(1, "Ana")
        updated_user["fotos"] = []
        client = FakeSupabaseClient({
            "user": [updated_user],
            "ficha": [
                {"id": 10, "id_user_a": 1, "id_user_b": 2},
                {"id": 11, "id_user_a": 2, "id_user_b": 3},
            ],
            "visualizacoes_perfil": [
                {"usuario_leitor_id": 1, "usuario_alvo_id": 2, "contador_aparicoes": 2},
                {"usuario_leitor_id": 2, "usuario_alvo_id": 3, "contador_aparicoes": 1},
            ],
        })
        form = valid_form()
        form.pop("senha")
        form.pop("confirmar_senha")
        with patch("app.get_supabase_client", return_value=client):
            response = self.client.post("/profile", data=form)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/profile")
        self.assertEqual(client.tables["user"][0]["fisico"], int(form["fisico"]))
        self.assertEqual(client.tables["ficha"], [{"id": 11, "id_user_a": 2, "id_user_b": 3}])
        self.assertEqual(len(client.tables["visualizacoes_perfil"]), 1)
        self.assertEqual(client.tables["visualizacoes_perfil"][0]["usuario_leitor_id"], 2)
        self.assertTrue(any(call[0] == "rpc" for call in client.calls))

    def test_profile_can_remove_and_add_photos(self):
        with TemporaryDirectory() as folder:
            old_photo = f"{'a' * 32}.jpg"
            (Path(folder) / old_photo).write_bytes(b"old photo")
            profile_row = user_row(1, "Ana")
            profile_row["fotos"] = [old_photo]
            client = FakeSupabaseClient({"user": [profile_row]})
            form = valid_form()
            form.pop("senha")
            form.pop("confirmar_senha")
            form["remover_fotos"] = old_photo
            upload = image_upload()
            form["fotos"] = (upload.stream, upload.filename, "image/png")

            with self.client.session_transaction() as session:
                session["user_id"] = 1
            with patch.dict(app.config, {"PHOTO_UPLOAD_FOLDER": folder}), patch(
                "app.get_supabase_client", return_value=client
            ):
                response = self.client.post("/profile", data=form)

            self.assertEqual(response.status_code, 302)
            self.assertFalse((Path(folder) / old_photo).exists())
            self.assertEqual(len(client.tables["user"][0]["fotos"]), 1)
            self.assertTrue((Path(folder) / client.tables["user"][0]["fotos"][0]).exists())


if __name__ == "__main__":
    unittest.main()
