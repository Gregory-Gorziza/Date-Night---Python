import datetime
from db import get_db_connection

class Compatibilidade:
    """
    Classe responsável por calcular o nível de compatibilidade (afinidade) 
    entre os usuários do sistema com base em diversas características 
    (idade, altura, gostos, interesses, etc.).
    """
    def __init__(self):
        self.compatibilidade = 0

    def calculate_compatibility(self, current_user_id):
        """
        Função principal que calcula a compatibilidade do usuário atual com todos os 
        outros usuários cadastrados no banco de dados. 
        O resultado é salvo na tabela 'ficha' para exibir os melhores 'matches'.
        """
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT * FROM user WHERE id = %s", (current_user_id,))
                user = cursor.fetchone()
                if not user:
                    return
                
                cursor.execute("SELECT * FROM user WHERE id != %s", (current_user_id,))
                other_users = cursor.fetchall()
                
                for comp_user in other_users:
                    if not self._check_gender(user, comp_user):
                        # Should delete from ficha if exists?
                        self._delete_ficha(current_user_id, comp_user['id'], cursor)
                        continue
                    
                    self.compatibilidade = 0
                    
                    self._idade(user, comp_user)
                    self._altura(user, comp_user)
                    self._imc(user, comp_user)
                    self._forma_fisica(user, comp_user)
                    self._cabelo(user, comp_user)
                    self._pele(user, comp_user)
                    self._tatuagens(user, comp_user)
                    self._certifica_salario(user, comp_user)
                    self._tracos(user, comp_user)
                    self._interesses(user, comp_user)
                    
                    self._salva_ficha(current_user_id, comp_user['id'], self.compatibilidade, cursor)
            conn.commit()

    def _check_gender(self, user, comp):
        """
        Verifica se a preferência de gênero de ambos os usuários coincide.
        Retorna True se ambos corresponderem à preferência um do outro.
        """
        return user['pref_genero'] == comp['genero'] and comp['pref_genero'] == user['genero']

    def _delete_ficha(self, user_id_a, user_id_b, cursor):
        sql = "DELETE FROM ficha WHERE (id_user_A = %s AND id_user_B = %s) OR (id_user_A = %s AND id_user_B = %s)"
        cursor.execute(sql, (user_id_a, user_id_b, user_id_b, user_id_a))

    def _salva_ficha(self, user_id_a, user_id_b, compatibilidade, cursor):
        # We need to ensure A < B to respect unique constraint
        if user_id_a > user_id_b:
            user_id_a, user_id_b = user_id_b, user_id_a
            
        sql_check = "SELECT id FROM ficha WHERE id_user_A = %s AND id_user_B = %s"
        cursor.execute(sql_check, (user_id_a, user_id_b))
        ficha = cursor.fetchone()
        
        if ficha:
            sql_update = "UPDATE ficha SET compatibilidade = %s WHERE id = %s"
            cursor.execute(sql_update, (compatibilidade, ficha['id']))
        else:
            sql_insert = "INSERT INTO ficha (id_user_A, id_user_B, compatibilidade, gostei_A, amei_A, gostei_B, amei_B, `match`) VALUES (%s, %s, %s, 0, 0, 0, 0, 0)"
            cursor.execute(sql_insert, (user_id_a, user_id_b, compatibilidade))

    def _idade(self, user, comp):
        """
        Calcula os pontos de compatibilidade baseados na regra de idade:
        Metade da idade mais sete (no código ajustado para +6).
        """
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
        """
        Calcula os pontos de compatibilidade baseados nos interesses em comum
        (viagens, livros, animais, esportes, etc).
        """
        fields = ['viagens', 'livros', 'causa', 'animais', 'jogos', 'artes', 'natureza', 'esportes', 'gastronomia', 'musica']
        for field in fields:
            if comp.get(field, 0) == user.get(field, 0):
                self.compatibilidade += 1
