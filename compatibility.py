import datetime
from db import get_supabase_client

class Compatibilidade:
    """Compatibilidade entre usuários."""
    def __init__(self):
        self.compatibilidade = 0

    def calculate_compatibility(self, current_user_id):
        """Calcula e salva combinações."""
        client = get_supabase_client()
        user_rows = (
            client.table("user")
            .select("*")
            .eq("id", current_user_id)
            .limit(1)
            .execute()
            .data
        )
        if not user_rows:
            return
        user = user_rows[0]
        other_users = (
            client.table("user")
            .select("*")
            .neq("id", current_user_id)
            .execute()
            .data
            or []
        )

        for comp_user in other_users:
            if not self._check_gender(user, comp_user):
                self._delete_ficha(client, current_user_id, comp_user["id"])
                continue

            score_a = self._score(user, comp_user)
            score_b = self._score(comp_user, user)
            score = (score_a + score_b) // 2
            self._salva_ficha(client, current_user_id, comp_user["id"], score)

    def _check_gender(self, user, comp):
        """Confere as preferências de gênero."""
        return user['pref_genero'] == comp['genero'] and comp['pref_genero'] == user['genero']

    def _delete_ficha(self, client, user_id_a, user_id_b):
        user_id_a, user_id_b = sorted((user_id_a, user_id_b))
        client.table("ficha").delete().eq("id_user_a", user_id_a).eq(
            "id_user_b", user_id_b
        ).execute()
        client.table("ficha").delete().eq("id_user_a", user_id_b).eq(
            "id_user_b", user_id_a
        ).execute()

    def _salva_ficha(self, client, user_id_a, user_id_b, compatibilidade):
        user_id_a, user_id_b = sorted((user_id_a, user_id_b))
        client.table("ficha").upsert(
            {
                "id_user_a": user_id_a,
                "id_user_b": user_id_b,
                "compatibilidade": compatibilidade,
            },
            on_conflict="id_user_a,id_user_b",
        ).execute()

    def _score(self, user, comp):
        self.compatibilidade = 0
        self._idade(user, comp)
        self._altura(user, comp)
        self._imc(user, comp)
        self._forma_fisica(user, comp)
        self._cabelo(user, comp)
        self._pele(user, comp)
        self._tatuagens(user, comp)
        self._certifica_salario(user, comp)
        self._tracos(user, comp)
        self._interesses(user, comp)
        return self.compatibilidade

    def _idade(self, user, comp):
        """Pontua a diferença de idade."""
        current_year = datetime.datetime.now().year
        idade_user = current_year - user['ano']
        idade_comp = current_year - comp['ano']
        
        # User masc, Comp fem
        if user['genero'] == 0 and comp['genero'] == 1:
            aux = (idade_user // 2) + 6
            if idade_comp >= aux:
                self.compatibilidade += 1
        # User fem, Comp masc
        elif user['genero'] == 1 and comp['genero'] == 0:
            aux = (idade_comp // 2) + 6
            if idade_user >= aux:
                self.compatibilidade += 1

    def _altura(self, user, comp):
        # masc = 0, fem = 1
        if (user['genero'] == 0 and comp['genero'] == 1 and user['altura'] >= comp['altura']) or \
           (user['genero'] == 1 and comp['genero'] == 0 and comp['altura'] >= user['altura']):
            self.compatibilidade += 1

    def _imc(self, user, comp):
        diff = user['imc'] - comp['imc']
        if -10 <= diff <= 10:
            self.compatibilidade += 1

    def _forma_fisica(self, user, comp):
        if comp['pref_fisico'] == 1 or user['pref_fisico'] == 1:
            if comp['fisico'] == user['fisico']:
                self.compatibilidade += 1
        else:
            if comp['pref_fisico'] == user['pref_fisico']:
                self.compatibilidade += 1

    def _cabelo(self, user, comp):
        # 0: preto, 1: castanho, 2: loiro, 3: ruivo
        if user['cabelo'] == 0:
            self.compatibilidade += 1 if comp['preto'] == 1 else -1
        elif user['cabelo'] == 1:
            self.compatibilidade += 1 if comp['castanho'] == 1 else -1
        elif user['cabelo'] == 2:
            self.compatibilidade += 1 if comp['loiro'] == 1 else -1
        elif user['cabelo'] == 3:
            self.compatibilidade += 1 if comp['ruivo'] == 1 else -1

        if comp['cabelo'] == 0:
            self.compatibilidade += 1 if user['preto'] == 1 else -1
        elif comp['cabelo'] == 1:
            self.compatibilidade += 1 if user['castanho'] == 1 else -1
        elif comp['cabelo'] == 2:
            self.compatibilidade += 1 if user['loiro'] == 1 else -1
        elif comp['cabelo'] == 3:
            self.compatibilidade += 1 if user['ruivo'] == 1 else -1

    def _pele(self, user, comp):
        # 0: clara, 1: morena, 2: escura
        if user['pele'] == 0:
            self.compatibilidade += 1 if comp['clara'] == 1 else -1
        elif user['pele'] == 1:
            self.compatibilidade += 1 if comp['morena'] == 1 else -1
        elif user['pele'] == 2:
            self.compatibilidade += 1 if comp['escura'] == 1 else -1
            
        if comp['pele'] == 0:
            self.compatibilidade += 1 if user['clara'] == 1 else -1
        elif comp['pele'] == 1:
            self.compatibilidade += 1 if user['morena'] == 1 else -1
        elif comp['pele'] == 2:
            self.compatibilidade += 1 if user['escura'] == 1 else -1

    def _tatuagens(self, user, comp):
        if user['pref_tatuagens'] == 1:
            self.compatibilidade += 1 if comp['tatuagens'] == 0 else -1
        if comp['pref_tatuagens'] == 1:
            self.compatibilidade += 1 if user['tatuagens'] == 0 else -1

    def _certifica_salario(self, user, comp):
        if comp['certificacao'] == 1 and user['certificacao'] == 1:
            self.compatibilidade += 1
        elif comp['certificacao'] == 0 and user['certificacao'] == 0:
            self.compatibilidade += 1
        elif comp['certificacao'] == 1 or user['certificacao'] == 1:
            if comp['certificacao'] == 1 and user['salario'] == 3:
                self.compatibilidade += 1
            if user['certificacao'] == 1 and comp['salario'] == 3:
                self.compatibilidade += 1
                
        if comp['salario'] == 0:
            if user['salario'] <= 1:
                self.compatibilidade += 1
        elif comp['salario'] == 1:
            if user['salario'] <= 2:
                self.compatibilidade += 1
        elif comp['salario'] == 2:
            if user['salario'] >= 1:
                self.compatibilidade += 1
        elif comp['salario'] == 3:
            if user['salario'] >= 2:
                self.compatibilidade += 1

    def _tracos(self, user, comp):
        if user['tracos'] == 0:
            if comp['tracos'] <= 1: self.compatibilidade += 1
        elif user['tracos'] == 1:
            if comp['tracos'] <= 2: self.compatibilidade += 1
        elif user['tracos'] == 2:
            if comp['tracos'] >= 1: self.compatibilidade += 1
        elif user['tracos'] == 3:
            if comp['tracos'] >= 2: self.compatibilidade += 1

    def _interesses(self, user, comp):
        """Pontua interesses em comum."""
        fields = ['viagens', 'livros', 'causa', 'animais', 'jogos', 'artes', 'natureza', 'esportes', 'gastronomia', 'musica']
        for field in fields:
            if comp.get(field, 0) == user.get(field, 0):
                self.compatibilidade += 1
