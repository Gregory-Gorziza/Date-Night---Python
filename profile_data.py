from datetime import date
import re

PROFILE_OPTIONS = {
    "genero": ("Masculino", "Feminino"),
    "pref_genero": ("Masculino", "Feminino"),
    "fisico": ("Fora de forma", "Normal", "Em forma"),
    "cabelo": ("Preto", "Castanho", "Loiro", "Ruivo"),
    "pele": ("Pele clara", "Pele morena", "Pele escura"),
    "salario": ("Até R$ 2.000", "R$ 2.000 a R$ 5.000", "R$ 5.000 a R$ 10.000", "Acima de R$ 10.000"),
    "certificacao": ("Não possui", "Cursando", "Possui", "Não informado"),
    "tracos": ("Casual", "Bar", "Baile", "Show", "Balada"),
}

PROFILE_INTEGER_DEFAULTS = {
    "dia": 1,
    "mes": 1,
    "ano": 2000,
    "genero": 0,
    "altura": 170,
    "fisico": 0,
    "peso": 70,
    "cabelo": 0,
    "pele": 0,
    "tatuagens": 0,
    "fumar": 0,
    "salario": 0,
    "certificacao": 0,
    "tracos": 0,
    "animais": 0,
    "musica": 0,
    "artes": 0,
    "esportes": 0,
    "jogos": 0,
    "livros": 0,
    "natureza": 0,
    "viagens": 0,
    "gastronomia": 0,
    "causa": 0,
    "pref_genero": 0,
    "dinamica": 0,
    "pref_fisico": 0,
    "pref_tatuagens": 0,
    "pref_fumar": 0,
    "ruivo": 0,
    "preto": 0,
    "castanho": 0,
    "loiro": 0,
    "escura": 0,
    "morena": 0,
    "clara": 0,
}

INTEREST_FIELDS = (
    ("viagens", "Viagens"),
    ("livros", "Livros"),
    ("causa", "Causas sociais"),
    ("animais", "Animais"),
    ("jogos", "Jogos"),
    ("artes", "Artes"),
    ("natureza", "Natureza"),
    ("esportes", "Esportes"),
    ("gastronomia", "Gastronomia"),
    ("musica", "Música"),
)

APPEARANCE_PREFERENCES = (
    ("preto", "Preto"),
    ("castanho", "Castanho"),
    ("loiro", "Loiro"),
    ("ruivo", "Ruivo"),
    ("escura", "Escura"),
    ("morena", "Morena"),
    ("clara", "Clara"),
)

FIELD_GROUPS = (
    ("Informações básicas", (("nome", "Nome"), ("email", "E-mail"))),
    ("Nascimento", (("dia", "Dia"), ("mes", "Mês"), ("ano", "Ano"))),
    ("Características", (("genero", "Gênero"), ("altura", "Altura (cm)"), ("peso", "Peso (kg)"), ("fisico", "Tipo físico"), ("cabelo", "Cor do cabelo"), ("pele", "Cor da pele"))),
    ("Hábitos", (("tatuagens", "Tem tatuagens?"), ("fumar", "Fuma?"), ("salario", "Faixa de renda"), ("certificacao", "Formação"), ("tracos", "Estilo"))),
)

PREFERENCE_OPTIONS = (
    ("pref_genero", "Gênero de interesse"),
    ("pref_fisico", "Preferência por tipo físico"),
    ("pref_tatuagens", "Prefere pessoas sem tatuagens"),
    ("pref_fumar", "Aceita pessoas fumantes"),
)

INTEGER_RANGES = {
    "dia": (1, 31),
    "mes": (1, 12),
    "ano": (1900, date.today().year),
    "altura": (120, 230),
    "peso": (35, 250),
}

BINARY_FIELDS = {"tatuagens", "fumar", "pref_fisico", "pref_tatuagens", "pref_fumar", "viagens", "livros", "causa", "animais", "jogos", "artes", "natureza", "esportes", "gastronomia", "musica", "preto", "castanho", "loiro", "ruivo", "escura", "morena", "clara"}


def parse_profile_form(form):
    nome = form.get("nome", "").strip()
    email = form.get("email", "").strip().lower()
    if not nome:
        raise ValueError("Informe seu nome.")
    if len(nome) > 120:
        raise ValueError("O nome deve ter até 120 caracteres.")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise ValueError("Informe um e-mail válido.")

    values = {"nome": nome, "email": email}
    for field, default in PROFILE_INTEGER_DEFAULTS.items():
        raw_value = form.get(field, str(default))
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            raise ValueError("Confira os valores do perfil.") from None

        if field in PROFILE_OPTIONS and not 0 <= value < len(PROFILE_OPTIONS[field]):
            raise ValueError("Confira as opções selecionadas.")
        if field in INTEGER_RANGES:
            minimum, maximum = INTEGER_RANGES[field]
            if not minimum <= value <= maximum:
                raise ValueError("Confira os dados de nascimento, altura e peso.")
        if field in BINARY_FIELDS and value not in (0, 1):
            raise ValueError("Confira as opções selecionadas.")
        if field == "tracos" and not 0 <= value < len(PROFILE_OPTIONS[field]):
            raise ValueError("Confira as opções selecionadas.")
        values[field] = value

    try:
        birth_date = date(values["ano"], values["mes"], values["dia"])
    except ValueError:
        raise ValueError("A data de nascimento não é válida.") from None
    if birth_date > date.today():
        raise ValueError("A data de nascimento não pode estar no futuro.")

    for fields in (("preto", "castanho", "loiro", "ruivo"), ("escura", "morena", "clara")):
        if not any(values[field] for field in fields):
            for field in fields:
                values[field] = 1

    if sum(values[field] for field, _ in INTEREST_FIELDS) < 3:
        raise ValueError("Selecione pelo menos três interesses.")
    return values


def calculate_imc(peso, altura):
    return int(peso / ((altura / 100) ** 2) + 0.5)


def profile_summary(user):
    birth_date = date(int(user["ano"]), int(user["mes"]), int(user["dia"]))
    age = date.today().year - birth_date.year - ((date.today().month, date.today().day) < (birth_date.month, birth_date.day))
    rows = [
        ("Idade", f"{age} anos"),
        ("Gênero", PROFILE_OPTIONS["genero"][user["genero"]]),
        ("Altura", f"{user['altura']} cm"),
        ("Peso", f"{user['peso']} kg"),
        ("Tipo físico", PROFILE_OPTIONS["fisico"][user["fisico"]]),
        ("Cabelo", PROFILE_OPTIONS["cabelo"][user["cabelo"]]),
        ("Pele", PROFILE_OPTIONS["pele"][user["pele"]]),
        ("Tatuagens", "Sim" if user["tatuagens"] else "Não"),
        ("Fuma", "Sim" if user["fumar"] else "Não"),
        ("Renda", PROFILE_OPTIONS["salario"][user["salario"]]),
        ("Formação", PROFILE_OPTIONS["certificacao"][user["certificacao"]]),
        ("Estilo", PROFILE_OPTIONS["tracos"][user["tracos"]]),
        ("Interesses", ", ".join(label for field, label in INTEREST_FIELDS if user[field])),
        ("Gênero de interesse", PROFILE_OPTIONS["pref_genero"][user["pref_genero"]]),
        ("Preferência física", "Sim" if user["pref_fisico"] else "Indiferente"),
        ("Preferência por tatuagens", "Sem tatuagens" if user["pref_tatuagens"] else "Indiferente"),
        ("Aceita fumantes", "Sim" if user["pref_fumar"] else "Não"),
        (
            "Aparência preferida",
            ", ".join(label for field, label in APPEARANCE_PREFERENCES if user[field]) or "Indiferente",
        ),
    ]
    return rows
