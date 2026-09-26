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
        self.action, self.payload = "upsert", dict(payload)
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
            row = next(
                (item for item in rows if all(item.get(field) == self.payload.get(field) for field in self.conflict_fields)),
                None,
            )
            if row:
                row.update(self.payload)
            else:
                row = dict(self.payload)
                row.setdefault("id", max((item.get("id", 0) for item in rows), default=0) + 1)
                rows.append(row)
            result = [row]
        return SimpleNamespace(data=result)


class FakeSupabaseClient:
    def __init__(self, tables=None, error_on=None, error=None):
        self.tables = tables or {}
        self.error_on = error_on
        self.error = error
        self.calls = []

    def table(self, table_name):
        return FakeSupabaseQuery(self, table_name)


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
        ficha = {
            "id": 7, "id_user_a": 1, "id_user_b": 2,
            "compatibilidade": 0, "gostei_a": 1, "amei_a": 0,
            "recusou_a": 0, "gostei_b": 0, "amei_b": 0,
            "recusou_b": 0, "match": 0,
        }
        client = FakeSupabaseClient({"user": [user_a, user_b], "ficha": [ficha]})

        with patch("compatibility.get_supabase_client", return_value=client):
            Compatibilidade().calculate_compatibility(1)

        self.assertEqual(len(client.tables["ficha"]), 1)
        self.assertEqual(client.tables["ficha"][0]["id_user_a"], 1)
        self.assertEqual(client.tables["ficha"][0]["id_user_b"], 2)
        self.assertEqual(client.tables["ficha"][0]["gostei_a"], 1)
        self.assertGreater(client.tables["ficha"][0]["compatibilidade"], 0)


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
        ), patch("app.app.logger.exception"):
            compatibility.return_value.calculate_compatibility = MagicMock()
            response = self.client.get("/home")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Execute schema_postgresql.sql", response.data)

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
        self.assertIn(b"Salvar perfil", response.data)
        self.assertIn(f"/uploads/{'c' * 32}.jpg".encode(), response.data)
        self.assertIn(b'enctype="multipart/form-data"', response.data)

        updated_user = user_row(1, "Ana")
        updated_user["fotos"] = []
        client = FakeSupabaseClient({"user": [updated_user]})
        form = valid_form()
        form.pop("senha")
        form.pop("confirmar_senha")
        with patch("app.get_supabase_client", return_value=client):
            response = self.client.post("/profile", data=form)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/profile")
        self.assertEqual(client.tables["user"][0]["fisico"], int(form["fisico"]))

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
